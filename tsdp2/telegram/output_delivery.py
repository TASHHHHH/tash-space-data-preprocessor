from __future__ import annotations

from tsdp2._deps import *
from tsdp2.logging_config import LOGGER
from tsdp2.astronomy.fits_io import load_2d_and_header
from tsdp2.astronomy.previews import make_thumbnail
from tsdp2.astronomy.reference import fits_resolution_text
from tsdp2.config import _html_escape, load_settings, save_settings
from tsdp2.downloads.mast import parse_mast_csv
from tsdp2.telegram.client import TelegramNotifier
from tsdp2.telegram.output_state import _tg_collect_outputs, _tg_output_candidate_dirs, _tg_record_outputs
from tsdp2.telegram.parsing import _tg_obs_value, _tg_observation_preview, _tg_parse_products, _tg_product_label, _tg_s

def _tg_outputs_report_text(self, files=None):
    # Opens FITS headers per file — call from worker threads only.
    files = files or _tg_collect_outputs(self)
    store = getattr(self, "_tg_recent_outputs", {}) or {}
    label = store.get("label", "outputs")
    updated = store.get("updated") or 0
    age = "unknown"
    if updated:
        dt = int(max(0, time.time() - updated))
        age = f"{dt//60}m {dt%60:02d}s ago"
    lines = [
        "📦 <b>TSDP outputs</b>",
        f"Source: {_html_escape(label)}",
        f"Tracked: {_html_escape(age)}",
        "",
        f"FITS outputs: {len(files.get('fits', []))}",
        f"Image outputs: {len(files.get('images', []))}",
        f"Logs/reports: {len(files.get('logs', []))}",
        f"CSV files: {len(files.get('csv', []))}",
    ]
    for key, title in (("fits", "FITS"), ("images", "Images"), ("logs", "Logs"), ("csv", "CSV")):
        paths = files.get(key, [])[:12]
        if paths:
            lines.append("")
            lines.append(f"<b>{title}</b>")
            for p in paths:
                try:
                    size = os.path.getsize(p) / 1e6
                    extra = ""
                    if key == "fits":
                        extra = f"  {fits_resolution_text(p)}"
                    lines.append(f"• {_html_escape(os.path.basename(p))}  {size:.1f} MB{extra}")
                except EXPECTED_OPERATION_ERRORS:
                    lines.append(f"• {_html_escape(os.path.basename(p))}")
        total = len(files.get(key, []))
        if total > 12:
            lines.append(f"... {total - 12} more")
    lines.append("")
    lines.append("Commands: /previews, /fits, /logs, /csv, /sendfits on")
    return "\n".join(lines)[:4096]


def _tg_send_previews_worker(self, tg, files=None, max_count=30):
    files = files or _tg_collect_outputs(self)
    fits = list(files.get("fits", []))
    images = list(files.get("images", []))
    sent = 0
    total = min(max_count, len(fits) + len(images))
    if total <= 0:
        tg.send("No previewable output files found yet.")
        return
    tg.send(f"🖼 Sending {total} preview(s).")

    for p in images[:max_count]:
        if sent >= max_count:
            break
        try:
            size = os.path.getsize(p)
            if size <= 10 * 1024 * 1024 and os.path.splitext(p)[1].lower() in {".png", ".jpg", ".jpeg"}:
                with open(p, "rb") as f:
                    data = f.read()
                tg.send_photo(data, caption=f"🖼 {_html_escape(os.path.basename(p))}")
            else:
                tg.send_document(p, caption=f"🖼 {_html_escape(os.path.basename(p))}")
            sent += 1
        except EXPECTED_OPERATION_ERRORS as exc:
            tg.send(f"⚠️ Preview send failed for {_html_escape(os.path.basename(p))}: {_html_escape(str(exc)[:300])}")

    for p in fits:
        if sent >= max_count:
            break
        try:
            png = TelegramNotifier.fits_to_png(p)
            if png:
                cap = f"🖼 {_html_escape(os.path.basename(p))}\n{_html_escape(fits_resolution_text(p))}"
                tg.send_photo(png, caption=cap)
                sent += 1
        except EXPECTED_OPERATION_ERRORS as exc:
            tg.send(f"⚠️ FITS preview failed for {_html_escape(os.path.basename(p))}: {_html_escape(str(exc)[:300])}")
    if len(fits) + len(images) > max_count:
        tg.send(f"Stopped at {max_count} previews to avoid Telegram spam. Use /report to list the rest.")


def _tg_send_fits_worker(self, tg, files=None, max_count=25):
    files = files or _tg_collect_outputs(self)
    fits = list(files.get("fits", []))
    if not fits:
        tg.send("No FITS output files found yet.")
        return
    if not bool(getattr(self, "_tg_send_fits_enabled", False)):
        tg.send("FITS sending is OFF. Send /sendfits on first.")
        return
    tg.send(f"📦 Sending up to {min(max_count, len(fits))} FITS document(s).")
    for p in fits[:max_count]:
        try:
            cap = f"📦 {_html_escape(os.path.basename(p))}\n{_html_escape(fits_resolution_text(p))}"
            tg.send_document(p, caption=cap)
        except EXPECTED_OPERATION_ERRORS as exc:
            tg.send(f"⚠️ FITS send failed for {_html_escape(os.path.basename(p))}: {_html_escape(str(exc)[:300])}")
    if len(fits) > max_count:
        tg.send(f"Stopped at {max_count} FITS files. Telegram uploads are capped to avoid freezing the app.")


def _tg_send_logs_worker(self, tg, files=None, max_count=8):
    files = files or _tg_collect_outputs(self)
    logs = list(files.get("logs", []))
    if not logs:
        tg.send("No log/report files found yet.")
        return
    tg.send(f"🧾 Sending {min(max_count, len(logs))} log/report file(s).")
    for p in logs[:max_count]:
        tg.send_document(p, caption=f"🧾 {_html_escape(os.path.basename(p))}")
    if len(logs) > max_count:
        tg.send(f"Stopped at {max_count} logs.")


def _tg_handle_outputs_command(self, lower, text, tg):
    if lower in ("/sendfits on", "sendfits on", "/sendfits yes", "send fits", "send fits on"):
        self._tg_send_fits_enabled = True
        try:
            cfg = load_settings(); cfg["tg_send_fits"] = True; save_settings(cfg)
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)
        tg.send("📦 FITS sending is now ON.")
        return True
    if lower in ("/sendfits off", "sendfits off", "/sendfits no", "dont send fits", "don't send fits", "send fits off"):
        self._tg_send_fits_enabled = False
        try:
            cfg = load_settings(); cfg["tg_send_fits"] = False; save_settings(cfg)
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)
        tg.send("📦 FITS sending is now OFF.")
        return True

    output_cmds = {
        "/outputs", "outputs", "/output", "output",
        "/previews", "previews", "/preview all", "preview all", "/preview", "preview",
        "/fits", "fits", "/logs", "logs", "/log", "log", "/report", "report",
    }
    if lower not in output_cmds:
        return False

    # All output collection (which may trigger a filesystem fallback scan) and
    # report building (which opens FITS headers) run in worker threads. The
    # command queue runs on the Tk main loop — it must never touch the disk.
    if lower in ("/report", "report"):
        def _report_worker():
            tg.send(_tg_outputs_report_text(self))
        threading.Thread(target=_report_worker, daemon=True).start()
        return True

    if lower in ("/logs", "logs", "/log", "log"):
        threading.Thread(target=_tg_send_logs_worker, args=(self, tg), daemon=True).start()
        return True

    if lower in ("/fits", "fits"):
        threading.Thread(target=_tg_send_fits_worker, args=(self, tg), daemon=True).start()
        return True

    if lower in ("/previews", "previews", "/preview all", "preview all", "/preview", "preview"):
        threading.Thread(target=_tg_send_previews_worker, args=(self, tg), daemon=True).start()
        return True

    # /outputs: report first, then previews, and FITS too if the toggle is on.
    def _outputs_worker():
        files = _tg_collect_outputs(self)
        tg.send(_tg_outputs_report_text(self, files))
        _tg_send_previews_worker(self, tg, files)
        if bool(getattr(self, "_tg_send_fits_enabled", False)):
            _tg_send_fits_worker(self, tg, files)
    threading.Thread(target=_outputs_worker, daemon=True).start()
    return True


_TG_ZIP_MAX_SEND_MB = 47


_TG_ZIP_MAX_INPUT_MB = 45


_TG_TEMP_DIR_NAMES = {
    "tmp", "temp", "cache", "__pycache__", ".cache",
    "scratch", "pipeline_tmp", "mosaic_tmp", "align_tmp", "alignment_tmp",
    "reproject_tmp", "reprojection_tmp", "unzip_tmp", "extract_tmp",
    "preview_tmp", "thumb_tmp", "thumbnail_tmp",
}


_TG_TEMP_FILE_SUFFIXES = (".tmp", ".part", ".aria2", ".download", ".crdownload")


def _tg_finish_keyboard():
    return [
        [
            {"text": "🖼 Previews", "callback_data": "out:previews"},
            {"text": "📦 FITS", "callback_data": "out:fits"},
            {"text": "🎞 GIF", "callback_data": "out:gif"},
        ],
        [
            {"text": "🧾 Logs", "callback_data": "out:logs"},
            {"text": "🧮 CSV", "callback_data": "out:csv"},
            {"text": "📄 Report", "callback_data": "out:report"},
        ],
        [
            {"text": "🗜 ZIP outputs", "callback_data": "out:zip"},
            {"text": "🧹 Clean temp", "callback_data": "out:clean_prompt"},
        ],
    ]


def _tg_send_finish_buttons(self, title="Job complete"):
    """Queue the finish-buttons message. Output collection can trigger a
    filesystem fallback scan and the Telegram call blocks on network, so both
    run in a worker thread — never on the Tk main loop."""
    if not bool(getattr(self, "_tg_runtime_enabled", False)):
        return
    token = str(getattr(self, "_tg_runtime_token", "") or "").strip()
    chat_id = str(getattr(self, "_tg_runtime_chat_id", "") or "").strip()
    if not token or not chat_id:
        return

    def _worker():
        try:
            files = _tg_collect_outputs(self)
            key = (title, len(files.get("fits", [])), len(files.get("images", [])), len(files.get("logs", [])))
            now = time.time()
            if getattr(self, "_tg_last_finish_buttons_key", None) == key and now - getattr(self, "_tg_last_finish_buttons_time", 0) < 20:
                return
            self._tg_last_finish_buttons_key = key
            self._tg_last_finish_buttons_time = now
            tg = TelegramNotifier(token, chat_id)
            tg.send_buttons(
                f"✅ <b>TSDP — {_html_escape(title)}</b>\n"
                f"FITS: {len(files.get('fits', []))}  |  Images: {len(files.get('images', []))}  |  Logs: {len(files.get('logs', []))}  |  CSV: {len(files.get('csv', []))}\n"
                "Choose what to send:",
                _tg_finish_keyboard(),
            )
        except EXPECTED_OPERATION_ERRORS as exc:
            try:
                LOGGER.warning("Telegram finish-button send failed: %s", exc,
                               exc_info=True)
            except EXPECTED_OPERATION_ERRORS:
                LOGGER.debug("Best-effort operation failed", exc_info=True)

    threading.Thread(target=_worker, daemon=True).start()


def _tg_unique_arcname(used, folder, path):
    base = os.path.basename(path) or "file"
    safe = re.sub(r"[^A-Za-z0-9._ -]+", "_", base).strip() or "file"
    arc = f"{folder}/{safe}"
    if arc not in used:
        used.add(arc)
        return arc
    stem, ext = os.path.splitext(safe)
    n = 2
    while True:
        arc = f"{folder}/{stem}_{n}{ext}"
        if arc not in used:
            used.add(arc)
            return arc
        n += 1


def _tg_zip_outputs_worker(self, tg, files, include_fits=False):
    try:
        import tempfile
        from datetime import datetime

        files = files or _tg_collect_outputs(self)
        images = list(files.get("images", []))
        logs = list(files.get("logs", []))
        csvs = list(files.get("csv", []))
        fits = list(files.get("fits", [])) if include_fits else []
        skipped = []
        chosen = []
        total_input = 0
        cap = int(_TG_ZIP_MAX_INPUT_MB * 1024 * 1024)

        # Report always goes in the ZIP, even if there are no files.
        report_text = _tg_outputs_report_text(self, files)

        for category, seq in (("images", images), ("logs", logs), ("csv", csvs), ("fits", fits)):
            for p in seq:
                try:
                    size = os.path.getsize(p)
                    if size <= 0:
                        skipped.append((p, "empty/unreadable"))
                        continue
                    if size > cap:
                        skipped.append((p, f"single file > {_TG_ZIP_MAX_INPUT_MB} MB"))
                        continue
                    if total_input + size > cap:
                        skipped.append((p, f"ZIP input cap {_TG_ZIP_MAX_INPUT_MB} MB reached"))
                        continue
                    chosen.append((category, p, size))
                    total_input += size
                except EXPECTED_OPERATION_ERRORS as exc:
                    skipped.append((p, str(exc)[:120]))

        if not include_fits and files.get("fits"):
            skipped.extend((p, "FITS skipped because /sendfits is off; use /zipoutputs all or /sendfits on") for p in files.get("fits", []))

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        zip_path = os.path.join(tempfile.gettempdir(), f"JWST_Suite_outputs_{stamp}.zip")
        used = set()
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            extra = [
                "",
                "ZIP contents:",
                f"included files: {len(chosen)}",
                f"input bytes: {total_input}",
                "",
                "Skipped:",
            ]
            if skipped:
                for p, reason in skipped[:200]:
                    extra.append(f"- {os.path.basename(str(p))}: {reason}")
                if len(skipped) > 200:
                    extra.append(f"... {len(skipped) - 200} more skipped")
            else:
                extra.append("none")
            zf.writestr("JWST_Suite_report.txt", re.sub(r"<[^>]*>", "", report_text) + "\n" + "\n".join(extra))
            for category, p, _size in chosen:
                zf.write(p, _tg_unique_arcname(used, category, p))

        zip_mb = os.path.getsize(zip_path) / 1e6
        tg.send(f"🗜 ZIP created: {zip_mb:.1f} MB, {len(chosen)} file(s). Sending now.")
        ok = tg.send_document(zip_path, caption=f"🗜 TSDP outputs ZIP\n{len(chosen)} file(s), {zip_mb:.1f} MB")
        if not ok:
            tg.send(
                "⚠️ ZIP could not be uploaded. It is probably over Telegram's bot upload limit.\n"
                f"Local ZIP path:\n<code>{_html_escape(zip_path)}</code>"
            )
        else:
            # Uploaded fine — remove the temp ZIP so repeated /zipoutputs calls
            # do not slowly fill the OS temp directory.
            try:
                os.remove(zip_path)
            except OSError:
                pass
    except EXPECTED_OPERATION_ERRORS as exc:
        try:
            tg.send(f"❌ ZIP failed: <pre>{_html_escape(str(exc)[:1200])}</pre>")
        except EXPECTED_OPERATION_ERRORS:
            LOGGER.debug("Best-effort operation failed", exc_info=True)


def _tg_safe_root(path):
    try:
        p = os.path.abspath(str(path))
        if not os.path.isdir(p):
            return None
        # Never clean filesystem roots or tiny paths.
        drive, tail = os.path.splitdrive(p)
        normalized_tail = tail.strip("\\/")
        if not normalized_tail or len(p) < 8:
            return None
        return p
    except EXPECTED_OPERATION_ERRORS:
        return None


def _tg_dir_size_quick(path, max_items=2000):
    total = 0
    count = 0
    try:
        for dp, _dns, fns in os.walk(path):
            for fn in fns:
                count += 1
                if count > max_items:
                    return total
                try:
                    total += os.path.getsize(os.path.join(dp, fn))
                except EXPECTED_OPERATION_ERRORS:
                    LOGGER.debug("Best-effort operation failed", exc_info=True)
    except EXPECTED_OPERATION_ERRORS:
        LOGGER.debug("Best-effort operation failed", exc_info=True)
    return total


def _tg_clean_temp_worker(self, tg):
    try:
        roots = []
        for d in _tg_output_candidate_dirs(self):
            r = _tg_safe_root(d)
            if r and r not in roots:
                roots.append(r)
        if not roots:
            tg.send("No safe output/work folders found for temp cleanup.")
            return

        removed = []
        failed = []
        freed = 0
        for root in roots[:12]:
            for dirpath, dirnames, filenames in os.walk(root, topdown=False):
                try:
                    rel = os.path.relpath(dirpath, root)
                    depth = 0 if rel == "." else len(rel.split(os.sep))
                    if depth > 6:
                        continue
                    base = os.path.basename(dirpath).lower()
                    if base in _TG_TEMP_DIR_NAMES or base.endswith("_tmp") or base.endswith("-tmp"):
                        # Do not remove the root itself, only child temp dirs.
                        if os.path.abspath(dirpath) == os.path.abspath(root):
                            continue
                        size = _tg_dir_size_quick(dirpath)
                        try:
                            shutil.rmtree(dirpath)
                            freed += size
                            removed.append(dirpath)
                        except EXPECTED_OPERATION_ERRORS as exc:
                            failed.append((dirpath, str(exc)[:160]))
                        continue
                    for fn in filenames:
                        low = fn.lower()
                        if low.endswith(_TG_TEMP_FILE_SUFFIXES):
                            p = os.path.join(dirpath, fn)
                            try:
                                size = os.path.getsize(p)
                            except EXPECTED_OPERATION_ERRORS:
                                size = 0
                            try:
                                os.remove(p)
                                freed += size
                                removed.append(p)
                            except EXPECTED_OPERATION_ERRORS as exc:
                                failed.append((p, str(exc)[:160]))
                except EXPECTED_OPERATION_ERRORS:
                    continue

        lines = [
            "🧹 <b>Temp cleanup complete</b>",
            f"Removed: {len(removed)} item(s)",
            f"Freed: {freed/1e6:.1f} MB",
        ]
        if removed:
            lines.append("")
            lines.append("Removed examples:")
            for p in removed[:12]:
                lines.append(f"• {_html_escape(os.path.basename(p))}")
            if len(removed) > 12:
                lines.append(f"... {len(removed) - 12} more")
        if failed:
            lines.append("")
            lines.append(f"Failed: {len(failed)}")
            for p, reason in failed[:5]:
                lines.append(f"• {_html_escape(os.path.basename(p))}: {_html_escape(reason)}")
        tg.send("\n".join(lines)[:4096])
    except EXPECTED_OPERATION_ERRORS as exc:
        tg.send(f"❌ Cleanup failed: <pre>{_html_escape(str(exc)[:1200])}</pre>")


def _tg_handle_zcb_callback(self, data, tg):
    data = (data or "").strip().lower()
    if data in ("out:previews", "out:preview"):
        return _tg_handle_outputs_command(self, "/previews", "/previews", tg)
    if data == "out:fits":
        return _tg_handle_outputs_command(self, "/fits", "/fits", tg)
    if data == "out:logs":
        return _tg_handle_outputs_command(self, "/logs", "/logs", tg)
    if data == "out:csv":
        threading.Thread(target=_tg_send_csv_worker, args=(self, tg), daemon=True).start()
        return True
    if data == "out:gif":
        threading.Thread(target=_tg_send_blink_gif_worker, args=(self, tg), daemon=True).start()
        return True
    if data == "out:report":
        return _tg_handle_outputs_command(self, "/report", "/report", tg)
    if data == "out:zip":
        return _tg_handle_zcb_command(self, "/zipoutputs", "/zipoutputs", tg)
    if data == "out:clean_prompt":
        def _prompt_worker():
            roots = []
            for d in _tg_output_candidate_dirs(self):
                r = _tg_safe_root(d)
                if r and r not in roots:
                    roots.append(r)
            root_lines = "\n".join(f"• <code>{_html_escape(r)}</code>" for r in roots[:8]) or "• (none found)"
            tg.send_buttons(
                "🧹 <b>Clean temporary files?</b>\n"
                "Removes only temp/cache folders and partial download files "
                "(.tmp/.part/.aria2), never final FITS/mosaic outputs.\n\n"
                f"<b>Folders that will be scanned:</b>\n{root_lines}",
                [[
                    {"text": "✅ Confirm clean", "callback_data": "out:clean_confirm"},
                    {"text": "Cancel", "callback_data": "out:cancel"},
                ]],
            )
        threading.Thread(target=_prompt_worker, daemon=True).start()
        return True
    if data == "out:clean_confirm":
        return _tg_handle_zcb_command(self, "/clean temp", "/clean temp", tg)
    if data == "out:cancel":
        tg.send("Cancelled.")
        return True
    return False


def _tg_handle_zcb_command(self, lower, text, tg):
    if lower.startswith("out:"):
        return _tg_handle_zcb_callback(self, lower, tg)

    if lower in ("/zipoutputs", "zipoutputs", "/zip outputs", "zip outputs", "/zip") or lower.startswith("/zipoutputs "):
        include_fits = bool(getattr(self, "_tg_send_fits_enabled", False)) or " all" in lower or " fits" in lower
        threading.Thread(target=_tg_zip_outputs_worker, args=(self, tg, None, include_fits), daemon=True).start()
        return True

    if lower in ("/clean temp", "clean temp", "/cleantemp", "cleantemp"):
        threading.Thread(target=_tg_clean_temp_worker, args=(self, tg), daemon=True).start()
        return True

    return False


def _tg_send_csv_worker(self, tg, files=None, max_count=10):
    files = files or _tg_collect_outputs(self)
    csvs = list(files.get("csv", []))
    if not csvs:
        tg.send("No CSV files found yet. CSVs appear here after /newjob searches "
                "or when you send me one.")
        return
    tg.send(f"🧮 Sending {min(max_count, len(csvs))} CSV file(s).")
    for p in csvs[:max_count]:
        try:
            size = os.path.getsize(p) / 1e6
            tg.send_document(p, caption=f"🧮 {_html_escape(os.path.basename(p))}  ({size:.2f} MB)")
        except EXPECTED_OPERATION_ERRORS as exc:
            tg.send(f"⚠️ CSV send failed for {_html_escape(os.path.basename(p))}: "
                    f"{_html_escape(str(exc)[:300])}")
    if len(csvs) > max_count:
        tg.send(f"Stopped at {max_count} CSV files. Use /report to list the rest.")


def _tg_download_telegram_file(tg, file_id, suggested_name="upload.csv"):
    """Download a file the user sent to the bot. Returns a local path or None."""
    import tempfile
    import urllib.request
    info = tg._post_json("getFile", {"file_id": file_id}, timeout=20)
    file_path = ((info or {}).get("result") or {}).get("file_path", "")
    if not file_path:
        return None
    url = f"https://api.telegram.org/file/bot{tg.token}/{file_path}"
    safe = re.sub(r"[^A-Za-z0-9._ -]+", "_", os.path.basename(suggested_name)) or "upload.csv"
    local = os.path.join(tempfile.gettempdir(),
                         f"tg_csv_{int(time.time())}_{safe}")
    with urllib.request.urlopen(url, timeout=60) as r, open(local, "wb") as f:
        f.write(r.read())
    return local


def _tg_handle_incoming_csv(self, payload, tg):
    """payload = '<file_id>|<file_name>'. Runs on the Tk thread; heavy work in a worker."""
    if getattr(self, "_tg_newjob", None):
        tg.send("A guided job setup is already active. Send /canceljob first, "
                "then resend the CSV.")
        return
    if getattr(self, "_tg_active_batch", None):
        tg.send("A Telegram download batch is already active. Wait for it to "
                "finish or send /cancel, then resend the CSV.")
        return

    file_id, _sep, fname = str(payload).partition("|")
    fname = fname or "upload.csv"
    tg.send(f"📥 Reading <b>{_html_escape(fname)}</b>…")

    def _worker():
        try:
            local = _tg_download_telegram_file(tg, file_id, fname)
            if not local:
                tg.send("❌ Telegram did not return the file. Try sending it again.")
                return
            rows = parse_mast_csv(local)
            if not rows:
                tg.send("❌ No observation rows found in that CSV. It must be a "
                        "MAST search-results export (comment lines starting with # are fine).")
                return
            rows = rows[:250]

            # Infer object + telescope from the rows themselves.
            from collections import Counter as _Counter
            targs = _Counter(_tg_s(_tg_obs_value(r, "target_name", "")).strip()
                             for r in rows if _tg_s(_tg_obs_value(r, "target_name", "")).strip())
            obj = targs.most_common(1)[0][0] if targs else os.path.splitext(fname)[0]
            colls = {_tg_s(_tg_obs_value(r, "obs_collection", "")).strip().upper()
                     for r in rows if _tg_s(_tg_obs_value(r, "obs_collection", "")).strip()}
            tel = colls.pop() if len(colls) == 1 else ""

            job = {
                "state": "task",
                "created": time.time(),
                "token": f"{time.time():.6f}-{id(self)}",
                "object": obj,
                "telescope": tel,
                "telescope_raw": tel,
                "raw_rows": rows,
                "rows": rows,
                "raw_csv_path": local,
                "csv_path": local,
                "csv_source": "telegram",
            }
            self._tg_newjob = job
            try:
                _tg_record_outputs(self, csvs=[local], label="telegram csv")
            except EXPECTED_OPERATION_ERRORS:
                LOGGER.debug("Best-effort operation failed", exc_info=True)
            tg.send(
                f"✅ Loaded <b>{len(rows)}</b> observation row(s) from your CSV.\n"
                f"Object: <b>{_html_escape(obj)}</b>\n"
                f"Archive: <b>{_html_escape(tel or 'mixed / any')}</b>\n\n"
                "Now send the task.\n\n"
                "Options:\n"
                "download\n"
                "align\n"
                "mosaic\n"
                "mosaic_align\n\n"
                "Send /canceljob to stop."
            )
        except EXPECTED_OPERATION_ERRORS as exc:
            tg.send(f"❌ CSV import failed:\n<pre>{_html_escape(str(exc)[:1200])}</pre>")

    threading.Thread(target=_worker, daemon=True).start()


def _tg_csv_product_shortcut(self, text, tg):
    """For CSV-sourced jobs, the product step must NOT trigger a MAST object
    search (we already have the rows). Jump straight to the sort step."""
    job = getattr(self, "_tg_newjob", None)
    if not job or job.get("csv_source") != "telegram" or job.get("state") != "product":
        return False
    clean = (text or "").strip()
    if clean.startswith("/"):
        return False
    products = _tg_parse_products(clean)
    if not products:
        tg.send("Send product type(s), for example i2d, cal, rate, drc, drz, drc drz, or any.")
        return True
    job["products"] = products
    job["product"] = _tg_product_label(products)
    job["state"] = "sort"
    self._tg_newjob = job
    rows = job.get("rows") or []
    tg.send(
        f"Products set: <b>{_html_escape(_tg_product_label(products))}</b>\n\n"
        f"Your CSV has <b>{len(rows)}</b> row(s).\n"
        f"First rows:\n<pre>{_tg_observation_preview(rows)}</pre>\n\n"
        "How should I order the rows before selection?\n\n"
        "Reply with one of:\n"
        "<code>date released</code> - newest first\n"
        "<code>obs id</code>\n"
        "<code>target name</code>\n"
        "<code>keep</code> - use the CSV order you sent"
    )
    return True


def _tg_build_blink_gif(fits_paths, max_px=640, frame_ms=700, stretch="asinh"):
    """Render FITS frames into an animated blink GIF. Returns (path, err)."""
    if not HAVE_PIL:
        return None, "Pillow is not installed"
    from PIL import ImageDraw

    frames, labels = [], []
    for p in fits_paths:
        try:
            data, _hdr = load_2d_and_header(p, preserve_nan=True)
            img = make_thumbnail(data, stretch, max_px=max_px)
            if img is None:
                continue
            frames.append(img.convert("RGB"))
            labels.append(f"{os.path.basename(p)}   {fits_resolution_text(p)}")
        except EXPECTED_OPERATION_ERRORS as exc:
            print(f"[Blink GIF] skipped {os.path.basename(str(p))}: {exc}", flush=True)
    if len(frames) < 2:
        return None, "need at least 2 readable FITS frames"

    W = max(f.width for f in frames)
    H = max(f.height for f in frames)
    bar = 22
    canvas_frames = []
    for f, lab in zip(frames, labels):
        c = Image.new("RGB", (W, H + bar), (8, 9, 14))
        c.paste(f, ((W - f.width) // 2, (H - f.height) // 2))
        d = ImageDraw.Draw(c)
        d.rectangle([0, H, W, H + bar], fill=(8, 9, 14))
        d.text((6, H + 4), lab[:90], fill=(220, 224, 240))
        canvas_frames.append(c)

    import tempfile
    from datetime import datetime
    out = os.path.join(tempfile.gettempdir(),
                       f"JWST_blink_{datetime.now().strftime('%Y%m%d_%H%M%S')}.gif")
    canvas_frames[0].save(out, save_all=True, append_images=canvas_frames[1:],
                          duration=int(frame_ms), loop=0, optimize=True)
    return out, None


def _tg_send_blink_gif_worker(self, tg, files=None, max_frames=8,
                              frame_ms=700):
    files = files or _tg_collect_outputs(self)
    fits = sorted(files.get("fits", []))[:max_frames]
    if len(fits) < 2:
        tg.send("Need at least 2 FITS outputs for a blink GIF. "
                "Run an alignment or mosaic first.")
        return
    tg.send(f"🎞 Building blink GIF from {len(fits)} frame(s)…")
    path, err = _tg_build_blink_gif(fits, frame_ms=frame_ms)
    if not path:
        tg.send(f"❌ Blink GIF failed: {_html_escape(str(err))}")
        return
    mb = os.path.getsize(path) / 1e6
    ok = tg.send_animation(
        path, caption=f"🎞 Blink comparator — {len(fits)} frame(s), {mb:.1f} MB")
    if not ok:
        ok = tg.send_document(path, caption="🎞 Blink comparator GIF")
    if ok:
        try:
            os.remove(path)
        except OSError:
            pass
    else:
        tg.send(f"⚠️ GIF upload failed. Local path:\n<code>{_html_escape(path)}</code>")

__all__ = [name for name in globals() if not name.startswith('__')]
