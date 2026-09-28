"""Optional minimize-to-tray with a keyboard-accessible restore/exit menu."""
import wx
import wx.adv


class TrayController(wx.adv.TaskBarIcon):
    def __init__(self, frame):
        super().__init__()
        self.frame = frame
        frame.Bind(wx.EVT_ICONIZE, self._iconize)
        self.Bind(wx.adv.EVT_TASKBAR_LEFT_DCLICK, self.restore)

    def _iconize(self, event):
        if event.IsIconized() and self.frame.a11y.minimize_to_tray:
            icon = wx.ArtProvider.GetIcon(wx.ART_INFORMATION, wx.ART_OTHER, (32, 32))
            if self.SetIcon(icon, "Quill Social"):
                self.frame.Hide()
                self.frame.announcer.say("Quill Social minimized to system tray.", "action")
        event.Skip()

    def restore(self, event=None):
        self.frame.Show()
        self.frame.Iconize(False)
        self.frame.Raise()
        self.RemoveIcon()
        self.frame.announcer.say("Quill Social restored.", "action")

    def CreatePopupMenu(self):
        menu = wx.Menu()
        restore = menu.Append(wx.ID_ANY, "Restore Quill Social")
        close = menu.Append(wx.ID_EXIT, "Exit")
        menu.Bind(wx.EVT_MENU, self.restore, restore)
        menu.Bind(wx.EVT_MENU, lambda event: self.frame.Close(), close)
        return menu
