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
    • Both players' Territory (public — README §13, derived entirely from
      public board state: home ranks + Building radii)
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

from copy import copy as _shallow_copy
from dataclasses import dataclass, field
from typing import Any, Iterable, TypeVar

from game.core.phases import KingCardStatus, Phase, RevelationState

_T = TypeVar("_T")


def _detached(items: "Iterable[_T]") -> tuple[_T, ...]:
    """
    Copy engine-owned records out of the privacy boundary.

    README §44's rule is that a controller never receives the GameState.
    Handing it the *live* KingCardState / RitualState / BuildingInstance /
    TrapInstance / BuildingPoolEntry objects would hand it a writable
    slice of that state through the back door: those dataclasses are
    mutable, so a bot could set ``ritual.activated = False`` or
    ``building.integrity = 99`` and the engine would believe it.

    Every one of these records is flat — its fields are scalars, enums, or
    frozen Positions — so a shallow copy per record fully detaches it.
    Value equality is unaffected, which is all any consumer relies on.
    """
    return tuple(_shallow_copy(item) for item in items)


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


@dataclass(frozen=True)
class PublicSquareEffect:
    """
    Stage 6 — one active temporary square effect (a visible battlefield
    hazard/zone, never hidden information): "frozen" (iron_vanguard /
    cursed_ground's un-enterable cousin), "scorched" (ember_drake),
    "blocked" (veil_of_stillness), "cursed" (cursed_ground's
    immobilize-on-entry zone).  ``owner`` is who set it (for UI framing —
    "your ward" vs "their hazard" — not a privacy boundary; these are all
    public board state).  ``card_id`` is the originating Monster/Spell
    card, so the UI's CardViewer can show its description when the owner
    right-clicks the zone (see AppController._collect_zone_entries).
    """

    position: Any           # Position
    effect_type: str        # "frozen" | "scorched" | "blocked" | "cursed"
    duration_turns: int
    owner: str | None
    card_id: str | None = None


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
    square_effects: tuple[PublicSquareEffect, ...] = ()  # Stage 6 — active zone hazards


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

    # ── Building pool information (Stage 8 — always fully public, README §12) ──
    own_building_pool: tuple[Any, ...]       # tuple[BuildingPoolEntry, ...]
    opponent_building_pool: tuple[Any, ...]  # tuple[BuildingPoolEntry, ...]

    # ── Territory (Stage 9 — always fully public, derived from public board
    # state: home ranks + Building radii — README §13). Every square in
    # each tuple is inside that player's Territory; the two can overlap.
    own_territory: tuple[Any, ...]           # tuple[Position, ...]
    opponent_territory: tuple[Any, ...]      # tuple[Position, ...]

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

def _obscured_by_veil_conjurer(state: "GameState", pos: "Position", registry: "object | None") -> bool:
    """True if ``pos`` lies within radius of ANY monster (either side)
    carrying ``obscure_influence`` (veil_conjurer)."""
    if registry is None:
        return False
    from game.cards.card import MonsterCard

    from game.mechanics.monsters import is_effects_suppressed

    for owner in ("white", "black"):
        for spos, unit in state.board.all_units_for(owner):
            if unit.monster_id is None:
                continue
            if is_effects_suppressed(unit):
                continue
            try:
                card = registry.get(unit.monster_id)
            except KeyError:
                continue
            if not isinstance(card, MonsterCard):
                continue
            for effect in card.effects:
                if effect.type != "obscure_influence":
                    continue
                radius = effect.params.get("radius", 1)
                if (
                    abs(pos.file - spos.file) <= radius
                    and abs(pos.rank - spos.rank) <= radius
                ):
                    return True
    return False


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
    from game.mechanics.territory import territory_squares as _territory_squares

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

    # Stage 6: active square effects (frozen/scorched/blocked/cursed/walled/
    # cost_zone) — all public battlefield state, never hidden information.
    square_effects: list[PublicSquareEffect] = []
    for pos, sq in state.board.squares.items():
        for eff in sq.temporary_effects:
            parts = eff.split(":")
            effect_type = parts[0]
            if effect_type not in (
                "frozen", "scorched", "blocked", "cursed", "walled",
                "cost_zone", "no_summon", "temp_territory",
            ):
                continue
            duration = int(parts[1]) if len(parts) > 1 else 0
            owner = parts[2] if len(parts) > 2 else None
            # cost_zone/no_summon carry one extra field (max_dist / blocked)
            # before card_id (see mechanics.effects.board_control).
            card_id_idx = 4 if effect_type in ("cost_zone", "no_summon") else 3
            card_id = (
                parts[card_id_idx]
                if len(parts) > card_id_idx and parts[card_id_idx] != "-"
                else None
            )
            # veil_conjurer's obscure_influence: within radius of a
            # veil_conjurer, this square's EXACT modifier (which card
            # caused it) is hidden from anyone who isn't the effect's own
            # owner and doesn't currently have a unit standing on the
            # square ("opponents cannot inspect the exact secondary
            # modifiers... until they enter the area" — the effect_type/
            # duration themselves stay visible, only card_id is hidden).
            observer_occupies = (
                (occ := state.board.get_unit(pos)) is not None and occ.owner == player_id
            )
            if card_id is not None and owner != player_id and not observer_occupies:
                if _obscured_by_veil_conjurer(state, pos, _registry):
                    card_id = None
            square_effects.append(PublicSquareEffect(
                position=pos, effect_type=effect_type,
                duration_turns=duration, owner=owner, card_id=card_id,
            ))

    pub_board = PublicBoardState(
        units=units,
        trap_locations=_detached(state.traps),
        building_locations=_detached(state.buildings),
        square_effects=tuple(square_effects),
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
            # false_prophecy's ritual_bluff: a still-SEALED Ritual whose
            # owner spent this deception shows a FABRICATED FORETOLD
            # requirement to the opponent — a different real Ritual's
            # archetype/required_vessel, picked deterministically (first
            # OTHER RitualCard in registry order) so it reads as plausible
            # card data rather than an obviously-fake placeholder. The
            # owner's own Observation (this branch only runs for ``opp``,
            # never ``ps``) and every engine check still see it as truly
            # SEALED — see mechanics.rituals for where that matters.
            if rs.bluff_turns > 0 and registry is not None and rs.ritual_id in registry:
                decoy = next(
                    (r for r in registry.all_rituals() if r.id != rs.ritual_id),
                    None,
                )
                if decoy is not None:
                    opp_ritual_info.append(PublicRitualInfo(
                        revelation=RevelationState.FORETOLD,
                        archetype=getattr(decoy, "archetype", None),
                        required_vessel=getattr(decoy, "required_vessel", None),
                    ))
                    continue
            opp_ritual_info.append(PublicRitualInfo(revelation=RevelationState.SEALED))
        elif rs.revelation == RevelationState.FORETOLD:
            # Stage 11: partial reveal (README §15 example) — archetype +
            # required Vessel only, never the exact pattern/summon.
            archetype = None
            required_vessel = None
            if registry is not None and rs.ritual_id in registry:
                ritual_card = registry.get(rs.ritual_id)
                archetype = getattr(ritual_card, "archetype", None)
                required_vessel = getattr(ritual_card, "required_vessel", None)
            opp_ritual_info.append(
                PublicRitualInfo(
                    revelation=RevelationState.FORETOLD,
                    archetype=archetype,
                    required_vessel=required_vessel,
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
        own_building_pool=_detached(ps.building_pool),
        opponent_building_pool=_detached(opp.building_pool),
        own_territory=tuple(_territory_squares(state, player_id, registry)),
        opponent_territory=tuple(_territory_squares(state, opp_id, registry)),
        own_king_pool=_detached(ps.king_pool),
        own_active_king=ps.active_king,
        own_retired_kings=tuple(ps.retired_kings),
        opponent_king_info=tuple(opp_king_info),
        own_rituals=_detached(ps.ritual_pool),
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
