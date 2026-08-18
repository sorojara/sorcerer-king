"""
Observation — Stage 0
======================

The Observation is the *only* object the AI or UI should ever receive.

It contains exactly the information that a player is *legally allowed to know*:
    • The public board state (all unit positions, all trap locations/identities)
    • Their own private hand and deck count
    • The opponent's hand COUNT (not the cards themselves)
    • Their own King pool statuses (hidden cards known to self as HIDDEN)
    • Opponent King public info (only what has been revealed)
    • Their own Ritual states (full)
    • Opponent Ritual public info (subject to revelation state)
    • All placed Buildings (public)
    • All placed Traps (public — traps are never secret by design)
    • Current phase, turn number, check flags

The AI receives an Observation, not a GameState.  This is enforced by
architecture, not convention.

AI must NEVER receive:
    ✗ Opponent hand card IDs
    ✗ Opponent deck order
    ✗ Hidden King identities
    ✗ Hidden Ritual identities
    ✗ Future RNG state

Reference: phase0.md §11 "Observation is critical because of AI"
           README.md §44 "AI Information Rules"
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from game.core.phases import KingCardStatus, Phase, RevelationState


# ─────────────────────────────────────────────────────────────────────────────
# Public sub-views (opponent-facing projections of private objects)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PublicKingInfo:
    """
    What the observer can deduce about an opponent's King card slot.
    - HIDDEN  → { "status": "hidden" }  (know a slot exists, nothing more)
    - ACTIVE  → { "status": "active", "king_card_id": "..." }
    - RETIRED → { "status": "retired", "king_card_id": "..." }
    """

    slot_index: int
    status: KingCardStatus
    king_card_id: str | None = None  # only set for ACTIVE or RETIRED


@dataclass(frozen=True)
class PublicRitualInfo:
    """
    What the observer can deduce about an opponent's Ritual.
    Respects the RevelationState.
    """

    revelation: RevelationState
    # Only populated for FORETOLD or REVEALED states:
    archetype: str | None = None
    required_vessel: str | None = None
    # Only populated for REVEALED:
    ritual_id: str | None = None
    full_data: dict[str, Any] | None = None


@dataclass(frozen=True)
class PublicUnitInfo:
    """
    The public view of a unit on the board.
    Monster identity is always visible (Traps are secret; Monsters are not).

    ``activatable_effects`` lists the effect types on this unit that require
    an explicit player action (e.g. "burrow", "disable_building").  This is
    computed from the card's effect list by the observer build function and
    exposed here so the UI can show ability-available badges without touching
    the card registry directly.
    """

    piece_id: str
    owner: str
    piece_type: str        # PieceType.value
    monster_id: str | None
    statuses: tuple[str, ...]
    position: Any          # Position — avoid circular at module level
    activatable_effects: tuple[str, ...] = ()   # effect types requiring player action


# ─────────────────────────────────────────────────────────────────────────────
# PublicBoardState
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PublicBoardState:
    """
    The fully-public snapshot of the board that both players and all observers
    may see.  Monster identities are public; Trap locations/identities are
    public (by design).
    """

    units: tuple[PublicUnitInfo, ...]   # all units currently on the board
    trap_locations: tuple[Any, ...]     # tuple[TrapInstance, ...] — public info
    building_locations: tuple[Any, ...]  # tuple[BuildingInstance, ...]


# ─────────────────────────────────────────────────────────────────────────────
# Observation
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Observation:
    """
    The full legal observation for one player at one moment in time.

    An AI receives this + a list[Action] of legal actions.
    The AI *never* receives the GameState.

    All fields are immutable (frozen dataclass) so an AI cannot accidentally
    mutate shared state.
    """

    player_id: str

    # ── Board ──────────────────────────────────────────────────────────────
    board: PublicBoardState

    # ── Own private information ───────────────────────────────────────────
    own_hand: tuple[str, ...]       # card IDs in own hand
    own_deck_count: int             # number of cards remaining in own deck
    own_graveyard: tuple[str, ...]  # own discard pile (public)

    # ── Opponent public information ───────────────────────────────────────
    opponent_hand_count: int        # how many cards opponent holds (no IDs)
    opponent_deck_count: int        # how many cards in opponent's deck
    opponent_graveyard: tuple[str, ...]  # opponent discard (public)

    # ── King pool information ──────────────────────────────────────────────
    own_king_pool: tuple[Any, ...]           # tuple[KingCardState, ...] — full info
    own_active_king: str | None             # active King card ID
    own_retired_kings: tuple[str, ...]

    opponent_king_info: tuple[PublicKingInfo, ...]  # what opponent has revealed

    # ── Ritual information ─────────────────────────────────────────────────
    own_rituals: tuple[Any, ...]              # tuple[RitualState, ...] — full info
    opponent_ritual_info: tuple[PublicRitualInfo, ...]

    # ── Phase / turn ───────────────────────────────────────────────────────
    phase: Phase
    turn_number: int
    active_player: str

    # ── Check flags (public) ───────────────────────────────────────────────
    own_in_check: bool
    opponent_in_check: bool

    # ── Pending decision (only set when it's this player's turn) ──────────
    pending_decision_type: str | None = None
    pending_decision_options: tuple[Any, ...] = field(default_factory=tuple)
    pending_decision_min: int = 0
    pending_decision_max: int = 0


# ─────────────────────────────────────────────────────────────────────────────
# Observation builder
# ─────────────────────────────────────────────────────────────────────────────

def build_observation(
    state: "GameState",
    player_id: str,
    registry: "object | None" = None,
) -> Observation:  # type: ignore[name-defined]  # noqa: F821
    """
    Construct an Observation for ``player_id`` from the full ``GameState``.

    This is the single translation layer.  Any information that should not
    cross the privacy boundary must be filtered here.

    ``registry`` is optional; when provided, activatable_effects on each unit
    are computed and exposed so the UI can show ability badges.

    Import is deferred to avoid circular imports.
    """
    from game.core.state import GameState, PendingDecision
    from game.core.phases import KingCardStatus, RevelationState
    from game.mechanics.effects import get_activatable_effects

    ps = state.get_player(player_id)
    opp_id = state.opponent_of(player_id)
    opp = state.get_player(opp_id)

    _registry = registry

    # ── Public board ───────────────────────────────────────────────────────
    units = tuple(
        PublicUnitInfo(
            piece_id=unit.piece.id,
            owner=unit.owner,
            piece_type=unit.piece.piece_type.value,
            monster_id=unit.monster_id,
            statuses=tuple(unit.statuses),
            position=pos,
            activatable_effects=tuple(
                get_activatable_effects(unit, _registry)
            ),
        )
        for pos, unit in state.board.all_units()
    )

    pub_board = PublicBoardState(
        units=units,
        trap_locations=tuple(state.traps),
        building_locations=tuple(state.buildings),
    )

    # ── Opponent King public info ──────────────────────────────────────────
    opp_king_info = []
    for i, kcs in enumerate(opp.king_pool):
        if kcs.status == KingCardStatus.HIDDEN:
            opp_king_info.append(PublicKingInfo(slot_index=i, status=KingCardStatus.HIDDEN))
        else:
            opp_king_info.append(
                PublicKingInfo(
                    slot_index=i,
                    status=kcs.status,
                    king_card_id=kcs.king_card_id,
                )
            )

    # ── Opponent Ritual public info ────────────────────────────────────────
    opp_ritual_info = []
    for rs in opp.ritual_pool:
        if rs.revelation == RevelationState.SEALED:
            opp_ritual_info.append(PublicRitualInfo(revelation=RevelationState.SEALED))
        elif rs.revelation == RevelationState.FORETOLD:
            # Would include archetype/vessel from card registry — placeholder
            opp_ritual_info.append(
                PublicRitualInfo(
                    revelation=RevelationState.FORETOLD,
                    archetype=None,     # populated from card registry in Stage 11
                    required_vessel=None,
                )
            )
        else:  # REVEALED
            opp_ritual_info.append(
                PublicRitualInfo(
                    revelation=RevelationState.REVEALED,
                    ritual_id=rs.ritual_id,
                )
            )

    # ── Pending decision ───────────────────────────────────────────────────
    pd = state.pending_decision
    pd_type = None
    pd_opts: tuple = ()
    pd_min = 0
    pd_max = 0
    if pd is not None and pd.player_id == player_id:
        pd_type = pd.decision_type.value
        pd_opts = tuple(pd.options)
        pd_min = pd.min_choices
        pd_max = pd.max_choices

    # ── Check flags ────────────────────────────────────────────────────────
    own_check = ps.is_in_check()
    opp_check = opp.is_in_check()

    return Observation(
        player_id=player_id,
        board=pub_board,
        own_hand=tuple(ps.hand),
        own_deck_count=len(ps.deck),
        own_graveyard=tuple(ps.graveyard),
        opponent_hand_count=len(opp.hand),
        opponent_deck_count=len(opp.deck),
        opponent_graveyard=tuple(opp.graveyard),
        own_king_pool=tuple(ps.king_pool),
        own_active_king=ps.active_king,
        own_retired_kings=tuple(ps.retired_kings),
        opponent_king_info=tuple(opp_king_info),
        own_rituals=tuple(ps.ritual_pool),
        opponent_ritual_info=tuple(opp_ritual_info),
        phase=state.phase,
        turn_number=state.turn_number,
        active_player=state.active_player,
        own_in_check=own_check,
        opponent_in_check=opp_check,
        pending_decision_type=pd_type,
        pending_decision_options=pd_opts,
        pending_decision_min=pd_min,
        pending_decision_max=pd_max,
    )
