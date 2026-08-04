from __future__ import annotations

from tsdp2._deps import *
from tsdp2.logging_config import LOGGER

class TkUiDispatcher:
    """Thread-safe worker-to-Tk callback bridge polled by the GUI thread."""

    def __init__(self, root: tk.Misc, interval_ms: int = 40):
        self._root = root
        self._interval_ms = max(10, int(interval_ms))
        self._queue: "queue.Queue[tuple[Callable, tuple, dict]]" = queue.Queue()
        self._closed = threading.Event()
        self._after_id = self._root.after(self._interval_ms, self._drain)

    def post(self, callback: Callable, *args, **kwargs) -> None:
        if not self._closed.is_set():
            self._queue.put((callback, args, kwargs))

    def _drain(self) -> None:
        if self._closed.is_set():
            return
        try:
            while True:
                callback, args, kwargs = self._queue.get_nowait()
                try:
                    callback(*args, **kwargs)
                except (tk.TclError, RuntimeError):
                    LOGGER.debug("Discarded a UI callback during shutdown",
                                 exc_info=True)
                except EXPECTED_OPERATION_ERRORS:
                    LOGGER.exception("Queued UI callback failed")
        except queue.Empty:
            pass
        try:
            self._after_id = self._root.after(
                self._interval_ms, self._drain)
        except tk.TclError:
            self._closed.set()

    def close(self) -> None:
        self._closed.set()
        try:
            self._root.after_cancel(self._after_id)
        except (tk.TclError, AttributeError):
            pass
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break


_UI_DISPATCHER: Optional[TkUiDispatcher] = None


def post_to_ui(callback: Callable, *args, **kwargs) -> None:
    """Queue a callback for the main Tk thread."""
    dispatcher = _UI_DISPATCHER
    if dispatcher is None:
        if threading.current_thread() is threading.main_thread():
            callback(*args, **kwargs)
            return
        LOGGER.error("UI callback requested before the dispatcher was ready")
        return
    dispatcher.post(callback, *args, **kwargs)


def install_ui_dispatcher(dispatcher: TkUiDispatcher | None) -> None:
    """Install or clear the process-wide main-thread dispatcher."""
    global _UI_DISPATCHER
    _UI_DISPATCHER = dispatcher


def current_ui_dispatcher() -> TkUiDispatcher | None:
    return _UI_DISPATCHER


__all__ = [name for name in globals() if not name.startswith('__')]
