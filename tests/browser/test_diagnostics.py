"""Diagnostic sanitization tests."""

from compliance_agent.browser.diagnostics import sanitize_html, sanitize_url


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


def test_sanitize_html_neutralizes_active_content_and_sensitive_urls() -> None:
    source = (
        '<meta http-equiv="refresh" content="0;url=https://evil.example">'
        '<iframe src="https://evil.example"><div>hidden</div></iframe>'
        '<img src="javascript:alert(1)" onerror="alert(2)">'
        '<a href="data:text/html,unsafe">unsafe</a>'
    )

    sanitized = sanitize_html(source)

    assert "iframe" not in sanitized
    assert "onerror" not in sanitized
    assert "javascript:" not in sanitized
    assert "data:text" not in sanitized
    assert "default-src 'none'" in sanitized


def test_sanitize_url_removes_query_fragment_and_redacts_identity() -> None:
    sanitized = sanitize_url(
        "https://admin.google.com/ac/apps/gmail/admin@example.com?token=secret#fragment"
    )

    assert sanitized == "https://admin.google.com/ac/apps/gmail/a***@example.com"
