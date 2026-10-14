#!/usr/bin/env python3
# Copyright (c) 2026 Oracle and/or its affiliates.
# Licensed under the Universal Permissive License (UPL), Version 1.0.
# See ../../LICENSE.txt.

"""Build the portable Compute notebook from the AI Compute diagnostic script."""

import hashlib
import json
from pathlib import Path

DIRECTORY = Path(__file__).resolve().parent
OUTPUT = DIRECTORY / "AIDP_Compute_Private_Network_Diagnostics.ipynb"


def cell(kind, source, hidden=False):
    result = dict(cell_type=kind, id=hashlib.sha256(source.encode()).hexdigest()[:12],
                  metadata={"jupyter": {"source_hidden": True}} if hidden else {},
                  source=source.splitlines(keepends=True))
    if kind == "code":
        compile(source, "notebook-cell", "exec")
        result.update(execution_count=None, outputs=[])
    return result


def build():
    source = (DIRECTORY / "ai_compute_network_diagnostics.py").read_text()
    source = source.split('\nif __name__ == "__main__":')[0] + "\n"
    # Keep the implementation inspectable and byte-identical to the script,
    # without executing the script's command-line entry point in a notebook.
    sections = []
    for marker in ("def _keys(", "def check(", "def run_diagnostics("):
        before, source = source.split(marker, 1)
        sections.append(before)
        source = marker + source
    sections.append(source)
    cells = [
        cell("markdown", """# AIDP Compute private network diagnostics

Copyright (c) 2026 Oracle and/or its affiliates. Licensed under the Universal
Permissive License (UPL), Version 1.0. See the repository LICENSE.txt.

Start with ADB, then add Oracle E-Business Suite database listeners, on-premises
databases, and RAC SCAN endpoints. This notebook uses the same diagnostics as
`ai_compute_network_diagnostics.py` and runs without a sibling script or OCI SDK.

Run in the **affected private workspace and Compute**. The default checks run
where this notebook's Python kernel runs; confirm that location in your AIDP
setup. An optional executor sample is available below. The initial empty target
list generates no network traffic. Python 3.9+ is required.

Run **DNS resolution first**, then enable connectivity checks. Each connectivity
sample resolves all configured names before TCP/TLS and optional Oracle login.
DNS failures or expectation mismatches skip downstream probes for that target.
The checks do not change network configuration. A failed attempt provides
evidence and investigation hints, rather than a definitive network root cause.

Establish the [customer VCN DNS baseline](RUNBOOK.md#customer-dns), then compare
DNS and connectivity from this affected runtime. Customer-context resolution
can return translated addresses here; differing IP sets alone are not a fault.
Use the [decision table](RUNBOOK.md#comparison) to choose a customer correction
and retest. Review [advanced DNS](RUNBOOK.md#advanced-dns) only for an applicable
datasource; a workspace access list does not repair a missing customer DNS record.

Keep the customer runbook alongside the notebook or open it from the sample
repository. Displayed results show customer actions by default. Conditional
Support guidance is retained in JSON; enable `SHOW_SUPPORT_GUIDANCE` only when
needed for an unresolved issue. No service-internal access is required to begin.
"""),
        cell("markdown", """## 1. Configure endpoints

Use the connection method your application already has. DNS and listener-port
checks do not require a wallet. They establish reachability; the real client
connection verifies the required security setup and database login.

| Existing connection | Network configuration | Connection check after DNS and TCP |
| --- | --- | --- |
| ADB with a client wallet for mTLS | `protocol: "tcps"`, `tls_mode: "mtls"` | Compatible Oracle client with the mounted wallet |
| ADB without a wallet, using allowed TLS | `protocol: "tcps"`, `tls_mode: "tls"` | Server certificate verification and compatible Oracle client |
| On-premises Oracle using TCP | `protocol: "tcp"` | Existing Oracle client/security settings; no raw TLS check |
| On-premises Oracle using TCPS | `protocol: "tcps"`, DBA-confirmed TLS/client-certificate settings | Matching Oracle client; wallet only where required |

For ADB, copy HOST and PORT from the selected **Database Connection** descriptor.
Both methods are encrypted; mTLS uses 1522, while TLS can use 1521 or 1522.
For on-premises sources, follow the existing datasource configuration. Oracle
native network encryption can use TCP and requires compatible client setup;
other datasource logins require their own client. Wallet presence alone does not
identify the mode. [Connection-method runbook](RUNBOOK.md#connection-method),
[ADB connection details](https://docs.oracle.com/en/cloud/paas/autonomous-database/serverless/adbsb/connect-download-wallet.html)

`expected_ips` is optional: leave it empty unless the complete runtime-visible
set is confirmed. Backend customer IPs may differ; continue the network and
client checks without a mapping inventory. Omit `expected_dns_count` when unknown.

For `kind: "scan"`, optionally list known listener VIPs/FQDNs and ports in
`redirect_targets`. These are supplemental probes. AIDP SCAN proxy DNS can
expose one address; set `expected_dns_count` only when you know the expected
runtime behavior. The Oracle client probe is needed to exercise a real redirect.
[AIDP private connectivity](https://blogs.oracle.com/dataintegration/aidp-private-workspace-connectivity-to-oracle-databases)

Example target to adapt (the `.invalid` placeholder is rejected until replaced):

```python
{
    "name": "ADB using a wallet", "kind": "adb",
    "host": "adb-private.example.invalid", "port": 1522,
    "protocol": "tcps", "tls_mode": "mtls", "expected_ips": []
}
```

For walletless ADB TLS, select its allowed TLS host/port, set `tls_mode: "tls"`
and omit wallet environment references from `oracle`. `targets.example.json`
contains separate ADB wallet and walletless templates, plus cloud EBS, on-premises
DBs and SCAN. Keep only the endpoints you need.
Never place passwords or wallet contents in this notebook or target JSON.

Use the actual endpoint HOST in `host`, rather than an Oracle TNS alias such as
`dbname_high`. Validate alias lookup, `tnsnames.ora`, `TNS_ADMIN` and wallet setup
with the application's JDBC client. The optional Python probe builds an explicit
host/service connection and does not reproduce arbitrary JDBC alias resolution.

Include non-database dependencies with `kind: "dependency"`, using their actual
hostname, port and TCP/TLS policy. UDF service DNS failures can occur even when
ADB access works. These probes do not validate HTTP, SDK or service functionality.
"""),
        cell("code", """# Edit this list, or set CONFIG_PATH to your uploaded target JSON.
CONFIG_PATH = None
CONFIG = {
    "timeout_seconds": 5,
    "oracle_timeout_seconds": 20,
    "max_addresses": 16,
    "endpoints": [],
}
RUNTIME_LABEL = "Compute notebook Python runtime"
RUN_CONNECTIVITY_CHECKS = False  # Enable after reviewing section 3 DNS results.
RUN_ORACLE_PROBE = False
SHOW_SUPPORT_GUIDANCE = False  # Optional fallback; JSON retains conditional hints.
REPEAT_SAMPLES = 1  # Opt in to 2-10 samples for intermittent failures.
SAMPLE_INTERVAL_SECONDS = 5  # Pause after each completed sample, 0-60 seconds.
EXECUTOR_SAMPLE_PARTITIONS = 0  # Opt in, up to 16; requires classic PySpark RDDs.
REPORT_PATH = None  # Optional writable path, e.g. /tmp/aidp-connectivity.json.
"""),
        cell("markdown", """## 2. Load the shared diagnostics

These collapsed cells embed the script's implementation so the notebook remains
portable. Expand them to inspect the code. DNS and Oracle probes run in bounded
child processes. TCP and TLS have individual timeouts; the total run can take
several minutes for multiple endpoints and addresses. Each probe runs once per
sample; repeat sampling is optional and bounded to ten samples.
"""),
    ]
    cells.extend(cell("code", section, hidden=True) for section in sections)
    cells.extend([
        cell("markdown", """## 3. Check DNS resolution first

Resolve the exact application host, plus explicitly supplied SCAN listener names.
Review every returned address and any confirmed runtime-visible expectations.
This step makes no destination TCP/TLS connection and no database login; DNS-only
PASS does not verify connectivity. Numeric IP targets skip hostname resolution.

For `DNS_NAME_NOT_FOUND`, verify the actual descriptor HOST and the customer VCN's
records, private view and on-premises forwarding. For temporary failures/timeouts,
retain repeated samples and compare customer DNS with this affected runtime.
System-resolver results do not reveal DNS response codes, TTLs or the active
workspace resolution context. Follow the [DNS runbook](RUNBOOK.md#dns).

Use the actual catalog/default Compute for catalog failures, and executor samples
for executor-side UDF dependencies. Success from another runtime is comparison
evidence. If an explicit hostname resolves but JDBC reports UnknownHostException,
validate its alias configuration and JVM resolver with the workload owner.
"""),
        cell("code", """if CONFIG_PATH:
    CONFIG = json.loads(Path(CONFIG_PATH).read_text())
CONFIG = validate_config(CONFIG)  # Validate all destinations before probing.

def show_summary(result, max_rows=40):
    print_report(result, max_rows=max_rows, show_support_guidance=SHOW_SUPPORT_GUIDANCE)

dns_report = run_samples(CONFIG, label=RUNTIME_LABEL, dns_only=True,
                         sample_count=REPEAT_SAMPLES, interval_seconds=SAMPLE_INTERVAL_SECONDS)
show_summary(dns_report)
"""),
        cell("markdown", """## 4. Check connectivity after DNS

After reviewing DNS results, set `RUN_CONNECTIVITY_CHECKS = True` in section 1
and rerun the configuration cell and this cell. Each sample resolves names again
to avoid using an old report.
Targets with failed DNS/expectations skip TCP/TLS and database login; other targets
continue as comparisons. Every returned address is checked up to the configured cap.
If no tested address accepts TCP, the Oracle login is skipped. TCP success permits
the optional Oracle probe; raw mTLS warnings need the compatible wallet-backed client.

The optional Oracle probe needs an approved `python-oracledb` installation and
the environment variables named in each endpoint's `oracle` settings. It uses
the same host/port/protocol plus the service name, performs one login and
`SELECT 1 FROM DUAL`, and reports only error codes. Credentials and wallet
paths stay out of reports. With Thin mTLS, mount a supported wallet directory
containing `ewallet.pem`; choose Thick mode when the workload requires it and
install/configure the Oracle client separately.
[Oracle client configuration](https://python-oracledb.readthedocs.io/en/latest/user_guide/connection_handling.html)

The network probe does not use your database wallet. `MTLS_WALLET_UNVERIFIED`
therefore means the wallet-based connection was not tested by that check. Inspect
the optional Oracle result and original workload separately; this coverage warning
can remain when the real wallet-based login succeeds.

For intermittent failures, set `REPEAT_SAMPLES` to 2-10. Each check has a UTC
timestamp and sample index. Earlier failures remain in the report when later
samples pass. Each enabled Oracle probe can attempt a login per sample; after
ORA-01017, ORA-28000 or ORA-28001, further logins to that endpoint are skipped.
The interval is a pause after the previous sample completes, not a fixed cadence.
"""),
        cell("code", """connectivity_status = "NOT_REQUESTED"
report = dns_report
if RUN_CONNECTIVITY_CHECKS:
    report = run_samples(CONFIG, label=RUNTIME_LABEL, oracle_probe=RUN_ORACLE_PROBE,
                         sample_count=REPEAT_SAMPLES, interval_seconds=SAMPLE_INTERVAL_SECONDS)
    connectivity_status = report["status"]
    show_summary(report)
else:
    print("Connectivity not requested. Review DNS results, then enable RUN_CONNECTIVITY_CHECKS in section 1.")
"""),
        cell("markdown", """## 5. Optional Spark executor sample

Leave the partition count at zero for notebook-only checks. This sample runs
DNS-only checks initially, and network checks when `RUN_CONNECTIVITY_CHECKS` is
enabled, without database login so it does not distribute credentials.
It requires classic PySpark RDD support; Spark Connect runtimes without RDDs
are explicitly skipped. Oracle wallet/CA files may be located differently on
executors; deploy any configured CA bundle there before sampling.

Partition count is **not** executor count: Spark may schedule several tasks on
one host and miss other executors. Hostnames show observed coverage. Scheduling
and job duration are managed by Spark, beyond the individual probe timeouts.
"""),
        cell("code", """executor_reports = []
executor_sample_status = "NOT_REQUESTED"
if EXECUTOR_SAMPLE_PARTITIONS:
    _number(EXECUTOR_SAMPLE_PARTITIONS, 1, 16, "EXECUTOR_SAMPLE_PARTITIONS", integer=True)
    try:
        sample_context = spark.sparkContext
    except (NameError, AttributeError, NotImplementedError):
        sample_context = None
    except Exception:
        # Some Spark Connect versions raise a specific unsupported-operation error.
        sample_context = None
    if sample_context is None:
        executor_sample_status = "SKIPPED_NO_RDD_SUPPORT"
        print("Executor sample skipped: this runtime has no classic Spark RDD support.")
    elif not CONFIG["endpoints"]:
        executor_sample_status = "SKIPPED_NO_TARGETS"
        print("Executor sample skipped: no endpoints configured.")
    else:
        def probe_partition(iterator):
            try:
                yield run_samples(CONFIG, label="Spark executor sample", oracle_probe=False,
                                  dns_only=not RUN_CONNECTIVITY_CHECKS,
                                  sample_count=REPEAT_SAMPLES, interval_seconds=SAMPLE_INTERVAL_SECONDS)
            except (ValueError, OSError):
                # For example, a CA file mounted only in the notebook runtime.
                yield {
                    "status": "WARN", "runtime": runtime_context("Spark executor sample"),
                    "counts": {"WARN": 1}, "checks": [check(
                        "WARN", "EXECUTOR_CONFIG_UNAVAILABLE", "configuration", "executor",
                        next_step="Check target configuration and CA file deployment in this executor runtime."
                    )],
                }

        try:
            executor_reports = sample_context.parallelize(
                range(EXECUTOR_SAMPLE_PARTITIONS), EXECUTOR_SAMPLE_PARTITIONS
            ).mapPartitions(probe_partition).collect()
            executor_sample_status = "SAMPLED"
            print("Sampled hosts:", sorted({r["runtime"]["hostname"] for r in executor_reports}))
            for executor_report in executor_reports:
                show_summary(executor_report, max_rows=10)
        except Exception as error:
            executor_sample_status = "FAILED"
            print("Executor sample failed:", type(error).__name__, "- review the Spark job logs; no executor coverage established.")
else:
    print("Executor sample not requested; notebook results do not verify executors.")
"""),
        cell("markdown", """## 6. Export evidence and interpret results

Start with the failed stage's [runbook section](RUNBOOK.md). Compare customer DNS
and this runtime using the [decision table](RUNBOOK.md#comparison). Verify your
endpoint/client settings, customer DNS, listener, source-specific network rules
and routes with the customer network/database/workspace administrators. Correct
the identified customer-owned setting and repeat the relevant checks.

Retain before/after reports and rerun the original operation in the affected
runtime. A socket or one database connection does not prove catalog, job or AI
Compute recovery. If the failure remains unresolved, use the [fallback Support
handoff](RUNBOOK.md#support). To display conditional guidance without new probes,
set `SHOW_SUPPORT_GUIDANCE = True` and call `show_summary(report)` for the saved
report. Service-internal access and mapping expectations are not required inputs.

SCAN redirects remain unverified until an Oracle client connection succeeds.
One successful connection cannot validate every node or failover path. Raw
probes do not prove wallet authentication, JDBC configuration, EBS functionality,
or the correctness of VCN rules. Proxy-based application connections need an
application-specific check. Handle exported endpoint/network details as
operational evidence.
"""),
        cell("code", """evidence = {
    "dns_resolution": dns_report,
    "connectivity_status": connectivity_status,
    "notebook_runtime": report,
    "executor_sample_status": executor_sample_status,
    "executor_samples": executor_reports,
}
if REPORT_PATH:
    Path(REPORT_PATH).write_text(json.dumps(evidence, indent=2) + "\\n")
    print("Saved diagnostic evidence to the configured report path.")
else:
    print("Evidence retained in the evidence variable; set REPORT_PATH to export JSON.")
"""),
    ])
    notebook = dict(cells=cells, metadata={
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {"name": "python", "version": "3.9"},
        "aidp_diagnostics": {"source": "shared-utils/private-network-diagnostics/ai_compute_network_diagnostics.py", "version": "1.4"},
    }, nbformat=4, nbformat_minor=5)
    try:
        import nbformat
    except ImportError:
        # No third-party packages are needed to generate this portable artifact.
        pass
    else:
        nbformat.validate(nbformat.from_dict(notebook))
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(notebook, indent=1) + "\n")
    print(OUTPUT)


if __name__ == "__main__":
    build()
