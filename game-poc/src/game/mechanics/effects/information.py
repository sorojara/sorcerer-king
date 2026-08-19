"""
mechanics/effects/information.py — INFORMATION category
========================================================

Effect types in this category
------------------------------
reveal_hidden_info  STUB  — expose opponent's hidden state (needs the
                             Ritual system's SEALED/FORETOLD/REVEALED
                             states — target: "ritual" is the only
                             consumer today)
obscure_influence   STUB  — hide exact secondary modifiers (needs the
                             Spell/Trap zone system's secondary-modifier
                             concept, which doesn't exist yet)
reveal_enemy_hand_card IMPLEMENTED — Stage 6: one-time peek at N random
                             opponent hand cards (alarm_beacon)
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
# reveal_enemy_hand_card
# ---------------------------------------------------------------------------

def _reveal_enemy_hand_card(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — Stage 6 (alarm_beacon).

    Peeks at ``count`` random cards from the OWNER's opponent's hand and
    emits one EnemyCardRevealed(player_id=owner, ...) per card — a
    one-time, non-persistent reveal (see the event's docstring for why
    this doesn't touch Observation's hidden-info filtering).

    ``ctx.extra["owner_override"]`` supplies the Trap owner (see
    mechanics.monsters._fire_trap) since ``ctx.unit`` here is the
    triggering ENEMY piece, not the Trap owner.
    """
    from game.core.events import EnemyCardRevealed

    if ctx.state is None:
        return
    owner = (ctx.extra or {}).get("owner_override")
    if owner is None:
        return

    opponent = ctx.state.opponent_of(owner)
    pool = list(ctx.state.get_player(opponent).hand)
    if not pool:
        return

    count = min(ctx.effect.params.get("count", 1), len(pool))
    for _ in range(count):
        card_id = ctx.rng.choice(pool) if ctx.rng is not None else pool[0]
        pool.remove(card_id)
        ctx.events.append(EnemyCardRevealed(player_id=owner, revealed_card_id=card_id))


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

INFORMATION_HANDLERS: dict[str, object] = {
    "reveal_hidden_info": _reveal_hidden_info,
    "obscure_influence":  _obscure_influence,
    "reveal_enemy_hand_card": _reveal_enemy_hand_card,
}
