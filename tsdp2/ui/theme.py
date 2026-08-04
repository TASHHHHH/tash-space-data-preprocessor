from __future__ import annotations

from tsdp2._deps import *
from tsdp2.logging_config import LOGGER

# Three-color interface palette, with white as the canvas.
C_BG = "#ffffff"
C_BG2 = "#daf0ee"
C_BG3 = "#ffffff"
C_FG = "#3b413c"
C_FG_DIM = "#68716b"
C_ACCENT = "#94d1be"
C_ACCENT_HI = "#82c5b0"
C_GOOD = "#2f7d4f"
C_WARN = "#9a6112"
C_BAD = "#b23a3a"
C_BORDER = "#daf0ee"
C_TOOLBAR = "#ffffff"
C_STATUSBAR = "#daf0ee"

# Kept as compatibility aliases for any external extensions importing the old
# palette names.  New application chrome uses ttk styles below.
C_NAVY = C_FG
C_NAVY_HI = C_FG
C_TEAL = C_ACCENT


def force_light_titlebar(win):
    """Windows 10/11: keep the title bar light even if the OS is in dark mode."""
    if os.name != "nt":
        return
    try:
        import ctypes

        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()
        val = ctypes.c_int(0)
        for attr in (20, 19):
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attr, ctypes.byref(val), ctypes.sizeof(val)
            )
    except EXPECTED_OPERATION_ERRORS:
        LOGGER.debug("Could not set the Windows title-bar theme", exc_info=True)


def apply_theme(root: tk.Tk):
    """Apply the compact white, charcoal, and seafoam interface theme."""
    style = ttk.Style(root)
    # The clam engine respects the application's custom seafoam palette.
    # Image-backed third-party themes override button colors with blue assets.
    style.theme_use("clam")

    root.configure(bg=C_BG)

    style.configure(
        ".",
        background=C_BG,
        foreground=C_FG,
        fieldbackground=C_BG3,
        bordercolor=C_BORDER,
        troughcolor=C_BG2,
        focuscolor=C_ACCENT,
        selectbackground=C_ACCENT,
        selectforeground="#ffffff",
        insertcolor=C_FG,
        font=("Segoe UI", 9),
    )

    style.configure("TFrame", background=C_BG)
    style.configure("TLabel", background=C_BG, foreground=C_FG)
    style.configure("AppToolbar.TFrame", background=C_TOOLBAR)
    style.configure("AppToolbar.TLabel", background=C_TOOLBAR, foreground=C_FG)
    style.configure(
        "ChromeTitle.TLabel",
        background=C_TOOLBAR,
        foreground=C_FG_DIM,
        font=("Segoe UI Semibold", 9),
    )
    style.configure("AppTitle.TLabel", background=C_TOOLBAR, foreground=C_FG,
                    font=("Segoe UI Semibold", 15))
    style.configure("Header.TLabel", foreground=C_FG,
                    font=("Segoe UI Semibold", 12))
    style.configure("Sub.TLabel", foreground=C_FG_DIM)
    style.configure("Good.TLabel", foreground=C_GOOD)
    style.configure("Bad.TLabel", foreground=C_BAD)
    style.configure("Warn.TLabel", foreground=C_WARN)

    style.configure(
        "TLabelframe",
        background=C_BG,
        bordercolor=C_BORDER,
        relief="flat",
        borderwidth=1,
    )
    style.configure(
        "TLabelframe.Label",
        background=C_BG,
        foreground=C_FG,
        font=("Segoe UI Semibold", 9),
    )

    style.configure(
        "TButton",
        background=C_BG2,
        foreground=C_FG,
        bordercolor=C_BG2,
        padding=(10, 6),
        relief="flat",
        borderwidth=0,
    )
    style.map(
        "TButton",
        background=[
            ("disabled", C_BG3),
            ("pressed", C_ACCENT),
            ("active", C_ACCENT),
        ],
        foreground=[("disabled", C_FG_DIM)],
    )

    style.configure(
        "Accent.TButton",
        background=C_ACCENT,
        foreground=C_FG,
        bordercolor=C_ACCENT,
        padding=(10, 5),
    )
    style.map(
        "Accent.TButton",
        background=[("pressed", C_ACCENT_HI), ("active", C_ACCENT_HI)],
        foreground=[("disabled", C_FG_DIM)],
    )

    style.configure("TEntry", padding=4)
    style.configure("TCombobox", padding=3, arrowcolor=C_FG)
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", C_BG3)],
        foreground=[("readonly", C_FG)],
    )
    root.option_add("*TCombobox*Listbox.background", C_BG3)
    root.option_add("*TCombobox*Listbox.foreground", C_FG)
    root.option_add("*TCombobox*Listbox.selectBackground", C_ACCENT)

    style.configure("TCheckbutton", background=C_BG, foreground=C_FG)
    style.map("TCheckbutton", background=[("active", C_BG)])
    style.configure("TRadiobutton", background=C_BG, foreground=C_FG)
    style.map("TRadiobutton", background=[("active", C_BG)])

    style.configure(
        "TNotebook",
        background=C_BG,
        borderwidth=0,
        tabmargins=(12, 8, 12, 0),
    )
    style.configure(
        "TNotebook.Tab",
        background=C_BG3,
        foreground=C_FG_DIM,
        padding=(18, 9),
        font=("Segoe UI", 9),
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", C_BG2), ("active", C_BG2)],
        foreground=[("selected", C_FG)],
        expand=[("selected", (0, 0, 0, 1))],
    )

    style.configure(
        "Horizontal.TProgressbar",
        troughcolor=C_BG2,
        background=C_ACCENT,
        bordercolor=C_BORDER,
        lightcolor=C_ACCENT,
        darkcolor=C_ACCENT,
        thickness=7,
    )

    style.configure(
        "Treeview",
        background=C_BG3,
        foreground=C_FG,
        fieldbackground=C_BG3,
        bordercolor=C_BORDER,
        rowheight=23,
    )
    style.configure(
        "Treeview.Heading",
        background=C_BG2,
        foreground=C_FG,
        font=("Segoe UI Semibold", 9),
        relief="flat",
    )
    style.map(
        "Treeview",
        background=[("selected", C_ACCENT)],
        foreground=[("selected", C_FG)],
    )
    style.map("Treeview.Heading", background=[("active", C_ACCENT)])

    style.configure("TSpinbox", fieldbackground=C_BG3, foreground=C_FG,
                    arrowcolor=C_FG, padding=3)
    style.configure("Vertical.TScrollbar", background=C_BG3,
                    troughcolor=C_BG, bordercolor=C_BG, arrowcolor=C_FG_DIM)
    style.configure("Horizontal.TScrollbar", background=C_BG3,
                    troughcolor=C_BG, bordercolor=C_BG, arrowcolor=C_FG_DIM)
    style.configure("TSeparator", background=C_BORDER)
    style.configure("Statusbar.TFrame", background=C_STATUSBAR)
    style.configure("Statusbar.TLabel", background=C_STATUSBAR, foreground=C_FG_DIM,
                    font=("Segoe UI", 8))
    style.configure("ReadyStatus.TLabel", background=C_STATUSBAR,
                    foreground=C_FG, font=("Segoe UI Semibold", 8))


__all__ = [name for name in globals() if not name.startswith("__")]
