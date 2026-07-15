# Operator console design system

The console is server-rendered first. JavaScript may enhance navigation, tables, forms, SSE, and notifications, but every form retains a real `method` and `action`, every filter retains a link, and security never depends on browser code.

## Tokens and themes

| Token group | Examples | Purpose |
| --- | --- | --- |
| Surfaces | `--bg`, `--bg-raised`, `--surface`, `--hover` | Page, card, and interaction backgrounds |
| Content | `--text`, `--heading`, `--muted`, `--nav-text` | Semantic text hierarchy |
| Boundaries | `--line`, `--input-border`, `--focus-ring` | Separation, fields, and keyboard focus |
| Intent | `--accent`, `--success`, `--warning`, `--danger` plus `*-soft` | Action and status meaning |
| Shape/elevation | `--radius`, `--radius-sm`, `--shadow` | Shared component geometry |

The default token set is light. `prefers-color-scheme: dark` supplies automatic dark values; `html[data-theme="light|dark"]` overrides that preference. `theme.js` applies the stored choice before first paint and adds the `js` class used for progressive layouts such as fixed toasts.

## Components

Buttons (`primary`, `secondary`, `quiet`, `danger-button`), alerts/callouts, chips and state labels, form fields and `.field-error`, data tables/toolbars, empty states, toasts, setup steps, workflow/timeline rails, and approval controls share the semantic tokens above. Empty table states must use `empty_state()` from `partials/_macros.html`.

## Enhancement contracts

- `data-enhance` opts a server-rendered table into search and pagination. `data-page-size` controls the client page size.
- A sortable header uses `data-sort="text|num|date"`; cells should provide canonical `data-value`. ISO-8601 timestamp values sort lexically.
- A filter group uses `data-filter-attr`, `data-filter-target`, and buttons with `data-filter-value`; target rows expose the corresponding `data-*` value.
- htmx forms always retain `method`/`action`. Validation responses replace the form at 400/422 and focus the first `aria-invalid="true"` field.
- SSE uses only the fixed `phase`, `settled`, and `gone` events. The server fragment remains the authoritative run projection.

## Accessibility

Pages have a skip link and one main landmark. Visible labels are required for fields; errors use `aria-invalid` and `aria-describedby`. Status announcements use polite live regions unless an immediate error requires `role="alert"`. Icon-only controls require accessible names. Keyboard focus uses the shared focus ring. Motion is disabled under `prefers-reduced-motion`, including active timeline pulses and toast transitions. Truncated hashes can be revealed, and clipboard failure automatically exposes the full value.

## CSS section map

`styles.css` is labeled in seven layers: Tokens, Base, Layout shell, Components, Page-specific, Responsive, and Motion & a11y. Add new selectors to the narrowest applicable layer; do not encode request data or security state in CSS or JavaScript.
