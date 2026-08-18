"""
mechanics/effects/buildings.py — BUILDINGS category
====================================================

Effect types in this category
------------------------------
construction_speed_bonus    STUB  — faster building (Stage 8+)
repair_building             STUB  — restore building HP (Stage 8+)
building_aura               STUB  — bonus durability to nearby buildings (Stage 8+)
disable_building            STUB  — deactivate an enemy building (Stage 8+)
territory_expansion         STUB  — extend building territory radius (Stage 8+)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from game.mechanics.effects.registry import EffectContext


def _construction_speed_bonus(ctx: "EffectContext") -> None:
    """
    STUB — Stage 8+

    Buildings constructed by adjacent Pawns complete ``turns_reduced`` turns
    faster (minimum ``minimum_turns`` turns).

    When implemented this will:
      1. When a StartBuilding action is executed: scan for adjacent monsters
         with this effect.
      2. Reduce the building's completion_turn counter by turns_reduced.
      3. Clamp to minimum_turns.
    """
    raise NotImplementedError(
        "construction_speed_bonus is not yet implemented (Stage 8+). "
        "Effect params: " + repr(ctx.effect.params)
    )


def _repair_building(ctx: "EffectContext") -> None:
    """
    STUB — Stage 8+

    At end of owner's turn (``trigger: end_of_turn``), restore ``amount``
    durability to one damaged allied building within ``radius`` squares.

    When implemented this will:
      1. On EndTurn: scan for adjacent buildings with durability < max.
      2. Restore up to ``amount`` durability.
      3. Emit a BuildingRepaired event (new event type needed in events.py).
    """
    raise NotImplementedError(
        "repair_building is not yet implemented (Stage 8+). "
        "Effect params: " + repr(ctx.effect.params)
    )


def _building_aura(ctx: "EffectContext") -> None:
    """
    STUB — Stage 8+

    Allied buildings within ``radius`` squares of this monster require
    one additional successful hostile action to destroy
    (``allied_building_bonus.durability: 1``).

    When implemented this will:
      1. At capture/destroy attempt of a nearby building: check for adjacent
         monsters with this effect.
      2. Apply a +1 durability bonus for that attempt.
    """
    raise NotImplementedError(
        "building_aura is not yet implemented (Stage 8+). "
        "Effect params: " + repr(ctx.effect.params)
    )


def _disable_building(ctx: "EffectContext") -> None:
    """
    STUB — Stage 8+

    At end of owner's turn (``trigger: adjacent_end_turn``), may disable one
    adjacent enemy building until the start of the next owner turn.

    When implemented this will:
      1. On EndTurn: mark adjacent enemy buildings with "disabled:1" temp effect.
      2. During building resolution: skip effects from disabled buildings.
      3. Clear the flag at the owner's next StartTurn.
    """
    raise NotImplementedError(
        "disable_building is not yet implemented (Stage 8+). "
        "Effect params: " + repr(ctx.effect.params)
    )


def _territory_expansion(ctx: "EffectContext") -> None:
    """
    STUB — Stage 8+

    Adjacent buildings project their Territory zone one additional square
    (``radius_bonus: 1, source: adjacent_buildings``).

    When implemented this will:
      1. During territory computation: scan for monsters with this effect
         adjacent to each building.
      2. Extend the building's effective territory radius by radius_bonus.
    """
    raise NotImplementedError(
        "territory_expansion is not yet implemented (Stage 8+). "
        "Effect params: " + repr(ctx.effect.params)
    )


BUILDINGS_HANDLERS: dict[str, object] = {
    "construction_speed_bonus": _construction_speed_bonus,
    "repair_building":          _repair_building,
    "building_aura":            _building_aura,
    "disable_building":         _disable_building,
    "territory_expansion":      _territory_expansion,
}
