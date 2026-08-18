"""
mechanics/effects/king_duel.py — KING / DUEL category
======================================================

Effect types in this category
------------------------------
king_support_bonus          STUB  — improve Royal Support near king (Stage 9+)
royal_support_suppression   STUB  — reduce enemy Royal Support (Stage 9+)
duel_debuff                 STUB  — penalise defender at duel start (Stage 12+)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from game.mechanics.effects.registry import EffectContext


# ---------------------------------------------------------------------------
# king_support_bonus
# ---------------------------------------------------------------------------

def _king_support_bonus(ctx: "EffectContext") -> None:
    """
    STUB — Stage 9+

    Within ``radius`` squares of the allied King, this monster grants
    ``bonus`` additional Royal Support value.

    When implemented this will participate in the Royal-Support tallying
    algorithm called at FINAL_DUEL start.
    """
    if ctx.unit is None:
        return
    # Passive flag — Royal Support tallying reads this at duel resolution.
    radius = ctx.effect.params.get("radius", 2)
    bonus  = ctx.effect.params.get("bonus", 1)
    ctx.unit.add_status(f"king_support_bonus:{radius}:{bonus}")
    # NOTE: active resolution deferred to Stage 9+.


# ---------------------------------------------------------------------------
# royal_support_suppression
# ---------------------------------------------------------------------------

def _royal_support_suppression(ctx: "EffectContext") -> None:
    """
    STUB — Stage 9+

    Enemy units within ``radius`` squares contribute ``amount`` fewer points
    of Royal Support if a Final Duel begins while this monster is nearby.

    When implemented this will subtract from enemy Royal Support at FINAL_DUEL
    start (only units within radius at that moment are affected).
    """
    if ctx.unit is None:
        return
    radius = ctx.effect.params.get("radius", 2)
    amount = ctx.effect.params.get("amount", 1)
    ctx.unit.add_status(f"royal_suppression:{radius}:{amount}")
    # NOTE: active resolution deferred to Stage 9+.


# ---------------------------------------------------------------------------
# duel_debuff
# ---------------------------------------------------------------------------

def _duel_debuff(ctx: "EffectContext") -> None:
    """
    STUB — Stage 12+

    At FINAL_DUEL start (``trigger: final_duel_start``), if this monster is
    within ``condition.distance_to_enemy_king`` squares of the enemy King,
    the defender begins the Duel with ``defender_guard_penalty`` fewer Guards.

    When implemented this will:
      1. Check the condition at FINAL_DUEL trigger time.
      2. Modify the initial duel state's defender guard count.
    """
    if ctx.unit is None:
        return
    # Passive flag read at duel initialization.
    penalty = ctx.effect.params.get("defender_guard_penalty", 1)
    ctx.unit.add_status(f"duel_debuff:{penalty}")
    # NOTE: active resolution deferred to Stage 12+.


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

KING_DUEL_HANDLERS: dict[str, object] = {
    "king_support_bonus":        _king_support_bonus,
    "royal_support_suppression": _royal_support_suppression,
    "duel_debuff":               _duel_debuff,
}
