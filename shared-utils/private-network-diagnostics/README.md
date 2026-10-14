# AIDP private network diagnostics

Use the [Compute notebook](AIDP_Compute_Private_Network_Diagnostics.ipynb) in the affected private workspace, or run [the Python script](ai_compute_network_diagnostics.py) inside the affected AI Compute runtime. Both use the same implementation. Python 3.9+ is sufficient for network checks; no OCI credentials or control-plane calls are required.

Start with ADB. Add cloud Oracle E-Business Suite database listeners, on-premises Oracle DBs, and RAC SCAN endpoints as needed. EBS here means Oracle E-Business Suite: these checks cover its database listener, not application HTTP or business functionality.

Use the [customer runbook](RUNBOOK.md) to turn a result into diagnosis, a correction by the appropriate owner, and recovery verification. Each actionable check includes a customer `next_step`, a conditional `support_next_step`, and a `runbook_ref`; summaries label customer and Oracle Support guidance separately.

Follow **DNS resolution → per-address TCP → TLS where required → optional database login → original workload**. See the [failure coverage and acceptance tests](TEST_COVERAGE.md) for what each step proves and what needs a separate functional check.

Customers can review their connection settings, documented workspace controls, and customer-owned network/database resources when authorized. AIDP service-network CoreDNS, internal NSGs, RCE/NAT mappings and SCAN proxy internals require Oracle Support. The tool observes the runtime's connection path; it does not audit those controls. The runbook includes an [ownership table](RUNBOOK.md#ownership) and a [private Support handoff template](RUNBOOK.md#support).

## Configure targets

Copy `targets.example.json` to `targets.json`. Keep only the targets you need and replace every `.invalid` example host. Examples are rejected before any probes run. Use the actual host, port, and TCP/TCPS protocol from the database connection information; do not paste a URL, JDBC URL, password, or entire TNS descriptor into `host`.

### Use the connection method you already have

Start with the application's connection information, rather than choosing an encryption policy for the test. DNS and listener-port checks work without a database wallet. A successful socket connection proves the listener is reachable; the actual client connection verifies the required security setup, account and service.

| Existing connection | Sample settings | Additional check after DNS and TCP |
| --- | --- | --- |
| **ADB using a client wallet for mTLS** | `protocol: "tcps"`, `tls_mode: "mtls"`; host/port from the selected mTLS descriptor | Use the compatible Oracle client with the mounted wallet. The raw network probe does not use that wallet. |
| **ADB without a wallet, using TLS** | `protocol: "tcps"`, `tls_mode: "tls"`; host/port from the selected TLS descriptor | Verify the server certificate and connect using the compatible Oracle client. This method must already be permitted by the database. |
| **On-premises Oracle using TCP** | `protocol: "tcp"`; host/port from its existing descriptor | No raw TLS check. Use the Oracle client with the database's existing security settings; native network encryption can operate over TCP. |
| **On-premises Oracle using TCPS** | `protocol: "tcps"`; `tls_mode` and trust/client-certificate settings confirmed by the DBA | Verify the configured TLS connection with the matching client. Use a wallet only where that client/security configuration requires it. |

ADB uses an encrypted connection with either method. Its mTLS descriptor uses port 1522; its TLS descriptor can use 1521 or 1522. Copy the actual port from **Database Connection**, because selecting another method can change the port that must be reachable. The two ADB templates in `targets.example.json` cover these cases; keep the one matching your application. [ADB connection information](https://docs.oracle.com/en/cloud/paas/autonomous-database/serverless/adbsb/connect-download-wallet.html), [ADB wallet-based connection](https://docs.oracle.com/en/cloud/paas/autonomous-database/serverless/adbsb/connect-jdbc-thin-wallet.html)

Wallet presence alone does not identify the connection method: wallets can hold trusted server certificates as well as client certificates. Follow the selected descriptor and client configuration. For on-premises Oracle native network encryption, the Python probe needs a compatible Thick-mode setup. Other on-premises sources use `kind: "dependency"` for network checks and their own supported client for secure login; the Oracle probe does not implement their login protocols. Services that negotiate TLS within their application protocol need that client to validate TLS. [Oracle TLS configuration](https://docs.oracle.com/en/database/oracle/oracle-database/26/dbseg/configuring-transport-layer-security-encryption.html), [Oracle client encryption support](https://python-oracledb.readthedocs.io/en/latest/user_guide/connection_handling.html#securely-encrypting-network-traffic-to-oracle-database)

For a wallet-based ADB connection, `MTLS_WALLET_UNVERIFIED` means **the network probe did not test your wallet**. It is a coverage warning, not proof of a failed database connection or private-network fault. Assess the compatible client and original workload results separately. Follow the [connection-method runbook](RUNBOOK.md#connection-method) when unsure which settings apply.

Start with DNS only:

```bash
python3 ai_compute_network_diagnostics.py --config targets.json --dns-only \
  --label "AI Compute in affected private workspace" --output dns-report.json
```

Review the returned addresses and any confirmed expectation checks. Then run connectivity:

```bash
python3 ai_compute_network_diagnostics.py --config targets.json \
  --label "AI Compute in affected private workspace" --output connectivity-report.json
```

Each sample resolves **all** configured names, including supplied SCAN listener names, before opening destination sockets. Connectivity uses that sample's addresses. A DNS failure or supplied expectation mismatch skips TCP/TLS and Oracle login for that target; healthy targets continue for comparison. An enabled Oracle login requires at least one tested address to accept TCP. A partial-address failure remains FAIL even when another address or a database login passes. Raw mTLS warnings still require a compatible wallet-backed client check.

`--dns-only` makes resolver lookups but no destination socket or database connection. Its PASS applies only to DNS/expectation checks; `test_scope` and `not_run_stages` make that explicit in JSON and printed output. It cannot be combined with `--oracle-probe`. Numeric IP targets report `DNS_IP_LITERAL` as skipped hostname resolution; a numeric-only DNS run returns `NOT_RUN` unless an expectation fails. Use the original FQDN when investigating DNS errors.

Run that command from the directory containing the script, inside the target runtime. For catalog failures, use the actual catalog/default Compute path; a notebook attached to another Compute does not validate it. A laptop or Cloud Shell run measures that machine's network path. It does not validate the AIDP runtime's path. To compare locations, run the same targets from each and retain distinct reports.

| Field | Purpose |
|---|---|
| `name`, `kind` | Unique label; `adb`, `ebs`, `onprem`, `oracle`, `scan`, or `dependency` |
| `host`, `port`, `protocol` | Exact destination; protocol is `tcp` or `tcps` |
| `expected_ips` | Optional complete set of **runtime-visible** IPs, only when confirmed. Leave empty if service-side mappings are unavailable to you; customer database IPs may differ. |
| `expected_dns_count` | Optional confirmed count of unique A/AAAA answers; omit when unknown. No fixed SCAN count is assumed. |
| `tls_mode` | `tls` or `mtls`; raw probes do not load an Oracle wallet |
| `ca_file` | Optional local PEM CA bundle for the raw TLS probe; verification stays enabled |
| `tls_server_name` | Optional expected certificate hostname; default is the original endpoint hostname, including when connecting to synthetic IPs |
| `redirect_targets` | For SCAN: explicit known listener hosts/IPs and ports, with optional TLS and expected IP settings |
| `oracle` | Optional client configuration using environment variable **names**, as in the example |

DNS uses the runtime's system resolver and is killed if it exceeds `timeout_seconds`. All returned A/AAAA addresses are tested up to `max_addresses`; exceeding the cap produces a coverage warning. TCP and TLS each have that timeout. The Oracle subprocess has a separate **total** deadline of `oracle_timeout_seconds`, including login, redirects, query, and cleanup. Probes run sequentially; multiple addresses and endpoints can take several minutes. There are no retries within an individual probe or port scans.

`DNS_NAME_NOT_FOUND` and `DNS_TEMPORARY_FAILURE` distinguish system-resolver failure classes; `DNS_TIMEOUT` records the probe deadline. These results do not expose DNS response codes, TTLs, forwarding state or JVM caches. Short hostnames warn about search/alias ambiguity. The [DNS runbook](RUNBOOK.md#dns) explains the comparison with the original JDBC runtime. [Python resolver errors](https://docs.python.org/3/library/socket.html#socket.gaierror)

Include application service and other database dependencies with `kind: "dependency"`. Use the protocol and port from the actual application configuration. The example's port 443 is a placeholder choice, not a discovered service port. TCP/TLS checks do not validate HTTP authentication, SDK calls, PostgreSQL login, UDF functionality, or sustained Object Storage reads.

### Intermittent failures

Collect 1-10 samples with an optional pause of 0-60 seconds after each completed sample:

```bash
python3 ai_compute_network_diagnostics.py --config targets.json \
  --samples 3 --interval 10 --label "Affected Compute and workspace" \
  --output connectivity-samples.json
```

Every check includes `observed_at_utc`; repeated checks include `sample_index`. The report retains all checks and sample outcomes. An earlier failure keeps the overall result at `FAIL` even when a later sample passes. Add `--dns-only` to repeat resolution without connectivity or login. The pause is not a fixed sampling cadence, and no global deadline is imposed beyond the per-probe deadlines. With `--oracle-probe`, login can occur once per eligible endpoint per sample; ORA-01017, ORA-28000 and ORA-28001 suppress further logins to that endpoint while network sampling continues. No targets causes no traffic or waiting. In the notebook, set `REPEAT_SAMPLES` and `SAMPLE_INTERVAL_SECONDS`; the same setting also applies to each requested executor sample.

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

In AIDP, the workspace SCAN proxy can expose a single address and translate IP-based listener redirects. Leave DNS expectations unset unless the runtime-visible set/count is confirmed; ask Oracle Support when service-side mapping information is unavailable. Known listener targets are supplemental: their direct reachability may differ from the translated redirect path. For TCPS, the database owner should review advertised listener FQDNs and certificate identity. Use the [SCAN runbook](RUNBOOK.md#scan) for customer checks and Support escalation. [Oracle AIDP private connectivity guidance](https://blogs.oracle.com/dataintegration/aidp-private-workspace-connectivity-to-oracle-databases)

## Interpret results

| Result / runbook | Customer action | Conditional Oracle Support investigation |
|---|---|---|
| [DNS failure / ORA-17868](RUNBOOK.md#dns) | Verify actual host/alias, visible workspace settings and customer DNS with the appropriate administrator | Effective attachment, service-managed DNS/CoreDNS and mapping propagation after customer checks pass |
| [DNS expectation mismatch](RUNBOOK.md#dns-expectations) | Remove unverified expectations; use only a confirmed runtime-visible set/count | Confirm inaccessible service-side mappings; customers do not edit RCE mappings |
| [TCP timeout / refusal / route error](RUNBOOK.md#tcp) | Confirm listener and customer-owned rules/routes/return path with their owners | AIDP service-network NSGs, routing, connectivity and proxy health |
| [TLS or mTLS warning](RUNBOOK.md#tls) | Verify trust/identity/wallet and use a compatible Oracle client | Correlate the service path if the actual client still fails; a raw mTLS warning alone is insufficient |
| [SCAN redirect unverified](RUNBOOK.md#scan) | Verify visible SCAN settings and database service/listeners; run one Oracle connection | Internal SCAN proxy and redirect translation when customer configuration is verified |
| [Account / credential errors](RUNBOOK.md#accounts) | Contact the owner; customer DBAs correct customer-owned accounts | Service-managed database credentials/account state |
| [Oracle service / instance / ACL errors](RUNBOOK.md#oracle-service) | Verify connection information and customer-controlled readiness/access with the database owner | Managed database listener/readiness; AIDP path correlation for AIDP-only failures |
| [Client prerequisites](RUNBOOK.md#client) | Use supported dependency/secret mechanisms and a compatible client mode | Documented runtime mechanism unavailable or failing |
| [Generic DPY-6005 / total timeout](RUNBOOK.md#unclassified) | Compare network stages and actual client settings; avoid a guessed correction | Underlying service-managed dependency error and operation outcome |
| [Address / IPv6 coverage](RUNBOOK.md#coverage) | Review the cap and required address families; retain untested paths | Confirm managed-runtime networking where required coverage is unavailable |
| [Subprocess / executor gap](RUNBOOK.md#runtime) | Check provided dependencies/files and supported runtime access | Inspect the affected runtime when supported execution remains unavailable |

Use the [recovery checklist](RUNBOOK.md#recovery) after a correction and the [Support handoff](RUNBOOK.md#support) for unresolved or service-owned checks.

A timeout is evidence of a failed attempt, not proof of a particular missing security rule. Synthetic/reserved IPs are labeled as possible RCE addresses; the classification alone cannot confirm mappings. The script does not query or change VCNs, routes, NSGs, DNS, private endpoints, or databases. It uses direct sockets rather than HTTP proxies. Custom DSN proxies, failover descriptors, JDBC settings, and application-specific queries need validation with the actual workload.

`PASS` means the requested checks passed, with explicit skipped client checks retained. Check `test_scope`: DNS-only success does not validate TCP, TLS, database login or SCAN redirects. `WARN` indicates incomplete coverage or client/redirect validation, and `FAIL` indicates at least one failed check. mTLS raw-probe warnings remain even when the optional Oracle probe succeeds; read each stage separately. No configured endpoints returns `NOT_RUN`. Exit codes: 0 PASS, 1 FAIL, 2 configuration/no targets, 3 WARN. Reports include endpoint and network metadata; handle them as operational evidence.

## Compute notebook

Upload the notebook and choose the Python kernel backed by the affected Compute. Edit its target configuration or load your JSON. Section 3 runs DNS only. After reviewing it, enable `RUN_CONNECTIVITY_CHECKS`, rerun the configuration cell and section 4. Connectivity resolves names again and applies the same prerequisite checks. The notebook embeds the script so it has no sibling-file dependency. The initial empty target list deliberately generates no traffic. Default execution records the notebook Python runtime; confirm where that kernel runs in your AIDP setup.

An optional Spark executor sample runs DNS only initially, or network checks when `RUN_CONNECTIVITY_CHECKS` is enabled, always with Oracle login disabled. It requires classic PySpark RDD support, and reports sampled hostnames. Partition count is not executor count: scheduling may repeat hosts and miss executors. Spark Connect runtimes without RDD support skip the sample explicitly. Scheduler/job duration is managed by Spark and is not bounded by socket timeouts. Driver or notebook-only success cannot prove executor connectivity.

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
