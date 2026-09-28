from types import SimpleNamespace

import pytest

from quill_social.adapters.base import AdapterError, NotificationEvent
from quill_social.services.notifications import NotificationPolicy, save_policy
from quill_social.services.refresh import RefreshResult

wx = pytest.importorskip("wx")


@pytest.fixture
def frame(tmp_path, monkeypatch):
    from quill_social.ui.app import SocialFrame
    monkeypatch.setenv("QUILLSOCIAL_DATA", str(tmp_path))
    app = wx.App()
    frame = SocialFrame()
    frame.a11y.provide_confirmations = False
    yield frame
    frame._on_close(None)
    app.Destroy()


@pytest.mark.parametrize("column,command,positive,negative", [
    ("favourited", "cmd_favourite", "Favourited.", "Favourite removed."),
    ("reblogged", "cmd_repost", "Reposted.", "Repost removed."),
    ("bookmarked", "cmd_bookmark", "Bookmarked.", "Bookmark removed."),
])
def test_action_success_and_removal_are_explicit(frame, monkeypatch, column, command, positive, negative):
    item = frame._current_item()
    setattr(item, column, False)
    frame.store.set_flag(item.item_id, column, False)
    spoken = []
    monkeypatch.setattr(frame.announcer, "say", lambda text, level, **kw: spoken.append((text, level)))
    getattr(frame, command)()
    assert spoken[-1] == (positive, "action")
    assert getattr(frame.store.get_item(item.item_id), column)
    getattr(frame, command)()
    assert spoken[-1] == (negative, "action")
    assert not getattr(frame.store.get_item(item.item_id), column)


def test_failed_reaction_does_not_change_state_or_claim_success(frame, monkeypatch):
    from quill_social.ui import app as module
    item = frame._current_item()
    item.favourited = False
    frame.store.set_flag(item.item_id, "favourited", False)
    def fail(*args):
        raise AdapterError("Server denied this action", kind="permission")
    monkeypatch.setattr(module, "adapter_for", lambda *args: SimpleNamespace(set_favourite=fail))
    spoken, errors = [], []
    monkeypatch.setattr(frame.announcer, "say", lambda *args, **kw: spoken.append(args))
    monkeypatch.setattr(frame.announcer, "error", errors.append)
    frame.cmd_favourite()
    assert not item.favourited
    assert not frame.store.get_item(item.item_id).favourited
    assert not spoken
    assert "Server denied" in errors[-1]


def test_distinct_notifications_for_cached_post_and_follow_are_read_once(frame, monkeypatch):
    item = frame._current_item()
    frame.a11y.account_options[item.account_id] = {"speech_timelines": ["attention:notifications"]}
    events = [NotificationEvent("like-1", "favourite", "Ada", item, item.account_id),
              NotificationEvent("boost-1", "reblog", "Ben", item, item.account_id),
              NotificationEvent("follow-1", "follow", "Cat", None, item.account_id)]
    result = RefreshResult(items=[item], events=events, notification_keys={(item.account_id, item.remote_id)})
    spoken = []
    monkeypatch.setattr(frame.announcer, "say", lambda text, level, **kw: spoken.append((text, level)))
    frame._finish_refresh(result, False, True)
    automatic = [text for text, level in spoken if level == "automatic"]
    assert len(automatic) == 1
    assert "Ada favourited" in automatic[0]
    assert "Ben boosted" in automatic[0]
    assert "Cat: followed you" in automatic[0]
    spoken.clear()
    frame._finish_refresh(result, False, True)
    assert not [text for text, level in spoken if level == "automatic"]


def test_first_load_seeds_notifications_without_reading_history(frame, monkeypatch):
    item = frame._current_item()
    frame._announcement_ready.clear()
    frame.a11y.account_options[item.account_id] = {"speech_timelines": ["attention:notifications"]}
    result = RefreshResult(events=[NotificationEvent("old", "follow", "Old follower", None, item.account_id)],
                           successful_accounts={item.account_id})
    spoken = []
    monkeypatch.setattr(frame.announcer, "say", lambda text, level, **kw: spoken.append((text, level)))
    frame._finish_refresh(result, False, True)
    assert not [text for text, level in spoken if level == "automatic"]
    result.events.append(NotificationEvent("new", "follow", "New follower", None, item.account_id))
    frame._finish_refresh(result, False, True)
    automatic = [text for text, level in spoken if level == "automatic"]
    assert len(automatic) == 1 and "New follower" in automatic[0] and "Old follower" not in automatic[0]


def test_notification_speech_policy_is_applied(frame, monkeypatch):
    item = frame._current_item()
    frame.a11y.account_options[item.account_id] = {
        "speech_timelines": ["attention:notifications"]}
    frame._announcement_ready.add(item.account_id)
    save_policy(frame.store, NotificationPolicy(
        account_id=item.account_id, category="favourite", speak=False))
    result = RefreshResult(events=[NotificationEvent(
        "silent-like", "favourite", "Ada", item, item.account_id)])
    spoken = []
    monkeypatch.setattr(
        frame.announcer, "say", lambda text, level, **kw: spoken.append((text, level)))
    frame._announce_updates(result, [], enabled=True)
    assert not spoken


def test_poll_only_runs_for_enabled_accounts_and_never_overlaps(frame, monkeypatch):
    calls = []
    monkeypatch.setattr(frame, "_refresh_from_network", lambda **kw: calls.append(kw))
    frame._poll_announcements()
    assert not calls
    account = frame.store.list_accounts()[0]
    frame.a11y.account_options[account.account_id] = {"speech_timelines": ["home:all"]}
    frame._poll_announcements()
    assert calls == [{"announce": False, "automatic": True}]
    frame._refresh_running = True
    frame._poll_announcements()
    assert len(calls) == 1


@pytest.mark.parametrize("account_specific", [False, True])
def test_saved_quiet_hours_suppress_notification_speech(frame, monkeypatch, account_specific):
    from datetime import datetime

    from quill_social.ui.manage import save_quiet_hours
    item = frame._current_item()
    frame.a11y.account_options[item.account_id] = {"speech_timelines": ["attention:notifications"]}
    frame._announcement_ready.add(item.account_id)
    current = datetime.now()
    minute = current.hour * 60 + current.minute
    save_quiet_hours(frame.store, item.account_id if account_specific else "", minute, (minute+2)%1440)
    spoken = []
    monkeypatch.setattr(frame.announcer, "say", lambda *args, **kw: spoken.append(args))
    result = RefreshResult(events=[NotificationEvent("quiet", "favourite", "Ada", item, item.account_id)])
    frame._announce_updates(result, [], enabled=True)
    assert not spoken
    assert "quiet" in frame._seen_notification_ids[item.account_id]
