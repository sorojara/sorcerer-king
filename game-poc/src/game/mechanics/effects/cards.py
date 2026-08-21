"""
mechanics/effects/cards.py — CARDS category
============================================

Effect types in this category
------------------------------
draw_card           IMPLEMENTED  — draw 1+ cards from deck (apprentice_mage)
inspect_top_deck    IMPLEMENTED  — peek top N cards + open a REORDER_DECK
                                   PendingDecision to rearrange them (arcane_archivist)
reorder_top_deck    INERT PLACEHOLDER — the actual reorder is resolved by the
                                   REORDER_DECK PendingDecision inspect_top_deck
                                   opens (RulesEngine._execute_reorder_top_deck),
                                   not by a second effect-registry dispatch — this
                                   card's ``trigger: after_inspect`` is intentionally
                                   never fired by apply_on_summon_effects (its
                                   trigger whitelist doesn't include after_inspect),
                                   so this handler exists only so the type is
                                   recognised and never logged as unresolved
graveyard_to_deck   IMPLEMENTED  — return the owner's own first Graveyard card
                                   to the bottom of the deck (grave_scholar)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from game.mechanics.effects.registry import EffectContext


# ---------------------------------------------------------------------------
# draw_card
# ---------------------------------------------------------------------------

def _draw_card(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — draw ``count`` cards from deck.

    Handles deck-recycle (shuffle graveyard back) if deck is empty.

    Stage 6: Traps (alarm_beacon) can target ``owner`` (the Trap owner)
    rather than ``triggering_piece`` — mechanics.monsters._fire_trap()
    supplies the Trap owner via ``ctx.extra["owner_override"]`` since
    ``ctx.unit`` there is always the triggering (enemy) piece, not the
    Trap owner.
    """
    from game.core.events import CardDrawn, DeckRecycled

    params  = ctx.effect.params
    trigger = params.get("trigger", "on_summon")
    if trigger not in ("on_summon", "on_move", "passive", ""):
        return

    owner = (ctx.extra or {}).get("owner_override")
    if owner is None and ctx.unit is not None:
        owner = ctx.unit.owner
    if owner is None or ctx.state is None:
        return

    ps = ctx.state.get_player(owner)
    count = params.get("count", 1)

    for _ in range(count):
        if not ps.deck and ps.graveyard:
            ps.deck = list(ps.graveyard)
            ps.graveyard.clear()
            if ctx.rng is not None:
                ctx.rng.shuffle(ps.deck)
            ctx.events.append(DeckRecycled(
                player_id=owner,
                card_count=len(ps.deck),
            ))
        if ps.deck:
            card_id = ps.deck.pop(0)
            ps.hand.append(card_id)
            ctx.events.append(CardDrawn(player_id=owner, card_id=card_id))


# ---------------------------------------------------------------------------
# inspect_top_deck
# ---------------------------------------------------------------------------

def _inspect_top_deck(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — arcane_archivist on_summon.

    Reveals the top ``count`` cards of the owner's own deck via a private
    DeckInspected event (owner-only — same non-persistent-reveal
    convention as EnemyCardRevealed; Observation's hidden-info filtering
    is untouched), then opens a REORDER_DECK PendingDecision so the owner
    can submit the new top-to-bottom order for those same cards
    (RulesEngine._execute_reorder_top_deck resolves it via ReorderTopDeck).
    """
    from game.core.events import DeckInspected
    from game.core.phases import DecisionType
    from game.core.state import PendingDecision

    params = ctx.effect.params
    trigger = params.get("trigger", "on_summon")
    if trigger not in ("on_summon", "passive", ""):
        return
    if ctx.unit is None or ctx.state is None:
        return

    owner = ctx.unit.owner
    ps = ctx.state.get_player(owner)
    count = min(params.get("count", 3), len(ps.deck))
    if count <= 0:
        return

    peeked = list(ps.deck[:count])
    ctx.events.append(DeckInspected(player_id=owner, card_ids=tuple(peeked)))
    if ctx.state.pending_decision is None:
        ctx.state.pending_decision = PendingDecision(
            player_id=owner,
            decision_type=DecisionType.REORDER_DECK,
            options=peeked,
            min_choices=len(peeked),
            max_choices=len(peeked),
            context={},
        )


# ---------------------------------------------------------------------------
# reorder_top_deck
# ---------------------------------------------------------------------------

def _reorder_top_deck(ctx: "EffectContext") -> None:
    """
    INERT PLACEHOLDER — see module docstring. The actual reorder happens
    in RulesEngine._execute_reorder_top_deck, resolving the REORDER_DECK
    PendingDecision that _inspect_top_deck opens. Registered here only so
    the effect type is recognised and never logged as unresolved.
    """
    pass


# ---------------------------------------------------------------------------
# graveyard_to_deck
# ---------------------------------------------------------------------------

def _graveyard_to_deck(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — grave_scholar on_summon.

    Returns the owner's own FIRST Graveyard card (list order — the choice
    rarely matters this early in a match, matching the same "automatic,
    pool-order" simplification omen_reader's ritual_reveal_tradeoff already
    uses instead of a bespoke single-card-choice PendingDecision) to the
    bottom of the deck. Emits the existing CardsReturnedToDeck event.
    """
    from game.core.events import CardsReturnedToDeck

    params = ctx.effect.params
    trigger = params.get("trigger", "on_summon")
    if trigger not in ("on_summon", "passive", ""):
        return
    if ctx.unit is None or ctx.state is None:
        return

    owner = ctx.unit.owner
    ps = ctx.state.get_player(owner)
    if not ps.graveyard:
        return

    card_id = ps.graveyard.pop(0)
    ps.deck.append(card_id)
    ctx.events.append(CardsReturnedToDeck(player_id=owner, card_ids=(card_id,)))


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

CARDS_HANDLERS: dict[str, object] = {
    "draw_card":         _draw_card,
    "inspect_top_deck":  _inspect_top_deck,
    "reorder_top_deck":  _reorder_top_deck,
    "graveyard_to_deck": _graveyard_to_deck,
}
