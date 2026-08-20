"""
mechanics/kings.py — Stage 10: King Pool, Coronation/Succession, King policies
=================================================================================

Companion to mechanics/buildings.py and mechanics/territory.py, for the King
system (README §16-§20): random King Pool assignment at game setup, and the
handful of King ``effects`` (data/kings.yaml) that are actually wired up
today.

Lifecycle (state lives on PlayerState / KingCardState — core/state.py):

    Game setup (core/game.py Game.new())
        → assign_random_king_pool() picks one archetype for the player and
          assembles their 3-card King Pool: that archetype's "home" King
          (guaranteed a slot) plus 2 more random Kings from the shared
          roster. All three start HIDDEN (README §16).

    CoronateKing (core/rules.py._execute_coronate_king)
        → the first activation is free (README §17).

    ChangeKing (core/rules.py._execute_change_king)
        → Succession. Cost escalates each time (README §19.1): the 1st
          Succession costs a piece sacrifice OR a Building destruction
          (player's choice); the 2nd costs BOTH. The old King retires
          forever (README §19 "A retired King cannot become active again").
          Since the King Pool only ever holds 3 cards, 2 is also the
          maximum possible number of Successions in a match.

    Start of the owner's turn (core/game.py Game._auto_start_turn)
        → apply_king_policy_auras() refreshes the live, aura-style effects
          of the player's ACTIVE King (mirrors apply_building_auras).

Only the effects that plug into a system which actually exists today do
anything — everything else in a King's ``effects`` list (
``ritual_information_discount``, ``king_support_bonus``,
``royal_support_suppression``, ``enemy_territory_mobility``, and every
``duel_effect``) is inert, reserved for Rituals (Stage 11) and the Final
Duel (Stage 12+), matching the STUB convention already used throughout
mechanics/effects/ and mechanics/buildings.py.

Wired-up effect types, and where each is actually applied:
    vessel_support             — core/rules.py._execute_summon_monster
    territory_summon_bonus     — core/rules.py._execute_summon_monster
    construction_speed_bonus   — core/rules.py._execute_start_construction
    building_territory_bonus   — mechanics/territory.py._building_zone_squares
    formation_support          — apply_king_policy_auras (this module)
    graveyard_threshold_bonus  — apply_king_policy_auras (this module)
    spell_radius_bonus         — apply_king_policy_auras (this module)
    graveyard_recycle          — maybe_recycle_destroyed_monster (this
                                  module), called from the primary capture
                                  path in core/rules.py and from
                                  mechanics/monsters.check_damage_aura. Not
                                  yet wired into every destruction path
                                  (apply_retaliate / check_scorch_square) —
                                  those remain a documented gap.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from game.mechanics.area import in_area

if TYPE_CHECKING:
    from game.chess.pieces import Position
    from game.core.events import Event
    from game.core.rng import DeterministicRNG
    from game.core.state import GameState, PlayerState


# ─────────────────────────────────────────────────────────────────────────────
# Setup — random King Pool assignment
# ─────────────────────────────────────────────────────────────────────────────

def assign_random_king_pool(
    player: "PlayerState",
    registry: "object | None",
    rng: "DeterministicRNG",
) -> None:
    """
    Assign ``player`` a random archetype and a matching 3-card King Pool.

    Each King in data/kings.yaml names one "primary" archetype (the first
    entry of its ``archetype_support`` list). One archetype is chosen at
    random for the player; that archetype's King is guaranteed a slot in
    the pool, and two more Kings are drawn at random from the rest of the
    roster to fill it out to 3 (README §16's "pool of 3", all HIDDEN).

    No-op — the caller's existing ``king_pool`` is left untouched — if
    ``registry`` is None or carries no King cards, so registry-less callers
    (unit tests, Game.new() without a registry) don't crash.
    """
    from game.cards.card import KingCard
    from game.core.phases import KingCardStatus
    from game.core.state import KingCardState

    if registry is None:
        return
    all_kings: list[KingCard] = registry.all_kings()
    if not all_kings:
        return

    home_king_of: dict[str, str] = {}
    for king in all_kings:
        if not king.archetype_support:
            continue
        primary = king.archetype_support[0]
        home_king_of.setdefault(primary, king.id)
    if not home_king_of:
        return

    archetypes = list(home_king_of.keys())
    chosen_archetype = rng.choice(archetypes)
    home_king_id = home_king_of[chosen_archetype]

    rest = [k.id for k in all_kings if k.id != home_king_id]
    rng.shuffle(rest)
    pool_ids = [home_king_id] + rest[:2]
    rng.shuffle(pool_ids)  # don't always reveal the home King by slot order

    player.archetype = chosen_archetype
    player.king_pool = [
        KingCardState(king_card_id=kid, status=KingCardStatus.HIDDEN)
        for kid in pool_ids
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Active King lookup
# ─────────────────────────────────────────────────────────────────────────────

def get_active_king_card(state: "GameState", player_id: str, registry: "object | None"):
    """The ACTIVE KingCard for ``player_id``, or None (no King active / no registry)."""
    from game.cards.card import KingCard

    if registry is None:
        return None
    ps = state.get_player(player_id)
    if ps.active_king is None:
        return None
    try:
        card = registry.get(ps.active_king)
    except KeyError:
        return None
    return card if isinstance(card, KingCard) else None


def _king_effects(state: "GameState", player_id: str, registry: "object | None", effect_type: str):
    card = get_active_king_card(state, player_id, registry)
    if card is None:
        return []
    return [e for e in card.effects if e.type == effect_type]


# ─────────────────────────────────────────────────────────────────────────────
# vessel_support (dragon_high_king) — read at SummonMonster validation time
# ─────────────────────────────────────────────────────────────────────────────

def king_extra_vessel_types(
    state: "GameState",
    owner: str,
    archetype: str,
    registry: "object | None",
) -> set[str]:
    """
    Extra vessel piece-type strings granted by the owner's ACTIVE King's
    ``vessel_support`` policy for Monsters of ``archetype`` (dragon_high_king:
    Dragons may additionally use a Pawn as a Vessel). Unlike the per-unit
    broodmother aura (mechanics.monsters.get_extra_vessel_types), this is
    global — no board position / radius involved — since it's a kingdom-wide
    policy, not a battlefield aura.

    The King data's ``limit_per_turn`` isn't separately tracked: SummonMonster
    already consumes the turn's one preparation action (README §5), so "once
    per turn" already holds structurally.
    """
    extra: set[str] = set()
    for effect in _king_effects(state, owner, registry, "vessel_support"):
        if effect.params.get("archetype") != archetype:
            continue
        extra.update(effect.params.get("allow_extra_vessel", []))
    return extra


# ─────────────────────────────────────────────────────────────────────────────
# territory_summon_bonus (dragon_high_king) — read at SummonMonster validation
# ─────────────────────────────────────────────────────────────────────────────

def is_in_territory_with_king_bonus(
    state: "GameState",
    pos: "Position",
    owner: str,
    archetype: str,
    registry: "object | None",
) -> bool:
    """
    True if ``pos`` qualifies for a Monster-of-``archetype`` Vessel summon,
    counting the owner's ACTIVE King's ``territory_summon_bonus`` policy
    (dragon_high_king: Dragons may be summoned up to ``summon_range_bonus``
    squares beyond the normal Territory boundary). Falls back to plain
    Territory when no matching policy is active — call this INSTEAD of
    mechanics.territory.is_in_territory, not in addition to it.
    """
    from game.mechanics.territory import is_in_territory, territory_squares

    if is_in_territory(state, pos, owner, registry):
        return True

    for effect in _king_effects(state, owner, registry, "territory_summon_bonus"):
        if effect.params.get("archetype") != archetype:
            continue
        bonus = effect.params.get("summon_range_bonus", 0)
        if bonus <= 0:
            continue
        for square in territory_squares(state, owner, registry):
            if in_area(pos, square, bonus):
                return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# construction_speed_bonus (architect_king) — read at StartConstruction
# ─────────────────────────────────────────────────────────────────────────────

def apply_construction_speed_bonus(
    state: "GameState",
    owner: str,
    builder_piece_type: str,
    base_turns: int,
    registry: "object | None",
) -> int:
    """
    Final ``remaining_turns`` for a new construction, after the owner's
    ACTIVE King's ``construction_speed_bonus`` policy (architect_king:
    a construction started by a Pawn finishes sooner, clamped to
    ``minimum_turns``). ``limit_per_turn`` isn't separately tracked for the
    same structural reason as king_extra_vessel_types above.
    """
    for effect in _king_effects(state, owner, registry, "construction_speed_bonus"):
        condition = effect.params.get("condition", {})
        required_type = condition.get("builder_piece_type")
        if required_type is not None and required_type != builder_piece_type:
            continue
        reduced = base_turns - effect.params.get("turns_reduced", 0)
        minimum = effect.params.get("minimum_turns", 1)
        return max(reduced, minimum)
    return base_turns


# ─────────────────────────────────────────────────────────────────────────────
# building_territory_bonus (architect_king) — read from territory.py
# ─────────────────────────────────────────────────────────────────────────────

def building_territory_radius_bonus(state: "GameState", owner: str, registry: "object | None") -> int:
    """
    Extra Territory radius granted to EVERY COMPLETE Building the owner
    controls, from their ACTIVE King's ``building_territory_bonus`` policy
    (architect_king: "completed Buildings project larger Territory").
    ``first_building_only`` isn't modeled — the PoC's Building count is
    small enough that a flat per-Building bonus doesn't need gating yet.
    """
    total = 0
    for effect in _king_effects(state, owner, registry, "building_territory_bonus"):
        total += effect.params.get("radius_bonus", 0)
    return total


# ─────────────────────────────────────────────────────────────────────────────
# Live effects — start-of-turn auras (formation_support, graveyard_threshold_
# bonus, spell_radius_bonus)
# ─────────────────────────────────────────────────────────────────────────────

def apply_king_policy_auras(state: "GameState", player_id: str, registry: "object | None") -> None:
    """
    Called at the start of ``player_id``'s turn (mirrors
    mechanics.buildings.apply_building_auras). Refreshes the ACTIVE King's
    aura-style policy effects onto owned Monster units. Idempotent — units
    that already carry the granted status are left alone.
    """
    if registry is None:
        return
    for effect in _king_effects(state, player_id, registry, "formation_support"):
        _apply_formation_support(state, player_id, effect, registry)
    for effect in _king_effects(state, player_id, registry, "graveyard_threshold_bonus"):
        _apply_graveyard_threshold_bonus(state, player_id, effect, registry)
    for effect in _king_effects(state, player_id, registry, "spell_radius_bonus"):
        _apply_king_spell_radius_bonus(state, player_id, effect, registry)
    # king_support_bonus, royal_support_suppression, enemy_territory_mobility,
    # ritual_information_discount — STUB: no functioning target system yet
    # (Final Duel Royal Support tally is Stage 12+; Rituals are Stage 11+).


def _apply_formation_support(state: "GameState", player_id: str, effect, registry: "object") -> None:
    """marshal_king: capture protection for archetype units with N+ adjacent allies."""
    from game.cards.card import MonsterCard

    params = effect.params
    archetype = params.get("archetype")
    required = params.get("adjacent_allies_required", 1)
    bonus = params.get("capture_protection_bonus", 1)

    units = list(state.board.all_units_for(player_id))
    for pos, unit in units:
        if unit.monster_id is None:
            continue
        try:
            card = registry.get(unit.monster_id)
        except KeyError:
            continue
        if not isinstance(card, MonsterCard) or card.archetype != archetype:
            continue
        adjacent_allies = sum(
            1 for other_pos, _ in units
            if other_pos != pos and in_area(other_pos, pos, 1)
        )
        if adjacent_allies < required:
            continue
        if not any(s.startswith("shield:") for s in unit.statuses):
            unit.add_status(f"shield:{bonus}")


def _apply_graveyard_threshold_bonus(state: "GameState", player_id: str, effect, registry: "object") -> None:
    """grave_crowned_king: capture protection once the Graveyard is large enough."""
    from game.cards.card import MonsterCard

    params = effect.params
    ps = state.get_player(player_id)
    if len(ps.graveyard) < params.get("threshold", 5):
        return
    archetype = params.get("target_archetype")
    bonus = params.get("bonus", {}).get("capture_protection_uses", 1)

    for pos, unit in state.board.all_units_for(player_id):
        if unit.monster_id is None:
            continue
        try:
            card = registry.get(unit.monster_id)
        except KeyError:
            continue
        if not isinstance(card, MonsterCard) or card.archetype != archetype:
            continue
        if not any(s.startswith("shield:") for s in unit.statuses):
            unit.add_status(f"shield:{bonus}")


def _apply_king_spell_radius_bonus(state: "GameState", player_id: str, effect, registry: "object") -> None:
    """arcane_sovereign: +radius to Spells cast by archetype units (spellcaster)."""
    from game.cards.card import MonsterCard

    params = effect.params
    archetype = params.get("condition", {}).get("source_archetype")
    bonus = params.get("bonus", 1)

    for pos, unit in state.board.all_units_for(player_id):
        if unit.monster_id is None:
            continue
        try:
            card = registry.get(unit.monster_id)
        except KeyError:
            continue
        if not isinstance(card, MonsterCard) or (archetype and card.archetype != archetype):
            continue
        if not any(s.startswith("spell_radius_bonus:") for s in unit.statuses):
            unit.add_status(f"spell_radius_bonus:{bonus}")


# ─────────────────────────────────────────────────────────────────────────────
# graveyard_recycle (grave_crowned_king) — read at Monster-destruction time
# ─────────────────────────────────────────────────────────────────────────────

def maybe_recycle_destroyed_monster(
    state: "GameState",
    owner: str,
    card_id: "str | None",
    events: "list[Event]",
    registry: "object | None",
) -> bool:
    """
    grave_crowned_king: the FIRST allied Monster destroyed on ``owner``'s
    own turn is recycled to the bottom of their deck instead of simply
    vanishing — destroyed Monster cards aren't otherwise tracked anywhere
    (no graveyard entry exists for board destruction today, only for
    discards/spent instants). Gated by
    ``PlayerState.king_recycle_used_this_turn``, reset every
    ``reset_turn_flags()`` call.

    Returns True if the recycle fired.
    """
    from game.core.events import CardsReturnedToDeck

    if card_id is None or registry is None:
        return False
    ps = state.get_player(owner)
    if ps.king_recycle_used_this_turn:
        return False
    if not _king_effects(state, owner, registry, "graveyard_recycle"):
        return False

    ps.king_recycle_used_this_turn = True
    ps.deck.append(card_id)  # "bottom_of_deck" — deck[0] is drawn next
    events.append(CardsReturnedToDeck(player_id=owner, card_ids=(card_id,)))
    return True
