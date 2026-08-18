"""
mechanics/effects/defense.py — DEFENSE category
================================================

Effect types in this category
------------------------------
capture_protection  IMPLEMENTED  — absorb N capture attempts (shield:N status)
trap_immunity       STUB         — ignore first trap trigger (Stage 8+)
intercept           STUB         — block attack targeting adjacent king (Stage 9+)
retaliate           STUB         — destroy attacker when this unit is captured (Stage 7+)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from game.mechanics.effects.registry import EffectContext


# ---------------------------------------------------------------------------
# capture_protection
# ---------------------------------------------------------------------------

def _capture_protection(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — absorbs the first N capture attempts (arcane_sentinel,
    stone_golem, ancient_tortoise).

    Applies ``shield:N`` status to the unit at summon time.
    The RulesEngine checks and consumes shields in _execute_move_piece().
    """
    if ctx.unit is None:
        return
    trigger = ctx.effect.params.get("trigger", "on_summon")
    if trigger not in ("on_summon", "passive", ""):
        return

    uses = ctx.effect.params.get("uses", 1)
    ctx.unit.statuses = [s for s in ctx.unit.statuses if not s.startswith("shield:")]
    ctx.unit.add_status(f"shield:{uses}")


# ---------------------------------------------------------------------------
# trap_immunity
# ---------------------------------------------------------------------------

def _trap_immunity(ctx: "EffectContext") -> None:
    """
    STUB — Stage 8+

    Flags the unit so it ignores the first Trap effect that would target it
    (``uses: 1``).

    When implemented this will:
      1. At summon: set "trap_immune:N" status on the unit.
      2. In trap-resolution code: if target unit has trap_immune:N, decrement N
         and skip the trap effect.
    """
    if ctx.unit is None:
        return
    trigger = ctx.effect.params.get("trigger", "on_summon")
    if trigger not in ("on_summon", "passive", ""):
        return

    uses = ctx.effect.params.get("uses", 1)
    ctx.unit.statuses = [s for s in ctx.unit.statuses if not s.startswith("trap_immune:")]
    ctx.unit.add_status(f"trap_immune:{uses}")
    # NOTE: trap resolution must check and consume this status (Stage 8+).


# ---------------------------------------------------------------------------
# intercept
# ---------------------------------------------------------------------------

def _intercept(ctx: "EffectContext") -> None:
    """
    STUB — Stage 9+

    When an adjacent King is targeted for capture, this unit may intercept
    and absorb that capture instead (``trigger: adjacent_king_targeted``,
    ``uses: 1``).

    When implemented this will require a pre-capture hook that:
      1. Detects that the target is an allied King.
      2. Scans adjacent units for one with an "intercept:N" status.
      3. Redirects the capture to the interceptor unit.
      4. Decrements / removes the status.
    """
    if ctx.unit is None:
        return
    uses = ctx.effect.params.get("uses", 1)
    ctx.unit.statuses = [s for s in ctx.unit.statuses if not s.startswith("intercept:")]
    ctx.unit.add_status(f"intercept:{uses}")
    # NOTE: intercept resolution is a pre-capture hook (Stage 9+).


# ---------------------------------------------------------------------------
# retaliate
# ---------------------------------------------------------------------------

def _retaliate(ctx: "EffectContext") -> None:
    """
    STUB — Stage 7+

    If this unit is destroyed by a capture (``trigger: capture_attempt``),
    the attacking unit is also destroyed (``destroy_attacker_if_destroyed: true``).

    At summon: flag "retaliate" status on the unit.
    At capture: the RulesEngine must check the flag and destroy the attacker.
    """
    if ctx.unit is None:
        return
    trigger = ctx.effect.params.get("trigger", "capture_attempt")
    if trigger not in ("capture_attempt", "on_summon", "passive", ""):
        return

    ctx.unit.add_status("retaliate")
    # NOTE: retaliate resolution is a post-capture hook (Stage 7+).


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

DEFENSE_HANDLERS: dict[str, object] = {
    "capture_protection": _capture_protection,
    "trap_immunity":      _trap_immunity,
    "intercept":          _intercept,
    "retaliate":          _retaliate,
}
