# Customer runbook: AIDP private connectivity

Use this runbook with the [Compute notebook](AIDP_Compute_Private_Network_Diagnostics.ipynb) or [AI Compute script](ai_compute_network_diagnostics.py). The tools observe a connection attempt from the runtime where they execute. They do not audit or repair network configuration. A failing stage narrows the investigation; it does not prove a specific missing rule or mapping.

Each applicable JSON check includes `next_step` for a customer action, `support_next_step` for a conditional Oracle Support investigation, and `runbook_ref` for the section below. CLI and notebook summaries label these separately. An Oracle Support line does not mean a service fault was proven.

<a id="ownership"></a>

## Who can investigate and change what?

| Area | Customer action, subject to permissions | Oracle Support boundary |
| --- | --- | --- |
| Connection settings and runtime files | Verify your host, port, protocol, service selection, driver, wallet deployment and credential references | Investigate supported dependency/credential mechanisms that fail in managed runtimes |
| Workspace private connectivity | Your workspace administrator can review documented subnet/NSG selections, DNS configuration and SCAN settings | Inspect effective runtime attachment and service-side application of those settings |
| Customer VCN and destination | Your network administrator can inspect customer-owned NSGs, security lists, routes, private DNS, peering and on-premises connectivity | Inspect NSGs, routing and connectivity components inside the AIDP service network |
| Database | Your DBA can manage customer-owned accounts; customer-operated database administrators can inspect their listeners and RAC instances | Investigate service-managed database accounts, listeners and platform dependencies |
| AIDP service network | Collect observed DNS/socket results and resource identifiers available in the customer interface | Inspect service-managed CoreDNS, DNS/RCE mappings, SCAN proxy translation and service-side NSGs |

A customer is **not expected to access AIDP service-network CoreDNS, internal NSGs or RCE administration**. An inaccessible customer-owned resource is a permissions/administrator handoff; an inaccessible service-owned resource is an Oracle Support handoff. The workspace's selected customer NSGs and the service network's internal NSGs are different controls.

Workbench exposes customer-facing private-network settings, and the documented workspace API describes `subnetId`, `nsgIds`, `dnsZones` (permitted FQDNs), and `scanDetails` (FQDN/port pairs). Use the supported interface and your granted permissions. [Workbench workspace configuration](https://docs.oracle.com/en/cloud/paas/ai-data-platform/aidug/workspaces.html), [workspace API schema](https://docs.oracle.com/en/cloud/paas/ai-data-platform/aiwap/op-aidataplatforms-aidataplatformid-workspaces-workspacekey-put.html)

<a id="start"></a>

## Start with the failed operation

1. Record the region, UTC failure window, workspace, affected Compute/AI Compute, and original notebook/catalog/job/agent operation. Include request, command or job IDs if already available. Missing service-internal identifiers do not prevent escalation.
2. Obtain the actual destination hostname, listener port and protocol from the connection information. A TNS alias such as `dbname_high` is not an endpoint HOST. Keep credentials and credential-bearing descriptors out of evidence.
3. Run from the affected runtime. A notebook kernel, Spark executor, default/catalog Compute, AI Compute and a customer-subnet VM can use different paths. If the actual failing runtime is not accessible, record that limit and ask Support to correlate it; do not substitute another runtime and declare success.
4. Confirm the [existing connection method](#connection-method). Keep its required security settings; a connectivity test does not require changing database policy.
5. Start with **DNS resolution only**. From the script directory in the affected runtime:

   ```bash
   python3 ai_compute_network_diagnostics.py --config targets.json --dns-only \
     --samples 3 --interval 10 --label "Affected runtime" \
     --output dns-before.json
   ```

   In the notebook, configure section 1 and run section 3 with `RUN_CONNECTIVITY_CHECKS = False`. Use real targets; `.invalid` placeholders are rejected. Leave `expected_ips` empty and omit `expected_dns_count` unless the runtime expectations have been confirmed. DNS-only PASS covers resolution and configured expectations; TCP/TLS, login and redirects remain untested.

6. Review DNS results and correct the failed name/confirmed expectation with the appropriate owner. Then run connectivity with a fresh report:

   ```bash
   python3 ai_compute_network_diagnostics.py --config targets.json \
     --samples 3 --interval 10 --label "Affected runtime" \
     --output connectivity-before.json
   ```

   In the notebook, enable `RUN_CONNECTIVITY_CHECKS`, rerun the configuration cell and section 4. Each sample resolves all configured names again before testing every returned address up to the cap. Failed DNS/expectations skip TCP/TLS and login for that target; healthy comparison targets continue. No successful tested TCP address skips an enabled Oracle login. If transport passes, investigate the optional client and original application settings. A warning or skipped check records missing coverage, not a proven outage.

Customer-subnet testing is a useful comparison where access is permitted. Cloud Shell or a VM must actually be attached to the relevant customer network for that comparison; a general internet test is not equivalent. Even a same-subnet success cannot validate the service-side path. [AIDP private connectivity architecture](https://blogs.oracle.com/dataintegration/aidp-private-workspace-connectivity-to-oracle-databases)

<a id="connection-method"></a>

## Does this connection need a wallet or a TLS check?

Use the application's existing connection information. The customer does not need to troubleshoot TLS terminology before running DNS and listener-port checks.

- **ADB with a client wallet for mTLS:** use its mTLS host/port and mount the wallet through the supported runtime mechanism for the optional Oracle connection.
- **ADB without a wallet:** use its allowed TLS connection information and a compatible client. It is still an encrypted connection.
- **On-premises Oracle:** ask the DBA for the existing TCP or TCPS descriptor and required client security setup. TCP can use Oracle native network encryption; the absence of a raw TLS check does not certify the connection is unencrypted.
- **Another on-premises source:** use its real hostname and listener port for network checks, then its own client to validate the required security and login protocol.

A wallet cannot fix a name that does not resolve or an unreachable listener port. Once DNS and TCP pass, a client can still fail because of trust, client certificates/wallet access, credentials or database service settings. TLS/mTLS selection can change the destination port; use the descriptor's port rather than assuming a default. A wallet may contain server trust certificates without being used for mTLS, so do not infer the mode from the file's presence alone. [ADB connection methods](https://docs.oracle.com/en/cloud/paas/autonomous-database/serverless/adbsb/connect-download-wallet.html), [Oracle TLS](https://docs.oracle.com/en/database/oracle/oracle-database/26/dbseg/configuring-transport-layer-security-encryption.html)

`MTLS_WALLET_UNVERIFIED` records that the raw probe did not use the wallet. Check the optional Oracle result and original operation to decide whether the wallet-based connection works. A warning from a probe that did not use your wallet is insufficient evidence for a network repair.

<a id="workspace"></a>

## Before changing workspace network settings

Capture the existing customer-visible settings and identify the specific correction. Ask the workspace administrator to make it through the supported Workbench UI/API, preserving other required entries. Coordinate an outage window: Oracle documents that most workspace network changes require existing Compute clusters to be stopped. Wait for the change to complete, then start the required Compute and rerun the affected path. Record the change time and retain the original settings for rollback through the same interface. Do not edit service-network components directly. [Editing a workspace](https://docs.oracle.com/en/cloud/paas/ai-data-platform/aidug/workspaces.html)

<a id="dns"></a>

## DNS failure or Oracle host-resolution failure

**Codes:** `DNS_ERROR`, `DNS_NAME_NOT_FOUND`, `DNS_TEMPORARY_FAILURE`, `DNS_TIMEOUT`, `DNS_NO_ADDRESSES`, `DNS_SHORT_NAME`, `DNS_IP_LITERAL`, `CONNECTIVITY_BLOCKED_BY_DNS`, `ORACLE_BLOCKED_BY_DNS`, `ORA-17868`; the original application might report `UnknownHostException`.

Name-not-found and temporary-failure codes describe the system resolver's result; they do not establish the DNS server's response code, TTL or forwarding cause. `DNS_IP_LITERAL` skips hostname resolution; an IP socket success does not validate the original FQDN. Short names require confirmation of the intended descriptor HOST and resolver search behavior. A blocked check makes no destination connection.

**Customer diagnosis and correction**

1. Verify the exact HOST against current connection information. Correct a wrong/stale host in your application and diagnostic target.
2. If JDBC uses an alias, verify the application's `tnsnames.ora`, `TNS_ADMIN` and wallet setup separately. The explicit Python probe does not reproduce JDBC alias lookup. [Oracle JDBC alias configuration](https://docs.oracle.com/en/database/oracle/oracle-database/21/jajdb/oracle/jdbc/OracleDriver.html)
3. Have the workspace administrator verify the permitted data-source FQDNs and relevant private/advanced-DNS settings. Use [workspace change precautions](#workspace) if correction is needed.
4. Have the network administrator verify customer-owned private DNS records/views and forwarding to on-premises DNS. If that resolver cannot resolve the destination, correct that customer DNS configuration first. [OCI private DNS](https://docs.oracle.com/en-us/iaas/Content/DNS/Tasks/privatedns.htm), [DNS troubleshooting](https://docs.oracle.com/en-us/iaas/Content/DNS/Tasks/troubleshooting.htm)
5. Compare the exact same target from the failing runtime and a known-working private comparison runtime. Catalog/default Compute and sampled Spark executors must be checked on their own paths. If Python resolves an explicit host while JDBC still fails, have the workload owner check its actual descriptor/alias, JVM resolver/search configuration and cached lookup behavior; a new Python process cannot validate the JVM's cache. Retest the original JDBC/UDF operation after the proven correction.

**Escalate:** If the customer DNS and visible workspace settings are correct but the affected runtime still fails, provide the before report and comparisons. Ask Oracle Support to inspect effective attachment and service-managed DNS/CoreDNS/mapping propagation. For a service-internal dependency, use Support; do not add its internal DNS name to a customer allowlist.

**Verify recovery:** DNS resolves the intended destination from the affected runtime, subsequent connection checks pass as applicable, and the original workload succeeds. An alias-related case also requires the original JDBC operation.

<a id="dns-expectations"></a>

## Expected DNS addresses or answer count differ

**Codes:** `DNS_EXPECTATION_MISMATCH`, `DNS_COUNT_MISMATCH`.

The tool skips destination connectivity after a failed supplied expectation. Review the expectation before contacting an unintended or unverified destination; removing an unverified expectation is a diagnostic configuration correction.

1. Confirm where the expected set/count came from and whether it applies to this runtime. Customer-side database IPs can differ from service-side translated/proxy addresses.
2. If the expectation is unverified, remove it and rerun observation-only checks. This corrects the diagnostic configuration; it does not prove the network was repaired.
3. Ask Oracle Support for mapping confirmation if the runtime expectation is not customer-visible. Do not create/edit RCE mappings or change CoreDNS yourself.

**Verify recovery:** A confirmed expectation matches and the original workload succeeds, or document that mapping comparison remains unverified while the connection works. A SCAN proxy may expose one address; do not require three answers solely because the database uses RAC. [AIDP SCAN proxy behavior](https://blogs.oracle.com/dataintegration/aidp-private-workspace-connectivity-to-oracle-databases)

<a id="tcp"></a>

## TCP timeout, refusal, route or socket error

**Codes:** `TCP_TIMEOUT`, `TCP_REFUSED`, `TCP_NO_ROUTE`, `TCP_ERROR`, `ORACLE_BLOCKED_BY_TCP`.

Inspect each tested address. A single failed path remains in the report even if another path passes; this can explain intermittent or RAC address selection failures. If no tested address accepts TCP, the tool skips the optional database login. Fix connectivity first; a database login is not a substitute for a failed listener test.

1. Confirm the correct listener port and TCP/TCPS selection with the database/service owner. For refusal, ask that owner whether the destination listener is available; the rejecting device is not identified by the result alone.
2. Ask the customer network administrator to review destination ingress, applicable customer-owned egress/security-list rules, peering/routes and return routing. For on-premises destinations, include the DRG, VPN/FastConnect, firewalls and the return path.
3. Before correcting a customer rule, establish the **source as seen by the destination**, intended protocol and port. The notebook hostname, service-side synthetic DNS address and destination-visible source are not interchangeable. Ask Support for source confirmation if it is not customer-visible.
4. Make only the proven customer-owned correction using normal change control. Do not treat a timeout as proof that a specific NSG is wrong.

For ADB private endpoints, the database's customer VCN security configuration governs access; the selected connection information determines the TLS/mTLS listener. Oracle documents mTLS on 1522 and TLS on 1521 or 1522. [ADB private-endpoint access](https://docs.oracle.com/en-us/iaas/autonomous-database-serverless/doc/private-endpoints-autonomous.html)

**Escalate:** If the customer path is verified, ask Oracle Support to inspect AIDP service-side NSGs, routing, private connectivity and RCE/proxy health for the recorded attempt. Customers cannot inspect those internal controls.

**Verify recovery:** The intended listener accepts TCP from the affected runtime, required TLS/client checks pass, and the original operation succeeds.

<a id="tls"></a>

## TLS validation failure or unverified mTLS wallet

**Codes:** `TLS_VERIFY_FAILED`, `TLS_FAILED`, `MTLS_WALLET_UNVERIFIED`.

1. Confirm that the selected endpoint/port uses TCPS and matches the database TLS/mTLS policy.
2. Verify the intended certificate hostname and trusted CA with the destination owner. Review your runtime's configured CA bundle/wallet files and accessibility.
3. For mTLS, use the optional Oracle probe with a properly deployed wallet and compatible client mode. Raw TLS does not load an Oracle wallet, so its warning can remain after a successful Oracle login. [python-oracledb connection configuration](https://python-oracledb.readthedocs.io/en/latest/user_guide/connection_handling.html)
4. Correct the proven identity, trust or wallet deployment problem. Do not disable verification or replace a hostname with an arbitrary IP to make the test pass.

**Escalate:** If the compatible client still fails after customer identity/trust/wallet checks, provide its sanitized code and stage results to Oracle Support and the destination owner.

**Verify recovery:** The required client handshake/login and original workload succeed with verification enabled. Record remaining raw-probe warnings as coverage limits.

<a id="scan"></a>

## SCAN socket passes but redirect is unverified or fails

**Code:** `SCAN_REDIRECT_UNVERIFIED`; a real connection can instead produce an Oracle/DPY failure.

1. Ask the workspace administrator to check the configured SCAN FQDN/port and the database owner to confirm the service and advertised listeners.
2. Enable one Oracle probe with those connection details. A socket-only check does not exercise redirection.
3. For customer-operated RAC, have its DBA validate the appropriate local listeners/instances and advertised listener identities. For managed database internals, use that service's Support team.
4. Treat direct `redirect_targets` probes as supplemental. They may use a different path from translated redirects. If the customer configuration/listeners are valid but the connection still fails, ask AIDP Support to inspect the service-managed SCAN proxy and translation.

**Verify recovery:** One Oracle connection and `SELECT 1 FROM DUAL` pass, followed by the original workload. That establishes one chosen path; all RAC instances and failover need separate validation. [Oracle RAC SCAN behavior](https://docs.oracle.com/en/database/oracle/oracle-database/18/rilin/about-connecting-to-an-oracle-rac-database-using-scans.html)

<a id="accounts"></a>

## Rejected credentials, locked account or expired password

**Codes:** `ORA-01017`, `ORA-28000`, `ORA-28001`, `ORACLE_RETRY_SUPPRESSED`.

Stop login retries and contact the database owner. For a customer-owned account, its DBA should verify authorization/account state, correct the credential issue, and coordinate updating your runtime credential reference. For an Oracle/AIDP service-managed account, including a platform dependency, use Oracle Support; customers are not expected to obtain, change or unlock that account.

The script suppresses later sampled logins after these errors **within one invocation**. Starting it again starts a new invocation, so do not repeatedly restart login probes against an unresolved account.

**Verify recovery:** After owner confirmation, perform one controlled Oracle probe and the original operation. Network-only success cannot resolve authentication. [ORA-28000](https://docs.oracle.com/en/error-help/db/ora-28000/), [ORA-28001](https://docs.oracle.com/en/error-help/db/ora-28001/), [ORA-01017](https://docs.oracle.com/en/error-help/db/ora-01017/)

<a id="oracle-service"></a>

## Listener service, instance readiness or access filtering

**Codes:** `ORA-12514`, `DPY-6001`, `ORA-12523`, `ORA-12506`.

Verify the selected service against current database connection information. For customer-operated databases, its DBA can investigate service registration, instance readiness and listener access policy. Customer network/database administrators can verify access controls they own and the destination-visible source. For ADB or another managed database, use its documented connection settings and customer-visible controls; internal listener repairs belong to Oracle Support.

If the same configuration works outside AIDP but fails on the affected AIDP path, provide that comparison to AIDP Support as evidence of differing paths, not proof of a service-network cause.

**Verify recovery:** A login to the intended service and the original operation succeed. A listener TCP connection alone is insufficient. [ORA-12514](https://docs.oracle.com/en/error-help/db/ora-12514/), [ORA-12523](https://docs.oracle.com/en/database/oracle/oracle-database/19/errmg/ORA-12500.html), [python-oracledb errors](https://python-oracledb.readthedocs.io/en/latest/user_guide/troubleshooting.html)

<a id="client"></a>

## Optional Oracle client is missing, unconfigured or incompatible

**Codes:** `ORACLE_DRIVER_MISSING`, `ORACLE_ENV_MISSING`, `ORACLE_PROBE_NOT_CONFIGURED`, `ORACLE_PROBE_NOT_ENABLED`, `ORA-28759`, `DPI-1047`, `DPY-3001`, `DPY-3010`.

Use the runtime's supported dependency/secret mechanism and the destination's required client mode. Verify the installed driver, needed client libraries and wallet deployment with the workspace/application administrator. Provide environment-variable names in target JSON, and values through the normal secret mechanism. Select Thin mode only when compatible with the destination; it is not a workaround for every Thick-mode dependency error.

A deliberately disabled Oracle probe is a coverage choice, not an outage. If enabling it, follow the [README setup](README.md#optional-database-and-scan-redirect-check). If the supported runtime mechanism fails or is not accessible, ask the administrator or Oracle Support for that supported path rather than modifying service images.

**Verify recovery:** The Oracle probe executes in the affected runtime without prerequisite warnings, and the original connector/application succeeds using its own client configuration.

<a id="coverage"></a>

## Address coverage or IPv6 is incomplete

**Codes:** `ADDRESS_COVERAGE_INCOMPLETE`, `IPV6_SOURCE_UNAVAILABLE`.

Review the returned addresses and `max_addresses` cap. Rerun within the supported cap when more addresses must be tested. Confirm required IPv4/IPv6 connectivity with the network administrator; ask Support about managed-runtime support if the required family cannot be exercised.

**Verify recovery:** Required paths have been tested or the remaining gap is explicitly recorded. An IPv4 pass, omitted address or sampled executor does not certify every path.

<a id="runtime"></a>

## Probe subprocess or executor execution is unavailable

**Codes:** `CHILD_PROCESS_FAILED`, `ORACLE_PROCESS_FAILED`, `EXECUTOR_CONFIG_UNAVAILABLE`; the notebook can also report a skipped/failed executor sample.

Check customer-provided dependencies, configuration files/CA deployment and your supported runtime access. Classic Spark executor sampling requires RDD support; Spark Connect may skip it. If the actual failing runtime cannot be reached or supported execution still fails, supply the runtime/resource and UTC attempt to Support. A probe-process failure does not prove DNS or NSG failure.

**Verify recovery:** The requested probe executes in the intended runtime. Retain missing executor coverage and confirm the original driver/executor operation separately.

<a id="unclassified"></a>

## Generic or unclassified Oracle/application connection failure

**Codes:** `DPY-6005`, `ORACLE_TIMEOUT`, `ORACLE_CLIENT_ERROR`, and other unresolved FAIL/WARN codes.

Compare the recorded network stages with the actual application client configuration. A generic wrapper or total deadline does not identify DNS, TCP, TLS, login, redirect or query as the cause. Do not apply a guessed network change. [python-oracledb error handling](https://python-oracledb.readthedocs.io/en/latest/user_guide/troubleshooting.html)

For a service-managed dependency such as AIDP Memory, ask Oracle Support to identify the non-secret destination, underlying error and owner-side outcome. Customers are not expected to obtain internal credentials or configure an unknown platform endpoint in the script. A background retry-exhausted event alone does not prove that an entire chat, deployment or job failed.

**Verify recovery:** The owner identifies and corrects the failure, then the original operation succeeds; for service-managed operations, Support must confirm the corresponding outcome.

<a id="recovery"></a>

## Verify a correction and decide whether to close

Capture a fresh report with the same targets, relevant client settings and affected runtime. Keep before/after files separate:

```bash
python3 ai_compute_network_diagnostics.py --config targets.json \
  --label "Affected runtime after correction" --output connectivity-after.json
```

After database-owner confirmation, add `--oracle-probe --samples 1` for one controlled database connection where appropriate. Then rerun the original catalog read, JDBC query, notebook/job or AI Compute operation.

Close only when the original operation succeeds on the required path and outstanding skipped/warning coverage is understood. For intermittent failures, observe an appropriate workload window: three samples can miss a problem. Earlier FAIL results remain in a repeated report even if later samples pass; retain them as history rather than treating that report as proof the correction failed.

<a id="support"></a>

## Prepare a private Oracle Support handoff

Use the normal Oracle Support channel for your service. Include only details available to the customer; you do not need service tenancy IDs, Kubernetes namespaces or CoreDNS access.

```text
Region and UTC start/end:
AIDP instance / workspace / affected Compute or AI Compute identifiers:
Original operation and available request/command/job IDs:
Destination hostname, port, TCP/TCPS, client mode, TLS/mTLS:
First failed stage and sanitized code:
Before/after diagnostic reports and test runtime labels:
Customer-visible workspace configuration reviewed:
Customer network / database owner findings and comparison vantage points:
Changes made, UTC time, and results:
Original workload outcome and remaining coverage limits:
Requested investigation: correlate the failing runtime/attempt and inspect
the appropriate service-owned DNS, network, SCAN or dependency component.
```

Include network metadata in a **private support request**, not a public GitHub issue. Remove passwords, tokens, wallet material and credential-bearing connection strings. Do not attach raw payloads or transcripts containing secrets. Mark unavailable fields as unknown, and preserve the useful target/timing/correlation metadata. [Oracle Support](https://www.oracle.com/support/)

## License

Copyright (c) 2026 Oracle and/or its affiliates. Licensed under the Universal Permissive License (UPL), Version 1.0. See [LICENSE.txt](../../LICENSE.txt).
