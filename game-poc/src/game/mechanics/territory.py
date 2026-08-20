"""
mechanics/territory.py — Stage 9: Territory
=============================================

Territory represents developed control of an area of the board (README §13).

Two layers combine into a player's Territory:

    1. Home territory — the player's own first three ranks (user brief:
       "first 3 rows are from each team's territory"). White = ranks 1-3
       (index 0-2); Black = ranks 6-8 (index 5-7). Ranks 4-5 (index 3-4)
       start as neutral no-man's-land.

    2. Building territory — a radius around each of the player's COMPLETE
       Buildings, scaling with the Building's size (BuildingCard.
       territory_radius: small=1, medium=2, major=3 — "increase size of
       radius over size of building").

Territory is intentionally NOT exclusive: a square can sit inside both
players' territory at once (contested ground once both sides have pushed
Buildings toward the centre) — this module answers "is X in PLAYER's
territory", never "who owns this square".

Trap placement / Spell targeting draw on a DELIBERATELY NARROWER zone than
plain Territory (user correction): ONLY squares covered by a Building's own
radius qualify — the bare home three ranks do NOT, on their own, make Traps
or Spells placeable there. "The original first 3 rows should only be trap
settable if they have a summoned monster or a building covering its area
— not available for traps by default." trap_zone_squares() /
spell_zone_squares() below are therefore NOT supersets of territory_squares()
— they're a separate, Building-only view. Some Buildings project a LARGER
radius specifically for this (README §12.4: Watchtower "extend
support/influence range", Shrine "Ritual radius" reinterpreted as arcane
reach) via trap_territory_bonus / spell_territory_bonus effects.

Consumers (core/rules.py):
    • SummonMonster — the vessel square MUST be in the caster's Territory
      (user brief: "summoning monsters into vessels can only happen in
      your territory") — home ranks count here. This is the one hard
      requirement, and the only place the bare home ranks matter.
    • PlaceTrap / Spell targeting — a square qualifies EITHER by hosting
      the caster's own summoned Monster / being reachable by a non-Pawn
      piece (the pre-existing Monster-anchor / piece-reach rule —
      SPELL_TARGET_REACH / TRAP_MONSTER_ANCHOR), OR by sitting inside a
      Building's radius (trap_zone_squares / spell_zone_squares — NOT
      bare home-rank Territory). A square only needs to satisfy ONE of
      the two ("can be satisfied BY both", never required to satisfy
      both).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from game.chess.pieces import Position
from game.core.phases import ConstructionStatus
from game.mechanics.area import expand_area

if TYPE_CHECKING:
    from game.core.state import GameState


# Home-rank indices (0 = rank 1 ... 7 = rank 8). "First 3 rows" per side.
HOME_RANKS: dict[str, tuple[int, ...]] = {
    "white": (0, 1, 2),
    "black": (5, 6, 7),
}


def is_home_territory(pos: Position, player_id: str) -> bool:
    return pos.rank in HOME_RANKS.get(player_id, ())


def _building_zone_squares(
    state: "GameState",
    player_id: str,
    registry: "object | None",
    bonus_effect_type: str | None = None,
) -> set[Position]:
    """
    Union of the area around every COMPLETE Building ``player_id`` owns.

    Each Building's own radius is ``card.territory_radius`` (derived from
    size). If ``bonus_effect_type`` is given and the Building carries an
    EffectEntry of that type, its ``bonus`` param (default 1) is added on
    top — this is how Watchtower/Shrine push Trap/Spell reach further out
    than plain territory without changing SummonMonster's boundary.

    Returns an empty set (no registry ⇒ no Building data to look up) when
    ``registry`` is None, matching every other Stage 8/9 building query.
    """
    if registry is None:
        return set()

    from game.cards.card import BuildingCard

    squares: set[Position] = set()
    for b in state.buildings:
        if b.owner != player_id or b.status != ConstructionStatus.COMPLETE:
            continue
        try:
            card = registry.get(b.building_card_id)
        except KeyError:
            continue
        if not isinstance(card, BuildingCard):
            continue

        radius = card.territory_radius
        if bonus_effect_type is not None:
            for effect in card.effects:
                if effect.type == bonus_effect_type:
                    radius += effect.params.get("bonus", 1)

        # Stage 10: architect_king's building_territory_bonus policy —
        # every COMPLETE Building the owner controls projects a larger
        # Territory while that King is active (README §12.4 / §20).
        from game.mechanics.kings import building_territory_radius_bonus
        radius += building_territory_radius_bonus(state, player_id, registry)

        squares.update(expand_area(b.position, radius, "square"))
    return squares


def territory_squares(state: "GameState", player_id: str, registry: "object | None" = None) -> set[Position]:
    """Every square that counts as ``player_id``'s Territory right now."""
    squares = {
        Position(f, r)
        for f in range(8)
        for r in HOME_RANKS.get(player_id, ())
    }
    squares.update(_building_zone_squares(state, player_id, registry))
    return squares


def is_in_territory(state: "GameState", pos: Position, player_id: str, registry: "object | None" = None) -> bool:
    if is_home_territory(pos, player_id):
        return True
    return pos in _building_zone_squares(state, player_id, registry)


def trap_zone_squares(state: "GameState", player_id: str, registry: "object | None" = None) -> set[Position]:
    """
    Squares where a Trap may be placed via Building coverage — NOT plain
    Territory. Every COMPLETE Building's own radius counts, extended
    further by a Watchtower-style ``trap_territory_bonus`` effect. The
    bare home three ranks do NOT qualify on their own (user correction).
    """
    return _building_zone_squares(state, player_id, registry, bonus_effect_type="trap_territory_bonus")


def spell_zone_squares(state: "GameState", player_id: str, registry: "object | None" = None) -> set[Position]:
    """
    Squares where a Spell may be targeted via Building coverage — NOT
    plain Territory. Every COMPLETE Building's own radius counts, extended
    further by a Shrine-style ``spell_territory_bonus`` effect. The bare
    home three ranks do NOT qualify on their own (mirrors trap_zone_squares
    per the user's original brief: "spell activation area, similar to
    traps").
    """
    return _building_zone_squares(state, player_id, registry, bonus_effect_type="spell_territory_bonus")


def is_in_trap_zone(state: "GameState", pos: Position, player_id: str, registry: "object | None" = None) -> bool:
    return pos in trap_zone_squares(state, player_id, registry)


def is_in_spell_zone(state: "GameState", pos: Position, player_id: str, registry: "object | None" = None) -> bool:
    return pos in spell_zone_squares(state, player_id, registry)
