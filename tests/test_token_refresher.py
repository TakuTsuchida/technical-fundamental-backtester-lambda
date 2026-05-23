from __future__ import annotations

import pytest


def test_handler_raises_not_implemented(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAIL_PARAM", "/my-service/jquants/mail")
    monkeypatch.setenv("PASSWORD_PARAM", "/my-service/jquants/password")
    monkeypatch.setenv("TOKEN_PARAM", "/my-service/jquants/id_token")

    from token_refresher.handler import handler

    with pytest.raises(NotImplementedError):
        handler({}, object())
