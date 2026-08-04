from __future__ import annotations

from tsdp2.ui._common import *

class MastImporterActionsMixin:
    def _add_to_queue(self):
        if not self._selected:
            messagebox.showwarning("Nothing selected", "Select at least one observation.", parent=self)
            return

        products = self._parse_products_text(self._product_var.get())
        selected_rows = [self._row_by_key.get(k) for k in self._selected if self._row_by_key.get(k) is not None]

        # IMPORTANT:
        # A MAST search-result CSV normally already contains the exact file the
        # user selected in the dataURL/dataURI column. Do NOT expand each obsid
        # through Observations.get_product_list() by default, because one CSV row
        # can explode into hundreds of calibration/auxiliary products. That was
        # the bug that turned an expected ~18 files into thousands.
        #
        # Correct default behavior for "Import MAST CSV": queue ONLY the files
        # listed in the CSV. Astroquery is used only as a fallback for unusual CSVs
        # that have observation ids but no direct product URL column.
        direct_urls = self._urls_from_csv(selected_rows, products)
        if direct_urls:
            self._add_btn.config(state="disabled", text="Adding CSV files…")
            self._count_lbl.config(
                text=f"Adding {len(direct_urls)} CSV-listed file(s), products: {self._product_label(products)}…"
            )
            self._finish_add(direct_urls, "CSV-listed files only")
            return

        self._add_btn.config(state="disabled", text="Querying MAST…  ⏳")
        self._count_lbl.config(
            text=f"No direct dataURL/dataURI files found. Querying MAST for {len(self._selected)} row(s), products: {self._product_label(products)}…"
        )

        def _worker():
            try:
                from astroquery.mast import Observations

                numeric_ids = []
                obs_ids = []
                for r in selected_rows:
                    obsid = self._get(r, "obsid", default="")
                    if obsid:
                        numeric_ids.append(str(obsid))
                    else:
                        obs_id = self._get(r, "obs_id", "observation_id", default="")
                        if obs_id:
                            obs_ids.append(obs_id)

                if numeric_ids:
                    product_list = Observations.get_product_list(numeric_ids)
                elif obs_ids:
                    obs_table = Observations.query_criteria(obs_id=obs_ids)
                    if len(obs_table) == 0:
                        raise RuntimeError("MAST returned no observations for these IDs.")
                    product_list = Observations.get_product_list([str(x) for x in obs_table["obsid"]])
                else:
                    raise RuntimeError("Selected CSV rows have no direct file URL and no obsid/obs_id column. Cannot query products.")

                filtered = self._filter_product_table(product_list, products)
                urls, total_size, seen = [], 0, set()
                for row in filtered:
                    uri = self._s(row["dataURI"] if "dataURI" in filtered.colnames else "")
                    fn = self._s(row["productFilename"] if "productFilename" in filtered.colnames else "") or self._filename_from_uri(uri)
                    if not uri:
                        continue
                    key = (uri, fn)
                    if key in seen:
                        continue
                    seen.add(key)
                    urls.append((self._mast_download_url(uri), fn))
                    try:
                        total_size += int(row["size"])
                    except EXPECTED_OPERATION_ERRORS:
                        LOGGER.debug("Best-effort operation failed", exc_info=True)

                size_str = (f"{total_size / 1e9:.1f} GB" if total_size > 1e9
                            else f"{total_size / 1e6:.0f} MB" if total_size > 0
                            else "size unknown")
                post_to_ui(self._finish_add, urls,
                           size_str + " (MAST-expanded fallback)")

            except ImportError:
                post_to_ui(self._astroquery_missing)
            except EXPECTED_OPERATION_ERRORS as exc:
                err = str(exc)
                post_to_ui(self._query_error, err)

        threading.Thread(target=_worker, daemon=True).start()

    def _urls_from_csv(self, rows, products):
        urls = []
        seen = set()
        for r in rows:
            uri = self._get(r, "dataURI", "dataURL", "data_url", "productURL", "product_url", "download_url", default="")
            if not uri:
                continue
            fn = self._filename_from_uri(uri)
            if not self._product_matches_filename(fn, products):
                continue
            url = self._mast_download_url(uri)
            key = (url, fn)
            if key in seen:
                continue
            seen.add(key)
            urls.append((url, fn))
        return urls

    def _finish_add(self, urls: list, size_str: str):
        if not urls:
            messagebox.showwarning(
                "No products found",
                "MAST returned no matching SCIENCE FITS products for the selected rows.\n\n"
                "Try products: any, i2d, cal, rate, drc, drz, flt, flc, or a space-separated pair like drc drz.",
                parent=self)
            self._add_btn.config(state="normal", text="Add to Download Queue  →")
            self._update_count()
            return
        messagebox.showinfo(
            "Ready",
            f"{len(urls)} file(s) queued  ·  {size_str} total\n\n"
            "Click «Start All» to begin downloading.",
            parent=self)
        self._on_add(urls)
        self.destroy()

    def _query_error(self, msg: str):
        messagebox.showerror("MAST query failed", msg, parent=self)
        self._add_btn.config(state="normal", text="Add to Download Queue  →")
        self._update_count()

    def _astroquery_missing(self):
        messagebox.showerror(
            "astroquery not installed",
            "Install it with:\n\n  pip install astroquery\n\nthen restart TSDP.\n\n"
            "Without astroquery, CSV fallback only works when the CSV itself includes dataURI or dataURL FITS links.",
            parent=self)
        self._add_btn.config(state="normal", text="Add to Download Queue  →")
        self._update_count()

__all__ = [name for name in globals() if not name.startswith('__')]
