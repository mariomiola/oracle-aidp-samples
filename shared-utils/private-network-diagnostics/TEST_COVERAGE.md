# Failure coverage and acceptance tests

The primary ADB flow is **DNS → TCP listener reachability → query with the existing database client**. It asks only for HOST/PORT from ADB Database Connection. `tcp_only=True` skips raw TLS probes; it does not change the actual database client's security. Optional broader diagnostics still cover TLS, SCAN and other datasources.

| ADB scenario | Live acceptance |
| --- | --- |
| Private endpoint in the workspace subnet | DNS and listener checks pass from that workspace, then its existing client returns `1` for `SELECT 1 FROM DUAL`. |
| Private endpoint in another subnet or VCN | The same tests pass across the intended private network path, followed by the original operation. |
| Public endpoint protected by an ACL | The actual database query succeeds from the workspace. TCP success alone cannot establish ACL acceptance. |

`test_adb_tcp_only_does_not_probe_tls_or_wallet_in_repeated_samples` verifies basic reachability avoids wallet warnings and preserves explicit untested stages. `test_adb_tcp_only_dns_failure_still_blocks_connections` verifies the DNS prerequisite. `test_adb_acl_rejection_is_not_hidden_by_successful_tcp_only_test` verifies a client-side access rejection remains a failure after the listener passes. The CLI test covers the same option and export scope.

Local fixtures do not certify any deployed subnet, route or ACL. The following broader checks remain available when needed.

| Failure or path | Customer diagnostic and local regression coverage | Recovery acceptance and remaining boundary |
| --- | --- | --- |
| ORA-17868 / UnknownHostException | DNS-only lookup of the descriptor's actual HOST. `test_dns_failure_or_mismatch_blocks_tcp_tls_and_oracle` verifies name-not-found, temporary failure, deadline and empty answers without destination traffic. | Resolve the intended FQDN from the same catalog/default Compute, notebook, AI Compute or executor path; then verify connectivity and the original operation. Review the selected workspace network and its DNS source before retesting. |
| TNS alias or short hostname used as HOST | Reject underscore-bearing alias-like input before traffic, and warn on short hostnames. `test_alias_configuration_rejected_before_dns_and_short_names_warn`. | Validate actual HOST plus original JDBC alias/TNS_ADMIN/wallet and JVM lookup behavior. Explicit Python resolution does not validate JDBC configuration or its DNS cache. |
| Wrong confirmed DNS address set/count | Stop downstream traffic after expectation mismatch. `test_scan_missing_expected_mapping_blocks_connectivity` and `test_dns_count_mismatch_blocks_connectivity`. | Confirm the expectation applies to this runtime; leave unverified mappings unset. Backend and translated runtime answers can differ. |
| Intermittent DNS / different runtime paths | Repeat DNS without login, preserve failed samples and keep healthy comparison targets. `test_sampling_retains_failure_after_recovery`, `test_failed_target_does_not_hide_healthy_comparison_target`. | Retain UTC before/after evidence from the actual failing paths and verify workload recovery over an appropriate window. Sampled executors and a few attempts can miss faults. |
| TCP timeout, refusal, route failure or partial-address failure | Separate per-IP results; skip Oracle login when no tested address accepts TCP. `test_all_tcp_failures_skip_oracle_but_partial_paths_retain_failure`, `test_route_failure_is_separate_from_timeout_and_refusal`, loopback success/refusal tests. | Verify the listener, selected network, datasource route, rules and return path for the observed AIDP source. A timeout alone does not identify a missing rule. |
| TLS trust / identity / mTLS | Keep verification enabled and report TCP separately. `test_tls_verified_and_untrusted_ca`; Oracle stub tests verify configured client arguments and redaction. | Correct verified identity/trust/wallet issues. Raw TLS does not load an Oracle wallet; use a compatible client and the original operation. |
| ORA-12514 / ORA-12523 | Optional Oracle service connection after DNS and TCP. `test_collected_oracle_error_classes_remain_separate_after_network_passes`. | Database owner verifies service registration/instance readiness; repeat one controlled connection and the original connector/catalog operation. Socket success alone is insufficient. |
| ORA-01017 / ORA-28000 / ORA-28001 | Account classification; suppress subsequent sampled logins. `test_sampling_suppresses_repeated_failed_oracle_logins`. | Correct the configured credential reference/account state with the DBA before another login. Network success does not resolve an account error. |
| Generic DPY-6005 / total Oracle timeout | Preserve the generic code and compare earlier stages; do not assign an unsupported cause. Oracle classification and unclassified-error tests. | Compare the preceding network stages and actual client configuration. Retest the original operation; a generic error does not establish a private-network fault. |
| UDF, PostgreSQL, HTTPS/SDK or Object Storage dependencies | Use an exact `dependency` hostname/port/protocol for DNS and TCP/TLS; sample the relevant executor where supported. Ordering and comparison-target tests. | Retest the actual UDF, JDBC login/read, authenticated SDK call or bounded object read. Connect-only tests do not cover API authorization or sustained reads. |
| SCAN and supplied listeners | Resolve all configured names before sockets; test each confirmed address and explicit listener. `test_all_names_resolved_before_any_connectivity_or_login`, `test_scan_checks_every_confirmed_ip`, `test_scan_proxy_single_answer_supported`, `test_supplemental_listener_checked`. | One Oracle connection exercises one selected redirect. It does not verify every RAC instance/failover path; supplemental direct listeners can use another path. |
| Unavailable runtime or endpoint | Record the required runtime/endpoint as untested. No guessed configuration change is prescribed. | A probe from another available runtime cannot validate the inaccessible path. |

`test_dns_only_cli_reports_scope_and_rejects_combined_login` verifies DNS-only output cannot imply connectivity or enable login. `test_ip_literal_does_not_claim_hostname_resolution` verifies numeric targets explicitly skip DNS. The generated notebook uses the identical script, starts in DNS-only mode and retains the initial DNS report when connectivity is not requested.

`test_cli_exports_next_step_without_responsibility_split` verifies output and exported evidence provide one actionable next step without a responsibility split. The notebook summary test verifies results can be displayed again without new network or login probes.

For live acceptance, start in the affected notebook or AI Compute. Confirm that failed lookups lead to the applicable DNS record/view/forwarding checks and listener failures lead to datasource routing/rules and return-path checks. If an optional customer-network comparison is available, differing translated answers alone must not count as a fault; a successful comparison TCP connection must not substitute for validation of the AIDP source. Advanced DNS applies only to documented datasource cases. None of these outcomes is established by a local regression test.

Run the suite from the repository root:

```bash
python3 -m unittest discover -s shared-utils/private-network-diagnostics -p 'test_*.py' -v
```

Runtime reports, actual endpoint settings, customer identifiers and incident evidence belong in private operational storage, outside this public sample.
