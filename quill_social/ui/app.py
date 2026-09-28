"""QUILL Social desktop shell -- accessible three-pane UI (PRD 9, 10, 29).

Account list + navigation tree | item list | details. Every pane is a named region, focus
moves predictably and is never stolen by a background refresh (PRD 28.3), and
every action is reachable by keyboard, menu, and the command center (PRD 3.2).
Up/Down move between items; Left/Right read the configured fields of the
focused item (PRD 10.1); Enter opens details. State is spoken through the
announcer and mirrored in the status bar so nothing important is hidden.

This is a self-contained shell for the first slice; the production target moves
the engine onto ``quill.ui.app_shell.AppShellFrame`` (PRD 44).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import webbrowser
from pathlib import Path
from threading import Thread

import wx

from quill_social import __title__, __version__, paths
from quill_social import a11y as a11y_mod
from quill_social import keymap as keymap_mod
from quill_social.adapters import oauth
from quill_social.adapters.base import AdapterError
from quill_social.adapters.github import MockGitHub
from quill_social.adapters.registry import adapter_for
from quill_social.capabilities import CapabilityRegistry
from quill_social.db import SocialStore
from quill_social.fields import FieldProfile, read_fields, render_row
from quill_social.model import Account, PublicationPlan, Workspace, now_ms
from quill_social.navigation import ordered_scopes
from quill_social.security.credentials import (
    InMemoryCredentialStore,
    WindowsCredentialManagerStore,
)
from quill_social.services import analytics as analytics_svc
from quill_social.services import catchup as catchup_svc
from quill_social.services import ecosystem as ecosystem_svc
from quill_social.services import notifications as notification_svc
from quill_social.services import smartfolder as smartfolder_svc
from quill_social.services.ai import accessibility as ai_a11y
from quill_social.services.ai import understand as ai_understand
from quill_social.services.refresh import fetch_accounts
from quill_social.services.scheduler import Scheduler
from quill_social.services.settings_backup import create_backup
from quill_social.services.thread_publisher import publish_thread
from quill_social.services.thread_splitter import mastodon_counter, split_thread
from quill_social.services.timeline_export import timeline_text
from quill_social.services.timelines import TimelineLibrary
from quill_social.time_display import format_timestamp
from quill_social.ui.account_preferences import AccountPreferencesDialog
from quill_social.ui.announce import Announcer
from quill_social.ui.commands import Command, CommandPalette
from quill_social.ui.composer import ComposerDialog
from quill_social.ui.composition_preferences import CompositionPreferencesPanel
from quill_social.ui.manage import (
    NotificationPoliciesDialog,
    OutboxDialog,
    PluginManagerDialog,
    SafetyCenterDialog,
)
from quill_social.ui.media_player import MediaPlayerDialog
from quill_social.ui.navigation_preferences import NavigationPreferencesPanel
from quill_social.ui.profile import ProfileDialog
from quill_social.ui.reading_preferences import AccountReadingPanel, BehaviorPanel
from quill_social.ui.settings_backup import create_settings_backup, restore_settings_backup
from quill_social.ui.studio import (
    AgendaDialog,
    ApprovalsDialog,
    DraftsDialog,
    QueueScheduleDialog,
)
from quill_social.ui.system_preferences import (
    GlobalHotkeys,
    ShortcutsPanel,
    load_global_bindings,
    save_global_bindings,
)
from quill_social.ui.timeline_views import TimelineViews
from quill_social.whereami import WhereAmI

# Navigation destinations (PRD 9.2). Each is (label, scope_id).
NAV_TREE = [
    ("Home", "home", [
        ("Unified Home", "home:all"),
        ("Unread", "home:unread"),
    ]),
    ("Attention", "attention", [
        ("Mentions", "attention:mentions"),
        ("Notifications", "attention:notifications"),
        ("Direct messages", "attention:messages"),
        ("Flagged", "attention:flagged"),
    ]),
    ("Library", "library", [
        ("Bookmarks", "library:bookmarks"),
        ("Favourites", "library:favourites"),
        ("Saved Searches", "library:searches"),
    ]),
    ("Publishing", "publishing", [
        ("Drafts", "pub:drafts"),
        ("Queue", "pub:queued"),
        ("Scheduled", "pub:scheduled"),
        ("Failed", "pub:failed"),
        ("Sent", "pub:published"),
    ]),
    ("Discover", "discover", [
        ("Search Results", "discover:search"),
        ("Catch Up", "discover:catchup"),
    ]),
    ("GitHub", "github", [
        ("Notifications", "gh:notifications"),
        ("Issues", "gh:issues"),
        ("Pull Requests", "gh:prs"),
        ("Discussions", "gh:discussions"),
        ("Releases", "gh:releases"),
    ]),
]


STANDARD_SCOPES = (
    "home:all", "attention:mentions", "attention:notifications", "attention:messages",
    "library:bookmarks", "library:favourites",
)


class SocialFrame(TimelineViews, wx.Frame):
    def __init__(self) -> None:
        super().__init__(None, title=__title__, size=(1180, 760))
        self.data_dir = paths.data_dir()
        self.store = SocialStore(paths.db_path())
        self.timeline_library = TimelineLibrary(self.store)
        self._timeline_jobs = set()
        self._timeline_loaded = set()
        self.a11y = a11y_mod.load(self.data_dir)
        self.keymap = keymap_mod.load(self.data_dir)
        self._backup_settings()
        self.caps = CapabilityRegistry()
        self.profile = FieldProfile()
        self.credentials = _make_credential_store()
        self.announcer = Announcer(self, verbosity=self.a11y.verbosity)

        self.current_scope = "home:all"
        self.current_scope_label = "Unified Home"
        self.selected_account_id: str | None = None
        self._updating_navigation = False
        self._refresh_running = False
        self._refresh_pending = False
        self.last_search = ""
        self._items: list = []
        self._field_index = 0
        self._closing = False
        self._marker_pending = {}
        self._marker_running = False
        self._pending_reactions = set()
        self._seen_notification_ids = {}
        self._announcement_ready = set()

        self._first_run_seed()
        self._load_caps()
        self._build_menu()
        self._build_ui()
        self._populate_accounts()
        a11y_mod.apply_to_frame(self, self.a11y)
        self._refresh_from_network(announce=False)
        self._populate_nav()
        self._apply_ui_mode()
        self._load_scope(self.current_scope, self.current_scope_label)
        self.CreateStatusBar()
        self.SetStatusText("QUILL Social ready. Press F1 for help.")
        self.announcer.say("QUILL Social ready", "normal")

        # Local scheduler tier (PRD 18.3): while the app is open, publish plans
        # whose time has arrived. A wx.Timer keeps the UI thread free.
        self.scheduler = Scheduler(self.store, self._resolve_adapter)
        self._sched_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_sched_tick, self._sched_timer)
        self._sched_timer.Start(30_000)

        self.Bind(wx.EVT_CHAR_HOOK, self._on_char_hook)
        self.Bind(wx.EVT_CLOSE, self._on_close)
        self._global_hotkeys = GlobalHotkeys(self)
        for error in self._global_hotkeys.apply(load_global_bindings(self.data_dir)):
            self.announcer.error(error)
        from quill_social.ui.tray import TrayController
        self._tray = TrayController(self)
        self._marker_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._flush_home_markers, self._marker_timer)
        self._marker_timer.Start(1500)
        self._announcement_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._poll_announcements, self._announcement_timer)
        self._announcement_timer.Start(60_000)
        if self.a11y.focus_posts_on_startup:
            wx.CallAfter(self._restore_reading_position)
        self.Centre()

    def _restore_reading_position(self) -> None:
        if self._closing:
            return
        try:
            state = json.loads((self.data_dir / "reading-position.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        if not isinstance(state, dict):
            state = {}
        account_id = state.get("account_id")
        if account_id and self.store.get_account(account_id):
            self.selected_account_id = account_id
            self._populate_accounts()
        scope = state.get("scope", "home:all")
        allowed = {scope_id: label for _, _, children in NAV_TREE for label, scope_id in children}
        allowed.update({spec.scope: spec.label for spec in self._extra_timelines()})
        if scope not in allowed or (self.a11y.ui_mode == "standard" and scope not in (*STANDARD_SCOPES, *(s.scope for s in self._extra_timelines()))):
            scope = "home:all"
        self._load_scope(scope, allowed[scope])
        if not scope.startswith(("pub:", "gh:")):
            self._render_list(selected_item_id=state.get("item_id"))
        self.list.SetFocus()

    def _flush_home_markers(self, event=None) -> None:
        if self._marker_running or not self._marker_pending or self._closing:
            return
        pending, self._marker_pending = self._marker_pending, {}
        requests = [(self.store.get_account(account_id), remote_id) for account_id, remote_id in pending.items()]
        credentials = self.credentials
        self._marker_running = True
        def worker():
            errors = []
            for account, remote_id in requests:
                if account and self.a11y.account_options.get(account.account_id, {}).get("sync_home_position"):
                    try:
                        adapter_for(account, credentials).save_home_position(remote_id)
                    except Exception as exc:
                        errors.append(f"Could not sync home position for {account.label}: {exc}")
            wx.CallAfter(self._finish_home_markers, errors)
        Thread(target=worker, daemon=True, name="social-markers").start()

    def _finish_home_markers(self, errors):
        if self._closing:
            return
        self._marker_running = False
        for error in errors:
            self.announcer.error(error)

    def cmd_clear_cache(self):
        if wx.MessageBox("Clear cached timeline posts for the selected account (all accounts in Unified Home)? "
                         "Saved, flagged, favourited, tagged posts and posts with notes are kept. "
                         "Drafts and scheduled posts are kept.", "Clear timeline cache",
                         wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION, self) != wx.YES:
            return
        count = self.store.clear_timeline_cache(self.selected_account_id)
        self._load_scope(self.current_scope, self.current_scope_label)
        self.announcer.say(f"Cleared {count} cached posts.", "normal")

    def _resolve_adapter(self, account_id: str):
        """Resolve an account's adapter, passing credentials when supported."""
        acct = self.store.get_account(account_id)
        try:
            return adapter_for(acct, self.credentials)
        except TypeError:
            return adapter_for(acct)

    def _on_sched_tick(self, _e) -> None:
        """Publish any due local-tier plans (PRD 18.3, 18.12)."""
        if self._closing or getattr(self, "_scheduler_running", False):
            return
        from quill_social.adapters.base import AdapterError
        from quill_social.model import now_ms
        from quill_social.services.scheduler import _failed_attempt, process_plan
        at = now_ms()
        deliveries = [(plan, self.store.get_draft(plan.draft_id),
                       self.store.get_account(plan.account_id))
                      for plan in self.store.due_plans(now=at)]
        if not deliveries:
            return
        self._scheduler_running = True
        credentials = self.credentials

        def worker():
            results = []
            for plan, draft, account in deliveries:
                try:
                    if draft is None or account is None:
                        raise AdapterError("Draft or account missing; cannot publish.", kind="validation")
                    results.append(process_plan(plan, adapter_for(account, credentials), draft, now=at))
                except Exception as exc:
                    error = exc if isinstance(exc, AdapterError) else AdapterError(str(exc))
                    results.append(_failed_attempt(plan, error, at))
            wx.CallAfter(self._finish_scheduled, results)
        Thread(target=worker, daemon=True, name="social-scheduler").start()

    def _finish_scheduled(self, results):
        self._scheduler_running = False
        if self._closing:
            return
        for result in results:
            self.store.put_plan(result.plan)
        published = [r for r in results if r.published]
        if published:
            self._refresh_from_network(announce=False)
            self._load_scope(self.current_scope, self.current_scope_label, keep_selection=True, announce=False)
        for result in results:
            account = self.store.get_account(result.plan.account_id)
            label = account.label if account else result.plan.account_id
            if result.published:
                self.announcer.say(f"{label}: Scheduled post sent.", "action")
            elif result.retry_scheduled:
                when = format_timestamp(result.plan.next_retry_at, self.a11y.display_timezone)
                self.announcer.error(f"{label}: Post could not be sent. Queued to retry at {when}. {result.message}")
            else:
                self.announcer.error(f"{label}: Post failed. {result.message}")

    # -- first run ------------------------------------------------------------

    def _first_run_seed(self) -> None:
        """Give a brand-new install a workspace and a local demo account.

        The mock account makes the whole app usable immediately, before any real
        credential is entered (PRD 42), and is clearly labeled as a demo.
        """
        if self.store.list_accounts():
            return
        ws = self.store.put_workspace(Workspace(name="Personal", position=0))
        self.store.put_account(Account(
            account_id="acct_mock",
            network="mock",
            handle="@you@mock.social",
            display_name="You (demo)",
            local_alias="Demo timeline",
            workspace_id=ws.workspace_id,
            is_default=True,
        ))

    def _load_caps(self) -> None:
        for acct in self.store.list_accounts():
            self.caps.seed_from_network(acct.account_id, acct.network)

    # -- menu -----------------------------------------------------------------

    def _build_menu(self) -> None:
        previous = self.GetMenuBar()
        for identifier in getattr(self, "_menu_bindings", []):
            self.Unbind(wx.EVT_MENU, id=identifier)
        self._menu_bindings = []
        if self.a11y.ui_mode == "standard":
            self._build_standard_menu()
        else:
            self._build_advanced_menu()
        self._menu_mode = self.a11y.ui_mode
        if previous:
            previous.Destroy()

    def _append_ported_file_items(self, menu) -> None:
        menu.AppendSeparator()
        self._menu_item(menu, "Edit &My Profile...", lambda event: self.cmd_edit_profile())
        self._menu_item(menu, "Export &Current Timeline...", lambda event: self.cmd_export_timeline())
        self._menu_item(menu, "Export All &Timelines...", lambda event: self.cmd_export_all_timelines())
        menu.AppendSeparator()
        self._menu_item(menu, "Create Settings &Backup...", lambda event: self.cmd_create_settings_backup())
        self._menu_item(menu, "&Restore Settings Backup...", lambda event: self.cmd_restore_settings_backup())

    def cmd_edit_profile(self) -> None:
        account = self.store.get_account(self.selected_account_id) if self.selected_account_id else None
        if account is None:
            accounts = [account for account in self.store.list_accounts()
                        if account.network in ("mastodon", "bluesky")]
            if not accounts:
                self.announcer.say("Add a Mastodon or Bluesky account to edit your profile.", "normal")
                return
            with wx.SingleChoiceDialog(self, "Choose the account whose profile you want to edit.",
                                       "Edit My Profile", [account.full_handle for account in accounts]) as chooser:
                if chooser.ShowModal() != wx.ID_OK:
                    return
                account = accounts[chooser.GetSelection()]
        if account.network not in ("mastodon", "bluesky"):
            self.announcer.say("Profile editing is available for Mastodon and Bluesky accounts.", "normal")
            return
        credentials = self.credentials
        with ProfileDialog(self, lambda: adapter_for(account, credentials),
                           account_label=account.full_handle) as dialog:
            if dialog.ShowModal() == wx.ID_OK and "display_name" in dialog.controls:
                account.display_name = dialog.controls["display_name"].GetValue()
                self.store.put_account(account)
                self._populate_accounts()

    def _backup_settings(self) -> None:
        try:
            create_backup(self.data_dir, only_if_changed=True)
        except (OSError, ValueError):
            logging.getLogger(__name__).exception("Could not create automatic settings backup")

    def cmd_create_settings_backup(self) -> None:
        a11y_mod.save(self.data_dir, self.a11y)
        keymap_mod.save(self.data_dir, self.keymap)
        create_settings_backup(self, self.data_dir)

    def cmd_restore_settings_backup(self) -> None:
        if not restore_settings_backup(self, self.data_dir):
            return
        self.a11y = a11y_mod.load(self.data_dir)
        self.keymap = keymap_mod.load(self.data_dir)
        self.announcer.set_verbosity(self.a11y.verbosity)
        for error in self._global_hotkeys.apply(load_global_bindings(self.data_dir)):
            self.announcer.error(error)
        a11y_mod.apply_to_frame(self, self.a11y)
        self._apply_ui_mode()
        self._build_menu()
        self._load_scope(self.current_scope, self.current_scope_label, keep_selection=True)
        self.announcer.say("Preferences and keyboard shortcuts restored.", "normal")

    def cmd_export_timeline(self) -> None:
        accounts = {account.account_id: account for account in self.store.list_accounts()}
        if self.current_scope.startswith(("pub:", "gh:")):
            rows = [self.list.GetItemText(index) for index in range(self.list.GetItemCount())]
            text = self.current_scope_label + "\n\n" + "\n".join(rows)
        else:
            if not self._items:
                self.announcer.say("There are no posts in this timeline to export.", "normal")
                return
            text = timeline_text(self.current_scope_label, self._items,
                                 accounts=accounts, timezone=self.a11y.display_timezone)
        self._save_timeline_export(text, "Current Timeline")

    def cmd_export_all_timelines(self) -> None:
        accounts = {account.account_id: account for account in self.store.list_accounts()}
        selected = accounts.get(self.selected_account_id)
        label = selected.full_handle if selected else "Unified Home (all accounts)"
        sections = [f"Quill Social timelines: {label}\n"
                    "Export of locally cached posts; does not fetch older server history.\n"]
        destinations = [(label, scope) for _, _, children in NAV_TREE
                        for label, scope in children if scope in STANDARD_SCOPES]
        destinations.extend((spec.label, spec.scope) for spec in self._extra_timelines())
        destinations.extend((folder.name, f"smart:{folder.folder_id}")
                            for folder in self.store.list_folders(kind="smart"))
        for title, scope in destinations:
            sections.append(timeline_text(title, self._scope_items(scope, limit=-1),
                                          accounts=accounts, timezone=self.a11y.display_timezone))
        self._save_timeline_export("\n".join(sections), "All Timelines")

    def _save_timeline_export(self, text: str, title: str) -> None:
        with wx.FileDialog(self, f"Export {title.lower()}",
                           defaultFile=f"Quill Social {title}.txt",
                           wildcard="Text files (*.txt)|*.txt",
                           style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT) as dialog:
            if dialog.ShowModal() != wx.ID_OK:
                return
            path = Path(dialog.GetPath())
        try:
            path.write_text(text, encoding="utf-8")
        except OSError as exc:
            self.announcer.error(f"Could not export timelines: {exc}")
            return
        self.announcer.say(f"Timeline exported to {path}", "normal")

    def _build_standard_menu(self) -> None:
        bar = wx.MenuBar()
        file_menu = wx.Menu()
        self._menu_item(file_menu, "Add &Account...\tCtrl+Shift+A", self._on_add_account)
        self._menu_item(file_menu, "&Preferences...", self._on_preferences)
        self._append_ported_file_items(file_menu)
        file_menu.AppendSeparator()
        self._menu_item(file_menu, "E&xit\tAlt+F4", lambda event: self.Close())
        bar.Append(file_menu, "&File")

        timeline = wx.Menu()
        self._menu_item(timeline, "&Refresh\tF5", lambda event: self.cmd_refresh())
        timeline.AppendSeparator()
        labels = {scope: label for _, _, children in NAV_TREE for label, scope in children}
        options = self._navigation_options()
        for scope in ordered_scopes(options, STANDARD_SCOPES):
            label = options.get("labels", {}).get(scope, "Home" if scope == "home:all" else labels[scope])
            self._menu_item(timeline, label, lambda event, dest=scope: self._open_standard_timeline(dest))
        self._append_timeline_commands(timeline)
        bar.Append(timeline, "&Timeline")

        post = wx.Menu()
        for label, handler in (("&New Post\tCtrl+N", self.cmd_compose),
                               ("&Reply\tCtrl+R", self.cmd_reply), ("&Quote\tCtrl+Q", self.cmd_quote),
                               ("&Boost / Repost\tCtrl+Shift+R", self.cmd_repost),
                               ("&Favourite\tCtrl+F", self.cmd_favourite),
                               ("Boo&kmark\tAlt+B", self.cmd_bookmark),
                               ("Flag for follow-&up", self.cmd_flag),
                               ("Mark rea&d\tCtrl+K", self.cmd_mark_read)):
            self._menu_item(post, label, lambda event, action=handler: action())
        self._menu_item(post, "New direct message...", lambda event: self.cmd_direct_message())
        bar.Append(post, "&Post")

        navigate = wx.Menu()
        for label, handler in (("Previous &timeline", lambda: self._move_timeline(-1)),
                               ("Next t&imeline", lambda: self._move_timeline(1)),
                               ("&Previous post", lambda: self._move_post(-1)),
                               ("&Next post", lambda: self._move_post(1)),
                               ("&Conversation\tCtrl+G", self.cmd_open_conversation),
                               ("Open &links\tCtrl+O", self.cmd_open_links),
                               ("Play &media\tCtrl+Enter", self.cmd_play_media),
                               ("&Search\tCtrl+L", self.cmd_search),
                               ("&Where Am I\tCtrl+Shift+I", self.cmd_where_am_i)):
            self._menu_item(navigate, label, lambda event, action=handler: action())
        bar.Append(navigate, "&Navigate")

        view = wx.Menu()
        self._menu_item(view, "View &post", lambda event: self.cmd_view_post())
        view.AppendSeparator()
        self._append_mode_choices(view)
        bar.Append(view, "&View")

        tools = wx.Menu()
        self._menu_item(tools, "Repeat last &announcement\tCtrl+Shift+Space",
                        lambda event: self.announcer.repeat_last())
        for label, handler in (("&Command Center\tCtrl+Shift+C", self.cmd_command_center),
                               ("&Safety Center", self.cmd_safety_center),
                               ("&Notification policies", self.cmd_notification_policies),
                               ("&Outbox", self.cmd_outbox), ("&Drafts", self.cmd_drafts),
                               ("&Help\tF1", self.cmd_help)):
            self._menu_item(tools, label, lambda event, action=handler: action())
        self._menu_item(tools, "&About", self._on_about)
        bar.Append(tools, "&Tools")
        self.SetMenuBar(bar)

    def _append_mode_choices(self, menu) -> None:
        self._mode_items = {}
        for mode in ("standard", "advanced"):
            item = menu.AppendRadioItem(wx.ID_ANY, f"{mode.capitalize()} mode")
            self._mode_items[mode] = item
            self.Bind(wx.EVT_MENU, lambda event, selected=mode: self.set_ui_mode(selected), item)
            self._menu_bindings.append(item.GetId())
            item.Check(mode == self.a11y.ui_mode)

    def _build_advanced_menu(self) -> None:
        bar = wx.MenuBar()

        m_file = wx.Menu()
        self._menu_item(m_file, "Add &Account...\tCtrl+Shift+A", self._on_add_account)
        self._menu_item(m_file, "&Preferences...", self._on_preferences)
        self._append_ported_file_items(m_file)
        m_file.AppendSeparator()
        self._menu_item(m_file, "E&xit\tAlt+F4", lambda _e: self.Close())
        bar.Append(m_file, "&File")

        m_view = wx.Menu()
        self._append_mode_choices(m_view)
        m_view.AppendSeparator()
        self._menu_item(m_view, "View &post", lambda event: self.cmd_view_post())
        self._append_timeline_commands(m_view)
        bar.Append(m_view, "&View")

        m_compose = wx.Menu()
        self._menu_item(m_compose, "&New Post\tCtrl+N", lambda _e: self.cmd_compose())
        self._menu_item(m_compose, "&Reply\tCtrl+R", lambda _e: self.cmd_reply())
        self._menu_item(m_compose, "&Quote\tCtrl+Q", lambda _e: self.cmd_quote())
        self._menu_item(m_compose, "New direct message...", lambda event: self.cmd_direct_message())
        bar.Append(m_compose, "&Compose")

        m_item = wx.Menu()
        self._menu_item(m_item, "&Boost / Repost\tCtrl+Shift+R",
                        lambda _e: self.cmd_repost())
        self._menu_item(m_item, "&Favourite\tCtrl+F", lambda _e: self.cmd_favourite())
        self._menu_item(m_item, "&Bookmark\tAlt+B", lambda _e: self.cmd_bookmark())
        self._menu_item(m_item, "Flag for follow-&up", lambda _e: self.cmd_flag())
        self._menu_item(m_item, "Open co&nversation\tCtrl+G",
                        lambda _e: self.cmd_open_conversation())
        self._menu_item(m_item, "&Open links\tCtrl+O", lambda _e: self.cmd_open_links())
        self._menu_item(m_item, "Mark &read\tCtrl+K", lambda _e: self.cmd_mark_read())
        m_item.AppendSeparator()
        self._menu_item(m_item, "&Accessibility check",
                        lambda _e: self.cmd_accessibility_check())
        self._menu_item(m_item, "Send to &QUILL", lambda _e: self.cmd_send_to_quill())
        self._menu_item(m_item, "&Play media\tCtrl+Enter",
                        lambda _e: self.cmd_play_media())
        bar.Append(m_item, "&Item")

        m_studio = wx.Menu()
        self._menu_item(m_studio, "&Drafts...", lambda _e: self.cmd_drafts())
        self._menu_item(m_studio, "&Agenda / Calendar...", lambda _e: self.cmd_agenda())
        self._menu_item(m_studio, "&Queue schedule...",
                        lambda _e: self.cmd_queue_schedule())
        self._menu_item(m_studio, "A&pprovals...", lambda _e: self.cmd_approvals())
        bar.Append(m_studio, "&Studio")

        m_tools = wx.Menu()
        self._menu_item(m_tools, "Repeat last &announcement\tCtrl+Shift+Space",
                        lambda event: self.announcer.repeat_last())
        self._menu_item(m_tools, "&Command Center\tCtrl+Shift+C",
                        lambda _e: self.cmd_command_center())
        self._menu_item(m_tools, "&Where Am I\tCtrl+Shift+I",
                        lambda _e: self.cmd_where_am_i())
        self._menu_item(m_tools, "&Search\tCtrl+L", lambda _e: self.cmd_focus_search())
        self._menu_item(m_tools, "&Refresh\tF5", lambda _e: self.cmd_refresh())
        self._menu_item(m_tools, "Catch &Up", lambda _e: self.cmd_catchup())
        m_tools.AppendSeparator()
        self._menu_item(m_tools, "&Insights / Analytics", lambda _e: self.cmd_analytics())
        self._menu_item(m_tools, "Safety Cen&ter", lambda _e: self.cmd_safety_center())
        self._menu_item(m_tools, "&Notification policies",
                        lambda _e: self.cmd_notification_policies())
        self._menu_item(m_tools, "&Plugins", lambda _e: self.cmd_plugins())
        self._menu_item(m_tools, "&Outbox", lambda _e: self.cmd_outbox())
        self._menu_item(m_tools, "Summari&ze this feed (AI)",
                        lambda _e: self.cmd_summarize_feed())
        bar.Append(m_tools, "&Tools")

        m_help = wx.Menu()
        self._menu_item(m_help, "&Help\tF1", lambda _e: self.cmd_help())
        self._menu_item(m_help, "&About", self._on_about)
        bar.Append(m_help, "&Help")

        self.SetMenuBar(bar)

    def _menu_item(self, menu, label, handler) -> None:
        text, separator, default_chord = label.partition("\t")
        if separator:
            command_id = next((command for command, chord in keymap_mod.DEFAULT_BINDINGS.items()
                               if chord == default_chord), None)
            if command_id:
                chord = self.keymap.chord_for(command_id)
                label = text + (f"\t{chord}" if chord else "")
        item = menu.Append(wx.ID_ANY, label)
        self.Bind(wx.EVT_MENU, handler, item)
        self._menu_bindings.append(item.GetId())

    # -- ui -------------------------------------------------------------------

    def _build_ui(self) -> None:
        splitter = wx.SplitterWindow(self, style=wx.SP_LIVE_UPDATE | wx.SP_3D)
        splitter.SetMinimumPaneSize(180)

        # Left: a flat account selector, followed by that account's navigation.
        left = wx.Panel(splitter)
        self._navigation_panel = left
        lsz = wx.BoxSizer(wx.VERTICAL)
        lsz.Add(wx.StaticText(left, label="&Accounts"), 0, wx.ALL, 4)
        self.accounts = wx.ListBox(left, style=wx.LB_SINGLE, size=(-1, 140))
        self.accounts.SetName("Accounts")
        lsz.Add(self.accounts, 0, wx.EXPAND | wx.ALL, 4)
        self.navigation_label = wx.StaticText(left, label="Navigation — Unified Home")
        lsz.Add(self.navigation_label, 0, wx.ALL, 4)
        self.nav = wx.TreeCtrl(
            left, style=wx.TR_HAS_BUTTONS | wx.TR_HIDE_ROOT | wx.TR_SINGLE
            | wx.TR_LINES_AT_ROOT | wx.TR_FULL_ROW_HIGHLIGHT)
        self.nav.SetName("Navigation")
        lsz.Add(self.nav, 1, wx.EXPAND | wx.ALL, 4)
        self.timelines = wx.ListBox(left)
        self.timelines.SetName("Timelines")
        lsz.Add(self.timelines, 1, wx.EXPAND | wx.ALL, 4)
        self.timelines.Bind(wx.EVT_LISTBOX, self._on_timeline_changed)
        left.SetSizer(lsz)

        # Right: search + list + details.
        right = wx.SplitterWindow(splitter, style=wx.SP_LIVE_UPDATE | wx.SP_3D)
        self._content_splitter = right
        right.SetMinimumPaneSize(120)

        top = wx.Panel(right)
        self._posts_panel = top
        tsz = wx.BoxSizer(wx.VERTICAL)
        srow = wx.BoxSizer(wx.HORIZONTAL)
        srow.Add(wx.StaticText(top, label="Search:"),
                 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)
        self.search = wx.TextCtrl(top, style=wx.TE_PROCESS_ENTER)
        self.search.SetName("Search posts")
        srow.Add(self.search, 1)
        tsz.Add(srow, 0, wx.EXPAND | wx.ALL, 4)
        self._search_sizer = srow

        posts_region = wx.Panel(top, name="Posts")
        posts_sizer = wx.BoxSizer(wx.VERTICAL)
        posts_sizer.Add(wx.StaticText(posts_region, label="Posts"), 0, wx.BOTTOM, 4)
        self.list = wx.ListCtrl(
            posts_region, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.LC_NO_HEADER,
            name="Posts")
        self.list.SetName("Posts")
        self.list.InsertColumn(0, "Post", width=760)
        posts_sizer.Add(self.list, 1, wx.EXPAND)
        posts_region.SetSizer(posts_sizer)
        tsz.Add(posts_region, 1, wx.EXPAND | wx.ALL, 4)
        self._standard_actions = wx.Panel(top)
        actions = wx.BoxSizer(wx.HORIZONTAL)
        for label, handler in (("Refresh", self.cmd_refresh), ("New Post", self.cmd_compose),
                               ("Reply", self.cmd_reply), ("View", self.cmd_view_post),
                               ("Links", self.cmd_open_links)):
            button = wx.Button(self._standard_actions, label=label)
            button.Bind(wx.EVT_BUTTON, lambda event, action=handler: action())
            actions.Add(button, 0, wx.RIGHT, 4)
        self._standard_actions.SetSizer(actions)
        tsz.Add(self._standard_actions, 0, wx.ALL, 4)
        top.SetSizer(tsz)

        bottom = wx.Panel(right)
        self._details_panel = bottom
        bsz = wx.BoxSizer(wx.VERTICAL)
        bsz.Add(wx.StaticText(bottom, label="Details"), 0, wx.ALL, 4)
        self.details = wx.TextCtrl(
            bottom, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2)
        self.details.SetName("Post details")
        bsz.Add(self.details, 1, wx.EXPAND | wx.ALL, 4)
        bottom.SetSizer(bsz)

        right.SplitHorizontally(top, bottom, -220)
        splitter.SplitVertically(left, right, 240)

        self.nav.Bind(wx.EVT_TREE_SEL_CHANGED, self._on_nav_changed)
        self.nav.Bind(wx.EVT_SET_FOCUS, self._on_navigation_focus)
        self.accounts.Bind(wx.EVT_LISTBOX, self._on_account_changed)
        self.list.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_item_selected)
        self.list.Bind(wx.EVT_LIST_ITEM_FOCUSED, self._on_item_selected)
        self.search.Bind(wx.EVT_TEXT_ENTER, lambda _e: self._run_search())

    def set_ui_mode(self, mode: str) -> None:
        if mode not in ("standard", "advanced"):
            return
        self.a11y.ui_mode = mode
        a11y_mod.save(self.data_dir, self.a11y)
        self._apply_ui_mode()
        self.announcer.say(f"{mode.capitalize()} mode", "normal")

    def _apply_ui_mode(self) -> None:
        standard = self.a11y.ui_mode == "standard"
        if getattr(self, "_menu_mode", None) != self.a11y.ui_mode:
            self._build_menu()
        focus = self.FindFocus()
        self.nav.Show(not standard)
        self.timelines.Show(standard)
        self._standard_actions.Show(standard)
        self._search_sizer.ShowItems(not standard)
        if standard and self._content_splitter.IsSplit():
            self._content_splitter.Unsplit(self._details_panel)
        elif not standard and not self._content_splitter.IsSplit():
            self._details_panel.Show()
            self._content_splitter.SplitHorizontally(self._posts_panel, self._details_panel, -220)
        self._populate_nav()
        if standard and self.current_scope not in self._timeline_scopes:
            self._open_standard_timeline("home:all")
        for mode, item in self._mode_items.items():
            item.Check(mode == self.a11y.ui_mode)
        self._navigation_panel.Layout()
        self._posts_panel.Layout()
        self.Layout()
        if focus and not focus.IsShownOnScreen() and self.IsShownOnScreen():
            (self.timelines if standard else self.nav).SetFocus()

    def _navigation_options(self) -> dict:
        return self.a11y.navigation.get(self.selected_account_id or "*", {})

    def _post_profile(self, account_id: str) -> FieldProfile:
        options = self.a11y.navigation.get(account_id, {})
        if "fields" not in options and self.selected_account_id is None:
            options = self.a11y.navigation.get("*", {})
        return FieldProfile(order=list(options["fields"])) if "fields" in options else self.profile

    def _refresh_timelines(self) -> None:
        labels = {scope: label if scope in STANDARD_SCOPES else f"{group}: {label}"
                  for group, _, children in NAV_TREE for label, scope in children}
        labels["home:all"] = "Home" if self.selected_account_id else "Unified Home"
        options = self._navigation_options()
        labels.update(options.get("labels", {}))
        labels.update({spec.scope: spec.label for spec in self._extra_timelines()})
        self._timeline_scopes = ordered_scopes(options, STANDARD_SCOPES) + [spec.scope for spec in self._extra_timelines()]
        names = [labels[scope] for scope in self._timeline_scopes]
        if list(self.timelines.GetStrings()) != names:
            self.timelines.Set(names)
        if self.current_scope in self._timeline_scopes:
            self.timelines.SetSelection(self._timeline_scopes.index(self.current_scope))

    def _on_timeline_changed(self, event) -> None:
        index = self.timelines.GetSelection()
        if index != wx.NOT_FOUND:
            self._load_scope(self._timeline_scopes[index], self.timelines.GetString(index))

    def _open_standard_timeline(self, scope: str) -> None:
        labels = {key: label for _, _, children in NAV_TREE for label, key in children}
        labels.update({spec.scope: spec.label for spec in self._extra_timelines()})
        label = labels[scope]
        if scope == "home:all" and self.selected_account_id:
            label = "Home"
        label = self._navigation_options().get("labels", {}).get(scope, label)
        self._load_scope(scope, label)

    def _move_timeline(self, delta: int) -> None:
        scopes = self._timeline_scopes
        current = scopes.index(self.current_scope) if self.current_scope in scopes else 0
        index = min(max(current + delta, 0), len(scopes) - 1)
        self._open_standard_timeline(scopes[index])
        self.timelines.SetFocus()

    def _move_post(self, delta: int) -> None:
        count = self.list.GetItemCount()
        if count:
            current = self.list.GetFirstSelected()
            target = min(max(current + delta, 0), count - 1)
            if current >= 0:
                self.list.Select(current, False)
            self.list.Select(target)
            self.list.Focus(target)
            self.list.EnsureVisible(target)
            self.list.SetFocus()

    def cmd_view_post(self) -> None:
        item = self._current_item()
        if item is None:
            self.announcer.error("Select a post to view.")
            return
        self._show_details(item)
        if self.a11y.ui_mode == "standard":
            title = "Notification details" if self._is_notification_row(item) else "Post details"
            self._show_text(title, self.details.GetValue())
        else:
            self.details.SetFocus()

    def _populate_accounts(self) -> None:
        accounts = self.store.list_accounts()
        self._account_ids = [None] + [account.account_id for account in accounts]
        self.accounts.Set(["Unified Home"] + [
            f"{account.label} ({account.network})" + (" — paused" if account.paused else "")
            for account in accounts
        ])
        if self.selected_account_id not in self._account_ids:
            self.selected_account_id = None
        self.accounts.SetSelection(self._account_ids.index(self.selected_account_id))

    def _on_account_changed(self, event) -> None:
        if self._closing:
            return
        index = self.accounts.GetSelection()
        if index == wx.NOT_FOUND:
            return
        self._select_account(self._account_ids[index])

    def _select_account(self, account_id: str | None, *, announce: bool = True) -> None:
        self.selected_account_id = account_id
        index = self._account_ids.index(account_id)
        if self.accounts.GetSelection() != index:
            self.accounts.SetSelection(index)
        self.current_scope = "home:all"
        self._populate_nav()
        self._load_scope("home:all", "Home" if account_id else "Unified Home", announce=announce)

    def _populate_nav(self) -> None:
        # The destinations are stable across accounts. Rebuilding and selecting
        # native tree items here steals focus from the Accounts list on Windows.
        self._updating_navigation = True
        try:
            options = self._navigation_options()
            menu_layout = (ordered_scopes(options, STANDARD_SCOPES), dict(options.get("labels", {})), [(s.scope, s.label) for s in self._extra_timelines()])
            if menu_layout != getattr(self, "_timeline_menu_layout", None):
                self._timeline_menu_layout = menu_layout
                self._build_menu()
            layout = [(label, group, [(options.get("labels", {}).get(scope, dict((s, n) for n, s in children)[scope]), scope)
                                     for scope in ordered_scopes(options, [s for _, s in children])])
                      for label, group, children in NAV_TREE]
            layout.append(("Additional timelines", "additional", [(s.label, s.scope) for s in self._extra_timelines()]))
            if not getattr(self, "_nav_nodes", None) or layout != getattr(self, "_nav_layout", None):
                self.nav.DeleteAllItems()
                self._nav_layout = layout
                root = self.nav.AddRoot("root")
                self._nav_nodes = {}
                for label, scope, children in layout:
                    if not children:
                        continue
                    parent = self.nav.AppendItem(root, label)
                    self.nav.SetItemData(parent, (scope, label))
                    for clabel, cscope in children:
                        node = self.nav.AppendItem(parent, clabel)
                        self.nav.SetItemData(node, (cscope, clabel))
                        self._nav_nodes[cscope] = node
                    if scope == "home":
                        self.nav.Expand(parent)
            home_label = options.get("labels", {}).get("home:all", "Home" if self.selected_account_id else "Unified Home")
            home = self._nav_nodes["home:all"]
            self.nav.SetItemText(home, home_label)
            self.nav.SetItemData(home, ("home:all", home_label))
            account = self.store.get_account(self.selected_account_id) if self.selected_account_id else None
            context = account.label if account else "Unified Home"
            pane = "Timelines" if self.a11y.ui_mode == "standard" else "Navigation"
            self.navigation_label.SetLabel(f"{pane} — {context}")
            self.nav.SetName(f"Navigation for {context}")
            self._pending_nav_scope = self.current_scope
        finally:
            self._updating_navigation = False
        if self.FindFocus() is self.nav:
            self._sync_navigation_selection()
        self._refresh_timelines()

    def _sync_navigation_selection(self) -> None:
        scope = getattr(self, "_pending_nav_scope", None)
        self._pending_nav_scope = None
        if scope not in self._nav_nodes:
            return
        self._updating_navigation = True
        try:
            self.nav.SelectItem(self._nav_nodes[scope])
        finally:
            self._updating_navigation = False

    def _on_navigation_focus(self, event) -> None:
        # Native selection may take focus: do it only after the user arrives.
        self._sync_navigation_selection()
        event.Skip()

    # -- data loading ---------------------------------------------------------

    def _poll_announcements(self, event=None) -> None:
        if self._closing or self._refresh_running:
            return
        self._poll_extra_timelines()
        enabled = any(self.a11y.account_options.get(account.account_id, {}).get("speech_timelines")
                      for account in self.store.list_accounts(include_paused=False))
        if enabled:
            self._refresh_from_network(announce=False, automatic=True)

    def _refresh_from_network(self, *, announce: bool = True, automatic: bool = False) -> None:
        """Pull each active account's home timeline into the local store.

        Failures are per-account and never crash the app or block other
        accounts (PRD 32.3). Live adapters that are not wired report a clear
        state instead of an opaque error.
        """
        if self._refresh_running:
            self._refresh_pending = True
            return
        accounts = self.store.list_accounts(include_paused=False)
        limit = self.a11y.timeline_limit
        sync_accounts = [account.account_id for account in accounts
                         if self.a11y.account_options.get(account.account_id, {}).get("sync_home_position")]
        if all(account.network == "mock" for account in accounts):
            self._apply_refresh(fetch_accounts(accounts, self.credentials, limit=limit),
                                announce=announce, automatic=automatic)
            return
        self._refresh_running = True
        if announce:
            self.announcer.say("Loading account timelines…", "normal")
        credentials = self.credentials

        def worker():
            result = fetch_accounts(accounts, credentials, limit=limit, sync_accounts=sync_accounts)
            if not self._closing:
                wx.CallAfter(self._finish_refresh, result, announce, automatic)

        Thread(target=worker, daemon=True, name="social-refresh").start()

    def _apply_refresh(self, result, *, announce: bool, automatic: bool = False) -> None:
        existing = {(item.account_id, item.remote_id) for item in self.store.list_items(limit=-1)}
        new_items = []
        for item in result.items:
            key = (item.account_id, item.remote_id)
            if key not in existing:
                new_items.append(item)
                existing.add(key)
            stored = self.store.upsert_item(item)
            if key in result.home_keys:
                self.store.put_document("home-post", stored.item_id, {})
                self.store.delete_document("timeline-only", stored.item_id)
            elif self.store.get_document("home-post", stored.item_id) is None:
                self.store.put_document("timeline-only", stored.item_id, {"source": "notification"})
        notification_svc.save_items(self.store, getattr(result, "notifications", []))
        for account_id, caps in getattr(result, "capabilities", {}).items():
            self.caps.set(account_id, caps)
        self._announce_updates(result, new_items, enabled=announce or automatic)
        if result.errors:
            self.announcer.error("\n".join(result.errors))
        elif announce:
            self.announcer.say(f"Refreshed. {result.posts} posts.", "normal")

    def _finish_refresh(self, result, announce: bool, automatic: bool = False) -> None:
        if self._closing:
            return
        self._refresh_running = False
        existing = {(item.account_id, item.remote_id) for item in self.store.list_items(limit=-1)}
        new_items = []
        for item in result.items:
            key = (item.account_id, item.remote_id)
            if key not in existing:
                new_items.append(item)
                existing.add(key)
            stored = self.store.upsert_item(item)
            if key in result.home_keys:
                self.store.put_document("home-post", stored.item_id, {})
                self.store.delete_document("timeline-only", stored.item_id)
            elif self.store.get_document("home-post", stored.item_id) is None:
                self.store.put_document("timeline-only", stored.item_id, {"source": "notification"})
        notification_svc.save_items(self.store, getattr(result, "notifications", []))
        for account_id, caps in getattr(result, "capabilities", {}).items():
            self.caps.set(account_id, caps)
        if not self.current_scope.startswith(("pub:", "gh:")):
            self._load_scope(self.current_scope, self.current_scope_label, keep_selection=True, announce=announce)
        # A remote marker only restores selection when the user has not started reading.
        marker = result.home_positions.get(self.selected_account_id)
        if marker and self.current_scope == "home:all" and not self._marker_pending:
            target = next((item for item in self._items if item.remote_id == marker), None)
            if target and wx.Window.FindFocus() != self.list:
                self._render_list(selected_item_id=target.item_id)
        # Keep errors visible after the row-count announcement.
        if result.errors:
            self.announcer.error("\n".join(result.errors))
        elif announce:
            self.announcer.say(f"Refreshed. {result.posts} posts.", "normal")
        self._announce_updates(result, new_items, enabled=announce or automatic)
        if self._refresh_pending:
            self._refresh_pending = False
            self._refresh_from_network()

    def _announce_updates(self, result, new_items, *, enabled):
        from quill_social.reading_options import automatic_text
        accounts = {account.account_id: account for account in self.store.list_accounts()}
        messages = []
        spoken_posts = set()
        focus = wx.Window.FindFocus()
        foreground = bool(self.IsActive() and focus and (focus == self or self.IsDescendant(focus)))

        def speak(item, account_id, scope, event=None):
            if item and item.visibility == "direct":
                return False
            options = self.a11y.account_options.get(account_id, {})
            account = accounts.get(account_id)
            if (not enabled or account_id not in self._announcement_ready or not account
                    or account.paused or options.get("speech_muted", False)
                    or scope not in options.get("speech_timelines", [])):
                return False
            if event is not None:
                from datetime import datetime

                from quill_social.ui.manage import load_quiet_hours
                category = notification_svc.normalize_category(event.kind)
                policy = notification_svc.get_policy(self.store, account_id, category)
                window = load_quiet_hours(self.store, account_id)
                if None in window:
                    window = load_quiet_hours(self.store, "")
                window = None if None in window else window
                notification = notification_svc.from_event(event, account.network)
                local = datetime.now()
                if not notification_svc.classify(
                        policy or notification_svc.NotificationPolicy(), notification,
                        quiet_hours=window, now_minute=local.hour * 60 + local.minute).speak:
                    return False
            messages.append(automatic_text(item, self.a11y, scope=scope, notification=event,
                                          account_label=account.label, account_count=len(accounts),
                                          active_account_context=foreground and self.selected_account_id == account_id))
            return True

        for event in sorted(result.events, key=lambda event: event.created_at):
            seen = self._seen_notification_ids.setdefault(event.account_id, set())
            if event.notification_id in seen:
                continue
            seen.add(event.notification_id)
            scopes = self.a11y.account_options.get(event.account_id, {}).get("speech_timelines", [])
            scope = ("attention:mentions" if event.kind in {"mention", "reply"}
                     and "attention:mentions" in scopes else "attention:notifications")
            if speak(event.item, event.account_id, scope, event) and event.item and event.kind in {"mention", "reply"}:
                spoken_posts.add((event.account_id, event.item.remote_id))
        for item in sorted(new_items, key=lambda item: item.created_at):
            key = (item.account_id, item.remote_id)
            if key in spoken_posts or (result.home_keys and key not in result.home_keys):
                continue
            if key in result.notification_keys and key not in result.home_keys:
                continue
            speak(item, item.account_id, "home:all")
        self._announcement_ready.update(result.successful_accounts)
        # Limit event history while retaining all IDs in the latest refresh window.
        for account_id, seen in self._seen_notification_ids.items():
            if len(seen) > 2000:
                self._seen_notification_ids[account_id] = {
                    event.notification_id for event in result.events if event.account_id == account_id}
        if messages:
            self.announcer.say("\n".join(messages), "automatic", interrupt=False)

    def _mentions_account(self, item) -> bool:
        import re
        account = self.store.get_account(item.account_id)
        if not account:
            return False
        handles = {account.full_handle.lstrip("@").lower(), account.handle.lstrip("@").lower()}
        mentioned = {match.lower() for match in re.findall(r"@([\w.-]+(?:@[\w.-]+)?)", item.text)}
        return bool(handles.intersection(mentioned)) or (account.network == "mock" and "you" in mentioned)

    def _scope_items(self, scope: str, *, limit: int | None = None) -> list:
        limit = self.a11y.timeline_display_limit if limit is None else limit
        if scope == "attention:messages" or self.timeline_library.get(scope):
            specs = self._message_specs() if scope == "attention:messages" else [self.timeline_library.get(scope)]
            rows = {item.item_id: item for spec in specs for item in self.timeline_library.items(spec.scope)}
            result = sorted(rows.values(), key=lambda item: item.created_at, reverse=True)
            return result if limit < 0 else result[:limit]
        def items(**filters):
            return self.store.list_items(account_id=self.selected_account_id, limit=limit, **filters)
        if scope == "home:all":
            sources = self._navigation_options().get("unified_sources", ["home:all"])
            merged = {}
            for source in sources:
                if source not in {"home:all", "home:unread", "attention:mentions", "attention:notifications",
                                  "attention:flagged", "library:bookmarks", "library:favourites"}:
                    continue
                for item in (items(home_only=True, exclude_timeline_only=True, exclude_direct=True) if source == "home:all" else self._scope_items(source, limit=limit)):
                    merged[item.item_id] = item
            result = sorted(merged.values(), key=lambda item: item.created_at, reverse=True)
            return result if limit < 0 else result[:limit]
        if scope == "home:unread":
            return items(unread_only=True, home_only=True, exclude_timeline_only=True, exclude_direct=True)
        if scope == "attention:mentions":
            return [it for it in items(exclude_direct=True) if self._mentions_account(it)]
        if scope == "attention:notifications":
            rows = notification_svc.list_items(
                self.store, self.selected_account_id, limit=-1)
            result = [notification_svc.to_social_item(row) for row in rows
                      if row.category not in {"mention", "reply"}]
            return result if limit < 0 else result[:limit]
        if scope == "attention:flagged":
            return items(flagged=True)
        if scope == "library:bookmarks":
            return items(bookmarked=True)
        if scope == "library:favourites":
            return items(favourited=True)
        if scope == "discover:search":
            return self.store.search_items(self.last_search, account_id=self.selected_account_id,
                                           limit=limit) if self.last_search else []
        if scope == "discover:catchup":
            return self._catchup_items(limit=limit)
        if scope.startswith("smart:"):
            folder_id = scope.split(":", 1)[1]
            folders = {f.folder_id: f for f in self.store.list_folders(kind="smart")}
            folder = folders.get(folder_id)
            if folder:
                return smartfolder_svc.evaluate(items(), folder.rule)
        return []

    def _catchup_items(self, *, limit: int | None = None) -> list:
        limit = self.a11y.timeline_display_limit if limit is None else limit
        items = self.store.list_items(account_id=self.selected_account_id, limit=limit, home_only=True, exclude_timeline_only=True, exclude_direct=True)
        items = catchup_svc.collapse_reposts(items)
        items = catchup_svc.collapse_cross_network(items)
        return items

    def _load_scope(self, scope: str, label: str, *, keep_selection: bool = False, announce: bool = True) -> None:
        self._conversation_request = None
        previous = self._current_item() if keep_selection else None
        previous_id = previous.item_id if previous else None
        field_index = self._field_index
        self.current_scope = scope
        self.current_scope_label = label
        self._pending_nav_scope = scope
        self._refresh_timelines()
        if not keep_selection:
            self.details.Clear()
        if scope.startswith("pub:"):
            self._load_publishing(scope, label, announce=announce)
            return
        if scope.startswith("gh:"):
            self._load_github(scope, label, announce=announce)
            return
        self._ensure_extra_loaded(scope)
        self._items = self._scope_items(scope)
        if self.a11y.reverse_timelines:
            self._items.reverse()
        self._render_list(selected_item_id=previous_id)
        selected = self._current_item()
        if previous_id and selected and selected.item_id == previous_id:
            self._field_index = field_index
        unread = sum(1 for it in self._items if not it.read)
        if self._announce_navigation_summary(announce):
            self.announcer.say(f"{label}. {len(self._items)} items, {unread} unread.",
                               "normal")

    def _announce_navigation_summary(self, requested: bool) -> bool:
        return bool(requested and self.a11y.ui_mode == "advanced"
                    and self.a11y.announce_timeline_summary)

    def _load_publishing(self, scope: str, label: str, *, announce: bool = True) -> None:
        self.list.DeleteAllItems()
        self._items = []
        if scope == "pub:drafts":
            drafts = self.store.list_drafts()
            if self.selected_account_id:
                drafts = [d for d in drafts if not d.targets or self.selected_account_id in d.targets]
            for i, d in enumerate(drafts):
                preview = (d.text[:80] or "(empty)").replace("\n", " ")
                self.list.InsertItem(i, f"Draft: {preview}")
            if self._announce_navigation_summary(announce):
                self.announcer.say(f"{label}. {len(drafts)} drafts.", "normal")
            self._draft_rows = drafts
            return
        state = scope.split(":", 1)[1]
        plans = self.store.list_plans(state=state)
        if self.selected_account_id:
            plans = [p for p in plans if p.account_id == self.selected_account_id]
        for i, p in enumerate(plans):
            when = format_timestamp(p.scheduled_for, self.a11y.display_timezone) if p.scheduled_for else "now"
            self.list.InsertItem(
                i, f"{p.network} -> {p.state}, {when}, retries {p.retry_count}")
        if self._announce_navigation_summary(announce):
            self.announcer.say(f"{label}. {len(plans)} items.", "normal")

    def _load_github(self, scope: str, label: str, *, announce: bool = True) -> None:
        """Render GitHub items as text rows (PRD 24.1).

        Backed by the deterministic MockGitHub so the collaboration surface is
        navigable out of the box; the live adapter plugs in behind the same
        interface. GitHub rows are not SocialItems, so item commands stay inert.
        """
        self.list.DeleteAllItems()
        self._items = []
        gh = MockGitHub()
        try:
            if scope == "gh:notifications":
                objs = gh.notifications()
            elif scope == "gh:issues":
                objs = gh.issues("")
            elif scope == "gh:prs":
                objs = gh.pull_requests("")
            elif scope == "gh:discussions":
                objs = gh.discussions("")
            else:
                objs = gh.releases("")
        except AdapterError as exc:
            self.list.InsertItem(0, str(exc))
            self.announcer.say(str(exc), "normal")
            return
        for i, o in enumerate(objs):
            d = o.to_dict()
            num = d.get("number")
            title = d.get("title") or d.get("subject") or d.get("tag") or "(item)"
            state = d.get("state", "")
            labels = " ".join(d.get("labels", []) or [])
            head = f"#{num} " if num else ""
            tail = f" [{state}]" if state else ""
            row = f"{head}{title}{tail}{(' ' + labels) if labels else ''}".strip()
            self.list.InsertItem(i, row)
        if self._announce_navigation_summary(announce):
            self.announcer.say(f"{label}. {len(objs)} items.", "normal")

    def _render_list(self, *, keep_selection: bool = False, selected_item_id: str | None = None) -> None:
        previous = self._current_item() if keep_selection else None
        if previous is not None:
            selected_item_id = previous.item_id
        self.list.DeleteAllItems()
        accounts = {a.account_id: a for a in self.store.list_accounts()}
        now = now_ms()
        for i, it in enumerate(self._items):
            row = render_row(it, self._post_profile(it.account_id), account=accounts.get(it.account_id),
                             settings=self.a11y, now=now)
            idx = self.list.InsertItem(i, row or "(no text)")
            if not it.read:
                # Read state is spoken, never color-only (PRD 6.5); we still bold
                # unread rows for low-vision users as a secondary cue.
                font = self.list.GetFont()
                font.SetWeight(wx.FONTWEIGHT_BOLD)
                self.list.SetItemFont(idx, font)
        if self._items:
            target = next((i for i, item in enumerate(self._items) if item.item_id == selected_item_id), 0)
            self.list.Select(target)
            self.list.Focus(target)
        else:
            self.details.Clear()

    # -- selection + field navigation -----------------------------------------

    def _current_item(self):
        idx = self.list.GetFirstSelected()
        if 0 <= idx < len(self._items):
            return self._items[idx]
        return None

    @staticmethod
    def _is_notification_row(item) -> bool:
        return bool(item and item.item_id.startswith("notification:"))

    def _notification_subject(self, item):
        """Resolve the cached post associated with a notification row."""
        if not self._is_notification_row(item):
            return item
        data = self.store.get_document(notification_svc.ITEM_KIND, item.item_id)
        if not data:
            return None
        note = notification_svc.NotificationItem.from_dict(data)
        if note.subject_item_id:
            subject = self.store.get_item(note.subject_item_id)
            if subject is not None:
                return subject
        return next((candidate for candidate in self.store.list_items(
            account_id=note.account_id, limit=-1)
            if candidate.remote_id == note.subject_id), None)

    def _on_item_selected(self, event) -> None:
        if self._closing:
            return
        self._field_index = 0
        item = self._current_item()
        if item is not None:
            self._show_details(item)
            if not item.read:
                if self._is_notification_row(item):
                    notification_svc.mark_read(self.store, item.item_id)
                else:
                    self.store.set_read(item.item_id, True)
                item.read = True
            if (self.current_scope == "home:all" and item.network == "mastodon"
                    and self.a11y.account_options.get(item.account_id, {}).get("sync_home_position")
                    and wx.Window.FindFocus() == self.list):
                self._marker_pending[item.account_id] = item.remote_id
        if event:
            event.Skip()

    def _show_details(self, item) -> None:
        if self._is_notification_row(item):
            data = self.store.get_document(notification_svc.ITEM_KIND, item.item_id)
            note = notification_svc.NotificationItem.from_dict(data) if data else None
            if note:
                lines = [
                    f"Notification: {note.category.replace('_', ' ')}",
                    f"From: {note.actor_display} {note.actor_handle}".rstrip(),
                    f"Network: {note.network}",
                    f"When: {format_timestamp(note.created, self.a11y.display_timezone, twelve_hour=self.a11y.post_timestamps_12_hour)}",
                    "",
                    notification_svc.summary(note),
                ]
                if note.subject_uri:
                    lines.extend(("", f"Post: {note.subject_uri}"))
                text = "\n".join(lines)
                if self.details.GetValue() != text:
                    self.details.SetValue(text)
                return
        accounts = {a.account_id: a for a in self.store.list_accounts()}
        acct = accounts.get(item.account_id)
        lines = [
            f"Author: {item.author_display} {item.author_handle}",
            f"Network: {item.network}"
            + (f" ({acct.label})" if acct else ""),
            f"When: {format_timestamp(item.created_at, self.a11y.display_timezone, twelve_hour=self.a11y.post_timestamps_12_hour)}",
            f"Visibility: {item.visibility}",
        ]
        if item.content_warning:
            lines.append(f"Content warning: {item.content_warning}")
        if item.is_repost:
            lines.append(f"Boosted by {item.reblog_by}")
        if item.is_reply:
            lines.append("This is a reply.")
        if item.is_quote:
            lines.append("This post quotes another post.")
        lines.append("")
        lines.append(item.text)
        if item.media:
            lines.append("")
            for m in item.media:
                alt = m.alt_text or "(no alt text -- undescribed)"
                lines.append(f"Media ({m.kind}): {alt}")
        if item.poll:
            lines.append("")
            lines.append(f"Poll, {item.poll.total_votes} votes"
                         + (", you voted" if item.poll.voted else ""))
            for opt in item.poll.options:
                lines.append(f"  {opt.title}: {opt.votes}")
        lines.append("")
        lines.append(
            f"Replies {item.reply_count}, boosts {item.reblog_count}, "
            f"favourites {item.favourite_count}")
        if item.missing_alt_count:
            lines.append(f"Accessibility: {item.missing_alt_count} media "
                         "without alt text.")
        text = "\n".join(lines)
        if self.details.GetValue() != text:
            self.details.SetValue(text)

    def _announce_field(self, delta: int) -> None:
        item = self._current_item()
        if item is None:
            return
        accounts = {a.account_id: a for a in self.store.list_accounts()}
        pairs = read_fields(item, self._post_profile(item.account_id), account=accounts.get(item.account_id),
                            settings=self.a11y)
        if not pairs:
            return
        self._field_index = (self._field_index + delta) % len(pairs)
        label, value = pairs[self._field_index]
        self.announcer.say(f"{label}: {value}", "normal", interrupt=True)

    # -- keyboard dispatch ----------------------------------------------------

    def _on_char_hook(self, event: wx.KeyEvent) -> None:
        chord = keymap_mod.chord_from_event(event)
        if chord and self.keymap.command_for(chord) in ("next_pane", "prev_pane"):
            self._dispatch(self.keymap.command_for(chord))
            return
        # Field navigation only when the list has focus.
        if self.FindFocus() is self.list:
            code = event.GetKeyCode()
            if code == wx.WXK_RETURN and not (event.ControlDown() or event.AltDown() or event.ShiftDown()):
                mapped = self.keymap.command_for(keymap_mod.chord_from_event(event))
                if mapped and self._dispatch(mapped):
                    return
                self.cmd_view_post()
                return
            if code == wx.WXK_LEFT:
                self._announce_field(-1)
                return
            if code == wx.WXK_RIGHT:
                self._announce_field(1)
                return
        chord = keymap_mod.chord_from_event(event)
        command_id = self.keymap.command_for(chord) if chord else None
        if command_id and self._dispatch(command_id):
            return
        event.Skip()

    def _dispatch(self, command_id: str) -> bool:
        if command_id == "repeat_announcement":
            self.announcer.repeat_last()
            return True
        if command_id in ("next_pane", "prev_pane"):
            panes = ([self.accounts, self.timelines, self.list] if self.a11y.ui_mode == "standard"
                     else [self.accounts, self.nav, self.list, self.details])
            focus = self.FindFocus()
            index = panes.index(focus) if focus in panes else -1
            panes[(index + (-1 if command_id == "prev_pane" else 1)) % len(panes)].SetFocus()
            return True
        if command_id.startswith("goto_") and command_id[5:].isdigit():
            index = int(command_id[5:]) - 1
            if 0 <= index < len(self._timeline_scopes):
                scope = self._timeline_scopes[index]
                self._load_scope(scope, self.timelines.GetString(index))
            return True
        handler = {
            "play_media": self.cmd_play_media,
            "compose": self.cmd_compose,
            "reply": self.cmd_reply,
            "quote": self.cmd_quote,
            "repost": self.cmd_repost,
            "favourite": self.cmd_favourite,
            "bookmark": self.cmd_bookmark,
            "open_conversation": self.cmd_open_conversation,
            "open_links": self.cmd_open_links,
            "mark_read": self.cmd_mark_read,
            "command_center": self.cmd_command_center,
            "where_am_i": self.cmd_where_am_i,
            "refresh": self.cmd_refresh,
            "search": self.cmd_search if self.a11y.ui_mode == "standard" else self.cmd_focus_search,
            "help": self.cmd_help,
        }.get(command_id)
        if handler is None:
            return False
        handler()
        return True

    # -- navigation events ----------------------------------------------------

    def _on_nav_changed(self, event) -> None:
        if self._closing or self._updating_navigation:
            return
        node = event.GetItem()
        try:
            data = self.nav.GetItemData(node)
        except RuntimeError:
            return  # tree being torn down
        if not data:
            return
        scope, label = data
        if scope in ("home", "attention", "library", "publishing", "discover", "github", "additional"):
            return  # parent header, not a feed
        self._load_scope(scope, label)

    def _run_search(self) -> None:
        self.last_search = self.search.GetValue().strip()
        self._load_scope("discover:search", "Search Results")

    # -- commands -------------------------------------------------------------

    def cmd_compose(self) -> None:
        self._open_composer()

    def cmd_reply(self) -> None:
        item = self._current_item()
        if item is None:
            self.announcer.error("Select a post to reply to.")
            return
        if self._is_notification_row(item):
            item = self._notification_subject(item)
            if item is None:
                self.announcer.error("This notification does not contain a post to reply to.")
                return
        if item.remote_id.startswith(("instance:", "search:")):
            self.announcer.error("This search result is read-only."
                                 if item.remote_id.startswith("search:")
                                 else "Remote-instance posts are read-only.")
            return
        if item.visibility == "direct":
            self.cmd_direct_message(item)
            return
        self._open_composer(reply_to=item, target_account_id=item.account_id)

    def cmd_quote(self) -> None:
        item = self._current_item()
        if item is None:
            self.announcer.error("Select a post to quote.")
            return
        if self._is_notification_row(item):
            item = self._notification_subject(item)
            if item is None:
                self.announcer.error("This notification does not contain a post to quote.")
                return
        if item.visibility == "direct" or item.remote_id.startswith(("instance:", "chat:", "search:")):
            self.announcer.error("This post cannot be quoted.")
            return
        account = self.store.get_account(item.account_id)
        caps = self.caps.get(item.account_id, item.network)
        if account and account.network == "mastodon" and not caps.supports_quote:
            if not item.uri:
                self.announcer.error("This server does not support native quotes and the post has no link.")
                return
            self._open_composer(
                initial_text=f"RE: {item.uri} ", quote_mode=True,
                target_account_id=item.account_id)
            return
        self._open_composer(
            quote_of=item.remote_id, quote_mode=True,
            target_account_id=item.account_id)

    def _open_composer(
        self, *, reply_to=None, quote_of: str = "", initial_text: str = "",
        quote_mode: bool = False, target_account_id: str | None = None,
    ) -> None:
        accounts = self.store.list_accounts(include_paused=False)
        if target_account_id:
            accounts = [account for account in accounts if account.account_id == target_account_id]
        if not accounts:
            self.announcer.error("Add an account first.")
            return
        caps = {a.account_id: self.caps.get(a.account_id, a.network) for a in accounts}
        dlg = ComposerDialog(self, accounts, caps, store=self.store,
                             reply_to=reply_to, quote_of=quote_of,
                             initial_text=initial_text, quote_mode=quote_mode,
                             selected_account_id=target_account_id or self.selected_account_id)
        self.announcer.say("Composer open. Editor has focus.", "normal")
        if dlg.ShowModal() == wx.ID_OK and dlg.result_draft:
            self._handle_compose_result(
                dlg.result_action, dlg.result_draft,
                schedule_at=getattr(dlg, "result_schedule_at", None))
        dlg.Destroy()

    def _handle_compose_result(self, action: str, draft, *, schedule_at=None) -> None:
        if action == "save":
            self.store.put_draft(draft)
            self.announcer.say("Draft saved.", "normal")
            return
        if action == "schedule":
            self.store.put_draft(draft)
            when = schedule_at or (now_ms() + 60_000)
            for account_id in draft.targets:
                acct = self.store.get_account(account_id)
                self.store.put_plan(PublicationPlan(
                    draft_id=draft.draft_id, account_id=account_id,
                    network=acct.network if acct else "mock",
                    tier="local", scheduled_for=when, state="scheduled"))
            self.announcer.say(
                f"Scheduled for {len(draft.targets)} account(s) at "
                f"{format_timestamp(when, self.a11y.display_timezone)}. The local scheduler runs while the app is "
                "open.", "normal")
            return
        # publish now
        if self.a11y.provide_confirmations:
            if wx.MessageBox(f"Publish this post to {len(draft.targets)} account(s)?",
                             "Publish post", wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION, self) != wx.YES:
                self.store.put_draft(draft)
                self.announcer.say("Post not published. Saved as a draft.", "normal")
                return
        self._publish_now(draft)

    def _publish_now(self, draft) -> None:
        self.store.put_draft(draft)
        if getattr(self, "_publishing", False):
            self.store.put_draft(draft)
            self.announcer.say("Another post is still sending. This post was saved as a draft.", "action")
            return
        targets = []
        for account_id in draft.targets:
            acct = self.store.get_account(account_id)
            if acct is None:
                self.announcer.error(f"Cannot publish: account {account_id} is unavailable.")
                continue
            targets.append((acct, self.caps.get(account_id, acct.network)))
        credentials = self.credentials
        self._publishing = True
        poll_duration = ((draft.poll.expires_at - draft.created) // 1000
                         if draft.poll and draft.poll.expires_at else None)

        def worker():
            results = []
            successful_accounts = []
            for acct, caps in targets:
                try:
                    adapter = adapter_for(acct, credentials)
                    if draft.thread_mode:
                        counter = mastodon_counter if acct.network == "mastodon" else len
                        split = split_thread(draft.text, caps.char_limit, counter=counter)
                        res = publish_thread(
                            adapter, split.texts(), run_id=draft.draft_id,
                            visibility=draft.visibility, content_warning=draft.content_warning,
                            lang=draft.lang, reply_to=draft.in_reply_to,
                            quote_of=draft.quote_of, media=list(draft.media), poll=draft.poll,
                            poll_expires_in=poll_duration,
                            on_progress=lambda index, total, remote, label=acct.label:
                                self.announcer.say(f"{label}: Thread part {index} of {total} sent.", "action"))
                        message = f"Thread sent. {res.total} parts." if res.ok else res.summary()
                        if not res.ok:
                            message += " " + res.results[-1].error_message
                        results.append((acct.label, message, not res.ok))
                        if res.ok:
                            successful_accounts.append(acct.account_id)
                    else:
                        from quill_social.adapters.base import PublishRequest
                        adapter.publish(PublishRequest(
                            text=draft.text, visibility=draft.visibility,
                            content_warning=draft.content_warning, lang=draft.lang,
                            in_reply_to=draft.in_reply_to, quote_of=draft.quote_of,
                            media=list(draft.media), poll=draft.poll,
                            poll_expires_in=poll_duration,
                            idempotency_key=draft.draft_id))
                        results.append((acct.label, "Post sent.", False))
                        successful_accounts.append(acct.account_id)
                except Exception as exc:
                    results.append((acct.label, f"Post failed: {exc}", True))
            wx.CallAfter(self._finish_publish, results, draft, successful_accounts)
        Thread(target=worker, daemon=True, name="social-publish").start()

    def _finish_publish(self, results, draft=None, successful_accounts=()):
        if self._closing:
            return
        self._publishing = False
        if draft is not None:
            if results and len(results) == len(draft.targets) and not any(row[2] for row in results):
                self.store.delete_draft(draft.draft_id)
            else:
                draft.targets = [target for target in draft.targets if target not in successful_accounts]
                self.store.put_draft(draft)
                self.announcer.say("The composition is saved in Drafts. Check successful destinations before resending.", "action")
        self._refresh_from_network(announce=False)
        self._load_scope(self.current_scope, self.current_scope_label, keep_selection=True, announce=False)
        for label, status, failed in results:
            if failed:
                self.announcer.error(f"{label}: {status}")
            else:
                self.announcer.say(f"{label}: {status}", "action")

    def _toggle_flag(self, column: str, verb: str) -> None:
        item = self._current_item()
        if item is None:
            self.announcer.error("Select a post first.")
            return
        if self._is_notification_row(item):
            item = self._notification_subject(item)
            if item is None:
                self.announcer.error("This notification does not contain a post for that action.")
                return
        if item.remote_id.startswith("search:") or (column != "flagged" and (
                item.remote_id.startswith(("instance:", "chat:"))
                or (column == "reblogged" and item.visibility == "direct"))):
            self.announcer.error("This action is not available for this post.")
            return
        new_value = not getattr(item, column)
        key = (item.item_id, column)
        if key in self._pending_reactions:
            self.announcer.say(f"{verb} is already in progress.", "action")
            return
        acct = self.store.get_account(item.account_id)
        if column == "flagged":
            self._finish_reaction(item, column, new_value, None)
            return
        if not acct:
            self.announcer.error("The account for this post is no longer available.")
            return
        self._pending_reactions.add(key)
        credentials = self.credentials
        def operation():
            error = None
            try:
                adapter = adapter_for(acct, credentials)
                method = {"favourited": "set_favourite", "bookmarked": "set_bookmark",
                          "reblogged": "set_reblog"}[column]
                getattr(adapter, method)(item.remote_id, new_value)
            except Exception as exc:
                error = str(exc) or type(exc).__name__
            return error
        if acct.network == "mock":
            self._finish_reaction(item, column, new_value, operation())
        else:
            def worker():
                error = operation()
                if not self._closing:
                    wx.CallAfter(self._finish_reaction, item, column, new_value, error)
            Thread(target=worker, daemon=True, name="social-reaction").start()

    def _finish_reaction(self, item, column, new_value, error) -> None:
        if self._closing:
            return
        self._pending_reactions.discard((item.item_id, column))
        if error:
            label = {"favourited": "favourite", "reblogged": "repost", "bookmarked": "bookmark"}[column]
            self.announcer.error(f"Could not {label} this post: {error}")
            return
        if not self.store.get_item(item.item_id):
            return
        self.store.set_flag(item.item_id, column, new_value)
        setattr(item, column, new_value)
        for visible in self._items:
            if visible.item_id == item.item_id:
                setattr(visible, column, new_value)
        message = {
            "favourited": ("Favourite removed.", "Favourited."),
            "reblogged": ("Repost removed.", "Reposted."),
            "bookmarked": ("Bookmark removed.", "Bookmarked."),
            "flagged": ("Follow-up flag removed.", "Flagged for follow-up."),
        }[column][int(new_value)]
        self.announcer.say(message, "action", interrupt=True)

    def cmd_favourite(self) -> None:
        self._toggle_flag("favourited", "Favourite")

    def cmd_bookmark(self) -> None:
        self._toggle_flag("bookmarked", "Bookmark")

    def cmd_repost(self) -> None:
        if self.a11y.provide_confirmations and self._current_item() is not None:
            if wx.MessageBox("Change the boost/repost state of this post?", "Boost / Repost",
                             wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION, self) != wx.YES:
                return
        self._toggle_flag("reblogged", "Boost")

    def cmd_flag(self) -> None:
        self._toggle_flag("flagged", "Follow-up flag")

    def cmd_mark_read(self) -> None:
        item = self._current_item()
        if item is None:
            return
        if self._is_notification_row(item):
            notification_svc.mark_read(self.store, item.item_id)
        else:
            self.store.set_read(item.item_id, True)
        item.read = True
        self.announcer.say("Marked read.", "normal")

    def cmd_open_conversation(self) -> None:
        item = self._current_item()
        if item is None:
            self.announcer.error("Select a post first.")
            return
        if self._is_notification_row(item):
            item = self._notification_subject(item)
            if item is None:
                self.announcer.error("This notification does not contain a conversation.")
                return
        if item.remote_id.startswith(("instance:", "search:")):
            self.announcer.error("This result does not have a conversation."
                                 if item.remote_id.startswith("search:")
                                 else "Remote-instance posts are read-only. Open the original post to view its conversation.")
            return
        thread = self.store.list_items(
            thread_root=item.thread_root or item.remote_id, account_id=item.account_id, limit=100)
        if not thread:
            thread = [item]
        self._items = sorted(thread, key=lambda x: x.created_at)
        self._render_list()
        self.announcer.say(f"Conversation. {len(self._items)} posts.", "normal")
        account = self.store.get_account(item.account_id)
        if account is None:
            return
        token = object()
        self._conversation_request = token
        scope, selected_account = self.current_scope, self.selected_account_id
        credentials = self.credentials

        def worker():
            try:
                posts = adapter_for(account, credentials).thread(item)
                error = None
            except Exception as exc:
                posts, error = [], str(exc)
            wx.CallAfter(self._finish_conversation, token, scope, selected_account, posts, error)
        Thread(target=worker, daemon=True, name="social-conversation").start()

    def _finish_conversation(self, token, scope, selected_account, posts, error):
        if self._closing or token is not getattr(self, "_conversation_request", None):
            return
        if self.current_scope != scope or self.selected_account_id != selected_account:
            return
        if error:
            self.announcer.error(f"Could not refresh conversation: {error}. Showing cached posts.")
            return
        for post in posts:
            stored = self.store.upsert_item(post)
            if self.store.get_document("home-post", stored.item_id) is None:
                self.store.put_document("timeline-only", stored.item_id, {"source": "conversation"})
        if posts:
            self._items = sorted(posts, key=lambda post: post.created_at)
            self._render_list()
            self.announcer.say(f"Conversation. {len(posts)} posts.", "normal")

    def cmd_open_links(self) -> None:
        item = self._current_item()
        if item is None:
            return
        if self._is_notification_row(item):
            item = self._notification_subject(item) or item
        import re
        urls = re.findall(r"https?://\S+", " ".join((item.text, item.uri)))
        if not urls:
            self.announcer.say("No links in this post.", "normal")
            return
        urls = list(dict.fromkeys(url.rstrip(".,;!?)") for url in urls))
        if len(urls) == 1 and self.a11y.open_single_link_without_dialog:
            self._open_announced_link(urls[0])
            return
        with wx.SingleChoiceDialog(self, "Choose a link to open", "Post links", urls) as dialog:
            if dialog.ShowModal() == wx.ID_OK:
                self._open_announced_link(urls[dialog.GetSelection()])

    def _open_announced_link(self, url):
        try:
            opened = webbrowser.open(url)
        except Exception as exc:
            self.announcer.error(f"Could not open link: {exc}")
            return
        if opened:
            self.announcer.say("Opened link.", "action")
        else:
            self.announcer.error("Could not open link. Check your default browser.")

    def cmd_refresh(self) -> None:
        if self.current_scope == "attention:messages" or self.timeline_library.get(self.current_scope):
            specs = self._message_specs() if self.current_scope == "attention:messages" else [self.timeline_library.get(self.current_scope)]
            for spec in specs:
                self._request_extra_timeline(spec, announce=True)
            return
        self._refresh_from_network(announce=True)
        self._load_scope(self.current_scope, self.current_scope_label)

    def cmd_focus_search(self) -> None:
        self._search_sizer.ShowItems(True)
        self._posts_panel.Layout()
        self.search.SetFocus()
        self.announcer.say("Search. Type a query and press Enter.", "normal")

    def cmd_catchup(self) -> None:
        self._load_scope("discover:catchup", "Catch Up")

    # -- analytics, safety, AI, ecosystem (PRD 33, 27, 21, 20) ----------------

    def cmd_analytics(self) -> None:
        """Show measured metrics as accessible data tables (PRD 33.2)."""
        all_items = self.store.list_items(limit=1000)
        own = [it for it in all_items if "you" in it.author_handle.lower()]
        report = analytics_svc.compute_metrics(
            own, all_items, plans=self.store.list_plans())
        blocks = [analytics_svc.to_markdown(t) for t in report.tables()]
        self._show_text("Insights", "\n\n".join(blocks))
        self.announcer.say(
            f"Insights. {report.posts_sent} posts sent, "
            f"{report.replies_received} replies received.", "normal")

    def cmd_safety_center(self) -> None:
        """Open the unified Safety Center (PRD 27.1)."""
        self._modal(SafetyCenterDialog(self, self.store))
        self.announcer.say("Safety Center closed.", "normal")

    def cmd_notification_policies(self) -> None:
        """Open the notification policy editor (PRD 25)."""
        self._modal(NotificationPoliciesDialog(self, self.store))

    def cmd_plugins(self) -> None:
        """Open the plugin manager (PRD 34)."""
        self._modal(PluginManagerDialog(self, self.store))

    def cmd_outbox(self) -> None:
        """Open the offline outbox (PRD 32)."""
        self._modal(OutboxDialog(self, self.store))

    def cmd_drafts(self) -> None:
        """Manage drafts; open a chosen draft in the composer (PRD 15.2)."""
        dlg = DraftsDialog(self, self.store)
        dlg.ShowModal()
        chosen = getattr(dlg, "chosen_draft_id", None)
        dlg.Destroy()
        if chosen:
            self._open_composer_for_draft(chosen)

    def cmd_agenda(self) -> None:
        """Open the accessible publishing calendar (PRD 18.5)."""
        self._modal(AgendaDialog(self, self.store))

    def cmd_queue_schedule(self) -> None:
        """Edit a posting queue schedule (PRD 18.4)."""
        self._modal(QueueScheduleDialog(self, self.store))

    def cmd_approvals(self) -> None:
        """Open the approvals workflow (PRD 18.8)."""
        self._modal(ApprovalsDialog(self, self.store))

    def cmd_play_media(self) -> None:
        """Play the focused post's media in the accessible player (PRD 19)."""
        item = self._current_item()
        if self._is_notification_row(item):
            item = self._notification_subject(item)
        if item is None or not item.media:
            self.announcer.say("This post has no media to play.", "normal")
            return
        self._modal(MediaPlayerDialog(self, item.media))

    def _open_composer_for_draft(self, draft_id: str) -> None:
        draft = self.store.get_draft(draft_id)
        if draft is None:
            return
        accounts = self.store.list_accounts(include_paused=False)
        caps = {a.account_id: self.caps.get(a.account_id, a.network) for a in accounts}
        dlg = ComposerDialog(
            self, accounts, caps, store=self.store,
            quote_of=draft.quote_of, initial_text=draft.text,
            quote_mode=bool(draft.quote_of),
            selected_account_id=draft.targets[0] if draft.targets else None)
        try:
            from types import SimpleNamespace

            from quill_social.ui.composer import _POLL_DURATIONS
            dlg._reply_to = SimpleNamespace(remote_id=draft.in_reply_to) if draft.in_reply_to else None
            dlg._media = list(draft.media)
            dlg._sync_media_list()
            dlg.visibility.SetStringSelection(draft.visibility)
            if draft.poll:
                dlg.poll_toggle.SetValue(True)
                dlg._poll_options = [option.title for option in draft.poll.options]
                dlg.poll_multiple.SetValue(draft.poll.multiple)
                remaining = max(0, (draft.poll.expires_at or draft.created) - draft.created)
                duration = min(range(len(_POLL_DURATIONS)),
                               key=lambda index: abs(_POLL_DURATIONS[index][1] - remaining))
                dlg.poll_duration.SetSelection(duration)
                dlg._sync_poll_options()
                dlg._sync_poll_enabled()
            if draft.content_warning:
                dlg.cw.SetValue(draft.content_warning)
            dlg.thread_mode.SetValue(draft.thread_mode)
            for index, account in enumerate(accounts):
                dlg.accounts_box.Check(index, account.account_id in draft.targets)
            dlg._refresh_report()
        except Exception as exc:
            dlg.Destroy()
            self.announcer.error(f"Could not restore this draft: {exc}. The saved draft is unchanged.")
            return
        if dlg.ShowModal() == wx.ID_OK and dlg.result_draft:
            dlg.result_draft.lang = draft.lang
            self.store.delete_draft(draft_id)
            self._handle_compose_result(
                dlg.result_action, dlg.result_draft,
                schedule_at=getattr(dlg, "result_schedule_at", None))
        dlg.Destroy()

    def _modal(self, dlg) -> None:
        """Show a dialog modally and destroy it. Central so teardown is uniform."""
        try:
            dlg.ShowModal()
        finally:
            dlg.Destroy()

    def cmd_summarize_feed(self) -> None:
        """AI summary of the visible feed that always lists its sources (PRD 12.6)."""
        if not self._items:
            self.announcer.error("Nothing to summarize here.")
            return
        summary = ai_understand.summarize(self._items[:25])
        text = (f"{summary.text}\n\nSources: {len(summary.sources)} posts. "
                "AI output is a draft; every source is listed and inspectable.")
        self._show_text("Summary", text)
        self.announcer.say(summary.text[:200], "normal", interrupt=True)

    def cmd_accessibility_check(self) -> None:
        """Run the accessibility assistant on the focused post (PRD 21.5)."""
        item = self._current_item()
        if item is None:
            self.announcer.error("Select a post first.")
            return
        issues = ai_a11y.check_item(item)
        if not issues:
            self.announcer.say("No accessibility issues found on this post.", "normal")
            self._show_text("Accessibility check", "No issues found.")
            return
        lines = [f"{i.severity.upper()}: {i.message}"
                 + (f"\n  Suggestion: {i.suggestion}" if i.suggestion else "")
                 for i in issues]
        self._show_text("Accessibility check", "\n".join(lines))
        self.announcer.say(f"{len(issues)} accessibility issue(s). "
                           + issues[0].message, "normal", interrupt=True)

    def cmd_send_to_quill(self) -> None:
        """Export the focused post or its conversation to QUILL markdown (PRD 20.1)."""
        item = self._current_item()
        if item is None:
            self.announcer.error("Select a post first.")
            return
        thread = self.store.list_items(
            thread_root=item.thread_root or item.remote_id, account_id=item.account_id, limit=100) or [item]
        thread = sorted(thread, key=lambda x: x.created_at)
        intent = ecosystem_svc.send_to_quill(thread, title=f"Thread by {item.author_display}")
        export_dir = self.data_dir / "exports"
        export_dir.mkdir(exist_ok=True)
        path = export_dir / f"{item.item_id}.md"
        path.write_text(intent.markdown, encoding="utf-8")
        self.announcer.say(f"{intent.describe()} Saved to {path}.", "normal")
        self._show_text("Send to QUILL", f"{intent.describe()}\n\nSaved to:\n{path}\n\n"
                        + intent.markdown)

    def _show_text(self, title: str, text: str) -> None:
        with TextReportDialog(self, title, text) as dialog:
            dialog.ShowModal()

    def cmd_command_center(self) -> None:
        commands = self._build_commands()
        dlg = CommandPalette(self, commands)
        if dlg.ShowModal() == wx.ID_OK and dlg.chosen():
            dlg.Destroy()
            dlg.chosen().handler()
            return
        dlg.Destroy()

    def cmd_where_am_i(self) -> None:
        item = self._current_item()
        idx = self.list.GetFirstSelected()
        acct = self.store.get_account(self.selected_account_id) if self.selected_account_id else None
        if item:
            acct = self.store.get_account(item.account_id)
        w = WhereAmI(
            workspace="Personal",
            account=acct.label if acct else "",
            network=item.network if item else (acct.network if acct else ""),
            feed=self.current_scope_label,
            position=idx + 1 if idx >= 0 else 0,
            total=len(self._items),
            unread=sum(1 for it in self._items if not it.read),
            sort_state="newest first",
            post_type=("reply" if item and item.is_reply else
                       "boost" if item and item.is_repost else "post") if item else "",
            visibility=item.visibility if item else "",
            media_state=(f"{len(item.media)} media" if item and item.media else ""),
            moderation_state=(", ".join(item.moderation_labels)
                              if item and item.moderation_labels else ""),
        )
        text = w.announce()
        self.announcer.say(text, "normal", interrupt=True)
        self.SetStatusText(text)

    def cmd_help(self) -> None:
        HelpDialog(self, self.keymap).ShowModal()

    def _build_commands(self) -> list[Command]:
        has_item = self._current_item() is not None
        km = self.keymap
        commands = [
            Command("repeat_announcement", "Repeat last announcement", self.announcer.repeat_last,
                    shortcut=km.chord_for("repeat_announcement")),
            Command("direct_message", "New direct message", self.cmd_direct_message),
            Command("open_timeline", "Open additional timeline", self.cmd_open_timeline),
            Command("close_timeline", "Close added timeline", self.cmd_close_timeline),
            Command("timeline_announcements", "Toggle timeline announcements", self.cmd_toggle_timeline_announcements),
            Command("edit_profile", "Edit my profile", self.cmd_edit_profile),
            Command("create_settings_backup", "Create settings backup", self.cmd_create_settings_backup),
            Command("restore_settings_backup", "Restore settings backup", self.cmd_restore_settings_backup),
            Command("export_timeline", "Export current timeline", self.cmd_export_timeline),
            Command("export_all_timelines", "Export all timelines", self.cmd_export_all_timelines),
            Command("standard_mode", "Switch to Standard mode", lambda: self.set_ui_mode("standard")),
            Command("advanced_mode", "Switch to Advanced mode", lambda: self.set_ui_mode("advanced")),
            Command("view_post", "View post", self.cmd_view_post, is_available=lambda: has_item),
            Command("compose", "New post", self.cmd_compose,
                    synonyms=["write", "toot", "skeet"], shortcut=km.chord_for("compose")),
            Command("reply", "Reply", self.cmd_reply, is_available=lambda: has_item,
                    unavailable_reason="Select a post to reply to.",
                    shortcut=km.chord_for("reply")),
            Command("quote", "Quote post", self.cmd_quote, is_available=lambda: has_item,
                    unavailable_reason="Select a post to quote.",
                    shortcut=km.chord_for("quote")),
            Command("repost", "Boost / repost", self.cmd_repost,
                    is_available=lambda: has_item, shortcut=km.chord_for("repost")),
            Command("favourite", "Favourite", self.cmd_favourite,
                    is_available=lambda: has_item, shortcut=km.chord_for("favourite")),
            Command("bookmark", "Bookmark", self.cmd_bookmark,
                    is_available=lambda: has_item, shortcut=km.chord_for("bookmark")),
            Command("open_conversation", "Open conversation",
                    self.cmd_open_conversation, is_available=lambda: has_item,
                    shortcut=km.chord_for("open_conversation")),
            Command("catchup", "Catch up", self.cmd_catchup, synonyms=["digest"]),
            Command("accessibility_check", "Accessibility check",
                    self.cmd_accessibility_check, is_available=lambda: has_item,
                    synonyms=["a11y", "alt text", "audit"]),
            Command("send_to_quill", "Send to QUILL", self.cmd_send_to_quill,
                    is_available=lambda: has_item, synonyms=["export", "document"]),
            Command("summarize_feed", "Summarize this feed", self.cmd_summarize_feed,
                    synonyms=["ai", "summary", "digest"]),
            Command("analytics", "Insights and analytics", self.cmd_analytics,
                    synonyms=["metrics", "stats"]),
            Command("safety_center", "Safety center", self.cmd_safety_center,
                    synonyms=["moderation", "filters", "mute", "block"]),
            Command("notification_policies", "Notification policies",
                    self.cmd_notification_policies, synonyms=["quiet hours", "digest"]),
            Command("plugins", "Plugins", self.cmd_plugins,
                    synonyms=["extensions", "add-ons"]),
            Command("outbox", "Outbox", self.cmd_outbox, synonyms=["offline", "pending"]),
            Command("drafts", "Drafts", self.cmd_drafts, synonyms=["saved posts"]),
            Command("agenda", "Agenda / calendar", self.cmd_agenda,
                    synonyms=["schedule", "calendar", "queue"]),
            Command("queue_schedule", "Queue schedule", self.cmd_queue_schedule,
                    synonyms=["posting times", "slots"]),
            Command("approvals", "Approvals", self.cmd_approvals,
                    synonyms=["review", "workflow"]),
            Command("play_media", "Play media", self.cmd_play_media,
                    is_available=lambda: bool(has_item and self._current_item()
                                              and self._current_item().media),
                    synonyms=["audio", "video", "player"]),
            Command("refresh", "Refresh", self.cmd_refresh,
                    shortcut=km.chord_for("refresh")),
            Command("search", "Search",
                    self.cmd_search if self.a11y.ui_mode == "standard" else self.cmd_focus_search,
                    shortcut=km.chord_for("search")),
            Command("where_am_i", "Where am I", self.cmd_where_am_i,
                    synonyms=["context"], shortcut=km.chord_for("where_am_i")),
            Command("add_account", "Add account",
                    lambda: self._on_add_account(None)),
            Command("preferences", "Preferences",
                    lambda: self._on_preferences(None)),
            Command("help", "Help", self.cmd_help, shortcut=km.chord_for("help")),
        ]
        if self.a11y.ui_mode == "standard":
            advanced = {"send_to_quill", "summarize_feed", "analytics", "plugins",
                        "agenda", "queue_schedule", "approvals", "catchup"}
            return [command for command in commands if command.command_id not in advanced]
        return commands

    # -- dialogs --------------------------------------------------------------

    def _on_add_account(self, _e) -> None:
        account = AddAccountDialog(self).run_and_apply(self)
        if account is not None:
            self._populate_accounts()
            self._select_account(account.account_id)
            self.accounts.SetFocus()
            self._refresh_from_network()
            self._load_scope(self.current_scope, self.current_scope_label)

    def _on_preferences(self, _e) -> None:
        PreferencesDialog(self, self.a11y).run_and_apply(self)

    def _on_about(self, _e) -> None:
        wx.MessageBox(
            f"{__title__} {__version__}\n\n"
            "Accessibility-first social workspace for Mastodon and Bluesky.\n"
            "Every conversation within reach.",
            "About QUILL Social", wx.OK | wx.ICON_INFORMATION, self)

    def _on_close(self, _e) -> None:
        if getattr(self, "_publishing", False):
            self.announcer.say("A post is still sending. Wait for the result before closing.", "action")
            if _e and _e.CanVeto():
                _e.Veto()
            return
        current = self._current_item()
        try:
            (self.data_dir / "reading-position.json").write_text(json.dumps({
                "account_id": self.selected_account_id, "scope": self.current_scope,
                "item_id": current.item_id if current else None,
            }), encoding="utf-8")
        except OSError:
            logging.getLogger(__name__).exception("Could not save reading position")
        self._closing = True
        self._marker_timer.Stop()
        self._announcement_timer.Stop()
        self._global_hotkeys.close()
        self._tray.RemoveIcon()
        self._tray.Destroy()
        try:
            if getattr(self, "_sched_timer", None):
                self._sched_timer.Stop()
        except Exception:
            pass
        try:
            a11y_mod.save(self.data_dir, self.a11y)
            keymap_mod.save(self.data_dir, self.keymap)
            self._backup_settings()
        except OSError:
            logging.getLogger(__name__).exception("Could not save settings on exit")
        try:
            self.store.close()
        except sqlite3.Error:
            logging.getLogger(__name__).exception("Could not close database")
        self.Destroy()


# -- small dialogs ------------------------------------------------------------


class TextReportDialog(wx.Dialog):
    """A read-only, screen-reader-reviewable text report (analytics, summaries).

    Used for any command that produces a block of text the user reviews rather
    than acts on. The text control gets focus so a screen reader lands in the
    content, and the whole report is selectable and copyable.
    """

    def __init__(self, parent, title: str, text: str):
        super().__init__(parent, title=title,
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        sizer = wx.BoxSizer(wx.VERTICAL)
        ctrl = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2)
        ctrl.SetName(title)
        ctrl.SetValue(text)
        sizer.Add(ctrl, 1, wx.EXPAND | wx.ALL, 8)
        sizer.Add(self.CreateButtonSizer(wx.OK), 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizer(sizer)
        self.SetSize((620, 520))
        ctrl.SetFocus()
        ctrl.SetInsertionPoint(0)


class HelpDialog(wx.Dialog):
    """Context help listing every command and its current shortcut (PRD 10.4)."""

    def __init__(self, parent, keymap):
        super().__init__(parent, title="QUILL Social Help",
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        sizer = wx.BoxSizer(wx.VERTICAL)
        text = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_READONLY)
        text.SetName("Help")
        lines = [
            "QUILL Social -- keyboard guide",
            "",
            "Accounts: Up/Down selects Unified Home or an account.",
            "View menu: switch between Standard and Advanced mode.",
            "Standard: Accounts, Timelines and Posts, with File, Timeline, Post, Navigate, View and Tools menus.",
            "Advanced mode shows the publishing, creation and integration tools.",
            "Advanced: Accounts, Navigation tree, Timeline and Details.",
            "F6 / Shift+F6: move between the visible panes.",
            "Up/Down: previous/next post.",
            "Left/Right: read the previous/next field of the focused post.",
            "Enter: open the focused post in Details.",
            "",
            "Shortcuts (remappable in Preferences):",
        ]
        for cmd, chord in sorted(keymap.as_dict().items()):
            lines.append(f"  {cmd.replace('_', ' ')}: {chord}")
        lines += [
            "",
            "The demo timeline is a local mock network so you can try every "
            "feature before adding a real account. Use File > Add Account to "
            "connect Mastodon or Bluesky.",
        ]
        text.SetValue("\n".join(lines))
        sizer.Add(text, 1, wx.EXPAND | wx.ALL, 8)
        sizer.Add(self.CreateButtonSizer(wx.OK), 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizer(sizer)
        self.SetSize((560, 520))


# Common Mastodon instances offered in the Add Account picker. The field is an
# editable combo box, so this is a convenience list, not a restriction -- type
# any server. Grows over time.
INSTANCE_PRESETS = [
    "caneandable.social",
    "leaseysocial.com",
    "mastodon.online",
    "mastodon.social",
    "tweesecake.social",
]

_NETWORK_GUIDANCE = {
    "mastodon": (
        "Mastodon: pick or type your server, then choose Sign in with browser. "
        "Your browser opens the server's approval page; approve access, copy the "
        "code it shows, paste it below, and choose Finish sign-in. No app setup "
        "needed."
    ),
    "bluesky": (
        "Bluesky: set the server to bsky.social (or your PDS) and your full "
        "handle (for example name.bsky.social). Choose Open app password page, "
        "create an app password, and paste it below -- not your main password."
    ),
    "mock": (
        "Mock: a local demo network. No server or credential is needed; it is "
        "for trying the app offline."
    ),
}

_BLUESKY_APP_PASSWORDS_URL = "https://bsky.app/settings/app-passwords"


class AddAccountDialog(wx.Dialog):
    """Add an account with an in-app OAuth browser sign-in (PRD 11.1, 31.1).

    Mastodon uses the accessible out-of-band OAuth flow: Sign in with browser
    registers the app with the server (once, cached), opens the approval page,
    and -- after you paste the code -- exchanges it for an access token. Bluesky
    uses an app password. Either way the secret goes to the OS credential vault,
    never the database, and Verify connection tests it before you commit.
    """

    def __init__(self, parent):
        super().__init__(parent, title="Add Account",
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self._frame = parent
        self._oauth: tuple[str, str, str] | None = None  # instance, client_id, secret
        self._token = ""  # access token obtained via OAuth

        sizer = wx.BoxSizer(wx.VERTICAL)

        sizer.Add(wx.StaticText(self, label="&Network:"), 0, wx.LEFT | wx.TOP, 6)
        self.network = wx.Choice(self, choices=["mastodon", "bluesky", "mock"])
        self.network.SetName("Network")
        self.network.SetSelection(0)
        sizer.Add(self.network, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)

        sizer.Add(wx.StaticText(self, label="&Server / instance:"),
                  0, wx.LEFT | wx.TOP, 6)
        self.instance = wx.ComboBox(
            self, value=INSTANCE_PRESETS[0], choices=INSTANCE_PRESETS,
            style=wx.CB_DROPDOWN)
        self.instance.SetName("Server or instance")
        sizer.Add(self.instance, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)

        sizer.Add(wx.StaticText(self, label="&Handle:"), 0, wx.LEFT | wx.TOP, 6)
        self.handle = wx.TextCtrl(self)
        self.handle.SetName("Handle")
        sizer.Add(self.handle, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)

        # OAuth row: sign-in button, code entry, finish button.
        self.signin_btn = wx.Button(self, label="&Sign in with browser")
        sizer.Add(self.signin_btn, 0, wx.LEFT | wx.RIGHT | wx.TOP, 6)

        sizer.Add(wx.StaticText(self, label="Authorization &code (from the browser):"),
                  0, wx.LEFT | wx.TOP, 6)
        crow = wx.BoxSizer(wx.HORIZONTAL)
        self.code = wx.TextCtrl(self)
        self.code.SetName("Authorization code")
        self.code.Enable(False)
        crow.Add(self.code, 1, wx.RIGHT, 6)
        self.finish_btn = wx.Button(self, label="&Finish sign-in")
        self.finish_btn.Enable(False)
        crow.Add(self.finish_btn, 0)
        sizer.Add(crow, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)

        sizer.Add(wx.StaticText(
            self, label="Or paste an access &token / app password:"),
            0, wx.LEFT | wx.TOP, 6)
        self.secret = wx.TextCtrl(self, style=wx.TE_PASSWORD)
        self.secret.SetName("Access token or app password")
        sizer.Add(self.secret, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)

        self.guidance = wx.StaticText(self, label=_NETWORK_GUIDANCE["mastodon"])
        self.guidance.SetName("Guidance")
        self.guidance.Wrap(430)
        sizer.Add(self.guidance, 0, wx.ALL, 6)

        vrow = wx.BoxSizer(wx.HORIZONTAL)
        self.verify_btn = wx.Button(self, label="&Verify connection")
        vrow.Add(self.verify_btn, 0, wx.RIGHT, 8)
        self.status = wx.StaticText(self, label="")
        self.status.SetName("Status")
        vrow.Add(self.status, 1, wx.ALIGN_CENTER_VERTICAL)
        sizer.Add(vrow, 0, wx.EXPAND | wx.ALL, 6)

        sizer.Add(self.CreateButtonSizer(wx.OK | wx.CANCEL),
                  0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizer(sizer)
        self.SetSize((480, 560))

        self.network.Bind(wx.EVT_CHOICE, self._on_network)
        self.signin_btn.Bind(wx.EVT_BUTTON, self._on_signin)
        self.finish_btn.Bind(wx.EVT_BUTTON, self._on_finish)
        self.verify_btn.Bind(wx.EVT_BUTTON, self._on_verify)

    # -- state helpers --------------------------------------------------------

    def _temp_account(self) -> Account:
        return Account(
            network=self.network.GetStringSelection(),
            handle=self.handle.GetValue().strip(),
            display_name=self.handle.GetValue().strip(),
            instance=self.instance.GetValue().strip(),
        )

    def _effective_secret(self) -> str:
        """The OAuth token if we have one, else whatever was pasted."""
        return self._token or self.secret.GetValue().strip()

    def _set_status(self, text: str) -> None:
        self.status.SetLabel(text)
        self.Layout()

    def _on_network(self, _e) -> None:
        net = self.network.GetStringSelection()
        self.guidance.SetLabel(_NETWORK_GUIDANCE.get(net, ""))
        self.guidance.Wrap(430)
        self._oauth = None
        self._token = ""
        self.code.SetValue("")
        self.code.Enable(False)
        self.finish_btn.Enable(False)
        self._set_status("")
        if net == "mastodon":
            self.signin_btn.SetLabel("&Sign in with browser")
            self.signin_btn.Enable(True)
            if self.instance.GetValue() in ("", "bsky.social"):
                self.instance.SetValue(INSTANCE_PRESETS[0])
        elif net == "bluesky":
            self.signin_btn.SetLabel("Open app &password page")
            self.signin_btn.Enable(True)
            self.instance.SetValue("bsky.social")
        else:
            self.signin_btn.SetLabel("&Sign in with browser")
            self.signin_btn.Enable(False)
        self.Layout()

    # -- OAuth flow -----------------------------------------------------------

    def _on_signin(self, _e) -> None:
        net = self.network.GetStringSelection()
        if net == "bluesky":
            webbrowser.open(_BLUESKY_APP_PASSWORDS_URL)
            self._set_status(
                "Opened Bluesky app passwords. Create one and paste it below "
                "with your handle.")
            return
        if net != "mastodon":
            self._set_status("This network needs no browser sign-in.")
            return
        instance = self.instance.GetValue().strip()
        if not instance:
            self._set_status("Enter your Mastodon server first.")
            return
        self._set_status(f"Registering with {instance}...")
        wx.SafeYield(self)
        try:
            client_id, client_secret = oauth.register_app(
                instance, self._frame.data_dir)
            url = oauth.auth_url(instance, client_id, client_secret)
        except Exception as exc:  # noqa: BLE001 - report any server/client failure
            self._set_status(f"Sign-in could not start: {exc}")
            return
        self._oauth = (instance, client_id, client_secret)
        webbrowser.open(url)
        self.code.Enable(True)
        self.finish_btn.Enable(True)
        self.code.SetFocus()
        self._set_status(
            "Browser opened. Approve access, copy the code it shows, paste it in "
            "the Authorization code field, and choose Finish sign-in.")

    def _on_finish(self, _e) -> None:
        if not self._oauth:
            self._set_status("Choose Sign in with browser first.")
            return
        code = self.code.GetValue().strip()
        if not code:
            self._set_status("Paste the authorization code from the browser.")
            return
        instance, client_id, client_secret = self._oauth
        self._set_status("Exchanging the code for an access token...")
        wx.SafeYield(self)
        try:
            self._token = oauth.exchange_code(
                instance, client_id, client_secret, code)
        except Exception as exc:  # noqa: BLE001 - surface auth failures to the user
            self._set_status(f"Could not get a token: {exc}")
            return
        self._set_status(
            "Signed in. Choose Verify connection to test it, or OK to add the "
            "account.")

    def _on_verify(self, _e) -> None:
        """Test the credential against the live network without persisting it."""
        net = self.network.GetStringSelection()
        if net == "mock":
            self._set_status("Mock network needs no credential.")
            return
        secret = self._effective_secret()
        if not secret:
            self._set_status("Sign in, or paste a token / app password first.")
            return
        if net == "bluesky" and not self.handle.GetValue().strip():
            self._set_status("Bluesky needs your handle to sign in.")
            return
        tmp = InMemoryCredentialStore()
        acct = self._temp_account()
        tmp.store(net, acct.account_id, secret)
        self._set_status("Connecting...")
        wx.SafeYield(self)
        try:
            adapter = adapter_for(acct, tmp)
            items = adapter.home_timeline(limit=1)
            self._set_status(
                f"Connected to {net}. Fetched {len(items)} post(s). "
                "Choose OK to add the account.")
        except AdapterError as exc:
            self._set_status(f"Could not connect: {exc}")
        except Exception as exc:  # noqa: BLE001 - surface any client error to the user
            self._set_status(f"Connection error: {exc}")

    def run_and_apply(self, frame) -> Account | None:
        added = None
        if self.ShowModal() == wx.ID_OK and (
            self.handle.GetValue().strip() or self.instance.GetValue().strip()):
            acct = self._temp_account()
            frame.store.put_account(acct)
            frame.caps.seed_from_network(acct.account_id, acct.network)
            secret = self._effective_secret()
            if secret and acct.network != "mock":
                try:
                    frame.credentials.store(acct.network, acct.account_id, secret)
                    if isinstance(frame.credentials, InMemoryCredentialStore):
                        frame.announcer.error(
                            f"Added {acct.label} for this session only. Secure credential "
                            "storage is unavailable; you will need to sign in again after closing."
                        )
                    else:
                        frame.announcer.say(f"Added {acct.label}; credential stored securely.", "normal")
                except Exception:
                    frame.announcer.say(
                        f"Added {acct.label}; could not store the credential.",
                        "normal")
            else:
                frame.announcer.say(f"Added {acct.label}.", "normal")
            added = acct
        self.Destroy()
        return added


class PreferencesDialog(wx.Dialog):
    """Presentation and reading preferences, using native notebook pages."""

    def __init__(self, parent, settings):
        super().__init__(parent, title="Preferences", style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self._settings = settings
        outer = wx.BoxSizer(wx.VERTICAL)
        self.notebook = wx.Notebook(self)
        general = wx.Panel(self.notebook)
        reading = wx.Panel(self.notebook)
        self.notebook.AddPage(general, "General")
        self.notebook.AddPage(reading, "Reading")
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(general, label="Interface &mode:"), 0, wx.ALL, 6)
        self.ui_mode = wx.Choice(general, choices=["Standard", "Advanced"])
        self.ui_mode.SetName("Interface mode")
        self.ui_mode.SetSelection(1 if settings.ui_mode == "advanced" else 0)
        sizer.Add(self.ui_mode, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)
        sizer.Add(wx.StaticText(general, label="Display time &zone:"), 0, wx.ALL, 6)
        self.display_timezone = wx.Choice(general, choices=["System timezone", "UTC"])
        self.display_timezone.SetName("Display time zone")
        self.display_timezone.SetSelection(1 if settings.display_timezone == "utc" else 0)
        sizer.Add(self.display_timezone, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)
        sizer.Add(wx.StaticText(general, label="Announcement verbosity:"),
                  0, wx.ALL, 6)
        self.verbosity = wx.Choice(general, choices=["minimal", "normal", "verbose"])
        self.verbosity.SetName("Verbosity")
        self.verbosity.SetStringSelection(settings.verbosity)
        sizer.Add(self.verbosity, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)
        self.high_contrast = wx.CheckBox(general, label="High contrast")
        self.high_contrast.SetValue(settings.high_contrast)
        sizer.Add(self.high_contrast, 0, wx.ALL, 6)
        self.speak_network = wx.CheckBox(general, label="Speak network on each row")
        self.speak_network.SetValue(settings.speak_network_prefix)
        sizer.Add(self.speak_network, 0, wx.ALL, 6)
        self.speak_engagement = wx.CheckBox(
            general, label="Speak engagement counts on each row")
        self.speak_engagement.SetValue(settings.speak_engagement)
        sizer.Add(self.speak_engagement, 0, wx.ALL, 6)
        accounts_button = wx.Button(general, label="Manage Accounts…")
        accounts_button.Bind(wx.EVT_BUTTON, lambda event: self._manage_accounts(parent))
        sizer.Add(accounts_button, 0, wx.ALL, 6)
        general.SetSizer(sizer)
        reading_sizer = wx.BoxSizer(wx.VERTICAL)
        self._reading_controls = {}
        for name, label in (
            ("reverse_timelines", "Show newest posts at bottom"),
            ("condense_mentions", "Condense multiple leading mentions in post rows"),
            ("exclude_web_addresses", "Exclude web addresses from post rows"),
            ("post_timestamps_relative", "Show post times as relative times"),
            ("post_timestamps_12_hour", "Show absolute post times in 12 hour format"),
            ("announce_read_state", "Include read or unread state in post rows"),
        ):
            control = wx.CheckBox(reading, label=label)
            control.SetName(label)
            control.SetValue(getattr(settings, name))
            self._reading_controls[name] = control
            reading_sizer.Add(control, 0, wx.ALL, 6)
        reading_sizer.Add(wx.StaticText(reading, label=(
            "Post row preferences also apply to field navigation. "
            "The full post remains available in View post and exports.")), 0, wx.ALL, 6)
        reading.SetSizer(reading_sizer)
        self.composition_preferences = CompositionPreferencesPanel(self.notebook, settings)
        self.navigation_preferences = NavigationPreferencesPanel(
            self.notebook, settings.navigation, parent.store.list_accounts(), parent.selected_account_id)
        self.account_reading = AccountReadingPanel(
            self.notebook, settings, parent.store.list_accounts(), parent.selected_account_id)
        self.behavior_preferences = BehaviorPanel(self.notebook, settings, parent)
        self.shortcuts_preferences = ShortcutsPanel(self.notebook, parent.keymap, load_global_bindings(parent.data_dir))
        for panel, title in ((self.composition_preferences, "Composition"),
                             (self.navigation_preferences, "Timelines and fields"),
                             (self.account_reading, "Account reading"),
                             (self.behavior_preferences, "Behavior and cache"),
                             (self.shortcuts_preferences, "Shortcuts")):
            self.notebook.AddPage(panel, title)
        outer.Add(self.notebook, 1, wx.EXPAND | wx.ALL, 6)
        outer.Add(self.CreateButtonSizer(wx.OK | wx.CANCEL),
                  0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizerAndFit(outer)
        self.SetSize((820, 650))

    def _manage_accounts(self, frame):
        with AccountPreferencesDialog(self, frame) as dialog:
            dialog.ShowModal()
            if dialog.changed:
                if frame.selected_account_id and not frame.store.get_account(frame.selected_account_id):
                    frame.selected_account_id = None
                frame._populate_accounts()
                frame._populate_nav()
                frame._load_scope(frame.current_scope, frame.current_scope_label, keep_selection=True)

    def run_and_apply(self, frame) -> None:
        if self.ShowModal() == wx.ID_OK:
            try:
                keymap, global_bindings = self.shortcuts_preferences.collect()
            except ValueError as exc:
                wx.MessageBox(str(exc), "Preferences", wx.OK | wx.ICON_ERROR, self)
                self.run_and_apply(frame)
                return
            self.composition_preferences.apply(self._settings)
            self.account_reading.apply(self._settings)
            self.behavior_preferences.apply(self._settings)
            self._settings.navigation = self.navigation_preferences.get_value()
            self._settings.ui_mode = "advanced" if self.ui_mode.GetSelection() == 1 else "standard"
            self._settings.display_timezone = "utc" if self.display_timezone.GetSelection() == 1 else "system"
            self._settings.verbosity = self.verbosity.GetStringSelection()
            self._settings.high_contrast = self.high_contrast.GetValue()
            self._settings.speak_network_prefix = self.speak_network.GetValue()
            self._settings.speak_engagement = self.speak_engagement.GetValue()
            for name, control in self._reading_controls.items():
                setattr(self._settings, name, control.GetValue())
            frame._backup_settings()
            a11y_mod.save(frame.data_dir, self._settings)
            frame.keymap = keymap
            keymap_mod.save(frame.data_dir, keymap)
            frame._build_menu()
            save_global_bindings(frame.data_dir, global_bindings)
            for error in frame._global_hotkeys.apply(global_bindings):
                frame.announcer.error(error)
            frame._backup_settings()
            frame.announcer.set_verbosity(self._settings.verbosity)
            a11y_mod.apply_to_frame(frame, self._settings)
            frame._apply_ui_mode()
            frame._load_scope(frame.current_scope, frame.current_scope_label, keep_selection=True)
            frame.announcer.say("Preferences saved.", "normal")
        self.Destroy()


def _make_credential_store():
    """Prefer the OS credential vault; fall back to an in-memory store (PRD 31.1).

    Secrets never touch the database -- only an opaque reference is persisted.
    """
    try:
        if WindowsCredentialManagerStore.available():
            return WindowsCredentialManagerStore()
    except Exception:
        pass
    return InMemoryCredentialStore()


def run() -> int:
    app = wx.App()
    frame = SocialFrame()
    frame.Show()
    app.MainLoop()
    return 0
