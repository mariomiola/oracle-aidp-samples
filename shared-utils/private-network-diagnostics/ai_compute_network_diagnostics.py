#!/usr/bin/env python3
# Copyright (c) 2026 Oracle and/or its affiliates.
# Licensed under the Universal Permissive License (UPL), Version 1.0.
# See ../../LICENSE.txt.

"""Bounded AIDP private-network diagnostics; Python 3.9+, standard library.

Run in the AI Compute runtime whose network path you want to test. The Compute
notebook embeds this same implementation. Oracle login is optional and needs
python-oracledb plus secrets already available in that runtime's environment.
"""

import argparse
from collections import Counter
from datetime import datetime, timezone
import errno
import ipaddress
import json
import math
import os
from pathlib import Path
import platform
import re
import socket
import ssl
import subprocess
import sys
import time

VERSION = "1.1"
DNS_CODE = r'''
import json, socket, sys
host, port = json.load(sys.stdin)
try:
    answers = socket.getaddrinfo(host, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
    print(json.dumps({"addresses": sorted(set(
        ("IPv6" if a[0] == socket.AF_INET6 else "IPv4", a[4][0])
        for a in answers if a[0] in (socket.AF_INET, socket.AF_INET6)))}))
except socket.gaierror as error:
    print(json.dumps({"error": "DNS_ERROR", "errno": error.errno}))
'''

# A separate process bounds DNS, Oracle login, redirects, query, and cleanup.
# Secrets stay in the inherited environment, never in argv, stdin, or reports.
ORACLE_CODE = r'''
import json, os, re, sys
request = json.load(sys.stdin)
endpoint, timeout = request["endpoint"], request["timeout"]
settings = endpoint["oracle"]
try:
    import oracledb
except ImportError:
    print(json.dumps({"status": "WARN", "code": "ORACLE_DRIVER_MISSING"}))
    sys.exit(0)
try:
    required = [settings[k] for k in ("user_env", "password_env", "service_name_env")]
    optional = [v for k, v in settings.items() if k.endswith("_env")]
    if any(not os.environ.get(name) for name in required + optional):
        print(json.dumps({"status": "WARN", "code": "ORACLE_ENV_MISSING"}))
        sys.exit(0)
    if settings.get("mode", "thin") == "thick":
        init = {}
        for key in ("client_lib_dir", "config_dir"):
            if key + "_env" in settings:
                init["lib_dir" if key == "client_lib_dir" else key] = os.environ[settings[key + "_env"]]
        oracledb.init_oracle_client(**init)
    args = dict(host=endpoint["host"], port=endpoint["port"],
                protocol=endpoint["protocol"],
                user=os.environ[settings["user_env"]],
                password=os.environ[settings["password_env"]],
                service_name=os.environ[settings["service_name_env"]],
                retry_count=0, tcp_connect_timeout=timeout,
                ssl_server_dn_match=True)
    for key in ("wallet_location", "wallet_password", "config_dir", "ssl_server_cert_dn"):
        if key + "_env" in settings:
            args[key] = os.environ[settings[key + "_env"]]
    with oracledb.connect(**args) as connection:
        connection.call_timeout = int(timeout * 1000)
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM DUAL")
            ok = cursor.fetchone() == (1,)
    print(json.dumps({"status": "PASS" if ok else "FAIL",
                      "code": "ORACLE_SELECT_1_OK" if ok else "ORACLE_QUERY_UNEXPECTED",
                      "client_mode": "thin" if oracledb.is_thin_mode() else "thick"}))
except Exception as error:
    detail = error.args[0] if error.args else None
    code = getattr(detail, "full_code", "")
    if not re.fullmatch(r"(?:ORA|DPY|DPI)-\d+", code or ""):
        # Preserve codes from older clients without exposing raw messages/DSNs.
        found = re.search(r"\b(?:ORA|DPY|DPI)-\d+\b", str(error))
        code = found.group(0) if found else "ORACLE_CLIENT_ERROR"
    print(json.dumps({"status": "FAIL", "code": code}))
'''

HINTS = {
    "DNS_ERROR": "Check the exact FQDN, customer VCN DNS views/forwarding, on-prem resolver path, and workspace DNS proxy/CoreDNS mapping.",
    "DNS_TIMEOUT": "The runtime resolver did not finish within the deadline; check DNS proxy/forwarders and resolver reachability.",
    "DNS_EXPECTATION_MISMATCH": "Compare runtime-visible IPs with current RCE NAT/SCAN proxy mappings, not backend customer IPs. Check stale DNS or incomplete mappings.",
    "DNS_COUNT_MISMATCH": "Check the configured expected answer count. A SCAN proxy can expose one IP while direct SCAN DNS can expose several.",
    "TCP_TIMEOUT": "Check workspace PE/RCE state, listener port, subnet/NSG/security-list rules, and routing. For on-prem check DRG, VPN/FastConnect, and return routes. Timeout alone does not locate the fault.",
    "TCP_REFUSED": "An active rejection was received; check the listener/port and firewall reject rules.",
    "TCP_NO_ROUTE": "Check local routes, PE/RCE routing, and remote/return routes; this result alone does not identify the missing route.",
    "IPV6_SOURCE_UNAVAILABLE": "AAAA was resolved but this runtime could not originate IPv6. IPv6 reachability remains unverified.",
    "TLS_VERIFY_FAILED": "Check server hostname/certificate, trust chain, runtime clock, and wallet/CA configuration. TCP was reached; database authentication remains unverified.",
    "TLS_FAILED": "Check TCPS listener selection, TLS policy, wallet/client-certificate requirements, and possible Oracle Net negotiation. A raw TLS probe is not an Oracle client.",
    "MTLS_WALLET_UNVERIFIED": "The raw TLS probe does not load an Oracle wallet. Use the optional Oracle client probe with the correct runtime wallet to validate mTLS.",
    "SCAN_REDIRECT_UNVERIFIED": "SCAN socket reachability does not exercise Oracle listener redirection. Review workspace SCAN proxy name/port and run the Oracle client probe. Supplied VIP checks are supplemental.",
    "ORACLE_DRIVER_MISSING": "Install an approved python-oracledb version in this runtime to enable the optional database probe.",
    "ORACLE_ENV_MISSING": "Supply configured environment values through the runtime secret mechanism; do not place secret values in the notebook or target JSON.",
    "ORACLE_PROBE_NOT_CONFIGURED": "Oracle validation was requested, but this endpoint has no oracle settings. Add environment variable references or run network-only checks.",
    "ORACLE_RETRY_SUPPRESSED": "An earlier sample failed authentication or account validation. Further Oracle logins were skipped; coordinate with the DBA before retrying.",
    "ORACLE_TIMEOUT": "Login, redirect, query, or cleanup exceeded the total Oracle deadline. Compare SCAN and listener paths and review Oracle client/server logs.",
    "ORA-01017": "Database authentication was rejected. Check credentials and account authorization; do not classify this as a DNS failure.",
    "ORA-28000": "The database account is locked. Coordinate with the DBA before another login attempt.",
    "ORA-28001": "The database password has expired. Coordinate a password change with the DBA; network changes will not resolve this error.",
    "ORA-17868": "The Oracle client could not resolve the supplied host. Compare the exact JDBC/Oracle hostname with runtime DNS and the RCE mapping; client resolver behavior may differ.",
    "ORA-12514": "The listener did not recognize the service name; check service registration and the supplied service.",
    "ORA-12523": "The listener could not find an appropriate available database instance. Ask the DBA to check registered services and instance readiness; TCP reachability alone is insufficient.",
    "DPY-6001": "Check database service name and listener service registration.",
    "ORA-12506": "Listener service ACL filtering rejected the connection; check ADB network access/ACL and listener policy.",
    "ORA-28759": "Check Oracle wallet location, file accessibility, and client mode.",
    "DPY-3010": "Check whether this database version is supported by the selected python-oracledb client mode.",
    "DPY-3001": "Check whether the database requires Oracle native network encryption; use an appropriate Thick client if needed.",
    "DPI-1047": "The Oracle client libraries could not be loaded; check installed libraries and their runtime dependencies.",
}


class ConfigurationError(ValueError):
    """A configuration error safe to show without exposing source values."""


def _keys(value, allowed, where):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise ConfigurationError("Invalid or unknown fields in " + where)


def _number(value, low, high, where, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigurationError(where + " must be numeric")
    if not math.isfinite(value) or not low <= value <= high or (integer and not isinstance(value, int)):
        raise ConfigurationError(where + " is outside the supported range")


def _host(value):
    if not isinstance(value, str) or not value or value != value.strip():
        raise ConfigurationError("host must be a hostname or IP without whitespace")
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        name = value.rstrip(".")
        if name.lower().endswith(".invalid") or name == "CHANGE_ME":
            raise ConfigurationError("Replace example hosts with actual endpoint names")
        if len(name) > 253 or not all(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", p) for p in name.split(".")):
            raise ConfigurationError("host must be a hostname, not a URL or connection descriptor")
        return name.lower()


def validate_config(config):
    """Validate every target before generating any network traffic."""
    _keys(config, {"endpoints", "timeout_seconds", "oracle_timeout_seconds", "max_addresses"}, "config")
    config = json.loads(json.dumps(config))
    config.setdefault("timeout_seconds", 5)
    config.setdefault("oracle_timeout_seconds", 20)
    config.setdefault("max_addresses", 16)
    _number(config["timeout_seconds"], 0.1, 30, "timeout_seconds")
    _number(config["oracle_timeout_seconds"], 1, 120, "oracle_timeout_seconds")
    _number(config["max_addresses"], 1, 64, "max_addresses", integer=True)
    endpoints = config.get("endpoints")
    if not isinstance(endpoints, list) or len(endpoints) > 16:
        raise ConfigurationError("endpoints must be a list of at most 16 targets")
    names = set()
    for endpoint in endpoints:
        _keys(endpoint, {"name", "kind", "host", "port", "protocol", "tls_mode", "ca_file", "tls_server_name", "expected_ips", "expected_dns_count", "redirect_targets", "oracle"}, "endpoint")
        name = endpoint.get("name")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_. -]{1,80}", name) or name in names:
            raise ConfigurationError("Each endpoint needs a unique, short name")
        names.add(name)
        endpoint["host"] = _host(endpoint.get("host"))
        _number(endpoint.get("port"), 1, 65535, "port", integer=True)
        endpoint.setdefault("kind", "oracle")
        endpoint.setdefault("protocol", "tcp")
        endpoint.setdefault("tls_mode", "tls")
        if endpoint["kind"] not in {"adb", "ebs", "oracle", "scan", "onprem", "dependency"}:
            raise ConfigurationError("kind must be adb, ebs, oracle, scan, onprem, or dependency")
        if endpoint["protocol"] not in {"tcp", "tcps"} or endpoint["tls_mode"] not in {"tls", "mtls"}:
            raise ConfigurationError("Use protocol tcp/tcps and tls_mode tls/mtls")
        if endpoint["protocol"] == "tcp" and any(k in endpoint for k in ("ca_file", "tls_server_name")):
            raise ConfigurationError("TLS options require protocol tcps")
        if endpoint["protocol"] == "tcp" and endpoint["tls_mode"] == "mtls":
            raise ConfigurationError("mTLS requires protocol tcps")
        if "tls_server_name" in endpoint:
            endpoint["tls_server_name"] = _host(endpoint["tls_server_name"])
        if "ca_file" in endpoint and (not isinstance(endpoint["ca_file"], str) or not Path(endpoint["ca_file"]).is_file()):
            raise ConfigurationError("ca_file must exist in the runtime")
        ips = endpoint.setdefault("expected_ips", [])
        if not isinstance(ips, list) or len(ips) > 64:
            raise ConfigurationError("expected_ips must be a list of at most 64 IP addresses")
        try:
            endpoint["expected_ips"] = sorted(set(str(ipaddress.ip_address(ip)) for ip in ips))
        except (ValueError, TypeError):
            raise ConfigurationError("expected_ips must contain valid IP addresses") from None
        if "expected_dns_count" in endpoint:
            _number(endpoint["expected_dns_count"], 1, 64, "expected_dns_count", integer=True)
        targets = endpoint.setdefault("redirect_targets", [])
        if not isinstance(targets, list) or len(targets) > 16:
            raise ConfigurationError("redirect_targets must contain at most 16 listener targets")
        if targets and endpoint["kind"] != "scan":
            raise ConfigurationError("redirect_targets are only supported for kind scan")
        for target in targets:
            _keys(target, {"host", "port", "protocol", "expected_ips", "tls_server_name", "ca_file", "tls_mode"}, "redirect target")
            # The same validation applies to each explicitly supplied listener.
            target.setdefault("protocol", endpoint["protocol"])
            target.setdefault("tls_mode", endpoint["tls_mode"])
            child = dict(target, name="redirect", kind="oracle")
            normalized = validate_config({"endpoints": [child]})["endpoints"][0]
            target.update({k: v for k, v in normalized.items() if k not in {"name", "kind", "redirect_targets"}})
        if "oracle" in endpoint:
            settings = endpoint["oracle"]
            _keys(settings, {"mode", "user_env", "password_env", "service_name_env", "wallet_location_env", "wallet_password_env", "config_dir_env", "client_lib_dir_env", "ssl_server_cert_dn_env"}, "oracle")
            if settings.get("mode", "thin") not in {"thin", "thick"}:
                raise ConfigurationError("Oracle mode must be thin or thick")
            if not all(k in settings for k in ("user_env", "password_env", "service_name_env")):
                raise ConfigurationError("Oracle probe needs user_env, password_env, and service_name_env")
            if any(not isinstance(v, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", v) for k, v in settings.items() if k.endswith("_env")):
                raise ConfigurationError("Oracle settings must reference environment variable names")
            if settings.get("mode", "thin") == "thin" and "client_lib_dir_env" in settings:
                raise ConfigurationError("client_lib_dir_env requires mode thick")
    return config


def check(status, code, stage, endpoint, **details):
    result = dict(status=status, code=code, stage=stage, endpoint=endpoint,
                  observed_at_utc=datetime.now(timezone.utc).isoformat(), **details)
    if code in HINTS:
        result["next_step"] = HINTS[code]
    return result


def child_json(code, payload, timeout):
    # Raw stdout/stderr/exception messages are deliberately never surfaced.
    try:
        process = subprocess.run([sys.executable, "-c", code], input=json.dumps(payload),
                                 text=True, capture_output=True, timeout=timeout, check=False)
        if process.returncode != 0:
            return {"error": "CHILD_PROCESS_FAILED", "returncode": process.returncode}
        return json.loads(process.stdout)
    except subprocess.TimeoutExpired:
        return {"error": "TIMEOUT"}
    except (OSError, ValueError):
        return {"error": "CHILD_PROCESS_FAILED"}


def address_type(ip):
    address = ipaddress.ip_address(ip)
    if address.version == 4 and address in ipaddress.ip_network("240.0.0.0/4"):
        return "synthetic_or_reserved_possible_RCE"
    return "private_or_special" if not address.is_global else "global"


def socket_probe(endpoint, address, timeout):
    family_name, ip = address
    family = socket.AF_INET6 if family_name == "IPv6" else socket.AF_INET
    sockaddr = (ip, endpoint["port"], 0, 0) if family == socket.AF_INET6 else (ip, endpoint["port"])
    name = endpoint["name"]
    details = dict(host=endpoint["host"], port=endpoint["port"], ip=ip,
                   family=family_name, address_type=address_type(ip))
    start = time.monotonic()
    results = []
    try:
        with socket.socket(family, socket.SOCK_STREAM) as connection:
            connection.settimeout(timeout)
            connection.connect(sockaddr)
            results.append(check("PASS", "TCP_CONNECTED", "tcp", name,
                                 elapsed_ms=round((time.monotonic() - start) * 1000, 1), **details))
            if endpoint["protocol"] == "tcps":
                tls_start = time.monotonic()
                try:
                    context = ssl.create_default_context(cafile=endpoint.get("ca_file"))
                    # The socket uses the resolved IP; SNI/verification use the DB hostname.
                    server_name = endpoint.get("tls_server_name", endpoint["host"])
                    with context.wrap_socket(connection, server_hostname=server_name) as secured:
                        results.append(check("PASS", "TLS_SERVER_VERIFIED", "tls", name,
                                             tls_version=secured.version(), server_name=server_name,
                                             elapsed_ms=round((time.monotonic() - tls_start) * 1000, 1), **details))
                except ssl.SSLCertVerificationError as error:
                    results.append(check("FAIL", "TLS_VERIFY_FAILED", "tls", name,
                                         verify_code=error.verify_code, **details))
                except (OSError, ValueError, ssl.SSLError):
                    status = "WARN" if endpoint["tls_mode"] == "mtls" else "FAIL"
                    results.append(check(status, "TLS_FAILED", "tls", name, **details))
                if endpoint["tls_mode"] == "mtls":
                    results.append(check("WARN", "MTLS_WALLET_UNVERIFIED", "tls", name, **details))
    except OSError as error:
        code, status = "TCP_ERROR", "FAIL"
        if isinstance(error, TimeoutError) or error.errno == errno.ETIMEDOUT:
            code = "TCP_TIMEOUT"
        elif error.errno == errno.ECONNREFUSED:
            code = "TCP_REFUSED"
        elif error.errno in {errno.ENETUNREACH, errno.EHOSTUNREACH}:
            code = "TCP_NO_ROUTE"
        elif family == socket.AF_INET6 and error.errno in {errno.EADDRNOTAVAIL, errno.EAFNOSUPPORT}:
            code, status = "IPV6_SOURCE_UNAVAILABLE", "WARN"
        results.append(check(status, code, "tcp", name, errno=error.errno, **details))
    return results


def probe_endpoint(endpoint, timeout, max_addresses):
    name, host, port = endpoint["name"], endpoint["host"], endpoint["port"]
    start = time.monotonic()
    resolved = child_json(DNS_CODE, [host, port], timeout)
    if "error" in resolved:
        code = {"TIMEOUT": "DNS_TIMEOUT"}.get(resolved["error"], resolved["error"])
        return [check("FAIL", code, "dns", name, host=host, errno=resolved.get("errno"))]
    addresses = resolved.get("addresses", [])
    if not addresses:
        return [check("FAIL", "DNS_NO_ADDRESSES", "dns", name, host=host)]
    results = [check("PASS", "DNS_RESOLVED", "dns", name, host=host,
                     addresses=addresses, elapsed_ms=round((time.monotonic() - start) * 1000, 1))]
    actual = {str(ipaddress.ip_address(a[1])) for a in addresses}
    expected = set(endpoint["expected_ips"])
    if expected:
        matched = actual == expected
        results.append(check("PASS" if matched else "FAIL",
                             "DNS_EXPECTATION_MATCH" if matched else "DNS_EXPECTATION_MISMATCH",
                             "dns_mapping", name, missing=sorted(expected - actual), unexpected=sorted(actual - expected)))
    count = endpoint.get("expected_dns_count")
    if count is not None:
        matched = len(addresses) == count
        results.append(check("PASS" if matched else "FAIL",
                             "DNS_COUNT_MATCH" if matched else "DNS_COUNT_MISMATCH",
                             "dns_mapping", name, expected=count, observed=len(addresses)))
    if len(addresses) > max_addresses:
        results.append(check("WARN", "ADDRESS_COVERAGE_INCOMPLETE", "coverage", name,
                             tested=max_addresses, total=len(addresses)))
    for address in addresses[:max_addresses]:
        results.extend(socket_probe(endpoint, address, timeout))
    return results


def runtime_context(label):
    nameservers = []
    try:
        lines = Path("/etc/resolv.conf").read_text().splitlines()
        nameservers = [line.split()[1] for line in lines if len(line.split()) > 1 and line.split()[0] == "nameserver"]
    except OSError:
        pass
    return dict(label=label, hostname=socket.gethostname(), python=platform.python_version(),
                system=platform.system(), nameservers=nameservers,
                proxy_variables_set=[k for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy") if os.environ.get(k)],
                connection_path="Direct sockets; HTTP proxies and Oracle DSN proxy options are not used")


def run_diagnostics(config, label="runtime", oracle_probe=False, oracle_suppressed=()):
    config = validate_config(config)
    results = []
    for endpoint in config["endpoints"]:
        results.extend(probe_endpoint(endpoint, config["timeout_seconds"], config["max_addresses"]))
        for index, target in enumerate(endpoint["redirect_targets"], 1):
            child = dict(target, name=endpoint["name"] + ":listener-" + str(index))
            results.extend(probe_endpoint(child, config["timeout_seconds"], config["max_addresses"]))
        oracle_ok = False
        if oracle_probe and endpoint["name"] in oracle_suppressed:
            results.append(check("SKIP", "ORACLE_RETRY_SUPPRESSED", "oracle", endpoint["name"]))
        elif oracle_probe and "oracle" in endpoint:
            start = time.monotonic()
            request = {"endpoint": endpoint, "timeout": config["timeout_seconds"]}
            response = child_json(ORACLE_CODE, request, config["oracle_timeout_seconds"])
            if "error" in response:
                response = dict(status="FAIL", code="ORACLE_TIMEOUT" if response["error"] == "TIMEOUT" else "ORACLE_PROCESS_FAILED")
            oracle_ok = response["status"] == "PASS"
            results.append(check(response["status"], response["code"], "oracle", endpoint["name"],
                                 client_mode=response.get("client_mode"),
                                 elapsed_ms=round((time.monotonic() - start) * 1000, 1)))
        else:
            results.append(check("SKIP" if not oracle_probe else "WARN", "ORACLE_PROBE_NOT_ENABLED" if not oracle_probe else "ORACLE_PROBE_NOT_CONFIGURED",
                                 "oracle", endpoint["name"]))
        if endpoint["kind"] == "scan":
            results.append(check("PASS" if oracle_ok else "WARN",
                                 "SCAN_SINGLE_CONNECTION_VERIFIED" if oracle_ok else "SCAN_REDIRECT_UNVERIFIED",
                                 "scan_redirect", endpoint["name"],
                                 supplied_listener_targets=len(endpoint["redirect_targets"])))
    counts = dict(Counter(row["status"] for row in results))
    status = "NOT_RUN" if not results else "FAIL" if counts.get("FAIL") else "WARN" if counts.get("WARN") else "PASS"
    return dict(version=VERSION, generated_at_utc=datetime.now(timezone.utc).isoformat(),
                runtime=runtime_context(label), status=status, counts=counts,
                limits=["Runtime-local evidence; does not inspect OCI routes, NSGs, PE/RCE configuration, or database server state.",
                        "Raw socket/TLS probes do not exercise Oracle Net or wallet authentication.",
                        "One Oracle login validates one chosen route and SELECT 1, not all RAC nodes, redirects, failover, or application queries.",
                        "Synthetic/reserved address classification alone does not prove an RCE mapping or private network path."],
                checks=results)


def run_samples(config, label="runtime", oracle_probe=False, sample_count=1, interval_seconds=5):
    """Collect bounded samples; retain failures even if a later sample passes."""
    _number(sample_count, 1, 10, "sample_count", integer=True)
    _number(interval_seconds, 0, 60, "interval_seconds")
    config = validate_config(config)
    if sample_count == 1 or not config["endpoints"]:
        return run_diagnostics(config, label, oracle_probe)
    checks, statuses, starts, suppressed = [], [], [], set()
    for index in range(1, sample_count + 1):
        if index > 1:
            time.sleep(interval_seconds)
        starts.append(datetime.now(timezone.utc).isoformat())
        report = run_diagnostics(config, label, oracle_probe, oracle_suppressed=suppressed)
        statuses.append(report["status"])
        suppressed.update(row["endpoint"] for row in report["checks"]
                          if row["stage"] == "oracle" and row["code"] in {"ORA-01017", "ORA-28000", "ORA-28001"})
        checks.extend(dict(row, sample_index=index) for row in report["checks"])
    counts = dict(Counter(row["status"] for row in checks))
    report.update(checks=checks, counts=counts,
                  status="FAIL" if counts.get("FAIL") else "WARN" if counts.get("WARN") else "PASS",
                  sampling={"sample_count": sample_count, "interval_seconds": interval_seconds,
                            "interval_policy": "Pause after the previous sample completes",
                            "started_at_utc": starts, "sample_statuses": statuses,
                            "oracle_probe_requested": oracle_probe,
                            "suppressed_oracle_endpoints": sorted(suppressed)})
    return report


def print_report(report):
    print("AIDP connectivity: " + report["status"] + " | " + report["runtime"]["label"])
    if "sampling" in report:
        print("Sample outcomes: " + ", ".join(report["sampling"]["sample_statuses"]))
    for row in report["checks"]:
        location = row.get("ip", row.get("host", ""))
        sample = "sample=" + str(row["sample_index"]) + " " if "sample_index" in row else ""
        print(sample + "{status:4} {endpoint} {stage} {code} {location}".format(location=location, **row))
        if row.get("next_step"):
            print("     " + row["next_step"])
    if not report["checks"]:
        print("No endpoints configured; no network probes ran.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="Target JSON; never put credentials here")
    parser.add_argument("--output", help="Optional JSON report path")
    parser.add_argument("--label", default="AI Compute runtime", help="Network vantage point label")
    parser.add_argument("--oracle-probe", action="store_true", help="Enable one login and SELECT 1 per configured endpoint per sample")
    parser.add_argument("--samples", type=int, default=1, help="Collect 1-10 samples; default 1")
    parser.add_argument("--interval", type=float, default=5, help="Pause 0-60 seconds between completed samples")
    args = parser.parse_args(argv)
    try:
        report = run_samples(json.loads(Path(args.config).read_text()), args.label, args.oracle_probe,
                             args.samples, args.interval)
        print_report(report)
        if args.output:
            Path(args.output).write_text(json.dumps(report, indent=2) + "\n")
    except ConfigurationError as error:
        print("Configuration error: " + str(error), file=sys.stderr)
        return 2
    except (OSError, ValueError, TypeError) as error:
        # Config errors contain no source values or credential payloads.
        print("Configuration/report error: " + type(error).__name__, file=sys.stderr)
        return 2
    return {"PASS": 0, "FAIL": 1, "WARN": 3, "NOT_RUN": 2}[report["status"]]


if __name__ == "__main__":
    sys.exit(main())
