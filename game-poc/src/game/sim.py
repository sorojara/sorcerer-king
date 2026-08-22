"""
Headless Simulation Harness — README Stage 2 / AI Stage 0
===========================================================

Runs complete matches with no UI so the engine can be fuzzed and the
README §42 telemetry can be gathered in bulk.

This is the "Simulation Harness" half of README Stage 2 ("RandomBot +
Simulation Harness") and the delivery vehicle for AI Stage 0's stated
purpose: *test engine stability, validate legal-action generation, fuzz
unusual states, run automated simulations*.

Controllers are ordinary ``PlayerController`` implementations, so the same
harness runs RandomBot mirrors, HeuristicBot mirrors, SearchBot mirrors, or
any head-to-head between them — which is how each AI stage is measured
against the one before it.

Usage (library):

    from game.sim import play_match, run_matches
    from game.ai.random_bot import RandomBot

    stats = play_match(seed=1, controllers={"white": RandomBot(1),
                                            "black": RandomBot(2)})
    print(stats.turn_count, stats.winner)

Usage (CLI):

    python -m game.sim --matches 50 --white heuristic --black random
    python -m game.sim --matches 20 --white search --black heuristic --search-depth 3
    python -m game.sim --matches 200 --csv telemetry.csv --json summary.json

Information rules (README §44) hold here exactly as in the UI: each
controller is handed ``game.get_observation(pid)`` and the legal action
list — never the GameState.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from game.logger import get_logger
from game.core.phases import Phase
from game.telemetry import MatchStats, TelemetryAggregator

if TYPE_CHECKING:  # pragma: no cover - typing only
    from game.ai.controller import PlayerController
    from game.ai.search import SearchLimits
    from game.cards.card import CardRegistry
    from game.core.game import Game
    from game.core.state import GameState

_log = get_logger(__name__)


# Hard safety valve: a match that somehow refuses to terminate is abandoned
# rather than hanging the harness.  Counted as "unfinished" by the aggregator.
DEFAULT_MAX_STEPS = 4000


# ─────────────────────────────────────────────────────────────────────────────
# Controller factory
# ─────────────────────────────────────────────────────────────────────────────

CONTROLLER_KINDS: list[str] = ["random", "heuristic", "search"]


def make_controller(
    kind: str,
    seed: int,
    player_id: str,
    registry: "CardRegistry | None" = None,
    search_limits: "SearchLimits | None" = None,
) -> "PlayerController":
    """
    Build a controller by short name.

    ``"random"``    — AI Stage 0 RandomBot     (README §45)
    ``"heuristic"`` — AI Stage 1 HeuristicBot  (README §46)
    ``"search"``    — AI Stage 2 SearchBot     (README §47)

    ``registry`` (the public card definitions, not game state — see §44)
    sharpens HeuristicBot's Monster and hand-quality scoring and SearchBot's
    Monster-aware move generation; RandomBot ignores it.

    ``search_limits`` sets SearchBot's per-decision budget; ignored by the
    other two.
    """
    kind = kind.lower()
    if kind == "random":
        from game.ai.random_bot import RandomBot

        bot: "PlayerController" = RandomBot(seed=seed)
    elif kind == "heuristic":
        from game.ai.heuristic_bot import HeuristicBot

        bot = HeuristicBot(seed=seed, registry=registry)
    elif kind == "search":
        from game.ai.search import SearchLimits as _SearchLimits
        from game.ai.search_bot import SearchBot

        bot = SearchBot(
            seed=seed,
            registry=registry,
            limits=search_limits or _SearchLimits(),
        )
    else:
        raise ValueError(
            f"Unknown controller kind {kind!r} "
            f"(expected one of {', '.join(CONTROLLER_KINDS)})"
        )
    bot.player_id = player_id
    return bot


# ─────────────────────────────────────────────────────────────────────────────
# Turn driving
# ─────────────────────────────────────────────────────────────────────────────

def acting_player(state: "GameState") -> str:
    """
    Whose input the engine is waiting for.

    Normally the active player; during a Final Duel the sides alternate
    within a round (defender first — mechanics/duel.py), which is not
    reflected in ``state.active_player``.
    """
    duel = state.duel
    if state.phase == Phase.FINAL_DUEL and duel is not None:
        return duel.defender if not duel.defender_acted_this_round else duel.attacker
    return state.active_player


# ─────────────────────────────────────────────────────────────────────────────
# One match
# ─────────────────────────────────────────────────────────────────────────────

def _execute_one(
    game: "Game",
    controller: "PlayerController",
    pid: str,
    legal: list,
    seed: int,
):
    """
    Ask ``controller`` for an action and execute it, retrying with the
    rejected action removed if the engine refuses it.

    A handful of offered actions can still be refused at execution time —
    ActivateRitual is the live example: an enemy Trap carrying
    ``profane_interruption`` aborts the attempt *and consumes itself* inside
    ``RulesEngine.execute`` (rules.py ``_execute_activate_ritual``), which is
    a real game event dressed as an IllegalActionError. A fuzz harness must
    survive that rather than treating it as a crash; ``game.telemetry``
    counts every rejection in ``illegal_action_count`` so it stays visible.

    Returns the action that actually executed, or None if every offer was
    refused (which is a genuine engine bug and is logged as one).
    """
    from game.core.rules import IllegalActionError

    candidates = list(legal)
    while candidates:
        action = controller.choose_action(game.get_observation(pid), candidates)
        try:
            game.execute(action)
        except IllegalActionError as exc:
            _log.warning(
                "SIM  seed=%d rejected %s for %s: %s",
                seed, type(action).__name__, pid, exc,
            )
            candidates = [a for a in candidates if a is not action]
            continue
        return action

    _log.error(
        "SIM  seed=%d every legal action was refused for %s — aborting match",
        seed, pid,
    )
    return None



def play_match(
    seed: int,
    controllers: "dict[str, PlayerController]",
    registry: "CardRegistry | None" = None,
    max_steps: int = DEFAULT_MAX_STEPS,
    on_step: "Callable[[str, object], None] | None" = None,
) -> MatchStats:
    """
    Play one complete match and return its ``MatchStats``.

    ``controllers`` maps player_id → controller.  Each controller's
    ``player_id`` is set here so callers can't mis-wire it.

    Returns telemetry even when the match is abandoned at ``max_steps``
    (``MatchStats.completed`` is False in that case).
    """
    from game.core.game import Game

    game = Game.new(seed=seed, registry=registry)

    for pid, controller in controllers.items():
        controller.player_id = pid

    steps = 0
    while not game.is_over() and steps < max_steps:
        steps += 1
        game.advance_to_preparation()
        if game.is_over():
            break

        pid = acting_player(game.state)
        legal = game.get_legal_actions(pid)
        if not legal:
            _log.error(
                "SIM  no legal actions  seed=%d phase=%s player=%s — aborting match",
                seed, game.state.phase.value, pid,
            )
            break

        action = _execute_one(game, controllers[pid], pid, legal, seed)
        if action is None:
            break
        if on_step is not None:
            on_step(pid, action)

    if steps >= max_steps:
        _log.warning("SIM  seed=%d hit max_steps=%d without finishing", seed, max_steps)

    return game.match_stats()


# ─────────────────────────────────────────────────────────────────────────────
# Many matches
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SimulationResult:
    aggregator: TelemetryAggregator
    errors: list[tuple[int, str]]   # (seed, error text) for crashed matches

    @property
    def matches(self) -> list[MatchStats]:
        return self.aggregator.matches


def run_matches(
    count: int,
    white: str = "random",
    black: str = "random",
    base_seed: int = 0,
    registry: "CardRegistry | None" = None,
    max_steps: int = DEFAULT_MAX_STEPS,
    stop_on_error: bool = False,
    progress: bool = False,
    search_limits: "SearchLimits | None" = None,
) -> SimulationResult:
    """
    Run ``count`` matches and aggregate their telemetry.

    Each match gets its own game seed AND its own bot seeds, both derived
    from ``base_seed``, so a whole run is reproducible from one number.

    A match that raises is recorded in ``errors`` and the run continues
    (that is the point of a fuzz harness) unless ``stop_on_error``.
    """
    agg = TelemetryAggregator()
    errors: list[tuple[int, str]] = []

    for i in range(count):
        seed = base_seed + i
        controllers = {
            "white": make_controller(white, seed * 2 + 1, "white", registry, search_limits),
            "black": make_controller(black, seed * 2 + 2, "black", registry, search_limits),
        }
        try:
            stats = play_match(
                seed=seed,
                controllers=controllers,
                registry=registry,
                max_steps=max_steps,
            )
        except Exception as exc:  # noqa: BLE001 — the harness reports, not crashes
            _log.exception("SIM  match seed=%d crashed", seed)
            errors.append((seed, f"{type(exc).__name__}: {exc}"))
            if stop_on_error:
                raise
            continue

        agg.add(stats)
        if progress:
            print(
                f"  match {i + 1}/{count}  seed={seed}  "
                f"turns={stats.turn_count}  winner={stats.winner or '—'}",
                flush=True,
            )

    return SimulationResult(aggregator=agg, errors=errors)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def load_default_registry() -> "CardRegistry | None":
    """Load the YAML card pool the same way the pygame app does."""
    from pathlib import Path

    from game.cards.card import load_registry_from_yaml

    data_dir = Path(__file__).parent / "data"
    try:
        return load_registry_from_yaml(data_dir)
    except Exception as exc:  # noqa: BLE001
        print(f"warning: could not load card registry ({exc}); "
              f"running with the fallback deck", file=sys.stderr)
        return None


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m game.sim",
        description="Headless match simulation + README §42 telemetry",
    )
    parser.add_argument("--matches", type=int, default=10, help="how many matches")
    parser.add_argument("--white", default="random", choices=CONTROLLER_KINDS)
    parser.add_argument("--black", default="random", choices=CONTROLLER_KINDS)
    parser.add_argument("--seed", type=int, default=None,
                        help="base seed (random if omitted)")
    parser.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS)
    # AI Stage 2 (README §47) search budget — ignored by the other bots.
    parser.add_argument("--search-depth", type=int, default=None,
                        help="SearchBot: plies to search (default 3)")
    parser.add_argument("--search-nodes", type=int, default=None,
                        help="SearchBot: node ceiling per decision")
    parser.add_argument("--search-seconds", type=float, default=None,
                        help="SearchBot: wall-clock ceiling per decision")
    parser.add_argument("--no-registry", action="store_true",
                        help="run without the YAML card registry")
    parser.add_argument("--csv", default=None, help="write per-match rows here")
    parser.add_argument("--json", default=None, help="write the run summary here")
    parser.add_argument("--quiet", action="store_true", help="no per-match progress")
    args = parser.parse_args(argv)

    base_seed = args.seed if args.seed is not None else random.randint(0, 2**31 - 1)
    registry = None if args.no_registry else load_default_registry()

    search_limits = None
    if "search" in (args.white, args.black):
        from game.ai.search import SearchLimits as _SearchLimits

        default = _SearchLimits()
        search_limits = _SearchLimits(
            max_depth=args.search_depth if args.search_depth is not None
            else default.max_depth,
            max_nodes=args.search_nodes if args.search_nodes is not None
            else default.max_nodes,
            max_seconds=args.search_seconds if args.search_seconds is not None
            else default.max_seconds,
        )

    print(
        f"Simulating {args.matches} match(es): "
        f"white={args.white} vs black={args.black}  base_seed={base_seed}"
    )
    result = run_matches(
        count=args.matches,
        white=args.white,
        black=args.black,
        base_seed=base_seed,
        registry=registry,
        max_steps=args.max_steps,
        progress=not args.quiet,
        search_limits=search_limits,
    )

    summary = result.aggregator.summary()
    summary["white_controller"] = args.white
    summary["black_controller"] = args.black
    if search_limits is not None:
        summary["search_limits"] = {
            "max_depth": search_limits.max_depth,
            "max_nodes": search_limits.max_nodes,
            "max_seconds": search_limits.max_seconds,
        }
    summary["base_seed"] = base_seed
    summary["errors"] = [{"seed": s, "error": e} for s, e in result.errors]

    print()
    print(json.dumps(summary, indent=2, default=str))

    if args.csv:
        result.aggregator.write_csv(args.csv)
        print(f"\nWrote per-match telemetry to {args.csv}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=2, default=str)
        print(f"Wrote run summary to {args.json}")

    if result.errors:
        print(f"\n{len(result.errors)} match(es) crashed — see 'errors' above",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
