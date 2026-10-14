# AIDP private network diagnostics

Use these samples **inside the AIDP runtime where the connection fails**. Start with ADB, then add cloud Oracle E-Business Suite database listeners, on-premises databases and RAC SCAN endpoints.

| Your runtime | Start here |
| --- | --- |
| **Compute** | Open the [diagnostic notebook](AIDP_Compute_Private_Network_Diagnostics.ipynb) on the affected Compute. Configure the datasource and run its DNS cells, followed by its connectivity cell. |
| **AI Compute** | Use the [Python script](ai_compute_network_diagnostics.py) through the execution mechanism already available in your AI Compute environment. See the [Python example below](#ai-compute). |

Follow **DNS resolution → listener connectivity → existing database client → original operation**. The [runbook](RUNBOOK.md) helps you identify the workspace network, understand where DNS should resolve the name, find the relevant route/rules and correct the first failed stage. Each actionable result includes a `next_step` and a link to the matching runbook section.

DNS follows the customer VCN context for ordinary private names. Your private views or forwarding configuration determine the backend destination; AIDP may return a translated address. Different addresses from a customer-side lookup do not by themselves indicate a failure. Review [advanced DNS settings](RUNBOOK.md#advanced-dns) only for an applicable datasource. [AIDP private connectivity architecture](https://blogs.oracle.com/dataintegration/aidp-private-workspace-connectivity-to-oracle-databases)

## Compute notebook

1. Upload/open the notebook and select the affected workspace and Compute.
2. Identify the workspace's VCN/subnet, datasource location and expected DNS source using the table at the top of the notebook. Enter the application's actual HOST and listener PORT in section 1, or load your uploaded target JSON.
3. Run sections 1, 2 and 3. The default `RUN_CONNECTIVITY_CHECKS = False` runs DNS only. Follow the displayed next step and [DNS runbook](RUNBOOK.md#dns) for a failed lookup.
4. After reviewing DNS, set `RUN_CONNECTIVITY_CHECKS = True`, rerun the configuration cell and section 4. This resolves names again before testing each returned address.
5. When the listener is reachable, test the application's existing database connection, then repeat the original operation. Enable the optional Oracle probe only when its client and credentials are already configured.

The notebook embeds the script and needs no sibling Python file, OCI credentials or control-plane calls. Empty targets cause no traffic. The checks run where its Python kernel runs; verify that this is the path relevant to your failed operation.

Optional executor sampling in section 5 uses classic PySpark RDDs and records sampled hostnames. Partition count is not executor count: scheduling can repeat hosts or miss executors. Spark Connect without RDD support skips that sample. The executor sample disables Oracle login. Notebook-only success does not establish executor connectivity.

<a id="ai-compute"></a>

## AI Compute script

Upload the script and your target configuration where your AI Compute Python execution can access them. Use the execution interface you already use for that workload. Both notebook and script run the same checks; network checks need only Python 3.9+ and its standard library.

If that interface accepts Python code, use the following with the uploaded script available on the Python import path. Replace the `.invalid` HOST before running; placeholders are rejected.

```python
from ai_compute_network_diagnostics import run_samples, print_report

config = {
    "endpoints": [{
        "name": "ADB", "kind": "adb",
        "host": "replace-with-your-adb-host.example.invalid",
        "port": 1522,  # Copy the actual PORT from your connection information.
        "protocol": "tcps", "tls_mode": "mtls",  # Existing wallet-based method.
    }],
}

dns_report = run_samples(config, label="Affected AI Compute", dns_only=True)
print_report(dns_report)
```

After reviewing DNS, run the connectivity stage in the same environment:

```python
connectivity_report = run_samples(config, label="Affected AI Compute")
print_report(connectivity_report)
```

If your existing execution mechanism launches Python files, the script also has a command-line entry point: supply `--config` with the target JSON path and `--dns-only` for the DNS stage. Omit `--dns-only` for connectivity. `--output` saves a JSON report. A separate terminal or VM is not required by this sample.

## Configure the datasource you actually use

Copy only the relevant entries from `targets.example.json`, replacing all `.invalid` hosts. The examples cover ADB with a wallet, permitted walletless ADB, EBS database listeners, on-premises Oracle, SCAN and other dependencies. Keep credentials out of JSON and notebook cells.

| Setting | What to supply |
| --- | --- |
| `name`, `kind` | A label and type: `adb`, `ebs`, `onprem`, `oracle`, `scan` or `dependency`. EBS checks its database listener. |
| `host`, `port` | Actual HOST and PORT from the application's connection information. Do not paste a URL, full descriptor or TNS alias into `host`. |
| `protocol`, `tls_mode` | Existing TCP/TCPS method; TCPS uses `tls` or `mtls`. DNS and TCP checks do not require a wallet. |
| `expected_ips`, `expected_dns_count` | Optional expectations for this runtime. Leave unset unless confirmed; do not assume customer-side database IPs or three SCAN answers. |
| `ca_file`, `tls_server_name` | Optional trust bundle and certificate hostname for raw TLS. The default certificate hostname is the original HOST; verification remains enabled. |
| `redirect_targets` | Optional known SCAN local-listener targets; direct checks are supplemental to an actual redirected connection. |
| `oracle` | Optional Oracle-client settings that reference secrets and service information by environment variable name. |

For ADB, copy the selected Database Connection descriptor. A wallet-based mTLS connection uses `tcps`/`mtls`; a permitted walletless TLS connection uses `tcps`/`tls`. Copy the descriptor's port rather than assuming one. For on-premises Oracle, keep its configured TCP/TCPS and client security setup. Oracle native network encryption can operate over TCP. [ADB connection information](https://docs.oracle.com/en/cloud/paas/autonomous-database/serverless/adbsb/connect-download-wallet.html), [Oracle client encryption configuration](https://python-oracledb.readthedocs.io/en/latest/user_guide/connection_handling.html#securely-encrypting-network-traffic-to-oracle-database)

A wallet is relevant after hostname and listener reachability are established. `MTLS_WALLET_UNVERIFIED` means the raw network probe did not test the wallet; assess the existing database client and original workload. Do not change database policy or disable verification to obtain a passing result.

<a id="oracle-client"></a>

## Optional Oracle client and SCAN connection

Use an actual HOST rather than a TNS alias such as `dbname_high`. Verify the application's `tnsnames.ora`, `TNS_ADMIN` and selected alias with its existing JDBC/Oracle client. The Python probe builds an explicit host/service connection and does not reproduce arbitrary JDBC alias lookup. [Oracle JDBC alias configuration](https://docs.oracle.com/en/database/oracle/oracle-database/21/jajdb/oracle/jdbc/OracleDriver.html)

Configure `python-oracledb` and secrets through the runtime's existing mechanisms. Set `RUN_ORACLE_PROBE = True` in the notebook or pass `oracle_probe=True` to `run_samples` in AI Compute. The script entry point also accepts `--oracle-probe`. Each configured endpoint uses its HOST/PORT/protocol and `service_name_env`, attempts one login and runs `SELECT 1 FROM DUAL`. Use an account authorized for that operation.

Thin mode requires no Instant Client. For Thin mTLS, mount an appropriate wallet directory containing `ewallet.pem`; the ZIP alone is insufficient. Thick mode requires installed Oracle Client libraries and its Oracle Net configuration. Set `mode: "thick"` and the applicable environment references in the example. Use the client mode compatible with your application's security, including native network encryption where required. Failures expose sanitized Oracle/DPY/DPI codes rather than credentials or raw exception messages. [python-oracledb connection configuration](https://python-oracledb.readthedocs.io/en/latest/user_guide/connection_handling.html)

A SCAN socket connection does not validate redirection. Confirm workspace SCAN FQDN/port settings and perform an actual Oracle connection. One successful connection validates one selected path; all RAC instances/failover need separate validation. [SCAN runbook](RUNBOOK.md#scan)

## Read the first failed stage and retest

| First failed stage | Follow-up |
| --- | --- |
| [DNS](RUNBOOK.md#dns) | Confirm HOST and workspace VCN/subnet; inspect the private DNS view/record or forwarding path for that domain. |
| [Expected DNS answer](RUNBOOK.md#dns-expectations) | Confirm the test expectation applies to this runtime; translated answers can differ from backend addresses. |
| [Listener connectivity](RUNBOOK.md#tcp) | Check listener readiness, datasource route, applicable rules and return routing for the observed AIDP source. |
| [TLS/client security](RUNBOOK.md#tls) | Use the existing client, trust/wallet and required security settings. |
| [SCAN redirect](RUNBOOK.md#scan) | Check SCAN settings, selected service and local listeners; use an actual Oracle connection. |
| [Account](RUNBOOK.md#accounts) or [database service](RUNBOOK.md#oracle-service) | Correct the identified credential/account/service issue before another connection. |
| [Client prerequisites](RUNBOOK.md#client), [generic error](RUNBOOK.md#unclassified) or [coverage gap](RUNBOOK.md#coverage) | Resolve the stated prerequisite or record what remains untested; avoid a guessed network correction. |

A comparison from an already available machine in the customer VCN is [optional](RUNBOOK.md#comparison) when it helps explain a failed result. Start in the affected AIDP runtime. After a correction, rerun its checks and the [original operation](RUNBOOK.md#recovery).

## What the results cover

DNS-only PASS covers resolution and supplied expectations. TCP/TLS, database login and SCAN redirects remain untested. Each connectivity sample resolves all configured names before opening sockets; failed DNS/expectations skip downstream checks for that target. Other targets continue. No successful tested TCP address skips an enabled Oracle login. Partial-address failures remain visible even when another address works.

The probes observe the runtime's system resolver and direct socket path. They do not audit DNS/routing/rules or validate HTTP proxies, JDBC aliases, other database login protocols, UDFs, SDK calls, EBS business functionality or sustained object reads. Use the actual client/workload for those checks. DNS errors do not expose server response codes, TTLs or JVM caches.

`PASS` means requested checks passed; `WARN` records missing coverage or client/redirect validation; `FAIL` means at least one check failed; empty targets return `NOT_RUN`. Check `test_scope` and `not_run_stages`, and retain skipped results. Script exit codes are 0 PASS, 1 FAIL, 2 configuration/no targets and 3 WARN.

For intermittent failures, use notebook `REPEAT_SAMPLES` or script `sample_count` (1–10) and the pause setting (0–60 seconds). Earlier failures remain in the report. With Oracle login enabled, account errors suppress later logins within that invocation; do not restart login probes against an unresolved account issue. The per-probe deadlines bound DNS, TCP/TLS and Oracle subprocess attempts, not the whole run or Spark scheduling time.

Export through the notebook's final cell or save the AI Compute report as JSON. Keep populated targets, network metadata and runtime outputs outside version control. See [failure coverage and acceptance checks](TEST_COVERAGE.md) for verification limits.

## Maintaining these samples

From the repository root:

```bash
python3 -m unittest discover -s shared-utils/private-network-diagnostics -p 'test_*.py' -v
python3 shared-utils/private-network-diagnostics/build_compute_notebook.py
```

Regenerate the notebook after script edits. The generator emits nbformat 4.5, compiles the code cells and validates the format when `nbformat` is available. Keep the distributed notebook unexecuted. Local tests use mocks and loopback fixtures; live ADB/SCAN, AI Compute and executor paths require validation in those environments.

## License

Copyright (c) 2026 Oracle and/or its affiliates. Licensed under the Universal Permissive License (UPL), Version 1.0. See [LICENSE.txt](../../LICENSE.txt).
