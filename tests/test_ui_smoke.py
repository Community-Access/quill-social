"""Guarded smoke test of the wxPython shell.

Skips cleanly when wx cannot initialize (headless CI without a display). When it
can, it builds the full frame against a temp store, exercises field navigation,
Where Am I, a flag toggle, and command construction, then tears down -- catching
any wiring regression without a real screen reader.
"""

from __future__ import annotations

import pytest

wx = pytest.importorskip("wx")


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("QUILLSOCIAL_DATA", str(tmp_path))
    try:
        application = wx.App()
    except Exception:  # pragma: no cover - no display
        pytest.skip("wx cannot initialize in this environment")
    yield application
    application.Destroy()


def test_exports_keep_account_scope_and_do_not_change_navigation(app, monkeypatch):
    from quill_social.model import Account, SocialItem
    from quill_social.ui.app import SocialFrame

    frame = SocialFrame()
    try:
        frame.store.put_account(Account(account_id="export-a", handle="reader"))
        frame.store.put_account(Account(account_id="export-b", handle="other"))
        for index in range(505):
            frame.store.upsert_item(SocialItem(account_id="export-a", text=f"Export post {index}",
                                              remote_id=str(index), created_at=index + 1))
        frame.store.upsert_item(SocialItem(account_id="export-b", text="Other account private text"))
        frame.selected_account_id = "export-a"
        frame._load_scope("home:all", "Home")
        before = [item.item_id for item in frame._items]
        read_states = [item.read for item in frame._items]
        exports = []
        monkeypatch.setattr(frame, "_save_timeline_export", lambda text, title: exports.append(text))
        frame.cmd_export_timeline()
        assert "Export post 504" in exports[-1]
        assert "Other account private text" not in exports[-1]
        frame.cmd_export_all_timelines()
        assert "Export post 0\n" in exports[-1]  # Includes cache beyond the visible 500.
        assert "Other account private text" not in exports[-1]
        assert [item.item_id for item in frame._items] == before
        assert frame.current_scope == "home:all"
        assert [item.read for item in frame._items] == read_states
    finally:
        frame._on_close(None)


def test_restored_settings_are_applied_and_survive_close(app, monkeypatch, tmp_path):
    from quill_social import a11y
    from quill_social.services.settings_backup import create_backup, restore_backup
    from quill_social.ui import app as app_module

    frame = app_module.SocialFrame()
    data_dir = frame.data_dir
    try:
        a11y.save(data_dir, a11y.A11ySettings(ui_mode="advanced", exclude_web_addresses=True))
        backup = create_backup(data_dir)
        a11y.save(data_dir, frame.a11y)
        def restore(parent, directory):
            restore_backup(backup, directory)
            return True
        monkeypatch.setattr(app_module, "restore_settings_backup", restore)
        frame.cmd_restore_settings_backup()
        assert frame.a11y.ui_mode == "advanced"
        assert frame.a11y.exclude_web_addresses
        assert frame.GetMenuBar().FindMenu("Studio") != wx.NOT_FOUND
    finally:
        frame._on_close(None)
    assert a11y.load(data_dir).exclude_web_addresses


def test_frame_builds_and_navigates(app, monkeypatch):
    from quill_social.ui.app import SocialFrame

    frame = SocialFrame()
    try:
        assert frame.current_scope == "home:all"
        assert len(frame._items) == 15  # seeded mock timeline

        frame.list.Select(0)
        frame.list.Focus(0)
        item = frame._current_item()
        assert item is not None

        # field navigation does not raise
        frame._announce_field(1)
        frame._announce_field(-1)

        # where am i produces a non-empty utterance
        frame.cmd_where_am_i()

        # favourite toggles local state
        was = frame._current_item().favourited
        frame.cmd_favourite()
        assert frame._current_item().favourited is (not was)

        # command list builds
        assert len(frame._build_commands()) > 5

        # scope switches
        frame._load_scope("library:bookmarks", "Bookmarks")
        assert isinstance(frame._items, list)

        # GitHub scopes render rows from the mock surface
        frame._load_github("gh:issues", "Issues")
        assert frame.list.GetItemCount() >= 1

        # analytics / safety / AI / ecosystem commands run without a modal
        frame._show_text = lambda title, text: None
        frame._load_scope("home:all", "Home")
        frame.list.Select(0)
        frame.list.Focus(0)
        frame.cmd_analytics()
        frame.cmd_summarize_feed()
        frame.cmd_accessibility_check()
        frame.cmd_send_to_quill()

        # studio / manage dialogs open and tear down (modal stubbed)
        frame._modal = lambda dlg: dlg.Destroy()
        for handler in (
            frame.cmd_safety_center, frame.cmd_notification_policies,
            frame.cmd_plugins, frame.cmd_outbox, frame.cmd_agenda,
            frame.cmd_queue_schedule, frame.cmd_approvals,
        ):
            handler()

        # scheduler tick publishes a due plan
        from quill_social.model import Draft, PublicationPlan, now_ms
        d = Draft(text="scheduled hi", targets=["acct_mock"])
        frame.store.put_draft(d)
        frame.store.put_plan(PublicationPlan(
            draft_id=d.draft_id, account_id="acct_mock", network="mock",
            state="queued", scheduled_for=now_ms() - 1000))
        frame._on_sched_tick(None)
        import time
        deadline = time.monotonic() + 3
        while frame._scheduler_running and time.monotonic() < deadline:
            wx.Yield()
            time.sleep(0.01)
        assert frame.store.list_plans(state="published")
    finally:
        frame._on_close(None)


def test_commands_report_unavailable_without_selection(app):
    from quill_social.ui.app import SocialFrame

    frame = SocialFrame()
    try:
        frame._items = []
        frame.list.DeleteAllItems()
        cmds = {c.command_id: c for c in frame._build_commands()}
        assert not cmds["reply"].is_available()
        assert cmds["compose"].is_available()
    finally:
        frame._on_close(None)


def test_account_list_scopes_navigation_without_removing_features(app):
    from quill_social.model import Account, Draft, PublicationPlan
    from quill_social.ui.app import NAV_TREE, SocialFrame

    frame = SocialFrame()
    try:
        frame.store.put_account(Account(account_id="second", network="mock", handle="@second"))
        frame._populate_accounts()
        frame._refresh_from_network(announce=False)
        commands = {c.command_id for c in frame._build_commands()}
        assert frame.accounts.GetString(0) == "Unified Home"
        assert len(frame._scope_items("home:all")) == 30
        frame.accounts.SetSelection(2)
        event = wx.CommandEvent(wx.EVT_LISTBOX.typeId, frame.accounts.GetId())
        frame.accounts.ProcessWindowEvent(event)
        assert frame.selected_account_id == "second"
        assert {it.account_id for it in frame._items} == {"second"}
        assert frame.nav.GetItemText(frame._nav_nodes["home:all"]) == "Home"
        assert set(frame._nav_nodes) == {scope for _, _, children in NAV_TREE for _, scope in children}
        assert {c.command_id for c in frame._build_commands()} == commands

        first = frame.store.list_items(account_id="acct_mock")[0]
        second = frame._items[0]
        for item in (first, second):
            frame.store.set_flag(item.item_id, "bookmarked", True)
        bookmarks = frame._scope_items("library:bookmarks")
        assert second.item_id in {it.item_id for it in bookmarks}
        assert {it.account_id for it in bookmarks} == {"second"}
        frame.last_search = "the"
        assert all(it.account_id == "second" for it in frame._scope_items("discover:search"))
        frame.store.put_draft(Draft(text="first draft", targets=["acct_mock"]))
        frame.store.put_draft(Draft(text="second draft", targets=["second"]))
        frame._load_scope("pub:drafts", "Drafts")
        assert [d.text for d in frame._draft_rows] == ["second draft"]
        for draft in frame.store.list_drafts():
            frame.store.put_plan(PublicationPlan(draft_id=draft.draft_id, account_id=draft.targets[0], state="failed"))
        frame._load_scope("pub:failed", "Failed")
        assert frame.list.GetItemCount() == 1
        frame._select_account(None)
        assert len(frame._items) == 30
        assert frame.nav.GetItemText(frame._nav_nodes["home:all"]) == "Unified Home"
        frame._load_scope("pub:failed", "Failed")
        assert frame.list.GetItemCount() == 2
    finally:
        frame._on_close(None)


def test_add_account_loads_posts_without_manual_refresh(app, monkeypatch):
    import threading
    import time

    from quill_social.model import Account, SocialItem
    from quill_social.security.credentials import InMemoryCredentialStore
    from quill_social.services import refresh
    from quill_social.ui.app import AddAccountDialog, SocialFrame

    frame = SocialFrame()
    frame.credentials = InMemoryCredentialStore()
    account = Account(account_id="live", network="mastodon", handle="@person@example.test")
    worker_threads = []

    class Client:
        def home_timeline(self, **kwargs):
            worker_threads.append(threading.get_ident())
            return [SocialItem(network="mastodon", remote_id="123", text="Loaded after sign-in")]

        def notifications(self, **kwargs):
            return []

    def adapter(acct, credentials):
        assert credentials.resolve(credentials.reference("mastodon", "live")) == "test-only-token"
        return Client()

    monkeypatch.setattr(refresh, "adapter_for", adapter)
    monkeypatch.setattr(AddAccountDialog, "ShowModal", lambda self: wx.ID_OK)
    monkeypatch.setattr(AddAccountDialog, "_temp_account", lambda self: account)
    monkeypatch.setattr(AddAccountDialog, "_effective_secret", lambda self: "test-only-token")
    try:
        frame._on_add_account(None)
        assert frame.selected_account_id == "live"
        deadline = time.monotonic() + 5
        while frame._refresh_running and time.monotonic() < deadline:
            app.Yield()
            time.sleep(0.01)
        assert not frame._refresh_running
        assert [it.text for it in frame._items] == ["Loaded after sign-in"]
        assert worker_threads and all(t != threading.get_ident() for t in worker_threads)
        assert frame.accounts.GetSelection() == frame._account_ids.index("live")
        monkeypatch.setattr(AddAccountDialog, "ShowModal", lambda self: wx.ID_CANCEL)
        before = len(frame.store.list_accounts())
        frame._on_add_account(None)
        assert len(frame.store.list_accounts()) == before
    finally:
        frame._on_close(None)


def test_background_refresh_preserves_reading_position(app):
    from quill_social.model import SocialItem, now_ms
    from quill_social.services.refresh import RefreshResult
    from quill_social.ui.app import SocialFrame

    frame = SocialFrame()
    try:
        frame.list.Select(0, False)
        frame.list.Select(5)
        frame.list.Focus(5)
        before = frame._current_item().item_id
        frame._field_index = 2
        frame.details.SetSelection(3, 10)
        frame.details.SetFocus()
        focus = frame.FindFocus()
        fresh = SocialItem(account_id="acct_mock", remote_id="fresh", text="Newest post", created_at=now_ms() + 1000)
        frame._finish_refresh(RefreshResult(items=[fresh], posts=1), True)
        assert frame._current_item().item_id == before
        assert frame._field_index == 2
        assert frame.details.GetSelection() == (3, 10)
        assert frame.FindFocus() is focus
        assert not frame.store.get_item(fresh.item_id).read
    finally:
        frame._on_close(None)


@pytest.mark.parametrize("mode", ["standard", "advanced"])
def test_account_navigation_never_transfers_keyboard_focus(app, monkeypatch, mode):
    from quill_social.model import Account
    from quill_social.ui.app import SocialFrame

    frame = SocialFrame()
    try:
        frame.set_ui_mode(mode)
        frame.store.put_account(Account(account_id="second", network="mock", handle="@second"))
        frame._populate_accounts()
        frame.Show()
        frame.accounts.SetFocus()
        app.Yield()
        transfers = []
        def focused(event):
            transfers.append(event.GetEventObject())
            event.Skip()
        frame.nav.Bind(wx.EVT_SET_FOCUS, focused)
        frame.list.Bind(wx.EVT_SET_FOCUS, focused)
        nodes = dict(frame._nav_nodes)
        announcements = []
        monkeypatch.setattr(frame.announcer, "say", lambda text, *a, **k: announcements.append(text))
        for index in (1, 2, 0, 2):
            frame.accounts.SetSelection(index)
            event = wx.CommandEvent(wx.EVT_LISTBOX.typeId, frame.accounts.GetId())
            frame.accounts.ProcessWindowEvent(event)
            app.Yield()
            assert frame.FindFocus() is frame.accounts
            assert not transfers
            assert frame._nav_nodes == nodes
        assert not announcements  # Native account selection should speak uninterrupted.
        if mode == "standard":
            frame.timelines.SetFocus()
            app.Yield()
            assert frame.FindFocus() is frame.timelines
            assert frame._timeline_scopes[frame.timelines.GetSelection()] == "home:all"
            return
        frame.nav.SetFocus()
        app.Yield()
        assert frame.nav.GetSelection() == frame._nav_nodes["home:all"]
        frame.nav.SelectItem(frame._nav_nodes["library:bookmarks"])
        assert frame.current_scope == "library:bookmarks"
        frame.accounts.SetFocus()
        app.Yield()
        transfers.clear()
        frame.accounts.SetSelection(1)
        frame.accounts.ProcessWindowEvent(wx.CommandEvent(wx.EVT_LISTBOX.typeId, frame.accounts.GetId()))
        app.Yield()
        assert frame.FindFocus() is frame.accounts
        assert not transfers
        frame.nav.SetFocus()
        app.Yield()
        assert frame.nav.GetSelection() == frame._nav_nodes["home:all"]
        assert frame.current_scope == "home:all"
    finally:
        frame._on_close(None)


def test_navigation_summaries_are_advanced_and_optional(app, monkeypatch):
    from quill_social.model import Account
    from quill_social.ui.app import SocialFrame

    frame = SocialFrame()
    try:
        frame.store.put_account(Account(account_id="second", network="mock", handle="@second"))
        frame._populate_accounts()
        spoken = []
        monkeypatch.setattr(frame.announcer, "say", lambda text, *a, **k: spoken.append(text))
        frame.a11y.announce_timeline_summary = True

        bookmarks = frame._timeline_scopes.index("library:bookmarks")
        frame.timelines.SetSelection(bookmarks)
        frame.timelines.ProcessWindowEvent(
            wx.CommandEvent(wx.EVT_LISTBOX.typeId, frame.timelines.GetId()))
        assert spoken == []

        frame.set_ui_mode("advanced")
        spoken.clear()
        frame.nav.SelectItem(frame._nav_nodes["home:unread"])
        assert len(spoken) == 1
        assert spoken[0].startswith("Unread. ") and spoken[0].endswith(" unread.")
        spoken.clear()
        frame.accounts.SetSelection(2)
        frame.accounts.ProcessWindowEvent(
            wx.CommandEvent(wx.EVT_LISTBOX.typeId, frame.accounts.GetId()))
        assert spoken == ["Home. 0 items, 0 unread."]

        frame.a11y.announce_timeline_summary = False
        spoken.clear()
        frame.nav.SelectItem(frame._nav_nodes["library:bookmarks"])
        assert spoken == []
    finally:
        frame._on_close(None)


def test_display_timezone_preference_applies_immediately_and_cancel_keeps_value(app, monkeypatch):
    from quill_social import a11y
    from quill_social.time_display import format_timestamp
    from quill_social.ui.app import PreferencesDialog, SocialFrame

    frame = SocialFrame()
    try:
        assert frame.a11y.display_timezone == "system"
        item_id = frame._current_item().item_id
        dialog = PreferencesDialog(frame, frame.a11y)
        assert dialog.display_timezone.GetSelection() == 0
        dialog.display_timezone.SetSelection(1)
        monkeypatch.setattr(dialog, "ShowModal", lambda: wx.ID_OK)
        dialog.run_and_apply(frame)
        assert frame.a11y.display_timezone == "utc"
        assert a11y.load(frame.data_dir).display_timezone == "utc"
        assert frame._current_item().item_id == item_id
        assert f"When: {format_timestamp(frame._current_item().created_at, 'utc')}" in frame.details.GetValue()
        dialog = PreferencesDialog(frame, frame.a11y)
        dialog.display_timezone.SetSelection(0)
        monkeypatch.setattr(dialog, "ShowModal", lambda: wx.ID_CANCEL)
        dialog.run_and_apply(frame)
        assert frame.a11y.display_timezone == "utc"
        assert a11y.load(frame.data_dir).display_timezone == "utc"
    finally:
        frame._on_close(None)


def test_mode_switch_preserves_account_view_post_and_features(app, monkeypatch):
    from quill_social import a11y
    from quill_social.ui.app import SocialFrame

    frame = SocialFrame()
    try:
        frame.Show()
        app.Yield()
        assert frame.a11y.ui_mode == "standard"
        assert frame.timelines.IsShownOnScreen()
        assert not frame.nav.IsShownOnScreen()
        assert not frame.details.IsShownOnScreen()
        assert not frame.search.IsShownOnScreen()
        frame._select_account("acct_mock", announce=False)
        frame._load_scope("library:bookmarks", "Bookmarks")
        selected = frame._current_item().item_id
        commands = {cmd.command_id for cmd in frame._build_commands()}
        frame.timelines.SetFocus()
        frame.set_ui_mode("advanced")
        app.Yield()
        assert frame.nav.IsShownOnScreen()
        assert frame.details.IsShownOnScreen()
        assert frame.search.IsShownOnScreen()
        assert not frame.timelines.IsShownOnScreen()
        assert frame.FindFocus() is frame.nav
        assert frame.current_scope == "library:bookmarks"
        assert frame._current_item().item_id == selected
        assert frame.selected_account_id == "acct_mock"
        assert a11y.load(frame.data_dir).ui_mode == "advanced"
        assert commands < {cmd.command_id for cmd in frame._build_commands()}
        frame.set_ui_mode("standard")
        app.Yield()
        assert frame.FindFocus() is frame.timelines
        assert frame._current_item().item_id == selected
        assert {cmd.command_id for cmd in frame._build_commands()} == commands
        assert a11y.load(frame.data_dir).ui_mode == "standard"

        frame.set_ui_mode("advanced")
        frame.nav.SelectItem(frame._nav_nodes["gh:issues"])
        assert frame.current_scope == "gh:issues"
        assert frame.list.GetItemCount() > 0
        frame.set_ui_mode("standard")
        assert frame.current_scope == "home:all"
        assert "gh:issues" not in frame._timeline_scopes
        assert not {"home:unread", "attention:flagged", "discover:search",
                    "discover:catchup"}.intersection(frame._timeline_scopes)
        assert not frame.search.IsShownOnScreen()
    finally:
        frame._on_close(None)


def test_preferences_mode_is_saved_only_when_accepted(app, monkeypatch):
    from quill_social import a11y
    from quill_social.ui.app import PreferencesDialog, SocialFrame

    frame = SocialFrame()
    try:
        for result in (wx.ID_CANCEL, wx.ID_OK):
            dialog = PreferencesDialog(frame, frame.a11y)
            dialog.ui_mode.SetSelection(1)
            monkeypatch.setattr(dialog, "ShowModal", lambda chosen=result: chosen)
            dialog.run_and_apply(frame)
            expected = "advanced" if result == wx.ID_OK else "standard"
            assert frame.a11y.ui_mode == expected
            assert a11y.load(frame.data_dir).ui_mode == expected
    finally:
        frame._on_close(None)


def test_enter_views_post_but_respects_existing_remaps(app, monkeypatch):
    from quill_social.ui.app import SocialFrame

    frame = SocialFrame()
    try:
        frame.Show()
        app.Yield()
        frame.list.SetFocus()
        app.Yield()
        assert frame.FindFocus() is frame.list
        calls = []
        monkeypatch.setattr(frame, "cmd_view_post", lambda: calls.append("view"))
        monkeypatch.setattr(frame, "cmd_reply", lambda: calls.append("reply"))
        event = wx.KeyEvent(wx.wxEVT_CHAR_HOOK)
        monkeypatch.setattr(event, "GetKeyCode", lambda: wx.WXK_RETURN)
        frame._on_char_hook(event)
        frame.keymap.rebind("reply", "Enter")
        frame._on_char_hook(event)
        assert calls == ["view", "reply"]
    finally:
        frame._on_close(None)


def test_restored_thread_draft_preserves_standard_mode_and_thread_summary(app, monkeypatch):
    from quill_social.model import Draft, Media, Poll, PollOption, now_ms
    from quill_social.ui.app import ComposerDialog, SocialFrame

    frame = SocialFrame()
    try:
        draft = frame.store.put_draft(Draft(
            text="A thread", targets=["acct_mock"], thread_mode=True,
            in_reply_to="original", visibility="followers", content_warning="Spoilers",
            media=[Media(local_path="photo.jpg", alt_text="A bird")],
            poll=Poll(options=[PollOption(title="Yes"), PollOption(title="No")],
                      multiple=True, expires_at=now_ms() + 86_400_000)))
        seen = []
        def inspect(dialog):
            assert dialog.ui_mode == "standard"
            assert "thread splitting" in dialog.more_options.GetLabel()
            assert "Thread splitting" in dialog.report.GetValue()
            dialog.more_options.SetValue(True)
            dialog._on_more_options()
            assert dialog.thread_mode.IsShown()
            assert dialog.thread_mode.GetValue()
            restored = dialog._build_draft()
            assert restored.media == draft.media
            assert restored.in_reply_to == draft.in_reply_to
            assert restored.visibility == draft.visibility
            assert restored.content_warning == draft.content_warning
            assert [option.title for option in restored.poll.options] == ["Yes", "No"]
            assert restored.poll.multiple
            seen.append(True)
            return wx.ID_CANCEL
        monkeypatch.setattr(ComposerDialog, "ShowModal", inspect)
        frame._open_composer_for_draft(draft.draft_id)
        assert seen
        assert frame.store.get_draft(draft.draft_id) is not None
    finally:
        frame._on_close(None)


def test_standard_menus_posts_label_and_hidden_advanced_tools(app):
    from quill_social.ui.app import SocialFrame

    frame = SocialFrame()
    try:
        def menu_titles():
            bar = frame.GetMenuBar()
            return [bar.GetMenuLabelText(i) for i in range(bar.GetMenuCount())]
        assert menu_titles() == ["File", "Timeline", "Post", "Navigate", "View", "Tools"]
        assert frame.list.GetName() == "Posts"
        assert frame.list.GetParent().GetName() == "Posts"
        assert frame.list.GetParent() is not frame.search.GetParent()
        assert not hasattr(frame, "more_views")
        hidden = {"agenda", "queue_schedule", "approvals", "analytics", "plugins",
                  "send_to_quill", "summarize_feed", "catchup"}
        assert not hidden.intersection(command.command_id for command in frame._build_commands())
        # Repeated rebuilding must not accumulate menu handlers or lose tools.
        for _ in range(3):
            frame.set_ui_mode("advanced")
            assert "Studio" in menu_titles()
            assert hidden <= {command.command_id for command in frame._build_commands()}
            frame.set_ui_mode("standard")
            assert menu_titles() == ["File", "Timeline", "Post", "Navigate", "View", "Tools"]
        calls = []
        frame.cmd_refresh = lambda: calls.append("refresh")
        menu = frame.GetMenuBar().GetMenu(1)
        event = wx.CommandEvent(wx.EVT_MENU.typeId, menu.GetMenuItems()[0].GetId())
        frame.ProcessEvent(event)
        assert calls == ["refresh"]
    finally:
        frame._on_close(None)
