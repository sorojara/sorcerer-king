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
TRAP_MARKER_OWN      = ( 90, 170, 255)       # bright blue ring  — your own Trap (dormant)
TRAP_MARKER_ENEMY    = (255,  90,  70)       # bright red ring   — enemy Trap (dormant, identity hidden)
TRAP_LABEL_BG        = ( 20,  15,  35, 170)  # RGBA backing for the trap name label (own only)

# Activated Trap markers — vivid gold/orange so it's immediately obvious
# the trap is hot and can fire this turn.
TRAP_MARKER_OWN_ACTIVE   = (255, 210,   0)  # gold ring   — your own Trap is activated
TRAP_MARKER_ENEMY_ACTIVE = (255, 130,   0)  # orange ring — enemy Trap is activated

# Stage 6: Trap danger zones (dormant) — muted blue/red tint shows the area
# of effect but doesn't scream urgency for a trap that hasn't fired yet.
ZONE_TINT_OWN        = ( 70, 150, 230,  55)  # RGBA — your own dormant Trap zone
ZONE_TINT_ENEMY      = (230,  70,  55,  55)  # RGBA — enemy dormant Trap zone

# Activated Trap zones — vivid gold/orange, clearly distinct from dormant.
ZONE_TINT_OWN_ACTIVE   = (255, 210,   0,  70)  # RGBA gold   — your activated Trap zone
ZONE_TINT_ENEMY_ACTIVE = (255, 130,   0,  70)  # RGBA orange — enemy activated Trap zone

# Active persistent/spatial Spell zones (square_effects: frozen/scorched/
# blocked/cursed) — these are always "on" while they exist, so they get a
# distinct purple tint to read differently from both dormant and activated
# Trap zones.  Own/enemy follow the same ownership framing convention.
ZONE_TINT_SPELL_OWN   = (160,  80, 230,  60)  # RGBA purple — your active Spell zone
ZONE_TINT_SPELL_ENEMY = (200,  50, 180,  60)  # RGBA magenta — enemy active Spell zone

# Stage 6: hover tooltip (Trap/zone info on mouseover)
TOOLTIP_BG          = ( 20,  20,  28, 235)  # RGBA near-opaque backing
TOOLTIP_BORDER       = (120, 120, 150)
TOOLTIP_TEXT         = (230, 230, 235)
TOOLTIP_TITLE        = (255, 210,   0)      # gold — matches SUMMON_VESSEL_DOT family

# ── Stage 9: Territory ─────────────────────────────────────────────────────
# A bold base-layer tint (own/enemy) drawn under every other zone tint
# (Trap/Spell/selection still stack visibly on top of it), but strong
# enough on its own to read at a glance — this is a primary spatial
# mechanic (README §8), not a faint hint.
TERRITORY_TINT_OWN     = ( 40, 200, 100, 115)  # RGBA vivid green — your Territory
TERRITORY_TINT_ENEMY   = (220,  50,  50, 115)  # RGBA vivid red   — enemy Territory
# Border stroke drawn along the outer edge of each Territory's footprint
# (opaque — always fully saturated, regardless of fill alpha) so the shape
# reads clearly even where fills overlap or sit under other tints.
TERRITORY_BORDER_OWN   = ( 40, 220, 110)
TERRITORY_BORDER_ENEMY = (230,  60,  60)

# ── Stage 8: Buildings ────────────────────────────────────────────────────
# Own/enemy framing follows the same convention as the Trap markers above.
BUILDING_MARKER_OWN            = (120, 200, 140)  # green — your own Building (fallback rect)
BUILDING_MARKER_ENEMY          = (200, 140, 120)  # muted orange — enemy Building (fallback rect)
BUILDING_MARKER_UNDER_CONSTR   = (200, 180,  60)  # amber ring while UNDER_CONSTRUCTION (fallback)

# Stage 8+: ownership banners (thin stripe drawn over the building front sprite)
# These are intentionally more saturated than the fallback rects so they read
# clearly against the artwork without covering the structure itself.
BUILDING_BANNER_OWN            = ( 80, 220, 110)  # vivid green — your own building
BUILDING_BANNER_ENEMY          = (220, 110,  60)  # muted orange — enemy building
BUILDING_BANNER_UNDER_CONSTR   = (220, 185,  40)  # amber — under construction progress

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

# ── Stage 10: Crowned King (Coronation) ─────────────────────────────────────
# Deliberately its own palette rather than reusing SUMMON_VESSEL_DOT/
# ACTIVATABLE_DOT — a Crowned King should read as grander than an ordinary
# summoned Monster (there is only ever one per side), not merely "another
# aura colour". Two tones (gold outer, violet inner) give the pulsing
# double-ring in board_view.py a richer, layered look than the Monster
# aura's single ring + glow.
ROYAL_AURA_GOLD    = (255, 215,  90)   # outer pulsing ring + glow
ROYAL_AURA_VIOLET  = (200, 140, 255)   # inner ring accent
ROYAL_BAR          = (255, 215,  90)   # underline bar (mirrors SUMMON_VESSEL_DOT's role)
ROYAL_LABEL_BG     = ( 40,  30,  10, 200)  # RGBA backing for the King-name label
