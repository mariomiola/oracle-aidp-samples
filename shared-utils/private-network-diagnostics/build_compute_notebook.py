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

Use this notebook on the Compute where your datasource connection fails.
Start with ADB, then add EBS database listeners, on-premises databases or RAC SCAN.
No separate terminal, VM or Cloud Shell session is needed to begin.

1. Identify your workspace network and enter the datasource HOST/PORT below.
2. Run sections 1, 2 and 3 to check **DNS resolution**.
3. Review the result, then enable section 4 to check **listener connectivity**.
4. Use your existing database client and rerun the original operation.

The [runbook](RUNBOOK.md) explains how the results relate to your DNS views,
forwarding, network routes and connection rules. The notebook embeds the shared
AI Compute script. Empty targets cause no traffic; network checks need Python
3.9+ and its standard library. Results cover this Python kernel's path; confirm
it is the affected Compute. Optional executor sampling is in section 5.

Copyright (c) 2026 Oracle and/or its affiliates. Licensed under the Universal
Permissive License (UPL), Version 1.0. See the repository LICENSE.txt.
"""),
        cell("markdown", """## 1. Identify your network and configure the datasource

Review your workspace's private-network settings and the existing connection.
Use these details to interpret the DNS and connectivity results. Unknown details
can be investigated after the first check; they do not prevent running it.

| Network detail | What to identify |
| --- | --- |
| Workspace VCN/subnet | The customer network selected by this workspace |
| Datasource HOST/PORT | Actual hostname and listener port from the connection information |
| DNS source | OCI private zone/view, another VCN's DNS, or on-premises DNS via forwarding |
| Datasource location | Same VCN, peered VCN/region, or on-premises through DRG/VPN/FastConnect |
| Connection controls | Applicable workspace-selected NSGs, subnet rules, destination rules and return route |
| SCAN, if used | Workspace SCAN hostname/port and database service |

The customer VCN DNS context selects the backend destination for ordinary private
names. AIDP can return translated addresses, so do not require the database's
customer-side IP or a fixed SCAN answer count here. Leave `expected_ips` empty
and omit `expected_dns_count` unless confirmed for this runtime.
[AIDP private connectivity architecture](https://blogs.oracle.com/dataintegration/aidp-private-workspace-connectivity-to-oracle-databases)

Copy HOST and PORT from your selected ADB **Database Connection** descriptor,
or the existing on-premises/EBS/SCAN descriptor. A TNS alias such as `dbname_high`
is not the actual HOST. Use the current connection method; DNS and listener
checks do not require a wallet or database login.

Example target to paste into `CONFIG["endpoints"]` below and adapt:

```python
{
    "name": "ADB", "kind": "adb",
    "host": "replace-with-your-adb-host.example.invalid",
    "port": 1522,  # Copy the actual PORT from your connection information.
    "protocol": "tcps", "tls_mode": "mtls"  # Existing wallet-based method.
}
```

For an existing permitted walletless ADB connection, use its TLS descriptor's
HOST/PORT and `tls_mode: "tls"`. For on-premises Oracle, use its existing TCP/TCPS
settings. `targets.example.json` contains templates for each case; keep only the
targets you need. Replace every `.invalid` hostname before running. Never put
passwords or wallet contents in target JSON or notebook cells.

For `kind: "scan"`, the real Oracle client connection is needed to exercise
redirection. Known `redirect_targets` are optional supplemental listener checks.
For other dependencies, use `kind: "dependency"` with their actual host and port;
their own client is needed for login and application-protocol checks.
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

Run this cell to resolve your actual datasource HOST and any supplied SCAN
listener names. It makes no destination TCP/TLS connection or database login.
A DNS-only PASS establishes resolution, not listener reachability.

If resolution fails, use the network details from section 1:

| DNS setup | What to inspect for the failed name |
| --- | --- |
| OCI private zone | Record and private view attached to the workspace VCN resolver |
| Another VCN's DNS | Shared view or forwarding rule and path to its listening resolver |
| On-premises DNS | Domain forwarding rule, endpoint, DNS server and query/reply route |

Follow the displayed next step and [DNS runbook](RUNBOOK.md#dns). Correct the
identified hostname, DNS record/view or forwarding issue and rerun this cell.
Review rule/view ordering if queries select the wrong source. The resolver result
alone does not expose server response codes, TTLs or the chosen forwarding rule.
A comparison from another machine in the customer VCN is optional when helpful.

Numeric IP targets skip hostname resolution. If the explicit HOST resolves but
JDBC still reports UnknownHostException, verify its TNS alias, selected descriptor
and JVM lookup behavior with the original client. For executor-side failures,
use section 5 to sample that path; notebook success alone does not cover it.
"""),
        cell("code", """if CONFIG_PATH:
    CONFIG = json.loads(Path(CONFIG_PATH).read_text())
CONFIG = validate_config(CONFIG)  # Validate all destinations before probing.

def show_summary(result, max_rows=40):
    print_report(result, max_rows=max_rows)

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
        cell("markdown", """## 6. Retest the connection and save your results

Use the first failed stage's [runbook section](RUNBOOK.md) to inspect your setup:
DNS records/views/forwarding for a lookup failure; listener, route/rules and return
path for a socket failure; existing client settings for a database connection
failure. Correct the identified configuration and repeat the relevant checks.

Keep before/after results separate and rerun the original notebook, JDBC query,
catalog read or job. One socket or Oracle connection does not validate the whole
operation. SCAN needs an actual client connection; one selected redirect does not
cover every RAC instance/failover path. The raw probe does not test your wallet.

This cell saves the observed results, runtime labels and coverage limits.
Handle endpoint/network details as operational information. Keep passwords,
tokens, wallet contents and credential-bearing descriptors out of the export.
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
        "aidp_diagnostics": {"source": "shared-utils/private-network-diagnostics/ai_compute_network_diagnostics.py", "version": "1.5"},
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
