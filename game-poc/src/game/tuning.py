"""
Weight Tuning — AI Stage 6 (README §53)
=========================================

§53 lists "optimize heuristic weights" among what self-play is for, and
"evolutionary tuning" among how. `game.ai.weights` made a weight vector
something that can leave the process; this is the loop that searches over
them.

    python -m game.tuning --generations 20 --population 6 --matches 40

The search
----------
A ``(1+λ)`` evolution strategy: one incumbent, λ mutants per generation,
each played head-to-head against the incumbent. The best mutant replaces
the incumbent only if it beats it by more than the noise. That is the
cheapest useful shape for a problem where a single fitness evaluation costs
minutes of match play — no population to maintain, no crossover to justify,
and one clear question per generation ("is this better than what we have?").

Paired, colour-balanced evaluation
----------------------------------
The single biggest lever on cost is variance, not speed. Every candidate
plays the *same seeds* as its rivals, and plays each seed once as White and
once as Black. Same deals, same openings, both sides of the first-player
edge — so the difference that survives is the weights and not the shuffle.
README §53's own balance work leaned on the same trick, and it is what
makes a 40-match evaluation say anything at all.

Fitness is mean score: 1 a win, ½ a draw, 0 a loss, over 2× ``matches``
games. A draw counting half matters here — README §53's corpus put
HeuristicBot's draw rate at 17-25 %, so a rule that ignored draws would be
throwing away a fifth of every measurement.

Honest about noise
------------------
A generation reports the standard error on every candidate's score and
promotes only on ``SIGMA`` standard errors of improvement. Hill-climbing on
noise is the default failure mode of this kind of search: with 80 games a
candidate needs to be about 8 points of score better to be distinguishable,
and a search that promotes anything less is just doing a random walk with
extra steps.

Resumable
---------
Every generation is checkpointed to ``out_dir`` as it completes, and a run
restarted with the same directory picks up from the last one. A twenty
generation run is hours; it should not be all-or-nothing.
"""

from __future__ import annotations

import argparse
import json
import math
import multiprocessing as mp
import random
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from game.ai.evaluation import DEFAULT_WEIGHTS, EvalWeights
from game.ai.weights import WEIGHT_FIELDS, weights_from_dict, weights_to_dict
from game.logger import get_logger
from game.sim import DEFAULT_MAX_STEPS, make_controller, play_match

_log = get_logger(__name__)

#: Standard errors of improvement required to promote a challenger.
SIGMA = 2.0

#: Games a zero-variance result needs before it counts as a clean sweep
#: rather than a small sample that happened to agree with itself.
MIN_SWEEP_GAMES = 10

#: Exploration bounds, as a multiple of the field's default. Wide on
#: purpose — these are not §51's play-style guardrails, which exist to stop
#: a personality tuning itself out of playing. A search has to be free to
#: propose the unreasonable; that is how it finds out the weight was wrong.
MIN_SCALE = 0.1
MAX_SCALE = 5.0

#: Fields that are square counts rather than scores. Mutating a radius
#: changes what the evaluation *looks at* rather than how much it cares,
#: which is a structural change hiding inside a numeric one — so they are
#: held fixed unless named explicitly.
STRUCTURAL_FIELDS = frozenset({
    "king_safety_radius", "enemy_king_radius", "royal_support_radius",
})

TUNABLE_FIELDS = frozenset(WEIGHT_FIELDS) - STRUCTURAL_FIELDS


@dataclass
class TuningSpec:
    generations: int = 10
    population: int = 6              # λ — mutants per generation
    matches: int = 40                # seeds per evaluation; each played twice
    sigma: float = 0.25              # mutation size, as a fraction of the value
    fields: tuple = ()               # which weights to tune ("" → all tunable)
    out_dir: str = "tuning"
    base_seed: int = 0
    workers: int = 0
    bot: str = "heuristic"
    max_steps: int = DEFAULT_MAX_STEPS
    seed: int = 12345                # the search's own RNG

    def tunable(self) -> tuple:
        if self.fields:
            unknown = set(self.fields) - set(WEIGHT_FIELDS)
            if unknown:
                raise ValueError(
                    f"unknown weight field(s): {', '.join(sorted(unknown))}"
                )
            return tuple(sorted(self.fields))
        return tuple(sorted(TUNABLE_FIELDS))

    def worker_count(self) -> int:
        import os

        return self.workers if self.workers > 0 else (os.cpu_count() or 1)


# ─────────────────────────────────────────────────────────────────────────────
# Mutation
# ─────────────────────────────────────────────────────────────────────────────

def mutate(
    weights: EvalWeights,
    fields: tuple,
    rng: random.Random,
    sigma: float = 0.25,
) -> EvalWeights:
    """
    One multiplicative Gaussian step per tuned field, clamped to the
    exploration bounds.

    Multiplicative rather than additive because the weights span two orders
    of magnitude — ``territory_square`` is 0.05 and ``king_safety_check`` is
    4.0, so a single additive step size would be a rounding error for one
    and a total rewrite of the other.
    """
    values = weights_to_dict(weights)
    for name in fields:
        default = getattr(DEFAULT_WEIGHTS, name)
        proposed = values[name] * (1.0 + rng.gauss(0.0, sigma))
        if default > 0:
            low, high = default * MIN_SCALE, default * MAX_SCALE
            proposed = max(low, min(high, proposed))
        values[name] = proposed
    return weights_from_dict(values)


# ─────────────────────────────────────────────────────────────────────────────
# Evaluation
# ─────────────────────────────────────────────────────────────────────────────

_WORKER: dict = {}


def _init_worker(spec: TuningSpec) -> None:
    from game.sim import load_default_registry

    _WORKER["spec"] = spec
    _WORKER["registry"] = load_default_registry()


def _play_pair(task: tuple) -> tuple:
    """
    One seed played twice — challenger as White, then as Black.

    Returns ``(challenger_score, incumbent_score)`` summed over the two, so
    a caller can average without caring which colour was which.
    """
    seed, challenger_values, incumbent_values = task
    spec: TuningSpec = _WORKER["spec"]
    registry = _WORKER["registry"]

    challenger = weights_from_dict(challenger_values)
    incumbent = weights_from_dict(incumbent_values)

    scores = [0.0, 0.0]
    for flip in (False, True):
        white_w, black_w = (
            (incumbent, challenger) if flip else (challenger, incumbent)
        )
        controllers = {
            "white": make_controller(
                spec.bot, seed * 2 + 1, "white", registry, weights=white_w,
            ),
            "black": make_controller(
                spec.bot, seed * 2 + 2, "black", registry, weights=black_w,
            ),
        }
        try:
            stats = play_match(
                seed=seed, controllers=controllers, registry=registry,
                max_steps=spec.max_steps,
            )
        except Exception:  # noqa: BLE001 — a crashed match scores nothing
            _log.exception("TUNING  match seed=%d crashed", seed)
            continue

        if stats.winner is None:
            scores[0] += 0.5
            scores[1] += 0.5
        else:
            challenger_side = "black" if flip else "white"
            won = stats.winner == challenger_side
            scores[0] += 1.0 if won else 0.0
            scores[1] += 0.0 if won else 1.0
    return tuple(scores)


@dataclass
class Result:
    """One challenger's head-to-head record against the incumbent."""

    score: float = 0.0
    games: int = 0
    sum_squares: float = 0.0

    def add(self, value: float) -> None:
        self.score += value
        self.games += 1
        self.sum_squares += value * value

    @property
    def mean(self) -> float:
        return self.score / self.games if self.games else 0.0

    @property
    def stderr(self) -> float:
        if self.games < 2:
            return 0.0
        variance = max(0.0, self.sum_squares / self.games - self.mean ** 2)
        return math.sqrt(variance / self.games)

    def as_dict(self) -> dict:
        return {
            "score": round(self.mean, 4),
            "stderr": round(self.stderr, 4),
            "games": self.games,
        }


def evaluate(
    challengers: list[EvalWeights],
    incumbent: EvalWeights,
    spec: TuningSpec,
    generation: int,
    pool=None,
) -> list[Result]:
    """Play every challenger against the incumbent over the same seeds."""
    seeds = [
        spec.base_seed + generation * spec.matches + i
        for i in range(spec.matches)
    ]
    incumbent_values = weights_to_dict(incumbent)

    tasks = [
        (seed, weights_to_dict(candidate), incumbent_values)
        for candidate in challengers
        for seed in seeds
    ]

    if pool is None:
        _init_worker(spec)
        outcomes = [_play_pair(task) for task in tasks]
    else:
        outcomes = pool.map(_play_pair, tasks)

    results = [Result() for _ in challengers]
    for index, (challenger_score, _incumbent_score) in enumerate(outcomes):
        results[index // len(seeds)].add(challenger_score / 2.0)
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Search
# ─────────────────────────────────────────────────────────────────────────────

def _checkpoint_path(out_dir: Path, generation: int) -> Path:
    return out_dir / f"generation-{generation:04d}.json"


def load_latest(out_dir: "str | Path") -> "tuple[int, EvalWeights] | None":
    """The most recent checkpoint in ``out_dir``, or None."""
    checkpoints = sorted(Path(out_dir).glob("generation-*.json"))
    if not checkpoints:
        return None
    data = json.loads(checkpoints[-1].read_text(encoding="utf-8"))
    return data["generation"], weights_from_dict(data["incumbent"])


def run(spec: TuningSpec, progress: bool = True) -> dict:
    """
    Search, checkpointing every generation.

    Returns the final incumbent and the history of what beat what.
    """
    out_dir = Path(spec.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    fields = spec.tunable()
    rng = random.Random(spec.seed)

    resumed = load_latest(out_dir)
    start_generation, incumbent = resumed if resumed else (0, DEFAULT_WEIGHTS)
    if resumed and progress:
        print(f"resuming from generation {start_generation}")

    # Replay the search RNG so a resumed run explores as the original would.
    for _ in range(start_generation * spec.population):
        rng.gauss(0.0, 1.0)

    history: list[dict] = []
    workers = min(spec.worker_count(), spec.population * spec.matches)
    started = time.perf_counter()

    pool = mp.Pool(workers, initializer=_init_worker, initargs=(spec,)) \
        if workers > 1 else None
    try:
        for generation in range(start_generation + 1, spec.generations + 1):
            challengers = [
                mutate(incumbent, fields, rng, spec.sigma)
                for _ in range(spec.population)
            ]
            results = evaluate(challengers, incumbent, spec, generation, pool)

            ranked = sorted(
                zip(results, challengers), key=lambda pair: -pair[0].mean
            )
            best, best_weights = ranked[0]
            # 0.5 is parity — the challenger and incumbent are the same
            # strength. Promote only on a margin the sample can actually see.
            margin = best.mean - 0.5
            if best.stderr > 0:
                promoted = margin > SIGMA * best.stderr
            else:
                # Zero variance: every game came out the same way. That is
                # the *most* a sample can say, not the least — the naive
                # rule divides by a zero error and refuses a clean sweep.
                # (game.analysis had the same trap in its Split.sigmas.)
                # A sweep still has to be over enough games to mean
                # something, hence MIN_SWEEP_GAMES.
                promoted = best.games >= MIN_SWEEP_GAMES and margin > 0

            entry = {
                "generation": generation,
                "promoted": promoted,
                "best": best.as_dict(),
                "margin": round(margin, 4),
                "required": round(SIGMA * best.stderr, 4),
                "candidates": [r.as_dict() for r, _ in ranked],
                "elapsed": round(time.perf_counter() - started, 1),
            }
            if promoted:
                incumbent = best_weights
            entry["incumbent"] = weights_to_dict(incumbent)
            history.append(entry)

            _checkpoint_path(out_dir, generation).write_text(
                json.dumps(entry, indent=2), encoding="utf-8",
            )
            if progress:
                verdict = "PROMOTED" if promoted else "held"
                print(
                    f"  gen {generation:3}/{spec.generations}  "
                    f"best {best.mean:.3f} ±{best.stderr:.3f}  "
                    f"margin {margin:+.3f} (needs {SIGMA * best.stderr:.3f})  "
                    f"{verdict}",
                    flush=True,
                )
    finally:
        if pool is not None:
            pool.close()
            pool.join()

    return {
        "generations": len(history),
        "promotions": sum(1 for h in history if h["promoted"]),
        "incumbent": weights_to_dict(incumbent),
        "changed": {
            name: round(value, 4)
            for name, value in weights_to_dict(incumbent).items()
            if abs(value - getattr(DEFAULT_WEIGHTS, name)) > 1e-9
        },
        "history": history,
        "seconds": round(time.perf_counter() - started, 1),
    }


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m game.tuning",
        description="AI Stage 6 (README §53) evaluation-weight search",
    )
    parser.add_argument("--generations", type=int, default=10)
    parser.add_argument("--population", type=int, default=6,
                        help="mutants per generation (λ)")
    parser.add_argument("--matches", type=int, default=40,
                        help="seeds per evaluation; each is played twice")
    parser.add_argument("--sigma", type=float, default=0.25,
                        help="mutation size, as a fraction of the weight")
    parser.add_argument("--fields", default=None,
                        help="comma-separated weights to tune (default: all)")
    parser.add_argument("--out", default="tuning")
    parser.add_argument("--seed", type=int, default=12345)
    parser.add_argument("--base-seed", type=int, default=0,
                        help="first match seed")
    parser.add_argument("--workers", type=int, default=0)
    parser.add_argument("--bot", default="heuristic",
                        choices=["heuristic", "search"])
    parser.add_argument("--export", default=None,
                        help="write the final weights here (game.ai.weights format)")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    spec = TuningSpec(
        generations=args.generations,
        population=args.population,
        matches=args.matches,
        sigma=args.sigma,
        fields=tuple(args.fields.split(",")) if args.fields else (),
        out_dir=args.out,
        base_seed=args.base_seed,
        workers=args.workers,
        bot=args.bot,
        seed=args.seed,
    )

    print(
        f"tuning {len(spec.tunable())} weight(s), {spec.generations} generations "
        f"× {spec.population} mutants × {spec.matches * 2} games  "
        f"({spec.generations * spec.population * spec.matches * 2} matches total)"
    )
    result = run(spec, progress=not args.quiet)

    print()
    print(json.dumps(
        {k: v for k, v in result.items() if k != "history"},
        indent=2, default=str,
    ))

    if args.export:
        from game.ai.weights import save_weights

        save_weights(weights_from_dict(result["incumbent"]), args.export)
        print(f"\nwrote {args.export}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
