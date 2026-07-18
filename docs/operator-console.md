# Local Reflex operator console

Start with `uv run gmail-agent`, or use `Setup-Gmail-Agent.cmd` once and then
`Start-Gmail-Agent.cmd` on Windows. The console binds to loopback only. Google credentials are
entered only in the attended Chrome window and are never copied into Reflex state or audit data.

## Run modes

- **Plan only** validates the typed policy and runs the four-specialist group chat. Google Admin is
  never opened and no approval control is enabled. Every configured specialist must respond in
  deterministic order with a typed verdict; clarification, unsafe, incomplete, and unattributed
  transcripts fail closed.
- **Dry run** performs the same review, opens headed Chrome, verifies the configured administrator
  and Workspace identities, reads the current Google policy, and presents the deterministic diff.
  The browser session is read-only.
- **Live** adds a short-lived server-owned approval envelope. The operator reviews the browser-
  backed before/after evidence, acknowledges it, and types the exact displayed phrase. Execution
  reopens Chrome, re-reads for drift, applies only the bound change, and independently reads back the
  complete expected state.

The mode selector is available in the top bar and Settings. Settings also persists the expected
administrator email, Workspace domain, group-chat/persona model, and vision-capable browser model.
Both model fields are installed-model dropdowns backed by Ollama's local catalog. Operators can
refresh that catalog or download an exact Ollama model tag; a downloaded model is added to both
menus but is not assigned to either role until the operator selects it and saves.
Changing modes, identities, models, policy scope, expressions, addresses, enabled state, or notice
content invalidates any unused approval immediately.
Draft, mode, and model controls are locked during an asynchronous agent review, Google read, or
approved write. A server-side revision check also discards late results if the draft changes.

## Operator flow

1. Use **Home** or **Ownership** to create a blocked-sender or Content compliance policy.
2. Enter the exact organizational-unit path. New Content compliance drafts default to inbound only;
   blank required fields keep Review disabled.
3. For blocked senders, enter domains/emails and optional approved-sender bypasses.
4. For Content compliance, select directions, any/all combination, typed expressions, optional
   address-list behavior, and optional envelope sender/recipient filters.
5. Review or randomize the plain-text rejection notice. The application first samples a complete
   persona brief — age, occupation, location, traits, goals, personality, and time period — from
   coherent application-owned pools, along with a current mood, one of the nine D&D alignments, and
   an alignment-compatible delivery style. Alignment dominates moral posture, authority,
   helpfulness, and finality, so surface style cannot flatten it. Occupation and goal pools are
   balanced away from recurring archival defaults, and unsupported archival identities introduced
   by the model are rejected. All nine alignments are eligible; only the immediately previous
   alignment is removed from the next random draw. The local model receives those exact fields,
   alignment-specific rhetorical moves, and required drafting effects, then verbalizes them into the
   role, voice, motif, and notice instead of inventing the identity from scratch.
   The policy category and internal policy ID remain structured application identity and are never
   included in that prompt. Generated drafts must pass a deterministic quality gate (no markup,
   escape artifacts, fabricated contact details, exposed category label, or sentence-like persona
   title; no stock blocked-sender formula) before they replace the visible draft. Creative refusal
   language is governed by the source-specific prompt contract rather than a fixed keyword list.
6. Select **Review plan** in plan-only mode or **Review and preview** in browser-backed modes.
7. Inspect the attributed group-chat messages and exact impact evidence. Dry-run ends here.
8. In live mode, acknowledge the evidence, type the exact phrase, and select **Approve & apply**.
9. Use **Runs** for session progress, **Audits** for verified terminal manifests, and **Ownership**
   to edit, enable/disable, or remove only exact managed resources.

Remove and enable/disable are focused confirmation flows: the full editor is hidden because those
operations intentionally change only the selected state. Editing an existing resource locks the
surface so a blocked-sender rule cannot accidentally become a Content compliance draft.
The focused flows still render the exact human-readable before/after impact and bound hashes. The
sidebar **New policy** action always resets to a clean create draft on the current surface.

## Current Google state

The **Ownership** page includes a *Current Google state* panel. In dry-run or live mode, the
**Read blocked senders** and **Read compliance rules** actions open the attended Chrome window with
Playwright, let the browser agent read the live Gmail policy configuration without writing, and
project the observed managed rules — with Edit, Enable/Disable, and Remove entry points into the
exact-approval flow — plus any unmanaged rule names, which stay read-only because they lack local
ownership evidence. Plan-only mode never opens Google, and the panel explains that instead of
failing silently. Each read is recorded in Runs and finalized as a no-change audit package.

## Appearance

The top bar includes a light/dark theme toggle. The selection persists locally in the browser and
every surface, including the policy editor, agent rail, and evidence panels, renders in both themes.

When the specialist group returns findings with a passing verdict, each specialist's findings are
listed under its message in the agent rail so the operator can see the reasoning, not only the
summary.

## Observability and failure behavior

Every browser-backed run records plan, preview, before state, expected after state, change set,
event chain, terminal result when available, and a digest manifest. The Audits page re-verifies the
event chain and artifact hashes on load. A failure before mutation is finalized as unchanged; an
exception after a commit sequence begins is finalized as indeterminate and must be reconciled.
Ownership snapshots are updated only after exact browser read-back verification.
Accepted specialist turns are saved in `agent-review.json`, bound to the typed plan hash and model
tag. Interrupted folders remain visible as indeterminate; verified packages can be opened or
exported as ZIP files directly from Audits.

The UI never claims mail-flow propagation from an Admin-console save. Separately authorized
mail-flow testing is required for that evidence level.
