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
ritual_requirement_reduction    IMPLEMENTED — reduce a Ritual's requirement
                                 by 1, once: as forbidden_priest's activated
                                 ability, or outright on cast as
                                 hasten_the_ritual (Spell, no unit)
ritual_reveal_tradeoff          IMPLEMENTED — Stage 11 — reveal own ritual
                                 to draw
reveal_ritual                   IMPLEMENTED — two usages sharing one type:
                                 ``own_ritual: true`` (ritual_insight) reveals
                                 the caster's own first not-REVEALED Ritual one
                                 step + draws cards; ``target_owner: opponent``
                                 (omen_bell) forces one of the triggering
                                 enemy's SEALED Rituals to FORETOLD
reveal_enemy_ritual              IMPLEMENTED — forbidden_knowledge: one random
                                 SEALED enemy Ritual becomes FORETOLD (reuses
                                 reveal_random_sealed; "selection: player" is
                                 simplified to random — no interactive
                                 mid-Spell target-choice flow exists)
ritual_bluff                     IMPLEMENTED — false_prophecy: one of the
                                 caster's own SEALED Rituals shows the
                                 OPPONENT a fabricated FORETOLD requirement
                                 for ``duration_turns`` (RitualState.bluff_turns
                                 — see core/observation.py); the Ritual stays
                                 truly SEALED for every engine check
interrupt_ritual                 IMPLEMENTED — profane_interruption: blocks
                                 ONE enemy ActivateRitual attempt whose
                                 sacrifice material overlaps the Trap's
                                 radius (RulesEngine._execute_activate_ritual,
                                 before validate_ritual runs — so nothing is
                                 sacrificed); consumes the Trap like any other
                                 single-charge Trap, rather than tracking a
                                 literal "retry next turn" delay

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
    params = ctx.effect.params
    trigger = params.get("trigger", "end_of_turn")
    amount = params.get("amount", 1)

    # ritual_acceleration (Spell, target_type "none") — there is no unit to
    # hang a per-turn boost on and no later moment to collect it, so the
    # progress is applied on cast, through the same accounting
    # advance_ritual_progress uses at end of turn (threshold promotions
    # included).
    if trigger == "instant_spell":
        if ctx.state is None:
            return
        owner = (ctx.extra or {}).get("caster_owner")
        if owner is None:
            return
        from game.mechanics.rituals import apply_ritual_progress

        apply_ritual_progress(ctx.state, owner, amount, ctx.events, ctx.registry)
        return

    if ctx.unit is None:
        return
    if trigger not in ("end_of_turn", "on_summon", "passive", ""):
        return

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
    from game.core.phases import RevelationState
    from game.core.events import RitualRevelationChanged
    from game.mechanics.rituals import get_ritual_state

    if ctx.state is None:
        return

    # ── hasten_the_ritual (Spell, target_type "none" — ctx.unit is None) ──
    # "Fully reveal one of your Rituals. For its next activation attempt,
    # one generic requirement is ignored." Same two effects as the Monster
    # branch below (force REVEALED + bank a requirement_reduction), but with
    # no unit to carry a charge: the Spell IS the charge, spent on cast.
    # Targets the caster's own first not-yet-REVEALED Ritual in pool order,
    # the same "the choice rarely matters" simplification used throughout
    # this module.
    if ctx.unit is None:
        owner = (ctx.extra or {}).get("caster_owner")
        if owner is None:
            return
        ps = ctx.state.get_player(owner)
        rstate = next(
            (rs for rs in ps.ritual_pool
             if not rs.activated and rs.revelation != RevelationState.REVEALED),
            None,
        )
        if rstate is None:
            return
        rstate.requirement_reduction += ctx.effect.params.get("amount", 1)
        previous = rstate.revelation
        rstate.revelation = RevelationState.REVEALED
        ctx.events.append(RitualRevelationChanged(
            player_id=owner, ritual_id=rstate.ritual_id,
            old_state=previous, new_state=RevelationState.REVEALED,
        ))
        return

    if ctx.trigger != "activated":
        ctx.unit.add_status("req_reduction:available")
        return

    if "req_reduction:available" not in ctx.unit.statuses:
        return
    ritual_id = (ctx.extra or {}).get("target")
    if not ritual_id:
        return

    rstate = get_ritual_state(ctx.state, ctx.unit.owner, ritual_id)
    if rstate is None or rstate.activated or rstate.revelation == RevelationState.REVEALED:
        return

    ctx.unit.remove_status("req_reduction:available")
    amount = ctx.effect.params.get("amount", 1)
    rstate.requirement_reduction += amount

    previous = rstate.revelation
    rstate.revelation = RevelationState.REVEALED
    ctx.events.append(RitualRevelationChanged(
        player_id=ctx.unit.owner, ritual_id=ritual_id,
        old_state=previous, new_state=RevelationState.REVEALED,
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
    # omen_reader fires the moment it lands ("On summon, you may reveal
    # one SEALED Ritual to draw one card"). oracle_of_the_last_star's copy
    # is ``once_per_turn`` instead — an ACTIVATED ability, reachable via
    # ActivateMonsterAbility (ritual_reveal_tradeoff is already in
    # ACTIVATED_EFFECT_TYPES) — so it must accept a dispatch that arrives
    # with ctx.trigger == "activated" rather than rejecting its own
    # declared trigger and doing nothing at all.
    if trigger in ("once_per_turn", "activated"):
        if ctx.trigger != "activated":
            return
    elif trigger not in ("on_summon", "passive", ""):
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

    # arcane_sovereign's ritual_information_discount — a VOLUNTARY reveal,
    # so the King policy's once-per-match rebate applies (see
    # mechanics.kings.maybe_ritual_information_discount).
    from game.mechanics.kings import maybe_ritual_information_discount
    maybe_ritual_information_discount(
        ctx.state, owner, target.revelation, ctx.events, ctx.registry, rng=ctx.rng,
    )

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
# reveal_ritual  (ritual_insight spell, omen_bell trap)
# ---------------------------------------------------------------------------

def _reveal_ritual(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — two independent modes sharing one effect type:

    1. ``own_ritual: true`` (ritual_insight, target_type "none" — dispatched
       via _resolve_spell_untargeted, ``ctx.unit is None``): the CASTER
       (``ctx.extra["caster_owner"]``) reveals their own first not-REVEALED
       Ritual one step and, if ``benefit == "draw_card"``, draws
       ``draw_count`` cards — structurally identical to
       ``_ritual_reveal_tradeoff`` above.

    2. Otherwise (omen_bell, fired via _fire_trap on RITUAL_PROGRESS —
       ``ctx.unit`` is the enemy piece whose Ritual just advanced): one of
       ``ctx.unit.owner``'s SEALED Rituals is forced to FORETOLD
       (reveal_random_sealed — matches ``from: sealed, to: foretold``).
    """
    if ctx.state is None:
        return
    params = ctx.effect.params

    if params.get("own_ritual"):
        owner = (ctx.extra or {}).get("caster_owner")
        if owner is None:
            return
        from game.core.phases import RevelationState
        from game.mechanics.rituals import promote_one_step

        ps = ctx.state.get_player(owner)
        target = next((rs for rs in ps.ritual_pool if rs.revelation != RevelationState.REVEALED), None)
        if target is None:
            return
        if not promote_one_step(ctx.state, owner, target.ritual_id, ctx.events):
            return
        # arcane_sovereign's ritual_information_discount — voluntary reveal.
        from game.mechanics.kings import maybe_ritual_information_discount
        maybe_ritual_information_discount(
            ctx.state, owner, target.revelation, ctx.events, ctx.registry, rng=ctx.rng,
        )
        if params.get("benefit") == "draw_card":
            from game.core.events import CardDrawn, DeckRecycled

            count = params.get("draw_count", 1)
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
        return

    if ctx.unit is None:
        return
    from game.mechanics.rituals import reveal_random_sealed
    reveal_random_sealed(ctx.state, ctx.unit.owner, ctx.events, rng=ctx.rng)


# ---------------------------------------------------------------------------
# reveal_enemy_ritual  (forbidden_knowledge)
# ---------------------------------------------------------------------------

def _reveal_enemy_ritual(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — forbidden_knowledge (target_type "none" — ``ctx.unit is
    None``, caster from ``ctx.extra["caster_owner"]``). One random SEALED
    Ritual belonging to the caster's opponent becomes FORETOLD.
    """
    if ctx.state is None:
        return
    owner = (ctx.extra or {}).get("caster_owner")
    if owner is None:
        return
    from game.mechanics.rituals import reveal_random_sealed
    reveal_random_sealed(ctx.state, ctx.state.opponent_of(owner), ctx.events, rng=ctx.rng)


# ---------------------------------------------------------------------------
# ritual_bluff  (false_prophecy)
# ---------------------------------------------------------------------------

def _ritual_bluff(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — false_prophecy (target_type "none"). Arms
    ``bluff_turns`` on the caster's own first SEALED Ritual (pool order —
    "the choice rarely matters" simplification used throughout this
    module). See RitualState.bluff_turns and core/observation.py for the
    read side, and RulesEngine._execute_end_turn for the decay.
    """
    if ctx.state is None:
        return
    owner = (ctx.extra or {}).get("caster_owner")
    if owner is None:
        return
    from game.core.phases import RevelationState

    ps = ctx.state.get_player(owner)
    target = next((rs for rs in ps.ritual_pool if rs.revelation == RevelationState.SEALED), None)
    if target is None:
        return
    target.bluff_turns = ctx.effect.params.get("duration_turns", 2)


# ---------------------------------------------------------------------------
# interrupt_ritual  (profane_interruption) — consumed pre-emptively in
# RulesEngine._execute_activate_ritual, BEFORE validate_ritual runs; see
# mechanics.rituals.find_interrupting_trap(). This handler is never
# dispatched through resolve_effect (there's no "the Ritual already
# happened" moment to react to — the whole point is stopping it before it
# starts) but is registered so the type is recognised.
# ---------------------------------------------------------------------------

def _interrupt_ritual(ctx: "EffectContext") -> None:
    pass


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def _ritual_support(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED (Stage 13) — Shrine (real logic lives elsewhere).

    A Building effect, and Building effects are never dispatched through
    this registry: mechanics.rituals._ritual_range_bonus() scans
    state.buildings directly when a Formation Ritual is matched, because
    the bonus has to be re-evaluated against the pattern being attempted,
    not banked as a status at some earlier moment. No-op placeholder so
    the type is recognised and never logged as UNRESOLVED.
    """
    pass


RITUAL_HANDLERS: dict[str, object] = {
    "ritual_support":               _ritual_support,
    "ritual_progress_boost":        _ritual_progress_boost,
    "ritual_pattern_substitute":    _ritual_pattern_substitute,
    "ritual_requirement_reduction": _ritual_requirement_reduction,
    "ritual_reveal_tradeoff":       _ritual_reveal_tradeoff,
    "reveal_ritual":                _reveal_ritual,
    "reveal_enemy_ritual":          _reveal_enemy_ritual,
    "ritual_bluff":                 _ritual_bluff,
    "interrupt_ritual":             _interrupt_ritual,
}
