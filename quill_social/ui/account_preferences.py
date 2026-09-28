"""Manage local account preferences without editing server profiles."""

import wx


class AccountPreferencesDialog(wx.Dialog):
    def __init__(self, parent, frame):
        super().__init__(parent, title="Manage Accounts",
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.frame = frame
        self.changed = False
        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(wx.StaticText(self, label="Accounts:"), 0, wx.ALL, 8)
        self.accounts = wx.ListBox(self)
        outer.Add(self.accounts, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        outer.Add(wx.StaticText(self, label="Local account name:"), 0, wx.ALL, 8)
        self.alias = wx.TextCtrl(self)
        outer.Add(self.alias, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        self.default = wx.CheckBox(self, label="Default posting account")
        self.paused = wx.CheckBox(self, label="Pause account (skip refresh and posting picker)")
        outer.Add(self.default, 0, wx.ALL, 8)
        outer.Add(self.paused, 0, wx.ALL, 8)
        row = wx.BoxSizer(wx.HORIZONTAL)
        self.save = wx.Button(self, label="&Save account")
        self.remove = wx.Button(self, label="&Remove account")
        row.Add(self.save, 0, wx.RIGHT, 8)
        row.Add(self.remove)
        outer.Add(row, 0, wx.ALL, 8)
        outer.Add(self.CreateStdDialogButtonSizer(wx.CLOSE), 0, wx.ALL | wx.ALIGN_RIGHT, 8)
        self.SetSizer(outer)
        self.SetSize((550, 430))
        self.SetMinSize((450, 350))
        self.accounts.Bind(wx.EVT_LISTBOX, self._on_select)
        self.save.Bind(wx.EVT_BUTTON, self._on_save)
        self.remove.Bind(wx.EVT_BUTTON, self._on_remove)
        self.Bind(wx.EVT_BUTTON, lambda _e: self.EndModal(wx.ID_CLOSE), id=wx.ID_CLOSE)
        self._refresh()
        self.accounts.SetFocus()

    def _refresh(self, account_id=None):
        self._accounts = self.frame.store.list_accounts()
        self.accounts.Set([account.label + (" (paused)" if account.paused else "")
                           for account in self._accounts])
        if self._accounts:
            index = next((i for i, account in enumerate(self._accounts)
                          if account.account_id == account_id), 0)
            self.accounts.SetSelection(index)
        self._on_select()

    def _selected(self):
        index = self.accounts.GetSelection()
        return self._accounts[index] if index != wx.NOT_FOUND else None

    def _on_select(self, _event=None):
        account = self._selected()
        for control in (self.alias, self.default, self.paused, self.save, self.remove):
            control.Enable(account is not None)
        self.alias.SetValue(account.local_alias if account else "")
        self.default.SetValue(account.is_default if account else False)
        self.paused.SetValue(account.paused if account else False)

    def _on_save(self, _event=None):
        account = self._selected()
        if account is None:
            return
        account.local_alias = self.alias.GetValue().strip()
        account.paused = self.paused.GetValue()
        account.is_default = self.default.GetValue()
        if account.is_default:
            for other in self.frame.store.list_accounts():
                if other.account_id != account.account_id and other.is_default:
                    other.is_default = False
                    self.frame.store.put_account(other)
        self.frame.store.put_account(account)
        self.changed = True
        self._refresh(account.account_id)
        self.accounts.SetFocus()

    def _on_remove(self, _event=None):
        account = self._selected()
        if account is None:
            return
        if getattr(self.frame, "_refresh_running", False):
            wx.MessageBox("Wait for the current refresh to finish, then remove this account.",
                          "Manage Accounts", wx.OK | wx.ICON_INFORMATION, self)
            return
        if wx.MessageBox(
            f"Remove {account.label} from Quill Social? Its saved sign-in and cached posts "
            "will be removed, and its pending scheduled posts cancelled. "
            "Your account on the server and other connected accounts will be kept.",
            "Remove account", wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING, self,
        ) != wx.YES:
            return
        try:
            self.frame.credentials.delete(account.network, account.account_id)
            if self.frame.credentials.reference(account.network, account.account_id) is not None:
                raise RuntimeError("The saved sign-in could not be removed. Try again.")
        except Exception:
            wx.MessageBox("The saved sign-in could not be removed. The account has been kept.",
                          "Manage Accounts", wx.OK | wx.ICON_ERROR, self)
            return
        for plan in self.frame.store.list_plans():
            if plan.account_id == account.account_id and plan.state not in {
                "published", "cancelled", "archived"
            }:
                plan.state = "cancelled"
                self.frame.store.put_plan(plan)
        self.frame.store.delete_account(account.account_id)
        self.changed = True
        self._refresh()
        self.accounts.SetFocus()
