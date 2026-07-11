# Selector repair procedure

An unknown page state, missing locator, or duplicate locator is a terminal fail-closed result for
that run.

1. Preserve the trace and sanitized diagnostic package.
2. Inspect the current UI manually in read-only mode.
3. Record the expected page state, container, role, accessible name, and match count.
4. Add sanitized HTML and accessibility fixtures without cookies, tokens, hidden fields, unrelated
   user data, or authentication screens.
5. Update an explicit locator contract; never add a "first match" fallback.
6. Test missing, duplicate, renamed, wrapped, and delayed variants.
7. Complete a read-only live test.
8. Complete a supervised disposable-resource write test with explicit confirmation.

An LLM may suggest a candidate repair from sanitized data, but the suggestion is never applied or
executed automatically.
