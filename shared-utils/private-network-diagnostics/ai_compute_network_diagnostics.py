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

VERSION = "1.4"
DNS_CODE = r'''
import json, socket, sys
host, port = json.load(sys.stdin)
try:
    answers = socket.getaddrinfo(host, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
    print(json.dumps({"addresses": sorted(set(
        ("IPv6" if a[0] == socket.AF_INET6 else "IPv4", a[4][0])
        for a in answers if a[0] in (socket.AF_INET, socket.AF_INET6)))}))
except socket.gaierror as error:
    code = "DNS_ERROR"
    if error.errno == getattr(socket, "EAI_AGAIN", None):
        code = "DNS_TEMPORARY_FAILURE"
    elif error.errno in {getattr(socket, "EAI_NONAME", None), getattr(socket, "EAI_NODATA", None)}:
        code = "DNS_NAME_NOT_FOUND"
    print(json.dumps({"error": code, "errno": error.errno}))
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

# next_step is a customer action; managed-service investigations are separate.
HINTS = {
    "DNS_NAME_NOT_FOUND": "The system resolver could not find an address for this name. Verify the actual descriptor HOST, then resolve it in the customer VCN's private DNS context and check its records/view/forwarding. This does not prove a DNS-server NXDOMAIN response.",
    "DNS_TEMPORARY_FAILURE": "The system resolver reported a temporary failure. Retain repeated UTC samples and compare customer DNS and affected runtimes before choosing a remedy.",
    "DNS_IP_LITERAL": "An IP literal bypasses hostname resolution. Use the original application FQDN to investigate a DNS failure; this target checks only the supplied address.",
    "DNS_SHORT_NAME": "Confirm the full endpoint hostname from the connection descriptor. A short name can depend on resolver search rules; a TNS alias must be resolved by the application's Oracle client.",
    "CONNECTIVITY_BLOCKED_BY_DNS": "Correct or verify the earlier DNS failure or confirmed expectation mismatch, then rerun. No TCP/TLS connection was attempted for this target.",
    "ORACLE_BLOCKED_BY_DNS": "Resolve the earlier DNS failure or confirmed expectation mismatch before enabling a database login for this target.",
    "ORACLE_BLOCKED_BY_TCP": "No tested address accepted TCP. Resolve the listener/connectivity failure before retrying database login.",
    "DNS_ERROR": "Verify the actual endpoint FQDN, then compare its resolution in the customer VCN's private DNS context with this affected runtime. Check customer VCN/on-premises records, views and forwarding with your network administrator.",
    "DNS_TIMEOUT": "Ask your network administrator to check customer-owned DNS forwarding and resolver reachability; compare customer VCN and affected-runtime lookups and retain the timed-out UTC attempt.",
    "DNS_NO_ADDRESSES": "Verify the endpoint FQDN and customer DNS records; an empty answer does not identify a service-side fault.",
    "DNS_EXPECTATION_MISMATCH": "Remove unverified expected_ips and collect an observation-only report. Use a runtime-visible expected set only when its source is confirmed; customer-side database IPs can differ.",
    "DNS_COUNT_MISMATCH": "Remove an unverified expected_dns_count. A workspace SCAN proxy can return one address; compare only with a confirmed runtime expectation.",
    "TCP_TIMEOUT": "Verify the endpoint and port. Ask your network administrator to review customer-owned destination rules, routes and return routing, including DRG/VPN/FastConnect for on-premises.",
    "TCP_REFUSED": "Confirm the configured listener port and protocol with the database/service owner; an active rejection alone does not identify the rejecting device.",
    "TCP_NO_ROUTE": "Ask your network administrator to check customer-owned routes and the return path. Compare customer baseline and affected-runtime results for the same listener.",
    "TCP_ERROR": "Retain the errno and confirm endpoint/port and customer network checks; the socket error alone does not locate the fault.",
    "IPV6_SOURCE_UNAVAILABLE": "Confirm the endpoint's required IPv4/IPv6 path with your network administrator. Retain the coverage warning; an IPv4 pass does not verify IPv6.",
    "TLS_VERIFY_FAILED": "Verify the endpoint identity, trusted CA and runtime wallet configuration. Do not disable certificate verification or replace the hostname with an arbitrary IP.",
    "TLS_FAILED": "Confirm TCPS versus TCP, TLS policy and wallet requirements. Use the compatible Oracle client probe for a database rather than treating raw TLS as a database login.",
    "MTLS_WALLET_UNVERIFIED": "The network probe did not use your database wallet, so wallet-based login is not verified by this check. Use your compatible Oracle client with the configured wallet; this coverage warning can remain even when that login succeeds.",
    "SCAN_REDIRECT_UNVERIFIED": "Check the customer-visible workspace SCAN name/port and obtain the database service details from its owner. Run one Oracle probe to exercise a redirect; direct VIP checks are supplemental.",
    "ORACLE_DRIVER_MISSING": "Install an approved python-oracledb version through your runtime's supported dependency mechanism, then rerun the optional database probe.",
    "ORACLE_ENV_MISSING": "Provide the configured environment values through your runtime secret mechanism; keep values out of notebooks, target JSON and reports.",
    "ORACLE_PROBE_NOT_CONFIGURED": "Add the optional Oracle client settings using environment-variable names, or continue with network-only checks.",
    "ORACLE_PROBE_NOT_ENABLED": "To validate database login or SCAN redirection, configure and explicitly enable the optional Oracle probe; network-only success does not cover either.",
    "ORACLE_RETRY_SUPPRESSED": "An earlier account/authentication error stopped further sampled logins. Contact the database owner before running another login attempt.",
    "ORACLE_TIMEOUT": "Compare DNS/TCP/TLS results and the actual client settings. Ask the database owner to review login, redirect and query readiness; the total deadline does not identify which operation stalled.",
    "ORA-01017": "For a customer-owned database, have its DBA verify the configured account and credential reference before retrying; network changes do not fix rejected credentials.",
    "ORA-28000": "Contact the database owner about the locked account. Avoid further logins until corrected; customers should not attempt to unlock service-managed accounts.",
    "ORA-28001": "For a customer-owned database, coordinate password renewal with its DBA and update the runtime's credential reference before one controlled login.",
    "ORA-17868": "Verify the connection descriptor's actual hostname and application alias setup. Compare the affected runtime's DNS result; a Python host probe does not validate JDBC alias resolution.",
    "ORA-12514": "Verify the selected database service from its connection information. For a customer-operated database, ask its DBA to review listener registration; for a managed database, review its documented connection settings and customer-visible status.",
    "ORA-12523": "Ask the database owner to check service/instance readiness. Customer-operated listeners belong to the DBA; managed database listener state requires Oracle Support.",
    "DPY-6001": "Verify the selected service name and ask the database owner to check registration; socket reachability alone is insufficient.",
    "DPY-6005": "This is a generic Oracle connection error. Compare DNS/TCP/TLS stages and matching client settings before choosing a network, wallet or database remedy.",
    "ORA-12506": "Ask the database owner to verify the permitted source and customer-visible access controls; do not infer a missing network rule from this code.",
    "ORA-28759": "Verify your mounted wallet files, runtime access and the selected Oracle client mode; do not copy wallet contents into reports.",
    "DPY-3010": "Confirm database-version compatibility with the selected approved Oracle client mode using the driver's documentation.",
    "DPY-3001": "Ask the database owner whether native network encryption is required, and use a compatible client mode and supported runtime configuration.",
    "DPI-1047": "Check the Oracle Client installation and dependencies in your runtime, or select Thin mode only when compatible with the workload.",
    "ADDRESS_COVERAGE_INCOMPLETE": "The address cap omitted some returned addresses. Review max_addresses and rerun within the supported cap if needed; do not claim coverage of untested addresses.",
    "CHILD_PROCESS_FAILED": "Check supported Python/runtime execution permissions and dependencies. The failed probe process is not proof of a DNS or database fault.",
    "ORACLE_PROCESS_FAILED": "Check runtime Python/client prerequisites; the failed probe process does not identify a database or network cause.",
    "ORACLE_CLIENT_ERROR": "Compare the actual client settings and failed stage with the original workload; retain sanitized error codes and request IDs for its owner."
}

RUNBOOK_SECTIONS = {
    code: section
    for section, codes in {
        "dns": ("DNS_ERROR", "DNS_TIMEOUT", "DNS_NO_ADDRESSES", "DNS_NAME_NOT_FOUND",
                "DNS_TEMPORARY_FAILURE", "DNS_IP_LITERAL", "DNS_SHORT_NAME", "ORA-17868",
                "CONNECTIVITY_BLOCKED_BY_DNS", "ORACLE_BLOCKED_BY_DNS"),
        "dns-expectations": ("DNS_EXPECTATION_MISMATCH", "DNS_COUNT_MISMATCH"),
        "tcp": ("TCP_TIMEOUT", "TCP_REFUSED", "TCP_NO_ROUTE", "TCP_ERROR", "ORACLE_BLOCKED_BY_TCP"),
        "tls": ("TLS_VERIFY_FAILED", "TLS_FAILED", "MTLS_WALLET_UNVERIFIED"),
        "scan": ("SCAN_REDIRECT_UNVERIFIED",),
        "accounts": ("ORA-01017", "ORA-28000", "ORA-28001", "ORACLE_RETRY_SUPPRESSED"),
        "oracle-service": ("ORA-12514", "ORA-12523", "DPY-6001", "ORA-12506"),
        "client": ("ORACLE_DRIVER_MISSING", "ORACLE_ENV_MISSING", "ORACLE_PROBE_NOT_CONFIGURED",
                   "ORACLE_PROBE_NOT_ENABLED", "ORA-28759", "DPY-3010", "DPY-3001", "DPI-1047"),
        "coverage": ("IPV6_SOURCE_UNAVAILABLE", "ADDRESS_COVERAGE_INCOMPLETE"),
        "runtime": ("CHILD_PROCESS_FAILED", "ORACLE_PROCESS_FAILED", "EXECUTOR_CONFIG_UNAVAILABLE"),
        "unclassified": ("DPY-6005", "ORACLE_TIMEOUT", "ORACLE_CLIENT_ERROR"),
    }.items()
    for code in codes
}

SUPPORT_HINTS = {
    "dns": "If customer DNS/settings are verified, ask Oracle Support to inspect the affected Compute's effective attachment, service-managed DNS/CoreDNS and internal mapping propagation. Customers cannot inspect these controls.",
    "dns-expectations": "Ask Oracle Support to confirm the runtime-visible mapping when it is unavailable to you; service-managed RCE/NAT mappings are not customer configuration.",
    "tcp": "If customer destination/routing checks pass, ask Oracle Support to inspect service-managed NSGs, routing, private connectivity and RCE/proxy health for this runtime and UTC attempt.",
    "tls": "Escalate if the compatible application/Oracle client still fails after customer trust, identity and wallet checks; ask Support to correlate the service path without disabling TLS verification.",
    "scan": "If the configured SCAN and customer listener paths are valid but the real connection fails, ask Support to inspect service-managed SCAN proxy/redirect translation for the recorded attempt.",
    "accounts": "For a service-managed database or account, Oracle Support must investigate credentials/account state; customers are not expected to access or unlock it.",
    "oracle-service": "For managed database listener/readiness issues, contact the database service's Oracle Support team. For AIDP-only failures with the same valid service, request AIDP path correlation.",
    "client": "If the documented runtime dependency/credential mechanism fails or is inaccessible, ask the workspace administrator or Oracle Support for the supported path; do not modify managed service images.",
    "coverage": "If the required address family/path cannot be tested in the affected runtime, ask Support to confirm its supported networking and record the untested coverage.",
    "runtime": "If supported runtime execution or executor access is unavailable, ask Support to inspect the runtime failure using the affected resource and UTC window.",
    "unclassified": "For a service-managed dependency or unresolved AIDP-only failure, ask Oracle Support to correlate the underlying error and owner-side operation outcome before selecting a service-network remedy."
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
        if "_" in name:
            raise ConfigurationError("Use the actual HOST from the connection descriptor; a TNS alias belongs in the application's Oracle client configuration")
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
    section = RUNBOOK_SECTIONS.get(code)
    if section is None and status in {"FAIL", "WARN"}:
        section = "unclassified"
        result.setdefault("next_step", "Retain the failed stage/code and compare the actual application settings; the result alone does not identify a cause.")
    if section:
        result["runbook_ref"] = "RUNBOOK.md#" + section
        if status in {"FAIL", "WARN"} or code == "ORACLE_RETRY_SUPPRESSED":
            result["support_next_step"] = SUPPORT_HINTS[section]
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
        if isinstance(error, (TimeoutError, socket.timeout)) or error.errno == errno.ETIMEDOUT:
            code = "TCP_TIMEOUT"
        elif error.errno == errno.ECONNREFUSED:
            code = "TCP_REFUSED"
        elif error.errno in {errno.ENETUNREACH, errno.EHOSTUNREACH}:
            code = "TCP_NO_ROUTE"
        elif family == socket.AF_INET6 and error.errno in {errno.EADDRNOTAVAIL, errno.EAFNOSUPPORT}:
            code, status = "IPV6_SOURCE_UNAVAILABLE", "WARN"
        results.append(check(status, code, "tcp", name, errno=error.errno, **details))
    return results


def resolve_endpoint(endpoint, timeout):
    """Resolve without opening destination sockets; retain resolver-layer evidence."""
    name, host, port = endpoint["name"], endpoint["host"], endpoint["port"]
    start = time.monotonic()
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        resolved = {"addresses": [["IPv6" if literal.version == 6 else "IPv4", str(literal)]]}
    else:
        resolved = child_json(DNS_CODE, [host, port], timeout)
    if "error" in resolved:
        code = {"TIMEOUT": "DNS_TIMEOUT"}.get(resolved["error"], resolved["error"])
        return [check("FAIL", code, "dns", name, host=host, errno=resolved.get("errno"),
                      elapsed_ms=round((time.monotonic() - start) * 1000, 1))], []
    addresses = resolved.get("addresses", [])
    if not addresses:
        return [check("FAIL", "DNS_NO_ADDRESSES", "dns", name, host=host)], []
    results = [check("SKIP" if literal is not None else "PASS",
                     "DNS_IP_LITERAL" if literal is not None else "DNS_RESOLVED", "dns", name, host=host,
                     addresses=addresses, elapsed_ms=round((time.monotonic() - start) * 1000, 1))]
    if literal is None and "." not in host and host != "localhost":
        results.append(check("WARN", "DNS_SHORT_NAME", "dns", name, host=host))
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
    # A failed expectation may identify an unintended destination. Do not connect.
    return results, [] if any(row["status"] == "FAIL" for row in results) else addresses


def probe_addresses(endpoint, addresses, timeout, max_addresses):
    name = endpoint["name"]
    if not addresses:
        return [check("SKIP", "CONNECTIVITY_BLOCKED_BY_DNS", "tcp", name)]
    results = []
    if len(addresses) > max_addresses:
        results.append(check("WARN", "ADDRESS_COVERAGE_INCOMPLETE", "coverage", name,
                             tested=max_addresses, total=len(addresses)))
    for address in addresses[:max_addresses]:
        results.extend(socket_probe(endpoint, address, timeout))
    return results


def probe_endpoint(endpoint, timeout, max_addresses):
    """Convenience check of one endpoint, preserving DNS-before-connectivity."""
    results, addresses = resolve_endpoint(endpoint, timeout)
    return results + probe_addresses(endpoint, addresses, timeout, max_addresses)


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


def run_diagnostics(config, label="runtime", oracle_probe=False, oracle_suppressed=(), dns_only=False):
    if dns_only and oracle_probe:
        raise ConfigurationError("DNS-only checks cannot enable an Oracle login")
    config = validate_config(config)
    results, plans, primary = [], [], {}
    # Phase 1 resolves every configured name, including supplemental listeners,
    # before any TCP/TLS socket or database login is attempted in this sample.
    for endpoint in config["endpoints"]:
        checks, addresses = resolve_endpoint(endpoint, config["timeout_seconds"])
        results.extend(checks)
        plan = {"endpoint": endpoint, "addresses": addresses}
        primary[endpoint["name"]] = plan
        plans.append(plan)
        for index, target in enumerate(endpoint["redirect_targets"], 1):
            child = dict(target, name=endpoint["name"] + ":listener-" + str(index))
            checks, addresses = resolve_endpoint(child, config["timeout_seconds"])
            results.extend(checks)
            plans.append({"endpoint": child, "addresses": addresses})
    # Phase 2 uses the addresses observed in this sample. Failed targets are
    # explicitly skipped; other targets can still provide comparison evidence.
    if not dns_only:
        for plan in plans:
            plan["network"] = probe_addresses(plan["endpoint"], plan["addresses"],
                                              config["timeout_seconds"], config["max_addresses"])
            results.extend(plan["network"])
    # Phase 3 is opt-in and never retries login after failed prerequisites.
    for endpoint in config["endpoints"] if not dns_only else []:
        plan = primary[endpoint["name"]]
        oracle_ok = False
        if oracle_probe and endpoint["name"] in oracle_suppressed:
            results.append(check("SKIP", "ORACLE_RETRY_SUPPRESSED", "oracle", endpoint["name"]))
        elif oracle_probe and "oracle" in endpoint:
            if not plan["addresses"]:
                results.append(check("SKIP", "ORACLE_BLOCKED_BY_DNS", "oracle", endpoint["name"]))
            elif not any(row["code"] == "TCP_CONNECTED" for row in plan["network"]):
                results.append(check("SKIP", "ORACLE_BLOCKED_BY_TCP", "oracle", endpoint["name"]))
            else:
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
    if dns_only and status == "PASS" and not any(row["code"] == "DNS_RESOLVED" for row in results):
        # Numeric-only targets cannot establish successful hostname resolution.
        status = "NOT_RUN"
    return dict(version=VERSION, generated_at_utc=datetime.now(timezone.utc).isoformat(),
                runtime=runtime_context(label), status=status, counts=counts,
                test_scope="dns_only" if dns_only else "network_and_oracle" if oracle_probe else "network",
                not_run_stages=["tcp", "tls", "oracle", "scan_redirect"] if dns_only else [],
                limits=["Runtime-local observations; does not inspect OCI routes, NSGs, PE/RCE configuration, or database server state.",
                        "Customers inspect their own permitted resources; service-managed CoreDNS, NSGs and internal RCE/proxy state require Oracle Support.",
                        "Raw socket/TLS probes do not exercise Oracle Net or wallet authentication.",
                        "One Oracle login validates one chosen route and SELECT 1, not all RAC nodes, redirects, failover, or application queries.",
                        "Synthetic/reserved address classification alone does not prove an RCE mapping or private network path."],
                checks=results)


def run_samples(config, label="runtime", oracle_probe=False, sample_count=1, interval_seconds=5, dns_only=False):
    """Collect bounded samples; retain failures even if a later sample passes."""
    _number(sample_count, 1, 10, "sample_count", integer=True)
    _number(interval_seconds, 0, 60, "interval_seconds")
    config = validate_config(config)
    if dns_only and oracle_probe:
        raise ConfigurationError("DNS-only checks cannot enable an Oracle login")
    if sample_count == 1 or not config["endpoints"]:
        return run_diagnostics(config, label, oracle_probe, dns_only=dns_only)
    checks, statuses, starts, suppressed = [], [], [], set()
    for index in range(1, sample_count + 1):
        if index > 1:
            time.sleep(interval_seconds)
        starts.append(datetime.now(timezone.utc).isoformat())
        report = run_diagnostics(config, label, oracle_probe, oracle_suppressed=suppressed, dns_only=dns_only)
        statuses.append(report["status"])
        suppressed.update(row["endpoint"] for row in report["checks"]
                          if row["stage"] == "oracle" and row["code"] in {"ORA-01017", "ORA-28000", "ORA-28001"})
        checks.extend(dict(row, sample_index=index) for row in report["checks"])
    counts = dict(Counter(row["status"] for row in checks))
    report.update(checks=checks, counts=counts,
                  status="FAIL" if counts.get("FAIL") else "WARN" if counts.get("WARN") else "NOT_RUN" if all(s == "NOT_RUN" for s in statuses) else "PASS",
                  sampling={"sample_count": sample_count, "interval_seconds": interval_seconds,
                            "interval_policy": "Pause after the previous sample completes",
                            "started_at_utc": starts, "sample_statuses": statuses,
                            "oracle_probe_requested": oracle_probe,
                            "suppressed_oracle_endpoints": sorted(suppressed)})
    return report


def print_report(report, max_rows=None, show_support_guidance=False):
    if max_rows is not None:
        _number(max_rows, 1, 1000, "max_rows", integer=True)
    print("AIDP diagnostics: " + report["status"] + " | Scope: " + report.get("test_scope", "runtime") + " | " + report["runtime"]["label"])
    print("Runtime: " + report["runtime"]["hostname"] + " | Checks: " + str(report["counts"]))
    if report.get("not_run_stages"):
        print("Not run: " + ", ".join(report["not_run_stages"]))
    if "sampling" in report:
        print("Sample outcomes: " + ", ".join(report["sampling"]["sample_statuses"]))
    for row in report["checks"][:max_rows]:
        location = row.get("ip", row.get("host", ""))
        sample = "sample=" + str(row["sample_index"]) + " " if "sample_index" in row else ""
        print(sample + "{status:4} {endpoint} {stage} {code} {location}".format(location=location, **row))
        if row.get("addresses"):
            print("     Addresses: " + ", ".join(family + " " + ip for family, ip in row["addresses"]))
        if row.get("next_step"):
            print("     Customer: " + row["next_step"])
        if show_support_guidance and row.get("support_next_step"):
            print("     Oracle Support: " + row["support_next_step"])
        if row.get("runbook_ref"):
            print("     Runbook: " + row["runbook_ref"])
    if not report["checks"]:
        print("No endpoints configured; no network probes ran.")
    elif max_rows is not None and len(report["checks"]) > max_rows:
        print("Display truncated; all " + str(len(report["checks"])) + " checks are retained in the report and JSON export.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="Target JSON; never put credentials here")
    parser.add_argument("--output", help="Optional JSON report path")
    parser.add_argument("--label", default="AI Compute runtime", help="Network vantage point label")
    parser.add_argument("--oracle-probe", action="store_true", help="Enable one login and SELECT 1 per configured endpoint per sample")
    parser.add_argument("--dns-only", action="store_true", help="Resolve names and compare confirmed expectations without TCP/TLS or database login")
    parser.add_argument("--show-support-guidance", action="store_true", help="Also display conditional Support steps; JSON retains them regardless")
    parser.add_argument("--samples", type=int, default=1, help="Collect 1-10 samples; default 1")
    parser.add_argument("--interval", type=float, default=5, help="Pause 0-60 seconds between completed samples")
    args = parser.parse_args(argv)
    try:
        report = run_samples(json.loads(Path(args.config).read_text()), args.label, args.oracle_probe,
                             args.samples, args.interval, dns_only=args.dns_only)
        print_report(report, show_support_guidance=args.show_support_guidance)
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
