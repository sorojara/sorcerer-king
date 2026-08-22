"""
Determinization — AI Stage 4 (README §49)
===========================================

    "For hidden information, sample plausible game states consistent with
     the AI's Observation ... Simulation 1: opponent has Ritual X.
     Simulation 2: opponent has Ritual Y. Simulation 3: opponent hand
     contains Trap support."

A *determinization* is one complete guess at everything the observer
cannot see, chosen so that nothing in it contradicts the Observation.  The
Monte-Carlo bot (``game.ai.monte_carlo``) draws a fresh one per simulation,
plays the position out in that imagined world, and averages the results —
so a decision is judged against the spread of worlds the opponent could
actually be in, rather than against the single world "the opponent holds
nothing", which is what a pure Stage 2 search quietly assumes.

What is sampled
---------------
    ``opponent_hand``     — Main-Deck card IDs, exactly ``opponent_hand_count``
                            of them.
    ``opponent_rituals``  — one Ritual ID per pool slot (README §14–15).
    ``opponent_kings``    — one King ID per pool slot (README §16).

Deck *order* is deliberately not sampled: nothing downstream draws a card
inside a simulation, so a sampled deck would be cost without consequence.

What makes a world consistent
-----------------------------
Every card that is publicly *known* to be the opponent's is removed from
the pool it could still be drawn from:

    • their graveyard (public — README §35),
    • the Monsters standing on their units (Monster identity is public),
    • the Traps they have placed (Traps are public by design — README §7.1).

Cards in the *observer's* own hand, graveyard, or board are NOT removed.
Each player's Main Deck is an independent 20-card sample from the shared
card pool (``Game._build_deck_from_registry``), so both players can hold
the same card at once — but within one deck the IDs are distinct, which is
why sampling is done without replacement.

Rituals respect their revelation state (README §15): a REVEALED slot is
known outright, a FORETOLD slot is narrowed to the roster entries matching
the leaked archetype and required Vessel, and a SEALED slot is open.
Kings work the same way: an ACTIVE or RETIRED slot is known, a HIDDEN one
is sampled from the roster minus the slots already accounted for.

false_prophecy's ``ritual_bluff`` shows a SEALED Ritual as FORETOLD with a
*fabricated* archetype (see ``core/observation.py``).  The sampler
believes it, which is the whole point of the card — the deception costs
the Monte-Carlo bot the same accuracy it costs a human.

Uniform, not informed
---------------------
Sampling is uniform over the consistent worlds.  README §48's belief model
is what replaces that with a weighted draw ("Blue-Eyes Ritual: 55 %,
Dragon Emperor: 25 %, Other: 20 %"); until it exists, uniform-over-
consistent is the honest prior, and it is still a strictly better model of
the opponent than "their hand is empty".

Information rules (README §44)
------------------------------
Input is an Observation and the public card registry — the same rulebook a
human reads off the cards.  No GameState, no deck order, no hidden ID is
ever consulted; a determinization is a *guess*, and it is wrong most of
the time by construction.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

from game.core.phases import KingCardStatus, RevelationState

if TYPE_CHECKING:  # pragma: no cover - typing only
    from game.cards.card import CardRegistry
    from game.core.observation import Observation


@dataclass(frozen=True)
class Determinization:
    """
    One sampled world: what the opponent might be holding right now.

    Every field is a tuple of card IDs.  ``opponent_rituals`` and
    ``opponent_kings`` are positional — entry *i* is the sampler's guess
    for pool slot *i* — so a slot whose identity is already public keeps
    its real ID.
    """

    opponent_hand: tuple[str, ...] = ()
    opponent_rituals: tuple[str, ...] = ()
    opponent_kings: tuple[str, ...] = ()

    @property
    def is_null(self) -> bool:
        """True for the empty world — "the opponent has nothing hidden"."""
        return not (
            self.opponent_hand or self.opponent_rituals or self.opponent_kings
        )


# The world a Stage 2 search implicitly assumes.  The Monte-Carlo bot draws
# it as its first sample so the ranking is always anchored to the position
# as it actually stands, before any guessing starts.
NULL_WORLD = Determinization()


class Determinizer:
    """
    Draws worlds consistent with one Observation.

    Built once per decision — working out which cards are still possible
    costs a pass over the registry, and that answer does not change while
    the observer is thinking — then ``sample()`` is called once per
    simulation.

    ``rng`` is the caller's own seeded RNG (the bot's, never the game's),
    so a run repeats exactly for a given seed.

    With no registry there is nothing to sample from and every draw is
    ``NULL_WORLD``; callers degrade to Stage 2 behaviour rather than break.
    """

    def __init__(
        self,
        obs: "Observation",
        registry: "CardRegistry | None" = None,
        rng: "random.Random | None" = None,
    ) -> None:
        self._obs = obs
        self._registry = registry
        self._rng = rng or random.Random(0)
        self._opponent = "black" if obs.player_id == "white" else "white"

        self._hand_pool: list[str] = self._main_deck_pool()
        self._ritual_slots: list[tuple[str | None, list[str]]] = self._ritual_candidates()
        self._king_slots: list[tuple[str | None, list[str]]] = self._king_candidates()

    # ── Public API ────────────────────────────────────────────────────────

    def sample(self) -> Determinization:
        """One world, drawn uniformly from the consistent ones."""
        if self._registry is None:
            return NULL_WORLD
        return Determinization(
            opponent_hand=self._draw_hand(),
            opponent_rituals=self._draw_slots(self._ritual_slots),
            opponent_kings=self._draw_slots(self._king_slots),
        )

    @property
    def hand_pool(self) -> tuple[str, ...]:
        """Every Main-Deck card that could still be in the opponent's hand."""
        return tuple(self._hand_pool)

    @property
    def is_informative(self) -> bool:
        """
        False when sampling cannot tell the caller anything.

        True means there is at least one hidden card to guess at; when it
        is False every ``sample()`` returns the null world and the caller
        may as well run the Stage 2 search once.
        """
        if self._registry is None:
            return False
        if self._obs.opponent_hand_count > 0 and self._hand_pool:
            return True
        return any(known is None for known, _ in self._ritual_slots + self._king_slots)

    # ── Pool construction ─────────────────────────────────────────────────

    def _main_deck_pool(self) -> list[str]:
        """
        Main-Deck cards the opponent could still be holding.

        ``ritual_only`` Monsters never enter a Main Deck (README §14 — they
        arrive only through ActivateRitual), so they are not candidates for
        a hand.
        """
        registry = self._registry
        if registry is None:
            return []

        pool = [c.id for c in registry.all_monsters() if not c.ritual_only]
        pool += [c.id for c in registry.all_spells()]
        pool += [c.id for c in registry.all_traps()]

        accounted: set[str] = set(self._obs.opponent_graveyard)
        for unit in self._obs.board.units:
            if unit.owner == self._opponent and unit.monster_id is not None:
                accounted.add(unit.monster_id)
        for trap in self._obs.board.trap_locations:
            if getattr(trap, "owner", None) == self._opponent:
                card_id = getattr(trap, "card_id", None)
                if card_id is not None:
                    accounted.add(card_id)

        # Sorted so the pool order is a function of the registry alone —
        # the RNG, not dict iteration order, decides what a run samples.
        return sorted(set(pool) - accounted)

    def _ritual_candidates(self) -> list[tuple[str | None, list[str]]]:
        """
        Per Ritual slot: ``(known_id, candidates)``.

        ``known_id`` is set for a REVEALED slot and None otherwise;
        ``candidates`` is what a hidden slot may still turn out to be.
        """
        registry = self._registry
        if registry is None:
            return []
        roster = registry.all_rituals()
        if not roster:
            return []

        slots: list[tuple[str | None, list[str]]] = []
        for info in self._obs.opponent_ritual_info:
            if info.revelation == RevelationState.REVEALED and info.ritual_id:
                slots.append((info.ritual_id, []))
                continue
            if info.revelation == RevelationState.FORETOLD:
                # README §15 leaks archetype + required Vessel, nothing else.
                candidates = [
                    r.id for r in roster
                    if (info.archetype is None or r.archetype == info.archetype)
                    and (
                        info.required_vessel is None
                        or r.required_vessel == info.required_vessel
                    )
                ]
                slots.append((None, candidates or [r.id for r in roster]))
                continue
            slots.append((None, [r.id for r in roster]))
        return slots

    def _king_candidates(self) -> list[tuple[str | None, list[str]]]:
        """Per King slot: ``(known_id, candidates)`` — README §16's pool of 3."""
        registry = self._registry
        if registry is None:
            return []
        roster = [k.id for k in registry.all_kings()]
        if not roster:
            return []

        slots: list[tuple[str | None, list[str]]] = []
        for info in self._obs.opponent_king_info:
            if info.status != KingCardStatus.HIDDEN and info.king_card_id:
                slots.append((info.king_card_id, []))
            else:
                slots.append((None, list(roster)))
        return slots

    # ── Drawing ───────────────────────────────────────────────────────────

    def _draw_hand(self) -> tuple[str, ...]:
        """
        ``opponent_hand_count`` distinct cards, drawn without replacement.

        The count is clamped to the pool: a long game can account for more
        cards publicly than the registry has left over, and a short sample
        is a better world than a crash.
        """
        wanted = min(self._obs.opponent_hand_count, len(self._hand_pool))
        if wanted <= 0:
            return ()
        return tuple(self._rng.sample(self._hand_pool, wanted))

    def _draw_slots(
        self, slots: "list[tuple[str | None, list[str]]]"
    ) -> tuple[str, ...]:
        """
        Fill one pool (Rituals or Kings), slot by slot, without replacement.

        Known slots are taken as they are and claim their ID first, so a
        hidden slot is never guessed to hold a card that is already face-up
        somewhere in the same pool.  The most constrained hidden slot is
        drawn first — a FORETOLD Ritual has far fewer candidates than a
        SEALED one, and filling it last is how a sampler paints itself into
        a corner.
        """
        if not slots:
            return ()

        out: list[str | None] = [known for known, _ in slots]
        claimed = {known for known in out if known is not None}

        hidden = sorted(
            (i for i, (known, _) in enumerate(slots) if known is None),
            key=lambda i: len(slots[i][1]),
        )
        for index in hidden:
            options = [c for c in slots[index][1] if c not in claimed]
            if not options:
                # Every candidate is spoken for — the pool is smaller than
                # the slot count.  Reuse rather than drop the slot.
                options = slots[index][1] or list(claimed)
            if not options:
                continue
            pick = self._rng.choice(options)
            out[index] = pick
            claimed.add(pick)

        return tuple(c for c in out if c is not None)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"Determinizer(opponent={self._opponent!r}, "
            f"hand={self._obs.opponent_hand_count}, "
            f"pool={len(self._hand_pool)})"
        )
