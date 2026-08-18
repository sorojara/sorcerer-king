"""
mechanics/effects/ritual.py — RITUAL category
==============================================

Effect types in this category
------------------------------
ritual_progress_boost           IMPLEMENTED  — advance ritual counter (passive flag)
ritual_pattern_substitute       STUB         — count as generic ritual component (Stage 11+)
ritual_requirement_reduction    STUB         — reduce ritual req by 1 (Stage 11+)
ritual_reveal_tradeoff          STUB         — reveal own ritual to draw (Stage 11+)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from game.mechanics.effects.registry import EffectContext


# ---------------------------------------------------------------------------
# ritual_progress_boost
# ---------------------------------------------------------------------------

def _ritual_progress_boost(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — flag applied at summon time.

    Adds a ``ritual_boost:N`` status to the unit.  End-of-turn ritual
    processing (Stage 11) reads this flag and advances the ritual counter.
    """
    if ctx.unit is None:
        return
    trigger = ctx.effect.params.get("trigger", "end_of_turn")
    if trigger not in ("end_of_turn", "on_summon", "passive", ""):
        return

    amount = ctx.effect.params.get("amount", 1)
    ctx.unit.statuses = [s for s in ctx.unit.statuses if not s.startswith("ritual_boost:")]
    ctx.unit.add_status(f"ritual_boost:{amount}")


# ---------------------------------------------------------------------------
# ritual_pattern_substitute
# ---------------------------------------------------------------------------

def _ritual_pattern_substitute(ctx: "EffectContext") -> None:
    """
    STUB — Stage 11+

    This monster counts as one compatible generic ritual component for a
    ritual pattern formed within ``condition.within_radius`` squares.

    When implemented this will participate in the ritual-pattern matching
    algorithm in the RulesEngine's ritual resolution pass.
    """
    raise NotImplementedError(
        "ritual_pattern_substitute is not yet implemented (Stage 11+). "
        "Effect params: " + repr(ctx.effect.params)
    )


# ---------------------------------------------------------------------------
# ritual_requirement_reduction
# ---------------------------------------------------------------------------

def _ritual_requirement_reduction(ctx: "EffectContext") -> None:
    """
    STUB — Stage 11+

    Once per summon, reduce one ritual requirement by ``amount``.
    The ritual must become fully REVEALED (``cost.reveal_ritual: true``)
    and this effect is consumed (``cost.retire_after_use: true``).

    When implemented this will:
      1. Mark the unit with a "req_reduction:available" status.
      2. Provide a REDUCE_RITUAL_REQ action type during PREPARATION.
      3. Consume the status and reveal the ritual on use.
    """
    if ctx.unit is None:
        return
    ctx.unit.add_status("req_reduction:available")
    # NOTE: active resolution deferred to Stage 11+.


# ---------------------------------------------------------------------------
# ritual_reveal_tradeoff
# ---------------------------------------------------------------------------

def _ritual_reveal_tradeoff(ctx: "EffectContext") -> None:
    """
    STUB — Stage 11+

    On summon: the player may choose to reveal one SEALED ritual to draw
    ``draw_cards`` cards in return (``reveal_own: 1``).

    When implemented this will:
      1. At on_summon: raise a CHOOSE_REVEAL_TRADEOFF PendingDecision.
      2. If the player accepts: advance the ritual's revelation state and
         trigger a draw_card effect.
    """
    raise NotImplementedError(
        "ritual_reveal_tradeoff is not yet implemented (Stage 11+). "
        "Effect params: " + repr(ctx.effect.params)
    )


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

RITUAL_HANDLERS: dict[str, object] = {
    "ritual_progress_boost":        _ritual_progress_boost,
    "ritual_pattern_substitute":    _ritual_pattern_substitute,
    "ritual_requirement_reduction": _ritual_requirement_reduction,
    "ritual_reveal_tradeoff":       _ritual_reveal_tradeoff,
}
