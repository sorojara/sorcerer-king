"""
ui/board_view.py — Board rendering (Stage 3)
=============================================

Draws:
  • 8×8 board squares with rank/file coordinate labels.
  • All chess pieces as Unicode glyphs (no image files required).
  • Selected-piece highlight (green tint on source square).
  • Legal-move dots / capture rings on destination squares.
  • Legal castling destinations (blue dot).
  • Check highlight (red tint on the King's square).

Building rendering (Stage 8+):
  Buildings are rendered in three layers that sandwich the chess piece:
    FLOOR   — stone pavement / ritual circle / ground foundation (below piece)
    BACK    — structural elements behind the unit (rear towers, altar arch)
    FRONT   — foreground elements allowed to overlap piece bottom (~15–20 %)

  The full per-square draw order is:
    board tile → territory tint → zone tints → floor → back → piece → front
    → status/range overlays → legal-move dots

  Assets are sliced from a single sprite sheet:
    data/images/buildings/basic_buildings.png  (1536 × 1024)
  Grid layout: 4 columns (icon | floor | back | front) × 3 rows
               (fortress | shrine | watchtower), 384 × 341 px per cell.

  Ownership banners are thin colored bars at the bottom of the back layer —
  green for own, muted orange for enemy, amber while under construction.
  No building name is drawn inside the square; hover the mouse to see a
  tooltip (wired in AppController._hover_tooltip_lines).

Design rules:
  • This module is PURE RENDERING — it never mutates game state.
  • It receives a PublicBoardState (from an Observation) + auxiliary
    display data passed by the AppController.  It never touches GameState.
  • All positions are in board coordinates (file 0–7, rank 0–7, rank 0 = rank 1).
  • Screen y=0 is the top; rank 7 (rank 8) is drawn at the top when
    white is at the bottom (``flip=False``).

Piece glyphs (Unicode chess symbols):
  White: ♔ ♕ ♖ ♗ ♘ ♙   (U+2654–U+2659)
  Black: ♚ ♛ ♜ ♝ ♞ ♟   (U+265A–U+265F)
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

import pygame

from game.chess.pieces import Position
from game.core.actions import Castle, MovePiece
from game.core.phases import PieceType
from game.mechanics.area import expand_area
from game.ui.colors import (
    ACTIVATABLE_DOT,
    BLACK_PIECE,
    BLACK_PIECE_SHADOW,
    BOARD_FRAME_INNER,
    BOARD_FRAME_OUTER,
    CASTLE_DOT,
    CHECK_TINT,
    COORD_DARK,
    COORD_LIGHT,
    DARK_SQUARE,
    INSPECT_TINT,
    LEGAL_CAPTURE,
    LEGAL_DOT,
    LIGHT_SQUARE,
    SELECTED_TINT,
    BUILDING_BANNER_ENEMY,
    BUILDING_BANNER_OWN,
    BUILDING_BANNER_UNDER_CONSTR,
    BUILDING_INTEGRITY_FULL,
    BUILDING_INTEGRITY_LOST,
    SIEGE_TARGET_RING,
    BUILDING_MARKER_ENEMY,
    BUILDING_MARKER_OWN,
    BUILDING_MARKER_UNDER_CONSTR,
    ROYAL_AURA_GOLD,
    ROYAL_AURA_VIOLET,
    ROYAL_BAR,
    ROYAL_LABEL_BG,
    SUMMON_VESSEL_DOT,
    SUMMON_VESSEL_TINT,
    TERRITORY_BORDER_ENEMY,
    TERRITORY_BORDER_OWN,
    TERRITORY_TINT_ENEMY,
    TERRITORY_TINT_ENEMY_SOFT,
    TERRITORY_TINT_OWN,
    TERRITORY_TINT_OWN_SOFT,
    TRAP_LABEL_BG,
    TRAP_MARKER_ENEMY,
    TRAP_MARKER_ENEMY_ACTIVE,
    TRAP_MARKER_OWN,
    TRAP_MARKER_OWN_ACTIVE,
    WHITE_PIECE,
    WHITE_PIECE_SHADOW,
    SCAR_TINT,
    ZONE_TINT_ENEMY,
    ZONE_TINT_ENEMY_ACTIVE,
    ZONE_TINT_ENEMY_ACTIVE_SOFT,
    ZONE_TINT_ENEMY_SOFT,
    ZONE_TINT_OWN,
    ZONE_TINT_OWN_ACTIVE,
    ZONE_TINT_OWN_ACTIVE_SOFT,
    ZONE_TINT_OWN_SOFT,
    ZONE_TINT_SPELL_ENEMY,
    ZONE_TINT_SPELL_ENEMY_SOFT,
    ZONE_TINT_SPELL_OWN,
    ZONE_TINT_SPELL_OWN_SOFT,
)

if TYPE_CHECKING:
    from game.core.observation import Observation


# ── Constants ──────────────────────────────────────────────────────────────
SQUARE_SIZE: int = 80       # pixels per square
# Stage 6: a left sidebar (hand card reference panel) now occupies the space
# left of the board — see ui/overlays.py HandInfoPanel and
# ui/pygame_app.py's LEFT_SIDEBAR_W. AppController is the single source of
# truth for this value; it's mirrored here so board_view's own coordinate
# math (_sq_to_screen / _screen_to_pos) and everything built on it (click
# hit-testing, tooltips) stays correct without threading an offset through
# every call site.
BOARD_OFFSET_X: int = 220   # left edge of board on the surface
# Stage 11: the Ritual strip (ui/ritual_bar.py) is pinned above the board,
# so the squares start below it. Same mirroring rationale as OFFSET_X —
# ritual_bar.BAR_HEIGHT reads this constant rather than the other way
# round, keeping the geometry all in one place.
BOARD_OFFSET_Y: int = 132   # top edge of board on the surface
BOARD_PIXEL_SIZE: int = SQUARE_SIZE * 8  # 640 px

# Unicode piece glyphs: piece_type_value → (white_glyph, black_glyph)
_GLYPHS: dict[str, tuple[str, str]] = {
    "king":   ("♔", "♚"),
    "queen":  ("♕", "♛"),
    "rook":   ("♖", "♜"),
    "bishop": ("♗", "♝"),
    "knight": ("♘", "♞"),
    "pawn":   ("♙", "♟"),
}

# ── Main assets sprite sheet (pieces + tiles) ───────────────────────────────
# Sheet: assets-3.png  1024 × 633 px  (magenta background ≈ (253,35,140))
#
# Background removal: pixels where R-G > 80 are magenta and become transparent.
# This threshold is applied at raw resolution before scaling so anti-aliased
# edges get natural fade; a second pass after scaling kills any remaining fringe.
#
# TILES — row y=25..89, each 73 px wide:
#   Stone Plains  x=81   ← LIGHT square tile
#   Cracked Earth x=155  ← DARK square tile
#
# BASIC (NEUTRAL) PIECES — body y=498..577 (header "Basic (Neutral) Pieces"
# above at y≈455..472; labels "King/Queen/..." below at y≈578+):
#   index 0  x=17   King    w=58
#   index 1  x=77   Queen   w=65
#   index 2  x=144  Rook    w=57
#   index 3  x=205  Bishop  w=59
#   index 4  x=266  Knight  w=65
#   index 5  x=333  Pawn    w=40
#
_ASSETS_TILE_Y: int = 25
_ASSETS_TILE_H: int = 65
_ASSETS_STONE_PLAINS_X: int  = 81   # light square
_ASSETS_STONE_PLAINS_W: int  = 73
_ASSETS_CRACKED_EARTH_X: int = 155  # dark square
_ASSETS_CRACKED_EARTH_W: int = 73

# ── Terrain tile atlas ──────────────────────────────────────────────────────
# Beyond the two base squares above, the sheet carries a full terrain set that
# the board swaps in to show *what is happening on a square* — instead of
# relying on a coloured wash alone.  Crops below are tight content bboxes
# measured by scanning the sheet for non-magenta runs (each art cell already
# includes its own dark rounded frame, so no padding is wanted).
#
#   Tiles panel, row 3  (y=184..246):
#       Ancient Ruins x=11 | Mountain x=83 | Battlefield x=157
#       Arcane Circle x=231 | Void Rift x=305
#   Special Terrain row (y=399..451):
#       Difficult x=11 | Impassable x=71 | Trap x=132
#       Scorched x=192 | Frozen x=256 | Corrupted x=316
#
# Keys are the names used throughout the renderer; values are (x, y, w, h),
# inset 2 px from those content bboxes: the outermost pixel ring of each cell
# is anti-aliased against the magenta backdrop (R-G still ~50-95 there, below
# _pil_kill_pink's threshold once blended), and scaling it up to SQUARE_SIZE
# turns that ring into a visible pink hairline around the tile.
_TILE_ATLAS: dict[str, tuple[int, int, int, int]] = {
    # Base squares — unchanged crops, still the board's default checker.
    "stone_plains":  (_ASSETS_STONE_PLAINS_X,  _ASSETS_TILE_Y,
                      _ASSETS_STONE_PLAINS_W,  _ASSETS_TILE_H),
    "cracked_earth": (_ASSETS_CRACKED_EARTH_X, _ASSETS_TILE_Y,
                      _ASSETS_CRACKED_EARTH_W, _ASSETS_TILE_H),
    # Targeting previews — shown only while a card is held, never persisted.
    "arcane_circle": (233, 186, 65, 59),   # where a Spell may be cast
    "void_rift":     (307, 186, 64, 59),   # where a Trap may be planted
    # Special Terrain — an effect is live on this square right now.
    "difficult":     ( 13, 401, 52, 49),
    "impassable":    ( 73, 401, 53, 49),
    "trap":          (134, 401, 52, 49),
    "scorched":      (194, 401, 55, 49),
    "frozen":        (258, 401, 52, 49),
    "corrupted":     (318, 401, 53, 49),
}

# Square effect type (Observation.board.square_effects) → terrain tile.
# The tile carries the *nature* of the hazard; the zone tint over it still
# carries ownership.  "temp_territory" is deliberately absent — Territory
# already has its own dedicated tint + border treatment.
SQUARE_EFFECT_TERRAIN: dict[str, str] = {
    "frozen":    "frozen",      # iron_vanguard / cursed_ground's cousin
    "scorched":  "scorched",    # ember_drake
    "blocked":   "impassable",  # veil_of_stillness
    "walled":    "impassable",
    "cursed":    "corrupted",   # cursed_ground
    "no_summon": "corrupted",
    "cost_zone": "difficult",   # movement is dearer here
}

# The terrain a square is left with once whatever was happening on it has
# finished resolving (README's "the land remembers"): scorched, frozen and
# cursed ground all settle back into broken ground.
SCAR_TERRAIN: str = "cracked_earth"

# Basic (Neutral) piece crops — tight content bboxes measured from the sheet.
# y=490, h=95 covers the maximum artwork height across all six pieces.
# x/w values come from pixel-scanning the content bounding box of each piece.
_ASSETS_PIECE_Y: int = 490
_ASSETS_PIECE_H: int = 95
_ASSETS_PIECE_ORDER: tuple[str, ...] = (
    "king", "queen", "rook", "bishop", "knight", "pawn"
)
# (x, width) tight content bboxes — artwork starts flush at these sheet coords
_ASSETS_PIECE_XW: tuple[tuple[int, int], ...] = (
    (16,  39),   # king
    (76,  66),   # queen
    (138, 65),   # rook
    (195, 69),   # bishop
    (258, 73),   # knight
    (330, 32),   # pawn
)

# Archetype piece crops — (x, y, w, h) per archetype per piece-type.
# Row order matches _ASSETS_PIECE_ORDER (king,queen,rook,bishop,knight,pawn).
# Measured from the "Chess Pieces by Archetype (Faction)" grid in assets-3.png.
# The archetype label icon (shield, axe, skull, etc.) sits at x≈380..455 and is
# excluded by scanning only x≥455.  All six blobs in the 455..920 range are the
# actual chess pieces: King, Queen, Rook, Bishop, Knight, Pawn.
_ARCHETYPE_PIECE_BBOXES: dict[str, tuple[tuple[int, int, int, int], ...]] = {
    "kingdom":     ((468,27,60,53),(555,27,43,53),(629,27,45,53),(702,27,41,53),(772,27,43,53),(843,27,34,53)),
    "warrior":     ((471,102,54,50),(549,102,48,50),(623,102,50,50),(698,102,42,50),(767,102,60,50),(841,102,36,50)),
    "dragon":      ((464,171,64,52),(547,171,61,52),(624,171,51,52),(694,171,55,52),(766,171,57,52),(841,171,41,52)),
    "spellcaster": ((464,243,59,53),(543,243,56,53),(624,243,51,53),(697,243,48,53),(768,243,51,53),(840,243,38,53)),
    "assassin":    ((464,315,59,50),(547,315,53,50),(624,315,51,50),(696,315,50,50),(762,315,67,50),(840,315,35,50)),
    "necromancer": ((467,384,55,50),(540,384,61,50),(624,384,51,50),(697,384,47,50),(764,384,64,50),(841,384,40,50)),
    "ritualist":   ((464,454,62,52),(545,454,52,52),(615,454,65,52),(697,454,53,52),(768,454,52,52),(840,454,43,52)),
    "beast":       ((462,527,63,55),(544,527,56,55),(622,527,55,55),(696,527,52,55),(766,527,64,55),(842,527,47,55)),
}

# Brightness multiplier applied to black piece RGB pixels (0.0 = fully black,
# 1.0 = no change).  0.15 keeps a faint hint of the original artwork while
# making the piece read as clearly "black" on both tile colours.
_BLACK_PIECE_BRIGHTNESS: float = 0.15

# Assassin pieces for the black player are inverted (whitened) instead of
# darkened, so the light-coloured assassin reads distinctly on dark squares.
# Value: each channel = 255 - original (PIL ImageOps.invert on RGB channels).
_ASSASSIN_INVERT_FOR_BLACK: bool = True

# R-G threshold for detecting the magenta background.
# Pure bg has R-G ≈ 218; piece artwork has R-G ≤ 101.
# 80 catches fringe/anti-aliased edge pixels without touching artwork.
_ASSETS_BG_RG_THRESHOLD: int = 80


# ── Building sprite sheet ───────────────────────────────────────────────────
# Sprite-sheet layout: 3 columns × 3 rows.
# Sheet size: 1536 × 1024 px.
# Columns (512 px wide each): floor(0) | back(1) | front(2)
# Rows:  fortress(0, y=0, h=341) | shrine(1, y=341, h=341) | watchtower(2, y=682, h=342)
_SHEET_COL_W: int = 512

# Row boundaries: height is 1024 px, not evenly divisible by 3 (1024 % 3 == 1),
# so the last row is 342 px tall instead of 341.
_SHEET_ROWS: tuple[tuple[int, int], ...] = (
    (0,   341),   # fortress
    (341, 341),   # shrine
    (682, 342),   # watchtower  ← gets the extra pixel
)

_BUILDING_ROW: dict[str, int] = {
    "fortress":   0,
    "shrine":     1,
    "watchtower": 2,
}

# Column indices within the sprite sheet
_COL_FLOOR = 0
_COL_BACK  = 1
_COL_FRONT = 2


def _building_type(card_id: str) -> str | None:
    """Map a building_card_id to its sprite row key (fortress/shrine/watchtower)."""
    cid = card_id.lower()
    for key in _BUILDING_ROW:
        if key in cid:
            return key
    return None


class BuildingSpriteCache:
    """
    Loads ``basic_buildings.png`` once and serves pre-scaled floor/back/front
    surfaces per building type, keyed by (building_type, layer).

    layer is one of "floor", "back", "front".

    All surfaces are SRCALPHA so transparent pixels are preserved.
    """

    _LAYER_COL = {"floor": _COL_FLOOR, "back": _COL_BACK, "front": _COL_FRONT}

    def __init__(self, sheet_path: str | Path, target_size: int = SQUARE_SIZE) -> None:
        self._cache: dict[tuple[str, str], pygame.Surface] = {}
        self._target = target_size
        try:
            raw = pygame.image.load(str(sheet_path)).convert_alpha()
            self._sheet: pygame.Surface | None = raw
        except Exception:
            self._sheet = None

    def get(self, building_type: str, layer: str) -> pygame.Surface | None:
        """Return a ``target_size × target_size`` surface for this building layer."""
        if self._sheet is None:
            return None
        key = (building_type, layer)
        if key in self._cache:
            return self._cache[key]
        row_idx = _BUILDING_ROW.get(building_type)
        col = self._LAYER_COL.get(layer)
        if row_idx is None or col is None:
            return None
        row_y, row_h = _SHEET_ROWS[row_idx]
        src_rect = pygame.Rect(
            col * _SHEET_COL_W,
            row_y,
            _SHEET_COL_W,
            row_h,
        )
        cell = self._sheet.subsurface(src_rect).copy()
        scaled = pygame.transform.smoothscale(cell, (self._target, self._target))
        self._cache[key] = scaled
        return scaled


def _pil_kill_pink(a: "np.ndarray") -> None:
    """Zero alpha on pixels that are still magenta-ish after scaling (R-G > 60)."""
    import numpy as np
    pink = (a[:, :, 0].astype(int) - a[:, :, 1].astype(int)) > 60
    a[pink, 3] = 0


class TileSpriteCache:
    """
    Loads ``assets-3.png`` once via PIL and serves pre-scaled terrain tile
    surfaces at SQUARE_SIZE, keyed by the names in ``_TILE_ATLAS``.

    ``get(is_light)`` keeps serving the two base squares (Stone Plains /
    Cracked Earth); ``get_named(name)`` serves any atlas entry, so the board
    can swap a square's ground for Arcane Circle, Void Rift, Trap, Frozen,
    Scorched and friends without a second sheet or a second cache.

    Tiles are built lazily on first request and memoised, so a board that
    never triggers a Spell never pays to decode the Arcane Circle art.

    PIL is used so that the magenta background fringe introduced by LANCZOS
    scaling can be detected and zeroed via R-G channel difference before the
    surface is handed to pygame.
    """

    def __init__(self, sheet_path: str | Path, target_size: int = SQUARE_SIZE) -> None:
        self._target = target_size
        self._cache: dict[str, pygame.Surface | None] = {}
        self._img: "Any" = None   # PIL RGBA Image
        try:
            from PIL import Image
            self._img = Image.open(str(sheet_path)).convert("RGBA")
        except Exception:
            self._img = None  # graceful fallback: every get() returns None

    def _build(self, x: int, y: int, w: int, h: int) -> "pygame.Surface":
        from PIL import Image
        import numpy as np

        t = self._target
        region = self._img.crop((x, y, x + w, y + h))
        a = np.array(region.convert("RGBA"), dtype=np.uint8)
        _pil_kill_pink(a)
        scaled = Image.fromarray(a, "RGBA").resize((t, t), Image.LANCZOS)
        sa = np.array(scaled, dtype=np.uint8)
        _pil_kill_pink(sa)
        # Composite onto an opaque dark background so transparent edge
        # pixels show the fallback colour rather than magenta.
        bg = Image.new("RGBA", (t, t), (60, 50, 40, 255))
        bg.paste(Image.fromarray(sa, "RGBA"), (0, 0), Image.fromarray(sa, "RGBA"))
        return pygame.image.fromstring(bg.tobytes(), (t, t), bg.mode)

    def get_named(self, name: str) -> "pygame.Surface | None":
        """Return the atlas tile called ``name``, or None if unavailable."""
        if name in self._cache:
            return self._cache[name]
        rect = _TILE_ATLAS.get(name)
        surf: pygame.Surface | None = None
        if rect is not None and self._img is not None:
            try:
                surf = self._build(*rect)
            except Exception:
                surf = None
        self._cache[name] = surf
        return surf

    def get(self, is_light: bool) -> "pygame.Surface | None":
        return self.get_named("stone_plains" if is_light else "cracked_earth")


class PieceSpriteCache:
    """
    Loads ``assets-3.png`` once via PIL and serves pre-scaled piece surfaces
    for both the Basic (Neutral) chess set and the per-archetype faction sets.

    Background removal uses an R-G channel difference mask (threshold 80)
    applied at raw resolution before scaling; a second pass after scaling
    kills any remaining pink fringe.  Returned surfaces are SRCALPHA with a
    transparent background — blit directly over the tile sprite.

    Owner colouring rules:
      • white  → artwork as-is
      • black, non-assassin → RGB × _BLACK_PIECE_BRIGHTNESS (≈15 %)
      • black, assassin archetype → RGB inverted (255 − channel) so the
        light-tone assassin silhouette reads clearly on dark tiles
    """

    def __init__(self, sheet_path: str | Path, target_size: int = SQUARE_SIZE) -> None:
        self._cache: dict[tuple[str, str, str], pygame.Surface] = {}
        self._target = target_size
        self._img: "Any" = None   # PIL RGBA Image
        try:
            from PIL import Image
            self._img = Image.open(str(sheet_path)).convert("RGBA")
        except Exception:
            pass

    # ── internal ──────────────────────────────────────────────────────────

    def _build(self, crop_x: int, crop_y: int, crop_w: int, crop_h: int,
               owner: str, archetype: str) -> "pygame.Surface":
        """
        Crop, clean, colour and scale one piece cell; return an SRCALPHA
        pygame surface of size (target, target).
        """
        import numpy as np
        from PIL import Image as _PILImage

        t = self._target
        region = self._img.crop((crop_x, crop_y, crop_x + crop_w, crop_y + crop_h))
        a = np.array(region.convert("RGBA"), dtype=np.uint8)  # (H, W, 4)

        # Mask magenta bg at raw resolution
        bg_mask = (
            a[:, :, 0].astype(int) - a[:, :, 1].astype(int)
        ) > _ASSETS_BG_RG_THRESHOLD
        a[:, :, 3] = np.where(bg_mask, 0, 255).astype(np.uint8)

        if owner == "black":
            art = ~bg_mask
            if archetype == "assassin" and _ASSASSIN_INVERT_FOR_BLACK:
                # Invert (whiten) the assassin for the black player
                a[art, :3] = (255 - a[art, :3].astype(int)).clip(0, 255).astype(np.uint8)
            else:
                a[art, :3] = (
                    a[art, :3].astype(float) * _BLACK_PIECE_BRIGHTNESS
                ).clip(0, 255).astype(np.uint8)

        # Scale preserving aspect ratio
        piece_pil = _PILImage.fromarray(a, "RGBA")
        cw, ch = piece_pil.size
        new_w = max(1, int(cw * t / ch))
        scaled = piece_pil.resize((new_w, t), _PILImage.LANCZOS)

        # Kill any remaining pink fringe after scaling
        sa = np.array(scaled, dtype=np.uint8)
        _pil_kill_pink(sa)

        # Centre on a square SRCALPHA canvas
        out_pil = _PILImage.new("RGBA", (t, t), (0, 0, 0, 0))
        ox = (t - new_w) // 2
        out_pil.paste(_PILImage.fromarray(sa, "RGBA"), (ox, 0),
                      _PILImage.fromarray(sa, "RGBA"))
        raw = out_pil.tobytes()
        return pygame.image.fromstring(raw, (t, t), "RGBA").convert_alpha()

    # ── public API ────────────────────────────────────────────────────────

    def get(
        self,
        piece_type: str,
        owner: str,
        bg_color: tuple[int, int, int] = (0, 0, 0),  # unused, kept for compat
    ) -> pygame.Surface | None:
        """Basic (Neutral) piece sprite — used for plain (unsummoned) pieces."""
        return self._get_with_archetype(piece_type, owner, "neutral")

    def get_archetype(
        self,
        piece_type: str,
        owner: str,
        archetype: str,
    ) -> pygame.Surface | None:
        """
        Archetype (faction) piece sprite for a summoned monster.
        Falls back to the neutral piece if the archetype is unknown.
        """
        if archetype not in _ARCHETYPE_PIECE_BBOXES:
            return self._get_with_archetype(piece_type, owner, "neutral")
        return self._get_with_archetype(piece_type, owner, archetype)

    def _get_with_archetype(
        self, piece_type: str, owner: str, archetype: str
    ) -> pygame.Surface | None:
        if self._img is None:
            return None
        key = (piece_type, owner, archetype)
        if key in self._cache:
            return self._cache[key]

        idx = (list(_ASSETS_PIECE_ORDER).index(piece_type)
               if piece_type in _ASSETS_PIECE_ORDER else -1)
        if idx < 0:
            return None

        if archetype == "neutral":
            px, pw = _ASSETS_PIECE_XW[idx]
            surf = self._build(px, _ASSETS_PIECE_Y, pw, _ASSETS_PIECE_H, owner, archetype)
        else:
            bboxes = _ARCHETYPE_PIECE_BBOXES.get(archetype)
            if bboxes is None or idx >= len(bboxes):
                return self._get_with_archetype(piece_type, owner, "neutral")
            bx, by, bw, bh = bboxes[idx]
            surf = self._build(bx, by, bw, bh, owner, archetype)

        self._cache[key] = surf
        return surf


def _sq_to_screen(pos: Position, flip: bool = False) -> tuple[int, int]:
    """
    Convert a board Position to the top-left pixel of its square.

    ``flip=False``  → rank 0 (rank 1) at bottom  (white perspective).
    ``flip=True``   → rank 0 at top               (black perspective, future).
    """
    x = BOARD_OFFSET_X + pos.file * SQUARE_SIZE
    if flip:
        y = BOARD_OFFSET_Y + pos.rank * SQUARE_SIZE
    else:
        y = BOARD_OFFSET_Y + (7 - pos.rank) * SQUARE_SIZE
    return x, y


def _screen_to_pos(mx: int, my: int, flip: bool = False) -> Position | None:
    """
    Convert a screen pixel (mx, my) to a board Position.
    Returns None if the click is outside the board area.
    """
    bx = mx - BOARD_OFFSET_X
    by = my - BOARD_OFFSET_Y
    if not (0 <= bx < BOARD_PIXEL_SIZE and 0 <= by < BOARD_PIXEL_SIZE):
        return None
    file = bx // SQUARE_SIZE
    if flip:
        rank = by // SQUARE_SIZE
    else:
        rank = 7 - (by // SQUARE_SIZE)
    try:
        return Position(file, rank)
    except ValueError:
        return None


class BoardView:
    """
    Stateless board renderer.

    Usage:
        view = BoardView(surface, font_large, font_small, building_sprites=cache)
        view.draw(observation, selected_pos, legal_dests, in_check_player)
    """

    def __init__(
        self,
        surface: pygame.Surface,
        font_large: Any,   # _ft.Font — pygame.font not used (Py 3.14 bug)
        font_small: Any,
        flip: bool = False,
        building_sprites: "BuildingSpriteCache | None" = None,
        tile_sprites: "TileSpriteCache | None" = None,
        piece_sprites: "PieceSpriteCache | None" = None,
    ) -> None:
        self._surface = surface
        self._font_large = font_large
        self._font_small = font_small
        self._flip = flip
        self._building_sprites: BuildingSpriteCache | None = building_sprites
        self._tile_sprites: TileSpriteCache | None = tile_sprites
        self._piece_sprites: PieceSpriteCache | None = piece_sprites

        # Pre-allocate overlay surface for alpha blending
        self._overlay = pygame.Surface(
            (SQUARE_SIZE, SQUARE_SIZE), pygame.SRCALPHA
        )
        # Stage 14: scratch canvas for transient VFX (summon/spell/trap/
        # ritual/coronation). 3×3 squares so a ring or ray can bleed into
        # neighboring squares — Ritual/Coronation deliberately do — without
        # needing per-effect surface allocation every frame. Re-filled
        # transparent and reused for every animation drawn each frame.
        self._anim_overlay = pygame.Surface(
            (SQUARE_SIZE * 3, SQUARE_SIZE * 3), pygame.SRCALPHA
        )

    # ── Public render entry point ─────────────────────────────────────────

    def draw(
        self,
        observation: "Observation",
        selected_pos: Position | None,
        legal_dests: list[Position],
        castle_dests: list[Position],
        checked_player: str | None,
        summon_vessel_dests: list[Position] | None = None,
        inspect_pos: Position | None = None,
        aura_colors: "dict[Position, tuple[int, int, int]] | None" = None,
        show_territory: bool = True,
        crowned_kings: "dict[Position, str] | None" = None,
        archetype_map: "dict[Position, str] | None" = None,
        siege_dests: list[Position] | None = None,
        spell_preview: "Iterable[Position] | None" = None,
        trap_preview: "Iterable[Position] | None" = None,
        scars: "dict[Position, float] | None" = None,
        animations: "list[dict] | None" = None,
    ) -> None:
        """
        Render the full board onto ``self._surface``.

        Parameters
        ----------
        observation        : current player observation (provides unit list)
        selected_pos       : currently selected source square (or None)
        legal_dests        : legal destination squares for the selected piece
        castle_dests       : legal castling destination squares (g1/c1 etc.)
        checked_player     : "white" or "black" if in check, else None
        summon_vessel_dests: Stage 5 — valid vessel squares for the held monster
                              card; Stage 6 also reuses this same gold-ring
                              channel for Trap-placement / Spell-target squares
                              (the AppController decides which mode is active).
        inspect_pos        : Stage 5 — unit being inspected (purple tint, info panel)
        aura_colors        : Stage 6 — Position → RGB for every summoned piece's
                              archetype aura. Computed by AppController (it owns
                              the card registry lookup); this module only draws
                              whatever color it's handed. Positions with no
                              Monster, or missing from the map, get no aura.
        show_territory      : Stage 9 — whether to draw the Territory base
                              tint (AppController's toggle button flips this).
        crowned_kings       : Stage 10 — Position → active king_card_id for
                              every King whose owner currently has an
                              ACTIVE King Card (README §17 Coronation).
                              Gets a richer, animated "royal" treatment —
                              see _draw_pieces — distinct from (and
                              grander than) a Monster's archetype aura,
                              since there's only ever one Crowned King per
                              side. Mirrors aura_colors: this module never
                              looks the card up itself.
        archetype_map       : Position → archetype string for every summoned
                              piece. Used to choose the faction sprite instead
                              of the neutral chess piece.
        siege_dests         : Stage 13 — squares holding a COMPLETE enemy
                              Building the SELECTED unit may besiege
                              (AttackBuilding). Drawn as a red ring on top of
                              the Building rather than a legal-move dot,
                              because clicking one does not move the piece.
        spell_preview       : squares a held Spell could be cast on. Their
                              ground is TEMPORARILY swapped for the Arcane
                              Circle tile (the gold targeting tint stays on
                              top) — nothing is persisted, the tile reverts
                              the moment targeting mode ends.
        trap_preview        : same idea for a held Trap, using the Void Rift
                              tile so "where can I plant this" never looks
                              like "where can I cast this".
        scars               : Position → 0..1 intensity for ground where a
                              Trap / Spell / Monster has FINISHED resolving.
                              Those squares get the Cracked Earth tile plus a
                              soot wash scaled by the intensity, so the board
                              carries a fading record of what happened on it.
                              AppController owns the bookkeeping (it is the
                              only side that sees events); this module just
                              draws what it is handed.
        animations          : Stage 14 — transient VFX for the moment a
                              Monster is summoned, a Spell is cast, a Trap
                              is set or sprung, a Ritual completes, or a
                              King is Coronated. Each entry is a dict with
                              "pos" (Position), "kind" (one of "summon" /
                              "spell" / "trap_set" / "trap_trigger" /
                              "ritual" / "coronation"), "color" (RGB),
                              "start" and "duration" (both pygame.time.
                              get_ticks() milliseconds). AppController owns
                              spawning them from the event log and pruning
                              expired ones — same "pure rendering" contract
                              as everything else this method is handed.
        """
        self._draw_squares(observation, selected_pos, legal_dests, castle_dests,
                           checked_player, summon_vessel_dests or [], inspect_pos,
                           show_territory, spell_preview or (), trap_preview or (),
                           scars or {})
        self._draw_coordinates()
        self._draw_traps(observation)
        # ── Stage 8+: building floor + back drawn BEFORE the chess piece ──────
        self._draw_building_floor_and_back(observation)
        # ── Pieces sandwiched between building back and front ─────────────────
        self._draw_pieces(observation, aura_colors or {}, crowned_kings or {},
                          archetype_map or {})
        # ── Building front drawn AFTER the chess piece ────────────────────────
        self._draw_building_front_and_banner(observation)
        # ── Stage 13: siege target rings, above everything on the square ─────
        self._draw_siege_targets(siege_dests or [])
        # ── Stage 14: transient VFX, topmost so they always read clearly ─────
        self._draw_animations(animations or [])
        # NOTE: the decorative rim (draw_frame) is deliberately NOT called
        # here — the Ritual bar and side panels are drawn AFTER this method
        # returns and would paint straight over it. AppController calls
        # draw_frame() itself as the very last step of a frame instead, so
        # it always ends up on top. See its docstring.

    def draw_frame(self) -> None:
        """
        Decorative rim traced around the board's outer edge — separates the
        playing field from the surrounding HUD chrome the way a physical
        board has a wooden rim around its squares.

        Deliberately a separate public method rather than part of
        ``draw()``: the Ritual bar above the board and the side panels
        beside it are drawn AFTER ``draw()`` returns (see AppController.
        _render), and would paint straight over a rim drawn any earlier.
        Call this once, last, after every other panel for the frame has
        been drawn.
        """
        outer = pygame.Rect(
            BOARD_OFFSET_X - 3, BOARD_OFFSET_Y - 3,
            BOARD_PIXEL_SIZE + 6, BOARD_PIXEL_SIZE + 6,
        )
        pygame.draw.rect(self._surface, BOARD_FRAME_OUTER, outer, width=3)
        inner = pygame.Rect(
            BOARD_OFFSET_X - 1, BOARD_OFFSET_Y - 1,
            BOARD_PIXEL_SIZE + 2, BOARD_PIXEL_SIZE + 2,
        )
        pygame.draw.rect(self._surface, BOARD_FRAME_INNER, inner, width=1)

    def _draw_siege_targets(self, siege_dests: list[Position]) -> None:
        """
        Stage 13 — ring every Building the selected unit may attack.

        Deliberately NOT the usual legal-move dot: a click here spends the
        chess move without the piece going anywhere, so it needs to read as
        a different kind of action at a glance.
        """
        for pos in siege_dests:
            sx, sy = _sq_to_screen(pos, self._flip)
            pygame.draw.rect(
                self._surface,
                SIEGE_TARGET_RING,
                pygame.Rect(sx + 1, sy + 1, SQUARE_SIZE - 2, SQUARE_SIZE - 2),
                width=3,
                border_radius=3,
            )

    # ── Private draw helpers ──────────────────────────────────────────────

    def _draw_squares(
        self,
        obs: "Observation",
        selected_pos: Position | None,
        legal_dests: list[Position],
        castle_dests: list[Position],
        checked_player: str | None,
        summon_vessel_dests: list[Position] | None = None,
        inspect_pos: Position | None = None,
        show_territory: bool = True,
        spell_preview: "Iterable[Position]" = (),
        trap_preview: "Iterable[Position]" = (),
        scars: "dict[Position, float] | None" = None,
    ) -> None:
        """Draw base square colors, then overlays for selection/legal/check/summon/inspect."""
        # Build a quick lookup: position → unit for check-king detection
        king_pos: Position | None = None
        if checked_player is not None:
            for unit_info in obs.board.units:
                if (
                    unit_info.piece_type == "king"
                    and unit_info.owner == checked_player
                ):
                    king_pos = unit_info.position
                    break

        vessel_set: set[Position] = set(summon_vessel_dests or [])

        # Stage 6: zones split by source so each gets a distinct tint.
        #
        # • Dormant Trap zones  → muted blue/red   (ZONE_TINT_OWN/ENEMY)
        # • Activated Trap zones→ vivid gold/orange (ZONE_TINT_*_ACTIVE)
        # • Active Spell zones  → purple/magenta    (ZONE_TINT_SPELL_*)
        #
        # Ownership framing is always relative to obs.player_id (the viewer).
        # Priority when a square falls in multiple sets (highest → lowest):
        #   activated trap > spell zone > dormant trap
        own_trap_active_squares:   set[Position] = set()
        enemy_trap_active_squares: set[Position] = set()
        own_trap_squares:          set[Position] = set()
        enemy_trap_squares:        set[Position] = set()
        own_spell_squares:         set[Position] = set()
        enemy_spell_squares:       set[Position] = set()

        for trap in obs.board.trap_locations:
            area = expand_area(trap.position, trap.radius, trap.shape)
            is_own = trap.owner == obs.player_id
            if getattr(trap, "activated", False):
                (own_trap_active_squares if is_own else enemy_trap_active_squares).update(area)
            else:
                (own_trap_squares if is_own else enemy_trap_squares).update(area)

        for eff in obs.board.square_effects:
            is_own = eff.owner == obs.player_id
            (own_spell_squares if is_own else enemy_spell_squares).add(eff.position)

        # ── Terrain substitution ─────────────────────────────────────────
        # A square's ground tile says WHAT is happening on it; the tint above
        # says WHOSE it is.  Highest priority wins, and only one tile can win:
        #
        #   1. Spell targeting preview  → Arcane Circle  (transient)
        #   2. Trap  targeting preview  → Void Rift      (transient)
        #   3. A Trap's own square      → Trap
        #   4. A live square effect     → its Special Terrain
        #   5. A resolved-effect scar   → Cracked Earth
        #
        # Previews outrank everything because they only exist while the player
        # is actively holding a card and hunting for a legal square — that is
        # the one moment where the answer to "where can this go?" matters more
        # than the board's standing state.
        terrain_by_pos: dict[Position, str] = {}
        for pos in spell_preview:
            terrain_by_pos[pos] = "arcane_circle"
        for pos in trap_preview:
            terrain_by_pos.setdefault(pos, "void_rift")
        for trap in obs.board.trap_locations:
            terrain_by_pos.setdefault(trap.position, "trap")
        for eff in obs.board.square_effects:
            terrain = SQUARE_EFFECT_TERRAIN.get(eff.effect_type)
            if terrain is not None:
                terrain_by_pos.setdefault(eff.position, terrain)
        # Scars are the lowest priority — anything currently live on the
        # square supersedes the memory of what used to be there.
        scars = scars or {}
        scar_shown: dict[Position, float] = {}
        for pos, intensity in scars.items():
            if pos not in terrain_by_pos:
                terrain_by_pos[pos] = SCAR_TERRAIN
                scar_shown[pos] = intensity

        # Stage 9: Territory — the lowest-priority zone tint (README §8).
        # Computed either way (cheap — just two tuples off the Observation)
        # so the toggle button can flip visibility with no extra state here.
        own_territory = set(getattr(obs, "own_territory", ())) if show_territory else set()
        enemy_territory = set(getattr(obs, "opponent_territory", ())) if show_territory else set()

        for file in range(8):
            for rank in range(8):
                pos = Position(file, rank)
                is_light = (file + rank) % 2 == 0
                color = LIGHT_SQUARE if is_light else DARK_SQUARE
                sx, sy = _sq_to_screen(pos, self._flip)
                rect = pygame.Rect(sx, sy, SQUARE_SIZE, SQUARE_SIZE)
                # Ground tile: the substituted terrain when this square has
                # one, else the stone-plains / cracked-earth checker.  A
                # missing sprite (no PIL, unreadable sheet) falls back to the
                # flat colors, and an unknown terrain name falls back to the
                # base tile rather than leaving a hole.
                terrain = terrain_by_pos.get(pos)
                tile_surf = None
                if self._tile_sprites is not None:
                    if terrain is not None:
                        tile_surf = self._tile_sprites.get_named(terrain)
                    if tile_surf is None:
                        tile_surf = self._tile_sprites.get(is_light)
                if tile_surf is not None:
                    self._surface.blit(tile_surf, (sx, sy))
                else:
                    pygame.draw.rect(self._surface, color, rect)

                # Soot wash over a scar, so it reads on light AND dark squares
                # (Cracked Earth is already the dark square's own ground).
                # Fades as the scar ages out — see AppController._scar_map.
                scar_level = scar_shown.get(pos)
                if scar_level:
                    r, g, b, a = SCAR_TINT
                    self._overlay.fill((r, g, b, int(a * min(1.0, scar_level))))
                    self._surface.blit(self._overlay, (sx, sy))

                # Was this square's ground replaced by Special Terrain?  If so
                # the zone tint below drops to its SOFT variant — the artwork
                # is already carrying the message and a full-strength wash
                # would only bury it.
                on_terrain = terrain is not None and terrain != SCAR_TERRAIN
                # Any tile swap at all — scars included — lightens the
                # Territory wash below, since that one is broad enough to
                # bury whatever the swapped tile was trying to say.
                painted = terrain is not None

                # Stage 9: Territory tint — drawn first so every other zone
                # tint below still stacks visibly on top of it. A contested
                # square (both players' Territory) shows both tints blended.
                if pos in own_territory:
                    self._overlay.fill(TERRITORY_TINT_OWN_SOFT if painted
                                       else TERRITORY_TINT_OWN)
                    self._surface.blit(self._overlay, (sx, sy))
                if pos in enemy_territory:
                    self._overlay.fill(TERRITORY_TINT_ENEMY_SOFT if painted
                                       else TERRITORY_TINT_ENEMY)
                    self._surface.blit(self._overlay, (sx, sy))

                # Stage 6: zone tints — drawn before selection/check so those
                # highlights always read on top.  Priority: activated trap >
                # spell zone > dormant trap.  Own tint beats enemy tint when
                # both would apply to the same square.
                if pos in own_trap_active_squares:
                    self._overlay.fill(ZONE_TINT_OWN_ACTIVE_SOFT if on_terrain
                                       else ZONE_TINT_OWN_ACTIVE)
                    self._surface.blit(self._overlay, (sx, sy))
                elif pos in enemy_trap_active_squares:
                    self._overlay.fill(ZONE_TINT_ENEMY_ACTIVE_SOFT if on_terrain
                                       else ZONE_TINT_ENEMY_ACTIVE)
                    self._surface.blit(self._overlay, (sx, sy))
                elif pos in own_spell_squares:
                    self._overlay.fill(ZONE_TINT_SPELL_OWN_SOFT if on_terrain
                                       else ZONE_TINT_SPELL_OWN)
                    self._surface.blit(self._overlay, (sx, sy))
                elif pos in enemy_spell_squares:
                    self._overlay.fill(ZONE_TINT_SPELL_ENEMY_SOFT if on_terrain
                                       else ZONE_TINT_SPELL_ENEMY)
                    self._surface.blit(self._overlay, (sx, sy))
                elif pos in own_trap_squares:
                    self._overlay.fill(ZONE_TINT_OWN_SOFT if on_terrain
                                       else ZONE_TINT_OWN)
                    self._surface.blit(self._overlay, (sx, sy))
                elif pos in enemy_trap_squares:
                    self._overlay.fill(ZONE_TINT_ENEMY_SOFT if on_terrain
                                       else ZONE_TINT_ENEMY)
                    self._surface.blit(self._overlay, (sx, sy))

                # Check highlight (king square)
                if king_pos is not None and pos == king_pos:
                    self._overlay.fill(CHECK_TINT)
                    self._surface.blit(self._overlay, (sx, sy))

                # Selection highlight
                elif selected_pos is not None and pos == selected_pos:
                    self._overlay.fill(SELECTED_TINT)
                    self._surface.blit(self._overlay, (sx, sy))

                # Stage 5: inspect tint (purple) — unit whose card info is shown
                elif inspect_pos is not None and pos == inspect_pos:
                    self._overlay.fill(INSPECT_TINT)
                    self._surface.blit(self._overlay, (sx, sy))

                # Stage 5: summon vessel highlight (gold tint)
                elif pos in vessel_set:
                    self._overlay.fill(SUMMON_VESSEL_TINT)
                    self._surface.blit(self._overlay, (sx, sy))

        # Stage 9: Territory borders — an opaque outline traced along the
        # outer edge of each Territory's footprint, so its shape reads
        # clearly at a glance even where fills overlap or sit under a
        # denser Trap/Spell tint.
        if own_territory:
            self._draw_territory_border(own_territory, TERRITORY_BORDER_OWN)
        if enemy_territory:
            self._draw_territory_border(enemy_territory, TERRITORY_BORDER_ENEMY)

        # Legal-move dots (drawn after base squares so they appear on top)
        for dest in legal_dests:
            self._draw_legal_dot(dest, obs)
        for dest in castle_dests:
            self._draw_castle_dot(dest)
        # Summon vessel dots (gold diamonds / rings drawn on top)
        for dest in (summon_vessel_dests or []):
            self._draw_summon_vessel_marker(dest)

    def _draw_territory_border(self, squares: "set[Position]", color: tuple) -> None:
        """
        Stage 9 — trace an opaque outline along the outer edge of a
        Territory footprint: for every square in ``squares``, draw a line
        on whichever of its four sides borders a square NOT in ``squares``
        (including the edge of the board itself).
        """
        LINE_W = 4
        for pos in squares:
            sx, sy = _sq_to_screen(pos, self._flip)
            # (delta-file, delta-rank, edge-line-in-screen-space)
            for df, dr in ((0, 1), (0, -1), (-1, 0), (1, 0)):
                nf, nr = pos.file + df, pos.rank + dr
                neighbor_in_zone = (
                    0 <= nf <= 7 and 0 <= nr <= 7
                    and Position(nf, nr) in squares
                )
                if neighbor_in_zone:
                    continue
                # dr=+1 (north, higher rank) is the TOP edge on screen
                # unless flipped; dr=-1 is the bottom; df=-1 is left; df=+1
                # is right — matches _sq_to_screen's own y-axis inversion.
                if dr == 1:
                    y_edge = sy if not self._flip else sy + SQUARE_SIZE
                    pygame.draw.line(self._surface, color, (sx, y_edge), (sx + SQUARE_SIZE, y_edge), LINE_W)
                elif dr == -1:
                    y_edge = sy + SQUARE_SIZE if not self._flip else sy
                    pygame.draw.line(self._surface, color, (sx, y_edge), (sx + SQUARE_SIZE, y_edge), LINE_W)
                elif df == -1:
                    pygame.draw.line(self._surface, color, (sx, sy), (sx, sy + SQUARE_SIZE), LINE_W)
                elif df == 1:
                    pygame.draw.line(self._surface, color, (sx + SQUARE_SIZE, sy), (sx + SQUARE_SIZE, sy + SQUARE_SIZE), LINE_W)

    def _draw_legal_dot(self, pos: Position, obs: "Observation") -> None:
        """Draw a dot (empty square) or ring (capture square) for a legal move."""
        sx, sy = _sq_to_screen(pos, self._flip)
        cx = sx + SQUARE_SIZE // 2
        cy = sy + SQUARE_SIZE // 2
        occupied = any(u.position == pos for u in obs.board.units)
        if occupied:
            # Ring for capture
            pygame.draw.circle(
                self._surface, LEGAL_CAPTURE, (cx, cy),
                SQUARE_SIZE // 2 - 4, 5
            )
        else:
            # Filled dot
            pygame.draw.circle(
                self._surface, LEGAL_DOT, (cx, cy),
                SQUARE_SIZE // 7
            )

    def _draw_castle_dot(self, pos: Position) -> None:
        sx, sy = _sq_to_screen(pos, self._flip)
        cx = sx + SQUARE_SIZE // 2
        cy = sy + SQUARE_SIZE // 2
        pygame.draw.circle(self._surface, CASTLE_DOT, (cx, cy), SQUARE_SIZE // 7)

    def _draw_summon_vessel_marker(self, pos: Position) -> None:
        """Draw a gold ring around a valid vessel square (summon mode)."""
        sx, sy = _sq_to_screen(pos, self._flip)
        cx = sx + SQUARE_SIZE // 2
        cy = sy + SQUARE_SIZE // 2
        pygame.draw.circle(
            self._surface, SUMMON_VESSEL_DOT, (cx, cy),
            SQUARE_SIZE // 2 - 5, 4   # thick ring
        )

    def _draw_coordinates(self) -> None:
        """Draw file letters (a–h) and rank numbers (1–8) on the board edges."""
        for file in range(8):
            letter = chr(ord("a") + file)
            is_light = (file + 0) % 2 == 0  # rank 0 = rank 1, always dark corner
            color = COORD_LIGHT if is_light else COORD_DARK
            surf = self._font_small.render(letter, True, color)
            # Bottom of each file column
            if not self._flip:
                sx = BOARD_OFFSET_X + file * SQUARE_SIZE + SQUARE_SIZE - surf.get_width() - 3
                sy = BOARD_OFFSET_Y + SQUARE_SIZE * 8 - surf.get_height() - 2
            else:
                sx = BOARD_OFFSET_X + file * SQUARE_SIZE + 3
                sy = BOARD_OFFSET_Y + 2
            self._surface.blit(surf, (sx, sy))

        for rank in range(8):
            number = str(rank + 1)
            is_light = (0 + rank) % 2 == 0
            color = COORD_LIGHT if is_light else COORD_DARK
            surf = self._font_small.render(number, True, color)
            if not self._flip:
                sx = BOARD_OFFSET_X + 3
                sy = BOARD_OFFSET_Y + (7 - rank) * SQUARE_SIZE + 3
            else:
                sx = BOARD_OFFSET_X + 3
                sy = BOARD_OFFSET_Y + rank * SQUARE_SIZE + 3
            self._surface.blit(surf, (sx, sy))

    def _draw_traps(self, obs: "Observation") -> None:
        """
        Stage 6 — every placed Trap gets a marker ring on its square (its
        existence and rough danger zone are always visible — that's the
        colored area from _draw_squares).  Its IDENTITY is only revealed
        for the viewer's own Traps: the name label is only drawn when
        ``trap.owner == obs.player_id``.  An enemy Trap shows a plain ring
        in the "enemy" color — you know something is there, not what it is.
        """
        for trap in getattr(obs, "trap_locations", ()):
            pos = trap.position
            sx, sy = _sq_to_screen(pos, self._flip)
            cx = sx + SQUARE_SIZE // 2
            cy = sy + SQUARE_SIZE // 2
            is_own = trap.owner == obs.player_id
            is_active = getattr(trap, "activated", False)
            if is_own:
                ring_color = TRAP_MARKER_OWN_ACTIVE if is_active else TRAP_MARKER_OWN
            else:
                ring_color = TRAP_MARKER_ENEMY_ACTIVE if is_active else TRAP_MARKER_ENEMY
            pygame.draw.circle(
                self._surface, ring_color, (cx, cy),
                SQUARE_SIZE // 2 - 3, 3,
            )
            if not is_own:
                continue
            short_name = trap.card_id.replace("_", " ").title()
            lbl = self._font_small.render(short_name, True, ring_color)
            lbl_bg = pygame.Surface((lbl.get_width() + 4, lbl.get_height() + 2), pygame.SRCALPHA)
            lbl_bg.fill(TRAP_LABEL_BG)
            lx = sx + (SQUARE_SIZE - lbl.get_width()) // 2
            ly = sy + 2
            self._surface.blit(lbl_bg, (lx - 2, ly - 1))
            self._surface.blit(lbl, (lx, ly))

    def _draw_building_floor_and_back(self, obs: "Observation") -> None:
        """
        Stage 8+ — first half of the 3-layer building render.
        Draws FLOOR then BACK sprites for every non-destroyed Building,
        before the chess piece is drawn.

        Falls back to the old inset-rect style if sprite assets are unavailable.
        """
        from game.core.phases import ConstructionStatus

        for b in getattr(obs.board, "building_locations", ()):
            if getattr(b, "status", None) == ConstructionStatus.DESTROYED:
                continue
            pos = b.position
            sx, sy = _sq_to_screen(pos, self._flip)
            btype = _building_type(b.building_card_id)
            under_construction = b.status == ConstructionStatus.UNDER_CONSTRUCTION

            if self._building_sprites is not None and btype is not None:
                # ── Sprite path ──────────────────────────────────────────
                floor_surf = self._building_sprites.get(btype, "floor")
                back_surf  = self._building_sprites.get(btype, "back")

                if under_construction:
                    # Under construction: draw floor only, tinted amber
                    if floor_surf is not None:
                        tinted = floor_surf.copy()
                        tinted.fill((200, 170, 50, 120), special_flags=pygame.BLEND_RGBA_MULT)
                        self._surface.blit(tinted, (sx, sy))
                    # Construction progress bar
                    self._draw_construction_bar(sx, sy, b)
                else:
                    # Complete: draw floor + back
                    if floor_surf is not None:
                        self._surface.blit(floor_surf, (sx, sy))
                    if back_surf is not None:
                        self._surface.blit(back_surf, (sx, sy))
            else:
                # ── Fallback: inset rect only (no sprites loaded) ─────────
                if under_construction:
                    color = BUILDING_MARKER_UNDER_CONSTR
                else:
                    color = BUILDING_MARKER_OWN if b.owner == obs.player_id else BUILDING_MARKER_ENEMY
                inset = 3
                rect = pygame.Rect(sx + inset, sy + inset,
                                   SQUARE_SIZE - inset * 2, SQUARE_SIZE - inset * 2)
                pygame.draw.rect(self._surface, color, rect, 3, border_radius=6)
                if under_construction:
                    self._draw_construction_bar(sx, sy, b)

    def _draw_building_front_and_banner(self, obs: "Observation") -> None:
        """
        Stage 8+ — second half of the 3-layer building render.
        Draws the FRONT sprite (foreground overlay) and a thin faction
        ownership banner stripe, after the chess piece has been drawn.

        The banner communicates ownership without altering the neutral stone
        artwork:  green = own, muted orange = enemy, amber = under construction.
        """
        from game.core.phases import ConstructionStatus

        for b in getattr(obs.board, "building_locations", ()):
            if getattr(b, "status", None) == ConstructionStatus.DESTROYED:
                continue
            pos = b.position
            sx, sy = _sq_to_screen(pos, self._flip)
            btype = _building_type(b.building_card_id)
            under_construction = b.status == ConstructionStatus.UNDER_CONSTRUCTION

            if under_construction:
                banner_color = BUILDING_BANNER_UNDER_CONSTR
            else:
                banner_color = (
                    BUILDING_BANNER_OWN
                    if b.owner == obs.player_id
                    else BUILDING_BANNER_ENEMY
                )

            if self._building_sprites is not None and btype is not None and not under_construction:
                front_surf = self._building_sprites.get(btype, "front")
                if front_surf is not None:
                    self._surface.blit(front_surf, (sx, sy))

            # Thin ownership banner — a 3-px stripe at the very bottom of the
            # square, drawn on top of the front layer so it always reads clearly.
            banner_h = 3
            pygame.draw.rect(
                self._surface,
                banner_color,
                pygame.Rect(sx + 2, sy + SQUARE_SIZE - banner_h - 1,
                            SQUARE_SIZE - 4, banner_h),
                border_radius=1,
            )

            # Stage 13 — integrity pips for a COMPLETE Building that has
            # taken siege damage. Nothing is drawn at full integrity, so an
            # undamaged board stays as clean as it was before.
            if not under_construction:
                self._draw_integrity_pips(sx, sy, b)

    def _draw_integrity_pips(self, sx: int, sy: int, b: Any) -> None:
        """
        Stage 13 — a row of small pips along the top of a damaged
        Building's square: filled = integrity remaining, hollow = lost.

        Deliberately silent at full health. A Building that has never been
        attacked shouldn't add clutter to the board; the pips appearing at
        all IS the signal that a siege is underway.
        """
        integrity = getattr(b, "integrity", None)
        max_integrity = getattr(b, "max_integrity", None)
        if integrity is None or not max_integrity or integrity >= max_integrity:
            return

        pip_r = 3
        gap = 3
        total_w = max_integrity * (pip_r * 2) + (max_integrity - 1) * gap
        px = sx + (SQUARE_SIZE - total_w) // 2 + pip_r
        py = sy + pip_r + 2
        for i in range(max_integrity):
            cx = px + i * (pip_r * 2 + gap)
            if i < integrity:
                pygame.draw.circle(self._surface, BUILDING_INTEGRITY_FULL, (cx, py), pip_r)
            else:
                pygame.draw.circle(self._surface, BUILDING_INTEGRITY_LOST, (cx, py), pip_r, 1)

    def _draw_construction_bar(self, sx: int, sy: int, b: Any) -> None:
        """
        Draw an amber progress bar at the bottom of an under-construction
        building square, showing elapsed / total turns.

        ``b`` is a BuildingInstance (duck-typed: needs .remaining_turns and
        optionally .total_turns — falls back to rendering a fraction label).
        """
        remaining = getattr(b, "remaining_turns", 0)
        total = getattr(b, "total_turns", None)

        bar_w = SQUARE_SIZE - 8
        bar_h = 7
        bar_x = sx + 4
        bar_y = sy + SQUARE_SIZE - bar_h - 4

        # Background
        pygame.draw.rect(
            self._surface,
            (40, 30, 10),
            pygame.Rect(bar_x, bar_y, bar_w, bar_h),
            border_radius=2,
        )

        if total is not None and total > 0:
            done = total - remaining
            fill_w = max(2, int(bar_w * done / total))
        else:
            # No total_turns attribute: draw a minimal stub
            fill_w = 4

        pygame.draw.rect(
            self._surface,
            BUILDING_BANNER_UNDER_CONSTR,
            pygame.Rect(bar_x, bar_y, fill_w, bar_h),
            border_radius=2,
        )

        # Turn counter label: "⚒ 2/3" or "⚒ 2" if no total
        if total is not None:
            label_text = f"\u2692 {total - remaining}/{total}"
        else:
            label_text = f"\u2692 {remaining}t"

        lbl = self._font_small.render(label_text, True, BUILDING_BANNER_UNDER_CONSTR)
        lx = sx + (SQUARE_SIZE - lbl.get_width()) // 2
        ly = bar_y - lbl.get_height() - 1
        self._surface.blit(lbl, (lx, ly))

    def _draw_pieces(
        self,
        obs: "Observation",
        aura_colors: "dict[Position, tuple[int, int, int]]",
        crowned_kings: "dict[Position, str]",
        archetype_map: "dict[Position, str]",
    ) -> None:
        """Render all pieces centered on their squares.

        When a PieceSpriteCache is available:
          • Unsummoned pieces use the Basic (Neutral) sprite.
          • Summoned (monster) pieces use the faction sprite for their archetype.
          • Black pieces are darkened (15 % brightness), except Assassin which
            is inverted (whitened) so it reads clearly on both tile colours.
        Falls back to Unicode glyphs when the cache is absent.

        Stage 5: monster units get a gold underline bar and a tiny name label.
        Stage 6: monster units also get a colored aura keyed by archetype.
        Stage 10: a Crowned King gets its own richer, animated aura, bar,
        name label, and crown badge — see _draw_royal_aura below.
        """
        for unit_info in obs.board.units:
            pos = unit_info.position
            sx, sy = _sq_to_screen(pos, self._flip)
            glyph_pair = _GLYPHS.get(unit_info.piece_type, ("?", "?"))
            glyph = glyph_pair[0] if unit_info.owner == "white" else glyph_pair[1]

            has_monster = bool(getattr(unit_info, "monster_id", None))
            archetype = archetype_map.get(pos) if has_monster else None
            king_card_id = crowned_kings.get(pos) if unit_info.piece_type == "king" else None
            is_crowned_king = king_card_id is not None
            text_color = WHITE_PIECE if unit_info.owner == "white" else BLACK_PIECE
            shadow_color = WHITE_PIECE_SHADOW if unit_info.owner == "white" else BLACK_PIECE_SHADOW

            # Stage 6: archetype aura — soft glow + ring, behind the piece.
            if has_monster and pos in aura_colors:
                color = aura_colors[pos]
                aura = pygame.Surface((SQUARE_SIZE, SQUARE_SIZE), pygame.SRCALPHA)
                cx, cy = SQUARE_SIZE // 2, SQUARE_SIZE // 2
                radius = SQUARE_SIZE // 2 - 4
                pygame.draw.circle(aura, (*color, 70), (cx, cy), radius)
                pygame.draw.circle(aura, (*color, 190), (cx, cy), radius, 3)
                self._surface.blit(aura, (sx, sy))

            # Stage 10: Crowned King — richer, animated "royal" aura,
            # behind the piece (same channel as Monster aura; they never overlap).
            if is_crowned_king:
                self._draw_royal_aura(sx, sy)

            # ── Piece rendering: sprite or glyph fallback ─────────────────
            if self._piece_sprites is not None:
                king_archetype = archetype_map.get(pos) if is_crowned_king else None
                if has_monster and archetype:
                    piece_surf = self._piece_sprites.get_archetype(
                        unit_info.piece_type, unit_info.owner, archetype
                    )
                elif is_crowned_king and king_archetype:
                    piece_surf = self._piece_sprites.get_archetype(
                        unit_info.piece_type, unit_info.owner, king_archetype
                    )
                else:
                    piece_surf = self._piece_sprites.get(
                        unit_info.piece_type, unit_info.owner
                    )
            else:
                piece_surf = None
            if piece_surf is not None:
                # Sprite path — blit the pre-rendered square-sized surface
                self._surface.blit(piece_surf, (sx, sy))
            else:
                # Glyph fallback (no cache or unknown piece type)
                shadow = self._font_large.render(glyph, True, shadow_color)
                sw = shadow.get_width()
                sh = shadow.get_height()
                bx = sx + (SQUARE_SIZE - sw) // 2 + 1
                by = sy + (SQUARE_SIZE - sh) // 2 + 1
                self._surface.blit(shadow, (bx, by))

                glyph_offset_y = -6 if has_monster else 0
                text = self._font_large.render(glyph, True, text_color)
                tw = text.get_width()
                th = text.get_height()
                bx = sx + (SQUARE_SIZE - tw) // 2
                by = sy + (SQUARE_SIZE - th) // 2 + glyph_offset_y
                self._surface.blit(text, (bx, by))

            # Stage 5: gold underline bar + monster name for transformed pieces
            if has_monster:
                # Gold underline bar at the bottom of the square
                bar_h = 4
                bar_y = sy + SQUARE_SIZE - bar_h - 1
                pygame.draw.rect(
                    self._surface, SUMMON_VESSEL_DOT,
                    pygame.Rect(sx + 3, bar_y, SQUARE_SIZE - 6, bar_h),
                    border_radius=2,
                )
                # Tiny monster name label
                monster_id = unit_info.monster_id
                short_name = monster_id.replace("_", " ").title() if monster_id else ""
                if short_name:
                    lbl = self._font_small.render(short_name, True, SUMMON_VESSEL_DOT)
                    lbl_x = sx + (SQUARE_SIZE - lbl.get_width()) // 2
                    lbl_y = bar_y - lbl.get_height() - 1
                    self._surface.blit(lbl, (lbl_x, lbl_y))

                # Stage 5+: activatable-ability badge (cyan dot, top-right corner)
                # Shown when the monster has at least one activatable effect.
                activatable = getattr(unit_info, "activatable_effects", ())
                if activatable:
                    badge_r = 6
                    badge_cx = sx + SQUARE_SIZE - badge_r - 3
                    badge_cy = sy + badge_r + 3
                    pygame.draw.circle(
                        self._surface, ACTIVATABLE_DOT,
                        (badge_cx, badge_cy), badge_r,
                    )
                    # White outline so it reads on any square colour
                    pygame.draw.circle(
                        self._surface, WHITE_PIECE,
                        (badge_cx, badge_cy), badge_r, 1,
                    )

            # Stage 10: Crowned King — gold underline bar + King-name label
            # (mirrors the Monster treatment above) plus a crown badge in
            # the corner (a King has no activatable-ability badge to
            # collide with there).
            if is_crowned_king:
                bar_h = 4
                bar_y = sy + SQUARE_SIZE - bar_h - 1
                pygame.draw.rect(
                    self._surface, ROYAL_BAR,
                    pygame.Rect(sx + 3, bar_y, SQUARE_SIZE - 6, bar_h),
                    border_radius=2,
                )
                short_name = king_card_id.replace("_", " ").title()
                lbl = self._font_small.render(short_name, True, ROYAL_BAR)
                lbl_bg = pygame.Surface((lbl.get_width() + 4, lbl.get_height() + 2), pygame.SRCALPHA)
                lbl_bg.fill(ROYAL_LABEL_BG)
                lbl_x = sx + (SQUARE_SIZE - lbl.get_width()) // 2
                lbl_y = bar_y - lbl.get_height() - 1
                self._surface.blit(lbl_bg, (lbl_x - 2, lbl_y - 1))
                self._surface.blit(lbl, (lbl_x, lbl_y))

                crown = self._font_small.render("\U0001F451", True, ROYAL_AURA_GOLD)
                self._surface.blit(crown, (
                    sx + SQUARE_SIZE - crown.get_width() - 2,
                    sy + 1,
                ))

    def _draw_royal_aura(self, sx: int, sy: int) -> None:
        """
        Stage 10 — a Crowned King's aura, behind the glyph.

        Deliberately more elaborate than a Monster's static single ring +
        glow (ui/colors.py ROYAL_AURA_* docstring): a slow breathing pulse
        (via ``pygame.time.get_ticks()``) drives the outer glow's radius
        and alpha, layered under a solid gold ring and a thinner inner
        violet ring — there's only ever one Crowned King per side, so it
        should read as singular, not just "another archetype colour".
        """
        t = pygame.time.get_ticks() / 1000.0
        pulse = (math.sin(t * 2.4) + 1) / 2   # 0..1, ~2.6s breathing cycle

        aura = pygame.Surface((SQUARE_SIZE, SQUARE_SIZE), pygame.SRCALPHA)
        cx, cy = SQUARE_SIZE // 2, SQUARE_SIZE // 2
        glow_radius = SQUARE_SIZE // 2 - 3 + int(pulse * 2)
        outer_radius = SQUARE_SIZE // 2 - 3
        inner_radius = SQUARE_SIZE // 2 - 8
        glow_alpha = int(55 + pulse * 45)

        pygame.draw.circle(aura, (*ROYAL_AURA_GOLD, glow_alpha), (cx, cy), glow_radius)
        pygame.draw.circle(aura, (*ROYAL_AURA_GOLD, 210), (cx, cy), outer_radius, 3)
        pygame.draw.circle(aura, (*ROYAL_AURA_VIOLET, 180), (cx, cy), inner_radius, 1)
        self._surface.blit(aura, (sx, sy))

    # ── Hit-test helper ───────────────────────────────────────────────────

    def pos_from_click(self, mx: int, my: int) -> Position | None:
        """Convert a mouse click to a board Position, or None if outside."""
        return _screen_to_pos(mx, my, self._flip)

    # ── Stage 6: hover tooltip ──────────────────────────────────────────────

    def draw_tooltip(
        self,
        mouse_pos: tuple[int, int],
        lines: list[tuple[str, tuple]],
    ) -> None:
        """
        Draw a small stacked-text tooltip box near ``mouse_pos``.

        ``lines`` is title-first: ``[(text, color), ...]``.  The caller
        (AppController) decides WHAT to show (Trap info, zone-effect info)
        — this only knows HOW to draw a box of text lines.  Clamped to stay
        fully on-screen.
        """
        from game.ui.colors import TOOLTIP_BG, TOOLTIP_BORDER

        if not lines:
            return

        surfs = [self._font_small.render(text, True, color) for text, color in lines]
        pad = 6
        line_h = max(s.get_height() for s in surfs) + 2
        w = max(s.get_width() for s in surfs) + pad * 2
        h = line_h * len(surfs) + pad * 2

        mx, my = mouse_pos
        x = mx + 16
        y = my + 16
        surface_w = self._surface.get_width()
        surface_h = self._surface.get_height()
        if x + w > surface_w:
            x = surface_w - w - 4
        if y + h > surface_h:
            y = surface_h - h - 4

        box = pygame.Surface((w, h), pygame.SRCALPHA)
        box.fill(TOOLTIP_BG)
        pygame.draw.rect(box, TOOLTIP_BORDER, box.get_rect(), 1)
        for i, surf in enumerate(surfs):
            box.blit(surf, (pad, pad + i * line_h))
        self._surface.blit(box, (x, y))

    # ── Stage 14: transient VFX (summon / spell / trap / ritual / coronation) ──
    #
    # All of it is pure code — circles, lines, a fading flash — drawn onto
    # ``_anim_overlay`` (a 3×3-square SRCALPHA scratch canvas, re-filled
    # transparent for every animation so alpha actually fades instead of
    # compositing onto whatever the last one drew) and blitted centered on
    # the target square. No image assets, no per-frame allocation.
    #
    # Each ``_anim_*`` method receives ``t`` already clamped to 0..1 — 0 the
    # instant the triggering event fired, 1 the instant its ``duration`` in
    # AppController's animation record runs out — and draws directly onto
    # ``self._anim_overlay`` centered at ``c`` (both axes, since the canvas
    # is square). It never touches game state and never reads the clock
    # itself — timing is entirely AppController's to own, exactly like the
    # "own vs enemy" color decisions ``aura_colors`` etc. already are.

    def _draw_animations(self, animations: "list[dict]") -> None:
        if not animations:
            return
        now = pygame.time.get_ticks()
        canvas = self._anim_overlay.get_width()
        center = canvas // 2
        for anim in animations:
            pos = anim.get("pos")
            if pos is None:
                continue
            duration = max(1, anim.get("duration", 500))
            t = (now - anim.get("start", now)) / duration
            if t < 0.0 or t > 1.0:
                continue
            draw_fn = getattr(self, f"_anim_{anim.get('kind')}", None)
            if draw_fn is None:
                continue
            self._anim_overlay.fill((0, 0, 0, 0))
            draw_fn(center, anim.get("color", (230, 200, 90)), t)
            sx, sy = _sq_to_screen(pos, self._flip)
            self._surface.blit(
                self._anim_overlay,
                (sx + SQUARE_SIZE // 2 - center, sy + SQUARE_SIZE // 2 - center),
            )

    def _anim_summon(self, c: int, color: tuple, t: float) -> None:
        """A Monster materializing on its vessel: two staggered rings
        expanding outward from the square, plus a brief white flash at
        the very start so the piece reads as *appearing*, not just tinted."""
        base_r = SQUARE_SIZE // 2
        for delay in (0.0, 0.2):
            lt = (t - delay) / (1 - delay)
            if lt <= 0.0 or lt > 1.0:
                continue
            r = int(base_r * (0.2 + 0.9 * lt))
            alpha = int(230 * (1 - lt))
            if r > 0 and alpha > 0:
                pygame.draw.circle(self._anim_overlay, (*color, alpha), (c, c), r, 3)
        if t < 0.22:
            flash_a = int(150 * (1 - t / 0.22))
            pygame.draw.circle(self._anim_overlay, (255, 255, 255, flash_a), (c, c), base_r)

    def _anim_spell(self, c: int, color: tuple, t: float) -> None:
        """A Spell landing: a soft glow behind a burst of rays reaching
        outward from the target square, slowly rotating as they fade."""
        base_r = SQUARE_SIZE // 2
        glow_a = int(110 * (1 - t))
        if glow_a > 0:
            pygame.draw.circle(self._anim_overlay, (*color, glow_a), (c, c), int(base_r * 0.6))
        n_rays = 8
        length = base_r * (0.35 + 0.8 * t)
        inner = length * 0.35
        alpha = int(230 * (1 - t))
        if alpha > 0:
            for i in range(n_rays):
                ang = (2 * math.pi * i / n_rays) + t * 0.6
                x1, y1 = c + math.cos(ang) * inner, c + math.sin(ang) * inner
                x2, y2 = c + math.cos(ang) * length, c + math.sin(ang) * length
                pygame.draw.line(self._anim_overlay, (*color, alpha), (x1, y1), (x2, y2), 3)

    def _anim_trap_set(self, c: int, color: tuple, t: float) -> None:
        """A Trap being planted: deliberately DISCREET — a single faint
        ring that barely grows before fading, over a short lifetime. A Trap
        stays concealed; this should read as a quiet placement, not a
        showy reveal (that's reserved for _anim_trap_trigger)."""
        base_r = SQUARE_SIZE // 2
        r = int(base_r * (0.35 + 0.35 * t))
        alpha = int(100 * (1 - t))
        if r > 0 and alpha > 0:
            pygame.draw.circle(self._anim_overlay, (*color, alpha), (c, c), r, 2)

    def _anim_trap_trigger(self, c: int, color: tuple, t: float) -> None:
        """A Trap springing: a sharp expanding ring, jagged spikes bursting
        outward, and a hot flash at the very start — the loud counterpart
        to the quiet _anim_trap_set."""
        base_r = SQUARE_SIZE // 2
        r = int(base_r * (0.25 + 1.0 * t))
        alpha = int(220 * (1 - t))
        if r > 0 and alpha > 0:
            pygame.draw.circle(self._anim_overlay, (*color, alpha), (c, c), r, 4)
        n_spikes = 6
        length = base_r * (0.5 + 0.9 * t)
        alpha2 = int(230 * (1 - t))
        if alpha2 > 0:
            for i in range(n_spikes):
                ang = (2 * math.pi * i / n_spikes) + math.pi / n_spikes
                x2, y2 = c + math.cos(ang) * length, c + math.sin(ang) * length
                pygame.draw.line(self._anim_overlay, (*color, alpha2), (c, c), (x2, y2), 3)
        if t < 0.15:
            flash_a = int(200 * (1 - t / 0.15))
            pygame.draw.circle(self._anim_overlay, (255, 240, 200, flash_a), (c, c), base_r)

    def _anim_ritual(self, c: int, color: tuple, t: float) -> None:
        """A Ritual completing: bigger and longer than a plain summon —
        three staggered gold/violet rings (the same pairing as a Crowned
        King's static aura) that expand PAST the square's edge, plus
        slowly-rotating rays, so it reads as grander than an ordinary
        summon even though a Monster lands the same way underneath it."""
        base_r = SQUARE_SIZE // 2
        for i, delay in enumerate((0.0, 0.22, 0.44)):
            lt = (t - delay) / (1 - delay)
            if lt <= 0.0 or lt > 1.0:
                continue
            r = int(base_r * (0.3 + 1.6 * lt))
            alpha = int(210 * (1 - lt))
            if r <= 0 or alpha <= 0:
                continue
            ring_color = ROYAL_AURA_GOLD if i % 2 == 0 else ROYAL_AURA_VIOLET
            pygame.draw.circle(self._anim_overlay, (*ring_color, alpha), (c, c), r, 3)
        n_rays = 12
        length = base_r * (1.1 + 0.6 * t)
        alpha_r = int(150 * (1 - t))
        if alpha_r > 0:
            for i in range(n_rays):
                ang = (2 * math.pi * i / n_rays) + t * 1.2
                x2, y2 = c + math.cos(ang) * length, c + math.sin(ang) * length
                pygame.draw.line(self._anim_overlay, (*ROYAL_AURA_GOLD, alpha_r), (c, c), (x2, y2), 2)

    def _anim_coronation(self, c: int, color: tuple, t: float) -> None:
        """A King's Coronation — the grandest effect on the board: four
        staggered gold/violet rings expanding well past the square, a wide
        rotating ray-burst, and a warm flash at the start. Longest-running
        of every animation here, reflecting there is only ever one Crowned
        King per side (README §17)."""
        base_r = SQUARE_SIZE // 2
        for i, delay in enumerate((0.0, 0.15, 0.32, 0.5)):
            lt = (t - delay) / (1 - delay)
            if lt <= 0.0 or lt > 1.0:
                continue
            r = int(base_r * (0.3 + 2.0 * lt))
            alpha = int(200 * (1 - lt))
            if r <= 0 or alpha <= 0:
                continue
            ring_color = ROYAL_AURA_GOLD if i % 2 == 0 else ROYAL_AURA_VIOLET
            width = 4 if i < 2 else 2
            pygame.draw.circle(self._anim_overlay, (*ring_color, alpha), (c, c), r, width)
        n_rays = 16
        length = base_r * (1.3 + 0.8 * t)
        alpha_r = int(180 * (1 - t))
        if alpha_r > 0:
            for i in range(n_rays):
                ang = (2 * math.pi * i / n_rays) + t * 0.8
                x2, y2 = c + math.cos(ang) * length, c + math.sin(ang) * length
                pygame.draw.line(self._anim_overlay, (*ROYAL_AURA_GOLD, alpha_r), (c, c), (x2, y2), 2)
        if t < 0.2:
            flash_a = int(190 * (1 - t / 0.2))
            pygame.draw.circle(self._anim_overlay, (255, 250, 220, flash_a), (c, c), int(base_r * 1.3))
