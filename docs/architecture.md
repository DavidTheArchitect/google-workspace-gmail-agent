# Architecture

The system is an imperative shell around a deterministic functional core.

```text
CLI / Agent Framework workflow / Playwright / Ollama
                         |
                 application services
                         |
             typed schemas and domain policies
```

Dependencies point inward. `schemas` and `domain` must not import Playwright, OpenAI/Ollama,
Agent Framework, command-line packages, environment readers, or concrete persistence adapters.

The production composition root acquires the exclusive run lock before loading ownership evidence,
creates the protected audit run, collects runtime manifest facts, and wraps only externally supplied
adapters that have passed the supervised UI-contract gate. Expected Playwright and I/O failures are
mapped to closed preflight, read, or uncertain-mutation outcomes before entering the graph. The
mutation-capable composition refuses plan-only or dry-run settings and independently rechecks the
configured administrator and Workspace identities before any state read.

Gemma converts language into a `TaskPlan`. It cannot select a browser locator, choose a mutation
handler, establish ownership, authorize a change, determine whether a save succeeded, or construct
the authoritative status. Direct commands construct the same `TaskPlan` without a model.

The implemented workflow is fixed:

```text
plan -> validate -> preflight -> read -> resolve ownership -> desired state -> diff
     -> human confirmation -> re-read -> drift check -> mutate -> fresh read-back
     -> verify -> audit -> deterministic report
```

Clarification, manual login, and mutation confirmation are the only human-in-the-loop pauses.
Every edge carries typed data. An approval contains the plan hash, before-state hash, and
change-set hash; a stale approval cannot authorize a different state.

Uncertain writes enter a separate reconciliation branch. Read-back may prove the desired state is
present, prove the before-state is unchanged, identify a partial operation, or remain
indeterminate. Only the unchanged case can retry, and only once while all original preconditions
remain valid.

## Implementation gate

The deterministic core and browser safety contracts can be implemented from the specification.
Actual Admin console read and write locator candidates cannot. They require sanitized evidence
from the current UI, fixture tests, a read-only live check, and a supervised disposable-resource
write test. Until that gate is complete, the CLI remains planning/readiness-only and fails closed.
