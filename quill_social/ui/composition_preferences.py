"""Native composition preferences shared by Standard and Advanced modes."""

import wx


class CompositionPreferencesPanel(wx.Panel):
    def __init__(self, parent, settings):
        super().__init__(parent)
        sizer = wx.BoxSizer(wx.VERTICAL)
        for name, label, default in (
            ("composition_word_wrap", "Word wrap composition fields", False),
            ("separate_reply_recipients", "Put reply usernames in a separate recipients field", False),
            ("ctrl_enter_to_send", "Use Control+Enter to send posts instead of Enter", True),
            ("provide_confirmations", "Provide confirmation dialogs", True),
        ):
            control = wx.CheckBox(self, label=label)
            control.SetValue(getattr(settings, name, default))
            setattr(self, name, control)
            sizer.Add(control, 0, wx.ALL, 8)
        sizer.Add(wx.StaticText(
            self, label="Shift+Enter always inserts a new line in the post editor."),
            0, wx.ALL, 8)
        self.SetSizer(sizer)

    def apply(self, settings):
        for name in ("composition_word_wrap", "separate_reply_recipients",
                     "ctrl_enter_to_send", "provide_confirmations"):
            setattr(settings, name, getattr(self, name).GetValue())
