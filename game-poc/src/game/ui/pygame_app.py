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
    Castle,
    DeclareRecompose,
    DiscardCard,
    DismissMonster,
    EndPreparation,
    EndTurn,
    MovePiece,
    PromotePawn,
    RepositionUnit,
    SummonMonster,
)
from game.core.phases import HAND_SIZE_LIMIT
from game.core.game import Game
from game.core.phases import Phase
from game.ui.board_view import BOARD_PIXEL_SIZE, BoardView
from game.ui.colors import BLACK
from game.ui.font import FTFont, load_font
from game.ui.hand_view import HandView
from game.ui.overlays import PromotionDialog, SidebarOverlay
from game.ui.state_io import export_state, import_state

# How many render frames the AI "thinks" before playing (visual pause)
_AI_THINK_FRAMES: int = 30  # 0.5 s at 60 fps


# ── Window geometry constants ──────────────────────────────────────────────
_BOARD_W: int = BOARD_PIXEL_SIZE          # 640
_BOARD_H: int = BOARD_PIXEL_SIZE          # 640
_HAND_H: int = HandView.HEIGHT            # 72  height of one hand row
_SIDEBAR_W: int = SidebarOverlay.SIDEBAR_WIDTH  # 200

WIN_W: int = _BOARD_W + _SIDEBAR_W       # 840
WIN_H: int = _BOARD_H + _HAND_H          # 712  (base — no debug row)
_WIN_H_DEBUG: int = _BOARD_H + _HAND_H * 2  # 784  (with black-hand debug row)

_SIDEBAR_X: int = _BOARD_W               # 640
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
        self._board_view = BoardView(
            surface=self._screen,
            font_large=self._font_large,
            font_small=self._font_small,
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

        # Index of card highlighted for discard (-1 = none)
        self._discard_highlight: int = -1

        # Stage 5: summon mode — card selected from hand awaiting vessel click
        # None = not in summon mode; str = card_id of the monster being summoned
        self._summon_card_id: str | None = None
        # Valid vessel positions for the currently selected summon card
        self._summon_vessel_positions: list[Position] = []

        # Stage 5: card info panel state
        # _hovered_card_id  — card the mouse is hovering over in the hand strip
        # _inspect_unit_pos — position of a board unit whose info is being shown
        #   (set by clicking a monster piece; takes priority over hand hover)
        self._hovered_card_id: str | None = None
        self._inspect_unit_pos: Position | None = None

        # Promotion dialog — created on demand, destroyed after selection
        self._promotion_dialog: PromotionDialog | None = None

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
                self._cancel_summon()
                self._deselect()
                self._inspect_unit_pos = None
                self._hovered_card_id = None
            elif event.key == pygame.K_d:
                # D key: dismiss the monster on the selected piece (if any)
                self._try_dismiss_selected()
            elif event.key == pygame.K_a:
                # A key: activate the first available ability on the selected/inspected piece
                self._try_activate_ability()

        elif event.type == pygame.MOUSEMOTION:
            mx, my = event.pos
            if self._promotion_dialog is not None:
                self._promotion_dialog.update_mouse(event.pos)
            self._sidebar.update_mouse(event.pos)
            # Track hand-card hover (only when not inspecting a board unit)
            if self._inspect_unit_pos is None:
                obs = self._current_obs()
                self._hovered_card_id = self._hand_view.card_from_click(
                    mx, my, list(obs.own_hand)
                )

        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            mx, my = event.pos
            if self._promotion_dialog is not None:
                self._handle_promotion_click(mx, my)
            else:
                self._handle_click(mx, my)

        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 3:
            # Right-click: inspect the unit at this board position (if any)
            mx, my = event.pos
            pos = self._board_view.pos_from_click(mx, my)
            if pos is not None:
                self._toggle_inspect(pos)

    # ── Click routing ─────────────────────────────────────────────────────

    def _handle_click(self, mx: int, my: int) -> None:
        """Route a left-click to board, sidebar buttons, or hand strip."""
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
        if btn == "recompose":
            self._do_recompose()
            return
        if btn is not None and btn.startswith("ability:"):
            ability_id = btn[len("ability:"):]
            self._do_activate_ability(ability_id)
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
            if self._summon_card_id is not None:
                # Summon mode: click on a valid vessel → fire SummonMonster
                if pos in self._summon_vessel_positions:
                    self._do_summon(self._summon_card_id, pos, active)
                else:
                    # Clicked elsewhere — cancel summon mode
                    self._cancel_summon()
                    self._show_toast("Summon cancelled.")
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
        • Non-monster cards: show a toast explaining they aren't usable yet.
        """
        from game.cards.card import MonsterCard

        # Toggle: clicking the already-selected card cancels summon mode.
        if self._summon_card_id == card_id:
            self._cancel_summon()
            return

        # Look up the card in the registry
        if card_id not in self._registry:
            self._show_toast(f"Unknown card: {card_id}")
            return

        card = self._registry.get(card_id)

        if not isinstance(card, MonsterCard):
            self._show_toast("Only MON cards can be summoned — Spells/Traps coming soon.")
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

    def _do_recompose(self) -> None:
        """
        Sidebar button: declare Recompose for the active human player.
        Only valid during PREPARATION when the preparation action hasn't been used.
        The card selection that follows is auto-resolved randomly by _auto_advance.
        """
        obs = self._current_obs()
        if obs.phase != Phase.PREPARATION:
            return
        active = obs.active_player
        if self._player_modes.get(active) == "ai":
            return
        self._execute_and_advance(DeclareRecompose(player_id=active), active)

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
        Right-click on the board: toggle the card-info inspect panel for the
        unit at ``pos`` (any unit — own or opponent).

        • If the same pos is already inspected → clear (toggle off).
        • If a different pos has a unit → set as inspected, clear hand hover.
        • If the square is empty → clear inspect.
        """
        obs = self._current_obs()
        has_unit = any(u.position == pos for u in obs.board.units)
        if not has_unit or pos == self._inspect_unit_pos:
            self._inspect_unit_pos = None
        else:
            self._inspect_unit_pos = pos
            self._hovered_card_id = None  # board inspect takes priority

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
        if self._promotion_dialog is not None:
            # Promotion: if it's an AI's turn, auto-pick queen
            obs = self._current_obs()
            active = obs.active_player
            if (obs.phase == Phase.PROMOTION_SELECTION and
                    self._player_modes.get(active) == "ai"):
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

        obs = self._current_obs()
        active = obs.active_player

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
        _stop = {Phase.PREPARATION, Phase.CHESS, Phase.PROMOTION_SELECTION, Phase.DISCARD}
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
                # Auto-resolve by picking a random SelectRecomposeCards from legal actions.
                # This handles both human and AI players — Recompose selection requires
                # no spatial decision, so random is acceptable for the PoC.
                legal = self._game.get_legal_actions(active)
                if not legal:
                    break
                import random as _random_rc
                action = _random_rc.choice(legal)
                try:
                    self._game.execute(action)
                except Exception:
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

        self._board_view.draw(
            observation=obs,
            selected_pos=self._selected_pos,
            legal_dests=board_legal_dests,
            castle_dests=self._castle_dests,
            checked_player=checked_player,
            summon_vessel_dests=board_summon_dests,
            inspect_pos=self._inspect_unit_pos,
        )

        # Sidebar (pass player modes, black-hand flag, and recompose button flag)
        active = obs.active_player
        active_is_human = self._player_modes.get(active) == "human"
        show_recompose = (
            obs.phase == Phase.PREPARATION
            and active_is_human
            and not self._game.state.get_player(active).preparation_action_used
        )

        # ── Card info panel ────────────────────────────────────────────────
        # Priority: board unit inspect > hand hover
        card_info = None
        ability_ids: list[str] = []
        _unit_statuses: tuple = ()

        if self._inspect_unit_pos is not None:
            unit_info = next(
                (u for u in obs.board.units if u.position == self._inspect_unit_pos),
                None,
            )
            if unit_info is not None and unit_info.monster_id is not None:
                try:
                    card_info = self._registry.get(unit_info.monster_id)
                except (KeyError, Exception):
                    card_info = None
                # Show ability buttons only for own pieces during CHESS
                if (
                    card_info is not None
                    and unit_info.owner == active
                    and obs.phase == Phase.CHESS
                    and active_is_human
                ):
                    ability_ids = list(unit_info.activatable_effects)
                # Always expose live statuses for the inspected unit
                _unit_statuses = unit_info.statuses if unit_info is not None else ()

        if card_info is None and self._hovered_card_id is not None:
            try:
                card_info = self._registry.get(self._hovered_card_id)
            except (KeyError, Exception):
                card_info = None

        self._sidebar.draw(
            obs,
            player_modes=self._player_modes,
            show_black_hand=self._show_black_hand,
            show_recompose_btn=show_recompose,
            card_info=card_info,
            ability_ids=ability_ids if ability_ids else None,
            unit_statuses=_unit_statuses if card_info is not None else None,
        )

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

        self._hand_view.draw(
            hand_obs,
            opponent_cards=black_cards,
            discard_mode=(obs.phase == Phase.DISCARD and active_is_human),
            selected_card_id=self._summon_card_id if active_is_human else None,
        )

        # Promotion dialog (on-board overlay)
        if self._promotion_dialog is not None:
            self._promotion_dialog.draw()

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
