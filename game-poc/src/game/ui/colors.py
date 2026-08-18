"""
ui/colors.py — Color palette for the Pygame UI (Stage 3)
=========================================================

All colors are defined here as RGB tuples so they can be adjusted
in a single place without hunting through render code.

Naming convention: CATEGORY_ROLE or just ROLE for shared values.
"""

# ── Board ──────────────────────────────────────────────────────────────────
LIGHT_SQUARE   = (240, 217, 181)   # classic light square (cream)
DARK_SQUARE    = (181, 136,  99)   # classic dark square  (brown)

# ── Selection / highlighting ───────────────────────────────────────────────
SELECTED_TINT       = ( 20, 160,  20,  90)  # green overlay on selected piece square (RGBA)
LEGAL_DOT           = ( 50, 180,  50, 150)  # filled circle on legal target (RGBA)
LEGAL_CAPTURE       = (200,  50,  50, 140)  # ring on legal capture target (RGBA)
CASTLE_DOT          = ( 50, 100, 200, 150)  # legal castle destination

# Stage 5: summon mode vessel highlights (gold)
SUMMON_VESSEL_TINT  = (210, 170,   0,  80)  # RGBA gold tint on valid vessel squares
SUMMON_VESSEL_DOT   = (255, 210,   0)       # bright gold ring border on vessel

# Stage 5+: activatable-ability badge on monster pieces (cyan dot, top-right corner)
ACTIVATABLE_DOT     = ( 80, 220, 220)       # bright cyan badge

# Stage 5+: inspect tint (purple overlay on selected-for-inspection unit square)
INSPECT_TINT        = (160,  80, 220,  80)  # RGBA purple tint

# ── Check / danger ─────────────────────────────────────────────────────────
CHECK_TINT     = (200,  20,  20, 110)  # red tint on checked king square

# ── Piece text colors ──────────────────────────────────────────────────────
WHITE_PIECE    = (255, 255, 255)
WHITE_PIECE_SHADOW = (60, 60, 60)
BLACK_PIECE    = ( 20,  20,  20)
BLACK_PIECE_SHADOW = (200, 200, 200)

# ── Sidebar / HUD ──────────────────────────────────────────────────────────
SIDEBAR_BG     = ( 30,  30,  40)
HUD_TEXT       = (220, 220, 220)
HUD_LABEL      = (140, 140, 160)
HUD_ACCENT     = ( 80, 200, 120)  # active player indicator
HUD_WARN       = (220, 100,  60)  # check warning
HUD_DUEL       = (180,  80, 200)  # final duel indicator

# ── Promotion dialog ───────────────────────────────────────────────────────
DIALOG_BG      = ( 40,  40,  55)
DIALOG_BORDER  = (100, 100, 130)
DIALOG_HOVER   = ( 60,  60,  80)

# ── Board coordinate labels ────────────────────────────────────────────────
COORD_LIGHT    = (181, 136,  99)   # labels on light squares (use dark sq colour)
COORD_DARK     = (240, 217, 181)   # labels on dark squares  (use light sq colour)

# ── General ────────────────────────────────────────────────────────────────
BLACK          = (  0,   0,   0)
WHITE          = (255, 255, 255)
