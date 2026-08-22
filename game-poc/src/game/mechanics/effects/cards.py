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
discard_card        IMPLEMENTED  — blood_price: the CAPTURING player
                                   (ctx.unit.owner — see check_capture_traps'
                                   "target of the Trap's effects is the
                                   CAPTURING piece") discards 1 random card
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
    # "enemy_summon_nearby" is spirit_hunter's reaction, dispatched by
    # mechanics.monsters.check_summon_reactions() once it has already
    # confirmed the range and the enemy-ness of the summon.
    if trigger not in ("on_summon", "on_move", "passive", "", "enemy_summon_nearby"):
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
    # celestial_oracle's copy is ``once_per_turn`` — an ACTIVATED ability
    # (inspect_top_deck is in ACTIVATED_EFFECT_TYPES) rather than an
    # on-summon one, so it must accept an "activated" dispatch and refuse
    # to fire on summon.
    if trigger in ("once_per_turn", "activated"):
        if ctx.trigger != "activated":
            return
    elif trigger not in ("on_summon", "passive", ""):
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
    # A single card has no ordering to choose — celestial_oracle just
    # "looks at the top card", so it never raises a decision the player
    # would have to click through for nothing.
    if count > 1 and ctx.state.pending_decision is None:
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
# discard_card  (blood_price)
# ---------------------------------------------------------------------------

def _discard_card(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — blood_price (capture-trigger Trap). ``ctx.unit`` is the
    CAPTURING piece (see mechanics.monsters.check_capture_traps), so
    ``ctx.unit.owner`` is the player who must discard — the attacker
    succeeds at the capture but pays a card cost.
    """
    from game.core.events import CardDiscarded

    if ctx.unit is None or ctx.state is None:
        return
    owner = ctx.unit.owner
    ps = ctx.state.get_player(owner)
    count = ctx.effect.params.get("count", 1)
    for _ in range(count):
        if not ps.hand:
            break
        card_id = ctx.rng.choice(ps.hand) if ctx.rng is not None else ps.hand[0]
        ps.hand.remove(card_id)
        ps.graveyard.append(card_id)
        ctx.events.append(CardDiscarded(player_id=owner, card_id=card_id))


# ---------------------------------------------------------------------------
# banish_deck_card  (erase_memory)
# ---------------------------------------------------------------------------

def _banish_deck_card(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — erase_memory ("Banish the top card of your opponent's
    deck face-down. They cannot look at it. Then you draw 1 card.").

    Banished cards go to ``PlayerState.banished`` rather than the
    graveyard, which matters mechanically: the deck-empty reshuffle in
    ``_draw_card`` above only ever reaches for ``graveyard``, so a banished
    card genuinely never comes back. "Face-down / cannot look at it" is
    modelled by simply not emitting a reveal event — nothing tells the
    victim which card it was, and Observation never exposes another
    player's banished pile.
    """
    from game.core.events import CardBanished, CardDrawn, DeckRecycled

    if ctx.state is None:
        return
    caster = (ctx.extra or {}).get("caster_owner")
    if caster is None:
        return
    params = ctx.effect.params
    source = params.get("from", "opponent")
    victim = ctx.state.opponent_of(caster) if source == "opponent" else caster
    victim_ps = ctx.state.get_player(victim)

    for _ in range(params.get("count", 1)):
        if not victim_ps.deck:
            break
        card_id = victim_ps.deck.pop(0)
        victim_ps.banished.append(card_id)
        ctx.events.append(CardBanished(player_id=victim, card_id=card_id))

    caster_ps = ctx.state.get_player(caster)
    for _ in range(params.get("draw_cards", 0)):
        if not caster_ps.deck and caster_ps.graveyard:
            caster_ps.deck = list(caster_ps.graveyard)
            caster_ps.graveyard.clear()
            if ctx.rng is not None:
                ctx.rng.shuffle(caster_ps.deck)
            ctx.events.append(DeckRecycled(player_id=caster, card_count=len(caster_ps.deck)))
        if caster_ps.deck:
            drawn = caster_ps.deck.pop(0)
            caster_ps.hand.append(drawn)
            ctx.events.append(CardDrawn(player_id=caster, card_id=drawn))


# ---------------------------------------------------------------------------
# dig_top_deck  (analyze_strategy)
# ---------------------------------------------------------------------------

def _dig_top_deck(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — analyze_strategy ("Look at the top 3 cards of your deck.
    Put 1 into your hand and put the rest on the bottom in any order.").

    The whole set of ``look`` cards is revealed to the caster via the same
    private DeckInspected event inspect_top_deck uses, then ``take`` of
    them go to hand and the remainder to the bottom of the deck.

    Which ones to keep is resolved automatically in deck order rather than
    through a PendingDecision — the same "the choice rarely matters, and
    there is no interactive mid-Spell choice flow" simplification
    graveyard_to_deck and ritual_reveal_tradeoff already use. The
    information gain (the caster sees all three) is the part that carries
    the card, and that is fully modelled.
    """
    from game.core.events import CardDrawn, DeckInspected

    if ctx.state is None:
        return
    caster = (ctx.extra or {}).get("caster_owner")
    if caster is None:
        return
    ps = ctx.state.get_player(caster)
    params = ctx.effect.params
    look = min(params.get("look", 3), len(ps.deck))
    if look <= 0:
        return

    seen = [ps.deck.pop(0) for _ in range(look)]
    ctx.events.append(DeckInspected(player_id=caster, card_ids=tuple(seen)))

    take = min(params.get("take", 1), len(seen))
    for _ in range(take):
        card_id = seen.pop(0)
        ps.hand.append(card_id)
        ctx.events.append(CardDrawn(player_id=caster, card_id=card_id))
    ps.deck.extend(seen)      # the rest go to the BOTTOM


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

CARDS_HANDLERS: dict[str, object] = {
    "banish_deck_card":  _banish_deck_card,
    "dig_top_deck":      _dig_top_deck,
    "draw_card":         _draw_card,
    "inspect_top_deck":  _inspect_top_deck,
    "reorder_top_deck":  _reorder_top_deck,
    "graveyard_to_deck": _graveyard_to_deck,
    "discard_card":      _discard_card,
}
