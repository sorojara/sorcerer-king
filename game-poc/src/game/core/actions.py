"""
Action Hierarchy — Stage 0 / Stage 1
======================================

An Action is what a player (human or AI) *attempts*.
The engine validates and converts it into one or more Events.

Principle (from phase0.md):
    "Everything a human or AI can do must become an Action."

All actions carry a ``player_id`` so the engine can validate turn ownership.

v0.1 action set (locked for Stage 0):
    MovePiece           — chess move
    SummonMonster       — transform a Vessel piece into a Monster unit
    ActivateSpell       — play a Spell card (with optional target)
    PlaceTrap           — place a Trap card on the board
    ActivateTrap        — manually trigger a placed Trap (if allowed)
    StartConstruction   — begin building a structure via a Pawn
    ActivateRitual      — attempt a Ritual summon
    CoronateKing        — reveal and activate the first King card (free)
    ChangeKing          — Succession: switch to a different King card
    DeclareRecompose    — commit to Recompose (RNG fires after this)
    SelectRecomposeCards— follow-up: choose exactly N cards to return
    DeclareMercenary    — commit to hiring a Mercenary piece
    SelectMercenaryCards— follow-up: choose the Monster cards to sacrifice
    PlaceMercenaryPiece — follow-up: choose the square to place the new piece
    PromotePawn         — follow-up: choose piece type after back-rank advance
    EndPreparation      — explicitly pass the preparation action
    EndTurn             — signal end of the player's full turn

Reserved for later stages:
    DismissMonster, ActivateMonsterAbility, DestroyBuilding, FinalDuelAction
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from game.chess.pieces import Position


# ─────────────────────────────────────────────────────────────────────────────
# Base Action
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Action:
    """Base class for all player actions."""

    player_id: str

    def __str__(self) -> str:  # pragma: no cover
        return repr(self)


# ─────────────────────────────────────────────────────────────────────────────
# Chess Phase Actions
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class MovePiece(Action):
    """
    Standard chess move.
    Valid only during Phase.CHESS.
    Triggers check detection, possible Trap RadiusEntered events,
    and possibly a PROMOTION_SELECTION PendingDecision if a Pawn
    reaches the back rank.
    """

    source: Position
    target: Position


@dataclass
class Castle(Action):
    """
    Kingside (O-O) or queenside (O-O-O) castling.
    Valid only during Phase.CHESS when castling rights exist,
    the path is clear, and the King does not pass through check.
    Counts as the chess move for this turn.

    ``side`` is "kingside" or "queenside".
    """

    side: str  # "kingside" | "queenside"


# ─────────────────────────────────────────────────────────────────────────────
# Preparation Phase Actions
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SummonMonster(Action):
    """
    Transform an owned chess piece into a Monster unit.
    The piece at ``vessel_position`` must be a valid vessel for ``card_id``.
    Consumes the preparation action for this turn.
    """

    card_id: str
    vessel_position: Position


@dataclass
class ActivateSpell(Action):
    """
    Play a Spell card from hand.
    ``target`` is effect-specific: a Position, a piece ID, a list of
    positions, etc.  The card definition in the registry determines what
    target type is expected.
    Consumes the preparation action.
    """

    card_id: str
    target: Any = None


@dataclass
class PlaceTrap(Action):
    """
    Place a Trap card from hand onto the board at ``position``.
    The Trap becomes immediately visible (per design: Traps are never secret).
    Consumes the preparation action.
    """

    card_id: str
    position: Position


@dataclass
class ActivateTrap(Action):
    """
    Manually trigger a placed Trap (for Traps that allow manual activation).
    ``target`` is optional and depends on the Trap definition.
    This action may be available in the Reaction phase.
    """

    trap_instance_id: str
    target: Any = None


@dataclass
class StartConstruction(Action):
    """
    Begin constructing a Building.
    The Pawn at ``pawn_position`` is committed as the builder.
    ``building_card_id`` must exist in the player's Building Pool with
    at least one copy available.
    Consumes the preparation action.
    """

    pawn_position: Position
    building_card_id: str


@dataclass
class ActivateRitual(Action):
    """
    Attempt a Ritual summon.
    ``ritual_id`` identifies the Ritual in the player's pool.
    ``sacrifice_positions`` are the board squares of the pieces being consumed.
    Consumes the preparation action.
    """

    ritual_id: str
    sacrifice_positions: list[Position] = field(default_factory=list)


@dataclass
class CoronateKing(Action):
    """
    Reveal and activate the first King card (free action).
    Cannot be performed while in check.
    ``king_card_id`` must be HIDDEN in the player's King pool.
    Consumes the preparation action.
    """

    king_card_id: str


@dataclass
class ChangeKing(Action):
    """
    Succession: replace the active King policy with another one.
    Has an increasing cost each time.
    Cannot be performed while in check.
    ``king_card_id`` must be HIDDEN (never RETIRED).
    Consumes the preparation action.
    """

    king_card_id: str


@dataclass
class DeclareRecompose(Action):
    """
    Commit to the Recompose action.
    After this action the engine fires the RNG to determine how many
    cards the player must return (``N``).  A PendingDecision of type
    RECOMPOSE_CARDS is placed requiring ``N`` card IDs.
    The player does NOT know N before committing.
    Consumes the preparation action.
    """


@dataclass
class SelectRecomposeCards(Action):
    """
    Follow-up to DeclareRecompose.
    ``card_ids`` must exactly match the required count from PendingDecision.
    These cards are returned to the deck; the deck is shuffled; the player
    draws the same number of replacements.
    """

    card_ids: list[str] = field(default_factory=list)


@dataclass
class DeclareMercenary(Action):
    """
    Commit to hiring a Mercenary piece.

    The player chooses the piece type they want (pawn / knight / bishop /
    rook / queen).  The engine validates that the player has enough Monster
    cards in hand to pay the cost and sets up a MERCENARY_SELECTION
    PendingDecision so the player can choose which Monster cards to sacrifice.

    Cost table (from README §9.2):
        4 Monster cards → Pawn
        6 Monster cards → Knight, Bishop, or Rook
        8 Monster cards → Queen

    Consumes the preparation action.
    """

    piece_type: str   # "pawn" | "knight" | "bishop" | "rook" | "queen"


@dataclass
class SelectMercenaryCards(Action):
    """
    Follow-up to DeclareMercenary.
    ``card_ids`` must be exactly the required Monster cards to sacrifice.
    Those cards are removed from the game (NOT the graveyard).
    Advances to MERCENARY_PLACEMENT so the player can pick the target square.
    """

    card_ids: list[str] = field(default_factory=list)


@dataclass
class PlaceMercenaryPiece(Action):
    """
    Follow-up to SelectMercenaryCards.
    ``position`` must be an empty square in the player's own first two ranks.
    Places the new chess piece there; completes the Mercenary action.
    """

    position: Position


@dataclass
class PromotePawn(Action):
    """
    Follow-up to a Pawn reaching the back rank.
    Resolves the PROMOTION_SELECTION PendingDecision.
    ``piece_type`` must be one of: queen, rook, bishop, knight.
    """

    position: Position
    piece_type: str  # "queen" | "rook" | "bishop" | "knight"


@dataclass
class EndPreparation(Action):
    """
    Explicitly pass the preparation action.
    Advances the game to Phase.CHESS.
    """


@dataclass
class DiscardCard(Action):
    """
    Discard one card from hand to the graveyard.
    Valid only during Phase.DISCARD (the end-of-turn hand-size enforcement
    phase).  The player must submit one DiscardCard per card above the
    hand-size limit (6) until the hand is within bounds.
    """

    card_id: str


@dataclass
class EndTurn(Action):
    """
    Signal that the current player has completed their full turn.
    Valid only during Phase.END (or Phase.REACTION / Phase.CHESS in
    exceptional states, or after all required discards are done).
    The engine will advance to the next player's START phase.
    """


# ─────────────────────────────────────────────────────────────────────────────
# Reserved stubs (not yet implemented in Stage 0)
# Added in Stage 1: Castle (above)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RepositionUnit(Action):
    """
    Follow-up to a blade_dancer (or similar) after-capture reposition effect.
    Resolves the REPOSITION PendingDecision: move the piece at ``from_position``
    to ``to_position`` (must be in PendingDecision.options).
    Stage 5+ only.
    """

    from_position: Position
    to_position: Position


@dataclass
class DismissMonster(Action):
    """
    Return a Monster card to hand/deck and restore its Vessel piece.
    Stage 5+ only.
    """

    unit_position: Position


@dataclass
class ActivateMonsterAbility(Action):
    """
    Trigger a special activated ability on a Monster.
    Stage 5+ only.
    """

    unit_position: Position
    ability_id: str
    target: Any = None


@dataclass
class FinalDuelAction(Action):
    """
    Any action taken during the Final Duel.
    Stage 12+ only.
    """

    duel_action_type: str
    parameters: dict[str, Any] = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# Union type alias for type-checkers
# ─────────────────────────────────────────────────────────────────────────────

# All action types valid in Stage 0 normal play.
STAGE0_ACTIONS = (
    MovePiece,
    SummonMonster,
    ActivateSpell,
    PlaceTrap,
    ActivateTrap,
    StartConstruction,
    ActivateRitual,
    CoronateKing,
    ChangeKing,
    DeclareRecompose,
    SelectRecomposeCards,
    DeclareMercenary,
    SelectMercenaryCards,
    PlaceMercenaryPiece,
    PromotePawn,
    EndPreparation,
    DiscardCard,
    EndTurn,
)
