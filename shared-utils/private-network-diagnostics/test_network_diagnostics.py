# Copyright (c) 2026 Oracle and/or its affiliates.
# Licensed under the Universal Permissive License (UPL), Version 1.0.
# See ../../LICENSE.txt.

"""Deterministic fixtures: DNS, local TCP/TLS, SCAN coverage and safe Oracle IO."""

import contextlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock
import uuid
from io import StringIO

DIRECTORY = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("diagnostics", DIRECTORY / "ai_compute_network_diagnostics.py")
diagnostics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostics)


def oracle_fixture_environment(directory):
    # These per-run dummy values reach only the temporary stub driver below.
    # They are never credentials for a database or other external service.
    return {
        "PYTHONPATH": directory,
        "TEST_DB_USER": "fixture-user-" + uuid.uuid4().hex,
        "TEST_DB_PASSWORD": uuid.uuid4().hex,
        "TEST_DB_SERVICE": "fixture-service-" + uuid.uuid4().hex,
    }


def config(**overrides):
    endpoint = dict(name="test", kind="oracle", host="localhost", port=1521, protocol="tcp")
    endpoint.update(overrides)
    return dict(timeout_seconds=1, endpoints=[endpoint])


def tcp_success(endpoint, address, timeout):
    return [diagnostics.check("PASS", "TCP_CONNECTED", "tcp", endpoint["name"], ip=address[1])]


@contextlib.contextmanager
def resolved_network():
    with mock.patch.object(diagnostics, "resolve_endpoint", return_value=(
            [diagnostics.check("PASS", "DNS_RESOLVED", "dns", "test")], [["IPv4", "127.0.0.1"]])), \
            mock.patch.object(diagnostics, "socket_probe", side_effect=tcp_success):
        yield


@contextlib.contextmanager
def listener(tls_context=None):
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(4)
    server.settimeout(0.1)
    stopped = threading.Event()
    names = []
    if tls_context:
        tls_context.set_servername_callback(lambda connection, name, context: names.append(name))

    def serve():
        while not stopped.is_set():
            try:
                connection, _ = server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with connection:
                connection.settimeout(1)
                if tls_context:
                    try:
                        with tls_context.wrap_socket(connection, server_side=True):
                            pass
                    except (OSError, ssl.SSLError):
                        pass

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    try:
        yield server.getsockname()[1], names
    finally:
        stopped.set()
        server.close()
        worker.join(timeout=2)


class DiagnosticsTest(unittest.TestCase):
    def test_all_names_resolved_before_any_connectivity_or_login(self):
        cfg = config(kind="scan", redirect_targets=[{"host": "listener.example.com", "port": 1529}],
                     oracle={"user_env": "TEST_USER", "password_env": "TEST_PASSWORD", "service_name_env": "TEST_SERVICE"})
        cfg["endpoints"].append(dict(name="application dependency", kind="dependency", host="dependency.example.com", port=443, protocol="tcps"))
        events = []
        def child(program, payload, timeout):
            if program == diagnostics.DNS_CODE:
                events.append("dns:" + payload[0])
                return {"addresses": [["IPv4", "127.0.0.1"]]}
            events.append("oracle")
            return {"status": "PASS", "code": "ORACLE_SELECT_1_OK"}
        def tcp(endpoint, address, timeout):
            events.append("tcp:" + endpoint["host"])
            return tcp_success(endpoint, address, timeout)
        with mock.patch.object(diagnostics, "child_json", side_effect=child), \
                mock.patch.object(diagnostics, "socket_probe", side_effect=tcp):
            report = diagnostics.run_diagnostics(cfg, oracle_probe=True)
        self.assertEqual(events, ["dns:localhost", "dns:listener.example.com", "dns:dependency.example.com",
                                  "tcp:localhost", "tcp:listener.example.com", "tcp:dependency.example.com", "oracle"])
        self.assertEqual([r["stage"] for r in report["checks"][:3]], ["dns", "dns", "dns"])

    def test_dns_only_never_opens_destination_socket_or_logs_in(self):
        with mock.patch.object(diagnostics, "child_json", return_value={"addresses": [["IPv4", "127.0.0.1"]]}) as child, \
                mock.patch.object(diagnostics, "socket_probe") as tcp:
            report = diagnostics.run_samples(config(kind="scan"), dns_only=True, sample_count=2, interval_seconds=0)
        self.assertEqual(report["test_scope"], "dns_only")
        self.assertEqual(report["not_run_stages"], ["tcp", "tls", "oracle", "scan_redirect"])
        self.assertEqual(report["status"], "PASS")
        self.assertTrue(all(r["stage"] == "dns" for r in report["checks"]))
        self.assertTrue(all(c.args[0] == diagnostics.DNS_CODE for c in child.call_args_list))
        tcp.assert_not_called()
        with self.assertRaises(diagnostics.ConfigurationError):
            diagnostics.run_samples(config(), dns_only=True, oracle_probe=True)

    def test_resolver_classification_does_not_expose_raw_error_or_infer_dns_rcode(self):
        for number, expected in ((socket.EAI_NONAME, "DNS_NAME_NOT_FOUND"),
                                 (socket.EAI_AGAIN, "DNS_TEMPORARY_FAILURE"),
                                 (socket.EAI_FAIL, "DNS_ERROR")):
            with self.subTest(code=expected), mock.patch.object(socket, "getaddrinfo", side_effect=socket.gaierror(number, "raw resolver detail")), \
                    mock.patch.object(sys, "stdin", StringIO(json.dumps(["localhost", 1521]))), \
                    contextlib.redirect_stdout(StringIO()) as output:
                exec(diagnostics.DNS_CODE, {})
            self.assertEqual(json.loads(output.getvalue()), {"error": expected, "errno": number})
            self.assertNotIn("raw resolver detail", output.getvalue())

    def test_dns_failure_or_mismatch_blocks_tcp_tls_and_oracle(self):
        cfg = config(oracle={"user_env": "TEST_USER", "password_env": "TEST_PASSWORD", "service_name_env": "TEST_SERVICE"})
        for response, code in (({"error": "DNS_NAME_NOT_FOUND"}, "DNS_NAME_NOT_FOUND"),
                               ({"error": "DNS_TEMPORARY_FAILURE"}, "DNS_TEMPORARY_FAILURE"),
                               ({"error": "TIMEOUT"}, "DNS_TIMEOUT"),
                               ({"addresses": []}, "DNS_NO_ADDRESSES")):
            with self.subTest(code=code), mock.patch.object(diagnostics, "child_json", return_value=response) as child, \
                    mock.patch.object(diagnostics, "socket_probe") as tcp:
                report = diagnostics.run_diagnostics(cfg, oracle_probe=True)
            tcp.assert_not_called()
            self.assertEqual(child.call_count, 1)
            self.assertEqual(report["status"], "FAIL")
            self.assertEqual([r["code"] for r in report["checks"]],
                             [code, "CONNECTIVITY_BLOCKED_BY_DNS", "ORACLE_BLOCKED_BY_DNS"])
        cfg["endpoints"][0]["expected_ips"] = ["127.0.0.2"]
        with mock.patch.object(diagnostics, "child_json", return_value={"addresses": [["IPv4", "127.0.0.1"]]}) as child, \
                mock.patch.object(diagnostics, "socket_probe") as tcp:
            report = diagnostics.run_diagnostics(cfg, oracle_probe=True)
        tcp.assert_not_called()
        self.assertEqual(child.call_count, 1)
        self.assertIn("DNS_EXPECTATION_MISMATCH", {r["code"] for r in report["checks"]})
        self.assertIn("ORACLE_BLOCKED_BY_DNS", {r["code"] for r in report["checks"]})

    def test_failed_target_does_not_hide_healthy_comparison_target(self):
        cfg = config()
        cfg["endpoints"].append(dict(name="working comparison", host="comparison.example.com", port=1521))
        with mock.patch.object(diagnostics, "child_json", side_effect=[{"error": "DNS_NAME_NOT_FOUND"}, {"addresses": [["IPv4", "127.0.0.1"]]}]), \
                mock.patch.object(diagnostics, "socket_probe", side_effect=tcp_success) as tcp:
            report = diagnostics.run_diagnostics(cfg)
        self.assertEqual(tcp.call_count, 1)
        self.assertEqual(tcp.call_args.args[0]["name"], "working comparison")
        self.assertEqual(report["status"], "FAIL")

    def test_alias_configuration_rejected_before_dns_and_short_names_warn(self):
        with mock.patch.object(diagnostics, "child_json") as child:
            with self.assertRaisesRegex(diagnostics.ConfigurationError, "actual HOST.*TNS alias"):
                diagnostics.run_diagnostics(config(host="database_high"))
            child.assert_not_called()
        with mock.patch.object(diagnostics, "child_json", return_value={"addresses": [["IPv4", "127.0.0.1"]]}):
            report = diagnostics.run_diagnostics(config(host="database-host"), dns_only=True)
        self.assertIn("DNS_SHORT_NAME", {r["code"] for r in report["checks"]})
        self.assertEqual(report["status"], "WARN")

    def test_ip_literal_does_not_claim_hostname_resolution(self):
        with mock.patch.object(diagnostics, "child_json") as child, \
                mock.patch.object(diagnostics, "socket_probe", side_effect=tcp_success):
            report = diagnostics.run_diagnostics(config(host="127.0.0.1"))
        child.assert_not_called()
        self.assertEqual(report["checks"][0]["code"], "DNS_IP_LITERAL")
        self.assertEqual(report["checks"][0]["status"], "SKIP")
        with mock.patch.object(diagnostics, "child_json") as child, \
                mock.patch.object(diagnostics, "socket_probe") as tcp:
            report = diagnostics.run_samples(config(host="127.0.0.1"), dns_only=True, sample_count=2, interval_seconds=0)
        child.assert_not_called()
        tcp.assert_not_called()
        self.assertEqual(report["status"], "NOT_RUN")

    def test_dns_count_mismatch_blocks_connectivity(self):
        with mock.patch.object(diagnostics, "child_json", return_value={"addresses": [["IPv4", "127.0.0.1"]]}), \
                mock.patch.object(diagnostics, "socket_probe") as tcp:
            report = diagnostics.run_diagnostics(config(expected_dns_count=2))
        tcp.assert_not_called()
        self.assertIn("DNS_COUNT_MISMATCH", {r["code"] for r in report["checks"]})
        self.assertIn("CONNECTIVITY_BLOCKED_BY_DNS", {r["code"] for r in report["checks"]})

    def test_dns_only_cli_reports_scope_and_rejects_combined_login(self):
        with tempfile.TemporaryDirectory() as directory:
            input_path, output_path = Path(directory, "targets.json"), Path(directory, "report.json")
            input_path.write_text(json.dumps(config()))
            arguments = [sys.executable, str(DIRECTORY / "ai_compute_network_diagnostics.py"),
                         "--config", str(input_path), "--dns-only", "--output", str(output_path)]
            process = subprocess.run(arguments, text=True, capture_output=True, timeout=5)
            self.assertEqual(process.returncode, 0, process.stderr)
            report = json.loads(output_path.read_text())
            self.assertEqual(report["test_scope"], "dns_only")
            self.assertTrue(all(r["stage"] == "dns" for r in report["checks"]))
            self.assertIn("Not run: tcp, tls, oracle, scan_redirect", process.stdout)
            self.assertIn("Addresses:", process.stdout)
            process = subprocess.run(arguments + ["--oracle-probe"], text=True, capture_output=True, timeout=5)
            self.assertEqual(process.returncode, 2)
            self.assertIn("DNS-only checks cannot enable an Oracle login", process.stderr)

    def test_route_failure_is_separate_from_timeout_and_refusal(self):
        endpoint = diagnostics.validate_config(config())["endpoints"][0]
        with mock.patch.object(diagnostics.socket, "socket") as factory:
            factory.return_value.__enter__.return_value.connect.side_effect = OSError(diagnostics.errno.EHOSTUNREACH, "raw network detail")
            rows = diagnostics.socket_probe(endpoint, ["IPv4", "127.0.0.1"], 1)
        self.assertEqual(rows[0]["code"], "TCP_NO_ROUTE")
        self.assertNotIn("raw network detail", json.dumps(rows))

    def test_all_tcp_failures_skip_oracle_but_partial_paths_retain_failure(self):
        cfg = config(oracle={"user_env": "TEST_USER", "password_env": "TEST_PASSWORD", "service_name_env": "TEST_SERVICE"})
        for partial in (False, True):
            programs = []
            def child(program, payload, timeout):
                programs.append(program)
                return ({"addresses": [["IPv4", "127.0.0.1"], ["IPv4", "127.0.0.2"]]}
                        if program == diagnostics.DNS_CODE else {"status": "PASS", "code": "ORACLE_SELECT_1_OK"})
            def tcp(endpoint, address, timeout):
                return (tcp_success(endpoint, address, timeout) if partial and address[1] == "127.0.0.2"
                        else [diagnostics.check("FAIL", "TCP_TIMEOUT", "tcp", endpoint["name"], ip=address[1])])
            with self.subTest(partial=partial), mock.patch.object(diagnostics, "child_json", side_effect=child), \
                    mock.patch.object(diagnostics, "socket_probe", side_effect=tcp):
                report = diagnostics.run_diagnostics(cfg, oracle_probe=True)
            self.assertEqual(programs.count(diagnostics.ORACLE_CODE), int(partial))
            self.assertEqual(report["status"], "FAIL")
            self.assertIn("ORACLE_SELECT_1_OK" if partial else "ORACLE_BLOCKED_BY_TCP", {r["code"] for r in report["checks"]})

    def test_collected_oracle_error_classes_remain_separate_after_network_passes(self):
        cfg = config(oracle={"user_env": "TEST_USER", "password_env": "TEST_PASSWORD", "service_name_env": "TEST_SERVICE"})
        for code, section in (("ORA-17868", "dns"), ("ORA-12514", "oracle-service"),
                              ("ORA-12523", "oracle-service"), ("ORA-28000", "accounts"),
                              ("ORA-28001", "accounts"), ("DPY-6005", "unclassified")):
            with self.subTest(code=code), resolved_network(), \
                    mock.patch.object(diagnostics, "child_json", return_value={"status": "FAIL", "code": code}):
                report = diagnostics.run_diagnostics(cfg, oracle_probe=True)
            row = next(r for r in report["checks"] if r["stage"] == "oracle")
            self.assertEqual(row["code"], code)
            self.assertEqual(row["runbook_ref"], "RUNBOOK.md#" + section)
            self.assertEqual(report["status"], "FAIL")

    def test_dns_failure_guides_workspace_and_dns_routing_checks(self):
        with mock.patch.object(diagnostics, "child_json", return_value={"error": "DNS_ERROR"}):
            report = diagnostics.run_diagnostics(config())
        row = report["checks"][0]
        self.assertEqual(row["runbook_ref"], "RUNBOOK.md#dns")
        self.assertIn("VCN/subnet", row["next_step"])
        self.assertIn("private views", row["next_step"])
        self.assertIn("forwarding rules", row["next_step"])
        self.assertNotIn("support_next_step", row)
        with contextlib.redirect_stdout(__import__("io").StringIO()) as output:
            diagnostics.print_report(report)
        self.assertIn("Next:", output.getvalue())
        self.assertIn("Runbook: RUNBOOK.md#dns", output.getvalue())
        self.assertNotIn("Customer:", output.getvalue())
        self.assertNotIn("Support", output.getvalue())
        self.assertNotIn("CoreDNS", output.getvalue())

    def test_tcp_timeout_guides_datasource_routing_checks(self):
        with mock.patch.object(diagnostics, "child_json", return_value={"addresses": [["IPv4", "127.0.0.1"]]}), \
                mock.patch.object(diagnostics.socket, "socket") as factory:
            factory.return_value.__enter__.return_value.connect.side_effect = socket.timeout()
            report = diagnostics.run_diagnostics(config())
        row = next(c for c in report["checks"] if c["code"] == "TCP_TIMEOUT")
        self.assertEqual(row["runbook_ref"], "RUNBOOK.md#tcp")
        self.assertIn("routes", row["next_step"])
        self.assertIn("return routing", row["next_step"])
        self.assertNotIn("support_next_step", row)

    def test_unclassified_oracle_error_preserves_code_and_avoids_guessed_remedy(self):
        cfg = config(oracle={"user_env": "TEST_DB_USER", "password_env": "TEST_DB_PASSWORD", "service_name_env": "TEST_DB_SERVICE"})
        with resolved_network(), \
                mock.patch.object(diagnostics, "child_json", return_value={"status": "FAIL", "code": "DPY-9999"}):
            report = diagnostics.run_diagnostics(cfg, oracle_probe=True)
        row = next(r for r in report["checks"] if r["stage"] == "oracle")
        self.assertEqual(row["code"], "DPY-9999")
        self.assertEqual(row["runbook_ref"], "RUNBOOK.md#unclassified")
        self.assertIn("does not identify a cause", row["next_step"])
        self.assertNotIn("support_next_step", row)

    def test_runbook_references_resolve_to_written_customer_procedures(self):
        text = (DIRECTORY / "RUNBOOK.md").read_text()
        for section in set(diagnostics.RUNBOOK_SECTIONS.values()):
            with self.subTest(section=section):
                self.assertIn('<a id="' + section + '"></a>', text)
        for section in ("start", "network-setup", "workspace", "recovery"):
            self.assertIn('<a id="' + section + '"></a>', text)
        self.assertIn("within one invocation", text)

    def test_notebook_summary_shows_next_step_without_new_probes(self):
        notebook = json.loads((DIRECTORY / "AIDP_Compute_Private_Network_Diagnostics.ipynb").read_text())
        source = next("".join(c["source"]) for c in notebook["cells"] if "def show_summary(" in "".join(c["source"]))
        namespace = dict(CONFIG_PATH=None, CONFIG={"endpoints": []}, RUNTIME_LABEL="test",
                         RUN_ORACLE_PROBE=False, REPEAT_SAMPLES=1, SAMPLE_INTERVAL_SECONDS=0,
                         Path=Path, json=json, validate_config=diagnostics.validate_config,
                         print_report=diagnostics.print_report,
                         run_samples=diagnostics.run_samples)
        with mock.patch.object(diagnostics, "child_json", return_value={"error": "DNS_ERROR"}):
            report = diagnostics.run_diagnostics(config())
        with contextlib.redirect_stdout(__import__("io").StringIO()):
            exec(compile(source, "summary-cell", "exec"), namespace)
        with mock.patch.object(diagnostics, "child_json") as child, \
                mock.patch.object(diagnostics, "socket_probe") as tcp, \
                contextlib.redirect_stdout(__import__("io").StringIO()) as output:
            namespace["show_summary"](report)
        self.assertIn("Next:", output.getvalue())
        self.assertIn("Runbook: RUNBOOK.md#dns", output.getvalue())
        self.assertNotIn("Customer:", output.getvalue())
        self.assertNotIn("Support", output.getvalue())
        child.assert_not_called()
        tcp.assert_not_called()

    def test_cli_exports_next_step_without_responsibility_split(self):
        with tempfile.TemporaryDirectory() as directory:
            input_path, output_path = Path(directory, "targets.json"), Path(directory, "report.json")
            input_path.write_text(json.dumps(config(host="localhost", expected_ips=["192.0.2.1"])))
            arguments = [sys.executable, str(DIRECTORY / "ai_compute_network_diagnostics.py"),
                         "--config", str(input_path), "--dns-only", "--output", str(output_path)]
            process = subprocess.run(arguments, text=True, capture_output=True, timeout=5)
            self.assertEqual(process.returncode, 1, process.stderr)  # Deliberate loopback mismatch.
            report = json.loads(output_path.read_text())
            row = next(r for r in report["checks"] if r["code"] == "DNS_EXPECTATION_MISMATCH")
            self.assertIn("next_step", row)
            self.assertNotIn("support_next_step", row)
            self.assertIn("Next:", process.stdout)
            self.assertNotIn("Customer:", process.stdout)
            self.assertNotIn("Support", process.stdout)
            self.assertEqual(report["test_scope"], "dns_only")

    def test_sampling_retains_failure_after_recovery(self):
        responses = [{"error": "DNS_ERROR", "errno": -2},
                     {"addresses": [["IPv4", "127.0.0.1"]]}]
        with mock.patch.object(diagnostics, "child_json", side_effect=responses), \
                mock.patch.object(diagnostics, "socket_probe", return_value=[]), \
                mock.patch.object(diagnostics.time, "sleep") as pause:
            report = diagnostics.run_samples(config(kind="dependency"), sample_count=2, interval_seconds=10)
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["sampling"]["sample_statuses"], ["FAIL", "PASS"])
        self.assertEqual({r["sample_index"] for r in report["checks"]}, {1, 2})
        self.assertTrue(all(r["observed_at_utc"] for r in report["checks"]))
        pause.assert_called_once_with(10)

    def test_sampling_validates_bounds_and_empty_targets_before_waiting(self):
        with mock.patch.object(diagnostics, "child_json") as child, \
                mock.patch.object(diagnostics.time, "sleep") as pause:
            for count, interval in ((0, 5), (11, 5), (True, 5), (2, -1), (2, float("nan"))):
                with self.subTest(count=count, interval=interval), self.assertRaises(ValueError):
                    diagnostics.run_samples(config(), sample_count=count, interval_seconds=interval)
            report = diagnostics.run_samples({"endpoints": []}, sample_count=10)
        self.assertEqual(report["status"], "NOT_RUN")
        child.assert_not_called()
        pause.assert_not_called()

    def test_sampling_suppresses_repeated_failed_oracle_logins(self):
        cfg = config(oracle={"user_env": "TEST_USER", "password_env": "TEST_PASSWORD",
                             "service_name_env": "TEST_SERVICE"})
        for code in ("ORA-01017", "ORA-28000", "ORA-28001"):
            calls = []
            def child(program, payload, timeout):
                calls.append(program)
                return ({"status": "FAIL", "code": code} if program == diagnostics.ORACLE_CODE
                        else {"addresses": [["IPv4", "127.0.0.1"]]})
            with self.subTest(code=code), mock.patch.object(diagnostics, "child_json", side_effect=child), \
                    mock.patch.object(diagnostics, "socket_probe", side_effect=tcp_success), \
                    mock.patch.object(diagnostics.time, "sleep"):
                report = diagnostics.run_samples(cfg, oracle_probe=True, sample_count=3)
            self.assertEqual(calls.count(diagnostics.ORACLE_CODE), 1)
            self.assertEqual(calls.count(diagnostics.DNS_CODE), 3)
            self.assertEqual(report["status"], "FAIL")
            self.assertEqual(report["sampling"]["suppressed_oracle_endpoints"], ["test"])
            self.assertEqual(sum(r["code"] == "ORACLE_RETRY_SUPPRESSED" for r in report["checks"]), 2)

    def test_no_targets_is_not_success(self):
        report = diagnostics.run_diagnostics({"endpoints": []})
        self.assertEqual(report["status"], "NOT_RUN")
        self.assertFalse(report["checks"])

    def test_all_targets_validated_before_network(self):
        cfg = config()
        cfg["endpoints"].append(dict(name="bad", host="example.invalid", port=1521))
        with mock.patch.object(diagnostics, "child_json") as child:
            with self.assertRaises(ValueError):
                diagnostics.run_diagnostics(cfg)
            child.assert_not_called()

    def test_bad_inputs_rejected(self):
        for fields in ({"port": True}, {"port": 0}, {"host": "https://host/db"},
                       {"password": "secret"}, {"tls_mode": "mtls"},
                       {"oracle": {"mode": "thin"}}, {"expected_ips": "10.0.0.1"}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                diagnostics.validate_config(config(**fields))
        with self.assertRaises(ValueError):
            diagnostics.validate_config({"endpoints": [], "timeout_seconds": float("nan")})

    def test_bounded_child_process_timeout(self):
        self.assertEqual(diagnostics.child_json("import time; time.sleep(10)", {}, 0.1), {"error": "TIMEOUT"})

    def test_dns_uses_real_local_resolver(self):
        response = diagnostics.child_json(diagnostics.DNS_CODE, ["localhost", 1521], 2)
        self.assertIn("127.0.0.1", {ip for family, ip in response["addresses"]})

    def test_dns_failure_skips_tcp(self):
        endpoint = diagnostics.validate_config(config())["endpoints"][0]
        with mock.patch.object(diagnostics, "child_json", return_value={"error": "DNS_ERROR", "errno": -2}), mock.patch.object(diagnostics, "socket_probe") as tcp:
            checks = diagnostics.probe_endpoint(endpoint, 1, 16)
        tcp.assert_not_called()
        self.assertEqual(checks[0]["code"], "DNS_ERROR")

    def test_scan_missing_expected_mapping_blocks_connectivity(self):
        cfg = config(kind="scan", expected_ips=["255.1.0.1", "255.1.0.2", "255.1.0.3"])
        response = {"addresses": [["IPv4", "255.1.0.1"], ["IPv4", "255.1.0.3"]]}
        with mock.patch.object(diagnostics, "child_json", return_value=response), mock.patch.object(diagnostics, "socket_probe", return_value=[]) as probe:
            report = diagnostics.run_diagnostics(cfg)
        probe.assert_not_called()
        mismatch = next(c for c in report["checks"] if c["code"] == "DNS_EXPECTATION_MISMATCH")
        self.assertEqual(mismatch["missing"], ["255.1.0.2"])
        self.assertEqual(report["status"], "FAIL")
        self.assertIn("SCAN_REDIRECT_UNVERIFIED", {c["code"] for c in report["checks"]})

    def test_scan_checks_every_confirmed_ip(self):
        addresses = [["IPv4", "255.1.0.1"], ["IPv4", "255.1.0.2"], ["IPv4", "255.1.0.3"]]
        cfg = config(kind="scan", expected_ips=[a[1] for a in addresses])
        with mock.patch.object(diagnostics, "child_json", return_value={"addresses": addresses}), \
                mock.patch.object(diagnostics, "socket_probe", side_effect=tcp_success) as tcp:
            report = diagnostics.run_diagnostics(cfg)
        self.assertEqual([c.args[1] for c in tcp.call_args_list], addresses)
        self.assertIn("DNS_EXPECTATION_MATCH", {r["code"] for r in report["checks"]})
        self.assertIn("SCAN_REDIRECT_UNVERIFIED", {r["code"] for r in report["checks"]})

    def test_scan_proxy_single_answer_supported(self):
        response = {"addresses": [["IPv4", "255.1.0.1"]]}
        with mock.patch.object(diagnostics, "child_json", return_value=response), mock.patch.object(diagnostics, "socket_probe", return_value=[]):
            report = diagnostics.run_diagnostics(config(kind="scan", expected_dns_count=1))
        self.assertNotIn("FAIL", report["counts"])
        self.assertEqual(report["status"], "WARN")

    def test_truncation_is_explicit(self):
        endpoint = diagnostics.validate_config(config())["endpoints"][0]
        response = {"addresses": [["IPv4", "127.0.0.1"], ["IPv6", "::1"]]}
        with mock.patch.object(diagnostics, "child_json", return_value=response), mock.patch.object(diagnostics, "socket_probe", return_value=[]) as probe:
            checks = diagnostics.probe_endpoint(endpoint, 1, 1)
        self.assertEqual(probe.call_count, 1)
        self.assertIn("ADDRESS_COVERAGE_INCOMPLETE", {c["code"] for c in checks})

    def test_local_tcp_success(self):
        with listener() as (port, names):
            endpoint = diagnostics.validate_config(config(host="127.0.0.1", port=port))["endpoints"][0]
            checks = diagnostics.socket_probe(endpoint, ["IPv4", "127.0.0.1"], 1)
        self.assertEqual(checks[0]["code"], "TCP_CONNECTED")

    def test_refused_port_classified(self):
        with socket.socket() as unused:
            unused.bind(("127.0.0.1", 0))
            port = unused.getsockname()[1]
        endpoint = diagnostics.validate_config(config(port=port))["endpoints"][0]
        checks = diagnostics.socket_probe(endpoint, ["IPv4", "127.0.0.1"], 1)
        self.assertEqual(checks[0]["code"], "TCP_REFUSED")

    def test_cli_against_local_listener_exports_json(self):
        with tempfile.TemporaryDirectory() as directory, listener() as (port, names):
            input_path, output_path = Path(directory, "targets.json"), Path(directory, "report.json")
            input_path.write_text(json.dumps(config(host="127.0.0.1", port=port)))
            process = subprocess.run([sys.executable, str(DIRECTORY / "ai_compute_network_diagnostics.py"),
                                      "--config", str(input_path), "--output", str(output_path)],
                                     text=True, capture_output=True, timeout=5)
            self.assertEqual(process.returncode, 0, process.stderr)
            report = json.loads(output_path.read_text())
            self.assertEqual(report["status"], "PASS")
            self.assertIn("ORACLE_PROBE_NOT_ENABLED", {c["code"] for c in report["checks"]})

    def test_ipv6_source_unavailable_is_incomplete(self):
        endpoint = diagnostics.validate_config(config())["endpoints"][0]
        with mock.patch.object(socket, "socket") as factory:
            factory.return_value.__enter__.return_value.connect.side_effect = OSError(diagnostics.errno.EADDRNOTAVAIL, "unavailable")
            checks = diagnostics.socket_probe(endpoint, ["IPv6", "::1"], 1)
        self.assertEqual(checks[0]["code"], "IPV6_SOURCE_UNAVAILABLE")
        self.assertEqual(checks[0]["status"], "WARN")

    def test_supplemental_listener_checked(self):
        cfg = config(kind="scan", redirect_targets=[{"host": "node-vip", "port": 1529}])
        with mock.patch.object(diagnostics, "child_json", return_value={"addresses": [["IPv4", "127.0.0.1"]]}), \
                mock.patch.object(diagnostics, "socket_probe", side_effect=tcp_success) as probe:
            report = diagnostics.run_diagnostics(cfg)
        self.assertEqual(probe.call_count, 2)
        self.assertEqual(probe.call_args_list[1].args[0]["port"], 1529)
        self.assertEqual(report["status"], "WARN")

    def test_requested_oracle_probe_without_settings_warns(self):
        with resolved_network():
            report = diagnostics.run_diagnostics(config(), oracle_probe=True)
        self.assertEqual(report["status"], "WARN")
        self.assertIn("ORACLE_PROBE_NOT_CONFIGURED", {r["code"] for r in report["checks"]})

    def test_executor_sample_failure_preserves_notebook_evidence(self):
        notebook = json.loads((DIRECTORY / "AIDP_Compute_Private_Network_Diagnostics.ipynb").read_text())
        source = next("".join(c["source"]) for c in notebook["cells"] if "".join(c["source"]).startswith("executor_reports = []"))
        spark_context = mock.Mock()
        spark_context.parallelize.side_effect = RuntimeError("Do not expose credentials from raw Spark messages")
        namespace = dict(EXECUTOR_SAMPLE_PARTITIONS=1, CONFIG=config(),
                         spark=SimpleNamespace(sparkContext=spark_context),
                         _number=diagnostics._number)
        with contextlib.redirect_stdout(__import__("io").StringIO()) as output:
            exec(compile(source, "executor-cell", "exec"), namespace)
        self.assertEqual(namespace["executor_sample_status"], "FAILED")
        self.assertNotIn("credentials", output.getvalue())

    def test_executor_local_configuration_issue_is_reported(self):
        notebook = json.loads((DIRECTORY / "AIDP_Compute_Private_Network_Diagnostics.ipynb").read_text())
        source = next("".join(c["source"]) for c in notebook["cells"] if "".join(c["source"]).startswith("executor_reports = []"))
        spark_context = mock.Mock()
        def collect():
            function = spark_context.parallelize.return_value.mapPartitions.call_args.args[0]
            return list(function(iter([0])))
        spark_context.parallelize.return_value.mapPartitions.return_value.collect.side_effect = collect
        namespace = dict(EXECUTOR_SAMPLE_PARTITIONS=1, CONFIG=config(),
                         REPEAT_SAMPLES=1, SAMPLE_INTERVAL_SECONDS=5,
                         RUN_CONNECTIVITY_CHECKS=False,
                         spark=SimpleNamespace(sparkContext=spark_context),
                         _number=diagnostics._number, runtime_context=diagnostics.runtime_context,
                         check=diagnostics.check, show_summary=lambda *args, **kwargs: None,
                         run_samples=mock.Mock(side_effect=ValueError("CA file missing")))
        with contextlib.redirect_stdout(__import__("io").StringIO()):
            exec(compile(source, "executor-cell", "exec"), namespace)
        self.assertEqual(namespace["executor_sample_status"], "SAMPLED")
        self.assertEqual(namespace["executor_reports"][0]["status"], "WARN")
        self.assertTrue(namespace["run_samples"].call_args.kwargs["dns_only"])
        with contextlib.redirect_stdout(StringIO()) as output:
            diagnostics.print_report(namespace["executor_reports"][0], max_rows=10)
        self.assertIn("EXECUTOR_CONFIG_UNAVAILABLE", output.getvalue())
        self.assertIn("Next:", output.getvalue())

    def test_notebook_dns_step_and_connectivity_opt_in_use_fresh_resolution(self):
        notebook = json.loads((DIRECTORY / "AIDP_Compute_Private_Network_Diagnostics.ipynb").read_text())
        namespace = {"__name__": "__main__"}
        for cell in notebook["cells"]:
            if cell["cell_type"] == "code" and cell["metadata"].get("jupyter", {}).get("source_hidden"):
                exec("".join(cell["source"]), namespace)
        namespace.update(CONFIG=config(), CONFIG_PATH=None, RUNTIME_LABEL="test",
                         REPEAT_SAMPLES=1, SAMPLE_INTERVAL_SECONDS=0,
                         RUN_CONNECTIVITY_CHECKS=False, RUN_ORACLE_PROBE=False)
        events = []
        def child(program, payload, timeout):
            events.append("dns")
            self.assertEqual(program, namespace["DNS_CODE"])
            return {"addresses": [["IPv4", "127.0.0.1"]]}
        def tcp(endpoint, address, timeout):
            events.append("tcp")
            return [namespace["check"]("PASS", "TCP_CONNECTED", "tcp", endpoint["name"])]
        namespace.update(child_json=child, socket_probe=tcp)
        dns_cell = next("".join(c["source"]) for c in notebook["cells"] if "def show_summary(" in "".join(c["source"]))
        network_cell = next("".join(c["source"]) for c in notebook["cells"] if "".join(c["source"]).startswith("connectivity_status = "))
        with contextlib.redirect_stdout(StringIO()):
            exec(dns_cell, namespace)
            exec(network_cell, namespace)
            self.assertEqual(events, ["dns"])
            self.assertEqual(namespace["connectivity_status"], "NOT_REQUESTED")
            self.assertEqual(namespace["report"]["test_scope"], "dns_only")
            namespace["RUN_CONNECTIVITY_CHECKS"] = True
            exec(network_cell, namespace)
        self.assertEqual(events, ["dns", "dns", "tcp"])
        self.assertEqual(namespace["report"]["test_scope"], "network")
        self.assertEqual(namespace["dns_report"]["test_scope"], "dns_only")

    @unittest.skipUnless(shutil.which("openssl"), "openssl needed for local TLS fixture")
    def test_tls_verified_and_untrusted_ca(self):
        with tempfile.TemporaryDirectory() as directory:
            cert, key, conf = [str(Path(directory) / filename) for filename in ("cert.pem", "key.pem", "openssl.cnf")]
            Path(conf).write_text("[req]\ndistinguished_name=dn\nx509_extensions=ext\nprompt=no\n[dn]\nCN=localhost\n[ext]\nsubjectAltName=DNS:localhost\nbasicConstraints=critical,CA:TRUE\nkeyUsage=critical,digitalSignature,keyEncipherment,keyCertSign\nextendedKeyUsage=serverAuth\n")
            subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-keyout", key, "-out", cert, "-config", conf], check=True, capture_output=True)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(cert, key)
            with listener(context) as (port, names):
                endpoint = diagnostics.validate_config(config(port=port, protocol="tcps", ca_file=cert))["endpoints"][0]
                checks = diagnostics.socket_probe(endpoint, ["IPv4", "127.0.0.1"], 1)
                self.assertEqual([c["code"] for c in checks], ["TCP_CONNECTED", "TLS_SERVER_VERIFIED"])
                self.assertIn("localhost", names)
                endpoint["tls_server_name"] = "wrong.example.com"
                checks = diagnostics.socket_probe(endpoint, ["IPv4", "127.0.0.1"], 1)
                self.assertEqual(checks[-1]["code"], "TLS_VERIFY_FAILED")
                self.assertIn("wrong.example.com", names)
                endpoint.pop("tls_server_name")
                endpoint.pop("ca_file")
                checks = diagnostics.socket_probe(endpoint, ["IPv4", "127.0.0.1"], 1)
                self.assertEqual(checks[-1]["code"], "TLS_VERIFY_FAILED")

    def test_oracle_probe_reads_secrets_without_reporting_them(self):
        stub = '''
import os
class Cursor:
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def execute(self, sql): assert sql == "SELECT 1 FROM DUAL"
    def fetchone(self): return (1,)
class Connection:
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def cursor(self): return Cursor()
def is_thin_mode(): return True
def connect(**kwargs):
    assert kwargs["host"] == "localhost" and kwargs["port"] == 1521
    assert kwargs["service_name"] == os.environ["TEST_DB_SERVICE"]
    assert kwargs["password"] == os.environ["TEST_DB_PASSWORD"]
    assert kwargs["ssl_server_dn_match"] is True
    return Connection()
'''
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "oracledb.py").write_text(stub)
            environment = oracle_fixture_environment(directory)
            cfg = config(kind="scan", oracle={"user_env": "TEST_DB_USER", "password_env": "TEST_DB_PASSWORD", "service_name_env": "TEST_DB_SERVICE"})
            with mock.patch.dict(os.environ, environment), resolved_network():
                report = diagnostics.run_diagnostics(cfg, oracle_probe=True)
            self.assertEqual(report["status"], "PASS")
            self.assertIn("SCAN_SINGLE_CONNECTION_VERIFIED", {c["code"] for c in report["checks"]})
            for key in ("TEST_DB_USER", "TEST_DB_PASSWORD", "TEST_DB_SERVICE"):
                self.assertNotIn(environment[key], json.dumps(report))

    def test_oracle_error_is_sanitized(self):
        stub = '''
import os
def connect(**kwargs):
    raise RuntimeError("ORA-01017 " + os.environ["TEST_DB_PASSWORD"] + " " + os.environ["TEST_DB_SERVICE"])
'''
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "oracledb.py").write_text(stub)
            environment = oracle_fixture_environment(directory)
            endpoint = diagnostics.validate_config(config(oracle={"user_env": "TEST_DB_USER", "password_env": "TEST_DB_PASSWORD", "service_name_env": "TEST_DB_SERVICE"}))["endpoints"][0]
            with mock.patch.dict(os.environ, environment):
                result = diagnostics.child_json(diagnostics.ORACLE_CODE, {"endpoint": endpoint, "timeout": 1}, 2)
        self.assertEqual(result, {"status": "FAIL", "code": "ORA-01017"})

    def test_notebook_embeds_script_and_executes_without_targets(self):
        notebook = json.loads((DIRECTORY / "AIDP_Compute_Private_Network_Diagnostics.ipynb").read_text())
        self.assertEqual(notebook["metadata"]["aidp_diagnostics"]["version"], diagnostics.VERSION)
        self.assertEqual((notebook["nbformat"], notebook["nbformat_minor"]), (4, 5))
        self.assertEqual(len({c["id"] for c in notebook["cells"]}), len(notebook["cells"]))
        source = (DIRECTORY / "ai_compute_network_diagnostics.py").read_text().split('\nif __name__ == "__main__":')[0] + "\n"
        embedded = "".join("".join(c["source"]) for c in notebook["cells"] if c["metadata"].get("jupyter", {}).get("source_hidden"))
        self.assertEqual(embedded, source)
        namespace = {"__name__": "__main__"}
        with mock.patch.object(subprocess, "run") as child, contextlib.redirect_stdout(__import__("io").StringIO()):
            for c in notebook["cells"]:
                if c["cell_type"] == "code":
                    exec(compile("".join(c["source"]), "notebook-cell", "exec"), namespace)
            child.assert_not_called()
        self.assertEqual(namespace["report"]["status"], "NOT_RUN")
        self.assertEqual(namespace["dns_report"]["test_scope"], "dns_only")
        self.assertEqual(namespace["connectivity_status"], "NOT_REQUESTED")
        self.assertEqual(namespace["executor_sample_status"], "NOT_REQUESTED")


if __name__ == "__main__":
    unittest.main()
