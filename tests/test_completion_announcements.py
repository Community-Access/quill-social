from types import SimpleNamespace

import pytest

from quill_social.adapters.base import AdapterError, PublishResult
from quill_social.model import Draft, PublicationPlan
from quill_social.services.scheduler import StepResult

wx = pytest.importorskip("wx")


@pytest.fixture
def frame(tmp_path, monkeypatch):
    from quill_social.ui.app import SocialFrame
    monkeypatch.setenv("QUILLSOCIAL_DATA", str(tmp_path))
    app = wx.App()
    window = SocialFrame()
    window.spoken = []
    window.errors = []
    monkeypatch.setattr(window.announcer, "say", lambda text, *a, **kw: window.spoken.append(text))
    monkeypatch.setattr(window.announcer, "error", window.errors.append)
    yield window
    window._on_close(None)
    app.Destroy()


@pytest.mark.parametrize("fail", [False, True])
def test_thread_progress_and_final_outcome(frame, monkeypatch, fail):
    from quill_social.ui import app as module
    pending = []
    monkeypatch.setattr(module, "Thread", lambda target, **kw: SimpleNamespace(start=lambda: pending.append(target)))
    monkeypatch.setattr(wx, "CallAfter", lambda fn, *args: fn(*args))
    monkeypatch.setattr(frame, "_refresh_from_network", lambda **kw: None)
    monkeypatch.setattr(module, "split_thread", lambda *a, **kw: SimpleNamespace(texts=lambda: ["one", "two"]))
    sent = []
    def publish(request):
        if fail and sent:
            raise AdapterError("Server refused second part")
        sent.append(request.text)
        return PublishResult(str(len(sent)))
    monkeypatch.setattr(module, "adapter_for", lambda *args: SimpleNamespace(publish=publish))
    frame._publish_now(Draft(text="A thread", targets=[frame.store.list_accounts()[0].account_id], thread_mode=True))
    assert frame._publishing
    assert not sent
    pending.pop()()
    assert not frame._publishing
    assert any("Thread part 1 of 2 sent" in text for text in frame.spoken)
    if fail:
        assert sent == ["one"]
        assert "Published 1 of 2" in frame.errors[-1]
        assert "Server refused" in frame.errors[-1]
        assert not any("Thread sent." in text for text in frame.spoken)
    else:
        assert sent == ["one", "two"]
        assert "Thread sent. 2 parts." in frame.spoken[-1]


def test_scheduler_announces_retry_and_failure(frame, monkeypatch):
    account = frame.store.list_accounts()[0]
    plan = PublicationPlan(account_id=account.account_id, next_retry_at=1000)
    results = [StepResult(plan, False, False, True, "Retry in 30s."),
               StepResult(plan, False, True, False, "Permission denied.")]
    frame._finish_scheduled(results)
    assert "Queued to retry" in frame.errors[0]
    assert "Permission denied" in frame.errors[1]


def test_link_success_and_failure(frame, monkeypatch):
    from quill_social.ui import app as module
    monkeypatch.setattr(module.webbrowser, "open", lambda url: True)
    frame._open_announced_link("https://example.org")
    assert frame.spoken[-1] == "Opened link."
    monkeypatch.setattr(module.webbrowser, "open", lambda url: False)
    frame._open_announced_link("https://example.org")
    assert "Could not open link" in frame.errors[-1]


def test_profile_success_announced_but_failure_not(frame, monkeypatch):
    from quill_social.ui.profile import ProfileDialog
    monkeypatch.setattr(ProfileDialog, "_run", lambda *args: None)
    dialog = ProfileDialog(frame, lambda: None)
    monkeypatch.setattr(dialog, "EndModal", lambda result: None)
    try:
        dialog._saved(None, None)
        assert frame.spoken == ["Profile updated."]
        dialog._saved(None, "Rejected")
        assert frame.spoken == ["Profile updated."]
    finally:
        dialog.Destroy()


@pytest.mark.parametrize("failure", [False, True])
def test_immediate_delivery_preserves_composition_on_failure(frame, monkeypatch, failure):
    from quill_social.model import Media, Poll, PollOption, now_ms
    from quill_social.ui import app as module
    pending = []
    monkeypatch.setattr(module, "Thread", lambda target, **kw: SimpleNamespace(start=lambda: pending.append(target)))
    monkeypatch.setattr(wx, "CallAfter", lambda fn, *args: fn(*args))
    monkeypatch.setattr(frame, "_refresh_from_network", lambda **kw: None)
    requests = []
    def publish(request):
        requests.append(request)
        if failure:
            raise AdapterError("Offline")
        return PublishResult("posted")
    monkeypatch.setattr(module, "adapter_for", lambda *args: SimpleNamespace(publish=publish))
    account = frame.store.list_accounts()[0]
    draft = Draft(text="Content", targets=[account.account_id],
                  media=[Media(local_path="photo.jpg", alt_text="Bird")],
                  poll=Poll(options=[PollOption(title="Yes"), PollOption(title="No")], expires_at=now_ms()+3600000))
    frame._publish_now(draft)
    assert frame.store.get_draft(draft.draft_id) is not None
    assert not requests
    pending.pop()()
    assert requests[0].media == draft.media
    assert requests[0].poll == draft.poll
    saved = frame.store.get_draft(draft.draft_id)
    if failure:
        assert saved.media == draft.media and saved.poll == draft.poll
        assert "Offline" in frame.errors[-1]
    else:
        assert saved is None


def test_scheduler_worker_uses_detached_data_and_persists_on_ui_thread(frame, monkeypatch):
    from quill_social.ui import app as module
    account = frame.store.list_accounts()[0]
    draft = Draft(text="scheduled", targets=[account.account_id])
    frame.store.put_draft(draft)
    plan = PublicationPlan(draft_id=draft.draft_id, account_id=account.account_id, state="queued")
    frame.store.put_plan(plan)
    workers, callbacks = [], []
    monkeypatch.setattr(module, "Thread", lambda target, **kw: SimpleNamespace(start=lambda: workers.append(target)))
    monkeypatch.setattr(wx, "CallAfter", lambda fn, *args: callbacks.append((fn, args)))
    monkeypatch.setattr(module, "adapter_for", lambda *args: SimpleNamespace(publish=lambda request: PublishResult("sent")))
    monkeypatch.setattr(frame, "_refresh_from_network", lambda **kw: None)
    frame._on_sched_tick(None)
    frame._on_sched_tick(None)
    assert len(workers) == 1
    with monkeypatch.context() as guard:
        for name in ("get_account", "get_draft", "put_plan", "due_plans"):
            guard.setattr(frame.store, name, lambda *a, **kw: pytest.fail("Worker accessed UI database"))
        workers.pop()()
    assert frame.store.get_plan(plan.plan_id).state == "queued"
    callback, args = callbacks.pop()
    callback(*args)
    assert frame.store.get_plan(plan.plan_id).state == "published"
    assert not frame._scheduler_running


def test_live_conversation_fetch_does_not_replace_new_scope(frame, monkeypatch):
    from quill_social.model import SocialItem
    from quill_social.ui import app as module
    original = frame._items[0]
    frame.list.Select(0)
    frame.list.Focus(0)
    posts = [original, SocialItem(account_id=original.account_id, text="New reply", remote_id="reply")]
    workers = []
    monkeypatch.setattr(module, "Thread", lambda target, **kw: SimpleNamespace(start=lambda: workers.append(target)))
    monkeypatch.setattr(wx, "CallAfter", lambda fn, *args: fn(*args))
    monkeypatch.setattr(module, "adapter_for", lambda *args: SimpleNamespace(thread=lambda item: posts))
    frame.cmd_open_conversation()
    workers.pop()()
    assert any(post.text == "New reply" for post in frame._items)
    assert frame.store.get_item(posts[1].item_id) is not None
    frame.list.Select(0)
    frame.list.Focus(0)
    frame.cmd_open_conversation()
    frame._load_scope("library:bookmarks", "Bookmarks")
    before = list(frame._items)
    workers.pop()()
    assert frame._items == before


@pytest.mark.parametrize("finish_method", ["_apply_refresh", "_finish_refresh"])
def test_home_membership_excludes_notification_and_conversation_cache(frame, monkeypatch, finish_method):
    from quill_social.adapters.base import NotificationEvent
    from quill_social.db import SocialStore
    from quill_social.model import Account, SocialItem
    from quill_social.services import notifications
    from quill_social.services.refresh import RefreshResult
    account = Account(account_id="live", network="mastodon", handle="me")
    frame.store.put_account(account)
    frame._populate_accounts()
    frame._select_account(account.account_id, announce=False)
    # Old caches have no provenance. Do not treat every cached post as Home.
    legacy = SocialItem(network="mastodon", account_id="live", remote_id="legacy", text="Old notification")
    frame.store.upsert_item(legacy)
    assert frame._scope_items("home:all") == []
    home = SocialItem(network="mastodon", account_id="live", remote_id="home", text="@me A genuine home post")
    mention = SocialItem(network="mastodon", account_id="live", remote_id="mention", text="@me A notification-only mention")
    liked = SocialItem(network="mastodon", account_id="live", remote_id="liked", text="Notification subject")
    events = [NotificationEvent("mention-event", "mention", "Ada", mention, "live"),
              NotificationEvent("like-event", "favourite", "Ada", liked, "live")]
    result = RefreshResult(items=[home, mention, liked], home_keys={("live", "home")},
        notification_keys={("live", "mention"), ("live", "liked")}, events=events,
        notifications=[notifications.from_event(event, "mastodon") for event in events])
    getattr(frame, finish_method)(result, announce=False)
    assert [post.remote_id for post in frame._scope_items("home:all")] == ["home"]
    frame.store.set_read(home.item_id, False)
    assert [post.remote_id for post in frame._scope_items("home:unread")] == ["home"]
    assert "mention" in [post.remote_id for post in frame._scope_items("attention:mentions")]
    assert "liked" in [post.remote_id for post in frame._scope_items("attention:notifications")]
    # A later notification for a previously fetched Home post must not demote it.
    getattr(frame, finish_method)(RefreshResult(items=[home], notification_keys={("live", "home")}), announce=False)
    assert [post.remote_id for post in frame._scope_items("home:all")] == ["home"]
    # Conversation replies are cached but remain outside Home until returned by its endpoint.
    reply = SocialItem(network="mastodon", account_id="live", remote_id="reply", text="Conversation reply")
    token = object()
    frame._conversation_request = token
    frame._finish_conversation(token, frame.current_scope, "live", [home, reply], None)
    assert "reply" not in [post.remote_id for post in frame._scope_items("home:all")]
    # Provenance survives restart and protects the initial cached view.
    reopened = SocialStore(frame.store.path)
    try:
        assert [post.remote_id for post in reopened.list_items(account_id="live", home_only=True)] == ["home"]
    finally:
        reopened.close()
    # A notification-only item can subsequently become a legitimate Home member.
    getattr(frame, finish_method)(RefreshResult(items=[mention], home_keys={("live", "mention")}), announce=False)
    assert {post.remote_id for post in frame._scope_items("home:all")} == {"home", "mention"}
    frame.selected_account_id = None
    assert "liked" not in [post.remote_id for post in frame._scope_items("home:all")]
