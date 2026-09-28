"""Native account reading and general behavior settings pages."""
from copy import deepcopy

import wx
from wx.lib.scrolledpanel import ScrolledPanel

from quill_social.reading_options import SPEECH_SCOPES, WARNING_MODES, sanitize_account_options


class AccountReadingPanel(ScrolledPanel):
    def __init__(self, parent, settings, accounts, selected_account_id):
        super().__init__(parent)
        self.values = deepcopy(settings.account_options)
        self.accounts = accounts
        self.current = None
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(self, label="Account:"), 0, wx.ALL, 6)
        self.account = wx.Choice(self, choices=[a.full_handle for a in accounts])
        sizer.Add(self.account, 0, wx.EXPAND | wx.ALL, 6)
        sizer.Add(wx.StaticText(self, label="Content warnings in post rows:"), 0, wx.ALL, 6)
        self.warning = wx.Choice(self, choices=["Post text only", "Warning followed by post text", "Warning only"])
        sizer.Add(self.warning, 0, wx.EXPAND | wx.ALL, 6)
        self.muted = wx.CheckBox(self, label="Mute automatic speech for this account")
        self.sync = wx.CheckBox(self, label="Sync home timeline position with Mastodon")
        sizer.Add(self.muted, 0, wx.ALL, 6)
        sizer.Add(self.sync, 0, wx.ALL, 6)
        sizer.Add(wx.StaticText(self, label="Announce new items when refreshing:"), 0, wx.ALL, 6)
        self.speech = {}
        for scope, title in zip(SPEECH_SCOPES, ("Home", "Mentions", "Notifications", "Direct messages"), strict=True):
            control = wx.CheckBox(self, label=title)
            self.speech[scope] = control
            sizer.Add(control, 0, wx.ALL, 6)
        sizer.Add(wx.StaticText(self, label="Speech settings control Quill's automatic announcements.\n"
                               "Your screen reader can still read and navigate every control."), 0, wx.ALL, 6)
        self.SetSizer(sizer)
        self.SetupScrolling(scroll_x=False)
        self.account.Bind(wx.EVT_CHOICE, self._select)
        if accounts:
            index = next((i for i, a in enumerate(accounts) if a.account_id == selected_account_id), 0)
            self.account.SetSelection(index)
            self._select(None)
        else:
            self.Enable(False)

    def _save(self):
        if self.current is not None:
            self.values[self.current.account_id] = {
                "content_warning_mode": WARNING_MODES[max(0, self.warning.GetSelection())],
                "speech_muted": self.muted.GetValue(),
                "sync_home_position": self.sync.GetValue() and self.current.network == "mastodon",
                "speech_timelines": [scope for scope, ctrl in self.speech.items() if ctrl.GetValue()],
            }

    def _select(self, event):
        self._save()
        self.current = self.accounts[self.account.GetSelection()]
        value = self.values.get(self.current.account_id, {})
        self.warning.SetSelection(WARNING_MODES.index(value.get("content_warning_mode", "warning_then_text")))
        self.muted.SetValue(value.get("speech_muted", False))
        self.sync.SetValue(value.get("sync_home_position", False))
        self.sync.Enable(self.current.network == "mastodon")
        for scope, control in self.speech.items():
            control.SetValue(scope in value.get("speech_timelines", []))

    def apply(self, settings):
        self._save()
        settings.account_options = sanitize_account_options(self.values)


class BehaviorPanel(ScrolledPanel):
    def __init__(self, parent, settings, frame):
        super().__init__(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)
        self.controls = {}
        for name, label in (
            ("open_single_link_without_dialog", "Open a single link without showing the links list"),
            ("remove_unicode", "Remove emojis and non-ASCII characters from post rows and display names"),
            ("notification_first_sentence", "Read only the first sentence in notification announcements"),
            ("announce_timeline_summary",
             "In Advanced mode, announce item and unread counts when changing accounts or timelines"),
            ("focus_posts_on_startup", "Restore last account and timeline, and focus posts on startup"),
            ("minimize_to_tray", "Minimize to system tray"),
        ):
            control = wx.CheckBox(self, label=label)
            control.SetValue(getattr(settings, name))
            self.controls[name] = control
            sizer.Add(control, 0, wx.ALL, 6)
        for name, label, maximum in (("timeline_limit", "Posts to retrieve per account", 1000),
                                     ("timeline_display_limit", "Cached posts to display", 10000)):
            sizer.Add(wx.StaticText(self, label=label), 0, wx.ALL, 6)
            control = wx.SpinCtrl(self, min=1, max=maximum, initial=getattr(settings, name))
            self.controls[name] = control
            sizer.Add(control, 0, wx.EXPAND | wx.ALL, 6)
        clear = wx.Button(self, label="Clear Timeline Cache…")
        clear.Bind(wx.EVT_BUTTON, lambda event: frame.cmd_clear_cache())
        sizer.Add(clear, 0, wx.ALL, 6)
        self.SetSizer(sizer)
        self.SetupScrolling(scroll_x=False)

    def apply(self, settings):
        for name, control in self.controls.items():
            setattr(settings, name, control.GetValue())
