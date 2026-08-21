"""
mechanics/effects/information.py — INFORMATION category
========================================================

Effect types in this category
------------------------------
reveal_hidden_info  IMPLEMENTED — Stage 11 — one random SEALED enemy
                             Ritual advances to FORETOLD (target: "ritual"
                             is the only consumer today)
obscure_influence   IMPLEMENTED — hides a square effect's exact card_id
                             (which Spell/Trap caused it) from anyone but
                             its owner within radius of a veil_conjurer,
                             unless the observer occupies that square;
                             real logic lives in core.observation via
                             build_observation()'s PublicSquareEffect
                             filtering (queried live, like damage_aura)
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
    IMPLEMENTED — Stage 11 (raven_scout).

    ``target: "ritual"`` is the only consumer today: one random SEALED
    enemy Ritual advances to FORETOLD (README §15.1's "opponent
    disruption" trigger — mechanics.rituals.promote_one_step, which never
    skips a step, so ``reveal_level`` isn't separately honoured beyond
    "one step" — every card using this effect today only ever wants
    SEALED→FORETOLD anyway). ``selection: random`` is the only supported
    selection strategy; anything else is a no-op rather than a crash, so a
    future card data-entry typo doesn't take the engine down.
    """
    if ctx.unit is None or ctx.state is None:
        return
    params = ctx.effect.params
    if params.get("target") != "ritual":
        return
    if params.get("selection", "random") != "random":
        return

    from game.mechanics.rituals import reveal_random_sealed

    owner = ctx.unit.owner
    opponent = ctx.state.opponent_of(owner)
    reveal_random_sealed(ctx.state, opponent, ctx.events, rng=ctx.rng)


# ---------------------------------------------------------------------------
# obscure_influence
# ---------------------------------------------------------------------------

def _obscure_influence(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — veil_conjurer passive aura (real logic lives elsewhere).

    Within ``radius`` squares of this monster, nearby Spell/Trap square
    effects (frozen/scorched/blocked/cursed) remain visible — effect_type,
    duration, owner — but the exact ``card_id`` that caused them is hidden
    from anyone but the effect's own owner, unless the observer currently
    occupies that square. Queried live from
    core.observation.build_observation() (via _obscured_by_veil_conjurer),
    the same "scan the board, don't arm a status" pattern as damage_aura.
    This entry is a no-op placeholder so the type is recognised by the
    registry and never logged as unresolved.
    """
    # Actual implementation lives in core.observation._obscured_by_veil_conjurer().
    pass


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
