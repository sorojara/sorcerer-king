"""
mechanics/effects/information.py — INFORMATION category
========================================================

Effect types in this category
------------------------------
reveal_hidden_info  STUB  — expose opponent's hidden state (Stage 6+)
obscure_influence   STUB  — hide exact secondary modifiers (Stage 6+)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from game.mechanics.effects.registry import EffectContext


# ---------------------------------------------------------------------------
# reveal_hidden_info
# ---------------------------------------------------------------------------

def _reveal_hidden_info(ctx: "EffectContext") -> None:
    """
    STUB — Stage 6+

    Reveals hidden information to the owner.  The ``target`` and
    ``reveal_level`` params control what is revealed:

        target: "ritual"      → reveal an enemy Ritual
        reveal_level: "foretold" → advance from SEALED to FORETOLD

    Example: raven_scout on_summon → one random SEALED enemy Ritual becomes
    FORETOLD.

    When implemented this will:
      1. Select the target based on params (random / closest / etc.).
      2. Advance the target's revelation state in GameState.
      3. Emit a RitualRevelationChanged event.
    """
    raise NotImplementedError(
        "reveal_hidden_info is not yet implemented (Stage 6+). "
        "Effect params: " + repr(ctx.effect.params)
    )


# ---------------------------------------------------------------------------
# obscure_influence
# ---------------------------------------------------------------------------

def _obscure_influence(ctx: "EffectContext") -> None:
    """
    STUB — Stage 6+

    Within ``radius`` squares of this monster, nearby Spell and Trap
    influence remains visible but opponents cannot inspect the exact
    secondary modifiers (``hide_exact_effect: true``).

    When implemented this will:
      1. Tag the affected zone in the board's temporary_effects.
      2. During Observation construction: filter out secondary modifier
         details for squares inside the zone when building the opponent's view.
    """
    raise NotImplementedError(
        "obscure_influence is not yet implemented (Stage 6+). "
        "Effect params: " + repr(ctx.effect.params)
    )


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

INFORMATION_HANDLERS: dict[str, object] = {
    "reveal_hidden_info": _reveal_hidden_info,
    "obscure_influence":  _obscure_influence,
}
