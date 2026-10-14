# Three simple ADB connection tests

Start in your **private workspace**, using its Compute notebook or AI Compute script. Copy only **HOST** and **PORT** from the selected ADB **Database Connection** information. Use the actual HOST, not a TNS alias such as `dbname_high`.

<a id="start"></a>
<a id="dns"></a>

## 1. Does the name resolve?

Run notebook sections 1–3, or the AI Compute DNS check in the [quick start](README.md#ai-compute).

**Pass:** `DNS_RESOLVED` appears. Continue to test 2.

**Fail:** copy HOST again from the current ADB connection information and retry. Confirm you ran the test in the private workspace you created. An ACL change cannot repair a failed hostname lookup.

<a id="tcp"></a>

## 2. Can the workspace reach ADB?

In the notebook, set `RUN_CONNECTIVITY_CHECKS = True`, rerun section 1, then run section 4. In AI Compute, run the connection check with `tcp_only=True`.

**Pass:** `TCP_CONNECTED` appears for the tested address. Continue to test 3. Keep any failed address results; they can explain intermittent failures.

**Fail:** recheck PORT against the selected ADB connection information and confirm the database is **Available**. Then follow the row for your case below. A timeout alone does not identify a firewall or ACL fault.

<a id="application-tests"></a>
<a id="connection-method"></a>
<a id="tls"></a>
<a id="accounts"></a>
<a id="oracle-service"></a>
<a id="client"></a>
<a id="unclassified"></a>

## 3. Does the database connection work?

Use the connection your application already uses, including its wallet if it uses one. Run:

```sql
SELECT 1 FROM DUAL;
```

**Pass:** the query returns `1`. Repeat your original notebook, job or catalog operation to complete validation.

**Fail:** follow the database error. A rejected/locked/expired account needs a credential or account correction. A wallet or service-name error needs the existing connection setup corrected. Use the optional [Oracle client probe](SCRIPT_REFERENCE.md#oracle-client) only if you want the sample to run this query with configured credentials.

The basic network tests do not perform a separate TLS handshake or validate your wallet. The actual database client performs its normal secure connection for test 3.

<a id="same-subnet"></a>
<a id="remote-subnet"></a>
<a id="acl"></a>

## What changes between the three cases?

The tests and inputs stay the same. Only the follow-up for a failed connection differs:

| Your ADB case | Simple follow-up if the connection fails |
| --- | --- |
| **Private endpoint in the workspace's subnet** | Confirm ADB is Available and its private-endpoint security rules allow the selected database port from the workspace's private connection. |
| **Private endpoint in another subnet or VCN** | Check the same ADB settings. The existing private network must also connect the workspace to the database network; selecting a workspace subnet does not create a connection to another VCN. |
| **Public endpoint with an ACL** | In ADB's **Network / Access control list** settings, review the allowed IPs/VCNs for the workspace's connection. Allowing only your laptop's address does not establish AIDP access. A successful query confirms acceptance of this connection. |

For a public endpoint, a VCN ACL entry applies to a Service Gateway path. If your configured path uses a NAT Gateway, the IP-based ACL uses that gateway's public IP, available in OCI Console. The test does not guess this address or ask you to supply it. [ADB access control rules](https://docs.oracle.com/en-us/iaas/autonomous-database-serverless/doc/access-control-rules-autonomous.html)

The private-endpoint cases use the database's private network security controls; a public-endpoint ACL does not apply to the private endpoint. [ADB private endpoint access](https://docs.oracle.com/en-us/iaas/autonomous-database-serverless/doc/private-endpoints-autonomous.html)

<a id="recovery"></a>

## Finish

After an identified correction, repeat the failed test and the tests after it. Save the before/after results if the issue is intermittent. A passing DNS or socket test alone does not complete the database test.

<a id="network-setup"></a>
<a id="customer-dns"></a>
<a id="comparison"></a>
<a id="advanced-dns"></a>
<a id="dns-expectations"></a>
<a id="scan"></a>
<a id="coverage"></a>
<a id="runtime"></a>
<a id="workspace"></a>
<a id="firewall"></a>
<a id="dns-firewall"></a>

## Optional deeper investigation

If these checks leave a failure unresolved, the [detailed reference](ADVANCED_CHECKS.md) covers DNS forwarding, workspace settings, firewall events, SCAN and other datasources. Those details are not prerequisites for the three ADB tests. Public ADB access can also depend on the workspace's supported DNS configuration for the ADB hostname; see the [documented workspace settings](https://docs.oracle.com/en/cloud/paas/ai-data-platform/aidug/workspaces.html).

## License

Copyright (c) 2026 Oracle and/or its affiliates. Licensed under the Universal Permissive License (UPL), Version 1.0. See [LICENSE.txt](../../LICENSE.txt).
