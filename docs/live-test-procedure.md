# Supervised live-test procedure

Live tests are excluded from routine CI. They require an attended headed browser, the configured
administrator, the expected Workspace, root OU selection, and disposable application-owned names
of the form `[Compliance Agent] zz-test-<run-id>`.

## Read-only acceptance gate

- Acquire the run lock and launch the dedicated profile.
- Confirm the Admin console host, administrator identity, Workspace identity, Gmail settings
  context, **Spam, Phishing and Malware** context, **Blocked senders** section, and root OU.
- Capture sanitized HTML and accessibility fixtures for the relevant page states.
- Read rules, lists, and list bindings without activating any mutation control.
- Confirm ambiguous and unknown states abort with diagnostics.

## Write acceptance gate

After fixture review, perform one explicitly confirmed operation at a time: create list, create
rule, fresh-read verify, add entry, verify, change notice, verify affected count, remove entry,
verify, remove rule, remove unreferenced owned list, and verify cleanup.

Simulate an ambiguous locator and a post-save timeout. The first must abort; the second must
reconcile by read-back without blindly clicking Save again. Inspect the Admin audit log and the
local audit package. Treat read-back as UI persistence only; allow for Google's documented
propagation window before a separately authorized mail-flow test.
