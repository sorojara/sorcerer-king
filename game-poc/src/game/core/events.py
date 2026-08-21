"""
Event Hierarchy — Stage 0
==========================

An Event is what *actually happened* as a result of an Action.

The engine converts Actions → Events → new GameState.

This separation enables:
    • replay / save games (log the events, not the state delta)
    • animations (drive UI from event stream)
    • AI analysis (search over event trees)
    • debugging (print the event log, see exactly what happened)

Pattern:
    Action ──► Validator ──► RulesEngine ──► list[Event] ──► GameState'

Every event carries ``player_id`` (who caused it) and descriptive fields.
Events are immutable records — they are never mutated after creation.

v0.1 events (minimal required set for Stage 0 invariant tests):

    Turn / Phase lifecycle:
        TurnStarted, PhaseAdvanced, TurnEnded
        PrepActionUsed, ChessMoveUsed

    Board movement:
        PieceMoved, PieceCaptured, PiecePromoted

    Monster / card:
        MonsterSummoned, SpellActivated, TrapPlaced, TrapTriggered
        MonsterDismissed, CardDrawn, CardsReturnedToDeck

    Recompose:
        RecomposeInitiated, RecomposeResolved

    Mercenary:
        MercenaryContractFired, MercenaryPiecePlaced

    Building:
        ConstructionStarted, BuildingCompleted, BuildingDestroyed

    King:
        KingCoronated, KingSuccession

    Ritual:
        RitualRevelationChanged, RitualActivated

    Check / Duel:
        CheckDetected, CheckResolved, FinalDuelTriggered
        GameOver

    Final Duel (Stage 12):
        DuelSupportSpent, DuelStrikeResolved, DuelRoundAdvanced,
        RoyalEscapeTriggered
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from game.chess.pieces import Position
from game.core.phases import (
    FinalDuelType,
    KingCardStatus,
    Phase,
    RevelationState,
)


# ─────────────────────────────────────────────────────────────────────────────
# Base Event
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Event:
    """Base class for all game events."""


# ─────────────────────────────────────────────────────────────────────────────
# Turn / Phase Lifecycle
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TurnStarted(Event):
    player_id: str
    turn_number: int


@dataclass(frozen=True)
class PhaseAdvanced(Event):
    player_id: str
    from_phase: Phase
    to_phase: Phase


@dataclass(frozen=True)
class TurnEnded(Event):
    player_id: str
    turn_number: int


@dataclass(frozen=True)
class PrepActionUsed(Event):
    player_id: str
    action_type: str  # class name of the action that consumed preparation


@dataclass(frozen=True)
class ChessMoveUsed(Event):
    player_id: str


# ─────────────────────────────────────────────────────────────────────────────
# Board Movement
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PieceMoved(Event):
    piece_id: str
    owner: str
    source: Position
    target: Position


@dataclass(frozen=True)
class PieceCaptured(Event):
    piece_id: str
    owner: str
    captured_at: Position
    captured_by_piece_id: str


@dataclass(frozen=True)
class PiecePromoted(Event):
    piece_id: str
    owner: str
    position: Position
    from_type: str   # "pawn"
    to_type: str     # "queen" | "rook" | "bishop" | "knight"
    new_piece_id: str


@dataclass(frozen=True)
class CastlingPerformed(Event):
    """
    Both the King and the Rook moved as part of castling.
    Emitted once; contains both moves for replay / UI.
    """

    player_id: str
    side: str             # "kingside" | "queenside"
    king_from: Position
    king_to: Position
    rook_from: Position
    rook_to: Position


@dataclass(frozen=True)
class EnPassantCapture(Event):
    """
    A pawn captured an enemy pawn en passant.
    The captured pawn was NOT at ``target`` but one rank behind it.
    """

    player_id: str
    pawn_piece_id: str
    source: Position
    target: Position          # square the capturing pawn lands on
    captured_pawn_piece_id: str
    captured_pawn_at: Position  # where the captured pawn actually was


@dataclass(frozen=True)
class StalemateDetected(Event):
    """Player has no legal moves and is NOT in check — stalemate."""
    player_id: str


@dataclass(frozen=True)
class CaptureBlocked(Event):
    """
    A capture attempt was blocked by the target's capture_protection shield.
    The attacker's chess move is consumed; no piece changes owner.
    """
    attacker_piece_id: str
    defender_piece_id: str
    position: Position
    shields_remaining: int   # charges left on the defender after this block


@dataclass(frozen=True)
class PiecePushed(Event):
    """
    A ``push_unit`` monster effect pushed a piece instead of capturing it
    (storm_dragon).  No ownership change occurs; the pushed piece is
    relocated to an empty adjacent square.
    """
    piece_id: str
    owner: str
    source: Position
    target: Position
    pushed_by_piece_id: str


@dataclass(frozen=True)
class PiecePulled(Event):
    """A ``pull_unit`` Spell/Trap effect pulled a piece one square toward a
    reference point (magnetic_reversal, gravity_well)."""
    piece_id: str
    owner: str
    source: Position
    target: Position


@dataclass(frozen=True)
class Retaliated(Event):
    """
    A ``retaliate`` monster effect destroyed the attacker when its unit
    was captured (thorn_boar).  Both pieces end up destroyed.
    """
    defender_piece_id: str
    attacker_piece_id: str
    position: Position


# ─────────────────────────────────────────────────────────────────────────────
# Monster / Card Events
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class MonsterSummoned(Event):
    player_id: str
    card_id: str
    vessel_piece_id: str
    position: Position


@dataclass(frozen=True)
class MonsterDestroyed(Event):
    """
    The Monster on a vessel was destroyed (vessel is also lost).
    Emitted instead of (or in addition to) PieceCaptured when a monster unit is captured.
    """
    player_id: str          # owner of the destroyed monster
    card_id: str            # which monster card was lost
    vessel_piece_id: str
    position: Position
    destroyed_by_piece_id: str | None = None


@dataclass(frozen=True)
class MonsterDismissed(Event):
    """Monster was voluntarily dismissed — vessel restored to board."""
    player_id: str
    card_id: str
    vessel_piece_id: str
    position: Position


@dataclass(frozen=True)
class MonsterAbilityActivated(Event):
    """A Monster's activated ability was triggered by its owner."""
    player_id: str
    card_id: str
    ability_id: str
    unit_position: "Position"
    target: "Any" = None


@dataclass(frozen=True)
class SpellActivated(Event):
    player_id: str
    card_id: str
    target: Any = None


@dataclass(frozen=True)
class TrapPlaced(Event):
    player_id: str
    card_id: str
    trap_instance_id: str
    position: Position
    radius: int


@dataclass(frozen=True)
class TrapRadiusEntered(Event):
    trap_instance_id: str
    entering_piece_id: str
    square: Position


@dataclass(frozen=True)
class TrapTriggered(Event):
    trap_instance_id: str
    triggering_piece_id: str | None = None


@dataclass(frozen=True)
class EnemyCardRevealed(Event):
    """
    Stage 6 — a Trap/Spell effect peeked at one of the opponent's hand
    cards (alarm_beacon).  ``player_id`` is who gets to know
    ``revealed_card_id`` (the peeker, not the hand's owner) — a one-time,
    non-persistent reveal.  This does NOT change what Observation exposes
    going forward (HIDDEN_INFO stays intact for AI decision-making); a
    human player learns it only by reading this event (e.g. a UI toast).
    """
    player_id: str
    revealed_card_id: str


@dataclass(frozen=True)
class MoveCancelled(Event):
    """
    Stage 6 — time_anchor undid a player's most recent MovePiece.  The
    board was restored to a pre-move snapshot; this event records what
    was undone for replay/telemetry (the board mutation itself isn't
    re-derivable from the event log alone, since it's a snapshot restore
    rather than an inverse of individual events).
    """
    cancelled_player: str
    source: Position
    target: Position
    turn_number: int


@dataclass(frozen=True)
class CardDrawn(Event):
    player_id: str
    card_id: str


@dataclass(frozen=True)
class CardDiscarded(Event):
    player_id: str
    card_id: str


@dataclass(frozen=True)
class DeckRecycled(Event):
    """Graveyard was shuffled back into the deck (empty-deck fallback)."""
    player_id: str
    card_count: int   # how many cards were recycled


@dataclass(frozen=True)
class CardsReturnedToDeck(Event):
    player_id: str
    card_ids: tuple[str, ...]


@dataclass(frozen=True)
class DeckInspected(Event):
    """
    arcane_archivist's ``inspect_top_deck`` — the owner peeked at the top
    ``card_ids`` of their own deck (in current top-to-bottom order). Private
    to ``player_id``, same non-persistent-reveal convention as
    EnemyCardRevealed — Observation's hidden-info filtering is unaffected;
    a human player learns it only by reading this event (a UI toast).
    Immediately followed by a REORDER_DECK PendingDecision letting the
    owner choose the new order for these same cards.
    """
    player_id: str
    card_ids: tuple[str, ...]


@dataclass(frozen=True)
class DeckReordered(Event):
    """arcane_archivist's ``reorder_top_deck`` resolution — the top of the
    owner's deck now reads ``card_ids`` (top-to-bottom)."""
    player_id: str
    card_ids: tuple[str, ...]


@dataclass(frozen=True)
class GraveyardInspected(Event):
    """
    grave_scholar's ``graveyard_inspect`` — the owner reviewed the full
    contents of their own Graveyard on summon (private; same
    non-persistent-reveal convention as EnemyCardRevealed / DeckInspected).
    """
    player_id: str
    card_ids: tuple[str, ...]


# ─────────────────────────────────────────────────────────────────────────────
# Recompose Events
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RecomposeInitiated(Event):
    """Fired when DeclareRecompose is executed; RNG has determined N."""

    player_id: str
    required_count: int  # how many cards must be returned


@dataclass(frozen=True)
class RecomposeResolved(Event):
    player_id: str
    returned_card_ids: tuple[str, ...]
    drawn_card_ids: tuple[str, ...]


# ─────────────────────────────────────────────────────────────────────────────
# Mercenary Events
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class MercenaryContractFired(Event):
    """Fired when SelectMercenaryCards is executed; cards are removed from game."""

    player_id: str
    sacrificed_card_ids: tuple[str, ...]   # Monster cards removed from game
    piece_type: str                         # piece type the player is purchasing


@dataclass(frozen=True)
class MercenaryPiecePlaced(Event):
    """Fired when PlaceMercenaryPiece is executed; new piece is on the board."""

    player_id: str
    piece_type: str
    position: Position
    piece_id: str                           # canonical ID of the new piece


# ─────────────────────────────────────────────────────────────────────────────
# Building Events
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ConstructionStarted(Event):
    player_id: str
    building_card_id: str
    building_instance_id: str
    position: Position
    builder_piece_id: str


@dataclass(frozen=True)
class SquareFrozen(Event):
    """A board square was frozen by a monster effect (no entry/exit for N turns)."""
    position: Position
    duration_turns: int
    caused_by_piece_id: str | None = None


@dataclass(frozen=True)
class SquareScorched(Event):
    """A board square was scorched by a monster effect (ember_drake)."""
    position: Position
    duration_turns: int
    caused_by_piece_id: str | None = None


@dataclass(frozen=True)
class BuildingCompleted(Event):
    player_id: str
    building_instance_id: str
    building_card_id: str
    position: Position


@dataclass(frozen=True)
class BuildingDestroyed(Event):
    building_instance_id: str
    position: Position
    destroyed_by: str | None = None  # piece_id or None


@dataclass(frozen=True)
class BuildingDisabled(Event):
    """saboteur's ``disable_building`` — an enemy Building's aura/support
    effects are suspended until the start of its owner's next turn."""
    building_instance_id: str
    position: Position
    disabled_by_piece_id: str


@dataclass(frozen=True)
class BuilderRestored(Event):
    """guild_foreman's ``restore_builder`` — an adjacent Pawn that already
    spent its once-per-match Builder token regains it."""
    player_id: str
    piece_id: str
    restored_by_piece_id: str


# ─────────────────────────────────────────────────────────────────────────────
# King Events
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class KingCoronated(Event):
    player_id: str
    king_card_id: str


@dataclass(frozen=True)
class KingSuccession(Event):
    player_id: str
    retired_king_card_id: str
    new_king_card_id: str
    # Stage 10: cost payment for telemetry (README §42 "King Successions").
    sacrificed_piece_id: str | None = None
    destroyed_building_id: str | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Ritual Events
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RitualRevelationChanged(Event):
    player_id: str
    ritual_id: str
    old_state: RevelationState
    new_state: RevelationState


@dataclass(frozen=True)
class RitualActivated(Event):
    player_id: str
    ritual_id: str
    sacrificed_piece_ids: tuple[str, ...]
    summoned_monster_id: str


# ─────────────────────────────────────────────────────────────────────────────
# Check / Duel / Game Over
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CheckDetected(Event):
    """The named player's King is now in check."""

    player_id: str


@dataclass(frozen=True)
class CheckResolved(Event):
    """The named player's King is no longer in check."""

    player_id: str


@dataclass(frozen=True)
class FinalDuelTriggered(Event):
    """
    King capture or checkmate reached — game enters FINAL_DUEL phase.
    Actual winner is NOT set here (the Duel decides it).
    """

    attacker: str
    defender: str
    duel_type: FinalDuelType
    trigger_position: Position | None = None  # square of King capture, if applicable


@dataclass(frozen=True)
class GameOver(Event):
    winner: str
    reason: str  # "final_duel_victory" | "duel_forfeit" | …


# ─────────────────────────────────────────────────────────────────────────────
# Final Duel actions (Stage 12) — see mechanics/duel.py
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class DuelSupportSpent(Event):
    """A queued DuelSupportItem (piece / building / king) was spent."""

    player_id: str
    item_id: str
    source: str    # "piece" | "building" | "king"
    ability: str
    label: str = ""


@dataclass(frozen=True)
class DuelStrikeResolved(Event):
    """An attacker Strike was attempted — landed, or blocked by a Guard/Bypass."""

    player_id: str
    blocked: bool
    strikes_landed: int
    strikes_needed: int


@dataclass(frozen=True)
class DuelRoundAdvanced(Event):
    """Both sides acted this Duel round — round_number increments."""

    round_number: int
    max_rounds: int


@dataclass(frozen=True)
class RoyalEscapeTriggered(Event):
    """
    Defender survived the Duel (README §33). The King relocates and gains
    temporary capture protection (Royal Immunity, implemented as shield:N).
    """

    defender: str
    new_position: Position | None
    duels_survived: int
