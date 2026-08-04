from __future__ import annotations

from tsdp2._deps import *
from tsdp2.logging_config import LOGGER
from tsdp2.astronomy.coverage import *
from tsdp2.astronomy.fits_io import *
from tsdp2.astronomy.previews import *
from tsdp2.astronomy.reference import *
from tsdp2.astronomy.regions import *
from tsdp2.ui.theme import force_light_titlebar

class CoverageRenderingMixin:

    def _set_controls_enabled(self, enabled: bool):
        for b in (self.prev_btn, self.next_btn, self.all_btn, self.refresh_btn, self.holes_chk, self.opacity_chk, self.labels_chk):
            try:
                b.configure(state='normal' if enabled else 'disabled')
            except EXPECTED_OPERATION_ERRORS:
                LOGGER.debug("Best-effort operation failed", exc_info=True)
        try:
            self.view_combo.configure(state='readonly' if enabled else 'disabled')
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)
        if enabled and len(filter_codes) <= 1:
            try:
                self.prev_btn.configure(state='disabled')
                self.next_btn.configure(state='disabled')
            except EXPECTED_OPERATION_ERRORS:
                LOGGER.debug("Best-effort operation failed", exc_info=True)

    def _apply_error(self, err_text):
        self.state['busy'] = False
        self._set_controls_enabled(True)
        self.status_var.set('Coverage failed.')
        messagebox.showerror('WCS coverage failed', err_text, parent=self.win)

    def _apply_report(self, report, label):
        self.state['busy'] = False
        self._set_controls_enabled(True)
        self.state['report'] = report
        png_path = report.get('png_path', '')
        txt_path = report.get('txt_path', '')
        if not png_path or not os.path.isfile(png_path) or (not HAVE_PIL):
            messagebox.showinfo('WCS coverage', report.get('summary', 'Coverage report created.'), parent=self.win)
        else:
            try:
                with Image.open(png_path) as img:
                    img.load()
                    photo = ImageTk.PhotoImage(img)
                    iw, ih = (img.width, img.height)
                self.canvas.delete('all')
                self.canvas.create_image(0, 0, image=photo, anchor='nw')
                self.canvas.configure(scrollregion=(0, 0, iw, ih))
                self.win._coverage_photo = photo
                self.state['photo'] = photo
                self._clear_selection()
                if report.get('preview_wcs_header'):
                    self.area_var.set('Grey inside an outline is missing/flat-fill data; colour is usable data. Draw an area; drag inside to move, squares to resize, round handle to rotate.')
                else:
                    self.area_var.set('Sky background unavailable; interactive sky selection is disabled.')
            except EXPECTED_OPERATION_ERRORS as exc:
                messagebox.showerror('WCS coverage failed', f'Could not display coverage PNG:\n{exc}', parent=self.win)
                return
            msg = f'Showing: {label}   Saved: {png_path}'
            if txt_path:
                msg += f'   Report: {txt_path}'
            self.msg_var.set(msg)
        valid = report.get('valid', 0)
        invalid = report.get('invalid', 0)
        union = report.get('union_pct', None)
        overlap = report.get('overlap_pct', None)
        if union is not None and overlap is not None:
            self.status_var.set(f'{label}: {valid} valid, {invalid} invalid, union {union:.1f}%, overlap {overlap:.1f}%')
        else:
            self.status_var.set(f'{label}: {valid} valid, {invalid} invalid')
        if self.on_report is not None:
            try:
                self.on_report(report)
            except EXPECTED_OPERATION_ERRORS as exc:
                print(f'[WCS coverage] on_report callback failed: {exc}', flush=True)

    def _render(self, filter_list, label):
        """Generate the coverage image in a worker thread. The SkyView cutout
        is a blocking network fetch (with survey fallbacks) and the sampled
        masks read every FITS — running that on the Tk thread froze the whole
        app for 10-60 s. Results are marshalled back with win.after()."""
        if self.state.get('busy'):
            return
        self.state['busy'] = True
        self._set_controls_enabled(False)
        self.status_var.set(f'Generating {label} coverage…')
        sampled = bool(self.hole_mask_var.get())
        overlay_opacity = 0.1 if bool(self.opacity10_var.get()) else 0.55
        show_tile_labels = bool(self.tile_labels_var.get())
        flist = list(filter_list)

        def _worker():
            try:
                report = create_wcs_coverage_map(self.groups, self.out_dir, selected_filters=flist, title=f'Coverage / holes preview — {label}', sampled_holes=sampled, overlay_opacity=overlay_opacity, show_tile_labels=show_tile_labels)
            except EXPECTED_OPERATION_ERRORS as exc:
                err = str(exc)
                try:
                    self.win.after(0, lambda: self._apply_error(err))
                except tk.TclError:
                    pass
                return
            try:
                self.win.after(0, lambda: self._apply_report(report, label))
            except tk.TclError:
                pass
        threading.Thread(target=_worker, daemon=True).start()

    def show_all(self):
        self.state['mode'] = 'all'
        self.view_var.set('All filters')
        return self._render(filter_codes, 'All filters')

    def show_filter(self, idx=None):
        if not filter_codes:
            return self.state.get('report')
        if idx is None:
            idx = self.state.get('idx', 0)
        self.state['idx'] = int(idx) % len(filter_codes)
        code = filter_codes[self.state['idx']]
        self.state['mode'] = 'filter'
        self.view_var.set(code)
        return self._render([code], f'{code} ({len(self.groups.get(code, []))} file(s))')

    def next_filter(self):
        return self.show_filter(self.state.get('idx', 0) + 1)

    def prev_filter(self):
        return self.show_filter(self.state.get('idx', 0) - 1)

    def refresh_current(self):
        cur = self.view_var.get()
        if cur == 'All filters':
            return self.show_all()
        if cur in filter_codes:
            self.state['idx'] = filter_codes.index(cur)
            return self.show_filter(self.state['idx'])
        return self.show_all()

    def combo_changed(self, _evt=None):
        cur = self.view_var.get()
        if cur == 'All filters':
            self.show_all()
        elif cur in filter_codes:
            self.show_filter(filter_codes.index(cur))

__all__ = [name for name in globals() if not name.startswith('__')]
