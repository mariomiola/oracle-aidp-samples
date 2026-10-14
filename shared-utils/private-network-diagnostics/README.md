# Test an ADB connection from a private workspace

Create your AIDP workspace with **private network access**, then run these checks from its Compute notebook or AI Compute.

The same simple tests cover:

- ADB with a private endpoint in the workspace's subnet.
- ADB with a private endpoint in another subnet or VCN.
- ADB with a public endpoint protected by an access control list (ACL).

## Compute notebook

Open the [notebook](AIDP_Compute_Private_Network_Diagnostics.ipynb) on your workspace's Compute.

1. In section 1, enter **HOST** and **PORT** from ADB's **Database Connection** information. These are the only network-test inputs.
2. Run sections 1–3 to check **DNS**. The expected result is `DNS_RESOLVED`.
3. Set `RUN_CONNECTIVITY_CHECKS = True`, rerun section 1 and run section 4 to check **reachability**. The expected result is `TCP_CONNECTED`.
4. Use the database connection your application already uses to run:

   ```sql
   SELECT 1 FROM DUAL;
   ```

   The expected result is `1`. Keep the existing wallet or walletless connection setup.

DNS and reachability checks do not require credentials, a wallet or TLS choices. They do not prove database login or ACL acceptance; the query completes that test.

<a id="ai-compute"></a>

## AI Compute

Make the [script](ai_compute_network_diagnostics.py) available to the Python execution you already use in AI Compute. Enter the same ADB HOST and PORT:

```python
from ai_compute_network_diagnostics import run_samples, print_report

HOST = ""  # Copy HOST from ADB Database Connection.
PORT = 1522  # Copy its PORT; keep 1522 only if that is the selected port.
config = {"endpoints": [
    {"name": "ADB", "kind": "adb", "host": HOST, "port": PORT, "protocol": "tcps"}
]} if HOST else {"endpoints": []}

# First, check DNS.
dns_result = run_samples(config, dns_only=True)
print_report(dns_result)
```

After DNS succeeds, run the reachability check:

```python
connection_result = run_samples(config, tcp_only=True)
print_report(connection_result)
```

Then run `SELECT 1 FROM DUAL` with your application's existing database connection. Both samples use the same checks; no separate terminal or test VM is needed.

## If a test fails

Use the [short guide for your ADB case](RUNBOOK.md). Start with the failed stage and retry after the identified correction. Keep the original application's connection settings.

The notebook and script observe the runtime where they run. A result from another Compute or a laptop does not validate this workspace's connection. DNS answers can be translated; no expected IPs or address counts are required. [AIDP private connectivity guidance](https://blogs.oracle.com/dataintegration/aidp-private-workspace-connectivity-to-oracle-databases)

Optional references: [other datasources and deeper checks](ADVANCED_CHECKS.md), [script/client options](SCRIPT_REFERENCE.md), [test coverage](TEST_COVERAGE.md).

## Maintaining the samples

Network tests need Python 3.9+ and its standard library. The optional Oracle login probe needs its existing client and credential configuration. Regenerate the notebook with `build_compute_notebook.py` after script edits; keep the distributed notebook unexecuted.

## License

Copyright (c) 2026 Oracle and/or its affiliates. Licensed under the Universal Permissive License (UPL), Version 1.0. See [LICENSE.txt](../../LICENSE.txt).
