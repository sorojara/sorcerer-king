"""
Self-Play Corpus Runner — AI Stage 6 (README §53)
===================================================

§53 sets one gate before any of the machine learning it lists is worth
starting:

    "Once the engine can run many headless games — AI vs AI, 10,000+,
     100,000+ matches — self-play becomes useful."

``game.sim`` already plays a match and already gathers §42 telemetry.  What
it does not do is survive the scale that sentence asks for: ``run_matches``
is a single-process ``for`` loop that holds everything in memory and hands
back nothing until the last match lands, so a run long enough to matter is
also long enough to be interrupted, and an interrupted run is a total loss.

This module is that loop made durable:

    parallel    — one worker per core, since matches are independent and
                  Python's GIL is not.
    sharded     — each worker appends to its own JSONL file, so no worker
                  waits on another to write and a reader can fan out again.
    resumable   — a seed already recorded is not replayed.  Stopping a run
                  and restarting it costs at most the matches that were
                  in flight.
    streaming   — one line per match, flushed as it completes.  A killed
                  run keeps everything it had already finished.

Two files come out of a run, in ``out_dir``:

    matches-NNNN.jsonl    one line per match: the seed it came from, who
                          played, how it ended, and its §42 telemetry.
    decisions-NNNN.jsonl  (``--trajectories``) one line per decision: the
                          position as §46's evaluator saw it, everything
                          the controller could have played, and what it
                          chose.  This is the row shape §53's "policy/value
                          networks" and "offline model training from
                          simulation data" both start from.

Usage::

    python -m game.selfplay --matches 10000 --out corpus/ \\
        --white heuristic --black heuristic --workers 4

    python -m game.selfplay --matches 2000 --out corpus/ \\
        --white search --black search --trajectories

    # interrupted?  the same command resumes it.

Reading a corpus back::

    from game.selfplay import iter_matches, iter_examples

    for match in iter_matches("corpus/"):
        print(match["seed"], match["winner"], match["end_reason"])

    for row in iter_examples("corpus/"):     # decisions joined to outcomes
        train(row["eval"], row["result"])    # result: +1 / 0 / -1
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import random
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator

from game.ai.personality import DEFAULT_PERSONALITY, PERSONALITY_KEYS
from game.logger import get_logger
from game.sim import CONTROLLER_KINDS, DEFAULT_MAX_STEPS, make_controller, play_match

_log = get_logger(__name__)

MATCH_SHARD_GLOB = "matches-*.jsonl"
DECISION_SHARD_GLOB = "decisions-*.jsonl"

#: Chunks per worker.  More than one so progress arrives during a run and a
#: slow chunk cannot leave a core idle to the end; few enough that the
#: per-chunk setup (registry load, file open) stays amortised.
_CHUNKS_PER_WORKER = 8


# ─────────────────────────────────────────────────────────────────────────────
# What a run is
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CorpusSpec:
    """Everything a self-play run needs, in one picklable object."""

    matches: int
    out_dir: str = "corpus"
    white: str = "heuristic"
    black: str = "heuristic"
    base_seed: int = 0
    workers: int = 0                    # 0 → one per core
    max_steps: int = DEFAULT_MAX_STEPS

    white_personality: "str | None" = None
    black_personality: "str | None" = None
    white_weights: "str | None" = None  # path to a game.ai.weights file
    black_weights: "str | None" = None

    search_depth: "int | None" = None
    search_nodes: "int | None" = None
    search_seconds: "float | None" = None
    mc_samples: "int | None" = None
    mc_depth: "int | None" = None

    # README §53 termination ceilings; None → MatchLimits() defaults.
    repetition_limit: "int | None" = None
    no_progress_plies: "int | None" = None
    max_turns: "int | None" = None

    trajectories: bool = False
    #: Include §46's evaluation breakdown on every decision row.  It is what
    #: makes the corpus trainable rather than merely a move log, and it is
    #: also the expensive part — one full evaluation per decision.
    features: bool = True

    def seeds(self) -> list[int]:
        return list(range(self.base_seed, self.base_seed + self.matches))

    def worker_count(self) -> int:
        return self.workers if self.workers > 0 else (os.cpu_count() or 1)

    def limits(self):
        from game.core.state import MatchLimits

        base = MatchLimits()
        return MatchLimits(
            repetition_limit=(
                self.repetition_limit if self.repetition_limit is not None
                else base.repetition_limit
            ),
            no_progress_plies=(
                self.no_progress_plies if self.no_progress_plies is not None
                else base.no_progress_plies
            ),
            max_turns=(
                self.max_turns if self.max_turns is not None else base.max_turns
            ),
        )


@dataclass
class CorpusResult:
    """What a run did — not what it produced.  The corpus is on disk."""

    requested: int
    already_present: int
    played: int
    failed: int
    seconds: float
    out_dir: str
    errors: list = field(default_factory=list)

    @property
    def matches_per_second(self) -> float:
        return self.played / self.seconds if self.seconds > 0 else 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Resume
# ─────────────────────────────────────────────────────────────────────────────

def iter_matches(out_dir: "str | Path") -> Iterator[dict]:
    """Every match record in a corpus, across all shards."""
    for shard in sorted(Path(out_dir).glob(MATCH_SHARD_GLOB)):
        with open(shard, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    # A run killed mid-write leaves at most one torn line.
                    _log.warning("SELFPLAY  skipping torn line in %s", shard)


def iter_decisions(out_dir: "str | Path") -> Iterator[dict]:
    """Every decision record in a corpus, across all shards."""
    for shard in sorted(Path(out_dir).glob(DECISION_SHARD_GLOB)):
        with open(shard, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    _log.warning("SELFPLAY  skipping torn line in %s", shard)


def count_decisions(out_dir: "str | Path") -> int:
    """
    How many decision rows a corpus holds, without parsing any of them.

    ``summarize`` only wants the count, and a 100,000-match corpus with
    trajectories holds tens of millions of rows — running each one through
    ``json.loads`` to arrive at a number would take longer than the whole
    rest of the summary. Counting newlines in binary chunks reads the same
    files an order of magnitude faster.
    """
    total = 0
    for shard in Path(out_dir).glob(DECISION_SHARD_GLOB):
        with open(shard, "rb") as fh:
            while chunk := fh.read(1 << 20):
                total += chunk.count(b"\n")
    return total


def completed_seeds(out_dir: "str | Path") -> set[int]:
    """
    Which seeds this corpus already holds a finished match for.

    A match abandoned at ``max_steps`` counts as done: replaying it would
    only abandon it again.  It is recorded with ``completed: false`` so a
    reader can drop it.
    """
    return {m["seed"] for m in iter_matches(out_dir) if "seed" in m}


def iter_examples(out_dir: "str | Path") -> Iterator[dict]:
    """
    Decisions joined to the outcome of the match they came from.

    ``result`` is from the acting player's side: +1 they won, -1 they lost,
    0 the match was drawn by a §53 limit.  Decisions from matches with no
    recorded outcome are skipped rather than labelled with a guess.
    """
    outcomes = {
        m["seed"]: m for m in iter_matches(out_dir) if m.get("completed")
    }
    for row in iter_decisions(out_dir):
        match = outcomes.get(row.get("seed"))
        if match is None:
            continue
        winner = match.get("winner")
        row = dict(row)
        row["result"] = 0 if winner is None else (
            1 if winner == row.get("player") else -1
        )
        row["end_reason"] = match.get("end_reason")
        yield row


# ─────────────────────────────────────────────────────────────────────────────
# Worker
# ─────────────────────────────────────────────────────────────────────────────

_WORKER: dict = {}


def _init_worker(spec: CorpusSpec) -> None:
    """
    Per-process setup, paid once instead of once per match.

    The card registry is a YAML load; the weight files are small but are
    still files.  Neither changes across a run.
    """
    from game.ai.weights import load_weights
    from game.sim import load_default_registry

    _WORKER["spec"] = spec
    _WORKER["registry"] = load_default_registry()
    _WORKER["limits"] = spec.limits()

    for side in ("white", "black"):
        path = getattr(spec, f"{side}_weights")
        _WORKER[f"{side}_weights"] = load_weights(path) if path else (None, None)

    search_limits = None
    if {"search", "montecarlo", "personality"} & {spec.white, spec.black}:
        from game.ai.search import SearchLimits

        base = SearchLimits()
        search_limits = SearchLimits(
            max_depth=spec.search_depth if spec.search_depth is not None else base.max_depth,
            max_nodes=spec.search_nodes if spec.search_nodes is not None else base.max_nodes,
            max_seconds=(
                spec.search_seconds if spec.search_seconds is not None
                else base.max_seconds
            ),
        )
    _WORKER["search_limits"] = search_limits

    mc_limits = None
    if "montecarlo" in (spec.white, spec.black):
        from game.ai.monte_carlo import MonteCarloLimits

        base_mc = MonteCarloLimits()
        mc_limits = MonteCarloLimits(
            samples=spec.mc_samples if spec.mc_samples is not None else base_mc.samples,
            depth=spec.mc_depth if spec.mc_depth is not None else base_mc.depth,
        )
    _WORKER["mc_limits"] = mc_limits


def _controllers_for(seed: int) -> dict:
    spec: CorpusSpec = _WORKER["spec"]
    out = {}
    for side, offset in (("white", 1), ("black", 2)):
        weights, biases = _WORKER[f"{side}_weights"]
        out[side] = make_controller(
            getattr(spec, side),
            seed * 2 + offset,
            side,
            _WORKER["registry"],
            _WORKER["search_limits"],
            _WORKER["mc_limits"],
            getattr(spec, f"{side}_personality"),
            weights=weights,
            biases=biases,
        )
    return out


def _match_record(seed: int, spec: CorpusSpec, stats, seconds: float) -> dict:
    """One match, flattened to the fields a corpus reader actually joins on."""
    record = {
        "seed": seed,
        "white": spec.white,
        "black": spec.black,
        "completed": stats.completed,
        "winner": stats.winner,
        "end_reason": stats.end_reason,
        "turns": stats.turn_count,
        "plies": stats.ply_count,
        "actions": stats.action_count,
        "seconds": round(seconds, 3),
        "final_duel": stats.final_duel_triggered,
        "final_duel_trigger": stats.final_duel_trigger,
        "telemetry": stats.to_dict(),
    }
    if spec.white == "personality":
        record["white_personality"] = spec.white_personality or DEFAULT_PERSONALITY
    if spec.black == "personality":
        record["black_personality"] = spec.black_personality or DEFAULT_PERSONALITY
    return record


def _play_chunk(task: tuple) -> dict:
    """
    Play one chunk of seeds and append them to this chunk's own shard.

    Runs in a worker process.  Everything it returns is a small summary —
    the corpus itself has already been written and flushed by the time this
    function comes back, which is what makes a killed run recoverable.
    """
    shard_index, seeds = task
    spec: CorpusSpec = _WORKER["spec"]
    out_dir = Path(spec.out_dir)
    registry = _WORKER["registry"]
    limits = _WORKER["limits"]

    played = 0
    failed = 0
    errors: list = []

    match_path = out_dir / f"matches-{shard_index:04d}.jsonl"
    decision_path = out_dir / f"decisions-{shard_index:04d}.jsonl"

    matches_fh = open(match_path, "a", encoding="utf-8")
    decisions_fh = open(decision_path, "a", encoding="utf-8") if spec.trajectories else None

    try:
        for seed in seeds:
            rows: list = []
            recorder = _make_recorder(seed, rows, spec, registry) if decisions_fh else None
            started = time.perf_counter()
            try:
                stats = play_match(
                    seed=seed,
                    controllers=_controllers_for(seed),
                    registry=registry,
                    max_steps=spec.max_steps,
                    on_decision=recorder,
                    limits=limits,
                )
            except Exception as exc:  # noqa: BLE001 — a corpus run reports, never dies
                _log.exception("SELFPLAY  match seed=%d crashed", seed)
                failed += 1
                errors.append({"seed": seed, "error": f"{type(exc).__name__}: {exc}"})
                continue

            matches_fh.write(
                json.dumps(
                    _match_record(seed, spec, stats, time.perf_counter() - started),
                    default=str,
                ) + "\n"
            )
            matches_fh.flush()

            if decisions_fh is not None:
                for row in rows:
                    decisions_fh.write(json.dumps(row, default=str) + "\n")
                decisions_fh.flush()

            played += 1
    finally:
        matches_fh.close()
        if decisions_fh is not None:
            decisions_fh.close()

    return {"shard": shard_index, "played": played, "failed": failed, "errors": errors}


def _make_recorder(seed: int, rows: list, spec: CorpusSpec, registry):
    """
    Build the ``on_decision`` hook that turns a decision into a corpus row.

    The row is deliberately not the raw Observation.  An Observation is a
    deep object and there are hundreds of decisions per match; written out
    whole it would make a corpus that is expensive to produce, expensive to
    read, and still not in the shape a model wants.  §46's evaluation
    breakdown is already a fixed-width, named, tuned-by-hand feature vector
    over exactly the systems this game is about, so that is what goes in —
    with the raw counts a model might want to re-derive from beside it.
    """
    from game.ai.evaluation import DEFAULT_WEIGHTS, evaluation_breakdown

    weights = _WORKER.get("white_weights", (None, None))[0] or DEFAULT_WEIGHTS

    def record(pid: str, observation, legal: list, action) -> None:
        row = {
            "seed": seed,
            "step": len(rows),
            "player": pid,
            "turn": getattr(observation, "turn_number", None),
            "phase": getattr(getattr(observation, "phase", None), "value", None),
            "action": type(action).__name__,
            "n_legal": len(legal),
        }
        if spec.features:
            try:
                row["eval"] = {
                    k: round(v, 4)
                    for k, v in evaluation_breakdown(
                        observation, weights, registry
                    ).items()
                }
            except Exception:  # noqa: BLE001 — a feature is never worth a match
                row["eval"] = None
        rows.append(row)

    return record


# ─────────────────────────────────────────────────────────────────────────────
# Run
# ─────────────────────────────────────────────────────────────────────────────

def run_corpus(spec: CorpusSpec, progress: bool = True) -> CorpusResult:
    """
    Play ``spec.matches`` matches into ``spec.out_dir``, in parallel, once.

    Seeds already recorded there are skipped, so calling this again after an
    interruption finishes the run rather than restarting it.
    """
    out_dir = Path(spec.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    wanted = spec.seeds()
    done = completed_seeds(out_dir)
    todo = [s for s in wanted if s not in done]

    if progress:
        print(
            f"corpus {out_dir}: {len(wanted)} requested, "
            f"{len(wanted) - len(todo)} already present, {len(todo)} to play"
        )

    started = time.perf_counter()
    if not todo:
        return CorpusResult(
            requested=len(wanted), already_present=len(wanted), played=0,
            failed=0, seconds=0.0, out_dir=str(out_dir),
        )

    workers = min(spec.worker_count(), len(todo))
    chunks = _partition(todo, max(1, workers * _CHUNKS_PER_WORKER))
    tasks = list(enumerate(chunks))

    played = failed = 0
    errors: list = []

    if workers == 1:
        # One core, or one chunk: skip the pool so a debugger can follow it.
        _init_worker(spec)
        results = (_play_chunk(task) for task in tasks)
        for done_count, summary in enumerate(results, 1):
            played += summary["played"]
            failed += summary["failed"]
            errors.extend(summary["errors"])
            _report(progress, done_count, len(tasks), played, len(todo), started)
    else:
        with mp.Pool(workers, initializer=_init_worker, initargs=(spec,)) as pool:
            for done_count, summary in enumerate(
                pool.imap_unordered(_play_chunk, tasks), 1
            ):
                played += summary["played"]
                failed += summary["failed"]
                errors.extend(summary["errors"])
                _report(progress, done_count, len(tasks), played, len(todo), started)

    return CorpusResult(
        requested=len(wanted),
        already_present=len(wanted) - len(todo),
        played=played,
        failed=failed,
        seconds=time.perf_counter() - started,
        out_dir=str(out_dir),
        errors=errors,
    )


def _partition(seeds: list[int], parts: int) -> list[list[int]]:
    """
    Deal seeds round-robin into ``parts`` chunks.

    Round-robin rather than contiguous slices: match cost varies a lot with
    the seed (a 50-turn game and a 300-turn game are both ordinary), and
    dealing interleaves the long ones instead of stacking them all into the
    last chunk.
    """
    parts = max(1, min(parts, len(seeds)))
    chunks: list[list[int]] = [[] for _ in range(parts)]
    for i, seed in enumerate(seeds):
        chunks[i % parts].append(seed)
    return chunks


def _report(
    progress: bool, chunks_done: int, chunks_total: int,
    played: int, total: int, started: float,
) -> None:
    if not progress:
        return
    elapsed = time.perf_counter() - started
    rate = played / elapsed if elapsed > 0 else 0.0
    remaining = (total - played) / rate if rate > 0 else float("inf")
    print(
        f"  chunk {chunks_done}/{chunks_total}  {played}/{total} matches  "
        f"{rate:.2f} match/s  eta {remaining / 60:.1f} min",
        flush=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────

def summarize(out_dir: "str | Path") -> dict:
    """
    Read a corpus back and report what is in it.

    Deliberately re-reads from disk rather than accumulating in memory
    during the run: the corpus is the artefact, and a summary that can only
    be produced by the process that wrote it is not much of a check on it.
    """
    from collections import Counter

    matches = list(iter_matches(out_dir))
    decided = [m for m in matches if m.get("winner")]
    drawn = [m for m in matches if m.get("completed") and not m.get("winner")]
    unfinished = [m for m in matches if not m.get("completed")]

    turns = [m["turns"] for m in matches if m.get("turns") is not None]
    seconds = [m["seconds"] for m in matches if m.get("seconds") is not None]

    return {
        "matches": len(matches),
        "decided": len(decided),
        "drawn": len(drawn),
        "unfinished": len(unfinished),
        "decisive_rate": round(len(decided) / len(matches), 4) if matches else None,
        "wins": dict(Counter(m["winner"] for m in decided).most_common()),
        "end_reasons": dict(Counter(m.get("end_reason") for m in matches).most_common()),
        "mean_turns": round(sum(turns) / len(turns), 2) if turns else None,
        "mean_seconds": round(sum(seconds) / len(seconds), 3) if seconds else None,
        "shards": len(list(Path(out_dir).glob(MATCH_SHARD_GLOB))),
        "decision_rows": count_decisions(out_dir),
    }


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m game.selfplay",
        description="AI Stage 6 (README §53) self-play corpus runner",
    )
    parser.add_argument("--matches", type=int, default=100)
    parser.add_argument("--out", default="corpus", help="corpus directory")
    parser.add_argument("--white", default="heuristic", choices=CONTROLLER_KINDS)
    parser.add_argument("--black", default="heuristic", choices=CONTROLLER_KINDS)
    parser.add_argument("--seed", type=int, default=0,
                        help="base seed; the corpus is reproducible from it")
    parser.add_argument("--workers", type=int, default=0,
                        help="processes (default: one per core)")
    parser.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS)

    parser.add_argument("--white-personality", default=None, choices=PERSONALITY_KEYS)
    parser.add_argument("--black-personality", default=None, choices=PERSONALITY_KEYS)
    parser.add_argument("--white-weights", default=None,
                        help="JSON weight file (see game.ai.weights)")
    parser.add_argument("--black-weights", default=None)

    parser.add_argument("--search-depth", type=int, default=None)
    parser.add_argument("--search-nodes", type=int, default=None)
    parser.add_argument("--search-seconds", type=float, default=None)
    parser.add_argument("--mc-samples", type=int, default=None)
    parser.add_argument("--mc-depth", type=int, default=None)

    parser.add_argument("--repetition-limit", type=int, default=None)
    parser.add_argument("--no-progress-plies", type=int, default=None)
    parser.add_argument("--max-turns", type=int, default=None)

    parser.add_argument("--trajectories", action="store_true",
                        help="also record one row per decision")
    parser.add_argument("--no-features", action="store_true",
                        help="trajectory rows without the evaluation breakdown")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--summary-only", action="store_true",
                        help="report on an existing corpus and play nothing")
    args = parser.parse_args(argv)

    if args.summary_only:
        print(json.dumps(summarize(args.out), indent=2, default=str))
        return 0

    for side in ("white", "black"):
        if getattr(args, f"{side}_weights") and getattr(args, side) == "personality":
            parser.error(
                f"--{side}-weights and --{side} personality both set the "
                f"evaluation table; pick one"
            )

    spec = CorpusSpec(
        matches=args.matches,
        out_dir=args.out,
        white=args.white,
        black=args.black,
        base_seed=args.seed,
        workers=args.workers,
        max_steps=args.max_steps,
        white_personality=args.white_personality,
        black_personality=args.black_personality,
        white_weights=args.white_weights,
        black_weights=args.black_weights,
        search_depth=args.search_depth,
        search_nodes=args.search_nodes,
        search_seconds=args.search_seconds,
        mc_samples=args.mc_samples,
        mc_depth=args.mc_depth,
        repetition_limit=args.repetition_limit,
        no_progress_plies=args.no_progress_plies,
        max_turns=args.max_turns,
        trajectories=args.trajectories,
        features=not args.no_features,
    )

    print(
        f"self-play: {spec.matches} match(es)  "
        f"white={spec.white} vs black={spec.black}  "
        f"workers={spec.worker_count()}  base_seed={spec.base_seed}"
    )

    try:
        result = run_corpus(spec, progress=not args.quiet)
    except KeyboardInterrupt:
        print("\ninterrupted — rerun the same command to resume", file=sys.stderr)
        return 130

    print()
    print(json.dumps(asdict(result), indent=2, default=str))
    print()
    print(json.dumps(summarize(spec.out_dir), indent=2, default=str))

    if result.failed:
        print(f"\n{result.failed} match(es) crashed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
