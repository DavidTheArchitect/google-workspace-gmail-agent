# Threat model

## Protected assets

- Google Workspace mail policy and organization scope.
- Administrator authentication material and browser session data.
- Accurate ownership state and confirmation evidence.
- Audit confidentiality and integrity.
- The distinction between UI persistence and mail-flow enforcement.

## Trust boundaries

The user's request and all LLM output are untrusted. Admin console DOM and accessible names are
external input. Local ownership files are necessary but not sufficient evidence. A save toast is
only a hint. Only validated typed models, dual ownership evidence, exact identity/OU preconditions,
fresh read-back, and deterministic comparison can cross the mutation or success boundary.

## Principal threats and controls

| Threat | Control |
|---|---|
| Prompt injection or invalid model plan | JSON schema, Pydantic validation, fixed action mapping, bounded retries |
| Mutation of a manually managed rule | Visible prefix plus exact local ownership record; disagreement is read-only |
| Wrong administrator, Workspace, or OU | Independent positive identity checks before resolving a mutation control |
| UI redesign activates the wrong control | Explicit page states, scoped semantic contracts, exact-one matching, no positional selectors |
| Stale operator approval | Canonical plan/state/change hashes and immediate pre-write read-back |
| Save timeout causes duplicate writes | No blind write retry; reconcile actual and desired state first |
| Partial multi-resource creation | `partially_applied` result, orphan evidence, separately confirmed cleanup |
| LLM claims success | Machine-readable deterministic result is authoritative |
| Authentication leakage | Dedicated profile, no credential reads, sanitization, protected local audit directory |
| Concurrent runs | OS-backed exclusive run lock before browser launch |
| Audit tampering | Hash-chained events and artifact hashes in a manifest |

## Non-goals

Version 1 does not repair selectors autonomously, use private Admin console APIs, operate on child
OUs, modify unmanaged resources, run unattended writes, or claim immediate Gmail enforcement.
