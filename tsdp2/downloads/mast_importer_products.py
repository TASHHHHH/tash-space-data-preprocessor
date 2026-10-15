from __future__ import annotations

from tsdp2.ui._common import *

class MastImporterProductsMixin:
    @staticmethod
    def _mast_download_url(uri: str):
        uri = str(uri or "").strip()
        if not uri:
            return ""
        if uri.lower().startswith("http://") or uri.lower().startswith("https://"):
            return uri
        return "https://mast.stsci.edu/api/v0.1/Download/file?uri=" + quote(uri, safe="")

    @staticmethod
    def _filename_from_uri(uri: str):
        uri = str(uri or "").strip()
        if not uri:
            return "download.fits"
        if uri.lower().startswith("http"):
            parsed = urlparse(uri)
            qs = parse_qs(parsed.query)
            if "uri" in qs and qs["uri"]:
                return MastImporterProductsMixin._filename_from_uri(qs["uri"][0])
            name = unquote(Path(parsed.path).name)
            return name or "download.fits"
        return unquote(uri.rsplit("/", 1)[-1]) or "download.fits"

    @staticmethod
    def _product_matches_filename(filename: str, products):
        filename_l = (filename or "").lower()
        if not (filename_l.endswith(".fits") or filename_l.endswith(".fit") or filename_l.endswith(".fts") or ".fits" in filename_l):
            return False
        products = [str(p).lower().strip() for p in (products or ["any"])]
        if "any" in products or "all" in products:
            return True
        for prod in products:
            if not prod:
                continue
            if filename_l.endswith(f"_{prod}.fits") or f"_{prod}." in filename_l or f"_{prod}_" in filename_l:
                return True
        return False

    def _filter_product_table(self, product_list, products):
        fn = globals().get("_tg_filter_product_table")
        if callable(fn):
            try:
                return fn(product_list, products)
            except EXPECTED_OPERATION_ERRORS:
                LOGGER.debug("Best-effort operation failed", exc_info=True)

        keep_idx = []
        try:
            names = set(product_list.colnames)
        except EXPECTED_OPERATION_ERRORS:
            names = set()

        def row_val(row, key):
            try:
                return self._s(row[key] if key in names else "")
            except EXPECTED_OPERATION_ERRORS:
                return ""

        products = products or ["any"]
        for i, row in enumerate(product_list):
            fnm = row_val(row, "productFilename") or self._filename_from_uri(row_val(row, "dataURI"))
            ptype = row_val(row, "productType").lower()
            subgroup = row_val(row, "productSubGroupDescription").lower()
            if ptype and ptype != "science":
                continue
            if "any" in products:
                if self._product_matches_filename(fnm, ["any"]):
                    keep_idx.append(i)
                continue
            if self._product_matches_filename(fnm, products) or any(str(p).lower() in subgroup for p in products):
                keep_idx.append(i)
        try:
            return product_list[keep_idx]
        except EXPECTED_OPERATION_ERRORS:
            try:
                return product_list[:0]
            except EXPECTED_OPERATION_ERRORS:
                return product_list

__all__ = [name for name in globals() if not name.startswith('__')]
