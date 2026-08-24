"""
Tunable weights, on disk — AI Stage 6 (README §53)
====================================================

§53 lists "optimize heuristic weights" among what self-play is for, and
§46's evaluation was already written as data — ``EvalWeights`` is a flat
dataclass of floats and ``ActionBias`` a flat table of them.  What was
missing is the way in and out: an optimizer runs *outside* the process that
plays the match, so a weight vector has to survive a trip through a file
and a command line.

    weights = load_weights("candidate.json")
    bot = SearchBot(weights=weights)

Absolute, not relative
----------------------
§51's ``weights_from_tilt`` / ``ActionBiasSet`` also build weight objects,
but they take *multipliers* and clamp them — deliberately, because a play
style that switched a whole evaluation category off would stop playing the
game (see §51's guardrails).  Tuning is the opposite problem: an optimizer
has to be free to propose the unreasonable, and clamping its proposals
silently would mean measuring a different candidate than the one that was
scored.  So these read absolute values and validate names only.

A file may be partial.  Anything it does not mention keeps its default, so
a search over three weights is a three-line file rather than a copy of the
whole table that goes stale the next time a weight is added.
"""

from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path
from typing import Any

from game.ai.evaluation import DEFAULT_WEIGHTS, EvalWeights
from game.ai.heuristic_bot import ActionBias

#: Every tunable evaluation weight, by name.
WEIGHT_FIELDS: frozenset[str] = frozenset(f.name for f in fields(EvalWeights))

#: The ``EvalWeights`` fields that are square counts, not scores.
_INT_FIELDS: frozenset[str] = frozenset(
    f.name for f in fields(EvalWeights) if f.type in ("int", int)
)

#: Every tunable action bias, by name.
BIAS_FIELDS: frozenset[str] = frozenset(n for n in dir(ActionBias) if n.isupper())


# ─────────────────────────────────────────────────────────────────────────────
# EvalWeights ⇄ dict
# ─────────────────────────────────────────────────────────────────────────────

def weights_to_dict(weights: EvalWeights = DEFAULT_WEIGHTS) -> dict[str, float]:
    """Every evaluation weight, by name — the shape ``weights_from_dict`` reads."""
    return {f.name: getattr(weights, f.name) for f in fields(weights)}


def weights_from_dict(
    values: dict[str, Any],
    base: EvalWeights = DEFAULT_WEIGHTS,
) -> EvalWeights:
    """
    Build ``EvalWeights`` from absolute values, falling back to ``base``.

    Unknown names raise rather than being ignored: a typo in a candidate
    vector would otherwise be scored as "that weight does nothing", and the
    optimizer would learn from a measurement of the wrong thing.
    """
    unknown = set(values) - WEIGHT_FIELDS
    if unknown:
        raise ValueError(
            f"unknown EvalWeights field(s): {', '.join(sorted(unknown))}"
        )
    resolved: dict[str, Any] = {}
    for name in WEIGHT_FIELDS:
        value = values.get(name, getattr(base, name))
        resolved[name] = int(round(value)) if name in _INT_FIELDS else float(value)
    return EvalWeights(**resolved)


# ─────────────────────────────────────────────────────────────────────────────
# ActionBias ⇄ dict
# ─────────────────────────────────────────────────────────────────────────────

class BiasTable:
    """
    A plain, mutable stand-in for the ``ActionBias`` class.

    The bots read biases as ``self._bias.SUMMON``, so anything carrying the
    same upper-case attributes drops straight in.  Unlike §51's
    ``ActionBiasSet`` this applies no clamps and no staple floors — see the
    module docstring.
    """

    def __init__(self, values: "dict[str, Any] | None" = None) -> None:
        values = values or {}
        unknown = set(values) - BIAS_FIELDS
        if unknown:
            raise ValueError(
                f"unknown ActionBias field(s): {', '.join(sorted(unknown))}"
            )
        for name in BIAS_FIELDS:
            setattr(self, name, float(values.get(name, getattr(ActionBias, name))))

    def as_dict(self) -> dict[str, float]:
        return {k: v for k, v in vars(self).items() if k.isupper()}

    def __repr__(self) -> str:  # pragma: no cover
        return f"BiasTable({self.as_dict()})"


def biases_to_dict(biases: "type[ActionBias] | ActionBias | BiasTable" = ActionBias) -> dict[str, float]:
    """Every action bias, by name."""
    return {name: float(getattr(biases, name)) for name in BIAS_FIELDS}


def biases_from_dict(values: dict[str, Any]) -> BiasTable:
    """Build a bias table from absolute values, falling back to the defaults."""
    return BiasTable(values)


# ─────────────────────────────────────────────────────────────────────────────
# Files
# ─────────────────────────────────────────────────────────────────────────────

def load_weights(path: "str | Path") -> tuple[EvalWeights, "BiasTable | None"]:
    """
    Read a weight file.

    Two accepted shapes — a flat object of ``EvalWeights`` values::

        {"material": 1.2, "ritual_progress": 0.4}

    or one that also carries an action-bias table::

        {"weights": {"material": 1.2}, "biases": {"SUMMON": 3.0}}

    Returns ``(weights, biases)``; ``biases`` is None for the flat form, so
    the caller leaves the bot's default table alone.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object")

    if "weights" in data or "biases" in data:
        weights = weights_from_dict(data.get("weights") or {})
        biases = biases_from_dict(data["biases"]) if data.get("biases") else None
        return weights, biases

    return weights_from_dict(data), None


def save_weights(
    weights: EvalWeights,
    path: "str | Path",
    biases: "type[ActionBias] | ActionBias | BiasTable | None" = None,
) -> None:
    """Write a weight file in the two-section form ``load_weights`` reads."""
    payload: dict[str, Any] = {"weights": weights_to_dict(weights)}
    if biases is not None:
        payload["biases"] = biases_to_dict(biases)
    Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
