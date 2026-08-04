from __future__ import annotations

from tsdp2._deps import *
from tsdp2.logging_config import LOGGER
from tsdp2.ui.coverage_window import show_wcs_coverage_window
from tsdp2.astronomy.regions import _normalise_mosaic_downscale
from tsdp2.ui.theme import force_light_titlebar

def ask_filter_selection(parent, groups: dict, title="Select filters", initial_selected=None, message=None, out_dir: str = "",
                         initial_scales=None, global_scale="1.0", return_scales=False):
    """Modal Tk dialog returning selected filter codes, or None on cancel.

    Used after FITS files are grouped by filter so the user can decide which
    groups continue into mosaic/align.  Default is all filters selected.
    """
    if not groups:
        return []

    codes = sorted(str(k) for k in groups.keys())
    selected0 = set(initial_selected or codes)
    result = {"value": None}

    dlg = tk.Toplevel(parent)
    dlg.title(title)
    dlg.transient(parent.winfo_toplevel())
    dlg.grab_set()
    dlg.resizable(True, True)
    try:
        force_light_titlebar(dlg)
    except EXPECTED_OPERATION_ERRORS:
        LOGGER.debug("Best-effort operation failed", exc_info=True)

    outer = ttk.Frame(dlg, padding=12)
    outer.grid(row=0, column=0, sticky="nsew")
    dlg.columnconfigure(0, weight=1)
    dlg.rowconfigure(0, weight=1)
    outer.columnconfigure(0, weight=1)

    ttk.Label(outer, text=message or "Choose which filter groups should continue.",
              style="Header.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 4))
    ttk.Label(outer,
              text="Unchecked filters stay loaded in the Mosaic tab, but they will not be processed.",
              style="Sub.TLabel").grid(row=1, column=0, sticky="w", pady=(0, 4))

    scale_row = ttk.Frame(outer)
    scale_row.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 8))
    ttk.Label(scale_row, text="Global output scale:").pack(side="left")
    global_scale_var = tk.StringVar(value=str(global_scale or "1.0"))
    ttk.Entry(scale_row, textvariable=global_scale_var, width=8).pack(side="left", padx=(6, 8))
    ttk.Label(scale_row, text="1 = full, 0.5 = half width/height. Per-filter blanks use global.",
              style="Sub.TLabel").pack(side="left")

    # Scrollable checkbox body for large filter sets.
    canvas = tk.Canvas(outer, highlightthickness=0, height=min(360, 42 * max(3, len(codes))))
    canvas.grid(row=3, column=0, sticky="nsew")
    outer.rowconfigure(3, weight=1)
    sb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
    sb.grid(row=3, column=1, sticky="ns")
    canvas.configure(yscrollcommand=sb.set)

    body = ttk.Frame(canvas, padding=(2, 2, 8, 2))
    body.columnconfigure(1, weight=1)
    win = canvas.create_window((0, 0), window=body, anchor="nw")
    body.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>", lambda e: canvas.itemconfig(win, width=e.width))

    vars_by_code = {}
    scale_vars_by_code = {}
    initial_scales = dict(initial_scales or {})
    ttk.Label(body, text="Filter", style="Sub.TLabel").grid(row=0, column=0, sticky="w", padx=4, pady=(0, 3))
    ttk.Label(body, text="Files", style="Sub.TLabel").grid(row=0, column=1, sticky="e", padx=12, pady=(0, 3))
    ttk.Label(body, text="Scale override", style="Sub.TLabel").grid(row=0, column=2, sticky="w", padx=12, pady=(0, 3))
    for r, code in enumerate(codes, start=1):
        paths = groups.get(code, []) or []
        var = tk.BooleanVar(value=(code in selected0))
        vars_by_code[code] = var
        ttk.Checkbutton(body, text=code, variable=var).grid(row=r, column=0, sticky="w", padx=4, pady=3)
        ttk.Label(body, text=f"{len(paths)} file(s)", style="Sub.TLabel").grid(row=r, column=1, sticky="e", padx=12, pady=3)
        svar = tk.StringVar(value=str(initial_scales.get(code, "") or ""))
        scale_vars_by_code[code] = svar
        ttk.Entry(body, textvariable=svar, width=8).grid(row=r, column=2, sticky="w", padx=12, pady=3)

    btns = ttk.Frame(outer)
    btns.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(10, 0))
    btns.columnconfigure(0, weight=1)

    def set_all(value):
        for v in vars_by_code.values():
            v.set(bool(value))

    def ok():
        chosen = [c for c in codes if vars_by_code[c].get()]
        if not chosen:
            messagebox.showerror("No filters selected", "Select at least one filter group.", parent=dlg)
            return
        try:
            gscale = _normalise_mosaic_downscale(global_scale_var.get(), default=1.0)
            scales = {}
            for c in codes:
                raw = scale_vars_by_code[c].get().strip()
                if raw:
                    scales[c] = _normalise_mosaic_downscale(raw, default=gscale)
        except EXPECTED_OPERATION_ERRORS as exc:
            messagebox.showerror("Invalid scale", str(exc), parent=dlg)
            return
        if return_scales:
            result["value"] = {"selected": chosen, "global_scale": gscale, "scales": scales}
        else:
            result["value"] = chosen
        dlg.destroy()

    def cancel():
        result["value"] = None
        dlg.destroy()

    def show_coverage():
        chosen_now = [c for c in codes if vars_by_code[c].get()] or codes
        show_wcs_coverage_window(dlg, groups, selected_filters=chosen_now, out_dir=out_dir)

    ttk.Button(btns, text="Select All", command=lambda: set_all(True)).grid(row=0, column=0, sticky="w", padx=3)
    ttk.Button(btns, text="Select None", command=lambda: set_all(False)).grid(row=0, column=1, sticky="w", padx=3)
    ttk.Button(btns, text="WCS Coverage…", command=show_coverage).grid(row=0, column=2, sticky="w", padx=(12, 3))
    ttk.Button(btns, text="Cancel", command=cancel).grid(row=0, column=3, sticky="e", padx=(18, 3))
    ttk.Button(btns, text="Continue", style="Accent.TButton", command=ok).grid(row=0, column=4, sticky="e", padx=3)

    dlg.protocol("WM_DELETE_WINDOW", cancel)
    try:
        parent.update_idletasks()
        x = parent.winfo_rootx() + max(40, (parent.winfo_width() - 520) // 2)
        y = parent.winfo_rooty() + max(40, (parent.winfo_height() - 460) // 2)
        dlg.geometry(f"720x520+{x}+{y}")
    except EXPECTED_OPERATION_ERRORS:
        dlg.geometry("720x520")
    dlg.wait_window()
    return result["value"]

__all__ = [name for name in globals() if not name.startswith('__')]
