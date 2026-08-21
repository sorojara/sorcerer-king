"""
mechanics/effects/king_duel.py — KING / DUEL category
======================================================

Effect types in this category
------------------------------
king_support_bonus          on_summon passive tag; READ by mechanics/duel.py
                             initialize_duel() at Final Duel start (Stage 12).
royal_support_suppression   on_summon passive tag; READ by mechanics/duel.py
                             initialize_duel() at Final Duel start (Stage 12).
duel_debuff                 not fired on_summon (trigger=final_duel_start);
                             mechanics/duel.py reads the effect straight off
                             the card at Duel start instead (Stage 12).
royal_support_mark          IMPLEMENTED — enter_radius Trap effect
                             (kingslayer_alarm); tags the triggering piece,
                             read by mechanics/duel.py at Duel start.
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
    Not on_summon-compatible (params.trigger == "final_duel_start" — see
    apply_on_summon_effects' allowed-trigger list), so this handler is never
    actually dispatched: mechanics/duel.py.initialize_duel() reads the
    ``duel_debuff`` EffectEntry straight off the card at Final Duel start
    instead (it needs the live distance-to-arena-center at that moment,
    which on_summon time can't know). Kept only so an unexpected dispatch
    (e.g. a future generic on_summon sweep) fails soft instead of logging
    UNRESOLVED.

    Card text: if this monster is within
    ``condition.distance_to_enemy_king`` squares of the enemy King when the
    Duel begins, the defender starts with ``defender_guard_penalty`` fewer
    Guards.
    """
    if ctx.unit is None:
        return
    penalty = ctx.effect.params.get("defender_guard_penalty", 1)
    ctx.unit.add_status(f"duel_debuff:{penalty}")


# ---------------------------------------------------------------------------
# royal_support_mark
# ---------------------------------------------------------------------------

def _royal_support_mark(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — Stage 12. kingslayer_alarm (traps.yaml): an enemy unit
    entering the Trap's radius (``trigger: enter_radius``) is tagged
    ``duel_marked:<penalty>``. mechanics/duel.py._gather_piece_support()
    reduces that unit's own generated DuelSupportItem amount by
    ``penalty`` (floored at 0 — the item is simply dropped) when a Final
    Duel later begins with the marked unit still on the board.

    ``params.target`` is already resolved to ``ctx.unit`` by the enter_radius
    trigger-detection pathway (mechanics/monsters.py check_enter_radius_traps)
    before this handler ever runs — see registry.py's TARGET SELECTION note.
    """
    if ctx.unit is None:
        return
    penalty = ctx.effect.params.get("duel_penalty", 1)
    ctx.unit.add_status(f"duel_marked:{penalty}")


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

KING_DUEL_HANDLERS: dict[str, object] = {
    "king_support_bonus":        _king_support_bonus,
    "royal_support_suppression": _royal_support_suppression,
    "duel_debuff":               _duel_debuff,
    "royal_support_mark":        _royal_support_mark,
}
