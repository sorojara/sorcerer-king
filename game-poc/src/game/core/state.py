"""
GameState — Stage 0 / Stage 1
==============================

The complete, authoritative, omniscient game state.

Rule: the GameState is NEVER handed directly to an AI or the UI.
      It is translated into an Observation first (see observation.py).

Top-level shape:

    GameState
      ├── board: BoardState
      ├── players: dict[str, PlayerState]
      ├── traps: list[TrapInstance]
      ├── buildings: list[BuildingInstance]
      ├── pending_decision: PendingDecision | None
      ├── duel: DuelState | None
      └── rng_state: stored in DeterministicRNG

PendingDecision exists for two-step actions (Recompose, Promotion, …)
where the engine must wait for a follow-up player input.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from game.chess.board import BoardState
from game.chess.chess_state import CastlingRights
from game.core.phases import (
    ConstructionStatus,
    DecisionType,
    FinalDuelType,
    KingCardStatus,
    Phase,
    RevelationState,
)


# ─────────────────────────────────────────────────────────────────────────────
# PendingDecision
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PendingDecision:
    """
    Represents a mid-turn decision that must be resolved before the game
    can advance to the next phase.

    Examples:
        • After DeclareRecompose — player must choose exactly N cards.
        • After a Pawn reaches back-rank — player must choose promotion type.
        • After certain Spell effects — player must choose a target.
    """

    player_id: str
    decision_type: DecisionType
    options: list[Any] = field(default_factory=list)  # valid choices
    min_choices: int = 1
    max_choices: int = 1
    context: dict[str, Any] = field(default_factory=dict)  # extra data for resolver


# ─────────────────────────────────────────────────────────────────────────────
# Ritual / King / Building sub-states
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RitualState:
    """Tracks one ritual card in a player's pool and its revelation progress."""

    ritual_id: str
    revelation: RevelationState = RevelationState.SEALED


@dataclass
class KingCardState:
    """Tracks one King card in a player's pool."""

    king_card_id: str
    status: KingCardStatus = KingCardStatus.HIDDEN


@dataclass
class BuildingPoolEntry:
    """
    One entry in a player's pre-match Building Pool.
    The pool is public information.
    """

    building_card_id: str
    copies_available: int


@dataclass
class TrapInstance:
    """
    A placed Trap on the board.
    Traps are NOT hidden — their identity, position, and radius are public.
    """

    id: str              # unique runtime ID, e.g. "trap-white-001"
    owner: str
    card_id: str         # references a trap definition in the card registry
    position: "Position"  # type: ignore[name-defined]  # forward ref ok
    radius: int          # default 1 = 3×3 area centered on position
    trigger_condition: str  # short tag: "enter_radius", "capture", etc.
    activated: bool = False
    shape: str = "square"        # Stage 6: mirrors TrapCard.shape
    # Stage 6: mirrors TrapCard.charges — None = reusable indefinitely,
    # otherwise decremented by mechanics.monsters._fire_trap() and the Trap
    # is removed from state.traps once it reaches 0 (pit_trap: charges=1).
    charges: int | None = None


@dataclass
class BuildingInstance:
    """A Building placed (or under construction) on the board."""

    id: str
    owner: str
    building_card_id: str
    position: "Position"  # type: ignore[name-defined]
    status: ConstructionStatus = ConstructionStatus.UNDER_CONSTRUCTION
    builder_piece_id: str | None = None
    remaining_turns: int = 0  # construction countdown; 0 when complete


@dataclass
class DuelState:
    """
    Placeholder for Final Duel bookkeeping.
    Populated when a FinalDuelType condition is triggered.
    Full implementation arrives in Stage 12.
    """

    duel_type: FinalDuelType
    attacker: str
    defender: str
    round_number: int = 1
    # future fields: arena_board, support_scores, …


@dataclass
class MoveSnapshot:
    """
    Stage 6 — a reversible-transaction snapshot of one MovePiece, kept so
    ``time_anchor`` can restore the board to how it looked immediately
    before that move.

    This is a pragmatic alternative to a fully generic undoable event log:
    rather than defining an inverse for every Event type, we snapshot the
    handful of mutable pieces of state a MovePiece can touch and restore
    them wholesale.  GameState keeps at most ONE of these per player (the
    player's own most recent move) — a new move for that player overwrites
    the previous snapshot, which is exactly "last move" semantics.

    ``board_before``              — deep copy of the board immediately
                                     before the move (captures piece
                                     positions, monster_id, statuses —
                                     reverting it also un-destroys any
                                     Monster that move captured, for free).
    ``victim_captured_len_before`` — length of the defender's (the
                                     opponent-of-mover's) ``captured_pieces``
                                     list before the move, so truncating
                                     back to it undoes the bookkeeping
                                     append from a capture, if any.
    ``castling_rights_before``    — deep copy of the mover's own castling
                                     rights before the move.
    ``en_passant_before``         — state.en_passant_target before the move.
    ``triggered_duel``            — True if this move triggered a Final
                                     Duel (King capture / checkmate /
                                     stalemate).  time_anchor refuses to
                                     cancel such a move (per its own
                                     design — see traps.yaml).
    """

    player_id: str
    turn_number: int
    source: "Position"       # type: ignore[name-defined]
    target: "Position"       # type: ignore[name-defined]
    board_before: "BoardState"
    victim_captured_len_before: int
    castling_rights_before: CastlingRights
    en_passant_before: "Position | None"  # type: ignore[name-defined]
    triggered_duel: bool = False


# ─────────────────────────────────────────────────────────────────────────────
# PlayerState
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PlayerState:
    """
    All private and public information belonging to one player.

    ``deck``     — ordered list of card IDs (top of deck = index 0).
    ``hand``     — card IDs currently in hand.
    ``graveyard``— card IDs destroyed / discarded.
    ``king_pool``— the three King cards (hidden until Coronation).
    ``active_king`` — the card_id of the currently active King, or None.
    ``ritual_pool`` — Ritual cards (hidden until revealed).
    ``building_pool`` — Building Pool (public).
    ``preparation_action_used`` — True once a major prep action is taken this turn.
    ``chess_move_used`` — True once a chess move is made this turn.
    """

    player_id: str

    deck: list[str] = field(default_factory=list)
    hand: list[str] = field(default_factory=list)
    graveyard: list[str] = field(default_factory=list)

    king_pool: list[KingCardState] = field(default_factory=list)
    active_king: str | None = None
    retired_kings: list[str] = field(default_factory=list)
    # Stage 10: the archetype randomly assigned to this player at setup —
    # guarantees their King Pool contains that archetype's "home" King (see
    # mechanics/kings.py assign_random_king_pool). Internal bookkeeping only,
    # not exposed via Observation (own_king_pool already reveals card IDs to
    # the owner; the archetype label adds nothing an opponent could exploit).
    archetype: str | None = None

    ritual_pool: list[RitualState] = field(default_factory=list)

    building_pool: list[BuildingPoolEntry] = field(default_factory=list)

    # Castling rights (Stage 1). Start both sides available.
    castling_rights: CastlingRights = field(default_factory=CastlingRights)

    # Captured enemy pieces (chess piece IDs, e.g. "white-pawn-a2").
    # Kept SEPARATE from graveyard so captured pieces are never recycled into
    # the card deck.  graveyard is cards-only; captured_pieces is pieces-only.
    captured_pieces: list[str] = field(default_factory=list)

    # Turn-scoped flags, reset at start of each player's turn.
    preparation_action_used: bool = False
    chess_move_used: bool = False
    # Stage 10: Grave-Crowned King's graveyard_recycle policy only fires for
    # the FIRST allied Monster destroyed each turn (README-style "once per
    # turn" cap — see mechanics/kings.py maybe_recycle_destroyed_monster).
    king_recycle_used_this_turn: bool = False

    def reset_turn_flags(self) -> None:
        """Call at the start of this player's turn."""
        self.preparation_action_used = False
        self.chess_move_used = False
        self.king_recycle_used_this_turn = False

    def is_in_check(self) -> bool:
        """
        Check status is computed by the RulesEngine, not stored here.
        This flag is a convenience cache; updated by the engine after each move.
        """
        return self._in_check

    _in_check: bool = field(default=False, repr=False)

    def set_check(self, value: bool) -> None:
        self._in_check = value


# ─────────────────────────────────────────────────────────────────────────────
# GameState
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class GameState:
    """
    The single source of truth for the entire game.

    This object is PRIVATE to the engine.  Observers receive an Observation.

    ``game_id``         — unique match identifier (for logging / replay).
    ``turn_number``     — increments after each full round (both players moved).
    ``active_player``   — "white" or "black".
    ``phase``           — current phase of the active player's turn.
    ``board``           — the 8×8 board with all units, traps, buildings.
    ``players``         — dict mapping player_id → PlayerState.
    ``traps``           — all placed TrapInstances (public info).
    ``buildings``       — all BuildingInstances on the board.
    ``pending_decision``— set when an action requires a follow-up response.
    ``duel``            — populated if a Final Duel has been triggered.
    ``winner``          — set to a player_id when the game ends.
    ``rng_seed``        — the original seed (logged for reproduction).
    ``event_log``       — all Events produced this game (for replay / telemetry).
    """

    game_id: str
    turn_number: int
    active_player: str
    phase: Phase

    board: BoardState

    players: dict[str, PlayerState] = field(default_factory=dict)

    traps: list[TrapInstance] = field(default_factory=list)
    buildings: list[BuildingInstance] = field(default_factory=list)

    pending_decision: PendingDecision | None = None
    duel: DuelState | None = None

    winner: str | None = None

    # En passant target square (Stage 1).
    # Set to the square behind a double-pushed pawn immediately after that push.
    # Cleared at the start of every subsequent move.
    en_passant_target: "Position | None" = None  # type: ignore[name-defined]

    rng_seed: int = 0
    event_log: list = field(default_factory=list)  # list[Event] — avoids circular import

    # Stage 6: one MoveSnapshot per player, keyed by player_id — their own
    # most recent MovePiece.  Powers time_anchor's cancel_move effect.
    move_history: "dict[str, MoveSnapshot | None]" = field(default_factory=dict)

    def get_player(self, player_id: str) -> PlayerState:
        if player_id not in self.players:
            raise KeyError(f"Unknown player: {player_id!r}")
        return self.players[player_id]

    def opponent_of(self, player_id: str) -> str:
        """Return the other player's ID (assumes exactly two players)."""
        ids = list(self.players.keys())
        return ids[1] if ids[0] == player_id else ids[0]

    def is_game_over(self) -> bool:
        return self.winner is not None or self.phase == Phase.GAME_OVER

    def is_final_duel_active(self) -> bool:
        return self.duel is not None and self.phase == Phase.FINAL_DUEL

    def deep_copy(self) -> "GameState":
        """Return a full deep copy suitable for AI tree-search / simulation."""
        return copy.deepcopy(self)
