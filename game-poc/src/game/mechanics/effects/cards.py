"""
mechanics/effects/cards.py — CARDS category
============================================

Effect types in this category
------------------------------
draw_card           IMPLEMENTED  — draw 1+ cards from deck (apprentice_mage)
inspect_top_deck    STUB         — look at top N cards without drawing (Stage 6+)
reorder_top_deck    STUB         — rearrange top N cards (Stage 6+)
graveyard_to_deck   STUB         — return a graveyard card to deck (Stage 6+)
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
    STUB — Stage 6+

    Reveal the top ``count`` cards of the owner's deck to the owner only.
    This is an information effect with no board mutation.

    When implemented this will emit an InspectTopDeck event carrying the
    card IDs (private to the owner's Observation).
    """
    raise NotImplementedError(
        "inspect_top_deck is not yet implemented (Stage 6+). "
        "Effect params: " + repr(ctx.effect.params)
    )


# ---------------------------------------------------------------------------
# reorder_top_deck
# ---------------------------------------------------------------------------

def _reorder_top_deck(ctx: "EffectContext") -> None:
    """
    STUB — Stage 6+

    Rearrange the top ``count`` cards of the deck in any order chosen by
    the player.  This is always preceded by inspect_top_deck.

    When implemented this will:
      1. Raise a REORDER_DECK PendingDecision listing the inspected card IDs.
      2. The player submits a ReorderTopDeck action with the desired order.
      3. The engine replaces the top N cards of the deck with the new order.
    """
    raise NotImplementedError(
        "reorder_top_deck is not yet implemented (Stage 6+). "
        "Effect params: " + repr(ctx.effect.params)
    )


# ---------------------------------------------------------------------------
# graveyard_to_deck
# ---------------------------------------------------------------------------

def _graveyard_to_deck(ctx: "EffectContext") -> None:
    """
    STUB — Stage 6+

    Return one card from the owner's Graveyard to the bottom (or top) of
    the deck.  The specific card is either random or player-chosen depending
    on params.

    When implemented this will:
      1. At on_summon: raise a CHOOSE_GRAVEYARD_CARD PendingDecision.
      2. The player selects a card from the graveyard.
      3. The engine moves that card from graveyard → deck.
      4. Emit a CardReturnedToDeck event.
    """
    raise NotImplementedError(
        "graveyard_to_deck is not yet implemented (Stage 6+). "
        "Effect params: " + repr(ctx.effect.params)
    )


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

CARDS_HANDLERS: dict[str, object] = {
    "draw_card":         _draw_card,
    "inspect_top_deck":  _inspect_top_deck,
    "reorder_top_deck":  _reorder_top_deck,
    "graveyard_to_deck": _graveyard_to_deck,
}
