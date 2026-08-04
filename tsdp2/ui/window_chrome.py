from __future__ import annotations

from tsdp2._deps import *
from tsdp2.logging_config import LOGGER
from tsdp2.resources import asset_path
from tsdp2.ui.theme import C_BG, C_BG2, C_FG, C_FG_DIM


class _WindowButton(tk.Label):
    """Flat Windows-style caption button with a modern hover state."""

    def __init__(self, master, glyph: str, command, close_button: bool = False):
        super().__init__(
            master,
            text=glyph,
            background=C_BG,
            foreground=C_FG,
            width=5,
            anchor="center",
            borderwidth=0,
            font=("Segoe MDL2 Assets", 10),
            cursor="hand2",
        )
        self._command = command
        self._close_button = close_button
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<ButtonRelease-1>", lambda _event: self._command())

    def _enter(self, _event) -> None:
        if self._close_button:
            self.configure(background="#c42b1c", foreground="#ffffff")
        else:
            self.configure(background=C_BG2, foreground=C_FG)

    def _leave(self, _event) -> None:
        self.configure(background=C_BG, foreground=C_FG)

    def set_glyph(self, glyph: str) -> None:
        self.configure(text=glyph)


class WindowChrome(tk.Frame):
    """Borderless, draggable window chrome with working desktop controls."""

    _MIN_WIDTH = 1100
    _MIN_HEIGHT = 720

    def __init__(self, root: tk.Tk, title: str, close_command):
        super().__init__(root, background=C_BG, height=38, borderwidth=0)
        self.root = root
        self.close_command = close_command
        self._restore_geometry = ""
        self._maximized = False
        self._drag_offset = (0, 0)
        self._resize_start = None
        self._resize_handles = []
        self.pack_propagate(False)
        self._build(title)
        self._install_resize_handles()
        self.root.bind("<Map>", self._restore_borderless_after_minimize, add="+")
        self.root.after_idle(self._configure_windows_shell)

    def _build(self, title: str) -> None:
        controls = tk.Frame(self, background=C_BG, borderwidth=0)
        controls.pack(side="right", fill="y")
        _WindowButton(
            controls, "\ue8bb", self.close_command, close_button=True
        ).pack(side="right", fill="y")
        self.maximize_button = _WindowButton(
            controls, "\ue922", self.toggle_maximize
        )
        self.maximize_button.pack(
            side="right", fill="y"
        )
        _WindowButton(controls, "\ue921", self.minimize).pack(
            side="right", fill="y"
        )

        draggable_widgets = [self]
        try:
            self._icon_image = tk.PhotoImage(
                file=os.fspath(asset_path("app_icon_24.png"))
            )
            icon_label = tk.Label(
                self,
                image=self._icon_image,
                background=C_BG,
                borderwidth=0,
            )
            icon_label.pack(side="left", padx=(12, 0), pady=7)
            draggable_widgets.append(icon_label)
        except tk.TclError:
            self._icon_image = None
            LOGGER.debug("Could not load the title-bar icon", exc_info=True)
        label = tk.Label(
            self,
            text=title,
            background=C_BG,
            foreground=C_FG_DIM,
            borderwidth=0,
            font=("Segoe UI", 9),
            anchor="w",
        )
        label.pack(side="left", fill="y", padx=(7, 0))
        draggable_widgets.append(label)
        divider = tk.Frame(self, background=C_BG2, borderwidth=0)
        divider.place(x=0, rely=1, y=-1, relwidth=1, height=1)

        for widget in draggable_widgets:
            widget.bind("<ButtonPress-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._drag)
            widget.bind("<Double-Button-1>", lambda _event: self.toggle_maximize())

    def _window_handle(self) -> int:
        self.root.update_idletasks()
        hwnd = int(self.root.winfo_id())
        if os.name == "nt":
            try:
                import ctypes

                return int(ctypes.windll.user32.GetParent(hwnd) or hwnd)
            except (AttributeError, OSError):
                return hwnd
        return hwnd

    def _configure_windows_shell(self) -> None:
        if os.name != "nt":
            return
        try:
            import ctypes

            hwnd = self._window_handle()
            user32 = ctypes.windll.user32
            ex_style = user32.GetWindowLongW(hwnd, -20)
            ex_style = (ex_style & ~0x00000080) | 0x00040000
            user32.SetWindowLongW(hwnd, -20, ex_style)

            corners = ctypes.c_int(2)
            no_border = ctypes.c_uint(0xFFFFFFFE)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 33, ctypes.byref(corners), ctypes.sizeof(corners)
            )
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 34, ctypes.byref(no_border), ctypes.sizeof(no_border)
            )
        except (AttributeError, OSError, RuntimeError):
            LOGGER.debug("Could not configure custom Windows chrome", exc_info=True)

    def minimize(self) -> None:
        try:
            if os.name == "nt":
                import ctypes

                ctypes.windll.user32.ShowWindow(self._window_handle(), 6)
            else:
                self.root.overrideredirect(False)
                self.root.iconify()
        except (AttributeError, OSError, tk.TclError):
            self.root.iconify()

    def _restore_borderless_after_minimize(self, _event=None) -> None:
        if self.root.state() == "normal" and not self.root.overrideredirect():
            self.root.after_idle(self._resume_borderless)

    def _resume_borderless(self) -> None:
        self.root.overrideredirect(True)
        self._configure_windows_shell()
        self._raise_resize_handles()

    def toggle_maximize(self) -> None:
        if self._maximized:
            if self._restore_geometry:
                self.root.geometry(self._restore_geometry)
            self._maximized = False
            self.maximize_button.set_glyph("\ue922")
            self._raise_resize_handles()
            return

        self._restore_geometry = self.root.geometry()
        if os.name == "nt":
            try:
                import ctypes
                from ctypes import wintypes

                work_area = wintypes.RECT()
                ctypes.windll.user32.SystemParametersInfoW(
                    0x0030, 0, ctypes.byref(work_area), 0
                )
                width = work_area.right - work_area.left
                height = work_area.bottom - work_area.top
                self.root.geometry(
                    f"{width}x{height}+{work_area.left}+{work_area.top}"
                )
            except (AttributeError, OSError):
                self._maximize_to_screen()
        else:
            self._maximize_to_screen()
        self._maximized = True
        self.maximize_button.set_glyph("\ue923")

    def _maximize_to_screen(self) -> None:
        self.root.geometry(
            f"{self.root.winfo_screenwidth()}x{self.root.winfo_screenheight()}+0+0"
        )

    def _start_drag(self, event) -> None:
        if self._maximized:
            self.toggle_maximize()
            self.root.update_idletasks()
            self._drag_offset = (self.root.winfo_width() // 2, 18)
        else:
            self._drag_offset = (
                event.x_root - self.root.winfo_x(),
                event.y_root - self.root.winfo_y(),
            )

    def _drag(self, event) -> None:
        offset_x, offset_y = self._drag_offset
        self.root.geometry(f"+{event.x_root - offset_x}+{event.y_root - offset_y}")

    def _install_resize_handles(self) -> None:
        specs = {
            "n": dict(relx=0, rely=0, x=8, width=-16, relwidth=1, height=4),
            "s": dict(relx=0, rely=1, y=-5, relwidth=1, height=5),
            "w": dict(relx=0, rely=0, y=38, width=5, relheight=1, height=-46),
            "e": dict(relx=1, rely=0, x=-5, y=38, width=5, relheight=1, height=-46),
            "nw": dict(x=0, y=0, width=8, height=8),
            "ne": dict(relx=1, x=-8, y=0, width=8, height=8),
            "sw": dict(rely=1, x=0, y=-8, width=8, height=8),
            "se": dict(relx=1, rely=1, x=-8, y=-8, width=8, height=8),
        }
        cursors = {
            "n": "size_ns",
            "s": "size_ns",
            "w": "size_we",
            "e": "size_we",
            "nw": "size_nw_se",
            "se": "size_nw_se",
            "ne": "size_ne_sw",
            "sw": "size_ne_sw",
        }
        for edge, placement in specs.items():
            handle_background = C_BG
            handle = tk.Frame(
                self.root,
                background=handle_background,
                borderwidth=0,
                cursor=cursors[edge],
            )
            handle.place(**placement)
            handle.bind(
                "<ButtonPress-1>",
                lambda event, selected=edge: self._start_resize(event, selected),
            )
            handle.bind("<B1-Motion>", self._resize)
            self._resize_handles.append(handle)
        self.root.after_idle(self._raise_resize_handles)

    def _raise_resize_handles(self) -> None:
        for handle in self._resize_handles:
            handle.lift()

    def _start_resize(self, event, edge: str) -> None:
        if self._maximized:
            return
        self._resize_start = (
            edge,
            event.x_root,
            event.y_root,
            self.root.winfo_x(),
            self.root.winfo_y(),
            self.root.winfo_width(),
            self.root.winfo_height(),
        )

    def _resize(self, event) -> None:
        if self._maximized or self._resize_start is None:
            return
        edge, start_x, start_y, x, y, width, height = self._resize_start
        dx = event.x_root - start_x
        dy = event.y_root - start_y

        new_x, new_y, new_width, new_height = x, y, width, height
        if "e" in edge:
            new_width = max(self._MIN_WIDTH, width + dx)
        if "s" in edge:
            new_height = max(self._MIN_HEIGHT, height + dy)
        if "w" in edge:
            new_width = max(self._MIN_WIDTH, width - dx)
            new_x = x + width - new_width
        if "n" in edge:
            new_height = max(self._MIN_HEIGHT, height - dy)
            new_y = y + height - new_height
        self.root.geometry(f"{new_width}x{new_height}+{new_x}+{new_y}")


__all__ = [name for name in globals() if not name.startswith("__")]


