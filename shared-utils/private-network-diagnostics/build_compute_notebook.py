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


def cell(kind, source, hidden=False, shared=False):
    result = dict(cell_type=kind, id=hashlib.sha256(source.encode()).hexdigest()[:12],
                  metadata={"jupyter": {"source_hidden": True}} if hidden else {},
                  source=source.splitlines(keepends=True))
    if shared:
        result["metadata"]["aidp_shared_implementation"] = True
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
        cell("markdown", """# Test ADB from your private workspace

Create your workspace with private network access and open this notebook on its
Compute. The same three checks cover all the cases below:

| Your ADB connection | Tests |
| --- | --- |
| Private endpoint in the workspace's subnet | DNS → reachability → query |
| Private endpoint in another subnet or VCN | DNS → reachability → query |
| Public endpoint protected by an ACL | DNS → reachability → query |

Enter only the **HOST** and **PORT** from ADB's **Database Connection** information
in section 1. Run sections 1–3 for DNS, enable section 4 for reachability, then run
`SELECT 1 FROM DUAL` using your existing database connection as described in
section 5. The [short guide](RUNBOOK.md) gives the follow-up if a test fails.

Copyright (c) 2026 Oracle and/or its affiliates. Licensed under the Universal
Permissive License (UPL), Version 1.0. See the repository LICENSE.txt.
"""),
        cell("markdown", """## 1. Enter your ADB connection information

Copy HOST and PORT from the selected ADB **Database Connection** descriptor.
Use the actual HOST, not a TNS alias such as `dbname_high`.

Network checks do not need database credentials, a wallet, expected IPs or TLS
choices. Keep your application's existing wallet or walletless setup for the
query test. An empty HOST runs no checks.
"""),
        cell("code", """# Edit only these two values for the basic ADB tests.
ADB_HOST = ""  # HOST from ADB Database Connection.
ADB_PORT = 1522  # Its PORT; keep 1522 only if that is the selected port.
RUN_CONNECTIVITY_CHECKS = False  # Set True after the DNS test, for section 4.
"""),
        cell("markdown", """## 2. Load the checks

Run these collapsed cells once. They contain the shared checks and default
settings. No edits or OCI credentials are needed. Network checks use Python
3.9+ and its standard library.
"""),
        cell("code", """# Default settings for the three basic ADB tests.
CONFIG_PATH = None
RUNTIME_LABEL = "Compute notebook Python runtime"
RUN_ORACLE_PROBE = False
REPEAT_SAMPLES = 1
SAMPLE_INTERVAL_SECONDS = 5
EXECUTOR_SAMPLE_PARTITIONS = 0
REPORT_PATH = None
executor_reports = []
executor_sample_status = "NOT_REQUESTED"

def get_notebook_config():
    if CONFIG_PATH:
        return json.loads(Path(CONFIG_PATH).read_text())
    return {"endpoints": [
        {"name": "ADB", "kind": "adb", "host": ADB_HOST, "port": ADB_PORT, "protocol": "tcps"}
    ]} if ADB_HOST else {"endpoints": []}
""", hidden=True),
    ]
    cells.extend(cell("code", section, hidden=True, shared=True) for section in sections)
    cells.extend([
        cell("markdown", """## 3. Test DNS

Run this cell. **Pass:** `DNS_RESOLVED` appears; continue to section 4.

If the lookup fails, copy HOST again from the current ADB connection information
and retry in this private workspace. Use the [short guide](RUNBOOK.md#dns) if it
still fails. This test does not open a database connection.
"""),
        cell("code", """CONFIG = validate_config(get_notebook_config())

def show_summary(result, max_rows=40):
    print_report(result, max_rows=max_rows)

dns_report = run_samples(CONFIG, label=RUNTIME_LABEL, dns_only=True,
                         sample_count=REPEAT_SAMPLES, interval_seconds=SAMPLE_INTERVAL_SECONDS)
show_summary(dns_report)
"""),
        cell("markdown", """## 4. Test reachability

Set `RUN_CONNECTIVITY_CHECKS = True` in section 1, rerun that configuration cell,
then run this cell. **Pass:** `TCP_CONNECTED` appears for the tested address.
Continue to the query test in section 5.

If it fails, recheck PORT and confirm ADB is **Available**. The [short guide](RUNBOOK.md#tcp)
gives the next check for the same-subnet, remote-subnet or ACL case.

This checks the listener without a separate TLS handshake or wallet test.
Your existing database client performs its normal secure connection for the
query. A reachable listener alone does not establish database login or ACL
acceptance. Keep any failed address results even if another address passes.
"""),
        cell("code", """connectivity_status = "NOT_REQUESTED"
report = dns_report
if RUN_CONNECTIVITY_CHECKS:
    CONFIG = validate_config(get_notebook_config())  # Refresh edited HOST/PORT.
    report = run_samples(CONFIG, label=RUNTIME_LABEL, oracle_probe=RUN_ORACLE_PROBE,
                         sample_count=REPEAT_SAMPLES, interval_seconds=SAMPLE_INTERVAL_SECONDS,
                         tcp_only=True)
    connectivity_status = report["status"]
    show_summary(report)
else:
    print("Connectivity not requested. Review DNS results, then enable RUN_CONNECTIVITY_CHECKS in section 1.")
"""),
        cell("markdown", """## 5. Test a query with your existing connection

Use the ADB connection your application already uses, including its wallet if
it uses one. Run:

```sql
SELECT 1 FROM DUAL;
```

**Pass:** the result is `1`. Repeat your original notebook, job or catalog read
to complete validation. If the query fails, use its database error to check the
existing account, wallet or service settings. See the [short guide](RUNBOOK.md#application-tests).

The network tests do not create a logged-in database connection. The optional
[Oracle probe](SCRIPT_REFERENCE.md#oracle-client) can run the query when its
existing client/credential settings are configured. Otherwise run it through
that application's client. The cell below keeps the network results and can
export them if `REPORT_PATH` is set.
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
        cell("markdown", """## 6. Optional executor check

Use this only when your original Spark operation fails on executors. Keep the
partition count at zero for the three basic ADB tests. See the [optional executor
reference](SCRIPT_REFERENCE.md) for coverage and runtime requirements.
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
                                  dns_only=not RUN_CONNECTIVITY_CHECKS, tcp_only=True,
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
    print("Optional executor check not requested.")

if "evidence" in globals():
    evidence["executor_sample_status"] = executor_sample_status
    evidence["executor_samples"] = executor_reports
    if REPORT_PATH:
        Path(REPORT_PATH).write_text(json.dumps(evidence, indent=2) + "\\n")
"""),
    ])
    notebook = dict(cells=cells, metadata={
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {"name": "python", "version": "3.9"},
        "aidp_diagnostics": {"source": "shared-utils/private-network-diagnostics/ai_compute_network_diagnostics.py", "version": "1.6"},
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
