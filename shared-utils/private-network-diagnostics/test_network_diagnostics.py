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

DIRECTORY = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("diagnostics", DIRECTORY / "ai_compute_network_diagnostics.py")
diagnostics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostics)


def config(**overrides):
    endpoint = dict(name="test", kind="oracle", host="localhost", port=1521, protocol="tcp")
    endpoint.update(overrides)
    return dict(timeout_seconds=1, endpoints=[endpoint])


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
                    mock.patch.object(diagnostics, "socket_probe", return_value=[]), \
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

    def test_scan_checks_every_ip_and_missing_expected_mapping(self):
        cfg = config(kind="scan", expected_ips=["255.1.0.1", "255.1.0.2", "255.1.0.3"])
        response = {"addresses": [["IPv4", "255.1.0.1"], ["IPv4", "255.1.0.3"]]}
        with mock.patch.object(diagnostics, "child_json", return_value=response), mock.patch.object(diagnostics, "socket_probe", return_value=[]) as probe:
            report = diagnostics.run_diagnostics(cfg)
        self.assertEqual(probe.call_count, 2)
        mismatch = next(c for c in report["checks"] if c["code"] == "DNS_EXPECTATION_MISMATCH")
        self.assertEqual(mismatch["missing"], ["255.1.0.2"])
        self.assertEqual(report["status"], "FAIL")
        self.assertIn("SCAN_REDIRECT_UNVERIFIED", {c["code"] for c in report["checks"]})

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
        with mock.patch.object(diagnostics, "probe_endpoint", return_value=[]) as probe:
            report = diagnostics.run_diagnostics(cfg)
        self.assertEqual(probe.call_count, 2)
        self.assertEqual(probe.call_args_list[1].args[0]["port"], 1529)
        self.assertEqual(report["status"], "WARN")

    def test_requested_oracle_probe_without_settings_warns(self):
        with mock.patch.object(diagnostics, "probe_endpoint", return_value=[]):
            report = diagnostics.run_diagnostics(config(), oracle_probe=True)
        self.assertEqual(report["status"], "WARN")
        self.assertEqual(report["checks"][0]["code"], "ORACLE_PROBE_NOT_CONFIGURED")

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
                         spark=SimpleNamespace(sparkContext=spark_context),
                         _number=diagnostics._number, runtime_context=diagnostics.runtime_context,
                         check=diagnostics.check, show_summary=lambda *args, **kwargs: None,
                         run_samples=mock.Mock(side_effect=ValueError("CA file missing")))
        with contextlib.redirect_stdout(__import__("io").StringIO()):
            exec(compile(source, "executor-cell", "exec"), namespace)
        self.assertEqual(namespace["executor_sample_status"], "SAMPLED")
        self.assertEqual(namespace["executor_reports"][0]["status"], "WARN")

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
            environment = dict(PYTHONPATH=directory, TEST_DB_USER="secret-user", TEST_DB_PASSWORD="secret-password", TEST_DB_SERVICE="secret-service")
            cfg = config(kind="scan", oracle={"user_env": "TEST_DB_USER", "password_env": "TEST_DB_PASSWORD", "service_name_env": "TEST_DB_SERVICE"})
            with mock.patch.dict(os.environ, environment), mock.patch.object(diagnostics, "probe_endpoint", return_value=[]):
                report = diagnostics.run_diagnostics(cfg, oracle_probe=True)
            self.assertEqual(report["status"], "PASS")
            self.assertIn("SCAN_SINGLE_CONNECTION_VERIFIED", {c["code"] for c in report["checks"]})
            self.assertNotIn("secret-", json.dumps(report))

    def test_oracle_error_is_sanitized(self):
        stub = 'def connect(**kwargs): raise RuntimeError("ORA-01017 secret-password secret-service")\n'
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "oracledb.py").write_text(stub)
            environment = dict(PYTHONPATH=directory, TEST_DB_USER="secret-user", TEST_DB_PASSWORD="secret-password", TEST_DB_SERVICE="secret-service")
            endpoint = diagnostics.validate_config(config(oracle={"user_env": "TEST_DB_USER", "password_env": "TEST_DB_PASSWORD", "service_name_env": "TEST_DB_SERVICE"}))["endpoints"][0]
            with mock.patch.dict(os.environ, environment):
                result = diagnostics.child_json(diagnostics.ORACLE_CODE, {"endpoint": endpoint, "timeout": 1}, 2)
        self.assertEqual(result, {"status": "FAIL", "code": "ORA-01017"})

    def test_notebook_embeds_script_and_executes_without_targets(self):
        notebook = json.loads((DIRECTORY / "AIDP_Compute_Private_Network_Diagnostics.ipynb").read_text())
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
        self.assertEqual(namespace["executor_sample_status"], "NOT_REQUESTED")


if __name__ == "__main__":
    unittest.main()
