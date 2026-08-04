from __future__ import annotations

from tsdp2.logging_config import _redact_sensitive_text, _register_secret


def test_registered_tokens_and_bot_urls_are_redacted():
    token = "123456:super-secret-token"
    _register_secret(token)
    message = (
        f"token={token} url=https://api.telegram.org/bot{token}/sendMessage"
    )
    redacted = _redact_sensitive_text(message)
    assert token not in redacted
    assert "super-secret-token" not in redacted
    assert "redacted" in redacted.lower()
