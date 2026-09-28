from types import SimpleNamespace

import pytest

wx = pytest.importorskip("wx")

from quill_social.model import Account  # noqa: E402
from quill_social.ui import timelines  # noqa: E402


@pytest.fixture
def app():
    application = wx.App()
    yield application
    application.Destroy()


def test_list_chooser_and_optional_announcements(app):
    account = Account(handle="example")
    dialog = timelines.TimelineDialog(None, [account], kind="list")
    try:
        assert dialog.parameters() == dict(account_id=account.account_id, kind="list",
                                           value="", label="", announce=False)
        dialog.kind.SetSelection(dialog.kinds.index("hashtag"))
        dialog._kind_changed()
        with pytest.raises(ValueError):
            dialog.parameters()
        dialog.value.SetValue("accessibility")
        assert dialog.parameters()["value"] == "accessibility"
        dialog.kind.SetSelection(dialog.kinds.index("local"))
        dialog._kind_changed()
        assert dialog.parameters()["value"] == ""
    finally:
        dialog.Destroy()


def test_recovered_search_dialog_collects_text_and_result_type(app):
    dialog = timelines.SearchDialog(None)
    try:
        with pytest.raises(ValueError, match="Enter text"):
            dialog.parameters()
        dialog.query.SetValue("accessibility")
        dialog.search_by.SetSelection(2)
        assert dialog.parameters() == ("accessibility", "accounts", "Users")
    finally:
        dialog.Destroy()


def test_direct_message_failure_preserves_edits_and_retry(app, monkeypatch):
    calls = []

    def send(recipient, text):
        calls.append((recipient, text))
        if len(calls) == 1:
            raise RuntimeError("Connection failed")
        return "message-id"

    class ImmediateThread:
        def __init__(self, *, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(timelines, "Thread", ImmediateThread)
    monkeypatch.setattr(wx, "CallAfter", lambda callback, *args: callback(*args))
    dialog = timelines.DirectMessageDialog(None, lambda: SimpleNamespace(send_direct_message=send))
    endings = []
    dialog.EndModal = endings.append
    try:
        dialog.recipient.SetValue("person@example.org")
        dialog.text.SetValue("Private hello")
        dialog._send(None)
        assert dialog.text.GetValue() == "Private hello"
        assert dialog.recipient.GetValue() == "person@example.org"
        assert dialog.send.IsEnabled()
        assert endings == []
        dialog._send(None)
        assert endings == [wx.ID_OK]
        assert dialog.result_item == "message-id"
        assert calls == [("person@example.org", "Private hello")] * 2
    finally:
        dialog.Destroy()
