"""
mechanics/effects/defense.py — DEFENSE category
================================================

Effect types in this category
------------------------------
capture_protection  IMPLEMENTED  — absorb N capture attempts (shield:N status)
trap_immunity       IMPLEMENTED  — ignore the first Trap effect that would
                                    affect this unit (ancient_tortoise); the
                                    flag is set here, consumed in
                                    mechanics.monsters._fire_trap() via
                                    _consume_trap_immunity()
intercept           IMPLEMENTED  — an escort adjacent to the King absorbs one
                                    assault on it in the King's place
                                    (royal_guard); the charge is armed here
                                    and spent in rules.py via
                                    mechanics.monsters.find_interceptor()
retaliate           IMPLEMENTED  — destroy attacker when this unit is captured
                                    (thorn_boar; consumed in rules.py's
                                    _execute_move_piece right after the
                                    capture is resolved)
capture_vulnerability IMPLEMENTED — Stage 6: "Exposed" — bypasses
                                    capture_protection for a few turns
                                    (pit_trap, counter_strike)
mark_unit           IMPLEMENTED  — vengeance_mark's "Vulnerable" status;
                                    mechanically identical to
                                    capture_vulnerability's "Exposed" (arms
                                    "exposed:N" on the capturing piece), so
                                    it reuses the same handler rather than
                                    introducing a parallel status nothing
                                    else would ever read
remove_negative_effects IMPLEMENTED — purify: strips every hostile status
                                    from an allied unit and, with
                                    ``include_square``, the hostile zone tags
                                    standing on its square
cancel_capture      IMPLEMENTED  — guardian_sigils; the real logic runs
                                    PRE-EMPTIVELY in
                                    mechanics.monsters.find_cancel_capture_trap(),
                                    called from _execute_move_piece
                                    alongside apply_capture_protection,
                                    BEFORE the capture happens — there's no
                                    "after the capture" moment to react to,
                                    so this entry is a no-op placeholder
                                    only so the type is recognised
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from game.mechanics.effects.registry import EffectContext


# ---------------------------------------------------------------------------
# capture_protection
# ---------------------------------------------------------------------------

def _capture_protection(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — absorbs the first N capture attempts (arcane_sentinel,
    stone_golem, ancient_tortoise).

    Applies ``shield:N`` status to the unit at summon time.
    The RulesEngine checks and consumes shields in _execute_move_piece().

    ``stacking: add`` (arcane_fortitude, a Spell — "Give an allied unit +1
    Capture Protection") ADDS to whatever the unit already holds instead of
    replacing it. A Monster's own on-summon capture_protection keeps the
    default replace behaviour, so re-arming it can never stack a shield
    higher than the card promises.
    """
    if ctx.unit is None:
        return
    params = ctx.effect.params
    trigger = params.get("trigger", "on_summon")
    if trigger not in ("on_summon", "passive", "", "instant_spell"):
        return

    uses = params.get("uses", 1)
    existing = 0
    for status in ctx.unit.statuses:
        if status.startswith("shield:"):
            existing = int(status.split(":")[1])
            break
    total = existing + uses if params.get("stacking") == "add" else uses
    ctx.unit.statuses = [s for s in ctx.unit.statuses if not s.startswith("shield:")]
    ctx.unit.add_status(f"shield:{total}")


# ---------------------------------------------------------------------------
# trap_immunity
# ---------------------------------------------------------------------------

def _trap_immunity(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — ancient_tortoise. Sets ``trap_immune:N`` at summon;
    mechanics.monsters._fire_trap() spends a charge instead of applying the
    Trap's effects (the Trap itself still triggers and still burns a charge
    of its own).
    """
    if ctx.unit is None:
        return
    trigger = ctx.effect.params.get("trigger", "on_summon")
    if trigger not in ("on_summon", "passive", ""):
        return

    uses = ctx.effect.params.get("uses", 1)
    ctx.unit.statuses = [s for s in ctx.unit.statuses if not s.startswith("trap_immune:")]
    ctx.unit.add_status(f"trap_immune:{uses}")


# ---------------------------------------------------------------------------
# intercept
# ---------------------------------------------------------------------------

def _intercept(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — royal_guard ("can intercept one attack targeting an
    adjacent King").

    Arms ``intercept:<uses>`` at summon time — ``adjacent_king_targeted``
    is whitelisted in apply_on_summon_effects for exactly this reason (same
    shape as retaliate's ``capture_attempt``): the charge is banked now and
    spent only if an assault on the escorted King actually arrives.

    Read side: mechanics.monsters.find_interceptor(), called from
    RulesEngine._execute_move_piece BEFORE the board is mutated (alongside
    apply_capture_protection / find_cancel_capture_trap). When it matches,
    the escort is destroyed in the King's place, the attacker is bounced
    back to its source square, and no Final Duel is triggered.
    """
    if ctx.unit is None:
        return
    uses = ctx.effect.params.get("uses", 1)
    ctx.unit.statuses = [s for s in ctx.unit.statuses if not s.startswith("intercept:")]
    ctx.unit.add_status(f"intercept:{uses}")


# ---------------------------------------------------------------------------
# retaliate
# ---------------------------------------------------------------------------

def _retaliate(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — thorn_boar. Arms the ``retaliate`` status at summon;
    mechanics.monsters.apply_retaliate(), called from
    RulesEngine._execute_move_piece right after the capture resolves,
    destroys the attacker in turn.
    """
    if ctx.unit is None:
        return
    trigger = ctx.effect.params.get("trigger", "capture_attempt")
    if trigger not in ("capture_attempt", "on_summon", "passive", ""):
        return

    ctx.unit.add_status("retaliate")


# ---------------------------------------------------------------------------
# capture_vulnerability  (Stage 6 — pit_trap, counter_strike)
# ---------------------------------------------------------------------------

def _capture_vulnerability(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — Stage 6 Trap effect ("Exposed").

    Flags the target as "exposed:<duration_turns>" — while exposed, its
    capture_protection shield (if any) does NOT block an incoming capture;
    see mechanics.monsters.apply_capture_protection(), which checks this
    status before consuming a shield charge.  Decays on the unit OWNER's
    own EndTurn, same as immobilize_piece.
    """
    if ctx.unit is None:
        return
    duration = ctx.effect.params.get("duration_turns", 1)
    ctx.unit.statuses = [s for s in ctx.unit.statuses if not s.startswith("exposed:")]
    ctx.unit.add_status(f"exposed:{duration}")


#: Unit statuses purify clears. Everything here is something an OPPONENT
#: (or a hostile zone) inflicted — never one of the unit's own banked
#: passives like ``shield:``, ``stealth`` or ``ritual_substitute``, which a
#: cleanse has no business stripping from its own side.
_NEGATIVE_STATUS_PREFIXES: tuple[str, ...] = (
    "immobilized:",
    "exposed:",
    "effects_suppressed:",
    "challenged_by:",
    "movement_limit:",
    "duel_marked:",
)

#: Square tags purify clears from the target's own square. Deliberately
#: excludes ``temp_territory:`` and ``no_summon:`` — those are placement
#: rules rather than something afflicting the unit standing there.
_NEGATIVE_SQUARE_PREFIXES: tuple[str, ...] = (
    "frozen:", "scorched:", "blocked:", "cursed:", "walled:", "cost_zone:", "sealed:",
)


def _remove_negative_effects(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — purify ("Remove all negative effects, curses, and Trap
    auras from target allied unit or zone.").

    The card names "unit or zone" as its target; this engine has no
    target_type that means "either", so the Spell targets a PIECE and
    ``include_square: true`` extends the cleanse to the zone that piece is
    standing in — which is the case the card is actually for (a unit
    trapped in a cursed or frozen square).

    Note what it deliberately does NOT touch: the unit's own
    ``shield:``/``stealth``/aura statuses. Those are the target's own
    banked passives, not an affliction, and stripping them would make
    purify a liability on your own board.
    """
    if ctx.unit is None or ctx.state is None:
        return

    ctx.unit.statuses = [
        s for s in ctx.unit.statuses
        if not s.startswith(_NEGATIVE_STATUS_PREFIXES)
    ]

    if ctx.effect.params.get("include_square") and ctx.position is not None:
        square = ctx.state.board.get_square(ctx.position)
        square.temporary_effects = [
            e for e in square.temporary_effects
            if not e.startswith(_NEGATIVE_SQUARE_PREFIXES)
        ]


def _cancel_capture(ctx: "EffectContext") -> None:
    """No-op placeholder — see module docstring. Real logic lives in
    mechanics.monsters.find_cancel_capture_trap()."""
    pass


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

DEFENSE_HANDLERS: dict[str, object] = {
    "capture_protection":    _capture_protection,
    "trap_immunity":         _trap_immunity,
    "intercept":             _intercept,
    "retaliate":             _retaliate,
    "capture_vulnerability": _capture_vulnerability,
    "mark_unit":             _capture_vulnerability,
    "cancel_capture":        _cancel_capture,
    "remove_negative_effects": _remove_negative_effects,
}
