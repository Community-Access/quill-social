import pytest

from quill_social.model import Account, SocialItem
from quill_social.services.notifications import NotificationItem, save_items
from quill_social.services.refresh import RefreshResult

wx = pytest.importorskip("wx")


@pytest.fixture
def frame(tmp_path, monkeypatch):
    from quill_social.ui.app import SocialFrame
    monkeypatch.setenv("QUILLSOCIAL_DATA", str(tmp_path))
    app = wx.App()
    frame = SocialFrame()
    monkeypatch.setattr(frame, "_request_extra_timeline", lambda spec, **kwargs: None)
    yield frame
    frame._on_close(None)
    app.Destroy()


def post(account, remote_id, **kwargs):
    return SocialItem(account_id=account.account_id, network=account.network,
                      remote_id=remote_id, author_handle="reader@example.social",
                      text="Example text", **kwargs)


def test_browsing_is_separate_from_home_until_home_fetch(frame):
    account = frame.store.list_accounts()[0]
    frame.selected_account_id = account.account_id
    spec = frame.timeline_library.create(account.account_id, "hashtag", "accessibility")
    item = post(account, "tag-only")
    frame.timeline_library.store_loaded(spec.scope, [item])
    assert "tag-only" not in {i.remote_id for i in frame._scope_items("home:all")}
    assert [i.remote_id for i in frame._scope_items(spec.scope)] == ["tag-only"]
    result = RefreshResult(items=[item], home_keys={(account.account_id, item.remote_id)})
    frame._apply_refresh(result, announce=False)
    assert "tag-only" in {i.remote_id for i in frame._scope_items("home:all")}
    direct = post(account, "private", visibility="direct")
    frame.store.upsert_item(direct)
    for scope in ("home:all", "home:unread", "attention:mentions", "attention:notifications", "discover:catchup"):
        assert "private" not in {i.remote_id for i in frame._scope_items(scope)}


@pytest.mark.parametrize("mode", ["standard", "advanced"])
def test_account_changes_keep_focus_and_extra_views_are_available(frame, mode):
    account = frame.store.list_accounts()[0]
    spec = frame.timeline_library.create(account.account_id, "user", "reader")
    frame.set_ui_mode(mode)
    frame.Show()
    frame.accounts.SetFocus()
    wx.Yield()
    frame._select_account(account.account_id, announce=False)
    wx.Yield()
    assert wx.Window.FindFocus() == frame.accounts
    assert spec.scope in frame._timeline_scopes
    assert spec.scope in frame._nav_nodes
    frame._open_standard_timeline(spec.scope)
    assert frame.current_scope == spec.scope
    frame.cmd_toggle_timeline_announcements()
    assert frame.timeline_library.get(spec.scope).announce
    frame.cmd_close_timeline()
    assert frame.current_scope == "home:all"
    assert frame.timeline_library.get(spec.scope) is None


def test_messages_first_load_silent_then_new_messages_once(frame, monkeypatch):
    account = frame.store.put_account(Account(network="mastodon", handle="owner", instance="https://example.social"))
    frame.selected_account_id = account.account_id
    spec = frame._message_specs()[0]
    frame.a11y.account_options[account.account_id] = {"speech_timelines": ["attention:messages"]}
    spoken = []
    monkeypatch.setattr(frame.announcer, "say", lambda text, level, **kw: spoken.append((text, level)))
    old = post(account, "old", visibility="direct", created_at=1)
    new = post(account, "new", visibility="direct", created_at=2)
    frame._finish_extra_timeline(spec, [old], None)
    assert not spoken
    frame._finish_extra_timeline(spec, [new, old], None)
    assert len(spoken) == 1
    assert "direct message" in spoken[0][0]
    frame._finish_extra_timeline(spec, [new, old], None)
    assert len(spoken) == 1
    assert [i.remote_id for i in frame._scope_items("attention:messages")] == ["new", "old"]
    frame.timeline_library.remove(spec.scope)
    frame._finish_extra_timeline(spec, [post(account, "late")], None)
    assert not frame.timeline_library.get(spec.scope)


@pytest.mark.parametrize("remote_id,visibility", [
    ("chat:conversation:message", "direct"),
    ("instance:remote.social:42", "public"),
    ("search:user:7", "public"),
])
def test_private_and_remote_rows_cannot_use_public_actions(frame, monkeypatch, remote_id, visibility):
    account = frame.store.list_accounts()[0]
    item = post(account, remote_id, visibility=visibility)
    monkeypatch.setattr(frame, "_current_item", lambda: item)
    public, private, errors = [], [], []
    monkeypatch.setattr(frame, "_open_composer", lambda **kw: public.append(kw))
    monkeypatch.setattr(frame, "cmd_direct_message", private.append)
    monkeypatch.setattr(frame.announcer, "error", errors.append)
    frame.cmd_reply()
    frame.cmd_quote()
    frame.cmd_repost()
    frame.cmd_favourite()
    assert not public
    assert not frame._pending_reactions
    assert len(private) == (1 if visibility == "direct" else 0)
    assert errors


def test_notifications_are_event_rows_and_keep_statusless_follows(frame):
    account = frame.store.list_accounts()[0]
    frame.selected_account_id = account.account_id
    subject = frame.store.upsert_item(post(account, "post", uri="https://example/post"))
    save_items(frame.store, [
        NotificationItem(notif_id="fav", category="favourite", account_id=account.account_id,
                         network=account.network, actor_display="Ada", subject_id="post",
                         text="Original", created=3),
        NotificationItem(notif_id="boost", category="repost", account_id=account.account_id,
                         network=account.network, actor_display="Ben", subject_id="post",
                         text="Original", created=2),
        NotificationItem(notif_id="follow", category="follow", account_id=account.account_id,
                         network=account.network, actor_display="Cat", created=1),
        NotificationItem(notif_id="mention", category="mention", account_id=account.account_id,
                         network=account.network, actor_display="Dan", created=4),
    ])
    rows = frame._scope_items("attention:notifications")
    assert [row.text for row in rows] == [
        "Ada favourited your post: Original",
        "Ben reposted your post: Original",
        "Cat followed you",
    ]
    assert len({row.item_id for row in rows}) == 3
    assert frame._notification_subject(rows[0]).item_id == subject.item_id


def test_mastodon_quote_uses_native_support_or_link_fallback(frame, monkeypatch):
    account = frame.store.put_account(Account(
        account_id="mastodon-quote", network="mastodon", handle="reader",
        instance="example.social"))
    item = post(account, "42", uri="https://example.social/@ada/42")
    monkeypatch.setattr(frame, "_current_item", lambda: item)
    opened = []
    monkeypatch.setattr(frame, "_open_composer", lambda **kwargs: opened.append(kwargs))

    frame.caps.seed_from_network(account.account_id, "mastodon")
    frame.cmd_quote()
    assert opened[-1] == {
        "initial_text": "RE: https://example.social/@ada/42 ",
        "quote_mode": True,
        "target_account_id": account.account_id,
    }

    frame.caps.refine(account.account_id, supports_quote=True)
    frame.cmd_quote()
    assert opened[-1] == {
        "quote_of": "42", "quote_mode": True,
        "target_account_id": account.account_id,
    }


def test_standard_search_creates_closeable_server_timeline(frame, monkeypatch):
    from quill_social.ui import timeline_views

    account = frame.store.put_account(Account(
        account_id="search-account", network="mastodon", handle="reader",
        instance="example.social"))
    frame._populate_accounts()
    frame._select_account(account.account_id, announce=False)

    class SearchDialog:
        def __init__(self, parent):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def ShowModal(self):
            return wx.ID_OK

        def parameters(self):
            return "accessibility", "statuses", "Posts"

    monkeypatch.setattr(timeline_views, "SearchDialog", SearchDialog)
    frame.cmd_search()
    spec = frame.timeline_library.get(frame.current_scope)
    assert spec.kind == "search"
    assert (spec.value, spec.search_type, spec.label) == (
        "accessibility", "statuses", "Search: accessibility")
    assert spec.scope in frame._timeline_scopes
    assert spec.scope in frame._nav_nodes
    frame.cmd_close_timeline()
    assert frame.timeline_library.get(spec.scope) is None
