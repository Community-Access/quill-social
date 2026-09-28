"""Native saved-timeline and direct-message dialogs."""

from threading import Thread

import wx

from quill_social.services.timelines import TIMELINE_KINDS

SEARCH_TYPES = (("All", ""), ("Posts", "statuses"),
                ("Users", "accounts"), ("Hashtags", "hashtags"))


class SearchDialog(wx.Dialog):
    """Recovered-client server search: text followed by a result-type choice."""

    def __init__(self, parent):
        super().__init__(parent, title="Search")
        root = wx.BoxSizer(wx.VERTICAL)
        root.Add(wx.StaticText(self, label="Search &text"), 0, wx.ALL, 8)
        self.query = wx.TextCtrl(self)
        root.Add(self.query, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        root.Add(wx.StaticText(self, label="Search &by"), 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
        self.search_by = wx.Choice(self, choices=[label for label, _ in SEARCH_TYPES])
        self.search_by.SetSelection(0)
        root.Add(self.search_by, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        root.Add(self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL),
                 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        self.SetSizerAndFit(root)
        self.Bind(wx.EVT_BUTTON, self._accept, id=wx.ID_OK)
        self.query.SetFocus()
        self.CentreOnParent()

    def parameters(self):
        query = self.query.GetValue().strip()
        if not query:
            raise ValueError("Enter text to search for.")
        index = max(0, self.search_by.GetSelection())
        label, search_type = SEARCH_TYPES[index]
        return query, search_type, label

    def _accept(self, event):
        try:
            self.parameters()
        except ValueError as exc:
            wx.MessageBox(str(exc), "Search", wx.OK | wx.ICON_ERROR, self)
            self.query.SetFocus()
            return
        self.EndModal(wx.ID_OK)


class TimelineDialog(wx.Dialog):
    def __init__(self, parent, accounts, *, kind="hashtag", account_id=""):
        super().__init__(parent, title="Open Timeline", size=(500, 400))
        self.accounts = list(accounts)
        self.kinds = [key for key in TIMELINE_KINDS if key not in {"messages", "search"}]
        root = wx.BoxSizer(wx.VERTICAL)
        root.Add(wx.StaticText(self, label="&Account"), 0, wx.ALL, 8)
        self.account = wx.Choice(self, choices=[a.display_name or a.handle for a in self.accounts])
        self.account.SetSelection(next((i for i, a in enumerate(self.accounts)
                                        if a.account_id == account_id), 0 if self.accounts else -1))
        root.Add(self.account, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        root.Add(wx.StaticText(self, label="Timeline &type"), 0, wx.ALL, 8)
        self.kind = wx.Choice(self, choices=[TIMELINE_KINDS[key] for key in self.kinds])
        self.kind.SetSelection(self.kinds.index(kind))
        root.Add(self.kind, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        self.value_label = wx.StaticText(self, label="&Value")
        root.Add(self.value_label, 0, wx.ALL, 8)
        self.value = wx.TextCtrl(self)
        root.Add(self.value, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        root.Add(wx.StaticText(self, label="Timeline &name (optional)"), 0, wx.ALL, 8)
        self.label = wx.TextCtrl(self)
        root.Add(self.label, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        self.announce = wx.CheckBox(self, label="&Announce new posts automatically")
        root.Add(self.announce, 0, wx.ALL, 8)
        root.Add(self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL), 0, wx.ALL | wx.ALIGN_RIGHT, 8)
        self.FindWindow(wx.ID_OK).SetLabel("&Open")
        self.SetSizer(root)
        self.kind.Bind(wx.EVT_CHOICE, self._kind_changed)
        self.Bind(wx.EVT_BUTTON, self._accept, id=wx.ID_OK)
        self._kind_changed()
        self.CentreOnParent()

    def _kind_changed(self, event=None):
        kind = self.kinds[self.kind.GetSelection()]
        self.value_label.SetLabel({"user": "User &handle", "hashtag": "&Hashtag",
                                   "instance": "Instance &domain", "list": "&List ID (leave blank to choose)"}
                                  .get(kind, "&Value (not needed)"))
        self.value.Enable(kind not in {"messages", "local"})

    def parameters(self):
        selection = self.account.GetSelection()
        if selection < 0:
            raise ValueError("Select an account.")
        kind = self.kinds[self.kind.GetSelection()]
        value = self.value.GetValue().strip() if self.value.IsEnabled() else ""
        if kind in {"user", "hashtag", "instance"} and not value.lstrip("#"):
            raise ValueError("Enter a value for this timeline.")
        return dict(account_id=self.accounts[selection].account_id, kind=kind,
                    value=value, label=self.label.GetValue().strip(),
                    announce=self.announce.GetValue())

    def _accept(self, event):
        try:
            self.parameters()
        except ValueError as exc:
            wx.MessageBox(str(exc), "Open Timeline", wx.OK | wx.ICON_ERROR, self)
            return
        self.EndModal(wx.ID_OK)


class DirectMessageDialog(wx.Dialog):
    def __init__(self, parent, resolve_adapter, *, account_label="", recipient=""):
        super().__init__(parent, title=f"Direct Message — {account_label}", size=(550, 400),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.resolve_adapter = resolve_adapter
        self.result_item = None
        self._sending = False
        root = wx.BoxSizer(wx.VERTICAL)
        root.Add(wx.StaticText(self, label="&Recipient (full handle)"), 0, wx.ALL, 8)
        self._conversation = recipient if recipient.startswith("convo:") else ""
        self.recipient = wx.TextCtrl(self, value="Current conversation" if self._conversation else recipient)
        if self._conversation:
            self.recipient.SetEditable(False)
        root.Add(self.recipient, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        root.Add(wx.StaticText(self, label="&Message"), 0, wx.ALL, 8)
        self.text = wx.TextCtrl(self, style=wx.TE_MULTILINE)
        root.Add(self.text, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        self.status = wx.StaticText(self, label="")
        root.Add(self.status, 0, wx.ALL, 8)
        root.Add(self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL), 0, wx.ALL | wx.ALIGN_RIGHT, 8)
        self.send = self.FindWindow(wx.ID_OK)
        self.send.SetLabel("&Send")
        self.SetSizer(root)
        self.Bind(wx.EVT_BUTTON, self._send, id=wx.ID_OK)
        self.Bind(wx.EVT_BUTTON, self._cancel, id=wx.ID_CANCEL)
        self.Bind(wx.EVT_CLOSE, self._cancel)
        (self.text if recipient else self.recipient).SetFocus()
        self.CentreOnParent()

    def _send(self, event):
        if self._sending:
            return
        recipient, text = self._conversation or self.recipient.GetValue().strip(), self.text.GetValue().strip()
        if not recipient or not text:
            wx.MessageBox("Enter a recipient and message.", "Direct Message", wx.OK | wx.ICON_ERROR, self)
            return
        self._sending = True
        self.send.Disable()
        self.recipient.Disable()
        self.text.Disable()
        self.FindWindow(wx.ID_CANCEL).Disable()
        self.status.SetLabel("Sending message…")

        def worker():
            try:
                result = self.resolve_adapter().send_direct_message(recipient, text)
                error = None
            except Exception as exc:
                result, error = None, str(exc)
            wx.CallAfter(self._sent, result, error)
        Thread(target=worker, daemon=True, name="social-direct-message").start()

    def _sent(self, result, error):
        if not self:
            return
        self._sending = False
        if error:
            self.send.Enable()
            self.recipient.Enable()
            self.text.Enable()
            self.FindWindow(wx.ID_CANCEL).Enable()
            self.status.SetLabel("Message was not sent.")
            wx.MessageBox(error, "Direct Message", wx.OK | wx.ICON_ERROR, self)
            self.text.SetFocus()
            return
        self.result_item = result
        self.EndModal(wx.ID_OK)

    def _cancel(self, event):
        if self._sending:
            if hasattr(event, "Veto") and event.CanVeto():
                event.Veto()
            return
        self.EndModal(wx.ID_CANCEL)
