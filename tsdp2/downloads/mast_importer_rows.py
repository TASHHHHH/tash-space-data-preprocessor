from __future__ import annotations

from tsdp2.ui._common import *

class MastImporterRowsMixin:
    @staticmethod
    def _s(v):
        try:
            if v is None:
                return ""
            return str(v).strip()
        except EXPECTED_OPERATION_ERRORS:
            return ""

    @classmethod
    def _get(cls, row, *keys, default=""):
        for key in keys:
            try:
                if key in row:
                    v = cls._s(row.get(key, ""))
                    if v and v.lower() not in ("nan", "none", "null", "--", "-999"):
                        return v
            except EXPECTED_OPERATION_ERRORS:
                LOGGER.debug("Best-effort operation failed", exc_info=True)
        return default

    @classmethod
    def _row_key(cls, row, idx):
        base = cls._get(row, "obsid", "obs_id", "observation_id", "dataURI", "dataURL", default="")
        if not base:
            base = f"row{idx + 1}"
        # obs_id can repeat for different products, so include the visual row index.
        return f"{idx}:{base}"

    @classmethod
    def _row_source(cls, row):
        prov = cls._get(row, "provenance_name", "provenance", default="")
        coll = cls._get(row, "obs_collection", "collection", "mission", "archive", default="")
        if prov and coll:
            return f"{coll} / {prov}"
        return prov or coll

    @classmethod
    def _row_filter(cls, row):
        return cls._get(row, "filters", "filter", "filter_name", "wavelength", "band", default="")

    @classmethod
    def _row_instrument(cls, row):
        return cls._get(row, "instrument_name", "instrument", "instrument_name_mast", default="")

    @classmethod
    def _row_target(cls, row):
        return cls._get(row, "target_name", "target", "sci_target_name", "object", default="")

    @classmethod
    def _row_exptime(cls, row):
        raw = cls._get(row, "t_exptime", "exptime", "exposure_time", "exp_time", default="")
        try:
            return f"{float(raw):.0f}"
        except EXPECTED_OPERATION_ERRORS:
            return raw

    @classmethod
    def _row_calib(cls, row):
        return cls._get(row, "calib_level", "calibration_level", "calib", default="")

    @classmethod
    def _row_obs_id(cls, row):
        return cls._get(row, "obs_id", "observation_id", "obsid", default="")

    @staticmethod
    def _parse_products_text(value: str):
        fn = globals().get("_tg_parse_products")
        if callable(fn):
            out = fn(value)
            return out or ["any"]
        raw = (value or "any").strip().lower()
        raw = raw.replace("+", " ").replace("/", " ").replace(";", " ").replace("|", " ")
        toks = [t.strip() for t in re.split(r"[\s,]+", raw) if t.strip()]
        if not toks:
            return ["any"]
        toks = ["any" if t in ("all", "science", "sci", "fits", "fit") else re.sub(r"[^a-z0-9_-]+", "", t) for t in toks]
        if "any" in toks:
            return ["any"]
        seen, out = set(), []
        for t in toks:
            if t and t not in seen:
                out.append(t)
                seen.add(t)
        return out or ["any"]

    @staticmethod
    def _product_label(products):
        products = products or ["any"]
        return ", ".join(products)

__all__ = [name for name in globals() if not name.startswith('__')]
