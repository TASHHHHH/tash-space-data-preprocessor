from __future__ import annotations

from tsdp2._deps import *
from tsdp2.logging_config import LOGGER
from tsdp2.output_paths import JobPaths
from tsdp2.ui.coverage_selection import CoverageSelectionMixin
from tsdp2.ui.coverage_rendering import CoverageRenderingMixin
from tsdp2.ui.theme import force_light_titlebar

class CoverageWindow(CoverageSelectionMixin, CoverageRenderingMixin):

    def __init__(self, parent, groups, filter_codes, out_dir, on_report=None, on_region=None):
        self.groups = groups
        self.out_dir = out_dir
        self.on_report = on_report
        self.on_region = on_region
        self.win = tk.Toplevel(parent)
        self.win.title('WCS Mosaic Coverage')
        self.win.transient(parent.winfo_toplevel())
        self.win.geometry('1240x940')
        try:
            force_light_titlebar(self.win)
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)
        body = ttk.Frame(self.win, padding=10)
        body.pack(fill='both', expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(2, weight=1)
        header = ttk.Frame(body)
        header.grid(row=0, column=0, columnspan=2, sticky='ew')
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text='Coverage / holes preview', style='Header.TLabel').grid(row=0, column=0, sticky='w')
        ttk.Label(header, text='Uses WCS plus lightweight sampled masks. Full science maps are not loaded.', style='Sub.TLabel').grid(row=0, column=1, sticky='e')
        controls = ttk.Frame(body)
        controls.grid(row=1, column=0, columnspan=2, sticky='ew', pady=(8, 2))
        controls.columnconfigure(9, weight=1)
        ttk.Label(controls, text='View:').grid(row=0, column=0, sticky='w', padx=(0, 6))
        self.view_var = tk.StringVar(value='All filters')
        self.hole_mask_var = tk.BooleanVar(value=True)
        self.opacity10_var = tk.BooleanVar(value=True)
        self.tile_labels_var = tk.BooleanVar(value=False)
        self.rotation_var = tk.DoubleVar(value=0.0)
        view_values = ['All filters'] + filter_codes
        self.view_combo = ttk.Combobox(controls, textvariable=self.view_var, values=view_values, state='readonly', width=20)
        self.view_combo.grid(row=0, column=1, sticky='w', padx=(0, 8))
        self.status_var = tk.StringVar(value='Preparing WCS coverage…')
        ttk.Label(controls, textvariable=self.status_var, style='Sub.TLabel').grid(row=0, column=9, sticky='e')
        self.canvas = tk.Canvas(body, highlightthickness=0, background='white')
        self.canvas.grid(row=2, column=0, sticky='nsew', pady=8)
        sx = ttk.Scrollbar(body, orient='horizontal', command=self.canvas.xview)
        sy = ttk.Scrollbar(body, orient='vertical', command=self.canvas.yview)
        sx.grid(row=3, column=0, sticky='ew')
        sy.grid(row=2, column=1, sticky='ns')
        self.canvas.configure(xscrollcommand=sx.set, yscrollcommand=sy.set)
        bottom = ttk.Frame(body)
        bottom.grid(row=4, column=0, columnspan=2, sticky='ew', pady=(4, 0))
        bottom.columnconfigure(0, weight=1)
        self.msg_var = tk.StringVar(value='')
        self.area_var = tk.StringVar(value='Drag to draw an area; then move, resize, and rotate it with the handles.')
        ttk.Label(bottom, textvariable=self.area_var).grid(row=0, column=0, columnspan=4, sticky='w', pady=(0, 3))
        ttk.Label(bottom, textvariable=self.msg_var, style='Sub.TLabel').grid(row=1, column=0, sticky='w')
        clear_area_btn = ttk.Button(bottom, text='Clear selected area')
        clear_area_btn.grid(row=1, column=1, sticky='e', padx=4)
        self.use_area_btn = ttk.Button(bottom, text='Use selected area for Mosaic', style='Accent.TButton', state='disabled')
        self.use_area_btn.grid(row=1, column=2, sticky='e', padx=4)
        ttk.Button(bottom, text='Close', command=self.win.destroy).grid(row=1, column=3, sticky='e', padx=(8, 0))
        rotation_row = ttk.Frame(bottom)
        rotation_row.grid(row=2, column=0, columnspan=4, sticky='ew', pady=(6, 0))
        ttk.Label(rotation_row, text='Selection rotation:').pack(side='left')
        self.rotate_left_btn = ttk.Button(rotation_row, text='−5°', state='disabled')
        self.rotate_left_btn.pack(side='left', padx=(8, 3))
        self.angle_spin = ttk.Spinbox(rotation_row, from_=-180.0, to=180.0, increment=1.0, textvariable=self.rotation_var, width=8, state='disabled')
        self.angle_spin.pack(side='left', padx=3)
        ttk.Label(rotation_row, text='degrees').pack(side='left', padx=(0, 3))
        self.rotate_right_btn = ttk.Button(rotation_row, text='+5°', state='disabled')
        self.rotate_right_btn.pack(side='left', padx=3)
        self.reset_rotation_btn = ttk.Button(rotation_row, text='Reset', state='disabled')
        self.reset_rotation_btn.pack(side='left', padx=(3, 12))
        ttk.Label(rotation_row, text='Drag inside to move, squares to resize, round handle/wheel to rotate.', style='Sub.TLabel').pack(side='left')
        self.state = {'idx': 0, 'mode': 'all', 'report': None, 'busy': False, 'photo': None, 'selection': None, 'selection_id': None, 'rotation_line_id': None, 'rotation_handle_id': None, 'rotation_handle': None, 'resize_handle_ids': {}, 'resize_handles': {}, 'resizing': False, 'resize_handle': None, 'resize_origin': None, 'rotating': False, 'moving': False, 'move_start': None, 'move_origin': None, 'drag_start': None}
        self.angle_spin.configure(command=self._angle_changed)
        self.angle_spin.bind('<Return>', self._angle_changed)
        self.angle_spin.bind('<FocusOut>', self._angle_changed)
        self.rotate_left_btn.configure(command=lambda: self._set_rotation(float(self.rotation_var.get()) - 5.0))
        self.rotate_right_btn.configure(command=lambda: self._set_rotation(float(self.rotation_var.get()) + 5.0))
        self.reset_rotation_btn.configure(command=lambda: self._set_rotation(0.0))
        clear_area_btn.configure(command=self._clear_selection)
        self.use_area_btn.configure(command=self._use_selection)
        self.canvas.bind('<ButtonPress-1>', self._drag_start)
        self.canvas.bind('<B1-Motion>', self._drag_motion)
        self.canvas.bind('<ButtonRelease-1>', self._drag_end)
        self.canvas.bind('<Motion>', self._hover, add='+')
        self.canvas.bind('<MouseWheel>', self._wheel_rotate)
        self.canvas.bind('<Button-4>', lambda event: self._set_rotation(float(self.rotation_var.get()) + 2.0) if self.state.get('selection') else None)
        self.canvas.bind('<Button-5>', lambda event: self._set_rotation(float(self.rotation_var.get()) - 2.0) if self.state.get('selection') else None)
        self.all_btn = ttk.Button(controls, text='All filters', command=self.show_all)
        self.all_btn.grid(row=0, column=2, sticky='w', padx=(0, 4))
        self.prev_btn = ttk.Button(controls, text='◀ Prev filter', command=self.prev_filter)
        self.prev_btn.grid(row=0, column=3, sticky='w', padx=(0, 4))
        self.next_btn = ttk.Button(controls, text='Next filter ▶', command=self.next_filter)
        self.next_btn.grid(row=0, column=4, sticky='w', padx=(0, 4))
        self.holes_chk = ttk.Checkbutton(controls, text='Show real holes', variable=self.hole_mask_var, command=self.refresh_current)
        self.holes_chk.grid(row=0, column=5, sticky='w', padx=(8, 4))
        self.opacity_chk = ttk.Checkbutton(controls, text='10% transparent fill', variable=self.opacity10_var, command=self.refresh_current)
        self.opacity_chk.grid(row=0, column=6, sticky='w', padx=(8, 4))
        self.labels_chk = ttk.Checkbutton(controls, text='Show tile % labels', variable=self.tile_labels_var, command=self.refresh_current)
        self.labels_chk.grid(row=0, column=7, sticky='w', padx=(8, 4))
        self.refresh_btn = ttk.Button(controls, text='Refresh', command=self.refresh_current)
        self.refresh_btn.grid(row=0, column=8, sticky='w', padx=(0, 4))
        self.view_combo.bind('<<ComboboxSelected>>', self.combo_changed)
        self.win.bind('<Left>', lambda _e: self.prev_filter())
        self.win.bind('<Right>', lambda _e: self.next_filter())
        self.win.bind('a', lambda _e: self.show_all())
        self.win.bind('A', lambda _e: self.show_all())
        self.win.after(50, self.show_all)


def show_wcs_coverage_window(parent, groups: dict, selected_filters=None, out_dir: str='', on_report=None, on_region=None):
    """Open a Tk preview window for WCS-only coverage.

    The first view shows all selected filters. The user can then cycle through
    individual filter groups without loading FITS science pixels or running
    reprojection. Each filter view is regenerated from header/WCS footprints.
    """
    out_dir = out_dir or str(Path.home() / 'JWST_Mosaics')
    if not groups:
        messagebox.showerror('WCS coverage failed', 'No mosaic groups are loaded.', parent=parent)
        return None
    if selected_filters:
        filter_codes = [c for c in sorted(groups.keys()) if c in set(selected_filters)]
    else:
        filter_codes = sorted(groups.keys())
    if not filter_codes:
        messagebox.showerror('WCS coverage failed', 'No selected filters have files.', parent=parent)
        return None
    if not isinstance(out_dir, JobPaths):
        out_dir = JobPaths.create(out_dir or str(Path.home() / 'JWST_Mosaics'), 'WCS_Coverage')
    session = CoverageWindow(parent, groups, filter_codes, out_dir, on_report, on_region)
    return session.win

__all__ = [name for name in globals() if not name.startswith('__')]
