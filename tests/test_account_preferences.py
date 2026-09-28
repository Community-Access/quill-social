"""Account management uses temporary data and in-memory credentials only."""

from types import SimpleNamespace

import pytest

wx = pytest.importorskip("wx")

from quill_social.model import Account, PublicationPlan, SocialItem  # noqa: E402
from quill_social.security.credentials import InMemoryCredentialStore  # noqa: E402
from quill_social.ui.account_preferences import AccountPreferencesDialog  # noqa: E402


@pytest.fixture
def dialog(store):
    application = wx.App()
    first = store.put_account(Account(account_id="first", handle="first", is_default=True))
    second = store.put_account(Account(account_id="second", handle="second"))
    credentials = InMemoryCredentialStore()
    credentials.store("mock", "first", "fake first token")
    credentials.store("mock", "second", "fake second token")
    frame = SimpleNamespace(store=store, credentials=credentials, _refresh_running=False)
    dlg = AccountPreferencesDialog(None, frame)
    yield dlg, first, second
    dlg.Destroy()
    application.Destroy()


def test_alias_default_and_pause_persist_without_changing_other_account(dialog):
    dlg, first, second = dialog
    dlg.accounts.SetSelection(1)
    dlg._on_select()
    dlg.alias.SetValue("Personal")
    dlg.default.SetValue(True)
    dlg.paused.SetValue(True)
    dlg._on_save()
    updated = dlg.frame.store.get_account(second.account_id)
    assert updated.local_alias == "Personal"
    assert updated.is_default and updated.paused
    assert not dlg.frame.store.get_account(first.account_id).is_default
    assert dlg.frame.store.get_account(first.account_id).handle == "first"
    assert dlg.changed


def test_remove_only_selected_account_credentials_cache_and_pending_plans(dialog, monkeypatch):
    dlg, first, second = dialog
    store = dlg.frame.store
    for account in (first, second):
        store.upsert_item(SocialItem(account_id=account.account_id, remote_id="post"))
        store.put_plan(PublicationPlan(account_id=account.account_id, state="scheduled"))
    monkeypatch.setattr(wx, "MessageBox", lambda *args: wx.YES)
    dlg._on_remove()
    assert store.get_account("first") is None
    assert store.get_account("second") is not None
    assert dlg.frame.credentials.reference("mock", "first") is None
    assert dlg.frame.credentials.reference("mock", "second") is not None
    assert not store.list_items(account_id="first")
    assert store.list_items(account_id="second")
    assert {p.account_id: p.state for p in store.list_plans()} == {
        "first": "cancelled", "second": "scheduled"}
    assert dlg.changed


@pytest.mark.parametrize("condition", ["declined", "refresh", "credential_error"])
def test_remove_keeps_account_when_not_confirmed_or_not_safe(dialog, monkeypatch, condition):
    dlg, _, _ = dialog
    monkeypatch.setattr(wx, "MessageBox", lambda *args: wx.NO if condition == "declined" else wx.YES)
    if condition == "refresh":
        dlg.frame._refresh_running = True
    if condition == "credential_error":
        monkeypatch.setattr(dlg.frame.credentials, "delete", lambda *args: None)
    dlg._on_remove()
    assert len(dlg.frame.store.list_accounts()) == 2
    assert not dlg.changed
