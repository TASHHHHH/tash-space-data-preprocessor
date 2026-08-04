from __future__ import annotations

from tsdp2._deps import *
from tsdp2.logging_config import LOGGER
from tsdp2.config import _html_escape

def _tg_norm_telescope(value: str) -> str:
    v = (value or "").strip().lower()
    aliases = {
        "all": "", "any": "", "none": "",
        "jwst": "JWST", "james webb": "JWST", "webb": "JWST",
        "hst": "HST", "hubble": "HST", "hubble space telescope": "HST",
        "spitzer": "SPITZER",
        "galex": "GALEX",
        "herschel": "HERSCHEL",
        "chandra": "CXO", "cxo": "CXO",
        "tess": "TESS",
        "kepler": "Kepler", "k2": "K2",
    }
    return aliases.get(v, value.strip().upper())


def _tg_norm_task(value: str):
    v = (value or "").strip().lower().replace("+", " ").replace("→", " ")
    v = re.sub(r"\s+", " ", v)
    if v in ("download", "download only", "just download", "only download", "dl"):
        return "download"
    if v in ("align", "wcs align", "download align", "download and align", "download then align"):
        return "align"
    if v in ("mosaic", "mosaic only", "download mosaic", "download and mosaic", "download then mosaic"):
        return "mosaic"
    if v in ("mosaic align", "mosaic and align", "download mosaic align", "download and mosaic and align", "download then mosaic then align", "all"):
        return "mosaic_align"
    return None


def _tg_task_needs_ref(mode: str) -> bool:
    return mode in ("align", "mosaic_align")


def _tg_norm_ref(value: str):
    v = (value or "").strip().lower()
    if v in ("highest", "highest resolution", "hi", "max", "maximum", "resolution"):
        return "Highest resolution"
    if v in ("ordinary", "ordinary best", "normal", "default", "best", "first"):
        return "Ordinary best"
    return None


def _tg_parse_products(value: str):
    """Parse Telegram product input into one or more MAST product subgroups.

    Examples accepted:
        drc
        drz
        drc,drz
        drc drz
        i2d cal rate
        any
    """
    raw = (value or "").strip().lower()
    if not raw:
        return []
    raw = raw.replace("+", " ").replace("/", " ").replace(";", " ").replace("|", " ")
    raw = raw.replace("and", " ")
    toks = [t.strip().lower() for t in re.split(r"[\s,]+", raw) if t.strip()]
    aliases = {
        "all": "any",
        "science": "any",
        "sci": "any",
        "fits": "any",
        "fit": "any",
    }
    out = []
    seen = set()
    for t in toks:
        t = aliases.get(t, t)
        t = re.sub(r"[^a-z0-9_-]+", "", t)
        if not t:
            continue
        if t in ("product", "products", "type", "types"):
            continue
        if t not in seen:
            out.append(t)
            seen.add(t)
    if "any" in seen:
        return ["any"]
    return out


def _tg_product_label(products) -> str:
    if isinstance(products, str):
        products = _tg_parse_products(products)
    products = products or ["any"]
    return ", ".join(products)


def _tg_norm_sort(value: str):
    v = (value or "").strip().lower().replace("_", " ").replace("-", " ")
    v = re.sub(r"\s+", " ", v)
    if v in ("date", "date released", "released", "release", "release date", "newest", "newest first", "time", "observation date"):
        return "date_released"
    if v in ("obs id", "obsid", "obs", "observation id", "observation", "id"):
        return "obs_id"
    if v in ("target", "target name", "name"):
        return "target_name"
    if v in ("keep", "none", "no", "default", "original", "as is", "mast", "mast order"):
        return "keep"
    return None


def _tg_sort_label(mode: str) -> str:
    return {
        "date_released": "date released, newest first",
        "obs_id": "observation ID",
        "target_name": "target name",
        "keep": "original MAST order",
    }.get(mode or "keep", "original MAST order")


def _tg_natural_key(value):
    s = _tg_s(value).lower()
    return [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", s)]


def _tg_date_sort_value(row):
    """Return a sortable release/observation date value.

    MAST columns are not perfectly consistent across archives. Prefer actual
    release-date style columns when present, then fall back to observation MJD.
    Missing dates sort last.
    """
    candidates = (
        "t_obs_release", "obs_release_date", "release_date", "data_release_date",
        "date_released", "public_release_date", "t_release", "t_obs_release_date",
        "t_max", "t_min", "date_obs", "obs_date", "start_time",
    )
    for key in candidates:
        raw = _tg_s(_tg_obs_value(row, key, "")).strip()
        if not raw or raw.lower() in ("nan", "none", "null", "--"):
            continue
        try:
            return float(raw)
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)
        # Basic ISO-ish date fallback. Keep this local so top imports stay untouched.
        try:
            from datetime import datetime
            cleaned = raw.replace("Z", "+00:00")
            return datetime.fromisoformat(cleaned).timestamp()
        except EXPECTED_OPERATION_ERRORS:
            # Final fallback: compare the raw string if it starts with YYYY-MM-DD.
            m = re.search(r"\d{4}-\d{2}-\d{2}", raw)
            if m:
                try:
                    from datetime import datetime
                    return datetime.strptime(m.group(0), "%Y-%m-%d").timestamp()
                except EXPECTED_OPERATION_ERRORS:
                    return 0.0
    return None


def _tg_sort_observation_rows(rows, mode: str):
    mode = mode or "keep"
    rows = list(rows or [])
    if mode == "keep":
        return rows
    if mode == "target_name":
        return sorted(rows, key=lambda r: (_tg_s(_tg_obs_value(r, "target_name", "")).lower(), _tg_natural_key(_tg_obs_value(r, "obs_id", ""))))
    if mode == "obs_id":
        return sorted(rows, key=lambda r: _tg_natural_key(_tg_obs_value(r, "obs_id", _tg_obs_value(r, "obsid", ""))))
    if mode == "date_released":
        # Newest first. Rows with no usable date go last.
        return sorted(rows, key=lambda r: (_tg_date_sort_value(r) is None, -float(_tg_date_sort_value(r) or 0), _tg_natural_key(_tg_obs_value(r, "obs_id", ""))))
    return rows


def _tg_parse_row_selection(text: str, max_n: int):
    raw = (text or "").strip().lower()
    if raw in ("all", "every", "everything"):
        return list(range(1, max_n + 1))
    tokens = re.findall(r"\d+\s*-\s*\d+|\d+", raw)
    chosen = []
    for tok in tokens:
        if "-" in tok:
            a, b = [int(x.strip()) for x in tok.split("-", 1)]
            if a > b:
                a, b = b, a
            chosen.extend(range(a, b + 1))
        else:
            chosen.append(int(tok))
    # Deduplicate, preserve order, clamp to CSV range.
    seen = set()
    out = []
    for n in chosen:
        if 1 <= n <= max_n and n not in seen:
            out.append(n)
            seen.add(n)
    return out


def _tg_s(value) -> str:
    try:
        if value is None:
            return ""
        return str(value)
    except EXPECTED_OPERATION_ERRORS:
        return ""


def _tg_obs_value(row, key, default=""):
    try:
        return row.get(key, default)
    except EXPECTED_OPERATION_ERRORS:
        try:
            return row[key]
        except EXPECTED_OPERATION_ERRORS:
            return default


def _tg_table_to_dicts(table):
    rows = []
    try:
        names = list(table.colnames)
    except EXPECTED_OPERATION_ERRORS:
        names = []
    for r in table:
        d = {}
        for name in names:
            try:
                val = r[name]
                if hasattr(val, "item"):
                    val = val.item()
            except EXPECTED_OPERATION_ERRORS:
                val = ""
            d[name] = _tg_s(val)
        rows.append(d)
    return rows


def _tg_write_observation_csv(rows, object_name, telescope):
    import csv as _csv
    import tempfile
    safe_obj = re.sub(r"[^A-Za-z0-9_.-]+", "_", object_name or "target").strip("_") or "target"
    safe_tel = re.sub(r"[^A-Za-z0-9_.-]+", "_", telescope or "MAST").strip("_") or "MAST"
    path = os.path.join(tempfile.gettempdir(), f"jwst_suite_{safe_obj}_{safe_tel}_observations.csv")
    cols = [
        "row", "obs_collection", "instrument_name", "filters", "target_name",
        "t_exptime", "calib_level", "dataproduct_type", "obs_id", "obsid",
        "proposal_id", "t_obs_release", "obs_release_date", "release_date",
        "data_release_date", "t_min", "t_max", "s_ra", "s_dec", "obs_title",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = _csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for i, r in enumerate(rows, 1):
            out = {c: _tg_s(_tg_obs_value(r, c, "")) for c in cols if c != "row"}
            out["row"] = i
            w.writerow(out)
    return path


def _tg_observation_preview(rows, limit=12):
    lines = []
    for i, r in enumerate(rows[:limit], 1):
        coll = _tg_s(_tg_obs_value(r, "obs_collection", "?")) or "?"
        inst = _tg_s(_tg_obs_value(r, "instrument_name", "?")) or "?"
        filt = _tg_s(_tg_obs_value(r, "filters", "")) or "no-filter"
        targ = _tg_s(_tg_obs_value(r, "target_name", "")) or "target"
        exp = _tg_s(_tg_obs_value(r, "t_exptime", ""))
        lines.append(_html_escape(f"{i}. {coll} | {inst} | {filt} | {targ} | {exp}s"))
    if len(rows) > limit:
        lines.append(_html_escape(f"… and {len(rows) - limit} more. Use the CSV for the full list."))
    return "\n".join(lines)


@dataclass(frozen=True)
class ParsedTelegramCommand:
    """Normalized Telegram command independent of Tk and network services."""

    name: str
    argument: str = ""


def parse_command(raw_text: str) -> ParsedTelegramCommand:
    """Parse a slash command, accepting Telegram's optional ``@bot`` suffix."""
    text = str(raw_text or "").strip()
    if not text:
        return ParsedTelegramCommand("")
    first, _, argument = text.partition(" ")
    name = first.lower()
    if name.startswith("/"):
        name = name[1:]
    name = name.split("@", 1)[0]
    return ParsedTelegramCommand(name=name, argument=argument.strip())


__all__ = [name for name in globals() if not name.startswith('__')]
