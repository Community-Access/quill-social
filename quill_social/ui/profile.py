"""Native profile editor; account resolution and network calls run off the UI thread."""

from pathlib import Path
from threading import Thread

import wx
from wx.lib.scrolledpanel import ScrolledPanel


class ProfileDialog(wx.Dialog):
    def __init__(self, parent, resolve_adapter, *, account_label=""):
        super().__init__(parent, title=f"Edit My Profile — {account_label}",
                         size=(620, 540), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self._resolve_adapter = resolve_adapter
        self._adapter = None
        self._original = {}
        self._saving = False
        self.controls = {}
        root = wx.BoxSizer(wx.VERTICAL)
        self.status = wx.StaticText(self, label="Loading profile…")
        root.Add(self.status, 0, wx.ALL, 10)
        self.panel = ScrolledPanel(self)
        self.form = wx.BoxSizer(wx.VERTICAL)
        self.panel.SetSizer(self.form)
        root.Add(self.panel, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)
        root.Add(self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL), 0, wx.ALL | wx.ALIGN_RIGHT, 10)
        self.save = self.FindWindow(wx.ID_OK)
        self.save.SetLabel("&Save")
        self.save.Disable()
        self.SetSizer(root)
        self.Bind(wx.EVT_BUTTON, self._on_save, id=wx.ID_OK)
        self.Bind(wx.EVT_BUTTON, self._on_cancel, id=wx.ID_CANCEL)
        self.Bind(wx.EVT_CLOSE, self._on_cancel)
        self.CentreOnParent()
        self._run(self._load, self._loaded)

    def _run(self, operation, callback):
        def worker():
            try:
                result, error = operation(), None
            except Exception as exc:
                result, error = None, str(exc)
            wx.CallAfter(self._deliver, callback, result, error)
        Thread(target=worker, daemon=True, name="social-profile").start()

    def _deliver(self, callback, result, error):
        if self:
            callback(result, error)

    def _load(self):
        adapter = self._resolve_adapter()
        return adapter, adapter.own_profile()

    def _text(self, key, label, value="", multiline=False):
        self.form.Add(wx.StaticText(self.panel, label=label), 0, wx.TOP, 8)
        control = wx.TextCtrl(self.panel, value=value,
                              style=wx.TE_MULTILINE if multiline else 0)
        self.form.Add(control, 0, wx.EXPAND | wx.BOTTOM, 4)
        self.controls[key] = control
        return control

    def _loaded(self, result, error):
        if error:
            self.status.SetLabel("Profile could not be loaded.")
            wx.MessageBox(error, "Edit My Profile", wx.OK | wx.ICON_ERROR, self)
            self.FindWindow(wx.ID_CANCEL).SetFocus()
            return
        self._adapter, self._original = result
        self._text("display_name", "&Display name", self._original.get("display_name", ""))
        self._text("note", "&Profile text", self._original.get("note", ""), multiline=True)
        if self._adapter.name == "mastodon":
            for key, label in (("avatar", "Avatar image"), ("header", "Header image")):
                control = self._text(key, f"{label} (leave blank to keep current image)")
                button = wx.Button(self.panel, label=f"Browse {label.lower()}…")
                button.Bind(wx.EVT_BUTTON, lambda event, c=control: self._browse_image(c))
                self.form.Add(button, 0, wx.BOTTOM, 4)
            for key, label in (("locked", "Approve follow requests"),
                               ("bot", "This account is a bot"),
                               ("discoverable", "List this account in profile directories"),
                               ("hide_collections", "Hide follows and followers"),
                               ("indexable", "Allow public search indexing")):
                if key in self._original:
                    control = wx.CheckBox(self.panel, label=label)
                    control.SetValue(self._original[key])
                    self.controls[key] = control
                    self.form.Add(control, 0, wx.TOP | wx.BOTTOM, 4)
            for index, pair in enumerate(self._original.get("fields", []) + [("", "")] *
                                         max(0, 4 - len(self._original.get("fields", [])))):
                self._text(f"field_name_{index}", f"Field {index + 1} name", pair[0])
                self._text(f"field_value_{index}", f"Field {index + 1} value", pair[1])
        self.status.SetLabel("Changes are saved to your account when you choose Save.")
        self.panel.SetupScrolling(scroll_x=False)
        self.Layout()
        self.save.Enable()
        self.controls["display_name"].SetFocus()

    def _changes(self):
        values = {key: control.GetValue() for key, control in self.controls.items()
                  if not key.startswith("field_")}
        if self._adapter.name == "mastodon":
            count = len([key for key in self.controls if key.startswith("field_name_")])
            values["fields"] = [(self.controls[f"field_name_{i}"].GetValue(),
                                 self.controls[f"field_value_{i}"].GetValue()) for i in range(count)]
            while values["fields"] and values["fields"][-1] == ("", ""):
                values["fields"].pop()
        return {key: value for key, value in values.items()
                if value != self._original.get(key) and not (key in {"avatar", "header"} and not value)}

    def _browse_image(self, control):
        with wx.FileDialog(self, "Choose profile image", style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
                           wildcard="Image files (*.png;*.jpg;*.jpeg;*.gif;*.webp)|*.png;*.jpg;*.jpeg;*.gif;*.webp") as dialog:
            if dialog.ShowModal() == wx.ID_OK:
                control.SetValue(dialog.GetPath())

    def _on_save(self, event):
        if self._saving or self._adapter is None:
            return
        changes = self._changes()
        for key in ("avatar", "header"):
            if key in changes and not Path(changes[key]).is_file():
                wx.MessageBox("Choose an existing image file.", "Edit My Profile", wx.OK | wx.ICON_ERROR, self)
                self.controls[key].SetFocus()
                return
        if not changes:
            self.EndModal(wx.ID_OK)
            return
        self._saving = True
        self.save.Disable()
        self.panel.Disable()
        self.FindWindow(wx.ID_CANCEL).Disable()
        self.status.SetLabel("Saving profile…")
        self._run(lambda: self._adapter.update_profile(changes), self._saved)

    def _saved(self, result, error):
        self._saving = False
        if not error:
            parent = self.GetParent()
            if parent and hasattr(parent, "announcer"):
                parent.announcer.say("Profile updated.", "action")
            self.EndModal(wx.ID_OK)
            return
        self.panel.Enable()
        self.save.Enable()
        self.FindWindow(wx.ID_CANCEL).Enable()
        self.status.SetLabel("Profile was not saved. Your edits are still available.")
        wx.MessageBox(error, "Edit My Profile", wx.OK | wx.ICON_ERROR, self)
        self.save.SetFocus()

    def _on_cancel(self, event):
        if self._saving:
            if isinstance(event, wx.CloseEvent) and event.CanVeto():
                event.Veto()
            return
        self.EndModal(wx.ID_CANCEL)
