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

    Siege (Stage 13, core/rules.py._execute_attack_building)
        → damage_building() — a COMPLETE Building has an ``integrity``
          pool (BuildingCard.base_integrity, from its size) that hostile
          actions chip away at. When it runs out, the Building collapses.

Stage 13 also makes a COMPLETE Building a physical obstacle: the square
it stands on cannot be entered by the enemy at all (chess/movement.py
reads SquareState.complete_building_owner, kept in sync by
refresh_building_blocks() below). The only way past it is to knock it
down, and only a narrow set of units may even try — see
can_attack_building().

A siege targets the structure and ignores whoever is standing on it: the
square being impassable to the enemy is exactly why a garrison cannot be
"dealt with first" (see core/rules.py._execute_attack_building).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from game.core.phases import ConstructionStatus
from game.mechanics.area import in_area

if TYPE_CHECKING:
    from game.chess.pieces import Position, UnitInstance
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
    refresh_building_blocks(state)
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
    # Stage 13: a finished Building becomes a physical obstacle to the
    # enemy — and gains the integrity pool a siege chips away at.
    b.integrity = b.max_integrity
    refresh_building_blocks(state)
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


# ─────────────────────────────────────────────────────────────────────────────
# Stage 13 — Siege: durability, attack eligibility, damage, repair
#
# README §11 says Buildings "do not move / do not normally capture", and
# until Stage 13 nothing could attack one either: the only ways a Building
# left the board were construction-disruption (capturing its committed
# Builder Pawn) and the King Succession sacrifice. That left a whole family
# of shipped card effects inert — building_damage_bonus, building_aura,
# building_capture_protection, building_damage, building_vulnerability,
# temporary_building_protection, repair_building and ignore_terrain all
# describe a contest over Buildings that had no contest to join.
#
# The contest introduced here:
#
#   • A COMPLETE Building is a WALL. Its square cannot be entered by the
#     opposing side at all (chess/movement.py). Its own owner may still
#     stand there — the Builder Pawn is standing there the moment the
#     Building completes, and evicting it would be absurd.
#
#   • Knocking it down is a dedicated chess-phase action (AttackBuilding),
#     not a move: the attacker bombards from where it stands and never
#     relocates, which keeps "the square is impassable" true right up
#     until the structure actually falls.
#
#   • Almost nothing may attack. Ritual Monsters can (they are the
#     match's siege engines, and this is what makes completing a Ritual
#     worth the board investment); the two Monsters whose card text is
#     explicitly about tearing down infrastructure can (obsidian_dragon,
#     sovereign_of_embers, via building_damage_bonus); and any unit at all
#     can, against a Building a Spell has cracked open first (siege_order's
#     building_vulnerability). Everything else must go around.
# ─────────────────────────────────────────────────────────────────────────────

def refresh_building_blocks(state: "GameState") -> None:
    """
    Re-derive ``SquareState.complete_building_owner`` for all 64 squares
    from ``state.buildings``.

    chess/movement.py needs "is there a finished enemy Building here?"
    while holding nothing but a BoardState (check detection and
    castling-safety probes never receive the GameState), so the answer is
    denormalised onto the square. Call this from every place a Building
    completes, is destroyed, or is loaded from disk; it is a cheap full
    rebuild rather than an incremental patch precisely so no caller can
    leave the two views out of sync.
    """
    live = {
        b.position: b.owner
        for b in state.buildings
        if b.status == ConstructionStatus.COMPLETE
    }
    for pos, sq in state.board.squares.items():
        sq.complete_building_owner = live.get(pos)


def find_building_at(state: "GameState", pos: "Position") -> "BuildingInstance | None":
    """The non-DESTROYED BuildingInstance standing on ``pos``, if any."""
    for b in state.buildings:
        if b.position == pos and b.status != ConstructionStatus.DESTROYED:
            return b
    return None


def building_max_integrity(building_card_id: str, registry: "object | None") -> int:
    """
    BuildingCard.base_integrity for ``building_card_id``, or 1 when no
    registry is available (registry-less unit tests) — the same fallback
    convention _execute_start_construction already uses for
    construction_turns.
    """
    if registry is None:
        return 1
    from game.cards.card import BuildingCard

    try:
        card = registry.get(building_card_id)
    except KeyError:
        return 1
    return card.base_integrity if isinstance(card, BuildingCard) else 1


def is_siege_open(building: "BuildingInstance") -> bool:
    """
    True while siege_order's ``building_vulnerability`` mark is live —
    the Building's "defensive protection is reduced", which this engine
    reads as: anyone may attack it, and every blow lands harder.
    """
    return building.vulnerable_turns > 0


def can_attack_building(
    state: "GameState",
    unit: "UnitInstance",
    building: "BuildingInstance",
    registry: "object | None",
) -> bool:
    """
    Whether ``unit`` is permitted to besiege ``building`` at all.

    Order matters only for readability — the three clauses are an OR:

    1. The Building is marked by a Spell's ``building_vulnerability``
       (siege_order): its defences are already breached, so any unit may
       pile on. This is the "or spells that allow that" escape hatch.
    2. ``unit`` is a Ritual Monster (MonsterCard.ritual_only) — the
       headline rule. Ritual Monsters are the only units that can
       routinely threaten infrastructure.
    3. ``unit``'s Monster card carries ``building_damage_bonus``
       (obsidian_dragon, sovereign_of_embers) — cards whose entire text is
       about destroying Buildings. Read from the card rather than the
       ``building_destroyer`` status armed at summon time so that a
       suppressed Monster is still checked through the one gate below.

    A Monster whose effects are suppressed (monster_seal /
    nullification_glyph) loses clauses 2 and 3 along with every other
    Monster contribution — but clause 1 still stands, since that permission
    comes from the Building's condition, not the attacker's.
    """
    if building.status != ConstructionStatus.COMPLETE:
        return False
    if building.owner == unit.owner:
        return False

    if is_siege_open(building):
        return True

    if unit.monster_id is None or registry is None:
        return False

    from game.mechanics.monsters import is_effects_suppressed
    if is_effects_suppressed(unit):
        return False

    from game.cards.card import MonsterCard
    try:
        card = registry.get(unit.monster_id)
    except KeyError:
        return False
    if not isinstance(card, MonsterCard):
        return False

    if card.ritual_only:
        return True
    return any(eff.type == "building_damage_bonus" for eff in card.effects)


#: A blow no amount of integrity or durability aura survives — used for
#: ``destroy_on_capture`` Monsters. Large enough that it can't be reached
#: by stacking auras, small enough to stay readable in an event log.
_OVERWHELMING = 99

#: What a Ritual Monster's blow is worth, against the 1 every other
#: attacker deals (README §11.2). Siege is meant to be the strategic payoff
#: for completing a Ritual, but at 1 damage a Ritual Monster besieged no
#: harder than a Pawn walking into a siege_order mark — and strictly worse
#: than bane_of_structures / molten_colossus, which are ordinary Main Deck
#: draws. 2 makes the Ritual investment out-siege the cards you merely draw
#: and drops a bare Fortress in one action.
RITUAL_SIEGE_DAMAGE = 2


def attack_damage(
    unit: "UnitInstance",
    building: "BuildingInstance",
    registry: "object | None",
) -> int:
    """
    Damage one AttackBuilding from ``unit`` deals.

    Base 1, or ``RITUAL_SIEGE_DAMAGE`` for a Ritual Monster — the same
    ``MonsterCard.ritual_only`` flag can_attack_building() reads for its
    headline permission clause, so "Ritual Monsters are the siege units"
    is one rule about one property rather than two that can drift.
    ``building_damage_bonus`` stacks on top of that base; with
    ``destroy_on_capture: true`` (obsidian_dragon, sovereign_of_embers —
    "Buildings captured by this monster are destroyed immediately") it
    instead returns a blow large enough to flatten any Building outright,
    aura durability included. siege_order's extra damage is added inside
    damage_building() so that every damage source benefits from it, not
    just this one.

    A suppressed Monster (monster_seal / nullification_glyph) falls back to
    1 along with every other Monster contribution — it only reaches this
    function at all through can_attack_building()'s siege_order clause,
    which is a property of the Building, not of the attacker.
    """
    if unit.monster_id is None or registry is None:
        return 1
    from game.cards.card import MonsterCard

    try:
        card = registry.get(unit.monster_id)
    except KeyError:
        return 1
    if not isinstance(card, MonsterCard):
        return 1

    from game.mechanics.monsters import is_effects_suppressed
    if is_effects_suppressed(unit):
        return 1

    damage = RITUAL_SIEGE_DAMAGE if card.ritual_only else 1
    for eff in card.effects:
        if eff.type != "building_damage_bonus":
            continue
        if eff.params.get("destroy_on_capture"):
            return _OVERWHELMING
        damage += eff.params.get("amount", 0)
    return damage


def aura_durability_bonus(
    state: "GameState",
    building: "BuildingInstance",
    registry: "object | None",
) -> int:
    """
    IMPLEMENTED — siege_captain / titan_of_the_foundation's ``building_aura``.

    Extra hits ``building`` absorbs thanks to allied Monsters standing
    within their own ``radius`` of it ("Allied Buildings within 1 square
    require one additional successful hostile action to destroy").

    Read from the ``building_aura:<radius>:<bonus>`` status armed at summon
    time (mechanics/effects/buildings.py _building_aura) so the aura is
    naturally re-evaluated on every blow: kill the siege_captain and the
    Building it was shielding is back to bare integrity on the next hit.
    """
    if registry is None:
        return 0
    from game.mechanics.monsters import is_effects_suppressed

    bonus = 0
    for pos, unit in state.board.all_units_for(building.owner):
        if unit.monster_id is None or is_effects_suppressed(unit):
            continue
        for status in unit.statuses:
            if not status.startswith("building_aura:"):
                continue
            _, radius_s, bonus_s = status.split(":")
            if in_area(building.position, pos, int(radius_s)):
                bonus += int(bonus_s)
    return bonus


def _consume_building_shield(
    state: "GameState",
    building: "BuildingInstance",
    registry: "object | None",
) -> bool:
    """
    IMPLEMENTED — castle_keeper's ``building_capture_protection``.

    If an allied Monster within range carries an unspent
    ``building_capture_protection:<radius>:<uses>`` charge covering it,
    spend one and report True — the hostile action is absorbed entirely.
    Mirrors mechanics.monsters.apply_capture_protection's decrement-in-
    place handling of a unit's own ``shield:N``.
    """
    if registry is None:
        return False
    from game.mechanics.monsters import is_effects_suppressed

    for pos, unit in state.board.all_units_for(building.owner):
        if unit.monster_id is None or is_effects_suppressed(unit):
            continue
        for i, status in enumerate(unit.statuses):
            if not status.startswith("building_capture_protection:"):
                continue
            _, radius_s, uses_s = status.split(":")
            uses = int(uses_s)
            if uses <= 0 or not in_area(building.position, pos, int(radius_s)):
                continue
            uses -= 1
            if uses > 0:
                unit.statuses[i] = f"building_capture_protection:{radius_s}:{uses}"
            else:
                unit.statuses.pop(i)
            return True
    return False


def destroy_building(
    state: "GameState",
    building: "BuildingInstance",
    events: "list[Event]",
    destroyed_by: str | None,
) -> None:
    """
    Remove ``building`` from play. Shared by the siege path below and any
    other outright-destruction path that isn't construction disruption.
    """
    from game.core.events import BuildingDestroyed

    building.status = ConstructionStatus.DESTROYED
    building.integrity = 0
    sq = state.board.get_square(building.position)
    if sq.building_id == building.id:
        sq.building_id = None
    refresh_building_blocks(state)
    events.append(BuildingDestroyed(
        building_instance_id=building.id,
        position=building.position,
        destroyed_by=destroyed_by,
    ))


def damage_building(
    state: "GameState",
    building: "BuildingInstance",
    amount: int,
    events: "list[Event]",
    registry: "object | None",
    attacker_piece_id: str | None = None,
) -> bool:
    """
    Apply ``amount`` hostile damage to ``building``. Returns True if the
    Building was destroyed.

    The full defensive stack, in the order a blow meets it:

    1. ``building_capture_protection`` (castle_keeper) — an allied Monster
       nearby eats the entire action and spends a charge. Nothing else
       resolves.
    2. ``building_vulnerability`` (siege_order) — the Building is already
       cracked open, so the blow lands ``vulnerable_amount`` harder.
    3. ``building_aura`` durability (siege_captain, titan_of_the_foundation)
       — extra effective hit points, re-derived on every blow from the
       auras standing right now. A blow that would have killed a bare
       Building instead just eats into integrity.
    4. ``temporary_building_protection`` (emergency_fortifications) — the
       last line: a blow that WOULD destroy the Building is absorbed and
       the structure is left standing on its final point of integrity.

    An UNDER_CONSTRUCTION Building is never a valid target here: it is
    contested by capturing its Builder Pawn instead (cancel_construction).
    """
    from game.core.events import BuildingAttacked, BuildingAttackBlocked

    if building.status != ConstructionStatus.COMPLETE:
        return False

    if _consume_building_shield(state, building, registry):
        events.append(BuildingAttackBlocked(
            building_instance_id=building.id,
            position=building.position,
            attacker_piece_id=attacker_piece_id,
            reason="building_capture_protection",
        ))
        return False

    # Past the shield, the blow reached the structure — so the Building is
    # under active bombardment and repair_buildings will pass it over on
    # the owner's EndTurn. Marked here rather than only where integrity
    # actually drops so that a blow soaked by ``building_aura`` durability
    # or by ``temporary_building_protection`` still suppresses the repair:
    # otherwise the aura holds the line while the engineer heals behind it,
    # which is the same stalemate by a longer route.
    building.damaged_this_round = True

    if is_siege_open(building):
        amount += building.vulnerable_amount

    aura = aura_durability_bonus(state, building, registry)
    effective = building.integrity + aura - amount

    if effective > 0:
        # The aura only ever shields; it never becomes stored integrity.
        before = building.integrity
        building.integrity = max(0, effective - aura)
        events.append(BuildingAttacked(
            building_instance_id=building.id,
            position=building.position,
            attacker_piece_id=attacker_piece_id,
            damage=amount,
            integrity_remaining=building.integrity,
        ))
        if aura > 0 and building.integrity == before:
            events.append(BuildingAttackBlocked(
                building_instance_id=building.id,
                position=building.position,
                attacker_piece_id=attacker_piece_id,
                reason="building_aura",
            ))
        return False

    if building.protection_turns > 0 and building.protection_uses > 0:
        building.protection_uses -= 1
        if building.protection_uses <= 0:
            building.protection_turns = 0
        building.integrity = 1
        events.append(BuildingAttackBlocked(
            building_instance_id=building.id,
            position=building.position,
            attacker_piece_id=attacker_piece_id,
            reason="temporary_building_protection",
        ))
        return False

    building.integrity = 0
    events.append(BuildingAttacked(
        building_instance_id=building.id,
        position=building.position,
        attacker_piece_id=attacker_piece_id,
        damage=amount,
        integrity_remaining=0,
    ))
    destroy_building(state, building, events, destroyed_by=attacker_piece_id)
    return True


def tick_building_timers(state: "GameState", owner: str) -> None:
    """
    Called once per ``owner``'s own EndTurn, alongside
    tick_disabled_buildings. Decays the two Spell-granted timers that live
    on a Building — emergency_fortifications' protection and siege_order's
    vulnerability mark — on the OWNER's own turn, the same convention every
    other per-instance timer in this engine uses.

    Also clears ``damaged_this_round``, which repair_buildings has just
    read. Order matters: core/rules.py._execute_end_turn calls
    repair_buildings BEFORE this, so a Building hit during the round skips
    exactly one repair and is eligible again next round if the attacker
    lets up.
    """
    for b in state.buildings:
        if b.owner != owner:
            continue
        b.damaged_this_round = False
        if b.protection_turns > 0:
            b.protection_turns -= 1
            if b.protection_turns == 0:
                b.protection_uses = 0
        if b.vulnerable_turns > 0:
            b.vulnerable_turns -= 1


def repair_buildings(
    state: "GameState",
    owner: str,
    events: "list[Event]",
    registry: "object | None",
) -> None:
    """
    IMPLEMENTED — royal_engineer / worldforge_colossus' ``repair_building``
    (``trigger: end_of_turn``).

    At the end of ``owner``'s own turn, every one of their damaged COMPLETE
    Buildings within ``radius`` of an allied Monster carrying this effect
    regains ``amount`` integrity, capped at its ``max_integrity``. Scanned
    here rather than through the summon-time effect pipeline for the same
    reason as _resolve_restore_effect_charge: an end-of-turn repeating
    effect can't be expressed as a one-shot status.
    """
    if registry is None:
        return
    from game.cards.card import MonsterCard
    from game.core.events import BuildingRepaired
    from game.mechanics.monsters import is_effects_suppressed

    for pos, unit in state.board.all_units_for(owner):
        if unit.monster_id is None or is_effects_suppressed(unit):
            continue
        try:
            card = registry.get(unit.monster_id)
        except KeyError:
            continue
        if not isinstance(card, MonsterCard):
            continue
        for eff in card.effects:
            if eff.type != "repair_building":
                continue
            if eff.params.get("trigger", "end_of_turn") != "end_of_turn":
                continue
            radius = eff.params.get("radius", 1)
            amount = eff.params.get("amount", 1)
            for b in state.buildings:
                if b.owner != owner or b.status != ConstructionStatus.COMPLETE:
                    continue
                if b.integrity >= b.max_integrity:
                    continue
                # Hit since the owner's last EndTurn: no patching up a
                # structure while it is still being battered. Without this
                # a single royal_engineer's +1/turn exactly cancelled the
                # 1/turn a besieger dealt, and the Building never fell.
                if b.damaged_this_round:
                    continue
                if not in_area(b.position, pos, radius):
                    continue
                healed = min(amount, b.max_integrity - b.integrity)
                b.integrity += healed
                events.append(BuildingRepaired(
                    building_instance_id=b.id,
                    position=b.position,
                    amount=healed,
                    integrity=b.integrity,
                    repaired_by_piece_id=unit.piece.id,
                ))


def unit_ignores_building_terrain(
    unit: "UnitInstance",
    registry: "object | None",
) -> bool:
    """
    IMPLEMENTED — sky_serpent's ``ignore_terrain`` with ``buildings: true``
    ("Ignores movement restrictions created by Buildings and Territory").

    A unit that ignores Building terrain may SLIDE THROUGH the square a
    COMPLETE enemy Building stands on — the structure no longer stops its
    ray. It still may not LAND there: the square is physically occupied by
    a building, and clearing it is what AttackBuilding is for.

    Read from the ``ignore_terrain`` status armed at summon time so this
    stays a board-only query (chess/movement.py has no registry in its
    check-detection paths).
    """
    return "ignore_building_terrain" in unit.statuses
