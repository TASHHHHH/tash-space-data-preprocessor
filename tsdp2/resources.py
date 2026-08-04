"""Paths to bundled application assets."""

from __future__ import annotations

from pathlib import Path


ASSET_DIR = Path(__file__).resolve().parent / "assets"


def asset_path(name: str) -> Path:
    return ASSET_DIR / name


__all__ = ["ASSET_DIR", "asset_path"]
