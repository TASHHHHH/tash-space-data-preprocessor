from __future__ import annotations

from tsdp2._deps import *
from tsdp2.logging_config import LOGGER
from tsdp2.astronomy.coverage import *
from tsdp2.astronomy.fits_io import *
from tsdp2.astronomy.previews import *
from tsdp2.astronomy.reference import *
from tsdp2.astronomy.regions import *
from tsdp2.ui.theme import force_light_titlebar

class CoverageSelectionMixin:

    def _clear_selection(self):
        try:
            self.canvas.delete('sky_selection')
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)
        self.state['selection'] = None
        self.state['selection_id'] = None
        self.state['rotation_line_id'] = None
        self.state['rotation_handle_id'] = None
        self.state['rotation_handle'] = None
        self.state['resize_handle_ids'] = {}
        self.state['resize_handles'] = {}
        self.state['resizing'] = False
        self.state['resize_handle'] = None
        self.state['resize_origin'] = None
        self.state['rotating'] = False
        self.state['moving'] = False
        self.state['move_start'] = None
        self.state['move_origin'] = None
        self.state['drag_start'] = None
        self.rotation_var.set(0.0)
        for widget in (self.angle_spin, self.rotate_left_btn, self.rotate_right_btn, self.reset_rotation_btn):
            try:
                widget.configure(state='disabled')
            except EXPECTED_OPERATION_ERRORS:
                LOGGER.debug("Best-effort operation failed", exc_info=True)
        self.use_area_btn.configure(state='disabled')
        self.area_var.set('Draw an area; drag inside to move, squares to resize, round handle to rotate.')

    def _normalise_angle(self, value):
        angle = float(value or 0.0)
        return (angle + 180.0) % 360.0 - 180.0

    def _selection_corners(self):
        selection = self.state.get('selection')
        if not isinstance(selection, dict):
            return None
        return _rotated_rectangle_corners(selection['cx'], selection['cy'], selection['width'], selection['height'], selection.get('angle_deg', 0.0))

    def _draw_selection(self):
        selection = self.state.get('selection')
        corners = self._selection_corners()
        if not isinstance(selection, dict) or corners is None:
            return
        flat = [float(v) for point in corners for v in point]
        if self.state.get('selection_id') is None:
            self.state['selection_id'] = self.canvas.create_polygon(*flat, outline='#ffea00', width=3, dash=(8, 4), fill='#1d4ed8', stipple='gray25', tags=('sky_selection', 'sky_selection_box'))
        else:
            self.canvas.coords(self.state['selection_id'], *flat)
        center = np.asarray([selection['cx'], selection['cy']], dtype=float)
        top_mid = (corners[0] + corners[1]) / 2.0
        direction = top_mid - center
        length = float(np.hypot(direction[0], direction[1]))
        if length <= 1e-06:
            direction = np.asarray([0.0, -1.0])
        else:
            direction /= length
        handle = top_mid + direction * 28.0
        self.state['rotation_handle'] = (float(handle[0]), float(handle[1]))
        if self.state.get('rotation_line_id') is None:
            self.state['rotation_line_id'] = self.canvas.create_line(float(top_mid[0]), float(top_mid[1]), float(handle[0]), float(handle[1]), fill='#ffea00', width=2, tags=('sky_selection', 'sky_rotation_line'))
        else:
            self.canvas.coords(self.state['rotation_line_id'], float(top_mid[0]), float(top_mid[1]), float(handle[0]), float(handle[1]))
        radius = 7.0
        oval = (float(handle[0] - radius), float(handle[1] - radius), float(handle[0] + radius), float(handle[1] + radius))
        if self.state.get('rotation_handle_id') is None:
            self.state['rotation_handle_id'] = self.canvas.create_oval(*oval, fill='#ffea00', outline='#111827', width=2, tags=('sky_selection', 'sky_rotation_handle'))
        else:
            self.canvas.coords(self.state['rotation_handle_id'], *oval)
        resize_handles = _rotated_rectangle_handles(selection)
        self.state['resize_handles'] = {name: (float(point[0]), float(point[1])) for name, point in resize_handles.items()}
        resize_ids = self.state.setdefault('resize_handle_ids', {})
        handle_radius = 6.0
        for name, point in resize_handles.items():
            box = (float(point[0] - handle_radius), float(point[1] - handle_radius), float(point[0] + handle_radius), float(point[1] + handle_radius))
            if name not in resize_ids:
                resize_ids[name] = self.canvas.create_rectangle(*box, fill='#ffea00' if len(name) == 2 else '#ffffff', outline='#111827', width=2, tags=('sky_selection', 'sky_resize_handle', f'sky_resize_{name}'))
            else:
                self.canvas.coords(resize_ids[name], *box)
        self.canvas.tag_raise('sky_selection')

    def _canvas_point(self, event):
        report = self.state.get('report') or {}
        width = int(report.get('preview_width') or 0)
        height = int(report.get('preview_height') or 0)
        if width <= 0 or height <= 0:
            return None
        x = min(max(float(self.canvas.canvasx(event.x)), 0.0), width - 1.0)
        y = min(max(float(self.canvas.canvasy(event.y)), 0.0), height - 1.0)
        return (x, y)

    def _selection_to_region(self):
        report = self.state.get('report') or {}
        selection = self.state.get('selection')
        header_text = report.get('preview_wcs_header')
        corners = self._selection_corners()
        if not isinstance(selection, dict) or corners is None or (not header_text):
            return None
        xs = corners[:, 0]
        ys = corners[:, 1]
        preview_wcs = WCS(fits.Header.fromstring(header_text, sep='\n'), naxis=2)
        ra, dec = preview_wcs.all_pix2world(xs, ys, 0)
        cra, cdec = preview_wcs.all_pix2world([selection['cx']], [selection['cy']], 0)
        x0, y0 = np.min(corners, axis=0)
        x1, y1 = np.max(corners, axis=0)
        return {'world_corners': [[float(r), float(d)] for r, d in zip(ra, dec)], 'center_ra': float(cra[0]), 'center_dec': float(cdec[0]), 'preview_box': [float(x0), float(y0), float(x1), float(y1)], 'preview_corners': [[float(x), float(y)] for x, y in corners], 'rotation_deg': float(selection.get('angle_deg', 0.0)), 'rotated': bool(abs(float(selection.get('angle_deg', 0.0))) > 1e-07), 'source': 'interactive WCS coverage selection'}

    def _drag_start(self, event):
        point = self._canvas_point(event)
        if point is None or not (self.state.get('report') or {}).get('preview_wcs_header'):
            return
        handle = self.state.get('rotation_handle')
        if isinstance(self.state.get('selection'), dict) and handle is not None:
            if float(np.hypot(point[0] - handle[0], point[1] - handle[1])) <= 16.0:
                self.state['rotating'] = True
                self.state['drag_start'] = None
                self.canvas.configure(cursor='crosshair')
                return
        resize_handles = self.state.get('resize_handles') or {}
        nearest_resize = None
        nearest_distance = float('inf')
        for name, resize_point in resize_handles.items():
            distance = float(np.hypot(point[0] - resize_point[0], point[1] - resize_point[1]))
            if distance < nearest_distance:
                nearest_resize = name
                nearest_distance = distance
        if isinstance(self.state.get('selection'), dict) and nearest_resize is not None and (nearest_distance <= 13.0):
            self.state['resizing'] = True
            self.state['resize_handle'] = nearest_resize
            self.state['resize_origin'] = dict(self.state['selection'])
            self.state['drag_start'] = None
            self.canvas.configure(cursor='crosshair')
            self.area_var.set(f'Resizing from {nearest_resize.upper()} handle… hold Shift to keep proportions.')
            return
        existing_corners = self._selection_corners()
        if isinstance(self.state.get('selection'), dict) and existing_corners is not None and self._inside_polygon(point[0], point[1], existing_corners):
            self.state['moving'] = True
            self.state['move_start'] = point
            self.state['move_origin'] = dict(self.state['selection'])
            self.state['drag_start'] = None
            self.canvas.configure(cursor='fleur')
            self.area_var.set('Moving selection… release it at the new position.')
            return
        self._clear_selection()
        self.state['drag_start'] = point
        x, y = point
        self.state['selection'] = {'cx': x, 'cy': y, 'width': 0.0, 'height': 0.0, 'angle_deg': 0.0}
        self._draw_selection()

    def _drag_motion(self, event):
        point = self._canvas_point(event)
        if point is None:
            return
        selection = self.state.get('selection')
        if self.state.get('rotating') and isinstance(selection, dict):
            dx = point[0] - selection['cx']
            dy = point[1] - selection['cy']
            if float(np.hypot(dx, dy)) > 4.0:
                angle = self._normalise_angle(np.degrees(np.arctan2(dy, dx)) + 90.0)
                selection['angle_deg'] = angle
                self.rotation_var.set(round(angle, 2))
                self._draw_selection()
                self.area_var.set(f'Rotating selection: {angle:+.1f}°. Release to keep this angle.')
            return
        if self.state.get('resizing'):
            origin = self.state.get('resize_origin')
            handle_name = self.state.get('resize_handle')
            if not isinstance(origin, dict) or not handle_name:
                return
            keep_aspect = bool(int(getattr(event, 'state', 0) or 0) & 1)
            resized = _resize_rotated_rectangle(origin, handle_name, point[0], point[1], keep_aspect=keep_aspect, min_size=8.0)
            self.state['selection'] = resized
            self._draw_selection()
            ratio_note = ' · proportions locked' if keep_aspect else ''
            self.area_var.set(f"Resizing: {resized['width']:.0f} × {resized['height']:.0f} preview pixels{ratio_note}…")
            return
        if self.state.get('moving') and isinstance(selection, dict):
            move_start = self.state.get('move_start')
            origin = self.state.get('move_origin')
            if move_start is None or not isinstance(origin, dict):
                return
            selection['cx'] = float(origin['cx']) + point[0] - move_start[0]
            selection['cy'] = float(origin['cy']) + point[1] - move_start[1]
            report = self.state.get('report') or {}
            preview_w = int(report.get('preview_width') or 0)
            preview_h = int(report.get('preview_height') or 0)
            moved_corners = self._selection_corners()
            if moved_corners is not None and preview_w > 0 and (preview_h > 0):
                min_x, min_y = np.min(moved_corners, axis=0)
                max_x, max_y = np.max(moved_corners, axis=0)
                if min_x < 0:
                    selection['cx'] -= min_x
                if max_x > preview_w - 1:
                    selection['cx'] -= max_x - (preview_w - 1)
                if min_y < 0:
                    selection['cy'] -= min_y
                if max_y > preview_h - 1:
                    selection['cy'] -= max_y - (preview_h - 1)
            self._draw_selection()
            self.area_var.set(f"Moving selection: center {selection['cx']:.0f}, {selection['cy']:.0f} preview pixels…")
            return
        start = self.state.get('drag_start')
        if start is None:
            return
        x0, y0 = start
        x1, y1 = point
        self.state['selection'] = {'cx': (x0 + x1) / 2.0, 'cy': (y0 + y1) / 2.0, 'width': abs(x1 - x0), 'height': abs(y1 - y0), 'angle_deg': 0.0}
        self._draw_selection()
        self.area_var.set(f'Selecting {abs(x1 - x0):.0f} × {abs(y1 - y0):.0f} preview pixels…')

    def _drag_end(self, event):
        if self.state.get('resizing'):
            self.state['resizing'] = False
            self.state['resize_handle'] = None
            self.state['resize_origin'] = None
            self.canvas.configure(cursor='crosshair')
            region = self._selection_to_region()
            selection = self.state.get('selection') or {}
            if region:
                self.use_area_btn.configure(state='normal')
                self.area_var.set(f"Resized to {float(selection.get('width', 0)):.0f} × {float(selection.get('height', 0)):.0f} pixels, rotation {region['rotation_deg']:+.1f}°. Click ‘Use selected area for Mosaic’.")
            return
        if self.state.get('rotating'):
            self.state['rotating'] = False
            self.canvas.configure(cursor='')
            region = self._selection_to_region()
            if region:
                self.use_area_btn.configure(state='normal')
                self.area_var.set(f"Selected center RA {region['center_ra']:.6f}, Dec {region['center_dec']:.6f}, rotation {region['rotation_deg']:+.1f}°. Click ‘Use selected area for Mosaic’.")
            return
        if self.state.get('moving'):
            self.state['moving'] = False
            self.state['move_start'] = None
            self.state['move_origin'] = None
            self.canvas.configure(cursor='fleur')
            region = self._selection_to_region()
            if region:
                self.use_area_btn.configure(state='normal')
                self.area_var.set(f"Moved selection to RA {region['center_ra']:.6f}, Dec {region['center_dec']:.6f}, rotation {region['rotation_deg']:+.1f}°. Click ‘Use selected area for Mosaic’.")
            return
        start = self.state.get('drag_start')
        point = self._canvas_point(event)
        self.state['drag_start'] = None
        if start is None or point is None:
            return
        x0, y0 = start
        x1, y1 = point
        x0, x1 = sorted((x0, x1))
        y0, y1 = sorted((y0, y1))
        if x1 - x0 < 6 or y1 - y0 < 6:
            self._clear_selection()
            return
        self.state['selection'] = {'cx': (x0 + x1) / 2.0, 'cy': (y0 + y1) / 2.0, 'width': x1 - x0, 'height': y1 - y0, 'angle_deg': 0.0}
        self.rotation_var.set(0.0)
        for widget in (self.angle_spin, self.rotate_left_btn, self.rotate_right_btn, self.reset_rotation_btn):
            widget.configure(state='normal')
        self._draw_selection()
        region = self._selection_to_region()
        if region:
            self.use_area_btn.configure(state='normal')
            self.area_var.set(f"Selected sky area centered at RA {region['center_ra']:.6f}, Dec {region['center_dec']:.6f}, rotation 0.0°. Click ‘Use selected area for Mosaic’.")

    def _set_rotation(self, value):
        selection = self.state.get('selection')
        if not isinstance(selection, dict):
            return
        try:
            angle = self._normalise_angle(value)
        except EXPECTED_OPERATION_ERRORS:
            angle = float(selection.get('angle_deg', 0.0))
        selection['angle_deg'] = angle
        self.rotation_var.set(round(angle, 2))
        self._draw_selection()
        region = self._selection_to_region()
        if region:
            self.use_area_btn.configure(state='normal')
            self.area_var.set(f"Selected center RA {region['center_ra']:.6f}, Dec {region['center_dec']:.6f}, rotation {angle:+.1f}°. Click ‘Use selected area for Mosaic’.")

    def _angle_changed(self, _event=None):
        self._set_rotation(self.rotation_var.get())

    def _wheel_rotate(self, event):
        if not isinstance(self.state.get('selection'), dict):
            return
        direction = 1.0 if getattr(event, 'delta', 0) > 0 else -1.0
        self._set_rotation(float(self.rotation_var.get()) + direction * 2.0)
        return 'break'

    def _inside_polygon(self, x, y, polygon):
        inside = False
        if len(polygon) < 3:
            return False
        j = len(polygon) - 1
        for i in range(len(polygon)):
            xi, yi = polygon[i]
            xj, yj = polygon[j]
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                inside = not inside
            j = i
        return inside

    def _hover(self, event):
        if self.state.get('drag_start') is not None or self.state.get('rotating') or self.state.get('resizing') or self.state.get('moving'):
            return
        point = self._canvas_point(event)
        report = self.state.get('report') or {}
        header_text = report.get('preview_wcs_header')
        if point is None or not header_text:
            return
        x, y = point
        selection = self.state.get('selection')
        if isinstance(selection, dict):
            handle = self.state.get('rotation_handle')
            if handle is not None and float(np.hypot(x - handle[0], y - handle[1])) <= 16.0:
                self.canvas.configure(cursor='crosshair')
                self.area_var.set('Drag the round handle to rotate the selection.')
                return
            for name, resize_point in (self.state.get('resize_handles') or {}).items():
                if float(np.hypot(x - resize_point[0], y - resize_point[1])) <= 13.0:
                    self.canvas.configure(cursor='crosshair')
                    action = 'resize width and height' if len(name) == 2 else 'resize one side'
                    self.area_var.set(f'Drag the {name.upper()} square handle to {action}. Hold Shift on a corner to keep proportions.')
                    return
            corners = self._selection_corners()
            if corners is not None and self._inside_polygon(x, y, corners):
                self.canvas.configure(cursor='fleur')
                self.area_var.set('Drag anywhere inside the box to move it. Drag the round handle to rotate.')
                return
            self.canvas.configure(cursor='crosshair')
        else:
            self.canvas.configure(cursor='crosshair')
        try:
            preview_wcs = WCS(fits.Header.fromstring(header_text, sep='\n'), naxis=2)
            ra, dec = preview_wcs.all_pix2world([x], [y], 0)
            hits = []
            for tile in report.get('tiles') or []:
                if self._inside_polygon(x, y, tile.get('polygon') or []):
                    frac = tile.get('valid_fraction')
                    frac_txt = f'{float(frac):.1f}% data' if frac is not None else 'data fraction unknown'
                    hits.append(f"{tile.get('filename') or 'tile'} ({frac_txt})")
            suffix = ' | ' + '; '.join(hits[:3]) if hits else ''
            self.area_var.set(f'RA {float(ra[0]):.6f}, Dec {float(dec[0]):.6f}{suffix}')
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)

    def _use_selection(self):
        region = self._selection_to_region()
        if not region:
            messagebox.showinfo('Select a sky area', 'Drag a rectangle over the background image first.', parent=self.win)
            return
        if self.on_region is not None:
            try:
                self.on_region(region)
            except EXPECTED_OPERATION_ERRORS as exc:
                messagebox.showerror('Could not use selected area', str(exc), parent=self.win)
                return
        self.area_var.set('Selected area has been sent to the Mosaic tab.')

__all__ = [name for name in globals() if not name.startswith('__')]
