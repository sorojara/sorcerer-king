"""
Game Facade — Stage 1
======================

``Game`` is the single public API for the engine.

Usage (from phase0.md §20):
    game = Game.new(seed=938471)

    print(game.state.phase)        # Phase.PREPARATION
    print(game.get_legal_actions("white"))  # [EndPreparation, DeclareRecompose, ...]

    result = game.execute(action)
    print(result.events)

Architecture:
    Game owns:
        • GameState (the truth)
        • RulesEngine (the rules)
        • DeterministicRNG (the randomness)
        • CardRegistry (optional — for card-aware legal action generation)

    Everything else (UI, AI) uses Game through:
        game.execute(action)            → ExecutionResult
        game.get_legal_actions(pid)     → list[Action]
        game.get_observation(pid)       → Observation

This is the object the README §37 architecture diagram calls "Game Core".
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from game.logger import get_logger

_log = get_logger(__name__)

from game.chess.board import BoardState
from game.chess.chess_state import CastlingRights
from game.chess.pieces import ChessPiece, Position, UnitInstance
from game.core.actions import Action
from game.core.events import Event
from game.core.observation import Observation, build_observation
from game.core.phases import KingCardStatus, Phase, PieceType
from game.core.rng import DeterministicRNG
from game.core.rules import RulesEngine
from game.core.state import (
    BuildingPoolEntry,
    GameState,
    KingCardState,
    PlayerState,
)

if TYPE_CHECKING:
    from game.cards.card import CardRegistry


@dataclass
class ExecutionResult:
    """
    Returned by Game.execute() after a successful action.

    Contains the list of events produced and the current state snapshot.
    Used by UI (animate from events) and telemetry (log events).
    """

    events: list[Event]
    phase: Phase
    active_player: str
    winner: str | None


class Game:
    """
    The authoritative game controller.

    Owns the GameState, RulesEngine, and RNG.
    Coordinates turn lifecycle (auto-advancing START→DRAW→PREPARATION).

    Never expose GameState directly to the outside world.
    """

    # Fallback deck — used only when no registry is provided (e.g. unit tests).
    # When a registry IS provided, _build_deck_from_registry() is used instead
    # so each game samples a different subset of the full card pool.
    _DEFAULT_DECK: list[str] = [
        "dark_magician", "apprentice_mage", "arcane_sentinel",
        "wyrm_knight", "dragon_herald", "iron_vanguard",
        "blade_dancer", "shadow_wolf", "stone_golem", "ritual_acolyte",
        "arcane_reposition", "veil_of_stillness", "cursed_ground",
        "ritual_insight", "shatter_trap", "pit_trap", "ward_of_binding",
        "alarm_beacon", "counter_strike", "time_anchor",
    ]

    # Deck size for games built from the registry
    _DECK_SIZE: int = 20

    @classmethod
    def _build_deck_from_registry(
        cls,
        registry: "CardRegistry",
        rng: "DeterministicRNG",
    ) -> list[str]:
        """
        Build a 20-card deck by sampling from the full card pool.

        Distribution: half monsters, one quarter spells, one quarter traps.
        If there aren't enough cards in a category the remainder is filled from
        the other categories.  Cards are NOT shared between players — each call
        produces an independent random sample using the provided RNG.
        """
        monsters = [c.id for c in registry.all_monsters()]
        spells   = [c.id for c in registry.all_spells()]
        traps    = [c.id for c in registry.all_traps()]

        n_total    = cls._DECK_SIZE
        n_monsters = n_total // 2          # 10
        n_spells   = n_total // 4          # 5
        n_traps    = n_total - n_monsters - n_spells  # 5

        # Shuffle each pool independently using the game RNG for reproducibility
        rng.shuffle(monsters)
        rng.shuffle(spells)
        rng.shuffle(traps)

        # Clamp to available counts, backfill from monsters if short
        chosen_monsters = monsters[:n_monsters]
        chosen_spells   = spells[:n_spells]
        chosen_traps    = traps[:n_traps]

        shortfall = n_total - (len(chosen_monsters) + len(chosen_spells) + len(chosen_traps))
        if shortfall > 0:
            # Top up from whatever pool has extras
            extra = monsters[n_monsters:n_monsters + shortfall]
            chosen_monsters += extra

        return chosen_monsters + chosen_spells + chosen_traps

    def __init__(
        self,
        state: GameState,
        rng: DeterministicRNG,
        registry: "CardRegistry | None" = None,
    ) -> None:
        self._state = state
        self._rng = rng
        self._engine = RulesEngine(registry=registry)
        self._registry = registry

    # ── Factory ──────────────────────────────────────────────────────────

    @classmethod
    def new(
        cls,
        seed: int = 0,
        white_deck: list[str] | None = None,
        black_deck: list[str] | None = None,
        registry: "CardRegistry | None" = None,
        game_id: str | None = None,
    ) -> "Game":
        """
        Create a new game in the standard starting position.

        ``seed``       — determines all randomness (reproducible).
        ``white_deck`` — list of card IDs for white's deck (shuffled at start).
        ``black_deck`` — list of card IDs for black's deck.
        ``registry``   — card registry for legal-action validation (optional).
        ``game_id``    — unique ID for logging/replay; auto-generated if None.

        Initial state:
            • Standard chess starting position.
            • Active player: white.
            • Phase: PREPARATION (START→DRAW already auto-resolved at game start).
        """
        rng = DeterministicRNG(seed=seed)
        gid = game_id or str(uuid.uuid4())

        # Build each player's card pool independently.
        # When a registry is available: sample randomly from the full pool so
        # every game uses a different card selection.
        # Fallback (no registry, e.g. tests): use the fixed default deck.
        if white_deck is not None:
            white_cards = list(white_deck)
        elif registry is not None:
            white_cards = cls._build_deck_from_registry(registry, rng)
        else:
            white_cards = list(cls._DEFAULT_DECK)

        if black_deck is not None:
            black_cards = list(black_deck)
        elif registry is not None:
            black_cards = cls._build_deck_from_registry(registry, rng)
        else:
            black_cards = list(cls._DEFAULT_DECK)

        rng.shuffle(white_cards)
        rng.shuffle(black_cards)

        # Deal opening hand of 5 cards
        white_hand, white_deck_remaining = white_cards[:5], white_cards[5:]
        black_hand, black_deck_remaining = black_cards[:5], black_cards[5:]

        white = PlayerState(
            player_id="white",
            deck=white_deck_remaining,
            hand=white_hand,
            king_pool=[
                KingCardState(king_card_id="white-king-a"),
                KingCardState(king_card_id="white-king-b"),
                KingCardState(king_card_id="white-king-c"),
            ],
            castling_rights=CastlingRights(),
        )
        black = PlayerState(
            player_id="black",
            deck=black_deck_remaining,
            hand=black_hand,
            king_pool=[
                KingCardState(king_card_id="black-king-a"),
                KingCardState(king_card_id="black-king-b"),
                KingCardState(king_card_id="black-king-c"),
            ],
            castling_rights=CastlingRights(),
        )

        board = BoardState.make_standard_start()
        state = GameState(
            game_id=gid,
            turn_number=1,
            active_player="white",
            phase=Phase.PREPARATION,  # already past START/DRAW
            board=board,
            players={"white": white, "black": black},
            rng_seed=seed,
        )

        return cls(state=state, rng=rng, registry=registry)

    # ── Public API ────────────────────────────────────────────────────────

    @property
    def state(self) -> GameState:
        """
        Direct access to the full game state.
        For engine/test use only.  Never hand to AI; use get_observation() instead.
        """
        return self._state

    def execute(self, action: Action) -> ExecutionResult:
        """
        Execute ``action`` against the current game state.

        If the game is currently in Phase.START, auto-advance through
        START→DRAW→PREPARATION before dispatching the action.

        Returns an ExecutionResult with the events produced.
        Raises IllegalActionError if the action is invalid.
        """
        _log.debug(
            "EXECUTE  phase=%-14s player=%-6s action=%s",
            self._state.phase.value, self._state.active_player,
            repr(action),
        )

        # Auto-advance START → DRAW → PREPARATION
        events: list[Event] = []
        if self._state.phase == Phase.START:
            events.extend(self._auto_start_turn(self._state.active_player))

        try:
            new_events = self._engine.execute(self._state, action, self._rng)
        except Exception as exc:
            _log.warning("EXECUTE  FAILED  action=%s  error=%s", type(action).__name__, exc)
            raise

        events.extend(new_events)
        self._state.event_log.extend(new_events)  # engine already adds, avoid dup

        _log.debug(
            "EXECUTE  OK       phase=%-14s events=[%s]",
            self._state.phase.value,
            ", ".join(type(e).__name__ for e in new_events),
        )
        return ExecutionResult(
            events=new_events,
            phase=self._state.phase,
            active_player=self._state.active_player,
            winner=self._state.winner,
        )

    def get_legal_actions(self, player_id: str) -> list[Action]:
        """
        Return all legal actions the player may take right now.

        The registry is passed to the engine for card-aware filtering.
        """
        actions = self._engine.get_legal_actions(
            self._state, player_id, registry=self._registry
        )
        _log.debug(
            "LEGAL    phase=%-14s player=%-6s count=%d",
            self._state.phase.value, player_id, len(actions),
        )
        return actions

    def get_observation(self, player_id: str) -> Observation:
        """
        Return the Observation for ``player_id``.
        This is the only object that should ever be handed to an AI or the UI.
        """
        return build_observation(self._state, player_id, registry=self._registry)

    def is_over(self) -> bool:
        return self._state.is_game_over()

    def winner(self) -> str | None:
        return self._state.winner

    def resolve_final_duel_immediately(self) -> None:
        """
        Stage 3 shortcut: skip the card-based Final Duel and immediately
        declare the attacker as winner.

        In the full game (Stage 12+) this would be replaced by the card-draw
        duel mechanic.  For now, checkmate/king-capture → instant win.
        """
        if not self._state.is_final_duel_active():
            return
        duel = self._state.duel
        self._state.winner = duel.attacker
        self._state.phase = Phase.GAME_OVER

    def advance_to_preparation(self) -> None:
        """
        Drive the current player's turn from START → DRAW → PREPARATION.

        Called by the UI ``_auto_advance`` loop so it can stop at PREPARATION
        without consuming the preparation action (EndPreparation would do both
        the START advance AND close PREPARATION in one shot via execute()).

        Does nothing if the game is already past START (idempotent).
        """
        if self._state.phase == Phase.START:
            self._auto_start_turn(self._state.active_player)

    # ── Turn lifecycle auto-advance ──────────────────────────────────────

    def _auto_start_turn(self, player_id: str) -> list[Event]:
        """
        Auto-resolve START → DRAW → PREPARATION.
        Called by execute() when phase is START.
        """
        from game.core.events import CardDrawn, PhaseAdvanced, TurnStarted

        self._state.get_player(player_id).reset_turn_flags()
        events: list[Event] = [
            TurnStarted(player_id=player_id, turn_number=self._state.turn_number)
        ]
        # START → DRAW
        self._state.phase = Phase.DRAW
        events.append(PhaseAdvanced(
            player_id=player_id,
            from_phase=Phase.START,
            to_phase=Phase.DRAW,
        ))
        # DRAW: draw 1 card (recycles graveyard if deck is empty)
        ps = self._state.get_player(player_id)
        if not ps.deck and ps.graveyard:
            from game.core.events import DeckRecycled
            ps.deck = list(ps.graveyard)
            ps.graveyard.clear()
            self._rng.shuffle(ps.deck)
            events.append(DeckRecycled(player_id=player_id, card_count=len(ps.deck)))
        if ps.deck:
            card_id = ps.deck.pop(0)
            ps.hand.append(card_id)
            events.append(CardDrawn(player_id=player_id, card_id=card_id))
        # DRAW → PREPARATION
        self._state.phase = Phase.PREPARATION
        events.append(PhaseAdvanced(
            player_id=player_id,
            from_phase=Phase.DRAW,
            to_phase=Phase.PREPARATION,
        ))
        self._state.event_log.extend(events)
        return events
