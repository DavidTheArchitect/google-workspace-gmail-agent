# Reflex operator console design system

The accepted source of truth is `docs/design/reflex-operator-console.png` at 1536×1024.

## Visual tokens

- Canvas: true white `#ffffff`; navigation rail: cool gray `#f7f9fc`.
- Ink: `#111318`; muted text: `#596273`; borders: `#d9e0e9`.
- Primary cobalt: `#0b57d0`; selected background: `#dbe9ff`.
- Success: `#137333`; warning: `#b06000`; danger: `#b3261e`.
- Radius: 8px controls and panels; avoid nested cards and decorative pills.
- Shadow: none by default; use a single subtle elevation only for dialogs.
- Spacing scale: 4, 8, 12, 16, 24, 32, 48px.
- Typography: Inter/Geist-style system sans, 14px controls, 16px body, 20px section
  headings, 32px page title, deliberate weights and line heights.

## Layout and components

- Desktop shell: 206px navigation rail, flexible work area, 334px collaboration rail.
- Mobile: navigation becomes a compact header and the collaboration rail follows the form.
- Use open sections, hairline dividers, tables, and vertical rails instead of card grids.
- Primary controls are cobalt with white text. Secondary controls use a white surface and border.
- Agent messages share a thin cobalt progress line and circular role icons.
- Status text is functional only: local model, approval, and cloud-processing state.

## Allowed first-viewport copy

`Gmail Policy Agent`, `Create Gmail policy`, `Blocked senders`, `Content compliance`,
`Organizational unit`, `Email messages to affect`, `Inbound`, `Outbound`,
`Internal-Sending`, `Internal-Receiving`, `Match ANY`, `Agent group`, `Rejection notice`,
`Review change`, `Local · Gemma 4 12B`, `One approval required`, and
`No cloud processing`.

## Required interactions

- Toggle standard versus compliance policy authoring.
- Add/remove expressions and select all four mail directions.
- Generate and review a persona-backed rejection notice.
- Stream the four-agent collaboration transcript.
- Review a deterministic before/after change set and issue one exact approval.
- Navigate to runs, ownership, audits, settings, exact diff, and audit evidence.
