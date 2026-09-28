import json

import pytest

wx = pytest.importorskip("wx")


@pytest.fixture
def frame(tmp_path, monkeypatch):
    from quill_social.ui.app import SocialFrame
    monkeypatch.setenv("QUILLSOCIAL_DATA", str(tmp_path))
    application = wx.App()
    window = SocialFrame()
    yield window
    window._on_close(None)
    application.Destroy()


def test_automatic_announcements_are_new_only_and_honor_account_mute(frame, monkeypatch):
    from quill_social.model import SocialItem
    from quill_social.services.refresh import RefreshResult
    account = frame.store.list_accounts()[0]
    frame.a11y.account_options[account.account_id] = {
        "speech_timelines": ["home:all"], "speech_muted": False,
    }
    frame.a11y.notification_first_sentence = True
    spoken = []
    monkeypatch.setattr(frame.announcer, "say", lambda text, *args, **kwargs: spoken.append(text))
    item = SocialItem(account_id=account.account_id, remote_id="speech-new", author_display="Writer",
                      text="First sentence. Second sentence.")
    result = RefreshResult(items=[item], posts=1)
    frame._finish_refresh(result, True)
    assert any("New home post from Writer: First sentence. Second sentence." in text for text in spoken)
    spoken.clear()
    frame._finish_refresh(result, True)
    assert not any("from Writer:" in text for text in spoken)
    frame.a11y.account_options[account.account_id]["speech_muted"] = True
    frame._finish_refresh(RefreshResult(items=[SocialItem(account_id=account.account_id,
                                                        remote_id="speech-muted", author_display="Muted")]), True)
    assert not any("from Muted:" in text for text in spoken)


def test_home_export_unlimited_does_not_drop_last_item(frame):
    assert len(frame._scope_items("home:all", limit=-1)) == len(frame.store.list_items(limit=-1))


def test_startup_restores_account_timeline_and_post(frame):
    account = frame.store.list_accounts()[0]
    item = frame.store.list_items(account_id=account.account_id)[-1]
    (frame.data_dir / "reading-position.json").write_text(json.dumps({
        "account_id": account.account_id, "scope": "home:all", "item_id": item.item_id,
    }))
    frame._restore_reading_position()
    assert frame.selected_account_id == account.account_id
    assert frame._current_item().item_id == item.item_id


def test_cancelled_publish_keeps_draft(frame, monkeypatch):
    from quill_social.model import Draft
    monkeypatch.setattr(wx, "MessageBox", lambda *args, **kwargs: wx.NO)
    called = []
    monkeypatch.setattr(frame, "_publish_now", lambda draft: called.append(draft))
    draft = Draft(text="Keep this work", targets=[frame.store.list_accounts()[0].account_id])
    frame._handle_compose_result("publish", draft)
    assert not called
    assert frame.store.get_draft(draft.draft_id).text == draft.text


def test_single_link_setting_and_multiple_link_selection(frame, monkeypatch):
    from quill_social.ui import app as app_module
    item = frame._current_item()
    item.text = "https://example.org/one https://example.org/two"
    opened = []
    monkeypatch.setattr(app_module.webbrowser, "open", opened.append)
    class Choice:
        def __init__(self, *args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def ShowModal(self):
            return wx.ID_OK

        def GetSelection(self):
            return 1
    monkeypatch.setattr(wx, "SingleChoiceDialog", Choice)
    frame.cmd_open_links()
    assert opened == ["https://example.org/two"]
    item.text = "https://example.org/one"
    frame.cmd_open_links()
    assert opened[-1] == "https://example.org/one"
