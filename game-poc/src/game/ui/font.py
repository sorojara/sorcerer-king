"""
ui/font.py — Font loading, pygame 3.14 compat shim (Stage 3)
=============================================================

pygame.font and pygame.sysfont have a circular-import bug on
Python 3.14 + pygame 2.6.1:

    pygame.font → pygame.sysfont → pygame.font   (cycle)

This module provides a drop-in replacement using pygame._freetype,
the C-extension that has no Python-layer circular dependency.

``FTFont`` wraps ``pygame._freetype.Font`` with a ``render()`` signature
that matches ``pygame.font.Font.render()``:

    surf = font.render(text, antialias, color)   # returns Surface

so all existing call-sites in board_view / overlays / hand_view / pygame_app
require no changes.
"""

from __future__ import annotations

import os
from typing import Any

import pygame
import pygame._freetype as _ft

_ft.init()


# ── System-font search paths ───────────────────────────────────────────────
_SYSTEM_FONT_PATHS: list[str] = [
    # macOS — best Unicode / chess-glyph coverage first
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Geneva.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    # Linux
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    # Windows
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\seguisym.ttf",
]

# ── Title / display font search paths ───────────────────────────────────────
# Used only for short, ASCII-only headers and banners (section titles like
# "Players" / "Event Log", dramatic one-liners like "CHECK!" / "FINAL DUEL").
# A bold serif gives those a heavier, more "storybook" weight than the plain
# sans used for body/data text, without pulling in any new font asset — every
# path below is a font already shipped with the OS.  Never used for anything
# that needs the chess-glyph or wide-Unicode coverage _SYSTEM_FONT_PATHS was
# chosen for; load_font() remains the font for everything else.
_TITLE_FONT_PATHS: list[str] = [
    # macOS
    "/System/Library/Fonts/Supplemental/Herculanum.ttf",   # chiseled/display
    "/System/Library/Fonts/Supplemental/Georgia Bold.ttf",
    "/Library/Fonts/Georgia Bold.ttf",
    # Linux
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSerif-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSerifBold.ttf",
    # Windows
    r"C:\Windows\Fonts\georgiab.ttf",
    r"C:\Windows\Fonts\timesbd.ttf",
]


class FTFont:
    """
    Thin wrapper around ``pygame._freetype.Font`` that exposes the
    ``pygame.font.Font``-compatible API used throughout the UI:

        surf = font.render(text, antialias, color)

    The ``antialias`` parameter is accepted but ignored (FreeType always
    anti-aliases). ``color`` must be an RGB or RGBA tuple.

    ``get_height()`` / ``get_width()`` are not provided directly; use the
    returned surface's ``.get_height()`` / ``.get_width()`` instead, which
    is already how all UI code works.
    """

    def __init__(self, inner: "_ft.Font") -> None:
        self._inner = inner

    def render(
        self,
        text: str,
        antialias: bool,          # ignored — FreeType always AA
        color: tuple[int, ...],
        background: Any = None,   # ignored — transparent bg
    ) -> pygame.Surface:
        """
        Render ``text`` and return a Surface (not a (Surface, Rect) tuple).
        Matches the ``pygame.font.Font.render`` call signature.
        """
        surf, _ = self._inner.render(text, color)
        return surf


def load_font(size: int) -> FTFont:
    """
    Return an ``FTFont`` at ``size`` pixels tall.

    Searches known system paths for a Unicode-capable font that includes
    chess glyphs (♔–♟ U+2654–U+265F).  Falls back to the pygame-bundled
    freesansbold.ttf if nothing is found (glyphs render as boxes, but the
    engine stays functional).
    """
    for path in _SYSTEM_FONT_PATHS:
        if os.path.exists(path):
            try:
                inner = _ft.Font(path, size)
                return FTFont(inner)
            except Exception:
                continue
    # Bundled fallback always present inside the pygame wheel
    return FTFont(_ft.Font(None, size))


def load_title_font(size: int) -> FTFont:
    """
    Return a bold-serif ``FTFont`` at ``size`` pixels tall, for short
    display headers only (see ``_TITLE_FONT_PATHS``).

    Falls back to ``load_font(size)`` — the same sans used everywhere
    else — if no serif face is found on this system, so callers never
    need a second fallback path of their own.
    """
    for path in _TITLE_FONT_PATHS:
        if os.path.exists(path):
            try:
                inner = _ft.Font(path, size)
                return FTFont(inner)
            except Exception:
                continue
    return load_font(size)
