from __future__ import annotations

from tsdp2._deps import *
from tsdp2.logging_config import LOGGER
from tsdp2.config import _html_escape, _safe_int, path_exists_or_on_path
from tsdp2.astronomy.reference import text_progress_bar
from tsdp2.downloads.aria2 import guess_name_from_url
from tsdp2.downloads.pipeline import PipelineRunner
from tsdp2.utilities.threading import post_to_ui
from tsdp2.telegram.parsing import *
from tsdp2.telegram.output_state import _tg_record_outputs
from tsdp2.telegram.output_delivery import (
    _tg_csv_product_shortcut,
    _tg_handle_incoming_csv,
    _tg_handle_outputs_command,
    _tg_handle_zcb_command,
    _tg_send_blink_gif_worker,
    _tg_send_csv_worker,
)
from tsdp2.recovery.cleanup import _tg_do_flush, active_flush_jobs

def _tg_cmd_status_text(self):
    step = "idle"
    txt = "No active pipeline"
    pct = 0
    try:
        pct = float(self.pipe_prog["value"] or 0)
        txt = self.pipe_lbl.cget("text") or txt
        if self.pipeline_runner and self.pipeline_runner.is_alive():
            step = "running"
    except EXPECTED_OPERATION_ERRORS:
        LOGGER.debug("Best-effort operation failed", exc_info=True)
    return self._pipeline_dashboard_text(step, txt, pct)


def _tg_start_newjob(self, tg):
    if getattr(self, "_tg_newjob", None):
        tg.send("A guided job setup is already active. Send /canceljob first.")
        return
    if getattr(self, "_tg_active_batch", None):
        tg.send("A Telegram download batch is already active. Wait for it to finish or send /cancel.")
        return
    try:
        if self.pipeline_runner and self.pipeline_runner.is_alive():
            tg.send("A pipeline is already running. Wait for it to finish or send /stop.")
            return
        if self.aligner_ref is not None and getattr(self.aligner_ref, "_align_running", False):
            tg.send("An alignment job is already running. Wait for it to finish or send /stop.")
            return
    except EXPECTED_OPERATION_ERRORS:
        LOGGER.debug("Best-effort operation failed", exc_info=True)
    self._tg_newjob = {
        "state": "object",
        "created": time.time(),
        "token": f"{time.time():.6f}-{id(self)}",
    }
    tg.send(
        "🔭 <b>New MAST job</b>\n\n"
        "Send the object name/code.\n\n"
        "Examples:\n"
        "NGC 7293\n"
        "M16\n"
        "M42\n"
        "Pillars of Creation\n\n"
        "Send /canceljob to stop setup."
    )


def _tg_cancel_newjob(self, tg):
    self._tg_newjob = None
    tg.send("🛑 New job setup cancelled.")


def _tg_job_still_current(self, job) -> bool:
    """True only if this background search/fetch belongs to the live guided job."""
    try:
        cur = getattr(self, "_tg_newjob", None) or {}
        return bool(job and cur and cur.get("token") == job.get("token"))
    except EXPECTED_OPERATION_ERRORS:
        return False


def _tg_handle_newjob_input(self, text, tg):
    job = getattr(self, "_tg_newjob", None)
    if not job:
        return False
    state = job.get("state")
    clean = (text or "").strip()
    lower = clean.lower()

    if lower in ("/canceljob", "canceljob", "cancel job", "cancel", "/cancel"):
        _tg_cancel_newjob(self, tg)
        return True

    if state == "object":
        if len(clean) < 2:
            tg.send("Object name is too short. Send something like NGC 7293 or M16.")
            return True
        job["object"] = clean
        job["state"] = "telescope"
        tg.send(
            f"Object set: <b>{_html_escape(clean)}</b>\n\n"
            "Now send the telescope/archive.\n\n"
            "Examples: JWST, HST, Hubble, Spitzer, GALEX, Herschel, Chandra, any"
        )
        return True

    if state == "telescope":
        tel = _tg_norm_telescope(clean)
        job["telescope_raw"] = clean
        job["telescope"] = tel
        job["state"] = "task"
        shown = tel or "Any archive"
        tg.send(
            f"Telescope set: <b>{_html_escape(shown)}</b>\n\n"
            "Now send the task.\n\n"
            "Options:\n"
            "download\n"
            "align\n"
            "mosaic\n"
            "mosaic_align"
        )
        return True

    if state == "task":
        mode = _tg_norm_task(clean)
        if not mode:
            tg.send(
                "I did not understand the task. Send one of:\n"
                "download\nalign\nmosaic\nmosaic_align"
            )
            return True
        job["mode"] = mode
        if _tg_task_needs_ref(mode):
            job["state"] = "reference"
            tg.send(
                f"Task set: <b>{PipelineRunner.MODES.get(mode, mode)}</b>\n\n"
                "This task needs a reference mode. Send:\n"
                "highest\n"
                "ordinary"
            )
        else:
            job["reference"] = ""
            job["state"] = "product"
            tg.send(
                f"Task set: <b>{PipelineRunner.MODES.get(mode, mode)}</b>\n\n"
                "Now send product type(s).\n\n"
                "Examples:\n"
                "i2d\n"
                "cal\n"
                "rate\n"
                "drc drz\n"
                "drc,drz\n"
                "any"
            )
        return True

    if state == "reference":
        ref = _tg_norm_ref(clean)
        if not ref:
            tg.send("Reference mode not understood. Send highest or ordinary.")
            return True
        job["reference"] = ref
        job["state"] = "product"
        tg.send(
            f"Reference mode set: <b>{ref}</b>\n\n"
            "Now send product type(s).\n\n"
            "Examples:\n"
            "i2d\n"
            "cal\n"
            "rate\n"
            "drc drz\n"
            "drc,drz\n"
            "any"
        )
        return True

    if state == "product":
        products = _tg_parse_products(clean)
        if not products:
            tg.send("Send product type(s), for example i2d, cal, rate, drc, drz, drc drz, or any.")
            return True
        job["products"] = products
        job["product"] = _tg_product_label(products)  # compatibility with old code paths
        job["state"] = "searching"
        tg.send(
            "🔎 Searching MAST now…\n\n"
            f"Object: <b>{_html_escape(job.get('object'))}</b>\n"
            f"Telescope: <b>{_html_escape(job.get('telescope') or 'Any')}</b>\n"
            f"Task: <b>{PipelineRunner.MODES.get(job.get('mode'), job.get('mode'))}</b>\n"
            f"Products: <b>{_html_escape(_tg_product_label(products))}</b>"
        )
        threading.Thread(target=_tg_search_mast_job, args=(self, dict(job), tg), daemon=True).start()
        return True

    if state == "sort":
        sort_mode = _tg_norm_sort(clean)
        if not sort_mode:
            tg.send(
                "Sort option not understood. Send one of:\n"
                "date released\n"
                "obs id\n"
                "target name\n"
                "keep"
            )
            return True
        raw_rows = job.get("raw_rows") or job.get("rows") or []
        rows = _tg_sort_observation_rows(raw_rows, sort_mode)
        job["sort"] = sort_mode
        job["rows"] = rows
        if sort_mode == "keep" and job.get("raw_csv_path"):
            job["csv_path"] = job.get("raw_csv_path")
            doc_sent = False
        else:
            job["csv_path"] = _tg_write_observation_csv(rows, job.get("object", "target"), job.get("telescope") or "MAST")
            doc_sent = True
        job["state"] = "rows"
        self._tg_newjob = job
        try:
            _tg_record_outputs(self, csvs=[job["csv_path"]], label="mast csv")
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)
        if doc_sent:
            tg.send_document(job["csv_path"], caption=f"Sorted MAST observations for {job.get('object')} ({_tg_sort_label(sort_mode)})")
        tg.send(
            f"✅ CSV order set to <b>{_html_escape(_tg_sort_label(sort_mode))}</b>.\n\n"
            f"First rows:\n<pre>{_tg_observation_preview(rows)}</pre>\n\n"
            "Reply with the rows to download.\n"
            "Examples: <code>1</code>, <code>2,4,7</code>, <code>5-12</code>, <code>all</code>"
        )
        return True

    if state == "rows":
        rows = job.get("rows") or []
        selected = _tg_parse_row_selection(clean, len(rows))
        if not selected:
            tg.send(
                f"No valid row numbers found. There are {len(rows)} rows.\n"
                "Examples: 1,3,5-8 or all"
            )
            return True
        job["selected_rows"] = selected
        job["state"] = "fetching_products"
        tg.send(
            f"📦 Selected {len(selected)} observation row(s). Fetching product download URLs…\n"
            f"Products: <b>{_html_escape(_tg_product_label(job.get('products') or job.get('product')))}</b>"
        )
        threading.Thread(target=_tg_fetch_products_for_job, args=(self, dict(job), tg), daemon=True).start()
        return True

    if state in ("searching", "fetching_products", "queueing"):
        tg.send("Still working. Wait for the next prompt, or send /canceljob to cancel setup.")
        return True

    return False


def _tg_search_mast_job(self, job, tg):
    try:
        if not _tg_job_still_current(self, job):
            return
        try:
            from astroquery.mast import Observations
        except ImportError:
            tg.send(
                "❌ astroquery is not installed.\n\n"
                "Install it with:\n"
                "<code>pip install astroquery</code>\n\n"
                "Then restart TSDP."
            )
            self._tg_newjob = None
            return

        obj = job.get("object", "").strip()
        tel = job.get("telescope", "")
        table = Observations.query_object(obj, radius="0.25 deg")
        rows = _tg_table_to_dicts(table)
        if not _tg_job_still_current(self, job):
            return
        if tel:
            tel_u = tel.upper()
            rows = [r for r in rows if _tg_s(_tg_obs_value(r, "obs_collection", "")).upper() == tel_u]

        if not rows:
            tg.send(
                "❌ No observations found.\n\n"
                f"Object: <b>{obj}</b>\n"
                f"Telescope: <b>{tel or 'Any'}</b>\n\n"
                "Try /newjob again with telescope set to any, or use a different target name."
            )
            self._tg_newjob = None
            return

        # Keep the CSV sane. MAST can return a giant pile of rows for broad targets.
        rows = rows[:250]
        raw_csv_path = _tg_write_observation_csv(rows, obj, tel or "MAST")
        job["raw_rows"] = rows
        job["rows"] = rows
        job["raw_csv_path"] = raw_csv_path
        job["csv_path"] = raw_csv_path
        job["state"] = "sort"
        try:
            _tg_record_outputs(self, csvs=[raw_csv_path], label="mast csv")
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)
        if not _tg_job_still_current(self, job):
            return
        self._tg_newjob = job

        tg.send_document(raw_csv_path, caption=f"MAST observations for {obj} ({tel or 'Any archive'}) — original order")
        tg.send(
            f"✅ Found <b>{len(rows)}</b> observation row(s) for <b>{_html_escape(obj)}</b>.\n\n"
            f"First rows in original MAST order:\n<pre>{_tg_observation_preview(rows)}</pre>\n\n"
            "How should I order the CSV before row selection?\n\n"
            "Reply with one of:\n"
            "<code>date released</code> - newest first\n"
            "<code>obs id</code>\n"
            "<code>target name</code>\n"
            "<code>keep</code> - use the original CSV I just sent"
        )
    except EXPECTED_OPERATION_ERRORS as exc:
        self._tg_newjob = None
        tg.send(f"❌ MAST search failed:\n<pre>{str(exc)[:1200]}</pre>")


def _tg_filter_product_table(product_list, product):
    """Return only science FITS products matching one or more requested subgroups.

    Product input may be a string or list, for example:
        "drc"
        "drc, drz"
        ["drc", "drz"]
        "any"

    ``any`` means any SCIENCE FITS product, not previews/metadata/junk.
    """
    products = product if isinstance(product, (list, tuple, set)) else _tg_parse_products(product)
    products = list(products or ["any"])
    products = [str(p).strip().lower() for p in products if str(p).strip()]
    if not products:
        products = ["any"]
    any_product = "any" in products or "all" in products

    def _row_value(row, names, key):
        try:
            return _tg_s(row[key] if key in names else "")
        except EXPECTED_OPERATION_ERRORS:
            return ""

    def _matches_product(prod, fn, subgroup):
        prod = (prod or "").lower().strip()
        if not prod:
            return False
        return (
            f"_{prod}." in fn
            or f"_{prod}_" in fn
            or fn.endswith(f"_{prod}.fits")
            or fn.endswith(f"_{prod}.fit")
            or subgroup == prod
            or prod in subgroup
        )

    keep_idx = []
    try:
        names = set(product_list.colnames)
    except EXPECTED_OPERATION_ERRORS:
        names = set()

    for i, row in enumerate(product_list):
        fn = _row_value(row, names, "productFilename").lower()
        uri = _row_value(row, names, "dataURI").lower()
        subgroup = _row_value(row, names, "productSubGroupDescription").lower()
        ptype = _row_value(row, names, "productType").lower()
        ext_source = fn or uri

        if ptype and ptype != "science":
            continue
        if not (ext_source.endswith(".fits") or ext_source.endswith(".fit") or ext_source.endswith(".fts")
                or ".fits" in ext_source):
            continue

        if any_product:
            keep_idx.append(i)
            continue

        if any(_matches_product(prod, fn, subgroup) for prod in products):
            keep_idx.append(i)

    try:
        return product_list[keep_idx]
    except EXPECTED_OPERATION_ERRORS:
        try:
            return product_list[:0]
        except EXPECTED_OPERATION_ERRORS:
            return product_list


def _tg_fetch_products_for_job(self, job, tg):
    try:
        if not _tg_job_still_current(self, job):
            return
        try:
            from astroquery.mast import Observations
        except ImportError:
            tg.send("❌ astroquery is not installed. Run <code>pip install astroquery</code> and restart.")
            self._tg_newjob = None
            return

        rows = job.get("rows") or []
        selected_nums = job.get("selected_rows") or []
        selected_rows = [rows[i - 1] for i in selected_nums if 1 <= i <= len(rows)]
        numeric_ids = []
        for r in selected_rows:
            obsid = _tg_s(_tg_obs_value(r, "obsid", "")).strip()
            if obsid and obsid.lower() not in ("nan", "none", "-999"):
                numeric_ids.append(obsid)

        if not numeric_ids:
            tg.send("❌ Selected rows do not have numeric obsid values, so product lookup cannot continue.")
            self._tg_newjob = None
            return

        product_list = Observations.get_product_list(numeric_ids)
        if not _tg_job_still_current(self, job):
            return
        filtered = _tg_filter_product_table(product_list, job.get("products") or job.get("product", "any"))
        urls = []
        total_size = 0
        seen = set()
        for row in filtered:
            try:
                uri = _tg_s(row["dataURI"]).strip()
                try:
                    fn = _tg_s(row["productFilename"]).strip()
                except EXPECTED_OPERATION_ERRORS:
                    fn = ""
                fn = fn or guess_name_from_url(f"https://mast.stsci.edu/api/v0.1/Download/file?uri={uri}") or os.path.basename(uri)
            except EXPECTED_OPERATION_ERRORS:
                continue
            if not uri:
                continue
            key = (uri, fn)
            if key in seen:
                continue
            seen.add(key)
            urls.append((f"https://mast.stsci.edu/api/v0.1/Download/file?uri={quote(uri, safe='')}", fn))
            try:
                total_size += int(row["size"])
            except EXPECTED_OPERATION_ERRORS:
                LOGGER.debug("Best-effort operation failed", exc_info=True)

        if not urls:
            tg.send(
                "❌ MAST returned no downloadable products for that selection.\n\n"
                f"Products requested: <b>{_html_escape(_tg_product_label(job.get('products') or job.get('product')))}</b>\n"
                "Try /newjob again with product type set to any."
            )
            self._tg_newjob = None
            return

        job["urls"] = urls
        job["total_size"] = total_size
        job["state"] = "queueing"
        if not _tg_job_still_current(self, job):
            return
        self._tg_newjob = job
        post_to_ui(_tg_queue_job_urls_on_ui, self, dict(job), tg)
    except EXPECTED_OPERATION_ERRORS as exc:
        self._tg_newjob = None
        tg.send(f"❌ Product lookup failed:\n<pre>{str(exc)[:1200]}</pre>")


def _tg_batch_row_name(row) -> str:
    try:
        return row.name_var.get().strip() or guess_name_from_url(row.url_var.get().strip()) or "file"
    except EXPECTED_OPERATION_ERRORS:
        return "file"


def _tg_batch_dashboard_text(self, batch, note: str = "") -> str:
    rows = [r for r in batch.get("rows", []) if r in self.rows]
    total = max(1, len(rows))
    done = sum(1 for r in rows if r.status_var.get() == "Done")
    failed = sum(1 for r in rows if r.status_var.get() in ("Error", "Stopped"))
    running = [r for r in rows if r.status_var.get() == "Running"]
    queued = sum(1 for r in rows if r.status_var.get() in ("Queued", "Starting"))

    pct_values = []
    for r in rows:
        st = r.status_var.get()
        if st == "Done" or st in ("Error", "Stopped"):
            pct_values.append(100.0)
        else:
            try:
                pct_values.append(float(r.pct_var.get() or 0))
            except EXPECTED_OPERATION_ERRORS:
                pct_values.append(0.0)
    pct = int(max(0, min(100, sum(pct_values) / total))) if rows else 0

    current = "waiting"
    if running:
        r = running[0]
        try:
            rpct = int(float(r.pct_var.get() or 0))
        except EXPECTED_OPERATION_ERRORS:
            rpct = 0
        current = f"{_tg_batch_row_name(r)} — {rpct}%"
    else:
        # Show the most recently completed/error row when nothing is actively running.
        finished = [r for r in rows if r.status_var.get() in ("Done", "Error", "Stopped")]
        if finished:
            current = _tg_batch_row_name(finished[-1])

    started = float(batch.get("tg_started") or time.time())
    elapsed_s = int(max(0, time.time() - started))
    elapsed = f"{elapsed_s//60}m {elapsed_s%60:02d}s"
    eta = "unknown"
    if pct > 0 and pct < 100:
        eta_s = int(max(0, elapsed_s * (100 - pct) / max(pct, 1)))
        eta = f"{eta_s//60}m {eta_s%60:02d}s"
    elif pct >= 100:
        eta = "0m 00s"

    obj = batch.get("object", "target")
    mode = batch.get("mode", "download")
    task = PipelineRunner.MODES.get(mode, mode)
    ref = batch.get("reference") or "not needed"
    size_txt = batch.get("total_size_text") or "size unknown"
    note = (note or "Downloading").strip()

    return (
        f"📥 <b>TSDP — { _html_escape(note) }</b>\n\n"
        f"Object\n{_html_escape(obj)}\n\n"
        f"{text_progress_bar(pct)} {pct}%\n\n"
        f"Current Stage\nDownloading\n\n"
        f"Progress\n{done + failed} / {len(rows)} file(s) finished\n"
        f"✅ Done: {done}   ❌ Failed: {failed}   ⏳ Running: {len(running)}   ⬜ Queued: {queued}\n\n"
        f"Current File\n{_html_escape(current)}\n\n"
        f"Task\n{_html_escape(task)}\n\n"
        f"Reference\n{_html_escape(ref)}\n\n"
        f"Total\n{_html_escape(size_txt)}\n\n"
        f"Elapsed\n{elapsed}\n\n"
        f"ETA\n{eta}\n\n"
        f"Folder\n<code>{_html_escape(batch.get('download_dir', ''))}</code>\n\n"
        f"{_html_escape(self._resource_text())}"
    )


def _tg_update_batch_dashboard(self, batch, force: bool = False, note: str = ""):
    if not batch:
        return
    tg = self._get_tg()
    if not tg:
        return
    now = time.time()
    last = float(batch.get("last_dashboard_edit") or 0.0)
    if not force and now - last < 2.0:
        return
    batch["last_dashboard_edit"] = now
    text = _tg_batch_dashboard_text(self, batch, note=note)

    def _worker():
        lock = getattr(self, "_tg_download_dashboard_lock", None)
        if lock is None:
            lock = threading.Lock()
            self._tg_download_dashboard_lock = lock
        with lock:
            mid = batch.get("dashboard_message_id")
            if mid:
                ok = tg.edit_dashboard(mid, text)
                if ok:
                    return
            mid = tg.send_dashboard(text)
            if mid:
                batch["dashboard_message_id"] = mid

    threading.Thread(target=_worker, daemon=True).start()


def _tg_update_active_batch_dashboard(self, force: bool = False, note: str = ""):
    batch = getattr(self, "_tg_active_batch", None)
    if batch:
        _tg_update_batch_dashboard(self, batch, force=force, note=note)


_TG_TERMINAL_DOWNLOAD_STATES = {"Done", "Error", "Stopped"}


_TG_NONTERMINAL_DOWNLOAD_STATES = {"", "Idle", "Queued", "Starting", "Running", "Stopping", "Pending"}


def _tg_rows_for_batch(self, batch):
    """Return every GUI download row that belongs to a Telegram batch job."""
    if not batch:
        return []
    job_id = batch.get("job_id")
    rows = []
    seen = set()

    # First use the authoritative job_id marker. This catches rows even if
    # batch["rows"] was stale, copied, or incomplete.
    for r in getattr(self, "rows", []):
        try:
            if getattr(r, "_tg_job_id", None) == job_id:
                rows.append(r)
                seen.add(id(r))
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)

    # Then include any rows explicitly saved in the batch object.
    for r in batch.get("rows", []) or []:
        if id(r) not in seen:
            rows.append(r)
            seen.add(id(r))
    return rows


def _tg_batch_counts(self, batch):
    rows = _tg_rows_for_batch(self, batch)
    expected = int(batch.get("expected_count") or len(rows) or 0)
    statuses = [str(getattr(r, "status_var").get() or "") for r in rows]
    done = sum(1 for s in statuses if s == "Done")
    failed = sum(1 for s in statuses if s == "Error")
    stopped = sum(1 for s in statuses if s == "Stopped")
    terminal = sum(1 for s in statuses if s in _TG_TERMINAL_DOWNLOAD_STATES)
    unfinished = max(expected - terminal, 0)
    running_workers_for_job = 0
    try:
        job_id = batch.get("job_id")
        for r in getattr(self, "running_workers", {}).values():
            if getattr(r, "_tg_job_id", None) == job_id:
                running_workers_for_job += 1
    except EXPECTED_OPERATION_ERRORS:
        running_workers_for_job = 0
    return {
        "rows": rows,
        "expected": expected,
        "statuses": statuses,
        "done": done,
        "failed": failed,
        "stopped": stopped,
        "terminal": terminal,
        "unfinished": unfinished,
        "running_workers": running_workers_for_job,
    }


def _tg_check_active_batch(self):
    batch = getattr(self, "_tg_active_batch", None)
    if not batch or batch.get("pipeline_started"):
        return

    counts = _tg_batch_counts(self, batch)
    rows = counts["rows"]
    expected = counts["expected"]

    # If the full row list has not even been created yet, do not start anything.
    if expected <= 0 or len(rows) < expected:
        try:
            _tg_update_batch_dashboard(self, batch, force=False,
                                       note=f"Preparing downloads ({len(rows)}/{expected or '?'})")
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)
        return

    # Hard wait: every product file row must be terminal AND there must be no
    # aria2 worker from this Telegram job still alive.
    if counts["terminal"] < expected or counts["running_workers"] > 0:
        try:
            note = (f"Waiting for all downloads "
                    f"({counts['terminal']}/{expected} terminal, "
                    f"{counts['done']} done, {counts['failed']} failed, "
                    f"{counts['running_workers']} running)")
            _tg_update_batch_dashboard(self, batch, force=False, note=note)
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)
        return

    # Reconstruct missing last_file_path for successful rows.
    done_rows = []
    for r in rows:
        try:
            if r.status_var.get() != "Done":
                continue
            if not getattr(r, "last_file_path", ""):
                name = r.name_var.get().strip() or guess_name_from_url(r.url_var.get().strip())
                if name:
                    candidate = os.path.abspath(os.path.join(batch.get("download_dir", ""), name))
                    if os.path.exists(candidate):
                        r.last_file_path = candidate
            if getattr(r, "last_file_path", ""):
                done_rows.append(r)
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)

    tg = self._get_tg()
    mode = batch.get("mode", "download")

    # From this point on, the batch is complete. Restore the user's auto-run
    # preference, but never allow per-file pipeline starts retroactively.
    batch["pipeline_started"] = True

    if mode == "download":
        if tg:
            _tg_update_batch_dashboard(self, batch, force=True,
                                       note=f"Download complete. {counts['done']}/{expected} file(s) downloaded")
        self._tg_active_batch = None
        try:
            self.auto_run_pipeline.set(bool(batch.get("previous_auto_run", True)))
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)
        return

    if not done_rows:
        if tg:
            _tg_update_batch_dashboard(self, batch, force=True,
                                       note=f"All {expected} downloads finished but none succeeded. Pipeline was not started")
        self._tg_active_batch = None
        try:
            self.auto_run_pipeline.set(bool(batch.get("previous_auto_run", True)))
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)
        return

    if tg:
        _tg_update_batch_dashboard(self, batch, force=True,
                                   note=f"All {expected} product file(s) finished. Starting one combined pipeline")
    try:
        self._tg_dashboard_handoff_message_id = batch.get("dashboard_message_id")
    except EXPECTED_OPERATION_ERRORS:
        self._tg_dashboard_handoff_message_id = None

    # Clear active batch immediately before starting the one legitimate pipeline.
    # This pipeline is deliberately started from the full download folder, not
    # from a single FITS file.
    dl_dir = batch.get("download_dir")
    self._tg_active_batch = None
    try:
        self.auto_run_pipeline.set(bool(batch.get("previous_auto_run", True)))
    except EXPECTED_OPERATION_ERRORS:
        LOGGER.debug("Best-effort operation failed", exc_info=True)
    self._start_pipeline_for(dl_dir)


_GLOBAL_TERMINAL_DOWNLOAD_STATES = {"Done", "Error", "Stopped"}


def _global_row_status(row):
    try:
        return str(row.status_var.get() or "")
    except EXPECTED_OPERATION_ERRORS:
        return ""


def _global_row_has_url(row):
    try:
        return bool(row.url_var.get().strip())
    except EXPECTED_OPERATION_ERRORS:
        return False


def _global_unique_rows(rows):
    out = []
    seen = set()
    for r in rows:
        if id(r) not in seen:
            out.append(r)
            seen.add(id(r))
    return out


def _global_batch_rows(self, batch=None):
    batch = batch or getattr(self, "_post_download_batch", None)
    if not batch:
        return []
    bid = batch.get("id")
    rows = []
    seen = set()
    for r in batch.get("rows", []) or []:
        if r in getattr(self, "rows", []) and id(r) not in seen:
            rows.append(r)
            seen.add(id(r))
    for r in getattr(self, "rows", []):
        try:
            if getattr(r, "_post_download_batch_id", None) == bid and id(r) not in seen:
                rows.append(r)
                seen.add(id(r))
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)
    return rows


def _global_batch_counts(self, batch=None):
    batch = batch or getattr(self, "_post_download_batch", None)
    rows = _global_batch_rows(self, batch)
    statuses = [_global_row_status(r) for r in rows]
    running = 0
    try:
        bid = batch.get("id") if batch else None
        for r in getattr(self, "running_workers", {}).values():
            if getattr(r, "_post_download_batch_id", None) == bid:
                running += 1
    except EXPECTED_OPERATION_ERRORS:
        running = 0
    done = sum(1 for s in statuses if s == "Done")
    failed = sum(1 for s in statuses if s == "Error")
    stopped = sum(1 for s in statuses if s == "Stopped")
    terminal = sum(1 for s in statuses if s in _GLOBAL_TERMINAL_DOWNLOAD_STATES)
    expected = int((batch or {}).get("expected", len(rows)) or len(rows) or 0)
    return {
        "rows": rows,
        "statuses": statuses,
        "expected": expected,
        "done": done,
        "failed": failed,
        "stopped": stopped,
        "terminal": terminal,
        "running": running,
    }


def _global_common_source_for_batch(self, batch, rows):
    """Return the correct pipeline source after a batch finishes.

    One downloaded file can be processed as that file. Two or more downloaded
    files must be processed as their containing folder/common folder so Mosaic
    and Align see the whole set, not just the last completed FITS.
    """
    done_paths = []
    for r in rows:
        try:
            if _global_row_status(r) != "Done":
                continue
            p = str(getattr(r, "last_file_path", "") or "").strip()
            if not p:
                name = r.name_var.get().strip() or guess_name_from_url(r.url_var.get().strip())
                dl_dir = getattr(r, "_download_dir_override", None) or batch.get("download_dir") or self.download_dir.get().strip()
                candidate = os.path.abspath(os.path.join(dl_dir, name)) if name else ""
                if candidate and os.path.exists(candidate):
                    p = candidate
                    r.last_file_path = p
            if p and os.path.exists(p):
                done_paths.append(os.path.abspath(p))
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)

    done_paths = list(dict.fromkeys(done_paths))
    if len(done_paths) == 1:
        return done_paths[0], done_paths

    if len(done_paths) > 1:
        parents = [os.path.dirname(p) for p in done_paths]
        try:
            common = os.path.commonpath(parents)
            if common and os.path.isdir(common):
                return common, done_paths
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)
        # Fallback to the configured batch directory.
        dl_dir = str(batch.get("download_dir") or self.download_dir.get().strip())
        return dl_dir, done_paths

    dl_dir = str(batch.get("download_dir") or self.download_dir.get().strip())
    return dl_dir, done_paths


def _tg_queue_job_urls_on_ui(self, job, tg):
    try:
        if not _tg_job_still_current(self, job):
            return
        urls = job.get("urls") or []
        if not urls:
            tg.send("❌ No URLs to queue.")
            self._tg_newjob = None
            return

        job_id = int(time.time())
        obj_safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", job.get("object", "target")).strip("_") or "target"
        base_dir = os.path.join(self.work_dir.get().strip() or str(Path.home() / "JWST_Work"), "telegram_jobs", f"{job_id}_{obj_safe}")
        dl_dir = os.path.join(base_dir, "downloads")
        os.makedirs(dl_dir, exist_ok=True)

        aria2 = self.aria2_path.get().strip()
        if not aria2 or not path_exists_or_on_path(aria2):
            tg.send(
                "❌ aria2 is not configured.\n\n"
                "Set the aria2 executable in the Downloader tab first.\n"
                f"Current path: <code>{_html_escape(aria2 or 'empty')}</code>"
            )
            self._tg_newjob = None
            return

        # Telegram batch jobs are processed once after all selected downloads finish.
        # Do not overwrite the user's remembered Download dir. Each Telegram row
        # carries its own download-dir override.
        previous_auto_run = bool(self.auto_run_pipeline.get())
        self.auto_run_pipeline.set(False)
        self.pipeline_mode.set(job.get("mode", "download"))
        ref = job.get("reference", "")
        if ref:
            self.reference_mode.set(ref)

        batch_rows = []
        for url, name in urls:
            self.add_row(url=url, name=name)
            row = self.rows[-1]
            row._tg_job_id = job_id
            row._tg_no_individual_pipeline = True
            row._download_dir_override = dl_dir
            batch_rows.append(row)

        mode = job.get("mode", "download")
        self._tg_active_batch = {
            "job_id": job_id,
            "object": job.get("object", "target"),
            "mode": mode,
            "reference": ref,
            "download_dir": dl_dir,
            "rows": batch_rows,
            "pipeline_started": False,
            "tg_started": time.time(),
            "previous_auto_run": previous_auto_run,
        }

        size = job.get("total_size", 0) or 0
        size_txt = f"{size/1e9:.2f} GB" if size > 0 else "size unknown"
        self._tg_active_batch["total_size_text"] = size_txt
        _tg_update_active_batch_dashboard(self, force=True, note="Queued downloads")
        batch = self._tg_active_batch
        batch["expected_count"] = len(urls)
        batch["allow_pipeline_start"] = False
        for protected_row in _tg_rows_for_batch(self, batch):
            protected_row._tg_expected_batch_count = len(urls)
            protected_row._tg_no_individual_pipeline = True
        _tg_update_batch_dashboard(
            self,
            batch,
            force=True,
            note=f"Queued {len(urls)} product file(s). Waiting for all before pipeline",
        )
        self._tg_newjob = None

        # Start only the rows created by this Telegram job. Do not accidentally
        # start older pending GUI rows.
        limit = _safe_int(self.concurrent_limit.get(), default=1, lo=1, hi=50)
        active = sum(1 for r in self.rows if r.worker and r.worker.is_alive())
        for row in batch_rows:
            if active < limit:
                self.start_row(row)
                active += 1
            else:
                row.status_var.set("Queued")

    except EXPECTED_OPERATION_ERRORS as exc:
        try:
            if "previous_auto_run" in locals():
                self.auto_run_pipeline.set(bool(previous_auto_run))
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)
        self._tg_newjob = None
        tg.send(f"❌ Queue/start failed:\n<pre>{_html_escape(str(exc)[:1200])}</pre>")


def _tg_help_text_runtime(self):
    """Return the complete command list without chained help wrappers."""
    return (
        "🔭 <b>TSDP Telegram Commands</b>\n\n"
        "<b>Core</b>\n/help · /ping · /resources · /status · /settings · /version\n"
        "<b>Jobs</b>\n/newjob · /canceljob · /rerun · /stop · /ref highest|ordinary\n"
        "<b>Outputs</b>\n/outputs · /previews · /fits · /logs · /report · /sendfits on|off\n"
        "<b>Archives</b>\n/zipoutputs [all] · /clean temp · /csv\n"
        "<b>Inspection</b>\n/gif or /blink\n"
        "<b>Reset</b>\n/flush\n\n"
        "The guided /newjob flow asks for object, archive, task, reference mode, "
        "product types, sort order, and selected CSV rows. You can also send a MAST CSV directly."
    )



def _tg_handle_command(self, raw_text: str, tg):
    text = (raw_text or "").strip()
    lower = text.lower()
    if not lower:
        return

    if lower in ("flush:confirm",):
        _tg_do_flush(self, tg)
        return
    if lower in ("flush:cancel",):
        tg.send("Flush cancelled.")
        return
    if lower in ("/flush", "flush", "/reset", "reset"):
        running = active_flush_jobs(self)
        warning = (f"\n⚠️ Currently running: <b>{', '.join(running)}</b> — these will be STOPPED." if running else "")
        tg.send_buttons(
            "🔄 <b>Flush all tabs?</b>\nClears download queue, aligner rows, mosaic groups, "
            f"Telegram job state, and caches. Settings are kept.{warning}",
            [[{"text": "✅ Confirm flush", "callback_data": "flush:confirm"},
              {"text": "Cancel", "callback_data": "flush:cancel"}]],
        )
        return
    if lower in ("/gif", "gif", "/blink", "blink", "/blinkgif", "blinkgif"):
        threading.Thread(target=_tg_send_blink_gif_worker, args=(self, tg), daemon=True).start()
        return
    if lower.startswith("__tgcsv__:"):
        _tg_handle_incoming_csv(self, text[len("__tgcsv__:"):], tg)
        return
    if lower in ("/csv", "csv", "/csvs", "csvs"):
        threading.Thread(target=_tg_send_csv_worker, args=(self, tg), daemon=True).start()
        return
    if _tg_csv_product_shortcut(self, text, tg):
        return
    if _tg_handle_zcb_command(self, lower, text, tg):
        return
    if _tg_handle_outputs_command(self, lower, text, tg):
        return

    # Job setup cancel gets priority when a guided job is active.
    if getattr(self, "_tg_newjob", None) and lower in ("/canceljob", "canceljob", "cancel job", "cancel", "/cancel"):
        _tg_cancel_newjob(self, tg); return

    if lower in ("/help", "help", "/start", "start"):
        tg.send(_tg_help_text_runtime(self)); return
    if lower in ("/ping", "ping"):
        tg.send("✅ TSDP bot is online."); return
    if lower in ("/resources", "resources", "/cpu", "/ram", "/disk"):
        tg.send("🖥 <b>Resources</b>\n" + self._resource_text()); return
    if lower in ("/status", "status", "/progress", "progress"):
        tg.send(_tg_cmd_status_text(self)); return
    if lower in ("/settings", "settings"):
        try:
            ref = self.reference_mode.get() or "Not selected"
            mode = self.pipeline_mode.get() or "Unknown"
            enabled = "ON" if self.tg_enabled.get() else "OFF"
            active = getattr(self, "_tg_newjob", None)
            job_state = active.get("state") if active else "none"
            tg.send(f"⚙️ <b>Settings</b>\nTelegram: {enabled}\nPipeline: {mode}\nReference: {ref}\nGuided job: {job_state}")
        except EXPECTED_OPERATION_ERRORS as exc:
            tg.send(f"⚠️ Settings read failed: {exc}")
        return
    if lower in ("/version", "version"):
        tg.send(f"🔭 TSDP {APP_VERSION}"); return
    if lower.startswith("/newjob") or lower == "newjob":
        _tg_start_newjob(self, tg); return

    # Continue guided job with normal text replies.
    if getattr(self, "_tg_newjob", None) and not lower.startswith("/"):
        if _tg_handle_newjob_input(self, text, tg):
            return

    if lower in ("/stop", "stop", "/cancel", "cancel"):
        try:
            # Stop pipeline and any active Telegram batch downloads.
            post_to_ui(self.cancel_pipeline)
            batch = getattr(self, "_tg_active_batch", None)
            if batch:
                for r in list(batch.get("rows", [])):
                    try:
                        if r.worker and r.worker.is_alive():
                            r.worker.stop()
                            r.status_var.set("Stopping")
                    except EXPECTED_OPERATION_ERRORS:
                        LOGGER.debug("Best-effort operation failed", exc_info=True)
                self._tg_active_batch = None
                try:
                    self.auto_run_pipeline.set(bool(batch.get("previous_auto_run", True)))
                except EXPECTED_OPERATION_ERRORS:
                    LOGGER.debug("Best-effort operation failed", exc_info=True)
            tg.send("🛑 Cancel requested.")
        except EXPECTED_OPERATION_ERRORS as exc:
            tg.send(f"⚠️ Cancel failed: {exc}")
        return
    if lower in ("/rerun", "rerun"):
        try:
            src = str(getattr(self, "_last_pipeline_source", "") or "").strip()
            if not src:
                tg.send("No previous pipeline source is remembered yet. Start one from the GUI or /newjob first.")
                return
            post_to_ui(self._start_pipeline_for, src)
            tg.send(f"🔁 Rerun requested for:\n<code>{_html_escape(src)}</code>")
        except EXPECTED_OPERATION_ERRORS as exc:
            tg.send(f"⚠️ Rerun failed: {_html_escape(exc)}")
        return
    if lower.startswith("/ref"):
        parts = text.split(maxsplit=1)
        ref = _tg_norm_ref(parts[1] if len(parts) > 1 else "")
        if not ref:
            tg.send("Use /ref highest or /ref ordinary.")
            return
        post_to_ui(self.reference_mode.set, ref)
        tg.send(f"Reference mode set to <b>{ref}</b>.")
        return

    tg.send("Unknown command. Send /help for the commands this build supports.")

__all__ = [name for name in globals() if not name.startswith('__')]
