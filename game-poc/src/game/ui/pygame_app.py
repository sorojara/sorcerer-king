"""
ui/pygame_app.py — Main Pygame application (Stage 3 / Stage 5)
===============================================================

AppController owns the top-level Pygame event loop, the Game instance,
and all UI sub-views.

Architecture rules:
  • The UI NEVER reads game.state directly.
  • Observation is the only game-world object consumed by render methods.
  • Legal actions are fetched via game.get_legal_actions(player_id) and
    submitted via game.execute(action).
  • AppController auto-advances REACTION and END phases so the human
    player only needs to interact during PREPARATION and CHESS phases.

Stage 5 interaction:
  • During PREPARATION, clicking a MON card in the hand enters
    "summon mode": valid vessel squares are highlighted in gold on the
    board.  Clicking a highlighted vessel fires SummonMonster.
    Pressing ESC or clicking the same card again cancels.
  • Clicking a monster unit (gold glyph tint) during PREPARATION shows
    its monster name in a tooltip; right-click / 'D' key dismisses.

Window layout:
  ┌─────────────────────┬──────────┐
  │  Board (640 × 640)  │ Sidebar  │
  │                     │ (200 px) │
  ├─────────────────────┘          │
  │  Hand strip (72 px)            │
  └────────────────────────────────┘
  Total: 840 × 712  (640 board + 72 hand = 712 tall, 640+200 = 840 wide)
"""

from __future__ import annotations

import datetime
import sys
from dataclasses import dataclass
from pathlib import Path

import pygame

from game.logger import get_logger

_log = get_logger(__name__)

from game.ai.controller import PlayerController
from game.ai.heuristic_bot import HeuristicBot
from game.ai.monte_carlo import MonteCarloBot, MonteCarloLimits
from game.ai.personality import DEFAULT_PERSONALITY, PERSONALITIES
from game.ai.personality_bot import PersonalityBot
from game.ai.random_bot import RandomBot
from game.ai.search import SearchLimits
from game.ai.search_bot import SearchBot
from game.cards.card import CardRegistry, load_registry_from_yaml
from game.chess.pieces import Position
from game.core.actions import (
    ActivateMonsterAbility,
    ActivateRitual,
    ActivateSpell,
    ActivateTrap,
    AttackBuilding,
    Castle,
    ChangeKing,
    CoronateKing,
    DeclareMercenary,
    DeclareRecompose,
    DiscardCard,
    DismissMonster,
    EndPreparation,
    EndTurn,
    FinalDuelAction,
    MovePiece,
    PlaceMercenaryPiece,
    PlaceTrap,
    PromotePawn,
    RepositionUnit,
    RevealRitual,
    SelectMercenaryCards,
    SelectRecomposeCards,
    StartConstruction,
    SummonMonster,
)
from game.core.phases import HAND_SIZE_LIMIT
from game.core.game import Game
from game.core.phases import KingCardStatus, Phase, PieceType, RevelationState
from game.mechanics.buildings import is_committed_builder
from game.ui.archetype_colors import aura_color_for
from game.ui.board_view import (
    BOARD_OFFSET_X,
    BOARD_OFFSET_Y,
    BOARD_PIXEL_SIZE,
    BoardView,
    BuildingSpriteCache,
    PieceSpriteCache,
    TileSpriteCache,
)
from game.ui.colors import BLACK, TOOLTIP_TEXT, TOOLTIP_TITLE
from game.ui.font import FTFont, load_font
from game.ui.hand_view import HandView
from game.ui.event_log import LOG_HEIGHT, EventLogPanel
from game.ui.overlays import (
    CardViewer,
    CardZoomOverlay,
    PromotionDialog,
    SidebarOverlay,
)
from game.ui.ritual_bar import BAR_HEIGHT as _RITUAL_BAR_H, RitualBar
from game.ui.state_io import export_state, import_state

# How many render frames the AI "thinks" before playing (visual pause)
_AI_THINK_FRAMES: int = 30  # 0.5 s at 60 fps


# ── Window geometry constants ──────────────────────────────────────────────
_BOARD_W: int = BOARD_PIXEL_SIZE          # 640
_BOARD_H: int = BOARD_PIXEL_SIZE          # 640
_HAND_H: int = HandView.HEIGHT            # 106  height of one hand row
_SIDEBAR_W: int = SidebarOverlay.SIDEBAR_WIDTH  # 200

# Stage 6: left sidebar — a dedicated, always-on hand-card reference panel
# (HandInfoPanel) replacing the old hover-to-see-info flow, which relied on
# MOUSEMOTION tracking that felt unreliable in practice. BOARD_OFFSET_X in
# board_view.py must match this exactly — see the note on that constant.
_LEFT_SIDEBAR_W: int = BOARD_OFFSET_X     # 220

# Stage 11: the Ritual strip (ui/ritual_bar.py) sits between the top of the
# window and the top of the board, spanning exactly the board's width. It
# only pushes the BOARD down — the two sidebars still start at y=0 and just
# get taller, which is free extra room for the CardViewer and the Event Log.
# BOARD_OFFSET_Y in board_view.py is the single source of truth for its
# height; _RITUAL_BAR_H is that same number under the widget's own name.
_RITUAL_H: int = _RITUAL_BAR_H            # 132 == BOARD_OFFSET_Y

WIN_W: int = _LEFT_SIDEBAR_W + _BOARD_W + _SIDEBAR_W   # 1060
WIN_H: int = _RITUAL_H + _BOARD_H + _HAND_H          # 878  (base — no debug row)
_WIN_H_DEBUG: int = _RITUAL_H + _BOARD_H + _HAND_H * 2  # 984  (with black-hand debug row)

_SIDEBAR_X: int = _LEFT_SIDEBAR_W + _BOARD_W   # 860 — right sidebar's left edge
_HAND_Y: int = _RITUAL_H + _BOARD_H      # 772  (base hand-strip top)

# Font sizes
_FONT_PX: int = 52    # chess glyph + large UI text
_FONT_SM_PX: int = 14  # small labels
_FONT_MD_PX: int = 20  # body text in the full-screen card zoom
_FONT_LG_PX: int = 30  # card name in the full-screen card zoom
_FONT_XS_PX: int = 12  # Ritual-strip sub-lines


# ─────────────────────────────────────────────────────────────────────────────
# The player roster — everything a side can be driven by
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class _PlayerChoice:
    """
    One selectable controller: a difficulty tier, a README §51 play style,
    or the human.

    The roster is the single source of truth for the picker popup AND for
    the sidebar button's label, so adding a controller means adding one
    entry here and nothing else.

    ``tier`` is the §52 difficulty word shown next to a stage bot's name;
    play styles leave it empty, because §51 is about *how* the bot plays,
    not how hard it thinks.
    """

    mode: str                       # "human" | "ai"
    kind: str                       # _ai_kinds value ("random", "search", …)
    personality: str | None         # set only when kind == "personality"
    name: str                       # picker row title
    tier: str                       # "Easy" / "Hard" / "" for a play style
    subtitle: str                   # one-line description, under the name
    detail: tuple[str, ...]         # what the detail panel lists
    label: str                      # sidebar button text

    def matches(self, mode: str, kind: str, personality: str | None) -> bool:
        if self.mode != mode:
            return False
        if mode != "ai":
            return True
        if self.kind != kind:
            return False
        return self.kind != "personality" or self.personality == personality


def _build_player_roster() -> list[_PlayerChoice]:
    """
    The picker's rows, in the order they are shown: the four AI stages by
    README §52 difficulty, then the five README §51 play styles, then the
    human.  Difficulty first because it is the coarse choice — a player
    picks how hard the game should be before they pick its flavour.
    """
    roster = [
        _PlayerChoice(
            mode="ai", kind="random", personality=None,
            name="Random", tier="Idiot",
            subtitle="Picks a legal action at random.",
            detail=("No evaluation, no search",
                    "Every legal action is equally likely",
                    "AI Stage 0 — README §45"),
            label="RANDOM AI",
        ),
        _PlayerChoice(
            mode="ai", kind="heuristic", personality=None,
            name="Heuristic", tier="Easy",
            subtitle="Scores every legal action once.",
            detail=("Weighted position evaluation",
                    "No search — one move deep",
                    "AI Stage 1 — README §46"),
            label="HEURISTIC AI",
        ),
        _PlayerChoice(
            mode="ai", kind="search", personality=None,
            name="Search", tier="Medium",
            subtitle="Searches the board a few moves ahead.",
            detail=("Alpha-beta, 3 plies, move ordering",
                    "Card decisions fall back to Easy",
                    "AI Stage 2 — README §47"),
            label="SEARCH AI",
        ),
        _PlayerChoice(
            mode="ai", kind="montecarlo", personality=None,
            name="Monte Carlo", tier="Hard",
            subtitle="Guesses your hand, then searches.",
            detail=("Samples the hidden cards into whole worlds",
                    "Searches inside every world it draws",
                    "AI Stage 4 — README §49"),
            label="MONTE CARLO AI",
        ),
    ]
    roster += [
        _PlayerChoice(
            mode="ai", kind="personality", personality=style.key,
            name=style.name, tier="",
            subtitle=style.blurb,
            detail=style.priorities,
            label=f"{style.name.upper()} AI",
        )
        for style in PERSONALITIES.values()
    ]
    roster.append(
        _PlayerChoice(
            mode="human", kind="random", personality=None,
            name="Human", tier="",
            subtitle="You play this side yourself.",
            detail=("Click the board to move",
                    "The hand strip and sidebar buttons are yours"),
            label="HUMAN",
        )
    )
    return roster


#: Every controller a side can be driven by, in picker order.
_PLAYER_ROSTER: list[_PlayerChoice] = _build_player_roster()


class AppController:
    """
    Pygame application controller.

    Owns:
      • A Game instance (facade to the engine).
      • All UI views (BoardView, SidebarOverlay, HandView, PromotionDialog).
      • Input state (selected_pos, legal_dests, castle_dests).
      • Player mode config: each side is Human or one of the AI models.

    Each side's controller is picked from the sidebar button, which opens a
    popup listing the whole roster — the four AI stages labelled by README
    §52 difficulty, the five README §51 play styles, and the human — so any
    two of them can be matched against each other without leaving the app.
    ``_PLAYER_ROSTER`` is the single source of truth for that list and for
    the button's own label.

    ``_player_modes`` keeps the coarse "human" / "ai" split every phase
    handler branches on, while ``_ai_kinds`` records WHICH bot plays an
    "ai" side:

        "random"      — AI Stage 0 RandomBot      (README §45)  "Idiot"
        "heuristic"   — AI Stage 1 HeuristicBot   (README §46)  "Easy"
        "search"      — AI Stage 2 SearchBot      (README §47)  "Medium"
        "montecarlo"  — AI Stage 4 MonteCarloBot  (README §49)  "Hard"
        "personality" — an AI Personality         (README §51); which play
                        style is in ``_ai_personalities``

    All five are ordinary PlayerControllers, so they receive an Observation and
    the legal action list and nothing else (README §44).  A short visual
    delay (_AI_THINK_FRAMES) lets the human see each AI move before the
    next action.

    That delay, and the search-based bots' own thinking time, are both
    tunable at runtime via ``_ai_speed`` / ``_AI_SPEEDS`` (sidebar "AI
    Speed" button or the S key) — see the docstring on ``_AI_SPEEDS`` for
    why an AI-vs-AI spectator match needs this and a human-vs-AI game
    usually doesn't.
    """

    # ── Construction ──────────────────────────────────────────────────────

    def __init__(self, seed: int = 0) -> None:
        # Init only display + time; never call pygame.init() which would
        # trigger pygame.font — broken circular import on Python 3.14 + 2.6.1.
        # Font init is handled inside ui/font.py via pygame._freetype.
        pygame.display.init()
        pygame.time.Clock()          # warm up time module
        pygame.display.set_caption("Sorcerer King — Stage 4 PoC")

        self._show_black_hand: bool = False
        # Stage 9: toggle the Territory board tint on/off.
        self._show_territory: bool = True
        self._screen = pygame.display.set_mode((WIN_W, WIN_H))
        self._clock = pygame.time.Clock()

        # Fonts via compat shim (pygame._freetype, no sysfont/font circular dep)
        self._font_large: FTFont = load_font(_FONT_PX)
        self._font_small: FTFont = load_font(_FONT_SM_PX)
        # Stage 11: the full-screen card zoom needs readable body copy, and
        # the Ritual strip needs a size below _FONT_SM_PX for its sub-lines.
        self._font_zoom_title: FTFont = load_font(_FONT_LG_PX)
        self._font_zoom_body: FTFont = load_font(_FONT_MD_PX)
        self._font_tiny: FTFont = load_font(_FONT_XS_PX)

        # ── Card registry ─────────────────────────────────────────────────
        _data_dir = Path(__file__).parent.parent / "data"
        try:
            self._registry: CardRegistry = load_registry_from_yaml(_data_dir)
        except Exception:
            self._registry = CardRegistry()  # empty registry — graceful fallback

        # ── Game engine ──────────────────────────────────────────────────
        self._game = Game.new(seed=seed, registry=self._registry)

        # ── UI sub-views ─────────────────────────────────────────────────
        _assets_sheet = _data_dir / "images" / "spritesheet" / "assets-3.png"
        _building_sheet = _data_dir / "images" / "buildings" / "basic_buildings.png"
        _building_sprites = BuildingSpriteCache(_building_sheet)
        _tile_sprites = TileSpriteCache(_assets_sheet)
        _piece_sprites = PieceSpriteCache(_assets_sheet)
        self._board_view = BoardView(
            surface=self._screen,
            font_large=self._font_large,
            font_small=self._font_small,
            building_sprites=_building_sprites,
            tile_sprites=_tile_sprites,
            piece_sprites=_piece_sprites,
        )
        self._sidebar = SidebarOverlay(
            surface=self._screen,
            font=self._font_large,
            font_small=self._font_small,
            x_offset=_SIDEBAR_X,
        )
        self._hand_view = HandView(
            surface=self._screen,
            font=self._font_small,
            y_offset=_HAND_Y,
            strip_width=_BOARD_W,
            registry=self._registry,
            x_offset=BOARD_OFFSET_X,
            images_dir=_data_dir / "images",
        )
        self._card_viewer = CardViewer(
            surface=self._screen,
            font=self._font_large,
            font_small=self._font_small,
            width=_LEFT_SIDEBAR_W,
            registry=self._registry,
            images_dir=_data_dir / "images",
        )
        self._event_log_panel = EventLogPanel(
            surface=self._screen,
            font_small=self._font_small,
            width=_LEFT_SIDEBAR_W,
        )
        # Stage 11: the Ritual strip above the board — both pools at a
        # glance, the viewer's own in full and the rival's redacted to
        # whatever their revelation state has leaked so far.
        self._ritual_bar = RitualBar(
            surface=self._screen,
            font=self._font_small,
            font_small=self._font_tiny,
            x_offset=BOARD_OFFSET_X,
            width=_BOARD_W,
            registry=self._registry,
        )
        # Stage 11: right-click a card in the CardViewer to blow it up
        # full-screen; holds the card_id being zoomed, None when closed.
        self._card_zoom = CardZoomOverlay(
            surface=self._screen,
            font_title=self._font_zoom_title,
            font_body=self._font_zoom_body,
            font_small=self._font_small,
            registry=self._registry,
            images_dir=_data_dir / "images",
        )
        self._card_zoom_id: "str | None" = None

        # ── Player modes & bots ───────────────────────────────────────────
        # "human" → human input; "ai" → the bot named by _ai_kinds plays.
        self._player_modes: dict[str, str] = {"white": "human", "black": "human"}
        self._ai_kinds: dict[str, str] = {"white": "random", "black": "random"}
        # README §51 play style used when _ai_kinds says "personality".
        self._ai_personalities: dict[str, str] = {
            "white": DEFAULT_PERSONALITY, "black": DEFAULT_PERSONALITY,
        }
        self._bot_seeds: dict[str, int] = {"white": 1, "black": 2}
        # AI think-speed tier — read by _make_bot (search budget) and
        # _tick_ai (think-delay), so it must exist before _bots is built.
        self._ai_speed: str = "normal"
        self._bots: dict[str, PlayerController] = {
            pid: self._make_bot(pid) for pid in ("white", "black")
        }

        # Countdown until the AI plays its chosen action (visual delay)
        self._ai_think_countdown: int = 0

        # ── Input state ──────────────────────────────────────────────────
        self._selected_pos: Position | None = None
        # Stage 13 — squares holding a COMPLETE enemy Building the selected
        # unit may besiege. Kept separate from _legal_dests because clicking
        # one fires AttackBuilding, which does NOT move the piece.
        self._siege_dests: list[Position] = []
        self._legal_dests: list[Position] = []
        self._castle_dests: list[Position] = []
        self._mouse_pos: tuple[int, int] = (0, 0)

        # Index of card highlighted for discard (-1 = none)
        self._discard_highlight: int = -1

        # Stage 7: recompose card selection — cards the human has toggled for return
        self._recompose_selected: list[str] = []

        # Stage 7: mercenary flow state
        # _mercenary_selected    — Monster card IDs the human has toggled for sacrifice
        # _mercenary_piece_type  — chosen piece type ("pawn" | "knight" | …); set by
        #                           the piece-type picker dialog before entering
        #                           MERCENARY_SELECTION phase
        self._mercenary_selected: list[str] = []
        self._mercenary_piece_type: str | None = None
        # Valid placement squares — highlighted on the board during MERCENARY_PLACEMENT
        self._mercenary_placement_squares: list[Position] = []

        # Stage 5: summon mode — card selected from hand awaiting vessel click
        # None = not in summon mode; str = card_id of the monster being summoned
        self._summon_card_id: str | None = None
        # Valid vessel positions for the currently selected summon card
        self._summon_vessel_positions: list[Position] = []

        # Stage 6: generic single-click targeting mode for Traps (PlaceTrap)
        # and "position"/"zone"/"trap" Spells (ActivateSpell) — maps every
        # clickable square directly to the concrete Action clicking it fires.
        # ActivateSpell target_type "none" never enters a mode (fires on
        # card click); target_type "piece" uses the two-step fields below.
        self._targeting_card_id: str | None = None
        self._targeting_action_by_pos: dict[Position, object] = {}
        # Which flavour of targeting is live — decides the preview terrain
        # the board paints under the gold tint ("trap" → Void Rift,
        # "spell" → Arcane Circle).
        self._targeting_kind: str | None = None

        # Terrain scars — squares where a Trap, Spell or Monster has finished
        # resolving keep a fading Cracked Earth mark.  Everything here is pure
        # presentation: the engine neither knows nor cares about scars, so the
        # bookkeeping lives on this side, fed by diffing consecutive
        # Observations and by scanning new events.  See _update_terrain_scars.
        self._reset_terrain_scars()

        # Stage 6: two-step targeting for "piece" Spells (arcane_reposition):
        # 1st click picks the source piece, 2nd click picks the destination.
        self._spell_piece_card_id: str | None = None
        self._spell_piece_actions: list = []
        self._spell_piece_source: Position | None = None

        # Stage 6 (corrected): CardViewer state — right-click driven, never
        # hover (hover proved unreliable).
        # _viewer_card_id      — the card currently shown in the left CardViewer.
        # _inspect_unit_pos    — board position of a right-clicked SUMMONED
        #   piece; set alongside _viewer_card_id so the purple board tint,
        #   ability buttons, and live statuses all know which unit is meant.
        #   None when the viewer is showing a hand card or a zone-list card
        #   (no unit context for those).
        # _zone_active_entries — (label, card_id) pairs for the "Active in
        #   this zone" section, populated by right-clicking a square inside
        #   one of the player's OWN active Trap/zone effects.
        self._viewer_card_id: str | None = None
        self._inspect_unit_pos: Position | None = None
        self._zone_active_entries: "list[tuple[str, str]] | None" = None

        # Promotion dialog — created on demand, destroyed after selection
        self._promotion_dialog: PromotionDialog | None = None

        # Stage 7: Mercenary piece-type picker dialog
        self._mercenary_picker: "_MercenaryPicker | None" = None

        # Stage 8: Build mode — a Building Pool entry selected via the
        # picker, awaiting a click on a highlighted builder-Pawn square.
        # None = not in build mode; str = the building_card_id being built.
        self._build_card_id: str | None = None
        self._build_positions: list[Position] = []
        self._build_picker: "_BuildPicker | None" = None

        # Stage 10: King picker/dialog + Succession cost-payment flow.
        # ``_king_picker`` is reused across every step (Coronate/Succeed
        # target choice, cost-type choice, Building choice) — ``_king_picker_mode``
        # says which step it's currently showing so the click handler knows
        # how to interpret the returned key. Once a piece-sacrifice cost is
        # pending, ``_king_sacrifice_positions`` highlights the valid board
        # squares (mirrors ``_build_positions``).
        self._king_picker: "_KingDialog | None" = None
        self._king_picker_mode: str | None = None          # "coronate" | "succeed" | "cost_choice" | "building_choice"
        self._king_picker_actions: list = []                # legal ChangeKing actions, filtered as the flow narrows
        self._king_succession_target: str | None = None    # king_card_id being succeeded to
        self._king_and_mode: bool = False                   # True = 2nd Succession (piece AND Building)
        self._king_sacrifice_positions: list[Position] = []
        self._king_sacrifice_building_id: str | None = None

        # Stage 11: Ritual picker/dialog. Two steps, reusing _KingDialog
        # (same generic list-picker): first pick WHICH Ritual to attempt
        # (one row per not-yet-activated Ritual with at least one legal
        # sacrifice combo), then — only if that Ritual has more than one
        # candidate combo — pick WHICH combo. Unlike King Succession, no
        # board-click step is needed: the engine already enumerated each
        # candidate's full sacrifice_positions in get_legal_actions.
        self._ritual_picker: "_KingDialog | None" = None
        self._ritual_picker_mode: str | None = None   # "choose_ritual" | "choose_combo"
        self._ritual_picker_actions: list = []          # legal ActivateRitual actions, filtered as the flow narrows
        self._ritual_picker_row_ids: list[str] = []     # row index → ritual_id, including disabled rows (see _do_ritual)

        # Player picker: the "who plays this side" popup opened by either
        # controller selector. ``_player_picker_side`` is the side being
        # configured. Opening it commits nothing, so Cancel needs no undo
        # state — the side is untouched until a row is clicked.
        self._player_picker: "_PlayerPicker | None" = None
        self._player_picker_side: str | None = None

        # Stage 12: Final Duel action picker. Unlike the King/Ritual pickers
        # above (multi-step flows persisted across frames), this one is
        # stateless — rebuilt fresh every _render() call from the current
        # legal Duel actions, since who's acting (defender/attacker) and
        # what's available flips every round. Reused only so the click
        # handler can read the row_rects the most recent draw() computed.
        self._duel_picker: "_KingDialog | None" = None

        # Auto-advance past phases that need no user input
        self._auto_advance()

    # ── Main loop ─────────────────────────────────────────────────────────

    def run(self) -> None:
        """Enter the main event-loop. Returns only when the user quits."""
        while True:
            for event in pygame.event.get():
                self._handle_event(event)

            # The player picker is a setup dialog, not a game decision:
            # freeze the match while it is open so the board does not move
            # on under the player choosing a controller.
            if self._player_picker is None:
                self._tick_ai()    # let AI play if it's their turn
            self._render()
            self._tick_toast()
            self._clock.tick(60)

    # ── Event dispatch ────────────────────────────────────────────────────

    def _handle_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.QUIT:
            pygame.quit()
            sys.exit()

        elif event.type == pygame.KEYDOWN:
            if event.key == pygame.K_q:
                pygame.quit()
                sys.exit()
            elif event.key == pygame.K_ESCAPE:
                if self._card_zoom_id is not None:
                    self._card_zoom_id = None
                    return
                if self._mercenary_picker is not None:
                    self._mercenary_picker = None
                    return
                if self._build_picker is not None:
                    self._build_picker = None
                    return
                if self._king_picker is not None or self._king_sacrifice_positions:
                    self._cancel_king_mode()
                    return
                if self._ritual_picker is not None:
                    self._cancel_ritual_mode()
                    return
                if self._player_picker is not None:
                    self._close_player_picker()
                    return
                self._cancel_summon()
                self._cancel_targeting_mode()
                self._cancel_spell_piece_mode()
                self._cancel_build_mode()
                self._deselect()
                self._inspect_unit_pos = None
                self._viewer_card_id = None
                self._zone_active_entries = None
            elif event.key == pygame.K_d:
                # D key: dismiss the monster on the selected piece (if any)
                self._try_dismiss_selected()
            elif event.key == pygame.K_a:
                # A key: activate the first available ability on the selected/inspected piece
                self._try_activate_ability()
            elif event.key == pygame.K_t:
                # T key: activate one of the player's own manual-trigger Traps
                # (time_anchor) — Stage 6.
                self._try_activate_trap()
            elif event.key == pygame.K_s:
                # S key: cycle the AI think-speed (Normal/Fast/Instant) —
                # mirrors the sidebar "AI Speed" button.
                self._toggle_ai_speed()

        elif event.type == pygame.MOUSEMOTION:
            self._mouse_pos = event.pos
            if self._promotion_dialog is not None:
                self._promotion_dialog.update_mouse(event.pos)
            if self._mercenary_picker is not None:
                self._mercenary_picker._mouse_pos = event.pos
            if self._build_picker is not None:
                self._build_picker._mouse_pos = event.pos
            if self._king_picker is not None:
                self._king_picker._mouse_pos = event.pos
            if self._ritual_picker is not None:
                self._ritual_picker._mouse_pos = event.pos
            if self._duel_picker is not None:
                self._duel_picker._mouse_pos = event.pos
            if self._player_picker is not None:
                self._player_picker._mouse_pos = event.pos
            self._sidebar.update_mouse(event.pos)
            self._card_viewer.update_mouse(event.pos)
            self._ritual_bar.update_mouse(event.pos)

        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mx, my = event.pos
            # Stage 11: the full-screen card zoom is modal — it swallows the
            # click either way, dismissing itself when the click lands
            # outside the big card and doing nothing when it lands on it.
            if self._card_zoom_id is not None:
                if self._card_zoom.handle_click(mx, my) == "close":
                    self._card_zoom_id = None
                return
            if self._player_picker is not None:
                result = self._player_picker.handle_click(mx, my)
                if result == "cancel":
                    self._close_player_picker()
                elif result is not None:
                    self._on_player_choice(result)
                return
            if self._mercenary_picker is not None:
                result = self._mercenary_picker.handle_click(mx, my)
                if result == "cancel":
                    self._mercenary_picker = None
                elif result is not None:
                    self._commit_mercenary(result)
                return
            if self._build_picker is not None:
                result = self._build_picker.handle_click(mx, my)
                if result == "cancel":
                    self._build_picker = None
                elif result is not None:
                    self._commit_build(result)
                return
            if self._king_picker is not None:
                result = self._king_picker.handle_click(mx, my)
                if result == "cancel":
                    self._cancel_king_mode()
                elif result is not None:
                    self._on_king_picker_choice(result)
                return
            if self._ritual_picker is not None:
                result = self._ritual_picker.handle_click(mx, my)
                if result == "cancel":
                    self._cancel_ritual_mode()
                elif result is not None:
                    self._on_ritual_picker_choice(result)
                return
            if self._duel_picker is not None:
                result = self._duel_picker.handle_click(mx, my)
                # No "cancel" concept for the Duel picker — "Advance" (always
                # present) is the safe pass; a miss just does nothing.
                if result is not None and result != "cancel":
                    self._on_duel_picker_choice(result)
                return
            if self._promotion_dialog is not None:
                self._handle_promotion_click(mx, my)
            else:
                self._handle_click(mx, my)

        elif event.type == pygame.MOUSEWHEEL:
            # Scroll the hand strip when the mouse is over it.
            if self._hand_view.is_over_hand(*self._mouse_pos):
                obs = self._current_obs()
                # wheel y: positive = scroll up (towards user) → move right in hand,
                # negative = scroll down → move left. One wheel tick = one card.
                self._hand_view.scroll(-event.y, len(list(obs.own_hand)))
            # Stage 11: scroll the Card Viewer (left sidebar) when the
            # inspected card's content — e.g. a Ritual stacked with its
            # summoned Monster — runs taller than the panel.
            elif (
                self._card_viewer.is_over(*self._mouse_pos)
                and self._mouse_pos[1] < self._screen.get_height() - LOG_HEIGHT
            ):
                # wheel y: positive = scroll up → move content down (toward
                # the top), matching the sign _hand_view.scroll() expects.
                self._card_viewer.scroll(-event.y)

        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 3:
            # Stage 6 (corrected): right-click sets the CardViewer's active
            # card. Priority: the open card zoom > a King-picker row > a
            # CardViewer card block (opens the zoom) > an "Active in this
            # zone" list entry > a Ritual-strip chip > a hand card > a
            # board square (unit / Trap-or-zone area).
            mx, my = event.pos

            # The zoom is modal for right-clicks too, so a stray one can't
            # quietly re-target the panel hidden underneath it.
            if self._card_zoom_id is not None:
                if self._card_zoom.handle_click(mx, my) == "close":
                    self._card_zoom_id = None
                return

            # Stage 10: right-click a row on the King picker (Coronation /
            # Succession target list, or a Building-cost choice) to inspect
            # that card without selecting it.
            if self._king_picker is not None:
                king_row_key = self._king_picker.key_at(mx, my)
                if king_row_key is not None:
                    if self._king_picker_mode in ("coronate", "succeed"):
                        self._viewer_card_id = king_row_key
                        self._inspect_unit_pos = None
                        self._zone_active_entries = None
                        return
                    if self._king_picker_mode == "building_choice":
                        bld = next(
                            (b for b in self._game.state.buildings if b.id == king_row_key),
                            None,
                        )
                        if bld is not None:
                            self._viewer_card_id = bld.building_card_id
                            self._inspect_unit_pos = None
                            self._zone_active_entries = None
                        return

            # Stage 11: right-click a row on the Ritual picker's first step
            # (choosing WHICH Ritual) to inspect it — including DISABLED
            # rows ("not ready" / "completed"): own_rituals is full
            # information to its owner regardless of readiness, so nothing
            # should be hidden from inspection. Reads the index-matched
            # _ritual_picker_row_ids rather than _KingDialog.key_at(), which
            # returns None for a disabled row's key (by design — see
            # _do_ritual). The second step's rows are sacrifice combos, not
            # cards, so nothing to inspect there.
            if self._ritual_picker is not None and self._ritual_picker_mode == "choose_ritual":
                for i, row_rect in enumerate(self._ritual_picker._row_rects):
                    if row_rect.collidepoint(mx, my) and i < len(self._ritual_picker_row_ids):
                        self._viewer_card_id = self._ritual_picker_row_ids[i]
                        self._inspect_unit_pos = None
                        self._zone_active_entries = None
                        return

            zone_card_id = self._card_viewer.zone_entry_from_click(mx, my)
            if zone_card_id is not None:
                self._viewer_card_id = zone_card_id
                self._inspect_unit_pos = None
                return

            # Stage 11: right-click the card already shown in the CardViewer
            # (or the "Summons:" block stacked under a Ritual) to read it at
            # full size — the 220 px panel truncates effects and description,
            # the zoom shows all of both.
            zoom_card_id = self._card_viewer.card_at(mx, my)
            if zoom_card_id is not None:
                self._card_zoom_id = zoom_card_id
                return

            # Stage 11: right-click a Ritual chip in the strip above the
            # board to open it in the CardViewer. Chips whose identity the
            # viewer isn't entitled to (a rival's SEALED/FORETOLD Ritual)
            # report None and are simply not inspectable.
            ritual_card_id = self._ritual_bar.ritual_at(mx, my)
            if ritual_card_id is not None:
                self._viewer_card_id = ritual_card_id
                self._inspect_unit_pos = None
                self._zone_active_entries = None
                return

            obs = self._current_obs()
            if self._player_modes.get(obs.active_player) == "human":
                hand_card_id = self._hand_view.card_from_click(mx, my, list(obs.own_hand))
                if hand_card_id is not None:
                    self._viewer_card_id = hand_card_id
                    self._inspect_unit_pos = None
                    self._zone_active_entries = None
                    return

            pos = self._board_view.pos_from_click(mx, my)
            if pos is not None:
                self._toggle_inspect(pos)

    # ── Click routing ─────────────────────────────────────────────────────

    def _handle_click(self, mx: int, my: int) -> None:
        """Route a left-click to the CardViewer, sidebar buttons, board, or hand strip."""
        # Stage 6: CardViewer ability buttons (left column) take top priority.
        viewer_ability_id = self._card_viewer.handle_ability_click(mx, my)
        if viewer_ability_id is not None:
            self._do_activate_ability(viewer_ability_id, self._inspect_unit_pos)
            return

        # Sidebar buttons take priority
        btn = self._sidebar.handle_click(mx, my)
        if btn == "export":
            self._do_export()
            return
        if btn == "import":
            self._do_import()
            return
        if btn == "toggle_white":
            self._toggle_mode("white")
            return
        if btn == "toggle_black":
            self._toggle_mode("black")
            return
        if btn == "toggle_black_hand":
            self._toggle_black_hand()
            return
        if btn == "toggle_territory":
            self._toggle_territory()
            return
        if btn == "toggle_speed":
            self._toggle_ai_speed()
            return
        if btn == "recompose":
            self._do_recompose()
            return
        if btn == "mercenary":
            self._do_mercenary()
            return
        if btn == "build":
            self._do_build()
            return
        if btn == "king":
            self._do_king()
            return
        if btn == "ritual":
            self._do_ritual()
            return
        if btn is not None and btn.startswith("trap:"):
            # Stage 6: "⚡ Activatable" section — fire a manual Trap directly.
            trap_instance_id = btn[len("trap:"):]
            self._do_activate_trap_by_id(trap_instance_id)
            return
        if btn is not None and btn.startswith("ability_at:"):
            # Stage 6: "⚡ Activatable" section — fire a monster ability on
            # ANY unit, not just the currently-inspected one. Trailing
            # segment (possibly empty) round-trips the target for
            # abilities that need one — see _compute_activatable_entries.
            _, f, r, ability_id, *target_parts = btn.split(":")
            target = None
            if target_parts:
                target_str = ":".join(target_parts)
                if target_str.startswith("pos:"):
                    _, tf, tr = target_str.split(":")
                    target = (int(tf), int(tr))
                elif target_str.startswith("id:"):
                    target = target_str[len("id:"):]
            self._do_activate_ability(ability_id, Position(int(f), int(r)), target=target)
            return

        obs = self._current_obs()
        active = obs.active_player

        # When it's an AI's turn, ignore all hand clicks.
        # Board clicks are still routed (but _handle_board_click guards against AI input).
        if self._player_modes.get(active) == "ai":
            pos = self._board_view.pos_from_click(mx, my)
            if pos is not None:
                self._handle_board_click(pos)
            return

        # Hand-strip clicks — use own_hand (active player's cards) for hit-test;
        # this matches what _render() draws (active human player's hand).
        if obs.phase == Phase.DISCARD:
            card_id = self._hand_view.card_from_click(mx, my, list(obs.own_hand))
            if card_id is not None:
                self._execute_and_advance(
                    DiscardCard(player_id=active, card_id=card_id),
                    active,
                )
                return

        # Stage 7: Recompose card selection — human picks N cards to return.
        if obs.phase == Phase.RECOMPOSE_SELECTION:
            card_id = self._hand_view.card_from_click(mx, my, list(obs.own_hand))
            if card_id is not None:
                self._handle_recompose_card_click(card_id, obs)
                return

        # Stage 7: Mercenary card selection — human picks N Monster cards to sacrifice.
        if obs.phase == Phase.MERCENARY_SELECTION:
            card_id = self._hand_view.card_from_click(mx, my, list(obs.own_hand))
            if card_id is not None:
                self._handle_mercenary_card_click(card_id, obs)
                return

        # Stage 7: Mercenary placement — human clicks a square in their first 2 ranks.
        if obs.phase == Phase.MERCENARY_PLACEMENT:
            pos = self._board_view.pos_from_click(mx, my)
            if pos is not None and pos in self._mercenary_placement_squares:
                self._execute_and_advance(
                    PlaceMercenaryPiece(player_id=active, position=pos),
                    active,
                )
                self._mercenary_placement_squares = []
                return

        # Stage 5: During PREPARATION a hand click may start summon mode
        if obs.phase == Phase.PREPARATION:
            card_id = self._hand_view.card_from_click(mx, my, list(obs.own_hand))
            if card_id is not None:
                self._handle_hand_click_preparation(card_id, obs)
                return

        pos = self._board_view.pos_from_click(mx, my)
        if pos is not None:
            self._handle_board_click(pos)

    def _handle_board_click(self, pos: Position) -> None:
        """
        Two-click selection model:
          1st click: select a piece that belongs to the active player.
          2nd click: attempt to move the selected piece to the clicked square.
                     If the square matches a castle destination, castle instead.

        Stage 5 PREPARATION additions:
          • If in summon mode (_summon_card_id set): clicking a highlighted
            vessel fires SummonMonster; clicking elsewhere cancels summon mode.
          • Otherwise (no summon mode):
              – Click on own piece → select it and show legal move previews
                (highlights); does NOT advance the phase.
              – Click on already-selected piece → deselect.
              – Click on empty / opponent square while a piece IS selected →
                deselect (nothing to do in PREP).
              – Click on empty square while NO piece is selected → fire
                EndPreparation and advance to CHESS.

        Board clicks are ignored when the active player is controlled by AI.
        """
        obs = self._current_obs()

        if obs.phase not in (Phase.CHESS, Phase.PREPARATION):
            return

        active = obs.active_player

        if self._player_modes.get(active) == "ai":
            return

        # ── CHESS phase: two-click move / castle / reposition ─────────────
        if obs.phase == Phase.CHESS:
            # REPOSITION pending decision (blade_dancer after-capture effect):
            # the human must click a highlighted square to teleport their piece.
            if obs.pending_decision_type == "reposition":
                # Auto-select the piece that must be repositioned if not yet selected.
                if self._selected_pos is None:
                    legal = self._game.get_legal_actions(active)
                    reposition_actions = [
                        a for a in legal if isinstance(a, RepositionUnit)
                    ]
                    if reposition_actions:
                        # All RepositionUnit actions share the same from_position.
                        self._selected_pos = reposition_actions[0].from_position
                        self._legal_dests = [a.to_position for a in reposition_actions]
                        self._castle_dests = []
                    return  # re-render to show highlights; wait for 2nd click
                # Second click: execute the reposition if target is valid.
                if pos in self._legal_dests:
                    action = RepositionUnit(
                        player_id=active,
                        from_position=self._selected_pos,
                        to_position=pos,
                    )
                    self._execute_and_advance(action, active)
                return

            if self._selected_pos is None:
                owned = self._unit_at(pos, active)
                if owned:
                    self._select(pos)
            else:
                if pos == self._selected_pos:
                    self._deselect()
                elif pos in self._castle_dests:
                    self._do_castle(pos, active)
                elif pos in self._siege_dests:
                    self._do_siege(self._selected_pos, pos, active)
                elif pos in self._legal_dests:
                    self._do_move(self._selected_pos, pos, active)
                else:
                    owned = self._unit_at(pos, active)
                    if owned:
                        self._select(pos)
                    else:
                        self._deselect()

        # ── PREPARATION phase ─────────────────────────────────────────────
        elif obs.phase == Phase.PREPARATION:
            if self._king_sacrifice_positions:
                # Stage 10: Succession cost — click a highlighted piece to
                # sacrifice (paired with a Building cost already chosen, if
                # this is the 2nd Succession's AND cost).
                if pos in self._king_sacrifice_positions:
                    self._do_king_sacrifice(pos, active)
                else:
                    self._cancel_king_mode()
                    self._show_toast("Succession cancelled.")
            elif self._build_card_id is not None:
                # Stage 8: Build mode — click on a highlighted builder Pawn
                # → fire StartConstruction.
                if pos in self._build_positions:
                    self._do_build_construction(self._build_card_id, pos, active)
                else:
                    self._cancel_build_mode()
                    self._show_toast("Construction cancelled.")
            elif self._summon_card_id is not None:
                # Summon mode: click on a valid vessel → fire SummonMonster
                if pos in self._summon_vessel_positions:
                    self._do_summon(self._summon_card_id, pos, active)
                else:
                    # Clicked elsewhere — cancel summon mode
                    self._cancel_summon()
                    self._show_toast("Summon cancelled.")
            elif self._targeting_card_id is not None:
                # Stage 6: Trap-placement / Spell-area targeting mode —
                # click a highlighted square → fire the matching action.
                action = self._targeting_action_by_pos.get(pos)
                self._cancel_targeting_mode()
                if action is not None:
                    self._execute_and_advance(action, active)
                else:
                    self._show_toast("Targeting cancelled.")
            elif self._spell_piece_card_id is not None:
                # Stage 6: piece-targeting Spell mode. Most piece Spells are
                # two-step (pick source, then destination), but the ones whose
                # destination is implied by the effect rather than chosen —
                # forced_march (move_unit) and magnetic_reversal (pull_unit),
                # whose actions carry only "position" — are ONE-step: clicking
                # the piece is the whole flow.
                if self._spell_piece_source is None:
                    valid_sources = {
                        tuple(a.target["position"]) for a in self._spell_piece_actions
                    }
                    if (pos.file, pos.rank) in valid_sources:
                        one_step = next(
                            (a for a in self._spell_piece_actions
                             if tuple(a.target["position"]) == (pos.file, pos.rank)
                             and "destination" not in a.target),
                            None,
                        )
                        if one_step is not None:
                            self._cancel_spell_piece_mode()
                            self._execute_and_advance(one_step, active)
                            return
                        self._spell_piece_source = pos
                    else:
                        self._cancel_spell_piece_mode()
                        self._show_toast("Targeting cancelled.")
                else:
                    src_tuple = (self._spell_piece_source.file, self._spell_piece_source.rank)
                    if pos == self._spell_piece_source:
                        self._spell_piece_source = None  # deselect, stay in mode
                        return
                    match = next(
                        (a for a in self._spell_piece_actions
                         if tuple(a.target["position"]) == src_tuple
                         and "destination" in a.target
                         and tuple(a.target["destination"]) == (pos.file, pos.rank)),
                        None,
                    )
                    self._cancel_spell_piece_mode()
                    if match is not None:
                        self._execute_and_advance(match, active)
                    else:
                        self._show_toast("Targeting cancelled.")
            else:
                # No summon mode: two-click model.
                # • Piece click → select/deselect (preview moves, stay in PREP).
                # • Empty-square click with nothing selected → end preparation.
                if self._selected_pos is not None:
                    # Something already selected — deselect on any non-vessel click.
                    self._deselect()
                else:
                    owned = self._unit_at(pos, active)
                    if owned:
                        # First click on own piece: select and show legal move previews.
                        self._select(pos)
                    else:
                        # Empty / opponent square with nothing selected → done with prep.
                        self._execute_and_advance(EndPreparation(player_id=active), active)

    # ── Stage 5: Summon / Dismiss helpers ────────────────────────────────

    def _handle_hand_click_preparation(self, card_id: str, obs) -> None:
        """
        Called when the human clicks a card during PREPARATION phase.

        • If the card is a MONSTER and not already selected: enter summon mode
          — compute valid vessel squares via legal actions and highlight them.
        • If the same monster card is already selected: cancel summon mode.
        • TRAP cards: enter Trap-placement targeting mode (Stage 6).
        • SPELL cards: dispatched by target_type (Stage 6) — see
          _enter_spell_mode().
        """
        from game.cards.card import MonsterCard, SpellCard, TrapCard

        # Toggle: clicking the already-selected card cancels its mode.
        if self._summon_card_id == card_id or self._targeting_card_id == card_id \
                or self._spell_piece_card_id == card_id:
            self._cancel_summon()
            self._cancel_targeting_mode()
            self._cancel_spell_piece_mode()
            return

        # Stage 8: a hand card click always supersedes build mode (Buildings
        # aren't hand cards, so there's no matching "same card" toggle here).
        self._cancel_build_mode()
        # Stage 10: likewise for any in-progress King Succession flow.
        self._cancel_king_mode()
        # Stage 11: and any in-progress Ritual picker.
        self._cancel_ritual_mode()

        # Look up the card in the registry
        if card_id not in self._registry:
            self._show_toast(f"Unknown card: {card_id}")
            return

        card = self._registry.get(card_id)

        if isinstance(card, TrapCard):
            legal = self._game.get_legal_actions(obs.active_player)
            matches = [
                (a.position, a) for a in legal
                if isinstance(a, PlaceTrap) and a.card_id == card_id
            ]
            self._enter_targeting_mode(card_id, card.name, matches, kind="trap")
            return

        if isinstance(card, SpellCard):
            self._enter_spell_mode(card_id, card, obs)
            return

        if not isinstance(card, MonsterCard):
            self._show_toast(f"{getattr(card, 'name', card_id)}: no UI support yet.")
            return

        # Enter summon mode: enumerate valid vessel positions from legal actions
        legal = self._game.get_legal_actions(obs.active_player)
        vessel_positions = [
            a.vessel_position
            for a in legal
            if isinstance(a, SummonMonster) and a.card_id == card_id
        ]

        if not vessel_positions:
            self._show_toast(f"{card.name}: no valid vessels on board.")
            return

        self._summon_card_id = card_id
        self._summon_vessel_positions = vessel_positions
        self._show_toast(f"Summoning {card.name} — click a gold vessel on the board  (ESC to cancel)")

    def _do_summon(self, card_id: str, vessel_pos: Position, player_id: str) -> None:
        """Execute SummonMonster and exit summon mode."""
        _log.info("SUMMON   player=%s  card=%r  vessel=%s", player_id, card_id, vessel_pos)
        self._cancel_summon()  # clear summon state first
        action = SummonMonster(player_id=player_id, card_id=card_id, vessel_position=vessel_pos)
        self._execute_and_advance(action, player_id)

    def _cancel_summon(self) -> None:
        """Exit summon mode without firing any action."""
        self._summon_card_id = None
        self._summon_vessel_positions = []

    def _cancel_targeting_mode(self) -> None:
        """Exit Trap-placement / Spell-area-targeting mode without firing."""
        self._targeting_card_id = None
        self._targeting_kind = None
        self._targeting_action_by_pos = {}

    def _cancel_spell_piece_mode(self) -> None:
        """Exit the two-step piece-targeting Spell mode without firing."""
        self._spell_piece_card_id = None
        self._spell_piece_actions = []
        self._spell_piece_source = None

    # ── Terrain scars ─────────────────────────────────────────────────────
    #
    # "After the effect of a Spell, Trap or Monster is done, the ground it
    # touched turns to Cracked Earth."  The engine has no concept of this —
    # it's a purely visual memory of the battlefield — so the UI keeps its
    # own record, built from two cheap sources:
    #
    #   • diffing consecutive Observations  (a square effect that stopped
    #     being listed has expired; a Monster piece_id that vanished from
    #     the board is gone for good)
    #   • scanning new events for TrapTriggered, which fires even when the
    #     Trap survives with charges left and so can't be spotted by diffing
    #
    # Scars fade instead of accumulating forever: on a dark square Cracked
    # Earth *is* the default ground, so a board eventually scarred edge to
    # edge would quietly erase its own checker pattern.
    _SCAR_LIFETIME_TURNS: int = 8   # turns a scar stays visible at all
    _SCAR_FADE_TURNS: int = 4       # of those, how many it spends fading out

    def _reset_terrain_scars(self) -> None:
        """Clear all scar bookkeeping — on startup and after loading a save."""
        self._scars: dict[Position, int] = {}          # square → turn scarred
        self._prev_effect_squares: set[Position] = set()
        self._prev_monster_squares: dict[str, Position] = {}
        self._trap_footprints: dict[str, tuple[Position, ...]] = {}
        # Start from the log's current end, not from zero: scars record what
        # has happened since this board appeared on screen.  Replaying a
        # loaded save's whole history would stamp every Trap the previous
        # session ever sprung onto the board at once, all dated to the turn
        # the file was opened.
        self._scar_events_seen: int = len(self._game.state.event_log)

    def _update_terrain_scars(self, obs) -> None:
        """Fold this frame's board state into the scar record."""
        from game.core.events import TrapPlaced, TrapTriggered
        from game.mechanics.area import expand_area

        turn = obs.turn_number

        # 1. Square effects that have run their duration out.
        effect_squares = {e.position for e in obs.board.square_effects}
        for pos in self._prev_effect_squares - effect_squares:
            self._scars[pos] = turn
        self._prev_effect_squares = effect_squares

        # 2. Traps that fired.  TrapTriggered carries only the instance id, and
        #    a single-charge Trap is already off the board by the time we look,
        #    so the footprint captured on previous frames is what we scar.
        #    TrapPlaced is folded into the same pass so a Trap that is planted
        #    AND sprung between two renders (several AI actions resolve inside
        #    one frame) still has a footprint to scar by the time we reach its
        #    TrapTriggered — events arrive in order, so the placement is always
        #    seen first.
        log = self._game.state.event_log
        # A loaded save swaps the whole log out from under us.
        if self._scar_events_seen > len(log):
            self._scar_events_seen = 0
        for ev in log[self._scar_events_seen:]:
            if isinstance(ev, TrapPlaced):
                self._trap_footprints.setdefault(
                    ev.trap_instance_id,
                    tuple(expand_area(ev.position, ev.radius)),
                )
            elif isinstance(ev, TrapTriggered):
                for pos in self._trap_footprints.get(ev.trap_instance_id, ()):
                    self._scars[pos] = turn
        self._scar_events_seen = len(log)

        # 3. Traps that left the board (spent their last charge, disarmed).
        traps_now = {t.id: t for t in obs.board.trap_locations}
        for trap_id, footprint in list(self._trap_footprints.items()):
            if trap_id not in traps_now:
                for pos in footprint:
                    self._scars[pos] = turn
                del self._trap_footprints[trap_id]
        for trap_id, trap in traps_now.items():
            self._trap_footprints[trap_id] = tuple(
                expand_area(trap.position, trap.radius, trap.shape)
            )

        # 4. Monsters that left the board (destroyed, dismissed, expired).
        #    Keyed by piece_id so a Monster merely *moving* doesn't scar.
        monsters_now = {
            u.piece_id: u.position
            for u in obs.board.units
            if u.monster_id is not None
        }
        for piece_id, pos in self._prev_monster_squares.items():
            if piece_id not in monsters_now:
                self._scars[pos] = turn
        self._prev_monster_squares = monsters_now

        # 5. Retire scars that have aged out.
        cutoff = turn - self._SCAR_LIFETIME_TURNS
        for pos, made in list(self._scars.items()):
            if made <= cutoff:
                del self._scars[pos]

    def _scar_map(self, obs) -> "dict[Position, float]":
        """Square → 0..1 scar intensity, full strength when fresh."""
        turn = obs.turn_number
        solid = self._SCAR_LIFETIME_TURNS - self._SCAR_FADE_TURNS
        out: dict[Position, float] = {}
        for pos, made in self._scars.items():
            age = turn - made
            if age <= solid:
                out[pos] = 1.0
            else:
                remaining = self._SCAR_LIFETIME_TURNS - age
                out[pos] = max(0.0, remaining / self._SCAR_FADE_TURNS)
        return out

    def _compute_aura_colors(self, obs) -> "dict[Position, tuple[int, int, int]]":
        """
        Stage 6 — Position → RGB aura color for every summoned piece on the
        board, keyed by its Monster's archetype (see ui/archetype_colors.py;
        colors live in data/archetype_colors.yaml, unassigned archetypes
        fall back to brown).  board_view.py only draws whatever it's given —
        this is the one place that looks archetype up via the card registry.
        """
        colors: dict[Position, tuple[int, int, int]] = {}
        for unit_info in obs.board.units:
            if unit_info.monster_id is None:
                continue
            archetype = None
            if self._registry is not None and unit_info.monster_id in self._registry:
                try:
                    card = self._registry.get(unit_info.monster_id)
                    archetype = getattr(card, "archetype", None)
                except Exception:
                    archetype = None
            colors[unit_info.position] = aura_color_for(archetype)
        return colors

    def _compute_archetype_map(self, obs) -> "dict[Position, str]":
        """
        Position → archetype string for every summoned piece on the board,
        plus every Crowned King piece (uses archetype_support[0] from the
        active KingCard so the faction king sprite is rendered).
        Used by BoardView to choose the faction sprite instead of the neutral
        chess piece.
        """
        result: dict[Position, str] = {}
        for unit_info in obs.board.units:
            # Summoned monsters
            if unit_info.monster_id is not None:
                archetype = None
                if self._registry is not None and unit_info.monster_id in self._registry:
                    try:
                        card = self._registry.get(unit_info.monster_id)
                        archetype = getattr(card, "archetype", None)
                    except Exception:
                        archetype = None
                if archetype:
                    result[unit_info.position] = archetype

            # Crowned Kings — use the primary archetype_support of the active KingCard
            elif unit_info.piece_type == "king":
                try:
                    active_king_id = self._game.state.get_player(unit_info.owner).active_king
                    if active_king_id and self._registry is not None and active_king_id in self._registry:
                        king_card = self._registry.get(active_king_id)
                        support = getattr(king_card, "archetype_support", ())
                        if support:
                            result[unit_info.position] = support[0]
                except Exception:
                    pass

        return result

    def _compute_crowned_kings(self, obs) -> "dict[Position, str]":
        """
        Stage 10 — Position → active king_card_id for every King currently
        Coronated (README §17). The active King is public information the
        moment Coronation happens, so this reads directly from GameState
        (like _do_king's other direct reads) rather than needing a
        King-specific Observation field.
        """
        crowned: dict[Position, str] = {}
        for unit_info in obs.board.units:
            if unit_info.piece_type != "king":
                continue
            active_king_id = self._game.state.get_player(unit_info.owner).active_king
            if active_king_id is not None:
                crowned[unit_info.position] = active_king_id
        return crowned

    def _compute_playable_card_ids(self, obs) -> set[str]:
        """
        Stage 6 — card_ids in ``obs.active_player``'s hand that have at
        least one legal action right now (SummonMonster / PlaceTrap /
        ActivateSpell all carry ``card_id``).  Used to grey out hand cards
        that currently have no valid vessel/square/target.
        """
        legal = self._game.get_legal_actions(obs.active_player)
        return {
            a.card_id for a in legal
            if getattr(a, "card_id", None) is not None
        }

    def _compute_activatable_entries(self, obs) -> list[tuple[str, str]]:
        """
        Stage 6 — (label, token) pairs for every ActivateTrap /
        ActivateMonsterAbility legal right now, across the WHOLE field —
        not gated on the player having inspected that specific piece/Trap
        first.  Consumed by SidebarOverlay's "⚡ Activatable" section and
        routed back in _handle_click via the token.
        """
        from game.core.actions import ActivateMonsterAbility, ActivateTrap

        entries: list[tuple[str, str]] = []
        seen: set[str] = set()
        for a in self._game.get_legal_actions(obs.active_player):
            if isinstance(a, ActivateTrap):
                token = f"trap:{a.trap_instance_id}"
                if token in seen:
                    continue
                seen.add(token)
                trap = next((t for t in obs.board.trap_locations if t.id == a.trap_instance_id), None)
                name = trap.card_id.replace("_", " ").title() if trap else a.trap_instance_id
                if trap is not None:
                    try:
                        name = self._registry.get(trap.card_id).name
                    except Exception:
                        pass
                entries.append((f"⚡ {name} (Trap)", token))

            elif isinstance(a, ActivateMonsterAbility):
                # Abilities that need a specific target (challenge_unit,
                # dismiss_monster, disable_building,
                # ritual_requirement_reduction) get one entry PER target
                # rather than collapsing to a single dead one — the target
                # is round-tripped through the token so _handle_click can
                # reconstruct the exact action.
                if isinstance(a.target, tuple) and len(a.target) == 2:
                    target_part = f"pos:{a.target[0]}:{a.target[1]}"
                    target_label = str(Position(a.target[0], a.target[1]))
                elif isinstance(a.target, str):
                    target_part = f"id:{a.target}"
                    target_label = a.target
                else:
                    target_part = ""
                    target_label = ""
                token = f"ability_at:{a.unit_position.file}:{a.unit_position.rank}:{a.ability_id}:{target_part}"
                if token in seen:
                    continue
                seen.add(token)
                unit_info = next(
                    (u for u in obs.board.units if u.position == a.unit_position), None
                )
                piece_label = unit_info.piece_id if unit_info else str(a.unit_position)
                label = f"⚡ {piece_label} — {a.ability_id.replace('_', ' ')}"
                if target_label:
                    label += f" → {target_label}"
                entries.append((label, token))

        return entries

    def _trap_position(self, trap_instance_id: str, obs) -> Position | None:
        """Look up a placed Trap's board position from its instance id."""
        for trap in obs.board.trap_locations:
            if trap.id == trap_instance_id:
                return trap.position
        return None

    # ── Stage 6: hover tooltips (Trap / active zone info) ──────────────────

    _ZONE_DESCRIPTIONS: dict[str, str] = {
        "frozen":   "Frozen — no piece may enter this square.",
        "scorched": "Scorched — destroys the next enemy that enters.",
        "blocked":  "Blocked — no piece may enter this square.",
        "cursed":   "Cursed — immobilizes the next piece that lands here.",
    }

    def _hover_tooltip_lines(self, hover_pos: Position, obs) -> list[tuple[str, tuple]]:
        """
        Build tooltip lines for the square under the mouse, in priority order:
        exact Trap square > Trap activation area > active zone effect.

        Identity/details are only shown for the viewer's OWN Traps/zones
        (``owner == obs.player_id``) — an enemy Trap or zone shows only that
        it exists, never its name, trigger, radius, charges, or effect type.
        Returns [] if there's nothing to show.
        """
        from game.core.phases import ConstructionStatus
        from game.mechanics.area import expand_area

        # Stage 13 — Buildings are public information (README §12: "The
        # Building Pool is public"), so unlike Traps there's nothing to
        # redact for the enemy's: integrity, siege marks and protection are
        # all things both players can see on the board anyway.
        for b in obs.board.building_locations:
            if b.position != hover_pos or getattr(b, "status", None) == ConstructionStatus.DESTROYED:
                continue
            lines = []
            try:
                lines.append((self._registry.get(b.building_card_id).name, TOOLTIP_TITLE))
            except Exception:
                lines.append((b.building_card_id.replace("_", " ").title(), TOOLTIP_TITLE))
            side = "yours" if b.owner == obs.player_id else "enemy"
            lines.append((f"Owner: {b.owner} ({side})", TOOLTIP_TEXT))
            if b.status == ConstructionStatus.UNDER_CONSTRUCTION:
                lines.append((f"Under construction: {b.remaining_turns} turn(s) left", TOOLTIP_TEXT))
            else:
                lines.append((f"Integrity: {b.integrity}/{b.max_integrity}", TOOLTIP_TEXT))
                if b.disabled_turns > 0:
                    lines.append((f"Disabled for {b.disabled_turns} turn(s)", TOOLTIP_TEXT))
                if b.vulnerable_turns > 0:
                    lines.append((
                        f"Marked for destruction — open to any attacker, "
                        f"+{b.vulnerable_amount} damage ({b.vulnerable_turns} turn(s))",
                        TOOLTIP_TEXT,
                    ))
                if b.protection_turns > 0 and b.protection_uses > 0:
                    lines.append((
                        f"Fortified: absorbs {b.protection_uses} destroying blow(s)",
                        TOOLTIP_TEXT,
                    ))
            return lines

        for trap in obs.board.trap_locations:
            if trap.position == hover_pos:
                if trap.owner != obs.player_id:
                    return [("Enemy Trap", TOOLTIP_TITLE)]
                lines: list[tuple[str, tuple]] = []
                try:
                    card = self._registry.get(trap.card_id)
                    lines.append((card.name, TOOLTIP_TITLE))
                except Exception:
                    lines.append((trap.card_id.replace("_", " ").title(), TOOLTIP_TITLE))
                lines.append((f"Owner: {trap.owner}", TOOLTIP_TEXT))
                lines.append((f"Trigger: {trap.trigger_condition}", TOOLTIP_TEXT))
                lines.append((f"Radius: {trap.radius} ({trap.shape})", TOOLTIP_TEXT))
                charges_txt = "unlimited" if trap.charges is None else str(trap.charges)
                lines.append((f"Charges left: {charges_txt}", TOOLTIP_TEXT))
                return lines

        for trap in obs.board.trap_locations:
            area = expand_area(trap.position, trap.radius, trap.shape)
            if hover_pos in area:
                if trap.owner != obs.player_id:
                    return [("Enemy Trap's activation area", TOOLTIP_TITLE)]
                name = trap.card_id.replace("_", " ").title()
                try:
                    name = self._registry.get(trap.card_id).name
                except Exception:
                    pass
                return [
                    (f"{name}'s activation area", TOOLTIP_TITLE),
                    (f"Owner: {trap.owner}  ·  triggers on {trap.trigger_condition}", TOOLTIP_TEXT),
                ]

        for eff in obs.board.square_effects:
            if eff.position == hover_pos:
                if eff.owner != obs.player_id:
                    return [("Enemy zone", TOOLTIP_TITLE)]
                desc = self._ZONE_DESCRIPTIONS.get(eff.effect_type, "")
                lines = [(f"{eff.effect_type.title()} zone", TOOLTIP_TITLE)]
                if desc:
                    lines.append((desc, TOOLTIP_TEXT))
                lines.append((f"Set by you  ·  {eff.duration_turns} turn(s) left", TOOLTIP_TEXT))
                return lines

        # Stage 8+: building hover — show name + status + key effects.
        # Buildings are always visible (public information), but effect details
        # are only meaningful from the owning player's perspective.
        for b in getattr(obs.board, "building_locations", ()):
            if b.position != hover_pos:
                continue
            from game.core.phases import ConstructionStatus
            if getattr(b, "status", None) == ConstructionStatus.DESTROYED:
                continue
            # Building name from registry if available
            bname = b.building_card_id.replace("_", " ").title()
            try:
                bname = self._registry.get(b.building_card_id).name
            except Exception:
                pass
            lines = [(bname, TOOLTIP_TITLE)]
            if b.status == ConstructionStatus.UNDER_CONSTRUCTION:
                remaining = getattr(b, "remaining_turns", "?")
                total = None
                try:
                    bcard = self._registry.get(b.building_card_id)
                    total = getattr(bcard, "construction_turns", None)
                except Exception:
                    pass
                if total is not None and isinstance(remaining, int):
                    done = total - remaining
                    progress = f"{done}/{total} turns complete"
                else:
                    progress = f"{remaining} turn(s) remaining"
                lines.append((f"Under Construction — {progress}", TOOLTIP_TEXT))
            else:
                owner_label = "Yours" if b.owner == obs.player_id else "Enemy"
                lines.append((f"Status: Complete  ·  {owner_label}", TOOLTIP_TEXT))
                # Show effect types from card if available
                try:
                    card = self._registry.get(b.building_card_id)
                    for eff in getattr(card, "effects", ()):
                        etype = getattr(eff, "type", "").replace("_", " ").title()
                        if etype:
                            lines.append((f"  {etype}", TOOLTIP_TEXT))
                except Exception:
                    pass
            return lines

        return []

    def _enter_targeting_mode(
        self,
        card_id: str,
        card_name: str,
        matches: list[tuple[Position, object]],
        kind: str = "spell",
    ) -> None:
        """
        Stage 6 — enter single-click targeting mode for a Trap or Spell.

        ``matches`` pairs every clickable square with the concrete Action
        that clicking it should fire (PlaceTrap or ActivateSpell).
        ``kind`` is "trap" or "spell" and only affects presentation: the
        board previews Trap squares as Void Rift and Spell squares as Arcane
        Circle, so the two modes never look alike.
        """
        if not matches:
            self._show_toast(f"{card_name}: no valid targets right now.")
            return
        self._cancel_summon()
        self._cancel_spell_piece_mode()
        self._targeting_card_id = card_id
        self._targeting_kind = kind
        self._targeting_action_by_pos = dict(matches)
        self._show_toast(f"{card_name} — click a highlighted square  (ESC to cancel)")

    def _enter_spell_mode(self, card_id: str, card, obs) -> None:
        """Stage 6 — dispatch card-click handling for a Spell by target_type."""
        active = obs.active_player
        if card.target_type == "none":
            action = ActivateSpell(player_id=active, card_id=card_id, target=None)
            self._execute_and_advance(action, active)
            return

        legal = self._game.get_legal_actions(active)
        spell_actions = [
            a for a in legal if isinstance(a, ActivateSpell) and a.card_id == card_id
        ]
        if not spell_actions:
            self._show_toast(f"{card.name}: no valid targets right now.")
            return

        if card.target_type == "trap":
            matches = []
            for a in spell_actions:
                pos = self._trap_position(a.target, obs)
                if pos is not None:
                    matches.append((pos, a))
            self._enter_targeting_mode(card_id, card.name, matches)

        elif card.target_type in ("position", "zone"):
            matches = [(Position(*a.target), a) for a in spell_actions]
            self._enter_targeting_mode(card_id, card.name, matches)

        elif card.target_type == "piece":
            self._cancel_summon()
            self._cancel_targeting_mode()
            self._spell_piece_card_id = card_id
            self._spell_piece_actions = spell_actions
            self._spell_piece_source = None
            # One-step Spells (no "destination" in their targets) aim at a
            # piece, not a move — and pull_unit aims at an ENEMY one.
            if any("destination" not in a.target for a in spell_actions):
                self._show_toast(f"{card.name} — click the target piece  (ESC to cancel)")
            else:
                self._show_toast(f"{card.name} — click your piece to move  (ESC to cancel)")

        else:
            self._show_toast(f"{card.name}: target type {card.target_type!r} not supported yet.")

    def _do_recompose(self) -> None:
        """
        Sidebar button: declare Recompose for the active human player.
        Only valid during PREPARATION when the preparation action hasn't been used.
        After DeclareRecompose the engine moves to RECOMPOSE_SELECTION; _auto_advance
        stops there so the human can interactively pick which cards to return.
        """
        obs = self._current_obs()
        if obs.phase != Phase.PREPARATION:
            return
        active = obs.active_player
        if self._player_modes.get(active) == "ai":
            return
        self._recompose_selected = []
        self._execute_and_advance(DeclareRecompose(player_id=active), active)

    def _handle_recompose_card_click(self, card_id: str, obs: "object") -> None:
        """
        Toggle a card in/out of the recompose selection.

        When the player has selected exactly N cards (pending_decision_min),
        SelectRecomposeCards is submitted automatically.
        """
        n = obs.pending_decision_min

        if card_id in self._recompose_selected:
            self._recompose_selected.remove(card_id)
        else:
            if len(self._recompose_selected) < n:
                self._recompose_selected.append(card_id)

        if len(self._recompose_selected) == n:
            active = obs.active_player
            action = SelectRecomposeCards(
                player_id=active,
                card_ids=list(self._recompose_selected),
            )
            self._recompose_selected = []
            self._execute_and_advance(action, active)

    def _do_mercenary(self) -> None:
        """
        Sidebar button: open the Mercenary piece-type picker for the active human player.
        Only valid during PREPARATION when the preparation action hasn't been used.
        Shows a small on-board picker so the player selects which piece type to buy.
        """
        obs = self._current_obs()
        if obs.phase != Phase.PREPARATION:
            return
        active = obs.active_player
        if self._player_modes.get(active) == "ai":
            return
        self._cancel_king_mode()
        self._cancel_ritual_mode()
        # Show the mercenary picker dialog — it will call _commit_mercenary() on selection.
        self._mercenary_selected = []
        self._mercenary_picker = _MercenaryPicker(
            surface=self._screen,
            font=self._font_small,
            player_id=active,
            registry=self._registry,
            hand=list(self._game.state.get_player(active).hand),
        )

    def _commit_mercenary(self, piece_type: str) -> None:
        """Called by the MercenaryPicker when the human picks a piece type."""
        obs = self._current_obs()
        active = obs.active_player
        self._mercenary_picker = None
        self._mercenary_selected = []
        try:
            self._execute_and_advance(
                DeclareMercenary(player_id=active, piece_type=piece_type),
                active,
            )
        except Exception as exc:
            self._show_toast(f"Mercenary: {exc}")

    def _do_build(self) -> None:
        """
        Sidebar button: open the Building Pool picker for the active human
        player.  Only valid during PREPARATION when the preparation action
        hasn't been used.  Shows a small on-board picker listing the
        player's public Building Pool entries (README §12); selecting one
        enters build mode (highlighted builder-Pawn squares on the board).
        """
        obs = self._current_obs()
        if obs.phase != Phase.PREPARATION:
            return
        active = obs.active_player
        if self._player_modes.get(active) == "human":
            self._cancel_king_mode()
            self._cancel_ritual_mode()
            self._build_picker = _BuildPicker(
                surface=self._screen,
                font=self._font_small,
                registry=self._registry,
                pool=list(self._game.state.get_player(active).building_pool),
            )

    def _commit_build(self, building_card_id: str) -> None:
        """Called by the BuildPicker when the human picks a Building. Enters build mode."""
        self._build_picker = None
        obs = self._current_obs()
        legal = self._game.get_legal_actions(obs.active_player)
        positions = [
            a.pawn_position for a in legal
            if isinstance(a, StartConstruction) and a.building_card_id == building_card_id
        ]
        if not positions:
            self._show_toast("No eligible builder Pawn for that Building.")
            return
        self._build_card_id = building_card_id
        self._build_positions = positions
        name = building_card_id
        if self._registry is not None and building_card_id in self._registry:
            name = self._registry.get(building_card_id).name
        self._show_toast(f"Building {name} — click a highlighted Pawn  (ESC to cancel)")

    def _do_build_construction(self, building_card_id: str, pawn_pos: Position, player_id: str) -> None:
        """Execute StartConstruction and exit build mode."""
        self._cancel_build_mode()
        action = StartConstruction(
            player_id=player_id, pawn_position=pawn_pos, building_card_id=building_card_id,
        )
        self._execute_and_advance(action, player_id)

    def _cancel_build_mode(self) -> None:
        """Exit build mode without firing any action."""
        self._build_card_id = None
        self._build_positions = []

    # ── Stage 10: King (Coronation / Succession) ─────────────────────────

    def _king_card_name(self, king_card_id: str) -> str:
        if self._registry is not None and king_card_id in self._registry:
            return self._registry.get(king_card_id).name
        return king_card_id

    def _do_king(self) -> None:
        """
        Sidebar button: open the King picker.

        Shows Coronation targets (no active King yet) OR Succession targets
        (an active King exists and a HIDDEN King remains) — whichever the
        engine's legal actions currently offer. Only valid during
        PREPARATION, before the preparation action is used.
        """
        obs = self._current_obs()
        if obs.phase != Phase.PREPARATION:
            return
        active = obs.active_player
        if self._player_modes.get(active) == "ai":
            return
        self._cancel_king_mode()
        self._cancel_ritual_mode()
        self._cancel_build_mode()
        self._cancel_summon()
        self._cancel_targeting_mode()
        self._cancel_spell_piece_mode()

        legal = self._game.get_legal_actions(active)
        coronate_actions = [a for a in legal if isinstance(a, CoronateKing)]
        change_actions = [a for a in legal if isinstance(a, ChangeKing)]

        if coronate_actions:
            seen: list[str] = []
            for a in coronate_actions:
                if a.king_card_id not in seen:
                    seen.append(a.king_card_id)
            rows = [(f"👑  Crown: {self._king_card_name(kid)}", kid) for kid in seen]
            self._king_picker = _KingDialog(
                self._screen, self._font_small, "👑  Coronation", rows,
            )
            self._king_picker_mode = "coronate"
        elif change_actions:
            seen = []
            for a in change_actions:
                if a.king_card_id not in seen:
                    seen.append(a.king_card_id)
            rows = [(f"⚔  Succeed: {self._king_card_name(kid)}", kid) for kid in seen]
            self._king_picker = _KingDialog(
                self._screen, self._font_small, "⚔  Succession", rows,
            )
            self._king_picker_mode = "succeed"
            self._king_picker_actions = change_actions
        else:
            self._show_toast("No King action available right now.")

    def _on_king_picker_choice(self, key: str) -> None:
        """Route a completed _king_picker click by the flow's current step."""
        mode = self._king_picker_mode
        self._king_picker = None

        obs = self._current_obs()
        active = obs.active_player

        if mode == "coronate":
            self._execute_and_advance(
                CoronateKing(player_id=active, king_card_id=key), active,
            )
            self._cancel_king_mode()
            return

        if mode == "succeed":
            self._begin_king_succession_cost(key)
            return

        if mode == "cost_choice":
            self._on_king_cost_choice(key)
            return

        if mode == "building_choice":
            self._on_king_building_choice(key)
            return

    def _begin_king_succession_cost(self, king_card_id: str) -> None:
        """
        A Succession target King was chosen — figure out the cost shape
        (README §19.1, see ChangeKing) and open whichever picker comes next.

        1st Succession — OR cost: only pure-piece and/or pure-Building
            ChangeKing actions exist for this King. Offer a choice between
            the two.
        2nd Succession — AND cost: every ChangeKing action for this King
            carries both a sacrifice_position and a destroy_building_id.
            Pick the Building first, then the piece.
        """
        actions = [a for a in self._king_picker_actions if a.king_card_id == king_card_id]
        self._king_succession_target = king_card_id

        pure_piece    = [a for a in actions if a.destroy_building_id is None and a.sacrifice_position is not None]
        pure_building = [a for a in actions if a.sacrifice_position is None and a.destroy_building_id is not None]
        combined      = [a for a in actions if a.sacrifice_position is not None and a.destroy_building_id is not None]

        if combined:
            self._king_and_mode = True
            self._open_king_building_picker(combined)
            return

        self._king_and_mode = False
        self._king_picker_actions = actions
        rows: list[tuple[str, "Any"]] = []
        if pure_piece:
            rows.append(("🗡  Sacrifice a Piece", "piece"))
        if pure_building:
            rows.append(("🏛  Destroy a Building", "building"))
        if not rows:
            self._show_toast("No Succession cost available.")
            self._cancel_king_mode()
            return
        self._king_picker = _KingDialog(
            self._screen, self._font_small, "Succession Cost", rows,
        )
        self._king_picker_mode = "cost_choice"

    def _on_king_cost_choice(self, choice: str) -> None:
        """1st Succession only: the player picked which single cost to pay."""
        self._king_picker = None
        actions = self._king_picker_actions
        if choice == "piece":
            positions = list({
                a.sacrifice_position for a in actions
                if a.destroy_building_id is None and a.sacrifice_position is not None
            })
            self._king_sacrifice_positions = positions
            self._king_sacrifice_building_id = None
            self._show_toast("Succession — click a piece to sacrifice  (ESC to cancel)")
        elif choice == "building":
            pure_building = [
                a for a in actions
                if a.sacrifice_position is None and a.destroy_building_id is not None
            ]
            self._open_king_building_picker(pure_building)

    def _building_display_name(self, building_id: str) -> str:
        bld = next((b for b in self._game.state.buildings if b.id == building_id), None)
        if bld is None:
            return building_id
        if self._registry is not None and bld.building_card_id in self._registry:
            return self._registry.get(bld.building_card_id).name
        return bld.building_card_id

    def _open_king_building_picker(self, actions: list) -> None:
        """
        Open a picker listing the distinct Buildings named across ``actions``.

        Used both for the 1st Succession's "Destroy a Building" cost choice
        (picking a Building commits the Succession directly) and the 2nd
        Succession's AND cost (picking a Building here is only step one —
        the piece-sacrifice board-click mode follows).
        """
        self._king_picker_actions = actions
        seen: list[str] = []
        for a in actions:
            if a.destroy_building_id not in seen:
                seen.append(a.destroy_building_id)
        rows = [(f"🏛  {self._building_display_name(bid)}", bid) for bid in seen]
        self._king_picker = _KingDialog(
            self._screen, self._font_small, "Choose a Building", rows,
        )
        self._king_picker_mode = "building_choice"

    def _on_king_building_choice(self, building_id: str) -> None:
        self._king_picker = None
        actions = [a for a in self._king_picker_actions if a.destroy_building_id == building_id]

        if self._king_and_mode:
            # 2nd Succession: Building chosen — now pick the piece.
            positions = list({
                a.sacrifice_position for a in actions if a.sacrifice_position is not None
            })
            self._king_sacrifice_positions = positions
            self._king_sacrifice_building_id = building_id
            self._show_toast("Succession — click a piece to sacrifice  (ESC to cancel)")
        else:
            # 1st Succession, Building-only cost — commits directly.
            obs = self._current_obs()
            active = obs.active_player
            self._execute_and_advance(ChangeKing(
                player_id=active, king_card_id=self._king_succession_target,
                destroy_building_id=building_id,
            ), active)
            self._cancel_king_mode()

    def _do_king_sacrifice(self, pos: Position, player_id: str) -> None:
        """Commit the ChangeKing action once the sacrifice piece is clicked."""
        action = ChangeKing(
            player_id=player_id, king_card_id=self._king_succession_target,
            sacrifice_position=pos, destroy_building_id=self._king_sacrifice_building_id,
        )
        self._cancel_king_mode()
        self._execute_and_advance(action, player_id)

    def _cancel_king_mode(self) -> None:
        """Exit every step of the King flow without firing any action."""
        self._king_picker = None
        self._king_picker_mode = None
        self._king_picker_actions = []
        self._king_succession_target = None
        self._king_and_mode = False
        self._king_sacrifice_positions = []
        self._king_sacrifice_building_id = None

    # ── Stage 11: Ritual ────────────────────────────────────────────────

    def _ritual_name(self, ritual_id: str) -> str:
        if self._registry is not None and ritual_id in self._registry:
            return self._registry.get(ritual_id).name
        return ritual_id

    def _ritual_combo_label(self, action: "ActivateRitual") -> str:
        """'via Bishop@c1, Pawn@b1' — the Vessel (last position) first."""
        positions = list(action.sacrifice_positions)
        if not positions:
            return "via (no sacrifice)"
        vessel = positions[-1]
        others = positions[:-1]
        parts = [f"Vessel@{vessel.to_algebraic()}"]
        parts += [p.to_algebraic() for p in others]
        return "via " + ", ".join(parts)

    _REVELATION_GLYPH: dict = {
        RevelationState.SEALED: "◆",
        RevelationState.FORETOLD: "◈",
        RevelationState.REVEALED: "◉",
    }

    def _reveal_step_label(self, rstate: "Any") -> str:
        """'Foretell (sealed → foretold)' — what one more step would do."""
        if rstate.revelation == RevelationState.SEALED:
            return "◈  Foretell  —  sealed → foretold"
        return "◉  Reveal  —  foretold → revealed"

    def _ritual_row_label(
        self, rstate: "Any", can_reveal: bool, combo_count: int,
    ) -> str:
        """One Ritual's line in the picker: glyph, name, and what it offers."""
        name = self._ritual_name(rstate.ritual_id)
        glyph = self._REVELATION_GLYPH.get(rstate.revelation, "◆")
        if rstate.activated:
            return f"✓  {name}  ·  completed"
        state_word = rstate.revelation.name.lower()
        if combo_count:
            return f"{glyph}  {name}  ·  ready to activate"
        if can_reveal:
            return f"{glyph}  {name}  ·  {state_word} — can reveal"
        if rstate.revelation == RevelationState.REVEALED:
            return f"{glyph}  {name}  ·  revealed — no sacrifice available"
        return f"{glyph}  {name}  ·  {state_word} — revealed once already"

    def _do_ritual(self) -> None:
        """
        Sidebar button: open the Ritual picker.

        Always lists EVERY Ritual in the player's own pool (own_rituals is
        full information to its owner regardless of revelation state — see
        core/observation.py), annotated with its revelation state. A Ritual
        that offers nothing right now — already ``activated``, or with no
        legal step and no legal sacrifice — renders as a disabled row
        (``_KingDialog`` already supports key=None for this).

        Choosing a Ritual opens its action list (README §15): the one
        voluntary revelation step it can take right now, and/or one row per
        legal sacrifice combo. Both live in the same menu because they are
        the same decision from the player's side — "what do I do with this
        Ritual this turn" — and because a Ritual is only ever activatable
        from REVEALED, so the two are steps of one ladder rather than
        alternatives.
        """
        obs = self._current_obs()
        if obs.phase != Phase.PREPARATION:
            return
        active = obs.active_player
        if self._player_modes.get(active) == "ai":
            return
        self._cancel_ritual_mode()
        self._cancel_king_mode()
        self._cancel_build_mode()
        self._cancel_summon()
        self._cancel_targeting_mode()
        self._cancel_spell_piece_mode()

        ps = self._game.state.get_player(active)
        if not ps.ritual_pool:
            self._show_toast("No Rituals assigned.")
            return

        legal = self._game.get_legal_actions(active)
        ritual_actions = [
            a for a in legal if isinstance(a, (ActivateRitual, RevealRitual))
        ]
        revealable = {
            a.ritual_id for a in ritual_actions if isinstance(a, RevealRitual)
        }
        combo_counts: dict[str, int] = {}
        for a in ritual_actions:
            if isinstance(a, ActivateRitual):
                combo_counts[a.ritual_id] = combo_counts.get(a.ritual_id, 0) + 1

        rows: list[tuple[str, "Any"]] = []
        row_ritual_ids: list[str] = []
        for rstate in ps.ritual_pool:
            rid = rstate.ritual_id
            row_ritual_ids.append(rid)
            can_reveal = rid in revealable
            combos = combo_counts.get(rid, 0)
            label = self._ritual_row_label(rstate, can_reveal, combos)
            rows.append((label, rid if (can_reveal or combos) else None))

        self._ritual_picker = _KingDialog(
            self._screen, self._font_small, "🔮  Rituals", rows,
            accent=(200, 130, 230), border=(160, 90, 210), width=400,
        )
        self._ritual_picker_mode = "choose_ritual"
        self._ritual_picker_actions = ritual_actions
        # Disabled rows carry key=None (unclickable, by _KingDialog design)
        # so key_at() can't recover their ritual_id for right-click inspect
        # — this parallel, always-populated list (row index → ritual_id)
        # is what the right-click handler below reads instead.
        self._ritual_picker_row_ids = row_ritual_ids

    def _on_ritual_picker_choice(self, key: str) -> None:
        """Route a completed _ritual_picker click by the flow's current step."""
        if self._ritual_picker_mode == "choose_ritual":
            actions = [a for a in self._ritual_picker_actions if a.ritual_id == key]
            if not actions:
                return
            # One sacrifice combo and nothing else: commit straight away,
            # as this flow always has. A RevealRitual never auto-commits,
            # though — spending information is irreversible and the player
            # asked for the choice, so it gets its own explicit row rather
            # than firing off the back of a click on the Ritual's name.
            if len(actions) == 1 and isinstance(actions[0], ActivateRitual):
                self._ritual_picker = None
                self._commit_ritual(actions[0])
                return
            # Revelation step first — it is the prerequisite, so it reads as
            # the top of the ladder rather than an afterthought under the
            # sacrifice list.
            actions.sort(key=lambda a: 0 if isinstance(a, RevealRitual) else 1)
            rows = [
                (
                    self._reveal_step_label(
                        self._own_ritual_state(a.player_id, a.ritual_id),
                    )
                    if isinstance(a, RevealRitual)
                    else f"🔮  Activate  —  {self._ritual_combo_label(a)}",
                    i,
                )
                for i, a in enumerate(actions)
            ]
            self._ritual_picker_actions = actions
            self._ritual_picker = _KingDialog(
                self._screen, self._font_small, f"🔮  {self._ritual_name(key)}", rows,
                accent=(200, 130, 230), border=(160, 90, 210), width=400,
            )
            self._ritual_picker_mode = "choose_action"
            return

        if self._ritual_picker_mode == "choose_action":
            self._ritual_picker = None
            self._commit_ritual(self._ritual_picker_actions[key])

    def _own_ritual_state(self, player_id: str, ritual_id: str) -> "Any":
        ps = self._game.state.get_player(player_id)
        return next((rs for rs in ps.ritual_pool if rs.ritual_id == ritual_id), None)

    def _commit_ritual(self, action: "ActivateRitual | RevealRitual") -> None:
        self._cancel_ritual_mode()
        self._execute_and_advance(action, action.player_id)

    def _cancel_ritual_mode(self) -> None:
        """Exit every step of the Ritual flow without firing any action."""
        self._ritual_picker = None
        self._ritual_picker_mode = None
        self._ritual_picker_actions = []
        self._ritual_picker_row_ids = []

    def _handle_mercenary_card_click(self, card_id: str, obs: "object") -> None:
        """
        Toggle a Monster card in/out of the mercenary sacrifice selection.
        Auto-submits SelectMercenaryCards when exactly N are chosen.
        Only Monster cards (present in options) may be toggled.
        """
        if card_id not in obs.pending_decision_options:
            return   # not a valid Monster card for sacrifice

        n = obs.pending_decision_min

        if card_id in self._mercenary_selected:
            self._mercenary_selected.remove(card_id)
        else:
            if len(self._mercenary_selected) < n:
                self._mercenary_selected.append(card_id)

        if len(self._mercenary_selected) == n:
            active = obs.active_player
            action = SelectMercenaryCards(
                player_id=active,
                card_ids=list(self._mercenary_selected),
            )
            self._mercenary_selected = []
            # After SelectMercenaryCards the engine enters MERCENARY_PLACEMENT;
            # compute the valid placement squares now for board highlighting.
            self._execute_and_advance(action, active)
            self._update_mercenary_placement_squares()

    def _update_mercenary_placement_squares(self) -> None:
        """Populate _mercenary_placement_squares from the current legal actions."""
        obs = self._current_obs()
        if obs.phase != Phase.MERCENARY_PLACEMENT:
            self._mercenary_placement_squares = []
            return
        legal = self._game.get_legal_actions(obs.active_player)
        self._mercenary_placement_squares = [
            a.position for a in legal if isinstance(a, PlaceMercenaryPiece)
        ]

    def _try_dismiss_selected(self) -> None:
        """
        D key: dismiss the monster on the currently selected piece (if any).
        Only works during PREPARATION phase for the human player.
        """
        obs = self._current_obs()
        if obs.phase != Phase.PREPARATION:
            return
        active = obs.active_player
        if self._player_modes.get(active) == "ai":
            return
        if self._selected_pos is None:
            self._show_toast("Select a monster piece first, then press D to dismiss.")
            return
        # Check the selected piece actually has a monster
        unit_info = next(
            (u for u in obs.board.units if u.position == self._selected_pos),
            None,
        )
        if unit_info is None or not getattr(unit_info, "monster_id", None):
            self._show_toast("No monster on selected piece to dismiss.")
            return
        self._execute_and_advance(
            DismissMonster(player_id=active, unit_position=self._selected_pos),
            active,
        )
        self._deselect()

    def _toggle_inspect(self, pos: Position) -> None:
        """
        Right-click on the board (Stage 6 — corrected):

        • A unit hosting a Monster → open it in the CardViewer directly
          (toggle off if that exact unit is already open).
        • A Crowned King (Stage 10) → open its owner's ACTIVE King card
          instead (README §17: the active King is public once Coronated,
          so this is safe regardless of whose King it is).
        • A plain unit (no Monster, King with no active policy yet) →
          nothing of its own to view, but it may be standing inside an
          active zone — falls through to the zone check below rather than
          clearing outright.
        • The square is inside one or more of the player's OWN
          active Trap/zone effects → populate the "Active in this zone"
          list. This does NOT open the viewer itself — right-click one of
          the list's entries for that (see zone_entry_from_click routing
          in _handle_event).
        • A square with only an ENEMY Trap/zone (nothing of the player's
          own) → identity stays hidden; just a toast, matching the
          "opponent sees only the zone" rule from the tint pass.
        • Truly empty square → clear everything.
        """
        obs = self._current_obs()
        unit_here = next((u for u in obs.board.units if u.position == pos), None)

        if unit_here is not None and unit_here.monster_id is not None:
            self._zone_active_entries = None
            if pos == self._inspect_unit_pos:
                self._inspect_unit_pos = None
                self._viewer_card_id = None
            else:
                self._inspect_unit_pos = pos
                self._viewer_card_id = unit_here.monster_id
            return

        if unit_here is not None and unit_here.piece_type == "king":
            active_king_id = self._game.state.get_player(unit_here.owner).active_king
            if active_king_id is not None:
                self._zone_active_entries = None
                if pos == self._inspect_unit_pos:
                    self._inspect_unit_pos = None
                    self._viewer_card_id = None
                else:
                    self._inspect_unit_pos = pos
                    self._viewer_card_id = active_king_id
                return

        # Either no unit here, or a plain (non-Monster, uncrowned-King) unit
        # that has nothing of its own to view — either way, check the zone.
        self._inspect_unit_pos = None

        entries = self._collect_zone_entries(pos, obs)
        if entries:
            self._zone_active_entries = entries
            self._viewer_card_id = None
            return

        self._zone_active_entries = None
        self._viewer_card_id = None

        has_enemy_only = (
            any(t.position == pos for t in obs.board.trap_locations)
            or any(e.position == pos for e in obs.board.square_effects)
        )
        if has_enemy_only:
            self._show_toast("Enemy Trap/zone — identity hidden, only its area is visible.")

    def _collect_zone_entries(self, pos: Position, obs) -> "list[tuple[str, str]]":
        """
        Stage 6 — (label, card_id) pairs for every one of the VIEWER's own
        Traps whose activation area covers ``pos``, plus any of their own
        active zone effects (frozen/scorched/blocked/cursed) sitting
        exactly at ``pos``.  Feeds the CardViewer's "Active in this zone"
        section.
        """
        from game.mechanics.area import expand_area

        entries: list[tuple[str, str]] = []
        seen: set[str] = set()

        for trap in obs.board.trap_locations:
            if trap.owner != obs.player_id:
                continue
            if pos not in expand_area(trap.position, trap.radius, trap.shape):
                continue
            if trap.card_id in seen:
                continue
            seen.add(trap.card_id)
            name = trap.card_id.replace("_", " ").title()
            try:
                name = self._registry.get(trap.card_id).name
            except Exception:
                pass
            entries.append((f"🪤 {name}", trap.card_id))

        for eff in obs.board.square_effects:
            if eff.position != pos or eff.owner != obs.player_id or eff.card_id is None:
                continue
            if eff.card_id in seen:
                continue
            seen.add(eff.card_id)
            name = eff.card_id.replace("_", " ").title()
            try:
                name = self._registry.get(eff.card_id).name
            except Exception:
                pass
            entries.append((f"✨ {name} ({eff.effect_type})", eff.card_id))

        return entries

    def _try_activate_ability(self) -> None:
        """
        A key: activate the first available ability on the currently inspected
        or selected piece (only during CHESS, human turn).

        If the inspected unit has exactly one activatable ability, fire it.
        If there are multiple, show a toast directing the player to use the
        sidebar buttons instead.
        """
        obs = self._current_obs()
        if obs.phase != Phase.CHESS:
            self._show_toast("Abilities can only be activated during CHESS phase.")
            return
        active = obs.active_player
        if self._player_modes.get(active) == "ai":
            return

        target_pos = self._inspect_unit_pos or self._selected_pos
        if target_pos is None:
            self._show_toast("Select or inspect a monster piece first (A to activate).")
            return

        unit_info = next(
            (u for u in obs.board.units
             if u.position == target_pos and u.owner == active),
            None,
        )
        if unit_info is None or not unit_info.activatable_effects:
            self._show_toast("No activatable abilities on this piece.")
            return

        abilities = list(unit_info.activatable_effects)
        if len(abilities) == 1:
            self._do_activate_ability(abilities[0], target_pos)
        else:
            self._show_toast(
                f"{len(abilities)} abilities available — click them in the sidebar."
            )

    def _try_activate_trap(self) -> None:
        """
        T key — Stage 6: activate one of the player's own placed
        ``trigger: manual`` Traps (time_anchor).  If exactly one is legal
        right now, fire it directly; if several, point at the sidebar's
        "⚡ Activatable" list (one clickable row per Trap) instead of
        guessing which one you meant.
        """
        obs = self._current_obs()
        if obs.phase != Phase.PREPARATION:
            self._show_toast("Traps can only be activated during your own Preparation phase.")
            return
        active = obs.active_player
        if self._player_modes.get(active) == "ai":
            return

        legal = self._game.get_legal_actions(active)
        trap_actions = [a for a in legal if isinstance(a, ActivateTrap)]
        if not trap_actions:
            self._show_toast("No activatable Traps right now.")
            return
        if len(trap_actions) == 1:
            self._do_activate_trap_by_id(trap_actions[0].trap_instance_id)
        else:
            self._show_toast(
                f"{len(trap_actions)} Traps available — use the sidebar's ⚡ Activatable list."
            )

    def _do_activate_trap_by_id(self, trap_instance_id: str) -> None:
        """Execute ActivateTrap for a specific Trap (T key or sidebar button)."""
        obs = self._current_obs()
        active = obs.active_player
        if self._player_modes.get(active) == "ai":
            return
        action = ActivateTrap(player_id=active, trap_instance_id=trap_instance_id)
        try:
            self._game.execute(action)
            self._auto_advance()
            self._show_toast("⚡ Trap activated!")
        except Exception as exc:
            self._show_toast(f"Cannot activate Trap: {exc}")

    def _do_activate_ability(
        self,
        ability_id: str,
        pos: Position | None = None,
        target: "object | None" = None,
    ) -> None:
        """
        Execute ActivateMonsterAbility for ``ability_id`` on ``pos``.

        ``pos`` defaults to ``_inspect_unit_pos`` then ``_selected_pos``.
        ``target`` is forwarded as-is — required by challenge_unit /
        dismiss_monster / disable_building / ritual_requirement_reduction
        (see _compute_activatable_entries, which is the only caller that
        supplies one today).
        Shows a toast with the result or the error message.

        Calls game.execute() directly (not _execute_and_advance) so that
        errors are surfaced as toasts rather than silently swallowed.
        """
        obs = self._current_obs()
        if obs.phase != Phase.CHESS:
            self._show_toast("Abilities can only be activated during CHESS phase.")
            return
        active = obs.active_player
        if self._player_modes.get(active) == "ai":
            return

        target_pos = pos or self._inspect_unit_pos or self._selected_pos
        if target_pos is None:
            self._show_toast("No unit selected for ability activation.")
            return

        action = ActivateMonsterAbility(
            player_id=active,
            unit_position=target_pos,
            ability_id=ability_id,
            target=target,
        )
        _log.debug("ABILITY  %s  ability=%r  pos=%s", active, ability_id, target_pos)
        try:
            self._game.execute(action)
            self._deselect()
            self._auto_advance()
            self._show_toast(f"⚡ {ability_id.replace('_', ' ')} activated!")
            _log.info("ABILITY  OK  %s  ability=%r", active, ability_id)
        except Exception as exc:
            _log.warning("ABILITY  FAILED  ability=%r  error=%s", ability_id, exc)
            self._show_toast(f"Cannot activate: {exc}")

    def _handle_promotion_click(self, mx: int, my: int) -> None:
        """Handle a click while the promotion dialog is showing."""
        assert self._promotion_dialog is not None
        obs = self._current_obs()
        active = obs.active_player

        chosen = self._promotion_dialog.handle_click(mx, my)
        if chosen is None:
            return  # missed the buttons — keep dialog open

        # Find the pawn on the back rank.
        # NOTE: pending_decision_options contains piece-type strings
        # ("queen","rook","bishop","knight"), NOT a Position.
        # The actual pawn position lives in pending_decision.context,
        # which isn't exposed through Observation.  Use board scan instead.
        back_rank = 7 if active == "white" else 0
        pawn_pos = self._find_pawn_on_rank(back_rank, active, obs)

        if pawn_pos is None:
            # Should never happen — keep dialog open rather than freeze
            return

        action = PromotePawn(
            player_id=active,
            position=pawn_pos,
            piece_type=chosen,
        )
        try:
            self._game.execute(action)
        except Exception as exc:
            # Bad action — keep dialog open so user can retry
            # (don't destroy the dialog on failure)
            return

        # Only dismiss the dialog after a successful execute
        self._promotion_dialog = None
        self._deselect()
        self._auto_advance()

    # ── Action execution helpers ──────────────────────────────────────────

    def _do_move(self, src: Position, dst: Position, player_id: str) -> None:
        _log.debug("MOVE     %s  %s → %s", player_id, src, dst)
        action = MovePiece(player_id=player_id, source=src, target=dst)
        self._execute_and_advance(action, player_id)

    def _do_siege(self, src: Position, dst: Position, player_id: str) -> None:
        """Stage 13 — besiege the Building at ``dst``; the piece stays at ``src``."""
        _log.debug("SIEGE    %s  %s → %s", player_id, src, dst)
        action = AttackBuilding(player_id=player_id, source=src, target=dst)
        self._execute_and_advance(action, player_id)

    def _do_castle(self, dst: Position, player_id: str) -> None:
        """Convert a castle-destination click into a Castle action."""
        side = "kingside" if dst.file == 6 else "queenside"
        _log.debug("CASTLE   %s  side=%s", player_id, side)
        action = Castle(player_id=player_id, side=side)
        self._execute_and_advance(action, player_id)

    def _execute_and_advance(self, action, player_id: str) -> None:
        """
        Execute ``action``, then auto-advance through phases that need no
        user input (REACTION, END → next turn START/DRAW/PREPARATION).

        On error: shows a toast and returns without advancing.
        Callers that need to inspect the error themselves should call
        self._game.execute() directly.
        """
        _log.debug("UI→ENG   %s  player=%s", type(action).__name__, player_id)
        try:
            result = self._game.execute(action)
        except Exception as exc:
            _log.warning("UI→ENG   FAILED  %s: %s", type(action).__name__, exc)
            self._deselect()
            self._show_toast(f"Illegal action: {exc}")
            return

        # Stage 12: a Duel that just concluded clears state.duel — the Duel
        # Log panel disappears with it, so surface the outcome as a toast
        # instead (the round-by-round feed already covered how it got there).
        from game.core.events import GameOver, RoyalEscapeTriggered
        for ev in result.events:
            if isinstance(ev, RoyalEscapeTriggered):
                self._show_toast(f"👑 {ev.defender}'s King escapes the Final Duel!")
            elif isinstance(ev, GameOver) and ev.reason == "final_duel_victory":
                self._show_toast(f"⚔ {ev.winner} wins the Final Duel!")

        self._deselect()
        self._cancel_summon()   # clear summon mode after any executed action
        self._cancel_targeting_mode()
        self._cancel_spell_piece_mode()

        # If a promotion is required, open the dialog
        obs = self._current_obs()
        if obs.phase == Phase.PROMOTION_SELECTION:
            self._promotion_dialog = PromotionDialog(
                surface=self._screen,
                font=self._font_large,
                owner=obs.active_player,
            )
            return  # wait for user to choose

        self._auto_advance()

    # ── Final Duel (Stage 12) ────────────────────────────────────────────

    def _build_duel_picker(self) -> "_KingDialog | None":
        """
        Build a fresh Duel Action picker for the currently-acting HUMAN
        side, or None if it's not a human's Duel turn — AI sides are
        played by _tick_ai instead. Rebuilt every _render() call rather
        than persisted: who's acting (defender/attacker) and what's
        available changes every round, unlike the King/Ritual pickers'
        multi-step flows.
        """
        duel = self._game.state.duel
        if duel is None:
            return None
        acting = duel.defender if not duel.defender_acted_this_round else duel.attacker
        if self._player_modes.get(acting) == "ai":
            return None
        legal = self._game.get_legal_actions(acting)
        if not legal:
            return None

        pool = duel.defender_support if acting == duel.defender else duel.attacker_support
        icons = {"support": "🛡️", "building": "🏰", "king_policy": "👑"}
        rows: list[tuple[str, Any]] = []
        for action in legal:
            item_id = action.parameters.get("item_id")
            if action.duel_action_type == "advance":
                label = "🚶  Advance (pass)"
            elif action.duel_action_type == "strike":
                label = "⚔️  Strike"
            else:
                item = next((i for i in pool if i.item_id == item_id), None)
                icon = icons.get(action.duel_action_type, "•")
                label = f"{icon}  {item.label if item else action.duel_action_type}"
            rows.append((label, (action.duel_action_type, item_id, acting)))

        role = "Defender" if acting == duel.defender else "Attacker"
        rounds = "∞" if not duel.escape_allowed else str(duel.max_rounds)
        title = (
            f"⚔ Final Duel — Round {duel.round_number}/{rounds} — "
            f"Strikes {duel.strikes_landed}/{duel.strikes_needed} — "
            f"{role} ({acting}) — Guards {duel.defender_guards} / Bypass {duel.attacker_bypass}"
        )
        return _KingDialog(
            self._screen, self._font_small, title, rows,
            accent=(230, 90, 90), border=(180, 60, 60),
        )

    def _on_duel_picker_choice(self, key: "tuple[str, str | None, str]") -> None:
        duel_action_type, item_id, player_id = key
        action = FinalDuelAction(
            player_id=player_id,
            duel_action_type=duel_action_type,
            parameters=({"item_id": item_id} if item_id else {}),
        )
        self._duel_picker = None
        self._execute_and_advance(action, player_id)

    # ── Export / Import ───────────────────────────────────────────────────

    # ── Saves directory (next to the running script or cwd) ──────────────────
    _SAVES_DIR: Path = Path.cwd() / "saves"

    def _ensure_saves_dir(self) -> Path:
        """Create the saves/ directory if it doesn't exist and return it."""
        self._SAVES_DIR.mkdir(parents=True, exist_ok=True)
        return self._SAVES_DIR

    def _do_export(self) -> None:
        """
        Write the current game state to saves/<timestamp>.json.

        Avoids tkinter (which crashes on macOS when pygame is running) by
        generating a timestamped filename automatically.  The path is shown
        in a toast so the player knows where the file landed.
        """
        saves = self._ensure_saves_dir()
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = saves / f"sorcerer_king_{ts}.json"
        try:
            export_state(self._game, path)
            self._show_toast(f"Saved → {path.name}")
        except Exception as exc:
            self._show_toast(f"Export failed: {exc}")

    def _do_import(self) -> None:
        """
        Load the most-recently modified save file from saves/.

        If no save files exist, or the saves/ directory is missing, a toast
        is shown instead.  Avoids tkinter entirely to prevent macOS crashes.
        """
        saves = self._SAVES_DIR
        if not saves.exists():
            self._show_toast("No saves/ directory found. Export first.")
            return

        candidates = sorted(
            saves.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            self._show_toast("No save files found in saves/.")
            return

        path = candidates[0]
        try:
            new_game = import_state(path, registry=self._registry)
        except Exception as exc:
            self._show_toast(f"Import failed: {exc}")
            return

        # Hot-swap the game instance and reset all UI state
        self._game = new_game
        self._reset_terrain_scars()
        self._deselect()
        self._promotion_dialog = None
        self._toast_msg = ""
        self._show_toast(f"Loaded ← {path.name}")
        # Drive to the first interactive phase
        self._auto_advance()

    # ── Toast notification (one-frame message at top of board) ────────────

    _toast_msg: str = ""
    _toast_ticks: int = 0
    _TOAST_DURATION: int = 180  # frames at 60 fps ≈ 3 seconds

    def _show_toast(self, msg: str) -> None:
        _log.info("TOAST    %s", msg)
        self._toast_msg = msg
        self._toast_ticks = self._TOAST_DURATION

    def _tick_toast(self) -> None:
        if self._toast_ticks > 0:
            self._toast_ticks -= 1

    # ── Player mode selector ──────────────────────────────────────────────

    # Every controller a side can be driven by — the four AI stages by
    # README §52 difficulty, the five README §51 play styles, and the human.
    # Adding a controller to the game means adding one entry to
    # ``_build_player_roster()`` and nothing else.
    _ROSTER: list[_PlayerChoice] = _PLAYER_ROSTER

    # AI Stage 2 budget for interactive play. The UI asks its bot for a move
    # on the frame the think-delay expires, so the search has to come back
    # inside a couple of frames' worth of patience — a headless run
    # (python -m game.sim --white search) can afford far more.
    _UI_SEARCH_LIMITS: SearchLimits = SearchLimits(
        max_depth=3, max_nodes=2500, max_seconds=0.6
    )

    # AI Stage 4 budget for interactive play, held to the same patience.
    # Stage 4 pays for its worlds with depth: a sampled CHESS decision runs
    # two plies here rather than three, which is what the Stage 2 bot
    # actually completes inside 0.6 s in a crowded midgame anyway (README
    # §47's measured baseline). A headless run (python -m game.sim --white
    # montecarlo) gets the full-depth default.
    _UI_MC_LIMITS: MonteCarloLimits = MonteCarloLimits(
        samples=12, depth=2,
        chess_samples=8, chess_depth=2,
        max_candidates=8, max_nodes=8000, max_seconds=0.9,
    )

    # AI speed tiers — sidebar "AI Speed" button / S key, ``_toggle_ai_speed``.
    #
    # A watched AI-vs-AI match was paying for two costs on *every single*
    # CHESS/PREPARATION decision, for the whole match:
    #
    #   1. _AI_THINK_FRAMES — a fixed 0.5 s pause with no compute in it at
    #      all, purely so a human spectator can register the previous move
    #      before the next one lands. Profiling a full heuristic-vs-heuristic
    #      match (game/sim.py, no rendering) showed real decision-making cost
    #      2.4 s out of 48 s total — the other ~98% was exactly this kind of
    #      per-decision overhead, and in the UI the 0.5 s pause is the bulk
    #      of it: ~330 gated decisions/match × 0.5 s ≈ 165 s on its own.
    #   2. The search budget itself (_UI_SEARCH_LIMITS / _UI_MC_LIMITS) —
    #      real thinking time, not padding. A SearchBot chess decision
    #      profiled at ~0.67 s on average (close to its 0.6 s ceiling), and
    #      there are ~2 such decisions per turn for a full match.
    #
    # NORMAL leaves both exactly as tuned for a human watching a human-vs-AI
    # game. FAST and INSTANT shrink both for AI-vs-AI spectating, where
    # nobody needs the pause and a shallower search is a fair trade for a
    # match that finishes in well under a minute instead of several.
    _AI_SPEEDS: dict[str, dict] = {
        "normal": dict(
            think_frames=_AI_THINK_FRAMES,
            search_limits=SearchLimits(max_depth=3, max_nodes=2500, max_seconds=0.6),
            mc_limits=MonteCarloLimits(
                samples=12, depth=2, chess_samples=8, chess_depth=2,
                max_candidates=8, max_nodes=8000, max_seconds=0.9,
            ),
        ),
        "fast": dict(
            think_frames=6,   # 0.1 s
            search_limits=SearchLimits(max_depth=3, max_nodes=1200, max_seconds=0.25),
            mc_limits=MonteCarloLimits(
                samples=8, depth=2, chess_samples=5, chess_depth=2,
                max_candidates=6, max_nodes=4000, max_seconds=0.4,
            ),
        ),
        "instant": dict(
            think_frames=0,
            search_limits=SearchLimits(max_depth=3, max_nodes=500, max_seconds=0.1),
            mc_limits=MonteCarloLimits(
                samples=5, depth=1, chess_samples=4, chess_depth=1,
                max_candidates=4, max_nodes=1500, max_seconds=0.15,
            ),
        ),
    }
    _AI_SPEED_ORDER: tuple[str, ...] = ("normal", "fast", "instant")

    def _speed_tier(self) -> dict:
        return self._AI_SPEEDS[self._ai_speed]

    def _toggle_ai_speed(self) -> None:
        """Cycle Normal → Fast → Instant → Normal and rebuild both bots."""
        i = self._AI_SPEED_ORDER.index(self._ai_speed)
        self._ai_speed = self._AI_SPEED_ORDER[(i + 1) % len(self._AI_SPEED_ORDER)]
        # Search budget is baked into a bot at construction time, so a
        # tier change only takes effect on bots rebuilt after it — same
        # trade-off _set_mode already makes when switching a side's
        # controller (a rebuilt bot starts its anti-repetition memory
        # fresh rather than carrying the old tier's bot's history).
        for pid in ("white", "black"):
            self._bots[pid] = self._make_bot(pid)
        self._show_toast(f"AI speed: {self._ai_speed.capitalize()}")

    def _make_bot(self, player_id: str) -> PlayerController:
        """
        Build the controller currently selected for ``player_id``.

        Every bot but RandomBot is given the card registry — the *public*
        card definitions, the same rulebook a human reads off the cards. It
        is not game state, so this is not an information leak (README §44);
        every bot still receives only an Observation at decision time.
        MonteCarloBot needs it most: the registry is the pool its sampled
        opponent hands are drawn from.
        """
        kind = self._ai_kinds.get(player_id, "random")
        seed = self._bot_seeds.get(player_id, 0)
        tier = self._speed_tier()
        bot: PlayerController
        if kind == "personality":
            bot = PersonalityBot(
                seed=seed,
                personality=self._ai_personalities.get(
                    player_id, DEFAULT_PERSONALITY
                ),
                registry=self._registry,
                limits=tier["search_limits"],
            )
        elif kind == "montecarlo":
            bot = MonteCarloBot(
                seed=seed,
                registry=self._registry,
                limits=tier["search_limits"],
                mc_limits=tier["mc_limits"],
            )
        elif kind == "search":
            bot = SearchBot(
                seed=seed,
                registry=self._registry,
                limits=tier["search_limits"],
            )
        elif kind == "heuristic":
            bot = HeuristicBot(seed=seed, registry=self._registry)
        else:
            bot = RandomBot(seed=seed)
        bot.player_id = player_id
        return bot

    def current_choice(self, player_id: str) -> _PlayerChoice:
        """The roster entry currently driving ``player_id``."""
        mode = self._player_modes.get(player_id, "human")
        kind = self._ai_kinds.get(player_id, "random")
        style = self._ai_personalities.get(player_id, DEFAULT_PERSONALITY)
        return next(
            (c for c in self._ROSTER if c.matches(mode, kind, style)),
            self._ROSTER[-1],           # the human row — the safe fallback
        )

    def mode_label(self, player_id: str) -> str:
        """
        Human-readable name of the controller driving ``player_id``.

        A play style names itself — "RITUALIST AI" says far more about what
        the player is up against than "PERSONALITY AI" would.
        """
        return self.current_choice(player_id).label

    def _toggle_mode(self, player_id: str) -> None:
        """
        Sidebar selector: open the player picker for ``player_id``.

        This used to cycle one step per click, which meant five clicks to
        reach the far end of the roster and no way to see what the options
        were before landing on one.  The roster is now long enough — four
        difficulty tiers, five play styles and the human — that a list you
        can read beats a cycle you have to walk.
        """
        self._open_player_picker(player_id)

    def _set_mode(
        self, player_id: str, mode: str, kind: str, label: str
    ) -> None:
        """Commit a controller choice for ``player_id`` and rebuild its bot."""
        self._player_modes[player_id] = mode
        self._ai_kinds[player_id] = kind
        # Rebuild so a switched-to bot starts from a clean, seeded state
        # rather than inheriting the previous controller's history.
        self._bots[player_id] = self._make_bot(player_id)

        self._show_toast(f"{player_id.capitalize()} is now {label}")
        # If just switched to AI and it's their turn, arm the countdown
        if mode == "ai":
            obs = self._current_obs()
            if obs.active_player == player_id and not self._game.is_over():
                self._ai_think_countdown = self._speed_tier()["think_frames"]

    # ── Player picker ─────────────────────────────────────────────────────

    def _open_player_picker(self, player_id: str) -> None:
        """
        Open the "who plays this side" popup for ``player_id``.

        Lists the whole roster — the AI stages by difficulty, the README
        §51 play styles, and the human — with the current choice marked.
        Nothing is committed until a row is clicked.
        """
        self._player_picker_side = player_id
        self._player_picker = _PlayerPicker(
            surface=self._screen,
            font=self._font_small,
            side=player_id,
            roster=self._ROSTER,
            current=self.current_choice(player_id),
        )
        self._player_picker._mouse_pos = self._mouse_pos

    def _on_player_choice(self, choice: _PlayerChoice) -> None:
        """A roster row was clicked: commit it and switch the side over."""
        player_id = self._player_picker_side
        self._close_player_picker()
        if player_id is None:
            return
        if choice.personality is not None:
            self._ai_personalities[player_id] = choice.personality
        self._set_mode(player_id, choice.mode, choice.kind, choice.label)
        if choice.subtitle:
            self._show_toast(f"{choice.name}: {choice.subtitle}")

    def _close_player_picker(self) -> None:
        """
        Close the popup.

        Cancel needs no undo: opening the picker changes nothing, so the
        side is still exactly as the player left it.
        """
        self._player_picker = None
        self._player_picker_side = None

    def _toggle_black_hand(self) -> None:
        """Toggle the debug black-hand view and resize the window accordingly."""
        self._show_black_hand = not self._show_black_hand
        new_h = _WIN_H_DEBUG if self._show_black_hand else WIN_H
        self._screen = pygame.display.set_mode((WIN_W, new_h))
        # Rebind all views to the new surface — a view left pointing at the
        # old one draws into a Surface nothing ever blits.
        self._board_view._surface = self._screen
        self._sidebar._surface = self._screen
        self._hand_view._surface = self._screen
        self._card_viewer._surface = self._screen
        self._event_log_panel._surface = self._screen
        self._ritual_bar._surface = self._screen
        self._card_zoom._surface = self._screen
        label = "ON" if self._show_black_hand else "OFF"
        self._show_toast(f"Black hand view: {label}")

    def _toggle_territory(self) -> None:
        """Stage 9: toggle the Territory board tint on/off."""
        self._show_territory = not self._show_territory
        label = "ON" if self._show_territory else "OFF"
        self._show_toast(f"Territory overlay: {label}")

    # ── AI tick (called every frame) ──────────────────────────────────────

    def _tick_ai(self) -> None:
        """
        If the active player is an AI and it's their interactive turn
        (CHESS or PREPARATION), count down the think-delay then execute
        one action.  Repeats each frame so multi-step turns (e.g. the AI
        passing PREPARATION then making a chess move) each get their own
        visual delay.
        """
        if self._game.is_over():
            return

        obs = self._current_obs()
        active = obs.active_player

        # Stage 12: Final Duel — turn order is defender-then-attacker each
        # round, NOT obs.active_player (that still names whoever's normal
        # chess move triggered the Duel). No think-delay: Duel rounds are
        # meant to resolve quickly (README §23, "should be short").
        if obs.phase == Phase.FINAL_DUEL:
            duel = self._game.state.duel
            if duel is None:
                return
            acting = duel.defender if not duel.defender_acted_this_round else duel.attacker
            if self._player_modes.get(acting) != "ai":
                return
            legal = self._game.get_legal_actions(acting)
            if not legal:
                return
            bot = self._bots[acting]
            action = bot.choose_action(self._game.get_observation(acting), legal)
            _log.debug("AI       %s chose Duel action %s", acting, action.duel_action_type)
            self._execute_and_advance(action, acting)
            return

        # AI promotion: auto-pick queen whenever it's an AI's turn in
        # PROMOTION_SELECTION — regardless of whether a dialog is showing.
        # (The dialog is only created for human players in _execute_and_advance.)
        if obs.phase == Phase.PROMOTION_SELECTION and self._player_modes.get(active) == "ai":
            back_rank = 7 if active == "white" else 0
            pawn_pos = self._find_pawn_on_rank(back_rank, active, obs)
            if pawn_pos:
                try:
                    self._game.execute(
                        PromotePawn(player_id=active,
                                    position=pawn_pos,
                                    piece_type="queen")
                    )
                except Exception:
                    pass
                self._promotion_dialog = None
                self._deselect()
                self._auto_advance()
            return

        if self._promotion_dialog is not None:
            return  # human promotion dialog is open — nothing else to do

        # AI mercenary selection: pick the first N Monster cards from legal actions.
        if obs.phase == Phase.MERCENARY_SELECTION and self._player_modes.get(active) == "ai":
            legal = self._game.get_legal_actions(active)
            if legal:
                import random as _random
                action = _random.choice(legal)
                try:
                    self._game.execute(action)
                except Exception:
                    pass
                self._auto_advance()
            return

        # AI mercenary placement: pick a random valid square.
        if obs.phase == Phase.MERCENARY_PLACEMENT and self._player_modes.get(active) == "ai":
            legal = self._game.get_legal_actions(active)
            if legal:
                import random as _random
                action = _random.choice(legal)
                try:
                    self._game.execute(action)
                except Exception:
                    pass
                self._auto_advance()
            return

        # AI discard: no think-delay, just pick a random card immediately
        if obs.phase == Phase.DISCARD and self._player_modes.get(active) == "ai":
            import random as _random
            ps = self._game.state.get_player(active)
            if ps.hand:
                card_id = _random.choice(ps.hand)
                try:
                    self._game.execute(DiscardCard(player_id=active, card_id=card_id))
                except Exception:
                    pass
                self._auto_advance()
            return

        # AI recompose selection: pick randomly from legal SelectRecomposeCards actions
        if obs.phase == Phase.RECOMPOSE_SELECTION and self._player_modes.get(active) == "ai":
            legal = self._game.get_legal_actions(active)
            if legal:
                import random as _random
                action = _random.choice(legal)
                try:
                    self._game.execute(action)
                except Exception:
                    pass
                self._auto_advance()
            return

        # AI reposition: when blade_dancer triggers a REPOSITION pending decision,
        # pick a random RepositionUnit from legal actions and execute it immediately.
        if (obs.phase == Phase.CHESS
                and obs.pending_decision_type == "reposition"
                and self._player_modes.get(active) == "ai"):
            legal = self._game.get_legal_actions(active)
            if legal:
                import random as _random
                action = _random.choice(legal)
                try:
                    self._game.execute(action)
                except Exception:
                    pass
                self._auto_advance()
            return

        # Only act during the two human-interactive phases
        if obs.phase not in (Phase.CHESS, Phase.PREPARATION):
            return
        if self._player_modes.get(active) != "ai":
            return

        # Arm the countdown when it first becomes the AI's turn. The
        # "instant" speed tier sets think_frames to 0 to skip the pause
        # entirely — it must also skip this whole arm/decrement dance
        # (arming to 0 and returning, forever, would never let the AI
        # move) and fall straight through to act below.
        think_frames = self._speed_tier()["think_frames"]
        if think_frames > 0:
            if self._ai_think_countdown <= 0:
                self._ai_think_countdown = think_frames
                return

            self._ai_think_countdown -= 1
            if self._ai_think_countdown > 0:
                return

        # --- Think time expired: pick and execute an action ---
        legal = self._game.get_legal_actions(active)
        if not legal:
            return

        bot = self._bots[active]
        action = bot.choose_action(obs, legal)
        _log.debug("AI       %s chose %s", active, type(action).__name__)
        self._execute_and_advance(action, active)

    def _auto_advance(self) -> None:
        """
        Auto-advance through phases that require no human decision:

          START       → engine auto-resolves to PREPARATION via Game.execute()
          DRAW        → engine auto-resolves to PREPARATION via Game.execute()
          REACTION    → EndTurn
          END         → EndTurn
          FINAL_DUEL  → stop (Stage 12): _tick_ai plays an AI side's Duel
                        turn every frame; a human side is shown the Duel
                        Action picker (_build_duel_picker, drawn in _render)

        After EndTurn the engine sets Phase.START for the next player; the
        next loop iteration drives that through to PREPARATION automatically.

        We stop advancing when:
          • Phase is PREPARATION (player may play a card — Stage 4).
          • Phase is CHESS       (player must click a move).
          • Phase is PROMOTION_SELECTION (dialog is showing).
          • Phase is FINAL_DUEL  (Duel Action picker / AI Duel turn — Stage 12).
          • game.is_over()       (GAME_OVER or winner set)
        """
        _stop = {Phase.PREPARATION, Phase.CHESS, Phase.PROMOTION_SELECTION, Phase.DISCARD,
                 Phase.RECOMPOSE_SELECTION, Phase.MERCENARY_SELECTION, Phase.MERCENARY_PLACEMENT,
                 Phase.FINAL_DUEL}
        _max_iters = 20   # safety valve — never loop forever
        for _ in range(_max_iters):
            if self._game.is_over():
                break
            obs = self._current_obs()
            active = obs.active_player

            # Stage 5: REPOSITION must be checked before the _stop guard because
            # the phase stays CHESS while the pending decision is active, and CHESS
            # is normally in _stop.
            #   • For AI players: auto-resolve randomly here.
            #   • For human players: stop so _handle_board_click can let the user pick.
            if obs.phase == Phase.CHESS and obs.pending_decision_type == "reposition":
                if self._player_modes.get(active) == "ai":
                    legal = self._game.get_legal_actions(active)
                    if not legal:
                        break
                    import random as _random_rp
                    action = _random_rp.choice(legal)
                    try:
                        self._game.execute(action)
                    except Exception:
                        break
                    continue  # re-check after resolving
                else:
                    # Human player — stop so the board click handler takes over.
                    break

            if obs.phase in _stop:
                break

            if obs.phase == Phase.START:
                # Advance START → DRAW → PREPARATION without consuming the
                # player's preparation action.  We must NOT call
                # execute(EndPreparation) here — that would skip PREPARATION
                # entirely (auto-start fires first, then EndPreparation runs).
                self._hand_view.reset_scroll()   # new hand on new turn → scroll to card 0
                self._game.advance_to_preparation()
                # After advance the phase is PREPARATION — loop will stop next iter.

            elif obs.phase == Phase.DRAW:
                # Should not normally be reached (START drives past DRAW via
                # advance_to_preparation), but advance defensively.
                self._game.advance_to_preparation()

            elif obs.phase in (Phase.REACTION, Phase.END):
                try:
                    self._game.execute(EndTurn(player_id=active))
                except Exception:
                    break

            elif obs.phase == Phase.DISCARD:
                # Human player — stop and let the UI handle it.
                # AI discards are handled by _tick_ai each frame.
                break

            elif obs.phase == Phase.RECOMPOSE_SELECTION:
                # Human players stop here — they pick cards interactively via
                # hand-strip clicks (_handle_recompose_card_click).
                # AI players are handled in _tick_ai and never reach this path.
                break

            else:
                # Unknown / unexpected phase — stop to avoid infinite loop
                break

    # ── Selection helpers ─────────────────────────────────────────────────

    def _select(self, pos: Position) -> None:
        """Select the piece at ``pos`` and compute its legal destinations."""
        self._selected_pos = pos
        legal_actions = self._game.get_legal_actions(
            self._current_obs().active_player
        )

        self._legal_dests = [
            a.target
            for a in legal_actions
            if isinstance(a, MovePiece) and a.source == pos
        ]
        self._siege_dests = [
            a.target
            for a in legal_actions
            if isinstance(a, AttackBuilding) and a.source == pos
        ]
        # Castle destinations
        active = self._current_obs().active_player
        king_start_file = 4
        king_rank = 0 if active == "white" else 7
        king_pos = Position(king_start_file, king_rank)
        if pos == king_pos:
            self._castle_dests = [
                Position(6, king_rank) if a.side == "kingside"
                else Position(2, king_rank)
                for a in legal_actions
                if isinstance(a, Castle)
            ]
        else:
            self._castle_dests = []

    def _deselect(self) -> None:
        self._selected_pos = None
        self._legal_dests = []
        self._castle_dests = []
        self._siege_dests = []

    def _viewer_obs(self, obs: "Observation") -> "Observation":
        """
        The Observation to render PRIVATE information from — hand cards,
        and (Stage 11) the Ritual strip's "fully revealed" row.

            • Active player is human       → their own view.
            • Active player is an AI, the
              other side is human          → the human's view (they're
                                             waiting; let them keep
                                             reading their own cards
                                             rather than being shown the
                                             bot's secrets).
            • Both sides are AI            → white's view (spectator mode).

        In a hotseat human-vs-human match this is simply "whoever is to
        move", so the private half of the screen swaps sides every turn,
        which is the whole point of the Ritual strip's two rows.
        """
        active = obs.active_player
        if self._player_modes.get(active) == "human":
            return obs   # already built for the active player
        opponent = "black" if active == "white" else "white"
        if self._player_modes.get(opponent) == "human":
            return self._game.get_observation(opponent)
        return self._game.get_observation("white")

    # ── Render ────────────────────────────────────────────────────────────

    def _render(self) -> None:
        self._screen.fill(BLACK)

        obs = self._current_obs()

        # Determine who is in check (if anyone)
        checked_player: str | None = None
        if obs.own_in_check:
            checked_player = obs.active_player
        elif obs.opponent_in_check:
            checked_player = (
                "black" if obs.active_player == "white" else "white"
            )

        # Board: pass summon vessel positions as extra legal_dests when in summon mode
        board_legal_dests = self._legal_dests
        board_summon_dests: list[Position] = []
        if self._summon_card_id is not None:
            board_summon_dests = self._summon_vessel_positions
        elif self._build_card_id is not None:
            # Stage 8: build mode reuses the same gold-ring highlight channel.
            board_summon_dests = self._build_positions
        elif self._targeting_card_id is not None:
            # Stage 6: Trap-placement / Spell-area targeting — reuses the
            # same gold-ring highlight channel as summon mode.
            board_summon_dests = list(self._targeting_action_by_pos.keys())
        elif self._spell_piece_card_id is not None:
            if self._spell_piece_source is None:
                board_summon_dests = [
                    Position(*a.target["position"]) for a in self._spell_piece_actions
                ]
            else:
                src_tuple = (self._spell_piece_source.file, self._spell_piece_source.rank)
                board_summon_dests = [
                    Position(*a.target["destination"]) for a in self._spell_piece_actions
                    if "destination" in a.target and tuple(a.target["position"]) == src_tuple
                ]
        elif obs.phase == Phase.MERCENARY_PLACEMENT and self._player_modes.get(obs.active_player) == "human":
            board_summon_dests = list(self._mercenary_placement_squares)
        elif self._king_sacrifice_positions:
            # Stage 10: Succession piece-sacrifice mode — same gold-ring channel.
            board_summon_dests = list(self._king_sacrifice_positions)

        # Targeting previews — the same squares already highlighted in gold
        # above, but routed to the board so it can also swap their ground:
        # Arcane Circle for "a Spell can land here", Void Rift for "a Trap
        # can be planted here".  Both revert the instant the mode ends.
        spell_preview: list[Position] = []
        trap_preview: list[Position] = []
        if self._targeting_card_id is not None:
            if self._targeting_kind == "trap":
                trap_preview = board_summon_dests
            else:
                spell_preview = board_summon_dests
        elif self._spell_piece_card_id is not None:
            spell_preview = board_summon_dests

        self._update_terrain_scars(obs)

        self._board_view.draw(
            observation=obs,
            selected_pos=self._selected_pos,
            legal_dests=board_legal_dests,
            castle_dests=self._castle_dests,
            checked_player=checked_player,
            summon_vessel_dests=board_summon_dests,
            inspect_pos=self._inspect_unit_pos,
            aura_colors=self._compute_aura_colors(obs),
            show_territory=self._show_territory,
            crowned_kings=self._compute_crowned_kings(obs),
            archetype_map=self._compute_archetype_map(obs),
            siege_dests=self._siege_dests,
            spell_preview=spell_preview,
            trap_preview=trap_preview,
            scars=self._scar_map(obs),
        )

        # Stage 11: the Ritual strip above the board. Drawn from the
        # VIEWER's Observation (see _viewer_obs) so its "you" row shows a
        # full pool and its "rival" row only what that side has leaked —
        # the widget never sees anything the observation layer redacted.
        viewer_obs = self._viewer_obs(obs)
        self._ritual_bar.draw(viewer_obs, active_player=obs.active_player)

        # Stage 6: hover tooltip for Traps / active zone effects.
        hover_pos = self._board_view.pos_from_click(*self._mouse_pos)
        if hover_pos is not None:
            tooltip_lines = self._hover_tooltip_lines(hover_pos, obs)
            if tooltip_lines:
                self._board_view.draw_tooltip(self._mouse_pos, tooltip_lines)

        # Sidebar (pass player modes, black-hand flag, and recompose button flag)
        active = obs.active_player
        active_is_human = self._player_modes.get(active) == "human"
        prep_available = (
            obs.phase == Phase.PREPARATION
            and active_is_human
            and not self._game.state.get_player(active).preparation_action_used
        )
        show_recompose = prep_available

        # Mercenary button:
        #   None  = not in PREPARATION → don't draw at all
        #   False = in PREPARATION but can't afford / no empty squares → greyed out
        #   True  = in PREPARATION and at least one tier is affordable with empty square
        show_mercenary: "bool | None" = None
        if prep_available and self._registry is not None:
            from game.cards.card import MonsterCard
            from game.chess.pieces import Position as _Pos
            ps = self._game.state.get_player(active)
            monster_count = sum(
                1 for cid in ps.hand
                if cid in self._registry and isinstance(self._registry.get(cid), MonsterCard)
            )
            own_ranks = (0, 1) if active == "white" else (6, 7)
            has_empty = any(
                self._game.state.board.get_unit(_Pos(f, r)) is None
                for f in range(8)
                for r in own_ranks
            )
            show_mercenary = (monster_count >= 4 and has_empty)
        elif prep_available:
            # registry not loaded yet — show disabled rather than hidden
            show_mercenary = False

        # Build button (Stage 8): same tri-state convention as Mercenary.
        show_build: "bool | None" = None
        if prep_available:
            ps = self._game.state.get_player(active)
            has_builder = any(
                unit.piece.piece_type == PieceType.PAWN and unit.builder_available
                and not is_committed_builder(self._game.state, unit.piece.id)
                for _pos, unit in self._game.state.board.all_units_for(active)
            )
            has_pool = any(e.copies_available > 0 for e in ps.building_pool)
            show_build = has_builder and has_pool

        # King button (Stage 10): same tri-state convention as Build/Mercenary.
        #   None  = not in PREPARATION, or the King Pool is fully spent
        #           (no HIDDEN King left ever again) → don't draw at all.
        #   False = a Coronation/Succession is conceptually possible but not
        #           legal right now (e.g. in check) → greyed out.
        #   True  = at least one Coronation/Succession is legal this instant.
        show_king: "bool | None" = None
        if prep_available:
            ps = self._game.state.get_player(active)
            king_conceptually_possible = any(
                kcs.status == KingCardStatus.HIDDEN for kcs in ps.king_pool
            )
            if king_conceptually_possible:
                legal = self._game.get_legal_actions(active)
                show_king = any(isinstance(a, (CoronateKing, ChangeKing)) for a in legal)

        # Ritual button (Stage 11): unlike Build/Mercenary/King, the button
        # itself stays enabled the whole time the player HAS a Ritual pool
        # — it opens a picker that always lists every owned Ritual, with
        # unready/completed ones rendered as disabled rows (see _do_ritual)
        # rather than hiding the button until something is actionable.
        #
        # It also ignores ``prep_available``: RevealRitual (README §15.2)
        # costs no preparation action, so the picker stays reachable after
        # the player has already summoned, built or crowned this turn.
        #   None = not in PREPARATION, not this human's turn, or the player
        #          has no Ritual pool at all (e.g. no registry) → don't draw.
        #   True = the player owns at least one Ritual — always clickable.
        show_ritual: "bool | None" = None
        if obs.phase == Phase.PREPARATION and active_is_human:
            ps = self._game.state.get_player(active)
            if ps.ritual_pool:
                show_ritual = True

        # ── CardViewer (left sidebar) ───────────────────────────────────────
        # Stage 6 (corrected): right-click driven only — hand cards, board
        # units, and "Active in this zone" entries all set self._viewer_card_id
        # directly (see _handle_event / _toggle_inspect).  Ability buttons +
        # live statuses only apply when the viewer is showing a right-clicked
        # SUMMONED unit (self._inspect_unit_pos set and matching).
        ability_ids: list[str] = []
        _unit_statuses: tuple = ()
        if self._inspect_unit_pos is not None and self._viewer_card_id is not None:
            unit_info = next(
                (u for u in obs.board.units if u.position == self._inspect_unit_pos),
                None,
            )
            if unit_info is not None and unit_info.monster_id == self._viewer_card_id:
                if unit_info.owner == active and obs.phase == Phase.CHESS and active_is_human:
                    ability_ids = list(unit_info.activatable_effects)
                _unit_statuses = unit_info.statuses
            elif (
                unit_info is not None
                and unit_info.piece_type == "king"
                and self._game.state.get_player(unit_info.owner).active_king == self._viewer_card_id
            ):
                # Stage 10: a Crowned King has no live "statuses"/abilities
                # of its own — just keep it selected (matched, not stale).
                pass
            else:
                # Stale — the unit moved/changed since the right-click.
                self._inspect_unit_pos = None

        # Stage 6: hand playability (greying) + the global Activatable list —
        # both only meaningful when it's actually this human's turn to act.
        playable_card_ids: "set[str] | None" = None
        activatable_entries: list[tuple[str, str]] = []
        if active_is_human:
            playable_card_ids = self._compute_playable_card_ids(obs)
            activatable_entries = self._compute_activatable_entries(obs)

        self._sidebar.draw(
            obs,
            player_modes=self._player_modes,
            mode_labels={
                pid: self.mode_label(pid) for pid in ("white", "black")
            },
            show_black_hand=self._show_black_hand,
            show_recompose_btn=show_recompose,
            show_mercenary_btn=show_mercenary,
            show_build_btn=show_build,
            show_king_btn=show_king,
            show_ritual_btn=show_ritual,
            show_territory=self._show_territory,
            ai_speed_label=self._ai_speed.capitalize(),
            activatable_entries=activatable_entries or None,
        )
        self._card_viewer.draw(
            self._viewer_card_id,
            ability_ids=ability_ids if ability_ids else None,
            unit_statuses=_unit_statuses,
            zone_entries=self._zone_active_entries,
            content_height=self._screen.get_height() - LOG_HEIGHT,
        )
        self._event_log_panel.draw(self._game.state.event_log, registry=self._registry)

        # Hand strip:
        # • Main row shows the viewer's hand — see _viewer_obs for which
        #   side that is (the same rule the Ritual strip above uses).
        # • Debug row shows BLACK's raw hand only when the toggle is ON.
        hand_obs = viewer_obs

        # Debug row: black's hand when toggle is ON
        black_cards: list[str] | None = (
            list(self._game.state.get_player("black").hand)
            if self._show_black_hand else None
        )

        selected_card_id = (
            self._summon_card_id or self._targeting_card_id or self._spell_piece_card_id
        )
        recompose_mode = obs.phase == Phase.RECOMPOSE_SELECTION and active_is_human
        mercenary_mode = obs.phase == Phase.MERCENARY_SELECTION and active_is_human
        self._hand_view.draw(
            hand_obs,
            opponent_cards=black_cards,
            discard_mode=(obs.phase == Phase.DISCARD and active_is_human),
            selected_card_id=selected_card_id if active_is_human else None,
            playable_card_ids=playable_card_ids,
            recompose_mode=recompose_mode,
            recompose_selected_ids=set(self._recompose_selected) if recompose_mode else None,
            mercenary_mode=mercenary_mode,
            mercenary_selected_ids=set(self._mercenary_selected) if mercenary_mode else None,
            mercenary_valid_ids=set(obs.pending_decision_options) if mercenary_mode else None,
        )

        # Promotion dialog (on-board overlay)
        if self._promotion_dialog is not None:
            self._promotion_dialog.draw()

        # Stage 7: Mercenary piece-type picker dialog
        if self._mercenary_picker is not None:
            self._mercenary_picker.draw()

        # Stage 8: Building Pool picker dialog
        if self._build_picker is not None:
            self._build_picker.draw()

        # Stage 10: King (Coronation / Succession) picker dialog
        if self._king_picker is not None:
            self._king_picker.draw()

        # Stage 11: Ritual picker dialog
        if self._ritual_picker is not None:
            self._ritual_picker.draw()

        # Stage 12: Final Duel round-by-round log — always visible while a
        # Duel is active, for BOTH sides' actions (including an AI's, which
        # has no picker to look at). Answers "what did the other King just
        # do" without digging through the sidebar Event Log.
        if self._game.state.duel is not None:
            self._draw_duel_log(self._game.state.duel)

        # Stage 12: Final Duel Action picker — rebuilt fresh every frame
        # (see _build_duel_picker docstring). None while it's an AI Duel
        # turn (handled by _tick_ai) or after GAME_OVER/Royal Escape.
        self._duel_picker = self._build_duel_picker()
        if self._duel_picker is not None:
            self._duel_picker._mouse_pos = self._mouse_pos
            self._duel_picker.draw()

        # The player picker sits on top of everything — it is a setup
        # dialog, not part of the match.
        if self._player_picker is not None:
            self._player_picker.draw()

        # Game-over banner
        if self._game.is_over():
            self._draw_game_over_banner()

        # Toast notification (save/load feedback)
        if self._toast_ticks > 0:
            self._draw_toast()

        # Stage 11: the full-screen card zoom is the topmost layer — it is
        # a modal "read this properly" view, so it covers the pickers and
        # the banner too, and every click goes to it until it's dismissed.
        if self._card_zoom_id is not None:
            self._card_zoom.draw(self._card_zoom_id)

        pygame.display.flip()

    def _draw_game_over_banner(self) -> None:
        """Render a simple 'Game Over' overlay in the centre of the board."""
        winner = self._game.winner()
        msg = f"Game Over — {winner.capitalize()} wins!" if winner else "Game Over — Draw"

        # Semi-transparent dark background
        overlay = pygame.Surface((_BOARD_W, 80), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 180))
        self._screen.blit(overlay, (0, _BOARD_H // 2 - 40))

        text_surf = self._font_large.render(msg, True, (255, 220, 60))
        tx = (_BOARD_W - text_surf.get_width()) // 2
        ty = _BOARD_H // 2 - text_surf.get_height() // 2
        self._screen.blit(text_surf, (tx, ty))

    def _draw_duel_log(self, duel: "object") -> None:
        """
        Stage 12 — a small always-on panel pinned to the top of the board
        showing the last few Duel actions (both sides), newest at the
        bottom. Distinct from the Duel Action picker (_build_duel_picker):
        this draws every frame the Duel is active regardless of whose turn
        it is or whether that side is AI, so a human watching an AI
        opponent — or spectating an AI-vs-AI Duel — can follow along.
        """
        lines: list[str] = duel.log[-7:]
        if not lines:
            return

        line_h = self._font_small.render("Ag", True, (230, 230, 230)).get_height() + 3
        pad = 8
        header_h = self._font_small.render("Ag", True, (230, 230, 230)).get_height()
        w = 380
        h = pad * 2 + header_h + 4 + line_h * len(lines)
        x = (_BOARD_W - w) // 2
        y = 8

        panel = pygame.Surface((w, h), pygame.SRCALPHA)
        panel.fill((18, 14, 20, 215))
        self._screen.blit(panel, (x, y))
        pygame.draw.rect(self._screen, (150, 70, 70), (x, y, w, h), 1, border_radius=4)

        header = self._font_small.render("⚔ Duel Log", True, (230, 150, 90))
        self._screen.blit(header, (x + pad, y + pad))
        ly = y + pad + header_h + 4

        for line in lines:
            if line.startswith("white"):
                color = (235, 235, 240)
            elif line.startswith("black"):
                color = (175, 175, 210)
            elif line.startswith("—"):
                color = (150, 130, 100)
            elif line.startswith("⚔"):
                color = (230, 150, 90)
            else:
                color = (210, 210, 210)
            surf = self._font_small.render(line, True, color)
            inner_w = w - pad * 2
            if surf.get_width() > inner_w:
                trimmed = line
                while trimmed and surf.get_width() > inner_w:
                    trimmed = trimmed[:-1]
                    surf = self._font_small.render(trimmed + "…", True, color)
            self._screen.blit(surf, (x + pad, ly))
            ly += line_h

    def _draw_toast(self) -> None:
        """
        Draw a short-lived notification bar at the bottom of the board
        to confirm Export / Import success or failure.
        """
        # Fade out over the last 60 frames
        alpha = min(255, self._toast_ticks * 4)
        bar_h = 28
        bar = pygame.Surface((_BOARD_W, bar_h), pygame.SRCALPHA)
        bar.fill((20, 20, 40, min(alpha, 200)))
        self._screen.blit(bar, (0, _HAND_Y - bar_h))

        surf = self._font_small.render(self._toast_msg, True, (180, 220, 255))
        tx = (_BOARD_W - surf.get_width()) // 2
        ty = _HAND_Y - bar_h + (bar_h - surf.get_height()) // 2
        self._screen.blit(surf, (tx, ty))

    # ── Observation helpers ───────────────────────────────────────────────

    def _current_obs(self):
        """Return an Observation for the current active player."""
        active = self._game.state.active_player
        return self._game.get_observation(active)

    def _unit_at(self, pos: Position, owner: str) -> bool:
        """Return True if a unit owned by ``owner`` is on ``pos``."""
        obs = self._current_obs()
        return any(
            u.position == pos and u.owner == owner
            for u in obs.board.units
        )

    @staticmethod
    def _find_pawn_on_rank(rank: int, owner: str, obs) -> Position | None:
        """Find a pawn of ``owner`` on ``rank``, used as fallback for promotion."""
        for u in obs.board.units:
            if u.owner == owner and u.piece_type == "pawn" and u.position.rank == rank:
                return u.position
        return None


# ─────────────────────────────────────────────────────────────────────────────
# _MercenaryPicker — a small on-board dialog for selecting the piece type
# ─────────────────────────────────────────────────────────────────────────────

class _MercenaryPicker:
    """
    Modal dialog drawn over the centre of the board.

    Displays the five buyable piece types with their Monster-card costs.
    Dims out options the player cannot afford.

    ``handle_click(mx, my)``
        Returns the selected piece type string, "cancel", or None (missed).
    ``draw()``
        Renders the dialog each frame.
    """

    _COSTS: list[tuple[str, int]] = [
        ("pawn",   4),
        ("knight", 6),
        ("bishop", 6),
        ("rook",   6),
        ("queen",  8),
    ]
    _PIECE_GLYPH: dict[str, str] = {
        "pawn":   "♟",
        "knight": "♞",
        "bishop": "♝",
        "rook":   "♜",
        "queen":  "♛",
    }

    _W = 260
    _ROW_H = 32
    _PADDING = 12
    _CANCEL_H = 28

    def __init__(
        self,
        surface: "pygame.Surface",
        font: "Any",
        player_id: str,
        registry: "Any",
        hand: list[str],
    ) -> None:
        self._surface = surface
        self._font = font
        self._monster_count = 0
        if registry is not None:
            from game.cards.card import MonsterCard
            self._monster_count = sum(
                1 for cid in hand
                if cid in registry and isinstance(registry.get(cid), MonsterCard)
            )
        self._player_id = player_id
        self._mouse_pos: tuple[int, int] = (0, 0)

        # Dialog geometry — centred on the board area
        h = (self._PADDING
             + self._font.render("Ag", True, (0, 0, 0)).get_height() + 8   # title
             + len(self._COSTS) * (self._ROW_H + 3)
             + 6 + self._CANCEL_H
             + self._PADDING)
        sw = surface.get_width()
        sh = surface.get_height()
        self._rect = pygame.Rect(
            (sw - self._W) // 2,
            (sh - h) // 2,
            self._W,
            h,
        )
        self._row_rects: list[pygame.Rect] = []
        self._cancel_rect: pygame.Rect | None = None

    def handle_click(self, mx: int, my: int) -> "str | None":
        for i, rect in enumerate(self._row_rects):
            if rect.collidepoint(mx, my):
                piece_type, cost = self._COSTS[i]
                if self._monster_count >= cost:
                    return piece_type
        if self._cancel_rect and self._cancel_rect.collidepoint(mx, my):
            return "cancel"
        # Click outside dialog = cancel
        if not self._rect.collidepoint(mx, my):
            return "cancel"
        return None

    def draw(self) -> None:
        # Semi-transparent backdrop
        overlay = pygame.Surface(
            (self._surface.get_width(), self._surface.get_height()), pygame.SRCALPHA
        )
        overlay.fill((0, 0, 0, 140))
        self._surface.blit(overlay, (0, 0))

        # Dialog background
        pygame.draw.rect(self._surface, (30, 24, 16), self._rect, border_radius=6)
        pygame.draw.rect(self._surface, (160, 120, 40), self._rect, 2, border_radius=6)

        x = self._rect.x + self._PADDING
        y = self._rect.y + self._PADDING

        title = self._font.render("⚔  Hire a Mercenary", True, (200, 160, 80))
        self._surface.blit(title, (x, y))
        y += title.get_height() + 8

        self._row_rects = []
        for piece_type, cost in self._COSTS:
            row_rect = pygame.Rect(x, y, self._W - self._PADDING * 2, self._ROW_H)
            self._row_rects.append(row_rect)
            affordable = self._monster_count >= cost

            hover = affordable and row_rect.collidepoint(self._mouse_pos)
            bg = (60, 48, 20) if hover else (40, 30, 10)
            pygame.draw.rect(self._surface, bg, row_rect, border_radius=4)
            border = (200, 160, 80) if affordable else (60, 50, 30)
            pygame.draw.rect(self._surface, border, row_rect, 1, border_radius=4)

            glyph = self._PIECE_GLYPH.get(piece_type, "?")
            label = f"{glyph}  {piece_type.capitalize()}   —  {cost} Monsters"
            color = (220, 180, 80) if affordable else (80, 70, 50)
            lbl_surf = self._font.render(label, True, color)
            ly = row_rect.y + (self._ROW_H - lbl_surf.get_height()) // 2
            self._surface.blit(lbl_surf, (row_rect.x + 6, ly))

            y += self._ROW_H + 3

        # Cancel button
        y += 6
        cancel_rect = pygame.Rect(x, y, self._W - self._PADDING * 2, self._CANCEL_H)
        self._cancel_rect = cancel_rect
        hover_c = cancel_rect.collidepoint(self._mouse_pos)
        pygame.draw.rect(self._surface, (50, 30, 30) if hover_c else (30, 20, 20),
                         cancel_rect, border_radius=4)
        pygame.draw.rect(self._surface, (160, 80, 80), cancel_rect, 1, border_radius=4)
        cs = self._font.render("Cancel", True, (180, 100, 100))
        self._surface.blit(cs, (
            cancel_rect.x + (cancel_rect.width - cs.get_width()) // 2,
            cancel_rect.y + (cancel_rect.height - cs.get_height()) // 2,
        ))


# ─────────────────────────────────────────────────────────────────────────────
# _BuildPicker — a small on-board dialog for selecting a Building Pool entry
# ─────────────────────────────────────────────────────────────────────────────

class _BuildPicker:
    """
    Modal dialog drawn over the centre of the board (Stage 8).

    Lists the player's public Building Pool entries (README §12) with
    remaining copies and point cost; entries with 0 copies left are dimmed.

    ``handle_click(mx, my)``
        Returns the selected building_card_id, "cancel", or None (missed).
    ``draw()``
        Renders the dialog each frame.
    """

    _W = 280
    _ROW_H = 34
    _PADDING = 12
    _CANCEL_H = 28

    def __init__(
        self,
        surface: "pygame.Surface",
        font: "Any",
        registry: "Any",
        pool: "list[Any]",   # list[BuildingPoolEntry]
    ) -> None:
        self._surface = surface
        self._font = font
        self._registry = registry
        self._pool = pool
        self._mouse_pos: tuple[int, int] = (0, 0)

        h = (self._PADDING
             + self._font.render("Ag", True, (0, 0, 0)).get_height() + 8   # title
             + len(pool) * (self._ROW_H + 3)
             + 6 + self._CANCEL_H
             + self._PADDING)
        sw = surface.get_width()
        sh = surface.get_height()
        self._rect = pygame.Rect(
            (sw - self._W) // 2,
            (sh - h) // 2,
            self._W,
            h,
        )
        self._row_rects: list[pygame.Rect] = []
        self._cancel_rect: pygame.Rect | None = None

    def handle_click(self, mx: int, my: int) -> "str | None":
        for i, rect in enumerate(self._row_rects):
            if rect.collidepoint(mx, my):
                entry = self._pool[i]
                if entry.copies_available > 0:
                    return entry.building_card_id
        if self._cancel_rect and self._cancel_rect.collidepoint(mx, my):
            return "cancel"
        if not self._rect.collidepoint(mx, my):
            return "cancel"
        return None

    def _card_label(self, entry: "Any") -> tuple[str, int]:
        """Return (display name, point cost) for a pool entry."""
        name = entry.building_card_id
        cost = 0
        if self._registry is not None and entry.building_card_id in self._registry:
            card = self._registry.get(entry.building_card_id)
            name = getattr(card, "name", name)
            cost = getattr(card, "cost", 0)
        return name, cost

    def draw(self) -> None:
        overlay = pygame.Surface(
            (self._surface.get_width(), self._surface.get_height()), pygame.SRCALPHA
        )
        overlay.fill((0, 0, 0, 140))
        self._surface.blit(overlay, (0, 0))

        pygame.draw.rect(self._surface, (16, 26, 20), self._rect, border_radius=6)
        pygame.draw.rect(self._surface, (90, 160, 110), self._rect, 2, border_radius=6)

        x = self._rect.x + self._PADDING
        y = self._rect.y + self._PADDING

        title = self._font.render("🏛  Building Pool", True, (140, 220, 160))
        self._surface.blit(title, (x, y))
        y += title.get_height() + 8

        self._row_rects = []
        for entry in self._pool:
            row_rect = pygame.Rect(x, y, self._W - self._PADDING * 2, self._ROW_H)
            self._row_rects.append(row_rect)
            available = entry.copies_available > 0

            hover = available and row_rect.collidepoint(self._mouse_pos)
            bg = (30, 50, 36) if hover else (20, 34, 25)
            pygame.draw.rect(self._surface, bg, row_rect, border_radius=4)
            border = (120, 200, 140) if available else (55, 70, 60)
            pygame.draw.rect(self._surface, border, row_rect, 1, border_radius=4)

            name, cost = self._card_label(entry)
            label = f"{name}  —  {cost} pt(s)  ×{entry.copies_available} left"
            color = (170, 230, 185) if available else (75, 90, 80)
            lbl_surf = self._font.render(label, True, color)
            ly = row_rect.y + (self._ROW_H - lbl_surf.get_height()) // 2
            self._surface.blit(lbl_surf, (row_rect.x + 6, ly))

            y += self._ROW_H + 3

        y += 6
        cancel_rect = pygame.Rect(x, y, self._W - self._PADDING * 2, self._CANCEL_H)
        self._cancel_rect = cancel_rect
        hover_c = cancel_rect.collidepoint(self._mouse_pos)
        pygame.draw.rect(self._surface, (50, 30, 30) if hover_c else (30, 20, 20),
                         cancel_rect, border_radius=4)
        pygame.draw.rect(self._surface, (160, 80, 80), cancel_rect, 1, border_radius=4)
        cs = self._font.render("Cancel", True, (180, 100, 100))
        self._surface.blit(cs, (
            cancel_rect.x + (cancel_rect.width - cs.get_width()) // 2,
            cancel_rect.y + (cancel_rect.height - cs.get_height()) // 2,
        ))


# ─────────────────────────────────────────────────────────────────────────────
# _PlayerPicker — "who plays this side" chooser
# ─────────────────────────────────────────────────────────────────────────────

class _PlayerPicker:
    """
    Modal popup listing every controller a side can be driven by.

    Deliberately NOT a ``_KingDialog``.  This is a setup choice the player
    makes once and then plays against for a whole match, and a bare name
    says nothing about what that match will feel like — "Monte Carlo" does
    not read as *hard*, and "Assassin" does not read as *goes for your King
    while losing*.  So every row carries a difficulty word or a play-style
    description, and the hovered row's behaviour is spelled out underneath.

    The roster comes in already ordered (difficulty tiers, then README §51
    play styles, then the human); the only thing this class adds is the
    "Personalities" heading above the first play style, so the two kinds of
    choice do not read as one flat list.

    ``handle_click(mx, my)`` returns the chosen ``_PlayerChoice``, "cancel",
    or None (a click that missed everything inside the dialog).
    """

    _W = 470
    _HEAD_H = 22
    _PADDING = 14
    _CANCEL_H = 28

    # Row height and gap are computed, not fixed: the roster grows as the
    # game gains controllers, and the window is only 746 px tall. These are
    # the comfortable values and the tightest ones still worth showing —
    # ``_fit()`` walks from the first toward the second until the dialog
    # fits, so adding a play style shrinks the rows instead of running off
    # the bottom of the screen.
    _GAP_ROOMY, _GAP_TIGHT = 3, 1
    _ROW_PAD_ROOMY, _ROW_PAD_TIGHT = 8, 2

    _BG = (26, 22, 32)
    _ACCENT = (206, 162, 230)
    _TIER = (150, 200, 190)
    _BORDER = (150, 110, 180)
    _DIM = (150, 140, 160)
    _ROW_BG = (40, 32, 48)
    _ROW_BG_HOVER = (58, 44, 70)
    _ROW_BG_CURRENT = (50, 40, 64)

    def __init__(
        self,
        surface: "pygame.Surface",
        font: "Any",
        side: str,
        roster: "list[_PlayerChoice]",
        current: "_PlayerChoice",
    ) -> None:
        self._surface = surface
        self._font = font
        self._side = side
        self._roster = roster
        self._current = current
        self._mouse_pos: tuple[int, int] = (0, 0)
        # The row the detail panel is describing: whatever the mouse is
        # over, falling back to the current choice so it is never blank.
        self._detail: "_PlayerChoice" = current

        # A heading is drawn above the first play style, so the difficulty
        # tiers and the §51 styles do not read as one flat list.
        self._heading_before = next(
            (c for c in roster if c.kind == "personality"), None
        )
        self._detail_lines = max((len(c.detail) for c in roster), default=0)

        line_h = font.render("Ag", True, (0, 0, 0)).get_height()
        self._line_h = line_h
        sw, sh = surface.get_width(), surface.get_height()
        h = self._fit(len(roster), line_h, sh - 8)
        self._rect = pygame.Rect((sw - self._W) // 2, max((sh - h) // 2, 4),
                                 self._W, h)
        self._row_rects: list[pygame.Rect] = []
        self._cancel_rect: "pygame.Rect | None" = None

    def _fit(self, rows: int, line_h: int, available: int) -> int:
        """
        Choose row metrics that fit ``rows`` rows into ``available`` pixels,
        and return the dialog height.

        Tries the roomy spacing first and tightens in steps; if even the
        tight spacing overflows, the detail panel gives up lines last,
        because a row the player cannot see is worse than a description
        they have to hover twice for.
        """
        height = 0
        for gap, row_pad in (
            (self._GAP_ROOMY, self._ROW_PAD_ROOMY),
            (self._GAP_ROOMY, self._ROW_PAD_TIGHT),
            (self._GAP_TIGHT, self._ROW_PAD_TIGHT),
        ):
            self._gap, self._row_h = gap, line_h * 2 + row_pad
            for lines in range(self._detail_lines, 0, -1):
                # The draw loop clips to the panel, so sizing it for fewer
                # lines IS dropping them — no separate counter needed.
                self._detail_h = 10 + (lines + 1) * (line_h + 2) + 6
                height = self._height(rows, line_h)
                if height <= available:
                    return height
        return height   # nothing fits; __init__ clamps the top edge to 4

    def _height(self, rows: int, line_h: int) -> int:
        return (self._PADDING
                + line_h + 10                                   # title
                + rows * (self._row_h + self._gap)
                + (self._HEAD_H + self._gap if self._heading_before else 0)
                + 6 + self._detail_h
                + 8 + self._CANCEL_H
                + self._PADDING)

    # ── Input ────────────────────────────────────────────────────────────

    def handle_click(self, mx: int, my: int) -> "_PlayerChoice | str | None":
        for rect, choice in zip(self._row_rects, self._roster):
            if rect.collidepoint(mx, my):
                return choice
        if self._cancel_rect and self._cancel_rect.collidepoint(mx, my):
            return "cancel"
        if not self._rect.collidepoint(mx, my):
            return "cancel"          # click-away closes, like every picker
        return None

    # ── Render ───────────────────────────────────────────────────────────

    def draw(self) -> None:
        overlay = pygame.Surface(
            (self._surface.get_width(), self._surface.get_height()), pygame.SRCALPHA
        )
        overlay.fill((0, 0, 0, 150))
        self._surface.blit(overlay, (0, 0))

        pygame.draw.rect(self._surface, self._BG, self._rect, border_radius=6)
        pygame.draw.rect(self._surface, self._BORDER, self._rect, 2, border_radius=6)

        x = self._rect.x + self._PADDING
        inner_w = self._W - self._PADDING * 2
        y = self._rect.y + self._PADDING

        title = self._font.render(
            f"{self._side.capitalize()} — who plays this side?", True, self._ACCENT
        )
        self._surface.blit(title, (x, y))
        y += title.get_height() + 10

        hovered: "_PlayerChoice | None" = None
        self._row_rects = []
        for choice in self._roster:
            if choice is self._heading_before:
                head = self._font.render("Personalities", True, self._DIM)
                self._surface.blit(head, (x + 2, y + 2))
                line_y = y + 2 + head.get_height() // 2
                pygame.draw.line(
                    self._surface, (70, 58, 88),
                    (x + head.get_width() + 10, line_y), (x + inner_w, line_y),
                )
                y += self._HEAD_H + self._gap

            row = pygame.Rect(x, y, inner_w, self._row_h)
            self._row_rects.append(row)
            is_current = choice is self._current
            is_hover = row.collidepoint(self._mouse_pos)
            if is_hover:
                hovered = choice

            bg = (self._ROW_BG_HOVER if is_hover
                  else self._ROW_BG_CURRENT if is_current else self._ROW_BG)
            pygame.draw.rect(self._surface, bg, row, border_radius=4)
            pygame.draw.rect(
                self._surface,
                self._ACCENT if (is_hover or is_current) else self._BORDER,
                row, 2 if is_current else 1, border_radius=4,
            )

            marker = "●" if is_current else "○"
            ms = self._font.render(marker, True, self._ACCENT)
            self._surface.blit(ms, (row.x + 8, row.y + 4))

            name = self._font.render(choice.name, True, self._ACCENT)
            self._surface.blit(name, (row.x + 26, row.y + 4))
            if choice.tier:
                tier = self._font.render(f"({choice.tier})", True, self._TIER)
                self._surface.blit(
                    tier, (row.x + 26 + name.get_width() + 6, row.y + 4)
                )
            sub = self._font.render(
                _fit_text(self._font, choice.subtitle, inner_w - 34),
                True, self._DIM,
            )
            self._surface.blit(sub, (row.x + 26, row.y + 4 + name.get_height() + 1))

            y += self._row_h + self._gap

        # ── Detail panel: what the hovered controller actually does ──────
        if hovered is not None:
            self._detail = hovered
        y += 6
        panel = pygame.Rect(x, y, inner_w, self._detail_h)
        pygame.draw.rect(self._surface, (18, 15, 24), panel, border_radius=4)
        pygame.draw.rect(self._surface, (70, 58, 88), panel, 1, border_radius=4)

        ty = panel.y + 7
        heading = ("Priorities" if self._detail.kind == "personality"
                   else "How it plays")
        hs = self._font.render(heading, True, self._DIM)
        self._surface.blit(hs, (panel.x + 8, ty))
        ty += hs.get_height() + 3
        for line in self._detail.detail:
            if ty + self._line_h > panel.bottom - 4:
                break
            ls = self._font.render(line, True, self._ACCENT)
            self._surface.blit(ls, (panel.x + 8, ty))
            ty += ls.get_height() + 2
        y += self._detail_h + 8

        cancel = pygame.Rect(x, y, inner_w, self._CANCEL_H)
        self._cancel_rect = cancel
        hover_c = cancel.collidepoint(self._mouse_pos)
        pygame.draw.rect(self._surface, (50, 30, 30) if hover_c else (30, 20, 20),
                         cancel, border_radius=4)
        pygame.draw.rect(self._surface, (160, 80, 80), cancel, 1, border_radius=4)
        cs = self._font.render("Cancel", True, (180, 100, 100))
        self._surface.blit(cs, (
            cancel.x + (cancel.width - cs.get_width()) // 2,
            cancel.y + (cancel.height - cs.get_height()) // 2,
        ))


def _fit_text(font: "Any", text: str, max_width: int) -> str:
    """Truncate ``text`` with an ellipsis until it fits ``max_width``."""
    if font.render(text, True, (0, 0, 0)).get_width() <= max_width:
        return text
    for cut in range(len(text) - 1, 0, -1):
        candidate = text[:cut].rstrip() + "…"
        if font.render(candidate, True, (0, 0, 0)).get_width() <= max_width:
            return candidate
    return "…"


# ─────────────────────────────────────────────────────────────────────────────
# _KingDialog — generic modal list-picker for the King system (Stage 10)
# ─────────────────────────────────────────────────────────────────────────────

class _KingDialog:
    """
    Generic modal list-picker reused across every step of the Coronation /
    Succession flow: choosing which King to Crown or Succeed to, choosing
    which cost to pay (1st Succession's piece-OR-Building choice), and
    choosing which Building (either cost path).

    Each row is ``(label, key)``; a row with ``key=None`` renders disabled
    and is unclickable — not used today (every row built by the AppController
    is always clickable) but kept for parity with _MercenaryPicker/_BuildPicker.

    ``handle_click(mx, my)`` returns the clicked row's key, "cancel", or
    None (missed everything).
    """

    _W = 300
    _ROW_H = 32
    _PADDING = 12
    _CANCEL_H = 28

    def __init__(
        self,
        surface: "pygame.Surface",
        font: "Any",
        title: str,
        rows: "list[tuple[str, Any]]",
        accent: tuple = (210, 180, 110),
        border: tuple = (170, 140, 70),
        width: "int | None" = None,
    ) -> None:
        self._surface = surface
        self._font = font
        self._title = title
        self._rows = rows
        self._accent = accent
        self._border = border
        # Per-dialog override of the 300 px default, for flows whose rows
        # carry more than a card name (the Ritual picker prints a
        # revelation state next to each entry).
        self._W = width or type(self)._W
        self._mouse_pos: tuple[int, int] = (0, 0)

        h = (self._PADDING
             + self._font.render("Ag", True, (0, 0, 0)).get_height() + 8   # title
             + max(len(rows), 1) * (self._ROW_H + 3)
             + 6 + self._CANCEL_H
             + self._PADDING)
        sw = surface.get_width()
        sh = surface.get_height()
        self._rect = pygame.Rect((sw - self._W) // 2, (sh - h) // 2, self._W, h)
        self._row_rects: list[pygame.Rect] = []
        self._cancel_rect: pygame.Rect | None = None

    def handle_click(self, mx: int, my: int) -> "str | None":
        for i, rect in enumerate(self._row_rects):
            if rect.collidepoint(mx, my):
                _label, key = self._rows[i]
                if key is not None:
                    return key
                return None
        if self._cancel_rect and self._cancel_rect.collidepoint(mx, my):
            return "cancel"
        if not self._rect.collidepoint(mx, my):
            return "cancel"
        return None

    def key_at(self, mx: int, my: int) -> "str | None":
        """
        RIGHT-click hit-test: the row's key at (mx, my), regardless of
        whether that row is enabled — used to inspect a King/Building card
        in the CardViewer without selecting it. Returns None off any row.
        """
        for i, rect in enumerate(self._row_rects):
            if rect.collidepoint(mx, my):
                return self._rows[i][1]
        return None

    def draw(self) -> None:
        overlay = pygame.Surface(
            (self._surface.get_width(), self._surface.get_height()), pygame.SRCALPHA
        )
        overlay.fill((0, 0, 0, 140))
        self._surface.blit(overlay, (0, 0))

        pygame.draw.rect(self._surface, (28, 22, 32), self._rect, border_radius=6)
        pygame.draw.rect(self._surface, self._border, self._rect, 2, border_radius=6)

        x = self._rect.x + self._PADDING
        y = self._rect.y + self._PADDING

        title_surf = self._font.render(self._title, True, self._accent)
        self._surface.blit(title_surf, (x, y))
        y += title_surf.get_height() + 8

        self._row_rects = []
        for label, key in self._rows:
            row_rect = pygame.Rect(x, y, self._W - self._PADDING * 2, self._ROW_H)
            self._row_rects.append(row_rect)
            enabled = key is not None

            hover = enabled and row_rect.collidepoint(self._mouse_pos)
            bg = (48, 38, 22) if hover else (36, 28, 18)
            pygame.draw.rect(self._surface, bg, row_rect, border_radius=4)
            border = self._border if enabled else (60, 55, 45)
            pygame.draw.rect(self._surface, border, row_rect, 1, border_radius=4)

            color = self._accent if enabled else (85, 78, 65)
            lbl_surf = self._font.render(label, True, color)
            # Trim rather than bleed past the row's right edge.
            max_lbl_w = row_rect.width - 12
            if lbl_surf.get_width() > max_lbl_w:
                trimmed = label
                while trimmed and self._font.render(
                    trimmed + "…", True, color,
                ).get_width() > max_lbl_w:
                    trimmed = trimmed[:-1]
                lbl_surf = self._font.render(trimmed + "…", True, color)
            ly = row_rect.y + (self._ROW_H - lbl_surf.get_height()) // 2
            self._surface.blit(lbl_surf, (row_rect.x + 6, ly))

            y += self._ROW_H + 3

        y += 6
        cancel_rect = pygame.Rect(x, y, self._W - self._PADDING * 2, self._CANCEL_H)
        self._cancel_rect = cancel_rect
        hover_c = cancel_rect.collidepoint(self._mouse_pos)
        pygame.draw.rect(self._surface, (50, 30, 30) if hover_c else (30, 20, 20),
                         cancel_rect, border_radius=4)
        pygame.draw.rect(self._surface, (160, 80, 80), cancel_rect, 1, border_radius=4)
        cs = self._font.render("Cancel", True, (180, 100, 100))
        self._surface.blit(cs, (
            cancel_rect.x + (cancel_rect.width - cs.get_width()) // 2,
            cancel_rect.y + (cancel_rect.height - cs.get_height()) // 2,
        ))
