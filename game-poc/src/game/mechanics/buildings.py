"""
mechanics/buildings.py — Stage 8: Building lifecycle + effects
=================================================================

Companion to mechanics/monsters.py, but for the Building system (README
§10–§12): construction commitment, disruption, completion, destruction,
and the handful of Building ``effects`` that are actually wired up today.

Construction lifecycle (state lives on BuildingInstance / UnitInstance):

    StartConstruction (core/rules.py._execute_start_construction)
        → BuildingInstance(status=UNDER_CONSTRUCTION, remaining_turns=N)
        → the Builder Pawn is COMMITTED: is_committed_builder() becomes
          True for it, which blocks MovePiece / SummonMonster on that
          piece (README §12.2 "Pawn is committed for a period of time").

    Each of the owner's own EndTurn (core/rules.py._execute_end_turn)
        → tick_construction() decrements remaining_turns; at 0 the
          Building becomes COMPLETE and the Pawn's builder_available
          flips to False (README §12.3 — the once-per-match token).

    Disruption (core/rules.py._execute_move_piece, on any capture)
        → cancel_construction() — capturing the committed Builder Pawn
          destroys the half-built Building outright (README §12.2 step
          4, "Opponent has an opportunity to disrupt it"). The consumed
          Building Pool copy is NOT refunded — it was already spent.

    Start of the owner's turn (core/game.py Game._auto_start_turn)
        → apply_building_auras() refreshes the live, radius-based
          effects of every COMPLETE Building the player owns.

Only three effect types actually do anything yet — everything else in a
Building's ``effects`` list (see data/buildings.yaml) is inert today,
reserved for systems that don't exist yet (Ritual, Territory, Final
Duel), matching the STUB convention already used throughout
mechanics/effects/.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from game.core.phases import ConstructionStatus
from game.mechanics.area import in_area

if TYPE_CHECKING:
    from game.chess.pieces import Position
    from game.core.events import Event
    from game.core.state import BuildingInstance, GameState, TrapInstance


# ─────────────────────────────────────────────────────────────────────────────
# Commitment / disruption / completion
# ─────────────────────────────────────────────────────────────────────────────

def find_committed_building(state: "GameState", piece_id: str) -> "BuildingInstance | None":
    """The UNDER_CONSTRUCTION BuildingInstance ``piece_id`` is Builder for, if any."""
    for b in state.buildings:
        if b.builder_piece_id == piece_id and b.status == ConstructionStatus.UNDER_CONSTRUCTION:
            return b
    return None


def is_committed_builder(state: "GameState", piece_id: str) -> bool:
    """True if ``piece_id`` is mid-construction and therefore cannot move or be transformed."""
    return find_committed_building(state, piece_id) is not None


def cancel_construction(
    state: "GameState",
    building: "BuildingInstance",
    events: "list[Event]",
    destroyed_by: str | None,
) -> None:
    """
    Disruption: the Builder Pawn was captured (or otherwise removed) before
    completion.  The Building is destroyed outright — the Building Pool
    copy already spent on it is NOT returned (README leaves this cost
    open, §57 "Building construction duration"; a permanently-lost
    investment matches how every other resource sink in this game works —
    Recompose, Mercenary, Tribute).
    """
    from game.core.events import BuildingDestroyed

    building.status = ConstructionStatus.DESTROYED
    sq = state.board.get_square(building.position)
    if sq.building_id == building.id:
        sq.building_id = None
    events.append(BuildingDestroyed(
        building_instance_id=building.id,
        position=building.position,
        destroyed_by=destroyed_by,
    ))


def monster_construction_speed_bonus(
    state: "GameState",
    owner: str,
    builder_position: "Position",
    base_turns: int,
    registry: "object | None",
) -> int:
    """
    IMPLEMENTED — master_mason's ``construction_speed_bonus``.

    Final ``remaining_turns`` for a construction just started at
    ``builder_position``, after every owned Monster within
    ``radius`` squares of it that carries this effect (mirrors
    mechanics.kings.apply_construction_speed_bonus's King-policy
    equivalent, applied on top of it in
    RulesEngine._execute_start_construction).
    """
    if registry is None:
        return base_turns
    from game.cards.card import MonsterCard

    turns = base_turns
    for pos, unit in state.board.all_units_for(owner):
        if unit.monster_id is None:
            continue
        try:
            card = registry.get(unit.monster_id)
        except KeyError:
            continue
        if not isinstance(card, MonsterCard):
            continue
        for effect in card.effects:
            if effect.type != "construction_speed_bonus":
                continue
            radius = effect.params.get("radius", 1)
            if (
                abs(builder_position.file - pos.file) <= radius
                and abs(builder_position.rank - pos.rank) <= radius
            ):
                reduced = turns - effect.params.get("turns_reduced", 1)
                minimum = effect.params.get("minimum_turns", 1)
                turns = max(reduced, minimum)
    return turns


def tick_disabled_buildings(state: "GameState", owner: str) -> None:
    """
    Called once per ``owner``'s own EndTurn. Ticks down ``disabled_turns``
    on every Building they own (saboteur's ``disable_building`` — decays
    on the VICTIM owner's own turn, same convention as burrow_cooldown /
    immobilized: "until the start of the next owner turn").
    """
    for b in state.buildings:
        if b.owner == owner and b.disabled_turns > 0:
            b.disabled_turns -= 1


def _complete_building(state: "GameState", b: "BuildingInstance", events: "list[Event]") -> None:
    """Shared completion side effects: COMPLETE status, spend the Builder
    token, emit BuildingCompleted. Called once ``remaining_turns`` hits 0,
    whether from a normal tick or rapid_construction's advance_construction."""
    from game.core.events import BuildingCompleted

    b.status = ConstructionStatus.COMPLETE
    found = state.board.find_unit_by_piece_id(b.builder_piece_id) if b.builder_piece_id else None
    if found is not None:
        _, builder_unit = found
        builder_unit.builder_available = False  # README §12.3: once-per-match token spent
    events.append(BuildingCompleted(
        player_id=b.owner,
        building_instance_id=b.id,
        building_card_id=b.building_card_id,
        position=b.position,
    ))


def advance_one_building(state: "GameState", b: "BuildingInstance", turns: int, events: "list[Event]") -> None:
    """
    IMPLEMENTED — rapid_construction's ``advance_construction``. Reduces
    ONE specific UNDER_CONSTRUCTION Building's ``remaining_turns`` by
    ``turns`` (floored at 0), completing it immediately if that reaches 0
    — "It may complete immediately if this satisfies its remaining
    construction time."
    """
    if b.status != ConstructionStatus.UNDER_CONSTRUCTION:
        return
    b.remaining_turns = max(0, b.remaining_turns - turns)
    if b.remaining_turns <= 0:
        _complete_building(state, b, events)


def tick_construction(state: "GameState", owner: str, events: "list[Event]") -> None:
    """
    Called once per ``owner``'s own EndTurn.  Every UNDER_CONSTRUCTION
    Building they own advances by one turn; at 0 remaining it completes.
    """
    for b in state.buildings:
        if b.owner != owner or b.status != ConstructionStatus.UNDER_CONSTRUCTION:
            continue
        b.remaining_turns -= 1
        if b.remaining_turns > 0:
            continue
        _complete_building(state, b, events)


# ─────────────────────────────────────────────────────────────────────────────
# Live effects — start-of-turn auras
# ─────────────────────────────────────────────────────────────────────────────

def apply_building_auras(state: "GameState", player_id: str, registry: "object | None") -> None:
    """
    Called at the start of ``player_id``'s turn.  Every COMPLETE Building
    they own refreshes its radius-based aura effects onto nearby allied
    Monster units.  Idempotent — units that already carry the aura's
    status are left alone.
    """
    if registry is None:
        return
    from game.cards.card import BuildingCard

    for b in state.buildings:
        if b.owner != player_id or b.status != ConstructionStatus.COMPLETE:
            continue
        if b.disabled_turns > 0:
            continue  # saboteur's disable_building — auras suspended
        try:
            card = registry.get(b.building_card_id)
        except KeyError:
            continue
        if not isinstance(card, BuildingCard):
            continue

        for effect in card.effects:
            if effect.type == "capture_protection_aura":
                _apply_shield_aura(state, player_id, b.position, card.radius)
            elif effect.type == "spell_radius_aura":
                _apply_spell_radius_aura(state, player_id, b.position, card.radius)
            # Any other effect type (final_duel_guard_bonus, ritual_support,
            # trap_radius_bonus, final_duel_support_range, ...) is either
            # handled elsewhere (trap_radius_bonus — see below) or a
            # documented Stage 9+ stub; nothing to do here.


def _apply_shield_aura(state: "GameState", player_id: str, center: "Position", radius: int) -> None:
    """Fortress: grant ``shield:1`` to owned Monsters within radius that don't already have one."""
    for pos, unit in state.board.all_units_for(player_id):
        if unit.monster_id is None:
            continue
        if not in_area(pos, center, radius):
            continue
        if not any(s.startswith("shield:") for s in unit.statuses):
            unit.add_status("shield:1")


def _apply_spell_radius_aura(state: "GameState", player_id: str, center: "Position", radius: int) -> None:
    """Shrine: grant the spell_radius_bonus:1 flag to owned Monsters within radius."""
    for pos, unit in state.board.all_units_for(player_id):
        if unit.monster_id is None:
            continue
        if not in_area(pos, center, radius):
            continue
        if not any(s.startswith("spell_radius_bonus:") for s in unit.statuses):
            unit.add_status("spell_radius_bonus:1")


# ─────────────────────────────────────────────────────────────────────────────
# Live effects — builders_ward (Trap, not a Building, but grants
# capture_protection to a committed Builder Pawn the same way Fortress does
# for Monsters)
# ─────────────────────────────────────────────────────────────────────────────

def apply_builders_ward_aura(state: "GameState", player_id: str, registry: "object | None") -> None:
    """
    IMPLEMENTED — builders_ward's ``protect_builder``, refreshed at the
    start of ``player_id``'s own turn (same call site as
    apply_building_auras) rather than an ``enter_radius`` movement
    trigger: the card protects the OWNER's own committed Builder Pawn
    ("An allied Pawn currently constructing"), and check_enter_radius_traps
    only ever fires a Trap against the OPPONENT entering it — an
    always-armed Trap can't be "entered" by its own owner's already-
    stationary Pawn under that model. Grants ``shield:N`` (idempotent —
    skips a Pawn that already has one) to any of the owner's own
    UNDER_CONSTRUCTION Buildings' Builder Pawns within the Trap's radius.
    """
    if registry is None:
        return
    from game.cards.card import TrapCard
    from game.core.phases import ConstructionStatus

    for trap in state.traps:
        if trap.owner != player_id:
            continue
        try:
            card = registry.get(trap.card_id)
        except KeyError:
            continue
        if not isinstance(card, TrapCard):
            continue
        for effect in card.effects:
            if effect.type != "protect_builder":
                continue
            uses = effect.params.get("capture_protection_uses", 1)
            for b in state.buildings:
                if (
                    b.owner != player_id
                    or b.status != ConstructionStatus.UNDER_CONSTRUCTION
                    or b.builder_piece_id is None
                ):
                    continue
                if not in_area(b.position, trap.position, trap.radius, trap.shape):
                    continue
                found = state.board.find_unit_by_piece_id(b.builder_piece_id)
                if found is None:
                    continue
                _, builder_unit = found
                if not any(s.startswith("shield:") for s in builder_unit.statuses):
                    builder_unit.add_status(f"shield:{uses}")


# ─────────────────────────────────────────────────────────────────────────────
# Live effects — Watchtower's Trap-radius extension
# ─────────────────────────────────────────────────────────────────────────────

def trap_radius_bonus(state: "GameState", trap: "TrapInstance", registry: "object | None") -> int:
    """
    Extra radius granted to ``trap`` by any COMPLETE Watchtower (or other
    ``trap_radius_bonus``-carrying Building) the Trap's owner controls
    within influence range of the Trap's position (README §12.4:
    Watchtower "extend support/influence range").
    """
    if registry is None:
        return 0
    from game.cards.card import BuildingCard

    bonus = 0
    for b in state.buildings:
        if b.owner != trap.owner or b.status != ConstructionStatus.COMPLETE:
            continue
        if b.disabled_turns > 0:
            continue  # saboteur's disable_building — bonus suspended
        try:
            card = registry.get(b.building_card_id)
        except KeyError:
            continue
        if not isinstance(card, BuildingCard):
            continue
        if not in_area(trap.position, b.position, card.radius):
            continue
        for effect in card.effects:
            if effect.type == "trap_radius_bonus":
                bonus += effect.params.get("bonus", 1)
    return bonus
