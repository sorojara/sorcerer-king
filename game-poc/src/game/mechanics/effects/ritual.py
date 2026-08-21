"""
mechanics/effects/ritual.py — RITUAL category
==============================================

Effect types in this category
------------------------------
ritual_progress_boost           IMPLEMENTED (unit-level flag; Stage 11's
                                 actual progress accounting is a direct
                                 board scan — see below)
ritual_pattern_substitute       IMPLEMENTED — Stage 11 — generic ritual
                                 component (mechanics/rituals.py's pattern
                                 matcher reads the status this arms)
ritual_requirement_reduction    IMPLEMENTED — Stage 11 — reduce a chosen
                                 Ritual's requirement by 1, once
ritual_reveal_tradeoff          IMPLEMENTED — Stage 11 — reveal own ritual
                                 to draw

Stage 11 wiring note: ``ritual_progress_boost`` (ritual_acolyte)'s actual
effect — advancing a Ritual's revelation progress — is NOT applied via this
handler. Its trigger is "end_of_turn", which apply_on_summon_effects
deliberately never fires (that pipeline only fires on_summon-compatible
triggers). Instead core/rules.py._execute_end_turn calls
mechanics.rituals.advance_ritual_progress(), which scans the ending
player's board directly for this effect type each turn (mirroring how
_resolve_restore_effect_charge handles battlefield_medic). The handler
below still arms a cosmetic ``ritual_boost:N`` status on summon for
anything that might want to display it; it plays no role in the actual
progress accounting.
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
    Cosmetic on-summon flag only — see module docstring for where the real
    progress accounting happens (mechanics.rituals.advance_ritual_progress,
    called every end-of-turn, not through this handler).
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
    IMPLEMENTED — Stage 11 (circle_keeper).

    On-summon: arms a ``ritual_substitute`` status on this unit. When
    validating or enumerating a Formation Ritual (mechanics/rituals.py
    _node_matches), a unit carrying this status satisfies ANY pattern
    node's piece_type requirement, as long as it still occupies the exact
    board offset that node demands — "counts as one compatible generic
    ritual component". The card's own ``condition.within_radius`` isn't
    separately modeled: the PoC keeps the substitution scoped to "this
    unit, wherever it stands", rather than adding a second radius-search
    pass to the pattern matcher for a one-card feature.
    """
    if ctx.unit is None:
        return
    ctx.unit.add_status("ritual_substitute")


# ---------------------------------------------------------------------------
# ritual_requirement_reduction
# ---------------------------------------------------------------------------

def _ritual_requirement_reduction(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — Stage 11 (forbidden_priest).

    Two-phase, matching the card text ("Once per summon, reduce one Ritual
    requirement by 1... Forbidden Priest loses this effect afterward"):

      on_summon  — arms a "req_reduction:available" status (unconsumed).
      activated  — (ACTIVATED_EFFECT_TYPES; fired via ActivateMonsterAbility
                   during CHESS) consumes that status and applies
                   ``amount`` to ``ctx.extra["target"]`` (a ritual_id in the
                   owner's own pool that isn't REVEALED yet), forcing that
                   Ritual to REVEALED and bumping its RitualState.
                   requirement_reduction — see mechanics/rituals.py
                   _effective_min_material / _effective_min_sacrifices /
                   _effective_pattern_nodes for how each condition type
                   spends it.
    """
    if ctx.unit is None:
        return

    if ctx.trigger != "activated":
        ctx.unit.add_status("req_reduction:available")
        return

    if "req_reduction:available" not in ctx.unit.statuses:
        return
    ritual_id = (ctx.extra or {}).get("target")
    if not ritual_id or ctx.state is None:
        return

    from game.core.phases import RevelationState
    from game.core.events import RitualRevelationChanged
    from game.mechanics.rituals import get_ritual_state

    rstate = get_ritual_state(ctx.state, ctx.unit.owner, ritual_id)
    if rstate is None or rstate.activated or rstate.revelation == RevelationState.REVEALED:
        return

    ctx.unit.remove_status("req_reduction:available")
    amount = ctx.effect.params.get("amount", 1)
    rstate.requirement_reduction += amount

    old = rstate.revelation
    rstate.revelation = RevelationState.REVEALED
    ctx.events.append(RitualRevelationChanged(
        player_id=ctx.unit.owner, ritual_id=ritual_id,
        old_state=old, new_state=RevelationState.REVEALED,
    ))


# ---------------------------------------------------------------------------
# ritual_reveal_tradeoff
# ---------------------------------------------------------------------------

def _ritual_reveal_tradeoff(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — Stage 11 (omen_reader), README §15.2 "Information as a
    Resource".

    On summon: if the owner has any Ritual that isn't yet REVEALED, reveal
    it one step (SEALED→FORETOLD or FORETOLD→REVEALED — see
    mechanics.rituals.promote_one_step; matches every OTHER revelation
    trigger's "never skip a step" rule) and draw ``draw_cards`` cards.
    Unconditional/automatic rather than an explicit player choice — no
    PendingDecision is raised — matching how apprentice_mage's unconditional
    draw_card already works; there is exactly one SEALED-or-FORETOLD Ritual
    worth targeting most of the time (data/rituals.yaml ships 3), so the
    "which one" choice rarely matters and always targets the pool's first
    not-yet-REVEALED entry (pool order).
    """
    if ctx.unit is None or ctx.state is None:
        return
    params = ctx.effect.params
    trigger = params.get("trigger", "on_summon")
    if trigger not in ("on_summon", "passive", ""):
        return

    from game.core.phases import RevelationState
    from game.mechanics.rituals import promote_one_step

    owner = ctx.unit.owner
    ps = ctx.state.get_player(owner)
    target = next((rs for rs in ps.ritual_pool if rs.revelation != RevelationState.REVEALED), None)
    if target is None:
        return
    if not promote_one_step(ctx.state, owner, target.ritual_id, ctx.events):
        return

    from game.core.events import CardDrawn, DeckRecycled

    count = params.get("draw_cards", 1)
    for _ in range(count):
        if not ps.deck and ps.graveyard:
            ps.deck = list(ps.graveyard)
            ps.graveyard.clear()
            if ctx.rng is not None:
                ctx.rng.shuffle(ps.deck)
            ctx.events.append(DeckRecycled(player_id=owner, card_count=len(ps.deck)))
        if ps.deck:
            card_id = ps.deck.pop(0)
            ps.hand.append(card_id)
            ctx.events.append(CardDrawn(player_id=owner, card_id=card_id))


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

RITUAL_HANDLERS: dict[str, object] = {
    "ritual_progress_boost":        _ritual_progress_boost,
    "ritual_pattern_substitute":    _ritual_pattern_substitute,
    "ritual_requirement_reduction": _ritual_requirement_reduction,
    "ritual_reveal_tradeoff":       _ritual_reveal_tradeoff,
}
