"""
mechanics/effects/combat.py — COMBAT category
==============================================

Effect types in this category
------------------------------
damage_unit    IMPLEMENTED — siphon_of_power (one named enemy) and
                dread_tide (every enemy in the resolved area). See
                "What one point of damage means" below.
spawn_piece    IMPLEMENTED — puts a NEW piece on the board:
                bone_marauder's Skeleton Pawn when it is destroyed, and
                illusory_doubles' short-lived copy of an allied unit.

What one point of damage means
-------------------------------
Units in this engine have no hit-point pool — a piece is on the board or
it isn't. But ``capture_protection`` (``shield:N``) is already a "survive
one hostile action" counter, and siphon_of_power's own text — "Deal 1
damage to an enemy unit. If it survives, you draw 1 card" — only means
anything if some units survive a point of damage. So the shield IS the
health bar:

    one point of damage  ==  one hostile action

which ``mechanics.monsters.apply_capture_protection()`` already resolves:
a shield charge is spent and the unit survives, or there is no shield and
the unit is destroyed. Everything that already bends capture protection
therefore bends damage too, for free — ``exposed:`` (pit_trap,
counter_strike) strips the save, and a suppressed Monster
(monster_seal / nullification_glyph) cannot use its shield either.

Kings are never damaged, matching the blanket King immunity Traps and
displacement Spells already follow: reaching the King is the Final Duel's
business, not a hazard's.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from game.chess.pieces import Position, UnitInstance
    from game.mechanics.effects.registry import EffectContext


# ---------------------------------------------------------------------------
# damage_unit
# ---------------------------------------------------------------------------

def _destroy_unit(ctx: "EffectContext", pos: "Position", unit: "UnitInstance") -> None:
    """Remove ``unit`` from the board and report it the way a capture does."""
    from game.core.events import MonsterDestroyed, PieceCaptured

    removed = ctx.state.board.remove_unit(pos)
    if removed is None:
        return
    ctx.state.get_player(ctx.state.opponent_of(removed.owner)).captured_pieces.append(
        removed.piece.id
    )
    if removed.monster_id is not None:
        ctx.events.append(MonsterDestroyed(
            player_id=removed.owner,
            card_id=removed.monster_id,
            vessel_piece_id=removed.piece.id,
            position=pos,
            destroyed_by_piece_id=None,
        ))
    ctx.events.append(PieceCaptured(
        piece_id=removed.piece.id,
        owner=removed.owner,
        captured_at=pos,
        captured_by_piece_id=None,
    ))
    # Any forced removal of a committed Builder loses the half-built
    # Building, exactly as a capture or a shove would.
    from game.mechanics.buildings import cancel_construction, find_committed_building

    disrupted = find_committed_building(ctx.state, removed.piece.id)
    if disrupted is not None:
        cancel_construction(ctx.state, disrupted, ctx.events, destroyed_by=None)


def _push_away(ctx: "EffectContext", pos: "Position", unit: "UnitInstance",
               centre: "Position", distance: int) -> None:
    """Shove ``unit`` ``distance`` squares directly away from ``centre``."""
    from game.chess.movement import blocks_movement
    from game.chess.pieces import Position as _Pos
    from game.core.events import PiecePushed

    step_f = (pos.file > centre.file) - (pos.file < centre.file)
    step_r = (pos.rank > centre.rank) - (pos.rank < centre.rank)
    if step_f == 0 and step_r == 0:
        return   # standing on the centre — no "away" direction exists
    nf, nr = pos.file + step_f * distance, pos.rank + step_r * distance
    if not (0 <= nf <= 7 and 0 <= nr <= 7):
        return
    dest = _Pos(nf, nr)
    if ctx.state.board.get_unit(dest) is not None:
        return
    if blocks_movement(ctx.state.board, dest, unit.owner):
        return
    ctx.state.board.move_unit(pos, dest)
    ctx.events.append(PiecePushed(
        piece_id=unit.piece.id, owner=unit.owner,
        source=pos, target=dest, pushed_by_piece_id=None,
    ))


def _damage_unit(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — siphon_of_power and dread_tide.

    Two dispatch shapes, both landing on the same resolution:

    1. target_type "piece" (siphon_of_power): ``ctx.unit`` is the enemy
       piece, already ownership-checked by _resolve_spell_on_piece.
    2. target_type "position" (dread_tide): _resolve_spell_on_area calls
       this once per square of the 5x5, so ``ctx.unit`` is None and the
       victim (if any) is whoever stands on ``ctx.position``.

    ``amount`` points of damage are applied one at a time, each resolved
    as a hostile action through ``apply_capture_protection`` — see the
    module docstring for why a shield charge is the health bar. A unit
    that survives every point triggers ``draw_if_survives`` cards for the
    caster (siphon_of_power's consolation) and, for
    ``push_from_center``, is shoved that many squares away from the
    Spell's anchor square (dread_tide).
    """
    from game.core.phases import PieceType
    from game.mechanics.monsters import apply_capture_protection

    if ctx.state is None:
        return

    params = ctx.effect.params
    caster = (ctx.extra or {}).get("caster_owner")

    unit = ctx.unit
    pos = ctx.position
    if unit is None:
        if pos is None:
            return
        unit = ctx.state.board.get_unit(pos)
        if unit is None:
            return

    if pos is None:
        return
    if unit.piece.piece_type == PieceType.KING:
        return   # Kings are never damaged — see the module docstring.

    if params.get("target_owner") == "opponent" and caster is not None:
        if unit.owner == caster:
            return   # never friendly fire, even inside your own blast

    # One unit, one hit per cast. _resolve_spell_on_area sweeps the area
    # square by square; a unit this effect PUSHES out of one square can land
    # on another square the sweep has not reached yet, and would otherwise be
    # damaged and pushed all over again. The shared per-cast extra dict is
    # what makes remembering it possible.
    if ctx.extra is not None:
        already = ctx.extra.setdefault("_damage_unit_resolved", set())
        if unit.piece.id in already:
            return
        already.add(unit.piece.id)

    amount = params.get("amount", 1)
    survived = True
    for _ in range(amount):
        if not apply_capture_protection(unit):
            _destroy_unit(ctx, pos, unit)
            survived = False
            break

    if not survived:
        return

    push = params.get("push_from_center", 0)
    if push:
        centre_xy = (ctx.extra or {}).get("area_center")
        centre = None
        if centre_xy is not None:
            from game.chess.pieces import Position as _Pos

            centre = _Pos(centre_xy[0], centre_xy[1])
        if centre is not None:
            _push_away(ctx, pos, unit, centre, push)

    draw = params.get("draw_if_survives", 0)
    if draw and caster is not None:
        from game.core.events import CardDrawn, DeckRecycled

        ps = ctx.state.get_player(caster)
        for _ in range(draw):
            if not ps.deck and ps.graveyard:
                ps.deck = list(ps.graveyard)
                ps.graveyard.clear()
                if ctx.rng is not None:
                    ctx.rng.shuffle(ps.deck)
                ctx.events.append(DeckRecycled(player_id=caster, card_count=len(ps.deck)))
            if ps.deck:
                card_id = ps.deck.pop(0)
                ps.hand.append(card_id)
                ctx.events.append(CardDrawn(player_id=caster, card_id=card_id))


# ---------------------------------------------------------------------------
# spawn_piece
# ---------------------------------------------------------------------------

def _next_token_id(state, owner: str, label: str) -> str:
    """A unique piece id for a spawned token, e.g. ``white-skeleton-3``."""
    prefix = f"{owner}-{label}-"
    existing = sum(
        1 for _pos, unit in state.board.all_units()
        if unit.piece.id.startswith(prefix)
    )
    return f"{prefix}{existing + 1}"


def _first_empty_adjacent(state, origin: "Position", owner: str) -> "Position | None":
    from game.chess.movement import blocks_movement
    from game.chess.pieces import Position as _Pos

    for df in (-1, 0, 1):
        for dr in (-1, 0, 1):
            if df == 0 and dr == 0:
                continue
            nf, nr = origin.file + df, origin.rank + dr
            if not (0 <= nf <= 7 and 0 <= nr <= 7):
                continue
            cand = _Pos(nf, nr)
            if state.board.get_unit(cand) is not None:
                continue
            if blocks_movement(state.board, cand, owner):
                continue
            return cand
    return None


def _spawn_piece(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — bone_marauder and illusory_doubles.

    Places a brand-new piece on the board, the same way
    RulesEngine._execute_place_mercenary_piece does for a Mercenary: build
    a ChessPiece with a generated id and drop a UnitInstance on an empty
    square. Two callers, distinguished by ``trigger``:

    1. ``trigger: on_destroyed`` (bone_marauder) — dispatched by
       RulesEngine._apply_death_triggered_effects from the card of the
       unit that just died, with ``ctx.position`` set to where it fell.
       ``piece_type`` names what to leave behind (a Skeleton Pawn).
    2. no trigger (illusory_doubles, a Spell on an allied piece) —
       ``copy_of_target: true`` makes the spawn mirror ``ctx.unit``'s own
       piece type.

    ``placement: adjacent`` picks the first empty square around the
    origin. Spawned pieces are marked with two statuses that nothing else
    in the game hands out:

        token:<label>       cosmetic provenance, for the UI and the log
        no_capture          this piece may move but never capture
                            ("Illusions cannot capture") — read in
                            chess.movement.get_pseudo_legal_moves
        expires:<N>         removed from the board at the owner's own
                            EndTurn once the counter runs out ("Lasts 2
                            turns") — ticked in RulesEngine._execute_end_turn

    A token carries no shield, so "have 1 Health" falls out for free: the
    first hostile action that reaches it destroys it (see this module's
    docstring on what damage means).
    """
    from game.chess.pieces import ChessPiece, Position, UnitInstance
    from game.core.events import PieceSpawned
    from game.core.phases import PieceType

    if ctx.state is None or ctx.position is None:
        return

    params = ctx.effect.params
    trigger = params.get("trigger", "")
    if trigger == "on_destroyed" and ctx.trigger != "on_destroyed":
        return
    if trigger != "on_destroyed" and ctx.trigger == "on_destroyed":
        return

    owner = None
    if ctx.unit is not None:
        owner = ctx.unit.owner
    if owner is None:
        owner = (ctx.extra or {}).get("caster_owner")
    if owner is None:
        return

    if params.get("copy_of_target") and ctx.unit is not None:
        piece_type = ctx.unit.piece.piece_type
    else:
        piece_type = PieceType(params.get("piece_type", "pawn"))
    if piece_type == PieceType.KING:
        return   # never spawn a second King

    label = params.get("label", "token")
    count = params.get("count", 1)
    for _ in range(count):
        dest = _first_empty_adjacent(ctx.state, ctx.position, owner)
        if dest is None:
            return
        piece = ChessPiece(
            id=_next_token_id(ctx.state, owner, label),
            owner=owner,
            piece_type=piece_type,
        )
        unit = UnitInstance(piece=piece)
        unit.add_status(f"token:{label}")
        if params.get("cannot_capture"):
            unit.add_status("no_capture")
        duration = params.get("duration_turns")
        if duration:
            unit.add_status(f"expires:{duration}")
        ctx.state.board.place_unit(dest, unit)
        ctx.events.append(PieceSpawned(
            player_id=owner,
            piece_id=piece.id,
            piece_type=piece_type.value,
            position=dest,
            label=label,
        ))


COMBAT_HANDLERS: dict[str, object] = {
    "damage_unit": _damage_unit,
    "spawn_piece": _spawn_piece,
}
