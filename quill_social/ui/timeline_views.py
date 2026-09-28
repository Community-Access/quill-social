"""Account-bound extra timeline loading without touching wx or SQLite in workers."""
from dataclasses import asdict
from threading import Thread

import wx

from quill_social.adapters.registry import adapter_for
from quill_social.reading_options import automatic_text
from quill_social.ui.timelines import DirectMessageDialog, SearchDialog, TimelineDialog


class TimelineViews:
    def _append_timeline_commands(self, menu):
        menu.AppendSeparator()
        for kind, label in (("user", "User"), ("hashtag", "Hashtag"), ("list", "List"),
                            ("local", "Local"), ("instance", "Remote instance")):
            self._menu_item(menu, f"Open {label.lower()} timeline...", lambda event, k=kind: self.cmd_open_timeline(k))
        for spec in self._extra_timelines():
            self._menu_item(menu, spec.label, lambda event, scope=spec.scope: self._open_standard_timeline(scope))
        self._menu_item(menu, "Close added timeline", lambda event: self.cmd_close_timeline())
        self._menu_item(menu, "Toggle timeline announcements", lambda event: self.cmd_toggle_timeline_announcements())

    def _extra_timelines(self):
        specs = self.timeline_library.list(self.selected_account_id)
        accounts = {account.account_id for account in self.store.list_accounts()}
        return [spec for spec in specs if spec.kind != "messages" and spec.account_id in accounts]

    def _message_specs(self):
        specs = []
        for account in self.store.list_accounts(include_paused=False):
            if account.network not in {"mastodon", "bluesky"}:
                continue
            if self.selected_account_id and account.account_id != self.selected_account_id:
                continue
            existing = next((spec for spec in self.timeline_library.list(account.account_id)
                             if spec.kind == "messages"), None)
            specs.append(existing or self.timeline_library.create(account.account_id, "messages", label="Direct messages", announce=False))
        return specs

    def cmd_open_timeline(self, kind="hashtag"):
        accounts = [account for account in self.store.list_accounts(include_paused=False)
                    if account.network in {"mastodon", "bluesky"}]
        if not accounts:
            self.announcer.error("Add a Mastodon or Bluesky account first.")
            return
        with TimelineDialog(self, accounts, kind=kind, account_id=self.selected_account_id or "") as dialog:
            if dialog.ShowModal() != wx.ID_OK:
                return
            parameters = dialog.parameters()
        if parameters["kind"] == "list" and not parameters.get("value"):
            account = self.store.get_account(parameters["account_id"])
            credentials = self.credentials
            def worker():
                try:
                    choices, error = adapter_for(account, credentials).timeline_lists(), None
                except Exception as exc:
                    choices, error = [], str(exc)
                wx.CallAfter(self._choose_network_list, parameters, choices, error)
            Thread(target=worker, daemon=True, name="social-lists").start()
            return
        self._create_extra_timeline(parameters)

    def _choose_network_list(self, parameters, choices, error):
        if self._closing:
            return
        if error or not choices:
            self.announcer.error(error or "This account has no lists available.")
            return
        with wx.SingleChoiceDialog(self, "Choose a list", "Open list timeline", [name for _, name in choices]) as dialog:
            if dialog.ShowModal() != wx.ID_OK:
                return
            value, label = choices[dialog.GetSelection()]
        parameters.update(value=value, label=label)
        self._create_extra_timeline(parameters)

    def _create_extra_timeline(self, parameters):
        spec = self.timeline_library.create(**parameters)
        if spec.kind == "search":
            self._timeline_loaded.discard(spec.scope)
        self._select_account(spec.account_id, announce=False)
        self._populate_nav()
        self._load_scope(spec.scope, spec.label)

    def cmd_search(self):
        account = self.store.get_account(self.selected_account_id) if self.selected_account_id else None
        if not account or account.paused or account.network not in {"mastodon", "bluesky"}:
            accounts = [item for item in self.store.list_accounts(include_paused=False)
                        if item.network in {"mastodon", "bluesky"}]
            if not accounts:
                self.announcer.error("Add a Mastodon or Bluesky account before searching.")
                return
            with wx.SingleChoiceDialog(
                    self, "Choose the account whose service you want to search.",
                    "Search", [item.full_handle for item in accounts]) as chooser:
                if chooser.ShowModal() != wx.ID_OK:
                    return
                account = accounts[chooser.GetSelection()]
        with SearchDialog(self) as dialog:
            if dialog.ShowModal() != wx.ID_OK:
                return
            query, search_type, _label = dialog.parameters()
        self._create_extra_timeline({
            "account_id": account.account_id, "kind": "search", "value": query,
            "label": f"Search: {query}", "announce": False, "search_type": search_type,
        })

    def _ensure_extra_loaded(self, scope):
        specs = self._message_specs() if scope == "attention:messages" else [self.timeline_library.get(scope)]
        for spec in specs:
            if spec and spec.scope not in self._timeline_loaded:
                self._request_extra_timeline(spec, announce=spec.kind == "search")

    def _request_extra_timeline(self, spec, *, announce=False):
        if self._closing or spec.scope in self._timeline_jobs:
            return
        account = self.store.get_account(spec.account_id)
        if not account or account.paused:
            return
        self._timeline_jobs.add(spec.scope)
        if announce:
            if spec.kind == "search":
                search_label = {"": "all", "statuses": "posts", "accounts": "users",
                                "hashtags": "hashtags"}[spec.search_type]
                self.announcer.say(f"Searching {search_label}...", "action")
            else:
                self.announcer.say(f"Loading {spec.label}...", "action")
        credentials, limit = self.credentials, self.a11y.timeline_limit
        def worker():
            try:
                adapter = adapter_for(account, credentials)
                items = (adapter.search(spec.value, spec.search_type, limit=limit)
                         if spec.kind == "search"
                         else adapter.fetch_timeline(spec.kind, spec.value, limit=limit))
                error = None
            except Exception as exc:
                items, error = [], str(exc)
            wx.CallAfter(self._finish_extra_timeline, spec, items, error, announce)
        Thread(target=worker, daemon=True, name="social-extra-timeline").start()

    def _finish_extra_timeline(self, spec, items, error, announce=False):
        if self._closing:
            return
        self._timeline_jobs.discard(spec.scope)
        current_spec = self.timeline_library.get(spec.scope)
        account = self.store.get_account(spec.account_id)
        if not current_spec or not account:
            return
        if error:
            self.announcer.error(f"{account.label}, {spec.label}: {error}")
            return
        baseline = spec.scope in self._timeline_loaded
        known = {item.remote_id for item in self.timeline_library.items(spec.scope)}
        loaded = self.timeline_library.store_loaded(spec.scope, items)
        self._timeline_loaded.add(spec.scope)
        options = self.a11y.account_options.get(spec.account_id, {})
        enabled = ("attention:messages" in options.get("speech_timelines", []) if spec.kind == "messages" else current_spec.announce)
        if baseline and enabled and not options.get("speech_muted") and not account.paused:
            focus = wx.Window.FindFocus()
            active = bool(self.IsActive() and focus and self.IsDescendant(focus)
                          and self.selected_account_id == account.account_id)
            messages = [automatic_text(item, self.a11y,
                                      scope="attention:messages" if spec.kind == "messages" else spec.scope,
                                      timeline_label=spec.label, account_label=account.label,
                                      account_count=len(self.store.list_accounts()), active_account_context=active)
                        for item in sorted(loaded, key=lambda item: item.created_at) if item.remote_id not in known]
            if messages:
                self.announcer.say("\n".join(messages), "automatic", interrupt=False)
        visible_messages = self.current_scope == "attention:messages" and self.selected_account_id in (None, spec.account_id)
        if self.current_scope == spec.scope or visible_messages:
            self._load_scope(self.current_scope, self.current_scope_label, keep_selection=True, announce=False)

        if announce:
            if spec.kind == "search":
                self.announcer.say(f"Search complete. {len(loaded)} results.", "action")
            else:
                self.announcer.say(f"Refreshed {spec.label}. {len(loaded)} items.", "action")

    def _poll_extra_timelines(self):
        # Message preferences apply per account even while another account is selected.
        for account in self.store.list_accounts(include_paused=False):
            if account.network in {"mastodon", "bluesky"} and "attention:messages" in self.a11y.account_options.get(account.account_id, {}).get("speech_timelines", []):
                if not any(spec.kind == "messages" for spec in self.timeline_library.list(account.account_id)):
                    self.timeline_library.create(account.account_id, "messages", label="Direct messages", announce=False)
        for spec in self.timeline_library.list():
            enabled = "attention:messages" in self.a11y.account_options.get(spec.account_id, {}).get("speech_timelines", []) if spec.kind == "messages" else spec.announce
            if enabled or self.current_scope == spec.scope or (spec.kind == "messages" and self.current_scope == "attention:messages"):
                self._request_extra_timeline(spec)

    def cmd_close_timeline(self):
        spec = self.timeline_library.get(self.current_scope)
        if not spec:
            self.announcer.say("Only an added timeline can be closed.", "action")
            return
        self.timeline_library.remove(spec.scope)
        self._timeline_loaded.discard(spec.scope)
        self._populate_nav()
        self._open_standard_timeline("home:all")
        self.announcer.say("Timeline closed.", "action")

    def cmd_toggle_timeline_announcements(self):
        spec = self.timeline_library.get(self.current_scope)
        if not spec:
            self.announcer.say("For Home, Mentions, Notifications and Messages, use Account reading in Preferences.", "action")
            return
        spec.announce = not spec.announce
        self.store.put_document("timeline", spec.scope, asdict(spec))
        self.announcer.say(f"Announcements {'enabled' if spec.announce else 'disabled'} for {spec.label}.", "action")

    def cmd_direct_message(self, item=None):
        account = self.store.get_account(item.account_id if item else self.selected_account_id) if item or self.selected_account_id else None
        if not account:
            accounts = [a for a in self.store.list_accounts(include_paused=False) if a.network in {"mastodon", "bluesky"}]
            if not accounts:
                self.announcer.error("Add a Mastodon or Bluesky account first.")
                return
            with wx.SingleChoiceDialog(self, "Choose the sending account", "Direct message", [a.full_handle for a in accounts]) as dialog:
                if dialog.ShowModal() != wx.ID_OK:
                    return
                account = accounts[dialog.GetSelection()]
        if account.paused or account.network not in {"mastodon", "bluesky"}:
            self.announcer.error("Choose an active Mastodon or Bluesky account.")
            return
        recipient = item.author_handle if item else ""
        if recipient.lstrip("@").casefold() in {account.handle.lstrip("@").casefold(), account.full_handle.lstrip("@").casefold()}:
            recipient = ""
        if item and item.remote_id.startswith("chat:"):
            recipient = "convo:" + item.remote_id.split(":", 2)[1]
        credentials = self.credentials
        with DirectMessageDialog(self, lambda: adapter_for(account, credentials), account_label=account.full_handle, recipient=recipient) as dialog:
            if dialog.ShowModal() == wx.ID_OK:
                self.announcer.say("Direct message sent.", "action")
                for spec in self.timeline_library.list(account.account_id):
                    if spec.kind == "messages":
                        self._request_extra_timeline(spec)
