"""
test_ai_stage6_offers.py — the offer/acceptance invariant (README §53).

    Every action ``get_legal_actions`` offers, ``RulesEngine.execute``
    accepts.

Three of the four defects README §53's corpus turned up lived in the gap
that invariant covers, each with a perfectly correct handler sitting behind
a generator that disagreed with it:

    knightfall          the handler implements four reposition modes; the
    evacuation_order    generator implemented one, so both cards were
                        offered targets the rules refuse — 100 % of the
                        time, which made them dead cards.
    stale REPOSITION    the generator gated the whole turn behind a
                        decision whose subject had been captured, so every
                        offer was refused and the player was locked out.

None of the ten test files that call ``get_legal_actions`` executed what it
handed back, which is exactly why an unplayable card could sit in the pool
looking implemented.  This is that missing test.

It works by replaying: reach a real position, deep-copy it, and execute
every single offered action against its own copy of the state.  Sampled
rather than exhaustive — a full sweep is minutes of deep-copying — so it is
a net, not a proof.  Widen ``SEEDS`` when hunting.
"""

from __future__ import annotations

import pytest

from game.core.game import Game
from game.core.rng import DeterministicRNG
from game.core.rules import IllegalActionError, RulesEngine
from game.sim import acting_player, make_controller

#: Matches to walk.  Each contributes a few dozen sampled positions.
SEEDS = (1, 2, 3)

#: Sample one position in this many, per match.  The cost is one full
#: GameState deep copy per offered action, so this is the dial that decides
#: whether the test runs in seconds or minutes.
SAMPLE_EVERY = 25

#: Plies to walk per match before giving up.
MAX_STEPS = 400

#: Rejections that are a real game event rather than a disagreement.
#: ``profane_interruption`` is a Trap that aborts an opponent's Ritual
#: *inside* execute and consumes itself doing it — game.sim's ``_execute_one``
#: documents the same exception. The offer was legal when it was made; the
#: opponent's card is what refused it.
LEGITIMATE_REJECTIONS = (
    "profane_interruption",
    "Ritual attempt interrupted",
)


def _is_legitimate(message: str) -> bool:
    return any(fragment in message for fragment in LEGITIMATE_REJECTIONS)


def _walk(registry, seed: int, bot: str = "heuristic"):
    """
    Yield ``(state_snapshot, offered_actions)`` at sampled positions of a
    real match, then play on.
    """
    game = Game.new(seed=seed, registry=registry)
    controllers = {
        "white": make_controller(bot, seed * 2 + 1, "white", registry),
        "black": make_controller(bot, seed * 2 + 2, "black", registry),
    }
    for pid, controller in controllers.items():
        controller.player_id = pid

    for step in range(MAX_STEPS):
        game.advance_to_preparation()
        if game.is_over():
            return
        pid = acting_player(game.state)
        legal = game.get_legal_actions(pid)
        if not legal:
            return

        if step % SAMPLE_EVERY == 0:
            yield game.state.deep_copy(), list(legal)

        try:
            game.execute(controllers[pid].choose_action(
                game.get_observation(pid), legal
            ))
        except Exception:  # noqa: BLE001 — the walk is scaffolding, not the test
            return


def _replay(registry, snapshot, actions) -> list[str]:
    """Execute every action against its own copy; report what was refused."""
    refused: list[str] = []
    for action in actions:
        trial = snapshot.deep_copy()
        try:
            RulesEngine(registry=registry).execute(
                trial, action, DeterministicRNG(seed=99)
            )
        except IllegalActionError as exc:
            if not _is_legitimate(str(exc)):
                refused.append(f"{type(action).__name__}: {exc} — {action!r}")
        except Exception as exc:  # noqa: BLE001 — a crash is worse than a refusal
            refused.append(
                f"{type(action).__name__} CRASHED: {type(exc).__name__}: {exc} "
                f"— {action!r}"
            )
    return refused


class TestOfferedActionsAreAccepted:
    @pytest.mark.parametrize("seed", SEEDS)
    def test_heuristic_play(self, seed, registry):
        """Ordinary play — the positions a real match actually reaches."""
        refused: list[str] = []
        sampled = 0
        for snapshot, actions in _walk(registry, seed, "heuristic"):
            sampled += 1
            refused.extend(_replay(registry, snapshot, actions))

        assert sampled > 0, f"seed {seed} produced no positions to sample"
        assert not refused, (
            f"{len(refused)} offered action(s) the rules refuse:\n  "
            + "\n  ".join(refused[:10])
        )

    @pytest.mark.parametrize("seed", (11, 12))
    def test_random_play(self, seed, registry):
        """
        RandomBot reaches states a scoring bot never would — it will summon
        a Monster onto a Queen, seal its own pieces, and play cards into
        positions no evaluation would choose.  That is the point of it here.
        """
        refused: list[str] = []
        for snapshot, actions in _walk(registry, seed, "random"):
            refused.extend(_replay(registry, snapshot, actions))

        assert not refused, (
            f"{len(refused)} offered action(s) the rules refuse:\n  "
            + "\n  ".join(refused[:10])
        )


class TestTheNetCatchesAKnownHole:
    """
    A test that never fails is indistinguishable from a test that never
    runs.  This reintroduces the knightfall bug — the generator ignoring
    the card's ``piece_types`` — and checks the replay notices.
    """

    def test_an_offer_the_rules_refuse_is_reported(self, registry):
        from game.core.actions import ActivateSpell

        game = Game.new(seed=77, registry=registry)
        pid = game.state.active_player
        game.state.get_player(pid).hand.append("knightfall")
        game.advance_to_preparation()

        # A Pawn is what the broken generator used to offer knightfall.
        pawn_pos, _pawn = next(
            (p, u) for p, u in game.state.board.all_units_for(pid)
            if u.piece.piece_type.value == "pawn"
        )
        bogus = ActivateSpell(
            player_id=pid, card_id="knightfall",
            target={"position": (pawn_pos.file, pawn_pos.rank),
                    "destination": (pawn_pos.file, pawn_pos.rank + 1)},
        )

        refused = _replay(registry, game.state.deep_copy(), [bogus])
        assert len(refused) == 1
        assert "knight" in refused[0]

    def test_a_legitimate_rejection_is_not_reported(self):
        """A Trap eating a Ritual is a game event, not a broken offer."""
        assert _is_legitimate("Ritual attempt interrupted by profane_interruption")
        assert not _is_legitimate("This Spell can only target: knight.")
