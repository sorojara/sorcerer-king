"""
ui/archetype_colors.py — Stage 6: per-archetype aura colors for summoned pieces
=================================================================================

Colors live in data/archetype_colors.yaml (a config file, not hardcoded in
Python) so the palette can be retuned without touching code. Loaded once
and cached; falls back to a hardcoded default if the file is missing or
malformed so a bad config never crashes the renderer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

_DATA_PATH = Path(__file__).parent.parent / "data" / "archetype_colors.yaml"

# Used only if archetype_colors.yaml is missing/unreadable — the file's own
# "default" key is what actually governs unlisted archetypes in normal use.
_FALLBACK_DEFAULT: tuple[int, int, int] = (139, 90, 43)  # brown

_cache: dict[str, Any] | None = None


def _load() -> dict[str, Any]:
    global _cache
    if _cache is not None:
        return _cache

    default = _FALLBACK_DEFAULT
    colors: dict[str, tuple[int, int, int]] = {}
    try:
        import yaml
        raw = yaml.safe_load(_DATA_PATH.read_text()) or {}
        if "default" in raw:
            default = tuple(raw["default"])  # type: ignore[assignment]
        colors = {
            name: tuple(rgb)  # type: ignore[misc]
            for name, rgb in (raw.get("colors") or {}).items()
        }
    except Exception:
        pass  # keep the hardcoded fallback — a bad config must not crash the UI

    _cache = {"default": default, "colors": colors}
    return _cache


def aura_color_for(archetype: "str | None") -> tuple[int, int, int]:
    """
    Return the RGB aura color for ``archetype``, or the configured default
    (brown, unless overridden in archetype_colors.yaml) if it's unknown or
    None (e.g. a piece with no Monster, or a Monster with no archetype set).
    """
    data = _load()
    if not archetype:
        return data["default"]
    return data["colors"].get(archetype, data["default"])


def reload() -> None:
    """Drop the cache — mainly useful for tests that edit the YAML file."""
    global _cache
    _cache = None
