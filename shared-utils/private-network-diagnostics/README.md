# AIDP private network diagnostics

Use the [Compute notebook](AIDP_Compute_Private_Network_Diagnostics.ipynb) in the affected private workspace, or run [the Python script](ai_compute_network_diagnostics.py) inside the affected AI Compute runtime. Both use the same implementation. Python 3.9+ is sufficient for network checks; no OCI credentials or control-plane calls are required.

Start with ADB. Add cloud Oracle E-Business Suite database listeners, on-premises Oracle DBs, and RAC SCAN endpoints as needed. EBS here means Oracle E-Business Suite: these checks cover its database listener, not application HTTP or business functionality.

## Configure targets

Copy `targets.example.json` to `targets.json`. Keep only the targets you need and replace every `.invalid` example host. Examples are rejected before any probes run. Use the actual host, port, and TCP/TCPS protocol from the database connection information; do not paste a URL, JDBC URL, password, or entire TNS descriptor into `host`.

For ADB, choose the private endpoint connection details from the Database Connection page. mTLS uses port 1522; TLS connections can use 1521 or 1522 according to the selected connection information. Choose `tls_mode: "mtls"` when a client wallet is required. For walletless TLS, select `tls_mode: "tls"` and remove wallet environment references from `oracle`. [Oracle ADB connection information](https://docs.oracle.com/en/cloud/paas/autonomous-database/serverless/adbsb/connect-download-wallet.html)

```bash
python3 ai_compute_network_diagnostics.py --config targets.json \
  --label "AI Compute in affected private workspace" --output connectivity-report.json
```

Run that command from the directory containing the script, inside the target runtime. For catalog failures, use the actual catalog/default Compute path; a notebook attached to another Compute does not validate it. A laptop or Cloud Shell run measures that machine's network path. It does not validate the AIDP runtime's path. To compare locations, run the same targets from each and retain distinct reports.

| Field | Purpose |
|---|---|
| `name`, `kind` | Unique label; `adb`, `ebs`, `onprem`, `oracle`, `scan`, or `dependency` |
| `host`, `port`, `protocol` | Exact destination; protocol is `tcp` or `tcps` |
| `expected_ips` | Optional complete set of **runtime-visible** IPs. Exact set comparison catches missing and unexpected answers. Leave empty to observe only. |
| `expected_dns_count` | Optional expected number of unique A/AAAA answers; no fixed SCAN count is assumed |
| `tls_mode` | `tls` or `mtls`; raw probes do not load an Oracle wallet |
| `ca_file` | Optional local PEM CA bundle for the raw TLS probe; verification stays enabled |
| `tls_server_name` | Optional expected certificate hostname; default is the original endpoint hostname, including when connecting to synthetic IPs |
| `redirect_targets` | For SCAN: explicit known listener hosts/IPs and ports, with optional TLS and expected IP settings |
| `oracle` | Optional client configuration using environment variable **names**, as in the example |

DNS uses the runtime's system resolver and is killed if it exceeds `timeout_seconds`. All returned A/AAAA addresses are tested up to `max_addresses`; exceeding the cap produces a coverage warning. TCP and TLS each have that timeout. The Oracle subprocess has a separate **total** deadline of `oracle_timeout_seconds`, including login, redirects, query, and cleanup. Probes run sequentially; multiple addresses and endpoints can take several minutes. There are no retries within an individual probe or port scans.

Include application service and other database dependencies with `kind: "dependency"`. Use the protocol and port from the actual application configuration. The example's port 443 is a placeholder choice, not a discovered service port. TCP/TLS checks do not validate HTTP authentication, SDK calls, PostgreSQL login, UDF functionality, or sustained Object Storage reads.

### Intermittent failures

Collect 1-10 samples with an optional pause of 0-60 seconds after each completed sample:

```bash
python3 ai_compute_network_diagnostics.py --config targets.json \
  --samples 3 --interval 10 --label "Affected Compute and workspace" \
  --output connectivity-samples.json
```

Every check includes `observed_at_utc`; repeated checks include `sample_index`. The report retains all checks and sample outcomes. An earlier failure keeps the overall result at `FAIL` even when a later sample passes. The pause is not a fixed sampling cadence, and no global deadline is imposed beyond the per-probe deadlines. With `--oracle-probe`, login can occur once per endpoint per sample; ORA-01017, ORA-28000 and ORA-28001 suppress further logins to that endpoint while network sampling continues. No targets causes no traffic or waiting. In the notebook, set `REPEAT_SAMPLES` and `SAMPLE_INTERVAL_SECONDS`; the same setting also applies to each requested executor sample.

## Optional database and SCAN redirect check

Use an endpoint hostname in `host`, rather than a TNS alias such as `dbname_high`. If the application uses a TNS alias, obtain its actual HOST, PORT, protocol and service from the selected connection descriptor, and verify the application's `tnsnames.ora` / `TNS_ADMIN` and wallet setup separately. The diagnostics build an explicit host/service connection and do not reproduce arbitrary JDBC alias lookup. [Oracle JDBC alias guidance](https://docs.oracle.com/en/database/oracle/oracle-database/21/jajdb/oracle/jdbc/OracleDriver.html)

Supply `python-oracledb` through the runtime's normal dependency mechanism and use its supported configuration for the selected database. Provision credentials with the normal runtime secret mechanism; do not put them into the target JSON, notebook, command line, or report. Use a least-privilege account that can connect and select from DUAL. Each enabled endpoint attempts one login and executes only `SELECT 1 FROM DUAL`.

```bash
python3 ai_compute_network_diagnostics.py --config targets.json \
  --oracle-probe --output connectivity-with-oracle.json
```

The script uses the configured endpoint host/port/protocol and reads the database service name from `service_name_env`. This keeps the client probe aligned with the network target. It does not accept arbitrary credential-bearing DSNs. Client failures report only Oracle/DPY/DPI error codes; exception text, usernames, passwords, service names, wallet content, and paths are excluded from the report.

Thin mode requires no Instant Client. For ADB mTLS, the mounted Thin-mode wallet directory must contain the appropriate `ewallet.pem`; the ZIP alone is insufficient. Thick mode requires installed Oracle Client libraries and a configured Oracle Net environment, including the appropriate wallet files. Set `mode: "thick"`, optionally `client_lib_dir_env` and `config_dir_env`, and omit Thin-only `wallet_password_env` if unused. For TCPS, `ssl_server_cert_dn_env` can supply the expected server DN when required by the connection information. Certificate verification is never disabled. Select the client mode that matches your application's encryption policy; Oracle native network encryption may require Thick mode. [python-oracledb connection guidance](https://python-oracledb.readthedocs.io/en/latest/user_guide/connection_handling.html)

SCAN is a RAC access name, used with TCP or TCPS. The SCAN listener selects a database node and returns a local listener address. A socket connection to the SCAN port alone cannot validate that redirect. The optional Oracle client check exercises one actual connection path; it does not guarantee all instances or failover work. [Oracle RAC SCAN behavior](https://docs.oracle.com/en/database/oracle/oracle-database/18/rilin/about-connecting-to-an-oracle-rac-database-using-scans.html)

In AIDP, the workspace SCAN proxy can expose a single address and translate IP-based listener redirects. Compare DNS with current **SCAN proxy/RCE mappings**, rather than assuming three DNS answers or comparing with customer-side VIPs. Known listener targets are supplemental: their direct reachability may differ from the translated redirect path. For TCPS, review the database's advertised listener FQDN and certificate identity. [Oracle AIDP private connectivity guidance](https://blogs.oracle.com/dataintegration/aidp-private-workspace-connectivity-to-oracle-databases)

## Interpret results

| Result | What to investigate |
|---|---|
| DNS fails or times out | Customer DNS views, forwarding to on-premises, workspace resolver/proxy, CoreDNS |
| Expected IPs differ | Stale/incomplete RCE or SCAN proxy mappings; expectation may use the wrong network vantage point |
| TCP times out | Listener port, PE/RCE state, NSG/security lists, routes; for on-premises include DRG, VPN/FastConnect, and return path |
| TCP refused | Listener/port or an active firewall rejection |
| Certificate verification fails | Host identity, trust chain, local clock, CA or wallet configuration |
| Raw mTLS warning | Expected without a client wallet; use the Oracle probe to validate the configured client path |
| SCAN redirect unverified | Run the Oracle probe; inspect workspace SCAN name/port and advertised listeners |
| ORA-01017 / ORA-28000 / ORA-28001 | Authentication, locked account or expired password; coordinate with the DBA |
| ORA-17868 | Oracle client host resolution; compare the exact JDBC hostname with system DNS and RCE mapping |
| ORA-12514 / DPY-6001 | Service name or listener registration |
| ORA-12523 | Listener could not select an appropriate available instance; check registration and readiness with the DBA |
| DPY-6005 | Generic Python Oracle connection failure; compare DNS/TCP/TLS stages and the actual client configuration. The code alone does not locate the fault. |
| ORA-12506 | Listener service ACL filtering |

A timeout is evidence of a failed attempt, not proof of a particular missing security rule. Synthetic/reserved IPs are labeled as possible RCE addresses; the classification alone cannot confirm mappings. The script does not query or change VCNs, routes, NSGs, DNS, private endpoints, or databases. It uses direct sockets rather than HTTP proxies. Custom DSN proxies, failover descriptors, JDBC settings, and application-specific queries need validation with the actual workload.

`PASS` means the requested checks passed, with explicit skipped client checks retained. `WARN` indicates incomplete coverage or client/redirect validation, and `FAIL` indicates at least one failed check. mTLS raw-probe warnings remain even when the optional Oracle probe succeeds; read each stage separately. No configured endpoints returns `NOT_RUN`. Exit codes: 0 PASS, 1 FAIL, 2 configuration/no targets, 3 WARN. Reports include endpoint and network metadata; handle them as operational evidence.

## Compute notebook

Upload the notebook and choose the Python kernel backed by the affected Compute. Edit its target configuration or load your JSON. The notebook embeds the script so it has no sibling-file dependency. The initial empty target list deliberately generates no traffic. Default execution records the notebook Python runtime; confirm where that kernel runs in your AIDP setup.

An optional Spark executor sample runs the same network checks with Oracle login disabled. It requires classic PySpark RDD support, and reports sampled hostnames. Partition count is not executor count: scheduling may repeat hosts and miss executors. Spark Connect runtimes without RDD support skip the sample explicitly. Scheduler/job duration is managed by Spark and is not bounded by socket timeouts. Driver or notebook-only success cannot prove executor connectivity.

## Local validation and notebook maintenance

Run these commands from the repository root:

```bash
python3 -m unittest discover -s shared-utils/private-network-diagnostics -p 'test_*.py' -v
python3 shared-utils/private-network-diagnostics/build_compute_notebook.py
```

The generator uses `nbformat` when installed, otherwise emits standard nbformat 4.5 JSON. It checks the saved cell structure and compiles all Python cells. The notebook is generated from the script; regenerate it after edits so Compute and AI Compute retain identical checks. Local listener fixtures validate success/failure handling. Actual private workspace, ADB wallet, SCAN proxy/redirect, and executor access require execution in those environments.

The tests use local TCP/TLS listeners and a stub Oracle driver. They cover DNS and input handling, retained intermittent failures, sampling bounds, and suppression of repeated failed logins. The default empty-target notebook run validates its execution without sending endpoint traffic. Live private ADB/SCAN, AI Compute, and Spark executor paths must be verified in the affected environment.

For an HTML preview, run from the repository root:

```bash
jupyter nbconvert --to html --output-dir /tmp \
  shared-utils/private-network-diagnostics/AIDP_Compute_Private_Network_Diagnostics.ipynb
```

The generated notebook is kept unexecuted so it contains no runtime metadata or target results. Keep diagnostic reports and populated target files outside version control.

## License

Copyright (c) 2026 Oracle and/or its affiliates. Licensed under the Universal Permissive License (UPL), Version 1.0. See [LICENSE.txt](../../LICENSE.txt).
