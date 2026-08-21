"""
mechanics/effects/defense.py — DEFENSE category
================================================

Effect types in this category
------------------------------
capture_protection  IMPLEMENTED  — absorb N capture attempts (shield:N status)
trap_immunity       IMPLEMENTED  — ignore the first Trap effect that would
                                    affect this unit (ancient_tortoise); the
                                    flag is set here, consumed in
                                    mechanics.monsters.check_enter_radius_traps()
                                    / check_capture_traps() (Stage 6)
intercept           STUB         — block attack targeting adjacent king
                                    (needs Final Duel / King targeting infra)
retaliate           IMPLEMENTED  — destroy attacker when this unit is captured
                                    (thorn_boar; consumed in rules.py's
                                    _execute_move_piece right after the
                                    capture is resolved)
capture_vulnerability IMPLEMENTED — Stage 6: "Exposed" — bypasses
                                    capture_protection for a few turns
                                    (pit_trap, counter_strike)
mark_unit           IMPLEMENTED  — vengeance_mark's "Vulnerable" status;
                                    mechanically identical to
                                    capture_vulnerability's "Exposed" (arms
                                    "exposed:N" on the capturing piece), so
                                    it reuses the same handler rather than
                                    introducing a parallel status nothing
                                    else would ever read
cancel_capture      IMPLEMENTED  — guardian_sigils; the real logic runs
                                    PRE-EMPTIVELY in
                                    mechanics.monsters.find_cancel_capture_trap(),
                                    called from _execute_move_piece
                                    alongside apply_capture_protection,
                                    BEFORE the capture happens — there's no
                                    "after the capture" moment to react to,
                                    so this entry is a no-op placeholder
                                    only so the type is recognised
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
# capture_vulnerability  (Stage 6 — pit_trap, counter_strike)
# ---------------------------------------------------------------------------

def _capture_vulnerability(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — Stage 6 Trap effect ("Exposed").

    Flags the target as "exposed:<duration_turns>" — while exposed, its
    capture_protection shield (if any) does NOT block an incoming capture;
    see mechanics.monsters.apply_capture_protection(), which checks this
    status before consuming a shield charge.  Decays on the unit OWNER's
    own EndTurn, same as immobilize_piece.
    """
    if ctx.unit is None:
        return
    duration = ctx.effect.params.get("duration_turns", 1)
    ctx.unit.statuses = [s for s in ctx.unit.statuses if not s.startswith("exposed:")]
    ctx.unit.add_status(f"exposed:{duration}")


def _cancel_capture(ctx: "EffectContext") -> None:
    """No-op placeholder — see module docstring. Real logic lives in
    mechanics.monsters.find_cancel_capture_trap()."""
    pass


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

DEFENSE_HANDLERS: dict[str, object] = {
    "capture_protection":    _capture_protection,
    "trap_immunity":         _trap_immunity,
    "intercept":             _intercept,
    "retaliate":             _retaliate,
    "capture_vulnerability": _capture_vulnerability,
    "mark_unit":             _capture_vulnerability,
    "cancel_capture":        _cancel_capture,
}
