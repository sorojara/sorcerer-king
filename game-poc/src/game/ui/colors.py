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

# Stage 6: Traps are always visible as a marker + zone, but identity is only
# shown for YOUR OWN Traps/Spells — the opponent's only reveal the zone
# itself (per-owner tint), never a name/label/tooltip detail.  "OWN" =
# belongs to the player whose Observation is currently being rendered
# (this is a hotseat app — obs.player_id is always the active player);
# "ENEMY" = belongs to the other player.
TRAP_MARKER_OWN      = ( 90, 170, 255)       # bright blue ring  — your own Trap
TRAP_MARKER_ENEMY    = (255,  90,  70)       # bright red ring   — enemy Trap (identity hidden)
TRAP_LABEL_BG        = ( 20,  15,  35, 170)  # RGBA backing for the trap name label (own only)

# Stage 6: Trap activation areas + persistent Spell/Trap zone effects
# (frozen/scorched/blocked/cursed) all share this own/enemy tint pair —
# deliberately NOT color-coded per effect type, since that would leak what
# an enemy zone actually does.  Your own zones' exact effect is available
# via hover tooltip instead.
ZONE_TINT_OWN        = ( 70, 150, 230,  65)  # RGBA — your own Trap/Spell zones
ZONE_TINT_ENEMY       = (230,  70,  55,  65)  # RGBA — enemy Trap/Spell zones

# Stage 6: hover tooltip (Trap/zone info on mouseover)
TOOLTIP_BG          = ( 20,  20,  28, 235)  # RGBA near-opaque backing
TOOLTIP_BORDER       = (120, 120, 150)
TOOLTIP_TEXT         = (230, 230, 235)
TOOLTIP_TITLE        = (255, 210,   0)      # gold — matches SUMMON_VESSEL_DOT family

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
