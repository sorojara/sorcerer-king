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
from pathlib import Path

import pygame

from game.logger import get_logger

_log = get_logger(__name__)

from game.ai.random_bot import RandomBot
from game.cards.card import CardRegistry, load_registry_from_yaml
from game.chess.pieces import Position
from game.core.actions import (
    ActivateMonsterAbility,
    ActivateSpell,
    ActivateTrap,
    Castle,
    DeclareMercenary,
    DeclareRecompose,
    DiscardCard,
    DismissMonster,
    EndPreparation,
    EndTurn,
    MovePiece,
    PlaceMercenaryPiece,
    PlaceTrap,
    PromotePawn,
    RepositionUnit,
    SelectMercenaryCards,
    SelectRecomposeCards,
    StartConstruction,
    SummonMonster,
)
from game.core.phases import HAND_SIZE_LIMIT
from game.core.game import Game
from game.core.phases import Phase, PieceType
from game.mechanics.buildings import is_committed_builder
from game.ui.archetype_colors import aura_color_for
from game.ui.board_view import BOARD_OFFSET_X, BOARD_PIXEL_SIZE, BoardView, BuildingSpriteCache
from game.ui.colors import BLACK, TOOLTIP_TEXT, TOOLTIP_TITLE
from game.ui.font import FTFont, load_font
from game.ui.hand_view import HandView
from game.ui.event_log import LOG_HEIGHT, EventLogPanel
from game.ui.overlays import CardViewer, PromotionDialog, SidebarOverlay
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

WIN_W: int = _LEFT_SIDEBAR_W + _BOARD_W + _SIDEBAR_W   # 1060
WIN_H: int = _BOARD_H + _HAND_H          # 746  (base — no debug row)
_WIN_H_DEBUG: int = _BOARD_H + _HAND_H * 2  # 852  (with black-hand debug row)

_SIDEBAR_X: int = _LEFT_SIDEBAR_W + _BOARD_W   # 860 — right sidebar's left edge
_HAND_Y: int = _BOARD_H                  # 640  (base hand-strip top)

# Font sizes
_FONT_PX: int = 52    # chess glyph + large UI text
_FONT_SM_PX: int = 14  # small labels


class AppController:
    """
    Pygame application controller.

    Owns:
      • A Game instance (facade to the engine).
      • All UI views (BoardView, SidebarOverlay, HandView, PromotionDialog).
      • Input state (selected_pos, legal_dests, castle_dests).
      • Player mode config: each side can be "human" or "ai".

    When a side is set to "ai" its turns are played automatically by a
    RandomBot.  A short visual delay (_AI_THINK_FRAMES) lets the human
    see each AI move before the next action.
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

        # ── Card registry ─────────────────────────────────────────────────
        _data_dir = Path(__file__).parent.parent / "data"
        try:
            self._registry: CardRegistry = load_registry_from_yaml(_data_dir)
        except Exception:
            self._registry = CardRegistry()  # empty registry — graceful fallback

        # ── Game engine ──────────────────────────────────────────────────
        self._game = Game.new(seed=seed, registry=self._registry)

        # ── UI sub-views ─────────────────────────────────────────────────
        _building_sheet = _data_dir / "images" / "buildings" / "basic_buildings.png"
        _building_sprites = BuildingSpriteCache(_building_sheet)
        self._board_view = BoardView(
            surface=self._screen,
            font_large=self._font_large,
            font_small=self._font_small,
            building_sprites=_building_sprites,
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

        # ── Player modes & bots ───────────────────────────────────────────
        # "human" → human input; "ai" → RandomBot plays automatically
        self._player_modes: dict[str, str] = {"white": "human", "black": "human"}
        self._bots: dict[str, RandomBot] = {
            "white": RandomBot(seed=1),
            "black": RandomBot(seed=2),
        }
        for pid, bot in self._bots.items():
            bot.player_id = pid

        # Countdown until the AI plays its chosen action (visual delay)
        self._ai_think_countdown: int = 0

        # ── Input state ──────────────────────────────────────────────────
        self._selected_pos: Position | None = None
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

        # Auto-advance past phases that need no user input
        self._auto_advance()

    # ── Main loop ─────────────────────────────────────────────────────────

    def run(self) -> None:
        """Enter the main event-loop. Returns only when the user quits."""
        while True:
            for event in pygame.event.get():
                self._handle_event(event)

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
                if self._mercenary_picker is not None:
                    self._mercenary_picker = None
                    return
                if self._build_picker is not None:
                    self._build_picker = None
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

        elif event.type == pygame.MOUSEMOTION:
            self._mouse_pos = event.pos
            if self._promotion_dialog is not None:
                self._promotion_dialog.update_mouse(event.pos)
            if self._mercenary_picker is not None:
                self._mercenary_picker._mouse_pos = event.pos
            if self._build_picker is not None:
                self._build_picker._mouse_pos = event.pos
            self._sidebar.update_mouse(event.pos)
            self._card_viewer.update_mouse(event.pos)

        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mx, my = event.pos
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

        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 3:
            # Stage 6 (corrected): right-click sets the CardViewer's active
            # card. Priority: an "Active in this zone" list entry > a hand
            # card > a board square (unit / Trap-or-zone area).
            mx, my = event.pos
            zone_card_id = self._card_viewer.zone_entry_from_click(mx, my)
            if zone_card_id is not None:
                self._viewer_card_id = zone_card_id
                self._inspect_unit_pos = None
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
        if btn == "recompose":
            self._do_recompose()
            return
        if btn == "mercenary":
            self._do_mercenary()
            return
        if btn == "build":
            self._do_build()
            return
        if btn is not None and btn.startswith("trap:"):
            # Stage 6: "⚡ Activatable" section — fire a manual Trap directly.
            trap_instance_id = btn[len("trap:"):]
            self._do_activate_trap_by_id(trap_instance_id)
            return
        if btn is not None and btn.startswith("ability_at:"):
            # Stage 6: "⚡ Activatable" section — fire a monster ability on
            # ANY unit, not just the currently-inspected one.
            _, f, r, ability_id = btn.split(":", 3)
            self._do_activate_ability(ability_id, Position(int(f), int(r)))
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
            if self._build_card_id is not None:
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
                # Stage 6: two-step piece-targeting Spell mode.
                if self._spell_piece_source is None:
                    valid_sources = {
                        tuple(a.target["position"]) for a in self._spell_piece_actions
                    }
                    if (pos.file, pos.rank) in valid_sources:
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
            self._enter_targeting_mode(card_id, card.name, matches)
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
        self._targeting_action_by_pos = {}

    def _cancel_spell_piece_mode(self) -> None:
        """Exit the two-step piece-targeting Spell mode without firing."""
        self._spell_piece_card_id = None
        self._spell_piece_actions = []
        self._spell_piece_source = None

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
                token = f"ability_at:{a.unit_position.file}:{a.unit_position.rank}:{a.ability_id}"
                if token in seen:
                    continue
                seen.add(token)
                unit_info = next(
                    (u for u in obs.board.units if u.position == a.unit_position), None
                )
                piece_label = unit_info.piece_id if unit_info else str(a.unit_position)
                entries.append((
                    f"⚡ {piece_label} — {a.ability_id.replace('_', ' ')}", token,
                ))

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
        from game.mechanics.area import expand_area

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
    ) -> None:
        """
        Stage 6 — enter single-click targeting mode for a Trap or Spell.

        ``matches`` pairs every clickable square with the concrete Action
        that clicking it should fire (PlaceTrap or ActivateSpell).
        """
        if not matches:
            self._show_toast(f"{card_name}: no valid targets right now.")
            return
        self._cancel_summon()
        self._cancel_spell_piece_mode()
        self._targeting_card_id = card_id
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
        • A plain unit (no Monster) → nothing of its own to view, but it
          may be standing inside an active zone — falls through to the
          zone check below rather than clearing outright.
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

        # Either no unit here, or a plain (non-Monster) unit that has
        # nothing of its own to view — either way, check what's in the zone.
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
    ) -> None:
        """
        Execute ActivateMonsterAbility for ``ability_id`` on ``pos``.

        ``pos`` defaults to ``_inspect_unit_pos`` then ``_selected_pos``.
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
            self._game.execute(action)
        except Exception as exc:
            _log.warning("UI→ENG   FAILED  %s: %s", type(action).__name__, exc)
            self._deselect()
            self._show_toast(f"Illegal action: {exc}")
            return

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

    # ── Player mode toggle ────────────────────────────────────────────────

    def _toggle_mode(self, player_id: str) -> None:
        """Cycle the player's mode: human → ai → human …"""
        current = self._player_modes.get(player_id, "human")
        self._player_modes[player_id] = "ai" if current == "human" else "human"
        mode = self._player_modes[player_id]
        self._show_toast(f"{player_id.capitalize()} is now {mode.upper()}")
        # If just switched to AI and it's their turn, arm the countdown
        if mode == "ai":
            obs = self._current_obs()
            if obs.active_player == player_id and not self._game.is_over():
                self._ai_think_countdown = _AI_THINK_FRAMES

    def _toggle_black_hand(self) -> None:
        """Toggle the debug black-hand view and resize the window accordingly."""
        self._show_black_hand = not self._show_black_hand
        new_h = _WIN_H_DEBUG if self._show_black_hand else WIN_H
        self._screen = pygame.display.set_mode((WIN_W, new_h))
        # Rebind all views to the new surface
        self._board_view._surface = self._screen
        self._sidebar._surface = self._screen
        self._hand_view._surface = self._screen
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

        # Arm the countdown when it first becomes the AI's turn
        if self._ai_think_countdown <= 0:
            self._ai_think_countdown = _AI_THINK_FRAMES
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
          FINAL_DUEL  → resolve_final_duel_immediately() (Stage 3: no card duel)

        After EndTurn the engine sets Phase.START for the next player; the
        next loop iteration drives that through to PREPARATION automatically.

        We stop advancing when:
          • Phase is PREPARATION (player may play a card — Stage 4).
          • Phase is CHESS       (player must click a move).
          • Phase is PROMOTION_SELECTION (dialog is showing).
          • game.is_over()       (GAME_OVER or winner set)
        """
        _stop = {Phase.PREPARATION, Phase.CHESS, Phase.PROMOTION_SELECTION, Phase.DISCARD,
                 Phase.RECOMPOSE_SELECTION, Phase.MERCENARY_SELECTION, Phase.MERCENARY_PLACEMENT}
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

            if obs.phase == Phase.FINAL_DUEL:
                # Stage 3: no card-based duel — attacker wins immediately.
                # Full duel mechanic deferred to Stage 12.
                self._game.resolve_final_duel_immediately()
                break  # game is now over; render loop will show the banner

            elif obs.phase == Phase.START:
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
                    if tuple(a.target["position"]) == src_tuple
                ]
        elif obs.phase == Phase.MERCENARY_PLACEMENT and self._player_modes.get(obs.active_player) == "human":
            board_summon_dests = list(self._mercenary_placement_squares)

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
        )

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
            show_black_hand=self._show_black_hand,
            show_recompose_btn=show_recompose,
            show_mercenary_btn=show_mercenary,
            show_build_btn=show_build,
            show_territory=self._show_territory,
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
        # • Main row always shows the CURRENT HUMAN player's hand:
        #     – If the active player is human → show their cards.
        #     – If the active player is AI but the other player is human
        #       → show that human's cards (they're waiting; let them look).
        #     – Both AI → fall back to white (spectator mode).
        # • Debug row shows BLACK's raw hand only when the toggle is ON.

        if active_is_human:
            hand_obs = obs   # already built for active player
        else:
            # Active player is AI — find the human viewer (if any)
            from game.core.observation import build_observation as _build_obs
            opponent = "black" if active == "white" else "white"
            if self._player_modes.get(opponent) == "human":
                hand_obs = _build_obs(self._game.state, opponent)
            else:
                # Both AI — spectator mode: show white
                hand_obs = _build_obs(self._game.state, "white")

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

        # Game-over banner
        if self._game.is_over():
            self._draw_game_over_banner()

        # Toast notification (save/load feedback)
        if self._toast_ticks > 0:
            self._draw_toast()

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
