# AIDP private connectivity: understand your network and fix the failed stage

Start in the AIDP environment where the connection fails:

- **Compute:** open the [diagnostic notebook](AIDP_Compute_Private_Network_Diagnostics.ipynb) on the affected Compute. Enter the datasource host and port, run the DNS cells, then the connectivity cell.
- **AI Compute:** use the [diagnostic script](ai_compute_network_diagnostics.py) through the Python execution interface available in your AI Compute environment. Run its DNS stage, then its connectivity stage. See the [AI Compute example](README.md#ai-compute).

The workflow is **DNS resolution → listener connectivity → existing database client → original operation**. You can begin inside AIDP without a separate VM, Cloud Shell session or terminal. Use the results below to understand the relevant part of your network setup and correct a specific problem.

<a id="network-setup"></a>

## Identify the network path for your datasource

Review your workspace's private-network settings and the datasource's connection information. Record what you know; an unknown field does not prevent a DNS test.

| Detail | What to identify |
| --- | --- |
| Workspace network | The selected customer VCN and subnet; confirm this is the workspace used by the failing notebook, job or AI Compute operation. |
| Destination | Actual HOST, listener PORT and database service from the application's connection information. A TNS alias is not the HOST. |
| DNS source | Does the name belong to an OCI private zone, another VCN's DNS, or an on-premises DNS server? Which customer VCN resolver view or forwarding rule supplies it? See [VCN private resolver configuration](https://docs.oracle.com/en-us/iaas/Content/Network/Concepts/dns-topic-Private-resolver.htm). |
| Database location | Same VCN, another VCN/region, or on-premises. Identify the relevant peering or DRG/VPN/FastConnect path. |
| Connection controls | Workspace-selected NSGs, subnet security lists, destination rules/firewall and return routing for the required listener port. |
| RAC/SCAN, if used | The SCAN hostname/port in workspace settings and the selected database service. |

For ordinary private names, AIDP uses the customer VCN DNS context. The DNS configuration selects the backend destination; AIDP can return a translated address. A translated answer can differ from the database's private IP without indicating a fault. [AIDP private connectivity architecture](https://blogs.oracle.com/dataintegration/aidp-private-workspace-connectivity-to-oracle-databases)

Keep these two paths distinct:

```text
DNS path:        datasource name → customer VCN DNS view or forwarding rule → DNS answer
Connection path: resolved destination + listener port → network route and rules → database
```

If the DNS server is on-premises, the DNS query itself needs a working network path to that server. Once the name resolves, reaching the database requires its own route and listener-port access. Fixing one path does not establish that the other works.

<a id="start"></a>

## Run the checks in your existing AIDP runtime

1. Configure the exact HOST and PORT. Start with ADB; use the selected **Database Connection** information. For EBS, use its database listener. For on-premises databases or SCAN, use the existing application descriptor.
2. **Compute notebook:** run sections 1, 2 and 3 with `RUN_CONNECTIVITY_CHECKS = False`. **AI Compute script:** call `run_samples(..., dns_only=True)` through your existing Python execution interface. No wallet or database account is needed for DNS.
3. Review the DNS result using [DNS diagnosis](#dns). Leave `expected_ips` empty and omit `expected_dns_count` unless confirmed for this runtime. An IP literal skips the hostname check.
4. After DNS succeeds, **Compute notebook:** enable `RUN_CONNECTIVITY_CHECKS`, rerun the configuration cell and section 4. **AI Compute script:** run the connectivity stage with `dns_only=False`. Review each returned address using [connectivity diagnosis](#tcp).
5. Once the listener is reachable, use your application's existing client and connection method. ADB can use a wallet or its permitted walletless connection; on-premises databases use their configured security. An optional Oracle probe can perform one login and `SELECT 1 FROM DUAL`.
6. Correct the identified setting, rerun the failed stage and subsequent stages, then repeat the original notebook/job/catalog/AI Compute operation. Keep before/after results separate.

The notebook tests its Python kernel's path. Optional executor sampling tests sampled Spark hosts; notebook success does not establish executor success. If the failing catalog/default Compute or another runtime cannot be tested, record that path as untested.

<a id="application-tests"></a>

## Choose the application test

For each datasource, run **DNS first, then listener connectivity** in your affected Compute notebook or AI Compute. After the listener is reachable, use the connection settings your application already has:

| Your datasource | Application test | What success confirms |
| --- | --- | --- |
| **ADB, with or without a wallet** | Connect using the selected Database Connection information and your existing wallet or walletless setup. Run `SELECT 1 FROM DUAL`. | The selected database service accepts this client connection and query. |
| **Cloud EBS database** | Connect to the EBS database listener using the existing Oracle client settings. Run `SELECT 1 FROM DUAL`, then a small read your application normally performs. | Database access works; EBS application functions need their own test. |
| **On-premises Oracle database** | Use the existing descriptor and client security settings. Run `SELECT 1 FROM DUAL`. | This database service is reachable through the configured private network path. |
| **Oracle RAC using SCAN** | Connect through SCAN with the actual Oracle client and run `SELECT 1 FROM DUAL`. Check the initial listener and redirected listener connections if it fails. | One selected redirect works; other RAC nodes and failover need separate checks. |
| **Other databases or APIs** | Use the application's existing driver or SDK for a small query/read. | That client can perform the tested operation; Oracle login checks do not test these protocols. |

If the listener test fails, follow the [firewall check](#firewall). If the listener works but the application test fails, check the reported client error and your existing [wallet/security](#connection-method), [account](#accounts) or [database service](#oracle-service) settings. Finish by repeating the original notebook, job, catalog read or AI Compute operation.

<a id="dns"></a>

## DNS does not resolve the datasource name

**Results:** `DNS_NAME_NOT_FOUND`, `DNS_TEMPORARY_FAILURE`, `DNS_TIMEOUT`, `DNS_NO_ADDRESSES`, `DNS_ERROR`, `ORA-17868` or application `UnknownHostException`.

Check the exact HOST first. If it is correct, follow the DNS source identified in your network setup:

| DNS source | Inspect | Correction and retest |
| --- | --- | --- |
| OCI private zone | Correct record, zone/view association with the workspace VCN resolver, and any CNAME target. | Correct the record or view association, then rerun AIDP DNS. |
| DNS in another VCN | Shared private view or forwarding rule, destination listening endpoint, and the network path to it. | Correct the identified view, forwarding or DNS network restriction, then rerun AIDP DNS. |
| On-premises DNS | Domain forwarding rule, forwarding endpoint, target DNS server, route through the hybrid connection and DNS replies. | Correct the identified rule, record or DNS-server reachability issue, then rerun AIDP DNS. |
| Same name has public and private answers | Private zone/view selected for the workspace VCN and the intended record. | Correct an unintended DNS view or record. Different answers outside the private network can be expected. |

Review DNS view selection and forwarding-rule order when a lookup goes to the wrong server. An earlier matching view or rule can prevent the intended forwarding path from being used. Verify DNS traffic and replies between the forwarding endpoint and the target server, including required UDP/TCP port 53 access. The script's system-resolver result does not reveal the DNS response code, TTL or which rule was selected. [OCI private DNS configuration](https://docs.oracle.com/en-us/iaas/Content/DNS/Tasks/privatedns.htm)

If a firewall is on the forwarding path, use the [DNS firewall check](#dns-firewall) below.

For `DNS_SHORT_NAME`, use the descriptor's full HOST and confirm any intended search suffix. If JDBC uses a TNS alias, verify `tnsnames.ora`, `TNS_ADMIN` and the selected descriptor with that client. Python's explicit HOST lookup does not validate JDBC alias lookup or the JVM's cached DNS answer. [Oracle JDBC alias configuration](https://docs.oracle.com/en/database/oracle/oracle-database/21/jajdb/oracle/jdbc/OracleDriver.html)

A blocked downstream check means no destination connection was attempted. After DNS resolves, proceed to the listener check and then rerun the original operation.

<a id="customer-dns"></a>
<a id="comparison"></a>

## Optional comparison when the AIDP result needs explanation

If a DNS or socket failure remains unclear and you already have an accessible VM or private Cloud Shell in the workspace's customer network, compare the **same HOST and PORT** there. This is an additional diagnostic, not a prerequisite to using the AIDP notebook or script.

| Comparison | What it tells you to check |
| --- | --- |
| Name fails in both places | HOST, customer DNS record/view or forwarding path. |
| Name works in the customer VCN but fails in AIDP | Workspace network selection and the exact application HOST; review advanced DNS only for an applicable datasource. |
| Both resolve with different IPs | Translation or different DNS views may explain this. Test each runtime's returned addresses; do not copy backend IPs into AIDP expectations. |
| Listener fails in both places | Listener readiness and the route/rules to the datasource. |
| Listener works from the comparison machine but fails in AIDP | Access rules and return routing for the AIDP source actually observed at the destination. The comparison machine uses a different source. |

Keep results labeled with their runtime and UTC time. A laptop or public Cloud Shell lookup does not validate the workspace's private DNS view. A comparison success does not establish that the affected AIDP path works.

<a id="advanced-dns"></a>

## Check advanced DNS only when it applies

Ordinary private/on-premises names start with the DNS records, views and forwarding described above. A workspace FQDN access entry does not create a missing customer DNS record.

Review **My data sources require advanced DNS configuration** for the documented cases: network-source requirements involving your in-region Service Gateway, cross-region sources, S3, or an `oraclecloud.com` service behind a private endpoint. Enter the applicable datasource FQDNs through the workspace interface, preserve other required entries, and retest the actual connection after the change completes. [Workspace network settings](https://docs.oracle.com/en/cloud/paas/ai-data-platform/aidug/workspaces.html)

<a id="dns-expectations"></a>

## A configured DNS expectation does not match

**Results:** `DNS_EXPECTATION_MISMATCH`, `DNS_COUNT_MISMATCH`.

Confirm the expected IP set/count applies to the runtime being tested. If it is unverified, remove that expectation and collect the observed DNS result. This corrects the test configuration; it does not repair the network. AIDP can return translated addresses, and SCAN proxy DNS can return one address. Do not require the customer-side database IPs or three SCAN answers in every runtime. [AIDP DNS and SCAN behavior](https://blogs.oracle.com/dataintegration/aidp-private-workspace-connectivity-to-oracle-databases)

<a id="tcp"></a>

## DNS resolves, but the listener cannot be reached

**Results:** `TCP_TIMEOUT`, `TCP_REFUSED`, `TCP_NO_ROUTE`, `TCP_ERROR`, `ORACLE_BLOCKED_BY_TCP`.

1. Confirm the listener PORT from the existing descriptor. For refusal, verify the listener is available; a refusal alone does not identify which device rejected the attempt.
2. Follow the datasource route: within the VCN, across VCN peering/DRG, or through VPN/FastConnect to on-premises. Check the route table associated with the relevant subnet and the return path.
3. Review applicable workspace-selected NSGs, subnet security lists, destination rules and firewalls for that listener port. Use the AIDP source **as seen at the destination**, using existing flow/firewall/listener logs where available. A translated DNS destination IP and a comparison VM's source are not that source.
4. Correct the identified listener, route or rule and rerun connectivity in the affected notebook or AI Compute. Retain a failed address even if another returned address succeeds; this can explain intermittent failures.

A timeout does not identify a particular missing NSG rule. If no tested address accepts TCP, the optional database login is skipped. After listener reachability passes, test the existing database client and original operation.

<a id="firewall"></a>

## Check what the firewall needs to allow

Use a test from your **affected notebook or AI Compute** and match it to a firewall event. The firewall's observed source address is the useful address for its rule; the notebook's local address can change along the network path.

1. **Run DNS.** Confirm the exact datasource HOST resolves. Correct a lookup failure before testing the database listener.
2. **Run one connectivity sample.** Test one datasource, with database login disabled. In Compute, use `REPEAT_SAMPLES = 1` and `RUN_ORACLE_PROBE = False`, then run section 4. In AI Compute, use the connectivity stage with `sample_count=1` and `oracle_probe=False`. One sample can test several returned addresses.
3. **Find the matching firewall event.** Note the test time in UTC; it is also recorded as `observed_at_utc` in the saved results. Search around that time for the datasource connection, allowing for the test's duration. Check the fields below and the matched rule.
4. **Correct the identified restriction.** A matching **deny** event identifies a rule to review. Allow the verified source to the required destination and listener port, and check the return route. If no event appears, check logging coverage, the time window and routing; a timeout alone does not prove firewall blocking.
5. **Retest the connection.** Repeat connectivity, then the [application test](#application-tests) and original operation. Repeat from each Compute/AI Compute path you need; one observed source does not establish every path's source range.

| Firewall field | Use this value |
| --- | --- |
| Source | The IP/address range verified for this AIDP connection at the firewall. |
| Destination | The database address on the customer network, as shown in the firewall event. A translated AIDP DNS address can differ. |
| Protocol and destination port | TCP and the listener PORT from the selected connection information. Client source ports can vary. |
| Result | Allow/deny, matched rule and connection/session outcome, where logged. |

For OCI security rules, stateful rules track response traffic. Stateless rules require corresponding response rules; either setup still needs a valid return route. [OCI security rules](https://docs.oracle.com/en-us/iaas/Content/Network/Concepts/securityrules.htm)

For **SCAN**, the actual client connection must reach the initial SCAN listener and the redirected database listener. Check both events if a login fails, including their destination addresses and configured ports. [AIDP SCAN connection path](https://blogs.oracle.com/dataintegration/aidp-private-workspace-connectivity-to-oracle-databases)

<a id="dns-firewall"></a>

### If DNS is forwarded through a firewall

Run the DNS stage in your notebook or AI Compute. For a failed forwarded lookup, check the rule for that domain and the path from the **DNS forwarding endpoint to the configured DNS server**. Verify UDP/TCP destination port 53 and replies. The DNS forwarding source can differ from the database connection source. After correcting the identified restriction, rerun DNS, then database connectivity. [OCI DNS forwarding requirements](https://docs.oracle.com/en-us/iaas/Content/DNS/Tasks/privatedns.htm)

<a id="connection-method"></a>
<a id="tls"></a>

## Listener reachable: use the connection method you already have

For ADB, follow the selected Database Connection descriptor: use the wallet where that connection requires it, or the permitted walletless method. For on-premises Oracle, use the configured TCP/TCPS descriptor and client security settings. Copy the actual port; do not change database policy to make a probe pass. [ADB connection information](https://docs.oracle.com/en/cloud/paas/autonomous-database/serverless/adbsb/connect-download-wallet.html)

`TLS_VERIFY_FAILED` or `TLS_FAILED` calls for checking the endpoint's certificate identity, trust configuration and required client setup. Keep verification enabled. `MTLS_WALLET_UNVERIFIED` means the raw network probe did not test the wallet; validate the wallet with the matching Oracle client. That warning can remain even when the client connects successfully.

Oracle native network encryption can operate over TCP and needs a compatible client. Other on-premises databases need their own client for secure login and application-protocol checks. [Oracle client connection configuration](https://python-oracledb.readthedocs.io/en/latest/user_guide/connection_handling.html)

<a id="scan"></a>

## SCAN listener reachable: validate the redirected connection

**Result:** `SCAN_REDIRECT_UNVERIFIED`.

Confirm the workspace's SCAN FQDN/port, the database service and the advertised local listeners. Run an actual Oracle connection using the existing descriptor to exercise the selected redirect. For RAC databases you administer, check listener registration and node-specific routing/rules if the redirect fails.

Known `redirect_targets` are supplemental: a direct listener probe can use a different path from the SCAN connection. One successful login and `SELECT 1 FROM DUAL` validate one chosen path, not every RAC instance or failover scenario. Finish by rerunning the original workload. [Oracle RAC SCAN behavior](https://docs.oracle.com/en/database/oracle/oracle-database/18/rilin/about-connecting-to-an-oracle-rac-database-using-scans.html)

<a id="accounts"></a>

## Database rejects the account or password

**Results:** `ORA-01017`, `ORA-28000`, `ORA-28001`, `ORACLE_RETRY_SUPPRESSED`.

Stop repeated logins. Verify the configured credential reference and review the database account's authorization, lock or expiry state with your DBA. Update the credential reference after correcting the account/password issue, then make one controlled connection and rerun the original operation. Network changes do not fix an account error.

The script suppresses subsequent sampled logins after these errors **within one invocation**. Starting a new invocation enables attempts again; do not restart login tests until the account issue is corrected.

<a id="oracle-service"></a>

## Database service is unavailable or rejects the source

**Results:** `ORA-12514`, `DPY-6001`, `ORA-12523`, `ORA-12506`.

Verify the selected service against current connection information and the database's availability. Review listener registration and instance readiness with your DBA where accessible. Check source-based database access controls for the actual AIDP attempt. Retest the client and original operation after the specific correction.

<a id="client"></a>

## The optional Oracle probe cannot start

**Results:** missing driver, environment reference, wallet or client library; `ORA-28759`, `DPY-3010`, `DPY-3001`, `DPI-1047`.

Use the runtime's existing dependency and secret mechanisms. Confirm the selected driver/client mode supports the database and its encryption setup, and that required wallet/client files are available in the runtime. DNS and socket checks do not require the Oracle probe. See the [optional client configuration](README.md#oracle-client).

<a id="unclassified"></a>

## Generic client error or total connection timeout

**Results:** `DPY-6005`, `ORACLE_TIMEOUT` or another unclassified client error.

Compare the preceding DNS and TCP results, then the actual client's service, trust/wallet and account configuration. The generic code does not identify a specific network cause. Retain the sanitized code and original-operation outcome; do not select a guessed network correction.

<a id="coverage"></a>
<a id="runtime"></a>

## A check could not cover the required path

For `ADDRESS_COVERAGE_INCOMPLETE`, review the address cap and the untested addresses. For `IPV6_SOURCE_UNAVAILABLE`, record that the required IPv6 path is untested. For a child-process or executor failure, check the available runtime dependencies, uploaded files and execution mechanism. An inaccessible runtime remains untested; a different runtime's success does not fill that gap.

<a id="workspace"></a>

## Apply a workspace network correction

Save the existing settings and change only the identified VCN/subnet, NSG, SCAN or applicable advanced DNS setting through the workspace interface. Coordinate the change: Oracle documents that most workspace network changes require existing Compute clusters to be stopped. Wait for completion, start the required Compute and repeat its DNS, connectivity and original-operation checks. [Editing a workspace](https://docs.oracle.com/en/cloud/paas/ai-data-platform/aidug/workspaces.html)

<a id="recovery"></a>

## Verify the original connection works

Keep before/after results for the same target and affected runtime. Repeat DNS, listener connectivity and the matching client connection, then the original catalog read, JDBC query, notebook/job or AI Compute operation.

For intermittent failures, repeat DNS/network sampling and observe the original workload over an appropriate period. Three passing samples can miss a fault; earlier failures remain in the saved history. Keep credentials, wallet contents and credential-bearing descriptors out of diagnostic exports.

## License

Copyright (c) 2026 Oracle and/or its affiliates. Licensed under the Universal Permissive License (UPL), Version 1.0. See [LICENSE.txt](../../LICENSE.txt).
