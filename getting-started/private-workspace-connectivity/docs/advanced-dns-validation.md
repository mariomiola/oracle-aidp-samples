# Advanced DNS validation scope

V01–V04 demonstrate connectivity scenarios: S3, customer Network Source, cross-region Object Storage, and a service private endpoint. V05–V08 examine additional behavior needed to operate Advanced DNS. A working connection in V01–V04 does not establish that these other behaviors work.

| Case | Additional behavior | Customer value | Current notebook coverage |
| --- | --- | --- | --- |
| V05 | Automatic handling of eligible service endpoints | Reduce manual hostname entries and support requests. | Selected endpoint configuration and authenticated access; automatic inclusion needs the additional evidence below. |
| V06 | Self-service DNS CRUD, validation and cancellation | Maintain scoped configuration and reject invalid input. | Manual UI operations with supplied save/readback and validation observations. |
| V07 | Apply supported Advanced DNS edits without restarting existing Spark workloads | Change connectivity while workloads continue running. | DNS convergence, functional phases, application identity and independent lifecycle evidence; deployment prerequisites apply. |
| V08 | Preserve existing configuration and connectivity, including historical support-configured entries | Adopt self-service management without breaking existing integrations. | Existing database connection regression; legacy adoption needs the additional evidence below. |

These are test objectives, not statements that every deployment implements them. No notebook performs workspace edits or automatically certifies the complete requirement. Keep external evidence private and record the deployed capability/version with your run. The customer examples deliberately avoid internal implementation details and rollout controls.

## V05 — Endpoint coverage and automatic inclusion

**Why run it:** Verify selected service endpoints and distinguish endpoints that work through platform defaults from endpoints that need customer configuration. This complements the individual connectivity scenarios by examining coverage across actual resources.

**Current scope:** The notebook evaluates supplied configuration and authenticated-operation records for selected endpoints. Its checker does not determine whether an entry was automatically included. Configuration acceptance, a successful DNS lookup, or an authenticated read alone cannot prove automatic inclusion or removal of a support dependency.

**Additional manual acceptance for automatic inclusion:**

1. Select an actual eligible endpoint using the documentation and capabilities of your deployed service. Record its region, endpoint type and supported operation. Do not treat a list of service families as an eligibility guarantee.
2. Capture the complete original customer DNS/SCAN configuration and establish whether a historical support override exists. Use a clean, approved test workspace if that history cannot be established; do not remove an override on a shared workspace to manufacture a baseline.
3. Before adding any entry, run runtime DNS and an authenticated read/query. Preserve evidence that the target is eligible and that no manual entry or historical override supplied its connectivity. Automatic platform behavior need not appear as an editable UI row.
4. Separately test any endpoint requiring explicit configuration. Label that result **manual configuration**, rather than automatic inclusion. Preserve the original policies and routes.
5. Review eligibility, configuration history, DNS, operation and path evidence together. If history or eligibility is unavailable, report automatic inclusion as **INCOMPLETE**, even if endpoint coverage succeeds.

Private endpoints, cross-region endpoints, S3 and customer source-context requirements need their own eligibility assessment and can require explicit configuration. Testing one resource does not establish coverage of all resources in its DNS zone. Keep the extra observations in a private companion record; the existing checker remains a selected-endpoint coverage check.

## V06 — Self-service configuration lifecycle

**Why run it:** Demonstrate that users can maintain Advanced DNS entries, reopen saved configuration, reject malformed input and cancel an unsaved change. A successful data read does not validate those management operations.

**Current scope:** The notebook checks supplied add/edit/delete/cancel snapshots and input-validation observations. Exercise the supported workspace creation and editing flows separately where available. Keep unrelated DNS and SCAN entries intact. The current procedure tests Advanced DNS entries; preserving a SCAN snapshot is not a functional SCAN proxy test.

For each saved operation, capture both its operation result and reopened settings. For cancellation and rejected input, capture the unchanged baseline. Record duplicate/normalization and partial-provisioning behavior as observed; do not assume undocumented atomic rollback. For runtime effects, pair this case with V01–V04 or V07. Internal reconciliation, mapping ownership/cleanup, rollout flags and telemetry require separate engineering validation.

## V07 — Apply changes without a Spark restart

**Why run it:** Establish that supported Advanced DNS additions, updates and deletions reach an already-running workload without restarting or replacing its Spark application.

**Current scope:** Run before/add/edit/delete/restored phases against real DNS changes. Retain DNS/mapping evidence, authenticated workload results, Spark application identity and independent lifecycle records throughout. Continued `SELECT 1` alone does not prove that the new destination became usable; an unchanged application display name alone does not exclude a restart.

Verify deployment support before starting. General workspace network-edit documentation says most network changes require stopped compute; this test applies only to the Advanced DNS operation your deployment supports live. If the UI requires a stop, record **BLOCKED** and do not bypass it. Older deployments/clusters can have an adoption prerequisite: confirm any one-time restart with the administrator, record it separately, then begin a fresh measured run. A restart during the measured change sequence fails the no-restart objective. Subsequent adoption does not retroactively turn a failed run into a pass.

## V08 — Existing connectivity and legacy adoption

**Why run it:** Detect regressions when unrelated Advanced DNS entries change, while preserving an integration's destination, route and authentication. A legacy variant also checks that previously support-configured connectivity survives adoption of self-service management without another support request.

**Current scope:** The notebook repeats a read-only database identity query around unrelated DNS edits and compares saved configuration. It does not establish the historical origin of the original entry or perform a platform rollout/migration. A previously working database is not necessarily a support-configured database.

**Additional manual acceptance for legacy adoption:**

1. Identify a confirmed historical support-configured endpoint and privately reference the original change record/configuration. Capture a fresh functional baseline with the original authentication and network restrictions.
2. Have the administrator identify the actual supported adoption transition to be tested. Record deployment state, the existing configuration and any prerequisite lifecycle action. If no transition can be observed, label this a **connection regression** run only.
3. Capture configuration and repeat the original operation after adoption, before adding any new entries. Confirm no replacement allowlisting request or manual re-entry was needed.
4. Run the notebook's unrelated add/edit/delete/restored sequence, preserving original DNS/SCAN entries, target identity, routes and authentication.
5. Review original provenance, transition, retained settings and operation evidence. If provenance/transition evidence is missing, report legacy adoption as **INCOMPLETE** regardless of the general regression result.

Use a fresh authenticated connection/query as well as the existing client where practical, so a cached address or long-lived connection cannot conceal a broken mapping. These manual checks supplement the current evaluator; they do not change its automatic acceptance scope.

## Public product reference

The [AIDP workspace guide](https://docs.oracle.com/en/cloud/paas/ai-data-platform/aidug/workspaces.html) describes private networking, Advanced DNS and workspace-edit prerequisites. Verify the behavior available in your deployment before executing a case. See [network and IAM examples](network-and-iam-examples.md) for preparation templates.

Copyright (c) 2026 Oracle and/or its affiliates. Licensed under the [Universal Permissive License](../../../LICENSE.txt), Version 1.0.
