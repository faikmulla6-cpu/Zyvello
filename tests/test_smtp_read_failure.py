"""Inbox failures must reach the handler's sending hold, never look empty."""
from types import SimpleNamespace

import pytest

from harvey.integrations.smtp_mail import SmtpImapProvider


@pytest.mark.asyncio
async def test_imap_failure_propagates_to_handler(monkeypatch):
    provider = SmtpImapProvider(SimpleNamespace(), SimpleNamespace())

    def fail(limit):
        raise ConnectionError("mailbox unavailable")

    monkeypatch.setattr(provider, "_fetch_unseen", fail)
    with pytest.raises(ConnectionError, match="mailbox unavailable"):
        await provider.get_replies()


@pytest.mark.parametrize("select_status,search_status", [("NO", "OK"), ("OK", "NO")])
def test_imap_negative_response_is_not_an_empty_inbox(monkeypatch, select_status, search_status):
    class FakeImap:
        logged_out = False

        def login(self, *args):
            return "OK", []

        def select(self, *args):
            return select_status, []

        def search(self, *args):
            return search_status, [b""]

        def logout(self):
            self.logged_out = True

    connection = FakeImap()
    monkeypatch.setattr("harvey.integrations.smtp_mail.imaplib.IMAP4_SSL", lambda *args, **kwargs: connection)
    provider = SmtpImapProvider(SimpleNamespace(), SimpleNamespace())
    with pytest.raises(RuntimeError, match="IMAP inbox"):
        provider._fetch_unseen(50)
    assert connection.logged_out


@pytest.mark.asyncio
async def test_successful_empty_read_remains_empty(monkeypatch):
    provider = SmtpImapProvider(SimpleNamespace(), SimpleNamespace())
    monkeypatch.setattr(provider, "_fetch_unseen", lambda limit: [])
    assert await provider.get_replies() == []
