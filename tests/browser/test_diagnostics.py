"""Diagnostic sanitization tests."""

from compliance_agent.browser.diagnostics import sanitize_html


def test_sanitize_html_removes_scripts_hidden_values_tokens_and_email_local_parts() -> None:
    source = (
        '<script>window.token="secret"</script>'
        '<input type="hidden" value="secret" data-token="secret">'
        "<div>admin@example.com</div>"
    )

    sanitized = sanitize_html(source)

    assert "script" not in sanitized
    assert "secret" not in sanitized
    assert "a***@example.com" in sanitized
