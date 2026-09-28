"""Native keyboard-operable editors for timeline layout and spoken fields."""

from copy import deepcopy

import wx

from quill_social.fields import AVAILABLE_FIELDS, DEFAULT_FIELD_ORDER
from quill_social.navigation import sanitize_navigation


class NavigationPreferencesPanel(wx.Panel):
    def __init__(self, parent, navigation, accounts, selected_account_id=None):
        super().__init__(parent)
        # Import at construction time to avoid a shell/dialog import cycle.
        from quill_social.ui.app import NAV_TREE

        self.values = deepcopy(sanitize_navigation(navigation))
        self.keys = ["*"] + [account.account_id for account in accounts]
        self.labels = {scope: label for _, _, children in NAV_TREE for label, scope in children}
        self.defaults = list(self.labels)
        self.source_scopes = [x for x in self.defaults if x.startswith(("home:", "attention:", "library:"))
                              and x != "library:searches"]
        self.key = selected_account_id if selected_account_id in self.keys else "*"
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(self, label="Account whose layout to change:"), 0, wx.ALL, 5)
        self.account = wx.Choice(self, choices=["Unified Home"] + [a.label for a in accounts], name="Layout account")
        self.account.SetSelection(self.keys.index(self.key))
        self.account.Bind(wx.EVT_CHOICE, self._account_changed)
        sizer.Add(self.account, 0, wx.EXPAND | wx.ALL, 5)
        sizer.Add(wx.StaticText(self, label="Timelines (unchecked timelines are hidden; Home stays available):"), 0, wx.ALL, 5)
        self.timelines = wx.CheckListBox(self, name="Timeline order and visibility", size=(-1, 130))
        sizer.Add(self.timelines, 1, wx.EXPAND | wx.ALL, 5)
        self._buttons(sizer, [("Move timeline up", lambda e: self._move(self.timelines, self.order, -1)),
                              ("Move timeline down", lambda e: self._move(self.timelines, self.order, 1)),
                              ("Rename timeline", self._rename)])
        sizer.Add(wx.StaticText(self, label="Post fields (check to include in the post row and field reading):"), 0, wx.ALL, 5)
        self.fields = wx.CheckListBox(self, name="Post field order and visibility", size=(-1, 130))
        sizer.Add(self.fields, 1, wx.EXPAND | wx.ALL, 5)
        self._buttons(sizer, [("Move field up", lambda e: self._move(self.fields, self.field_order, -1)),
                              ("Move field down", lambda e: self._move(self.fields, self.field_order, 1))])
        sizer.Add(wx.StaticText(self, label="Home includes these cached timelines (duplicates appear once):"), 0, wx.ALL, 5)
        self.sources = wx.CheckListBox(self, choices=[self.labels[x] for x in self.source_scopes],
                                       name="Home timeline sources", size=(-1, 90))
        sizer.Add(self.sources, 1, wx.EXPAND | wx.ALL, 5)
        self._buttons(sizer, [("Restore this account's defaults", self._restore)])
        self.SetSizer(sizer)
        self._load()

    def _buttons(self, sizer, entries):
        row = wx.BoxSizer(wx.HORIZONTAL)
        for label, handler in entries:
            button = wx.Button(self, label=label)
            button.Bind(wx.EVT_BUTTON, handler)
            row.Add(button, 0, wx.RIGHT, 5)
        sizer.Add(row, 0, wx.ALL, 5)

    def _load(self):
        options = self.values.get(self.key, {})
        self.order = list(dict.fromkeys([x for x in options.get("order", []) if x in self.defaults] + self.defaults))
        self.names = dict(options.get("labels", {}))
        self.timelines.Set([self.names.get(x, self.labels[x]) for x in self.order])
        self.timelines.SetCheckedItems([i for i, x in enumerate(self.order)
                                        if x == "home:all" or x not in options.get("hidden", [])])
        enabled = [x for x in options.get("fields", DEFAULT_FIELD_ORDER) if x in AVAILABLE_FIELDS]
        self.field_order = list(dict.fromkeys(enabled + list(AVAILABLE_FIELDS)))
        self.fields.Set([AVAILABLE_FIELDS[x] for x in self.field_order])
        self.fields.SetCheckedItems([i for i, x in enumerate(self.field_order) if x in enabled])
        self.sources.SetCheckedItems([i for i, x in enumerate(self.source_scopes)
                                     if x in options.get("unified_sources", ["home:all"])])
        self.timelines.SetSelection(0)
        self.fields.SetSelection(0)

    def _save(self):
        self.values[self.key] = {
            "order": list(self.order), "labels": dict(self.names),
            "hidden": [x for i, x in enumerate(self.order) if not self.timelines.IsChecked(i) and x != "home:all"],
            "fields": [x for i, x in enumerate(self.field_order) if self.fields.IsChecked(i)],
            "unified_sources": [x for i, x in enumerate(self.source_scopes) if self.sources.IsChecked(i)],
        }

    def _account_changed(self, event):
        self._save()
        self.key = self.keys[self.account.GetSelection()]
        self._load()

    def _move(self, control, values, delta):
        index = control.GetSelection()
        target = index + delta
        if index == wx.NOT_FOUND or not 0 <= target < len(values):
            return
        labels = list(control.GetStrings())
        checked = [control.IsChecked(i) for i in range(len(values))]
        for sequence in (values, labels, checked):
            sequence[index], sequence[target] = sequence[target], sequence[index]
        control.Set(labels)
        control.SetCheckedItems([i for i, value in enumerate(checked) if value])
        control.SetSelection(target)

    def _rename(self, event):
        index = self.timelines.GetSelection()
        if index == wx.NOT_FOUND:
            return
        with wx.TextEntryDialog(self, "Timeline name:", "Rename timeline", self.timelines.GetString(index)) as dialog:
            if dialog.ShowModal() == wx.ID_OK and dialog.GetValue().strip():
                self.names[self.order[index]] = dialog.GetValue().strip()
                self.timelines.SetString(index, dialog.GetValue().strip())

    def _restore(self, event):
        self.values.pop(self.key, None)
        self._load()

    def get_value(self):
        self._save()
        return deepcopy(self.values)
