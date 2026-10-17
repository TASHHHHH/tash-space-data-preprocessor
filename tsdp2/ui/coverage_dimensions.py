"""Live output dimensions for the coverage area's native mosaic grids."""
from tsdp2._deps import *
from tsdp2.astronomy.coverage_geometry import coverage_output_dimensions
from tsdp2.astronomy.regions import _normalise_mosaic_downscale


class CoverageDimensionsMixin:
    def _update_output_dimensions(self):
        try:
            scale = _normalise_mosaic_downscale(self.scale_var.get())
        except ValueError:
            self.output_var.set("Output dimensions: enter a scale from 0.02 to 1.")
            self.use_area_btn.configure(state="disabled")
            return
        if not self.geometry_cache:
            self.output_var.set("Output dimensions: calculating native mosaic geometry…")
            return
        selection = self.state.get("selection")
        if selection and min(selection.get("width", 0), selection.get("height", 0)) < 1:
            self.output_var.set("Output dimensions: drag to select a larger area.")
            self.use_area_btn.configure(state="disabled")
            return
        region = self._selection_to_region() if selection else None
        dimensions = coverage_output_dimensions(
            self.geometry_cache, scale, self.filter_scales, region)
        self.output_dimensions = dimensions
        parts = []
        for code, size in dimensions.items():
            if size.get("error"):
                parts.append(f"{code}: no overlap")
            else:
                parts.append(f"{code}: {size['width']:,} × {size['height']:,} px ({size['scale']:g}×)")
        label = "Selected output" if region else "Full footprint output"
        self.output_var.set(label + " — " + "  |  ".join(parts))
        can_use = bool(region and dimensions and all(not d.get("error") for d in dimensions.values()))
        self.use_area_btn.configure(state="normal" if can_use else "disabled")

    def _on_scale_changed(self, *_args):
        self._update_output_dimensions()
        try:
            scale = _normalise_mosaic_downscale(self.scale_var.get())
        except ValueError:
            return
        if self.on_scale is not None:
            self.on_scale(scale)
