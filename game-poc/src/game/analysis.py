"""
Corpus Analysis — AI Stage 6 (README §53)
===========================================

§53's first three goals for self-play are not machine learning:

    discover unexpected strategies
    evaluate balance
    estimate card strength
    ...
    discover Ritual patterns
    identify abusive Building combinations
    identify dominant King succession paths

All six are questions about a pile of finished matches, and all six are
answerable with counting — no model, no gradient, no new dependency.  This
module is the counting.

    python -m game.analysis --corpus corpus/heuristic-1k

Why the deal is the denominator
-------------------------------
The tempting way to rate a card is "how often does the side that plays it
win".  That number is almost meaningless here: a bot plays a card because
its evaluation liked the position, so the card's win rate is contaminated
by every reason the bot was already winning.  It measures §46's taste, not
the card.

Each player's 20-card deck is instead sampled at random from the shared
pool (``Game._build_deck_from_registry``), independently of anything either
side does.  That is a randomised assignment, and it is the whole reason a
self-play corpus can say anything causal at all.  So the primary number
here is conditioned on **the card being in the deck**, not on it being
played — "players dealt this card scored X, players not dealt it scored Y".
The played-rate is reported next to it as a *description of the bot*, which
is the only thing it can honestly be.

Reading the numbers
-------------------
Every rate carries its sample size and a standard error, and every
comparison carries whether the difference clears two of them.  A 1000-match
corpus gives each card a few hundred observations, which is enough to see a
large effect and nowhere near enough to see a small one — so effects that
do not clear the bar are reported as "noise" rather than quietly ranked.

A drawn match scores 0.5 for both sides, the same convention chess ratings
use.  Draws are common in self-play (README §53's notes: 45 % for
RandomBot, 20 % for HeuristicBot) and dropping them would throw away the
matches where a card most plausibly failed to break a deadlock.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Any, Iterator

from game.selfplay import iter_matches

#: Below this many observations a rate is reported but never ranked — the
#: interval is wider than any effect the game is likely to have.
MIN_SAMPLES = 30

#: How many standard errors a difference must clear to be called a signal.
SIGMA = 2.0

#: Reported in place of an infinite z when both groups have zero spread.
#: Finite so the report stays valid JSON — ``Infinity`` is not.
MAX_SIGMAS = 999.0


def set_thresholds(min_samples: int = MIN_SAMPLES, sigma: float = SIGMA) -> None:
    """
    Move the noise floor.

    A bigger corpus can afford a stricter bar; a smaller one has to admit it
    cannot see much. Exposed rather than hard-coded so a reader can check
    how much of a finding survives being asked for more evidence.
    """
    global MIN_SAMPLES, SIGMA
    MIN_SAMPLES = min_samples
    SIGMA = sigma


# ─────────────────────────────────────────────────────────────────────────────
# One player's match
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PlayerMatch:
    """One side of one finished match — the unit every rate is counted over."""

    seed: int
    player_id: str
    score: float                     # 1.0 win, 0.5 draw, 0.0 loss
    drawn_match: bool
    turns: int
    end_reason: "str | None"
    archetype: "str | None"
    active_king: "str | None"
    king_path: tuple
    deck: frozenset
    played: frozenset
    buildings: frozenset
    rituals: frozenset


def load_player_matches(corpus: "str | Path") -> list[PlayerMatch]:
    """
    Flatten a corpus into one row per player per finished match.

    Unfinished matches are dropped: they have no outcome, so they cannot
    contribute to any rate, and including them at 0.5 would invent draws
    the rules never produced.
    """
    rows: list[PlayerMatch] = []
    for match in iter_matches(corpus):
        if not match.get("completed"):
            continue
        telemetry = match.get("telemetry") or {}
        players = telemetry.get("players") or {}
        winner = match.get("winner")

        for player_id, stats in players.items():
            if winner is None:
                score = 0.5
            else:
                score = 1.0 if player_id == winner else 0.0
            rows.append(PlayerMatch(
                seed=match.get("seed", -1),
                player_id=player_id,
                score=score,
                drawn_match=winner is None,
                turns=match.get("turns", 0),
                end_reason=match.get("end_reason"),
                archetype=stats.get("archetype"),
                active_king=stats.get("active_king"),
                king_path=tuple(stats.get("kings_played") or ()),
                deck=frozenset(stats.get("deck_card_ids") or ()),
                played=frozenset((stats.get("cards_played_by_id") or {})),
                buildings=frozenset((stats.get("buildings_built_by_id") or {})),
                rituals=frozenset((stats.get("rituals_completed_by_id") or {})),
            ))
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# Rates
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Rate:
    """A mean score over a group of ``PlayerMatch`` rows, with its error."""

    n: int = 0
    total: float = 0.0
    sum_squares: float = 0.0
    wins: int = 0
    draws: int = 0

    def add(self, score: float) -> None:
        self.n += 1
        self.total += score
        self.sum_squares += score * score
        if score == 1.0:
            self.wins += 1
        elif score == 0.5:
            self.draws += 1

    @property
    def score(self) -> float:
        """Mean score: 1 per win, ½ per draw."""
        return self.total / self.n if self.n else 0.0

    @property
    def variance(self) -> float:
        if self.n < 2:
            return 0.0
        return max(0.0, self.sum_squares / self.n - self.score ** 2)

    @property
    def stderr(self) -> float:
        return math.sqrt(self.variance / self.n) if self.n else 0.0

    def as_dict(self) -> dict:
        return {
            "n": self.n,
            "score": round(self.score, 4),
            "stderr": round(self.stderr, 4),
            "wins": self.wins,
            "draws": self.draws,
            "losses": self.n - self.wins - self.draws,
        }


@dataclass
class Split:
    """A with/without comparison — the shape every §53 question reduces to."""

    key: str
    with_: Rate = field(default_factory=Rate)
    without: Rate = field(default_factory=Rate)

    @property
    def delta(self) -> float:
        return self.with_.score - self.without.score

    @property
    def stderr(self) -> float:
        return math.sqrt(self.with_.stderr ** 2 + self.without.stderr ** 2)

    @property
    def sigmas(self) -> float:
        if self.stderr:
            return abs(self.delta) / self.stderr
        # Neither group varies at all.  Either they agree exactly — no
        # effect, no evidence needed — or they are perfectly separated,
        # which is the *most* certain a comparison can be, not the least.
        # Dividing by a zero error and calling the result zero would have
        # reported a deterministic effect as noise.
        return 0.0 if self.delta == 0 else MAX_SIGMAS

    @property
    def significant(self) -> bool:
        return (
            self.with_.n >= MIN_SAMPLES
            and self.without.n >= MIN_SAMPLES
            and self.sigmas >= SIGMA
        )

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "delta": round(self.delta, 4),
            "stderr": round(self.stderr, 4),
            "sigmas": round(self.sigmas, 2),
            "significant": self.significant,
            "with": self.with_.as_dict(),
            "without": self.without.as_dict(),
        }


def _split_by_membership(
    rows: list[PlayerMatch],
    member_of: "Any",
    universe: "set | None" = None,
) -> dict[str, Split]:
    """
    For every key, the score of the rows that have it against the rows that
    do not.  ``member_of`` maps a row to the set of keys it carries.
    """
    keys: set = set(universe or ())
    memberships = []
    for row in rows:
        held = set(member_of(row))
        memberships.append(held)
        keys |= held

    splits = {key: Split(key=key) for key in keys}
    for row, held in zip(rows, memberships):
        for key, split in splits.items():
            (split.with_ if key in held else split.without).add(row.score)
    return splits


# ─────────────────────────────────────────────────────────────────────────────
# §53's questions
# ─────────────────────────────────────────────────────────────────────────────

def balance(rows: list[PlayerMatch], corpus: "str | Path") -> dict:
    """
    "evaluate balance" — the whole-corpus health check.

    First-player advantage is the headline: §57 lists it as an open design
    question, and it is the one number a self-play corpus answers directly.
    """
    matches = [m for m in iter_matches(corpus)]
    finished = [m for m in matches if m.get("completed")]

    white = Rate()
    black = Rate()
    for row in rows:
        (white if row.player_id == "white" else black).add(row.score)

    reasons: dict[str, int] = defaultdict(int)
    duel_triggers: dict[str, int] = defaultdict(int)
    for match in finished:
        reasons[str(match.get("end_reason"))] += 1
        if match.get("final_duel_trigger"):
            duel_triggers[str(match["final_duel_trigger"])] += 1

    turns = sorted(m.get("turns", 0) for m in finished)
    decisive = [m for m in finished if m.get("winner")]

    first_player = Split(key="white")
    for row in rows:
        (first_player.with_ if row.player_id == "white"
         else first_player.without).add(row.score)

    return {
        "matches": len(matches),
        "finished": len(finished),
        "unfinished": len(matches) - len(finished),
        "decisive": len(decisive),
        "drawn": len(finished) - len(decisive),
        "draw_rate": round(1 - len(decisive) / len(finished), 4) if finished else None,
        "first_player_advantage": first_player.as_dict(),
        "white": white.as_dict(),
        "black": black.as_dict(),
        "end_reasons": dict(sorted(reasons.items(), key=lambda kv: -kv[1])),
        "duel_triggers": dict(sorted(duel_triggers.items(), key=lambda kv: -kv[1])),
        "turns": {
            "mean": round(sum(turns) / len(turns), 1) if turns else None,
            "median": turns[len(turns) // 2] if turns else None,
            "p10": turns[len(turns) // 10] if turns else None,
            "p90": turns[(len(turns) * 9) // 10] if turns else None,
        },
    }


def card_strength(rows: list[PlayerMatch]) -> dict:
    """
    "estimate card strength" — conditioned on the deal, not on play.

    See the module docstring for why.  ``play_rate`` rides along as a
    description of the bot's preferences: a card that is never played
    cannot be earning its deck slot, whatever its score says.
    """
    splits = _split_by_membership(rows, lambda r: r.deck)

    play_counts: dict[str, int] = defaultdict(int)
    deck_counts: dict[str, int] = defaultdict(int)
    for row in rows:
        for card in row.deck:
            deck_counts[card] += 1
            if card in row.played:
                play_counts[card] += 1

    entries = []
    for key, split in splits.items():
        entry = split.as_dict()
        entry["play_rate"] = (
            round(play_counts[key] / deck_counts[key], 4) if deck_counts[key] else None
        )
        entries.append(entry)

    entries.sort(key=lambda e: -e["delta"])
    ranked = [e for e in entries if e["significant"]]

    # Split on the sign, not on position.  Slicing the ends of one ranked
    # list means that with fewer than twenty signals the same card shows up
    # as both the strongest and the weakest; the sign cannot do that, and it
    # is what the two words meant anyway.
    return {
        "cards": entries,
        "signals": ranked,
        "strongest": [e for e in ranked if e["delta"] > 0][:10],
        "weakest": [e for e in reversed(ranked) if e["delta"] < 0][:10],
        "never_played": sorted(
            e["key"] for e in entries
            if e["play_rate"] is not None and e["play_rate"] == 0.0
        ),
    }


def buildings(rows: list[PlayerMatch]) -> dict:
    """
    "identify abusive Building combinations".

    Single Buildings first, then pairs.  Note the denominator changes here:
    a Building is *chosen*, not dealt, so these are correlations rather
    than the causal read card strength gets.  A pair that wins may simply be
    the pair a winning player had time to finish.
    """
    singles = _split_by_membership(rows, lambda r: r.buildings)
    pairs = _split_by_membership(
        rows, lambda r: {" + ".join(sorted(p)) for p in combinations(sorted(r.buildings), 2)}
    )

    def ranked(splits: dict[str, Split]) -> list[dict]:
        out = [s.as_dict() for s in splits.values() if s.significant]
        out.sort(key=lambda e: -e["delta"])
        return out

    return {
        "singles": ranked(singles),
        "pairs": ranked(pairs)[:15],
        "note": "correlational — Buildings are chosen, not dealt",
    }


def kings(rows: list[PlayerMatch]) -> dict:
    """"identify dominant King succession paths"."""
    active = _split_by_membership(
        rows, lambda r: {r.active_king} if r.active_king else set()
    )
    paths: dict[str, Rate] = defaultdict(Rate)
    for row in rows:
        if row.king_path:
            paths[" → ".join(row.king_path)].add(row.score)

    ranked_paths = [
        {"path": path, **rate.as_dict()}
        for path, rate in paths.items() if rate.n >= MIN_SAMPLES
    ]
    ranked_paths.sort(key=lambda e: -e["score"])

    active_ranked = [s.as_dict() for s in active.values() if s.significant]
    active_ranked.sort(key=lambda e: -e["delta"])

    return {
        "active_king": active_ranked,
        "succession_paths": ranked_paths,
        "note": "correlational — a King is chosen, not dealt",
    }


def rituals(rows: list[PlayerMatch]) -> dict:
    """"discover Ritual patterns" — which completed Rituals actually pay."""
    completed = _split_by_membership(rows, lambda r: r.rituals)
    any_ritual = Split(key="completed any Ritual")
    for row in rows:
        (any_ritual.with_ if row.rituals else any_ritual.without).add(row.score)

    ranked = [s.as_dict() for s in completed.values() if s.significant]
    ranked.sort(key=lambda e: -e["delta"])
    return {
        "any_ritual": any_ritual.as_dict(),
        "by_ritual": ranked,
        "note": "correlational — completing a Ritual is an achievement, "
                "not an assignment",
    }


def vessels(rows: list[PlayerMatch], registry=None) -> dict:
    """
    Monster play rate grouped by the Vessels each Monster can be summoned
    onto — the strongest single predictor of whether a card is ever played.

    This started as a query about one card.  ``vessel_reclaimer`` sat at a
    15 % play rate where most cards were above 90 %, which looked like the
    dead-card signature that turned up two broken Spells.  It is not: every
    Monster restricted to ``bishop/queen`` sits between 13 % and 17 %, and
    the pattern holds across the whole pool.  A player has eight Pawns, two
    Bishops and one Queen, so vessel availability decides how often a card
    can be played at all, almost independently of what the card does.

    Reported because it is a balance property of the pool rather than of
    any card in it: a Monster that can only ride a Queen is a dead card in
    hand roughly nineteen times in twenty, however good it is when it
    lands.
    """
    if registry is None:
        return {"note": "no card registry available"}

    from game.cards.card import MonsterCard

    cards = getattr(registry, "_cards", None) or getattr(registry, "cards", {})
    strength = {e["key"]: e for e in card_strength(rows)["cards"]}

    groups: dict[str, list] = defaultdict(list)
    for card_id, card in cards.items():
        if not isinstance(card, MonsterCard):
            continue
        entry = strength.get(card_id)
        if entry is None or entry["play_rate"] is None:
            continue
        key = "/".join(sorted(getattr(card, "supported_vessels", []) or [])) or "(none)"
        groups[key].append((card_id, entry["play_rate"]))

    out = []
    for key, items in groups.items():
        rates = [r for _, r in items]
        out.append({
            "vessels": key,
            "cards": len(items),
            "mean_play_rate": round(sum(rates) / len(rates), 4),
            "min": round(min(rates), 4),
            "max": round(max(rates), 4),
        })
    out.sort(key=lambda e: e["mean_play_rate"])
    return {"by_vessel_group": out}


def archetypes(rows: list[PlayerMatch]) -> dict:
    """Archetype win rates — README §42's metric, over a corpus."""
    rates: dict[str, Rate] = defaultdict(Rate)
    for row in rows:
        if row.archetype:
            rates[row.archetype].add(row.score)
    out = [
        {"archetype": name, **rate.as_dict()}
        for name, rate in rates.items() if rate.n >= MIN_SAMPLES
    ]
    out.sort(key=lambda e: -e["score"])
    return {"by_archetype": out}


def full_report(corpus: "str | Path", registry=None) -> dict:
    """
    ``registry`` (the public card definitions) unlocks the vessel section;
    everything else is answerable from the corpus alone.
    """
    if registry is None:
        try:
            from game.sim import load_default_registry
            registry = load_default_registry()
        except Exception:  # noqa: BLE001 — the rest of the report still works
            registry = None
    rows = load_player_matches(corpus)
    if not rows:
        return {"corpus": str(corpus), "error": "no finished matches found"}
    return {
        "corpus": str(corpus),
        "observations": len(rows),
        "min_samples": MIN_SAMPLES,
        "sigma": SIGMA,
        "balance": balance(rows, corpus),
        "card_strength": card_strength(rows),
        "buildings": buildings(rows),
        "kings": kings(rows),
        "rituals": rituals(rows),
        "archetypes": archetypes(rows),
        "vessels": vessels(rows, registry),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Rendering
# ─────────────────────────────────────────────────────────────────────────────

def _bar(delta: float, scale: float = 0.1, width: int = 21) -> str:
    """
    A centred bar: left of centre is a losing card, right a winning one.

    ``scale`` is the delta that fills half the width — passed in from the
    largest effect actually on screen, so the picture is not all saturation
    at one corpus size and all stubs at another.
    """
    half = width // 2
    scale = scale or 0.1
    scaled = max(-half, min(half, round(delta * half / scale)))
    cells = [" "] * width
    cells[half] = "│"
    if scaled > 0:
        for i in range(half + 1, half + 1 + scaled):
            cells[i] = "█"
    elif scaled < 0:
        for i in range(half + scaled, half):
            cells[i] = "█"
    return "".join(cells)


def render_text(report: dict) -> str:
    if "error" in report:
        return f"{report['corpus']}: {report['error']}"

    out: list[str] = []
    add = out.append

    b = report["balance"]
    add(f"CORPUS  {report['corpus']}")
    add(f"  {b['matches']} matches — {b['decisive']} decisive, "
        f"{b['drawn']} drawn, {b['unfinished']} unfinished")
    add(f"  {report['observations']} player-observations")
    add("")

    add("BALANCE")
    fp = b["first_player_advantage"]
    verdict = "SIGNAL" if fp["significant"] else "noise"
    add(f"  first player   white {fp['with']['score']:.3f} vs "
        f"black {fp['without']['score']:.3f}   "
        f"Δ{fp['delta']:+.3f} ±{fp['stderr']:.3f}  ({fp['sigmas']:.1f}σ, {verdict})")
    add(f"  draw rate      {b['draw_rate']:.1%}")
    t = b["turns"]
    add(f"  match length   mean {t['mean']}  median {t['median']}  "
        f"p10 {t['p10']}  p90 {t['p90']}")
    add(f"  ended by       " + ", ".join(
        f"{k} {v}" for k, v in b["end_reasons"].items()))
    if b["duel_triggers"]:
        add(f"  duels          " + ", ".join(
            f"{k} {v}" for k, v in b["duel_triggers"].items()))
    add("")

    cs = report["card_strength"]
    add(f"CARD STRENGTH   (score when dealt the card, vs when not; "
        f"{len(cs['signals'])} of {len(cs['cards'])} clear {report['sigma']}σ)")
    signals = cs["signals"]
    if signals:
        shown = signals[:10] + (signals[-10:] if len(signals) > 20 else [])
        elided = len(signals) - len(shown)
        scale = max((abs(e["delta"]) for e in shown), default=0.1)
        add(f"  {'card':<26} {'Δscore':>8} {'±':>6} {'σ':>5} {'n':>5} "
            f"{'play%':>6}  {'weaker ← → stronger':^21}")
        for i, e in enumerate(shown):
            if elided and i == 10:
                add(f"  … {elided} more between …")
            add(f"  {e['key']:<26} {e['delta']:>+8.3f} {e['stderr']:>6.3f} "
                f"{e['sigmas']:>5.1f} {e['with']['n']:>5} "
                f"{(e['play_rate'] or 0):>6.0%}  {_bar(e['delta'], scale)}")
    else:
        add("  no card clears the noise floor at this corpus size")
    if cs["never_played"]:
        add(f"  never played:  {', '.join(cs['never_played'])}")
    add("")

    for title, section, key in (
        ("RITUALS", report["rituals"], "by_ritual"),
        ("BUILDINGS", report["buildings"], "singles"),
        ("BUILDING PAIRS", report["buildings"], "pairs"),
        ("KINGS", report["kings"], "active_king"),
    ):
        entries = section[key]
        add(f"{title}   ({section.get('note', '')})")
        if title == "RITUALS":
            ar = section["any_ritual"]
            add(f"  completing any Ritual: {ar['with']['score']:.3f} "
                f"vs {ar['without']['score']:.3f}  Δ{ar['delta']:+.3f} "
                f"({ar['sigmas']:.1f}σ)")
        if entries:
            for e in entries[:8]:
                add(f"  {e['key']:<40} {e['delta']:>+7.3f} "
                    f"({e['sigmas']:>4.1f}σ, n={e['with']['n']})")
        else:
            add("  nothing clears the noise floor")
        add("")

    groups = report.get("vessels", {}).get("by_vessel_group") or []
    if groups:
        add("MONSTER PLAY RATE BY VESSEL   (availability, not card quality)")
        for g in groups:
            add(f"  {g['vessels']:<24} {g['mean_play_rate']:>6.0%}  "
                f"({g['cards']} cards, {g['min']:.0%}-{g['max']:.0%})")
        add("")

    arch = report["archetypes"]["by_archetype"]
    add("ARCHETYPES")
    if arch:
        for e in arch:
            add(f"  {e['archetype']:<20} {e['score']:.3f} ±{e['stderr']:.3f}  "
                f"(n={e['n']})")
    else:
        add("  too few observations per archetype")
    add("")

    paths = report["kings"]["succession_paths"]
    if paths:
        add("KING SUCCESSION PATHS")
        for e in paths[:8]:
            add(f"  {e['path']:<44} {e['score']:.3f}  (n={e['n']})")
        add("")

    return "\n".join(out)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m game.analysis",
        description="AI Stage 6 (README §53) corpus analysis",
    )
    parser.add_argument("--corpus", required=True, help="a game.selfplay corpus dir")
    parser.add_argument("--json", default=None, help="also write the full report here")
    parser.add_argument("--min-samples", type=int, default=MIN_SAMPLES)
    parser.add_argument("--sigma", type=float, default=SIGMA)
    args = parser.parse_args(argv)
    set_thresholds(args.min_samples, args.sigma)

    report = full_report(args.corpus)
    print(render_text(report))

    if args.json:
        Path(args.json).write_text(
            json.dumps(report, indent=2, default=str), encoding="utf-8"
        )
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
