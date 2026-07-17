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

The attended production service acquires an exclusive browser-profile lock, loads ownership
evidence, creates protected audit evidence, and opens a headed persistent Chrome session. Expected
browser and I/O failures are mapped to closed preview, unchanged, drifted, or indeterminate
outcomes. The mutation-capable path refuses plan-only or dry-run settings and independently checks
the configured administrator and Workspace identities before every state read.

The Reflex operator console is a loopback-only imperative shell over the same services. Active live
approval envelopes stay server-side and in memory; terminal history comes from protected manifests;
verified ownership snapshots use atomic protected JSON. Browser sessions remain in the dedicated
Chrome profile and are never embedded into the console.

`RunMode` replaces ambiguous combinations of plan-only and dry-run booleans. Plan-only never opens
the browser; the dry-run service never issues a write permit. Live composition requires current
browser-backed hashes, a short-lived exact approval, and a pre-write drift check.

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

## Current-UI boundary

Google can change Admin console markup without notice. The local vision agent therefore receives a
fresh screenshot, bounded accessibility snapshot, and an application-generated catalog of opaque
candidate IDs. It can choose only one unique semantic candidate and application-owned input token;
rule commit controls additionally require both the approved OU and exact managed identity to be
visible; global address-list commits require their exact managed identity. Unknown read-only button
actions fail closed, and newly opened Admin tabs are adopted before extraction. Ambiguity aborts.
The browser model must advertise Ollama vision capability.

The Microsoft Agent Framework `GroupChatBuilder` remains the review pattern: four specialists,
eight bounded rounds by default, deterministic selection, shared prior turns, strict framework
author attribution, and pass/clarification/unsafe verdicts. Only a complete all-pass transcript is
accepted. The group has no browser tools, mutation tools, or approval authority.

Supervised disposable-resource acceptance is recommended for every tenant even though the attended
driver is built in.
