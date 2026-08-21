"""
mechanics/effects/_meta.py — Miscellaneous / multi-category effects
====================================================================

These effect types appear in monsters.yaml but don't belong cleanly to a
single primary category, or they span multiple systems not yet implemented.

Effect types handled here
--------------------------
copy_effect                 IMPLEMENTED — copy adjacent monster effect (mirror_magus)
vessel_support               IMPLEMENTED — allow extra vessel types in radius
                                  (broodmother; aura scan lives in
                                  mechanics.monsters.get_extra_vessel_types(),
                                  wired into SummonMonster legal-action
                                  generation + validation)
ignore_terrain               ARMED, DORMANT — no Building/Territory movement
                                  restriction exists anywhere in this engine
                                  to ignore; flag is armed for a future one
building_damage_bonus       ARMED, DORMANT — no action lets a piece attack a
                                  COMPLETE Building (README §11: "do not
                                  normally capture"); flag armed for later
building_capture_protection ARMED, DORMANT — same blocker as building_damage_bonus
weakened_target_bonus       IMPLEMENTED — bypasses an adjacent retaliate
                                  effect ONLY when the retaliating unit's own
                                  capture_protection was already spent; see
                                  mechanics.monsters.apply_retaliate()
restore_effect_charge       IMPLEMENTED — restore a spent effect charge to an
                                  adjacent ally at end of the owner's own turn
                                  (battlefield_medic; resolved in
                                  _execute_end_turn)
challenge_unit               IMPLEMENTED — restrict an adjacent enemy to
                                  fight only the duelist for duration_turns;
                                  enforced in chess.movement.get_pseudo_legal_moves
territory_bonus              IMPLEMENTED — extra leap mobility while standing
                                  in enemy Territory (moon_stalker); read by
                                  mechanics.monsters.get_movement_additions()
graveyard_inspect            IMPLEMENTED — private GraveyardInspected event
                                  revealing the owner's own Graveyard (grave_scholar)
graveyard_counter            IMPLEMENTED — passive counter from destroyed
                                  allies; incremented centrally in
                                  RulesEngine._apply_death_triggered_effects
capture_protection_from_counter IMPLEMENTED — shield charges from the
                                  graveyard_counter threshold (same central hook)
dismiss_monster              IMPLEMENTED — activated dismiss of an ADJACENT
                                  ally to the Graveyard (vessel_reclaimer;
                                  distinct from the player's own DismissMonster
                                  action, which targets self and returns to hand)
graveyard_scaling_movement  IMPLEMENTED — leap bonus once the owner's
                                  Graveyard reaches a threshold (crypt_walker);
                                  read by get_movement_additions()
death_trigger_draw          IMPLEMENTED — draw once per turn on an allied
                                  Monster's death; same central hook as
                                  graveyard_counter
sacrifice_bonus              IMPLEMENTED — extra Ritual progress when this
                                  unit is a pure Ritual sacrifice (blood_seer);
                                  consumed in mechanics.rituals.execute_ritual()
ritual_activation_range     IMPLEMENTED — extends Formation Ritual node
                                  matching by radius_bonus (herald_of_the_gate);
                                  consumed in mechanics.rituals pattern matcher
restore_builder              IMPLEMENTED — on summon, restore the Builder
                                  token to one adjacent Pawn that already
                                  spent it (guild_foreman)
capture_then_retreat        IMPLEMENTED — post-capture retreat away from the
                                  enemy King (dusk_reaver; reuses the
                                  REPOSITION PendingDecision machinery)
spell_radius_bonus          IMPLEMENTED (flag only)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from game.logger import get_logger

_log = get_logger(__name__)

if TYPE_CHECKING:
    from game.mechanics.effects.registry import EffectContext


def _spell_radius_bonus(ctx: "EffectContext") -> None:
    """IMPLEMENTED — passive flag applied at summon time (dark_magician)."""
    if ctx.unit is None:
        return
    bonus = ctx.effect.params.get("bonus", 0)
    ctx.unit.statuses = [s for s in ctx.unit.statuses if not s.startswith("spell_radius_bonus:")]
    ctx.unit.add_status(f"spell_radius_bonus:{bonus}")


# Effect types that are safe to copy (passive — no on-activation side effects).
# Excludes effects that require their own separate activation logic.
_COPYABLE_PASSIVE_TYPES: frozenset[str] = frozenset({
    "spell_radius_bonus",
    "alter_movement",
    "capture_protection",
    "damage_aura",
    "ignore_terrain",
    "building_damage_bonus",
    "weakened_target_bonus",
    "territory_bonus",
    "graveyard_counter",
    "graveyard_scaling_movement",
    "death_trigger_draw",
    "sacrifice_bonus",
    "ritual_activation_range",
})


def _copy_effect(ctx: "EffectContext") -> None:
    """Copy one copyable passive effect from an adjacent allied monster.

    Scans the 8 squares around the caster for an allied unit that carries
    at least one effect type in _COPYABLE_PASSIVE_TYPES.  The first such
    effect found is stored as ``copied_effect:<type>`` in the caster's
    statuses.  The status is visible in the right-click info panel.

    Raises IllegalActionError when:
      • ctx.unit or ctx.position is missing
      • No adjacent allied monster with a copyable effect exists
      • ctx.registry is unavailable
    """
    from game.cards.card import MonsterCard
    from game.core.rules import IllegalActionError

    if ctx.unit is None or ctx.position is None:
        raise IllegalActionError("copy_effect: missing unit or position.")
    if ctx.registry is None:
        raise IllegalActionError("copy_effect: no card registry available.")
    if ctx.state is None:
        raise IllegalActionError("copy_effect: no game state.")

    pos = ctx.position
    owner = ctx.unit.owner
    caster_id = ctx.unit.piece.id

    _log.debug("COPY     caster=%s  pos=%s  owner=%s  scanning neighbours...",
               caster_id, pos, owner)

    # Scan the 8 neighbouring squares
    found_type: str | None = None
    found_from: str | None = None
    for df in (-1, 0, 1):
        for dr in (-1, 0, 1):
            if df == 0 and dr == 0:
                continue
            nf, nr = pos.file + df, pos.rank + dr
            if not (0 <= nf <= 7 and 0 <= nr <= 7):
                continue
            from game.chess.pieces import Position as _Pos
            neighbour_pos = _Pos(nf, nr)
            neighbour_unit = ctx.state.board.get_unit(neighbour_pos)
            if neighbour_unit is None:
                _log.debug("COPY       [%d,%d] empty", nf, nr)
                continue
            if neighbour_unit.owner != owner:
                _log.debug("COPY       [%d,%d] %s — wrong owner (%s)",
                           nf, nr, neighbour_unit.piece.id, neighbour_unit.owner)
                continue
            if neighbour_unit.monster_id is None:
                _log.debug("COPY       [%d,%d] %s — no monster",
                           nf, nr, neighbour_unit.piece.id)
                continue
            try:
                neighbour_card = ctx.registry.get(neighbour_unit.monster_id)
            except KeyError:
                _log.debug("COPY       [%d,%d] monster_id=%r not in registry",
                           nf, nr, neighbour_unit.monster_id)
                continue
            if not isinstance(neighbour_card, MonsterCard):
                continue
            _log.debug("COPY       [%d,%d] %s (monster=%s) effects=%s",
                       nf, nr, neighbour_unit.piece.id, neighbour_unit.monster_id,
                       [e.type for e in neighbour_card.effects])
            for eff in neighbour_card.effects:
                if eff.type in _COPYABLE_PASSIVE_TYPES:
                    found_type = eff.type
                    found_from = neighbour_unit.piece.id
                    _log.debug("COPY       found copyable effect %r from %s",
                               found_type, found_from)
                    break
        if found_type:
            break

    if found_type is None:
        _log.warning("COPY     FAILED  caster=%s — no adjacent ally with copyable effect", caster_id)
        raise IllegalActionError(
            "No adjacent allied monster with a copyable passive effect found."
        )

    # Remove any previous copied_effect status, then add the new one
    ctx.unit.statuses = [
        s for s in ctx.unit.statuses if not s.startswith("copied_effect:")
    ]
    ctx.unit.add_status(f"copied_effect:{found_type}")
    _log.info("COPY     OK  caster=%s  copied=%r  from=%s  statuses=%s",
              caster_id, found_type, found_from, ctx.unit.statuses)


def _vessel_support(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — broodmother (real logic lives elsewhere).

    This is a passive positional aura: it doesn't change anything about
    broodmother's own unit, it extends the set of legal vessel piece types
    for OTHER summons happening nearby.  That can't be expressed as a status
    flag resolved once at summon time — it has to be re-checked every time a
    SummonMonster action is validated or enumerated.

    The actual scan is `mechanics.monsters.get_extra_vessel_types()`, called
    from both legal-action generation and `_execute_summon_monster()` in
    rules.py.  This handler is a no-op placeholder so the effect type is
    recognised by the registry and never logged as unresolved.
    """
    # Real implementation: mechanics.monsters.get_extra_vessel_types()
    pass


def _ignore_terrain(ctx: "EffectContext") -> None:
    """
    ARMED, DORMANT — sky_serpent. Nothing in this engine currently makes
    Buildings or Territory restrict movement (no such restriction exists
    to ignore — grep confirms chess.movement never reads ``building_id``
    and Territory only ever gates SummonMonster/Trap/Spell placement, not
    movement). The status is armed so a future movement restriction can
    consult it, but today it has no observable effect. Flagged to the
    user rather than inventing a new movement-restriction mechanic
    unprompted.
    """
    if ctx.unit is None:
        return
    params = ctx.effect.params
    if params.get("buildings"):
        ctx.unit.add_status("ignore_building_terrain")


def _building_damage_bonus(ctx: "EffectContext") -> None:
    """
    ARMED, DORMANT — obsidian_dragon. README §11 says Buildings "do not
    normally capture", and indeed no action in this engine lets a piece
    attack/capture a COMPLETE Building (the only destruction paths are
    construction-disruption — capturing the committed Builder Pawn — and
    the King Succession sacrifice, neither of which is "this monster
    capturing a Building"). The status is armed so a future
    attack-a-building action can consult it, but today it has no
    observable effect.
    """
    if ctx.unit is None:
        return
    ctx.unit.add_status("building_destroyer")


def _building_capture_protection(ctx: "EffectContext") -> None:
    """
    ARMED, DORMANT — castle_keeper. Same blocker as building_damage_bonus:
    there is no "attempt to destroy a Building" action to protect against
    today. Arms a status (rather than raising) so summoning castle_keeper
    no longer crashes; still a no-op until a building-attack mechanic
    exists.
    """
    if ctx.unit is None:
        return
    radius = ctx.effect.params.get("radius", 1)
    uses = ctx.effect.params.get("uses", 1)
    ctx.unit.statuses = [
        s for s in ctx.unit.statuses if not s.startswith("building_capture_protection:")
    ]
    ctx.unit.add_status(f"building_capture_protection:{radius}:{uses}")


def _weakened_target_bonus(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — executioner.

    Arms ``guaranteed_capture_vs_no_shield`` on summon. Consumed in
    ``mechanics.monsters.apply_retaliate()``, which now also checks the
    card's own condition ("capture_protection_remaining: 0") — the
    bypass only fires when the retaliating unit had no capture_protection
    shield left at the moment it was captured, matching "Excels against
    units whose defensive effects have already been spent" instead of
    unconditionally voiding every retaliate.
    """
    if ctx.unit is None:
        return
    ctx.unit.add_status("guaranteed_capture_vs_no_shield")


def _restore_effect_charge(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — battlefield_medic (real logic lives elsewhere).

    This only fires at end_of_turn, scanning the board for adjacency —
    not something a summon-time status flag can express.  The actual
    restoration is ``RulesEngine._resolve_restore_effect_charge()``, called
    from ``_execute_end_turn`` for the ending player's own units.  This
    handler is a no-op placeholder so the type is recognised by the
    registry and never logged as unresolved.
    """
    # Real implementation: RulesEngine._resolve_restore_effect_charge()
    pass


def _challenge_unit(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — duelist activated ability.

    ``ctx.extra["target"]`` is an (file, rank) tuple naming an adjacent
    enemy unit (enumerated by RulesEngine.get_legal_actions — one
    ActivateMonsterAbility per adjacent enemy). Tags the TARGET (not the
    duelist) with ``challenged_by:<duelist_piece_id>:<duration_turns>``.

    Enforcement lives in chess.movement.get_pseudo_legal_moves():
      • the challenged unit's own captures are restricted to the duelist,
      • every OTHER unit's captures against the challenged unit are
        illegal — only the duelist may capture it.
    Decays on the challenged unit OWNER's own EndTurn (mirrors
    immobilized/exposed — "until the next turn").
    """
    from game.chess.pieces import Position
    from game.core.rules import IllegalActionError

    if ctx.unit is None or ctx.position is None or ctx.state is None:
        return
    target = (ctx.extra or {}).get("target")
    if target is None:
        raise IllegalActionError("challenge_unit requires an adjacent enemy target.")
    tf, tr = target
    if max(abs(tf - ctx.position.file), abs(tr - ctx.position.rank)) != 1:
        raise IllegalActionError("challenge_unit target must be adjacent.")
    target_pos = Position(tf, tr)
    target_unit = ctx.state.board.get_unit(target_pos)
    if target_unit is None or target_unit.owner == ctx.unit.owner:
        raise IllegalActionError("challenge_unit target must be an adjacent enemy unit.")

    duration = ctx.effect.params.get("duration_turns", 1)
    target_unit.statuses = [
        s for s in target_unit.statuses if not s.startswith("challenged_by:")
    ]
    target_unit.add_status(f"challenged_by:{ctx.unit.piece.id}:{duration}")


def _territory_bonus(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — moon_stalker. Arms ``territory_movement_bonus:N``, read
    by ``mechanics.monsters.get_movement_additions()`` (called from
    chess.movement.get_pseudo_legal_moves with the GameState/registry it
    needs to test ``territory.is_in_territory`` against the OPPONENT's
    Territory) — adds N cardinal-direction leap squares, same shape as
    alter_movement's add_leap, while the unit currently stands inside
    enemy-controlled Territory.
    """
    if ctx.unit is None:
        return
    bonus = ctx.effect.params.get("movement_bonus", 1)
    ctx.unit.statuses = [
        s for s in ctx.unit.statuses if not s.startswith("territory_movement_bonus:")
    ]
    ctx.unit.add_status(f"territory_movement_bonus:{bonus}")


def _graveyard_inspect(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — grave_scholar on_summon. Reveals the owner's own
    Graveyard contents via a private GraveyardInspected event (same
    non-persistent-reveal convention as EnemyCardRevealed/DeckInspected —
    Observation's hidden-info filtering is untouched; a human player
    learns it by reading the event / a UI toast).
    """
    from game.core.events import GraveyardInspected

    if ctx.unit is None or ctx.state is None:
        return
    owner = ctx.unit.owner
    ps = ctx.state.get_player(owner)
    ctx.events.append(GraveyardInspected(player_id=owner, card_ids=tuple(ps.graveyard)))


def _graveyard_counter(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — bone_collector passive. Arms ``graveyard_counter:0``;
    incremented centrally in RulesEngine.execute() every time one of the
    owner's OTHER allied Monsters is destroyed (see
    ``RulesEngine._apply_death_triggered_effects`` — the same central
    MonsterDestroyed hook that feeds capture_protection_from_counter and
    death_trigger_draw), mirroring how PlayerState.monsters_lost_count is
    centrally maintained rather than patched into every destruction path.
    """
    if ctx.unit is None:
        return
    if not any(s.startswith("graveyard_counter:") for s in ctx.unit.statuses):
        ctx.unit.add_status("graveyard_counter:0")


def _capture_protection_from_counter(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — bone_collector passive companion to graveyard_counter.
    Arms ``capture_protection_from_counter:<required_counters>:<uses_per_counter>``,
    read by the same central hook that increments graveyard_counter: every
    time the counter reaches a new multiple of ``required_counters``, the
    unit gains ``uses_per_counter`` more capture_protection shield charges.
    """
    if ctx.unit is None:
        return
    required = ctx.effect.params.get("required_counters", 2)
    uses = ctx.effect.params.get("uses_per_counter", 1)
    ctx.unit.statuses = [
        s for s in ctx.unit.statuses
        if not s.startswith("capture_protection_from_counter:")
    ]
    ctx.unit.add_status(f"capture_protection_from_counter:{required}:{uses}")


def _dismiss_monster(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — vessel_reclaimer activated ability.

    Distinct from the player's own DismissMonster action (which targets
    the player's OWN unit and returns the card to hand): this targets an
    ADJACENT allied Monster (``ctx.extra["target"]``, an (file, rank)
    tuple enumerated by RulesEngine.get_legal_actions) and sends the card
    to the Graveyard instead (card text: "sending the Monster card to the
    Graveyard").
    """
    from game.chess.pieces import Position
    from game.core.events import MonsterDismissed
    from game.core.rules import IllegalActionError

    if ctx.unit is None or ctx.position is None or ctx.state is None:
        return
    target = (ctx.extra or {}).get("target")
    if target is None:
        raise IllegalActionError("dismiss_monster requires an adjacent allied Monster target.")
    tf, tr = target
    if max(abs(tf - ctx.position.file), abs(tr - ctx.position.rank)) != 1:
        raise IllegalActionError("dismiss_monster target must be adjacent.")
    target_pos = Position(tf, tr)
    target_unit = ctx.state.board.get_unit(target_pos)
    if (
        target_unit is None
        or target_unit.owner != ctx.unit.owner
        or target_unit.monster_id is None
    ):
        raise IllegalActionError("dismiss_monster target must be an adjacent allied Monster.")

    card_id = target_unit.monster_id
    target_unit.monster_id = None
    target_unit.statuses = [
        s for s in target_unit.statuses
        if not any(
            s.startswith(prefix) for prefix in (
                "shield:", "spell_radius_bonus:", "stealth",
                "ritual_boost:", "frozen:",
            )
        )
    ]
    ctx.state.get_player(target_unit.owner).graveyard.append(card_id)
    ctx.events.append(MonsterDismissed(
        player_id=target_unit.owner,
        card_id=card_id,
        vessel_piece_id=target_unit.piece.id,
        position=target_pos,
    ))


def _graveyard_scaling_movement(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — crypt_walker. Arms ``graveyard_leap:<threshold>:<bonus_leap>``,
    read by ``mechanics.monsters.get_movement_additions()`` (GameState-aware
    call site — see territory_bonus above) — adds ``bonus_leap`` cardinal
    leap squares once ``len(owner.graveyard) >= threshold``.
    """
    if ctx.unit is None:
        return
    threshold = ctx.effect.params.get("threshold", 5)
    bonus_leap = ctx.effect.params.get("bonus_leap", 1)
    ctx.unit.statuses = [s for s in ctx.unit.statuses if not s.startswith("graveyard_leap:")]
    ctx.unit.add_status(f"graveyard_leap:{threshold}:{bonus_leap}")


def _death_trigger_draw(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — mourning_queen passive. Arms ``death_trigger_draw:1``;
    consumed by the same central MonsterDestroyed hook as graveyard_counter
    (RulesEngine._apply_death_triggered_effects), which draws the owner one
    card the first time (per their own turn — PlayerState.
    death_trigger_draw_used_this_turn) another allied Monster is destroyed.
    """
    if ctx.unit is None:
        return
    if not any(s.startswith("death_trigger_draw:") for s in ctx.unit.statuses):
        ctx.unit.add_status("death_trigger_draw:1")


def _sacrifice_bonus(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — blood_seer. Arms ``sacrifice_ritual_bonus:N`` on summon;
    consumed in ``mechanics.rituals.execute_ritual()`` — when this unit is
    among the PURE sacrifices (not the Vessel) paid for a Ritual, the
    owner's first not-yet-REVEALED OTHER Ritual gains N progress (reusing
    the same "first not-yet-REVEALED, pool order, threshold-promotes"
    accounting as ritual_acolyte's end-of-turn ritual_progress_boost).
    """
    if ctx.unit is None:
        return
    progress = ctx.effect.params.get("ritual_progress", 2)
    ctx.unit.statuses = [
        s for s in ctx.unit.statuses if not s.startswith("sacrifice_ritual_bonus:")
    ]
    ctx.unit.add_status(f"sacrifice_ritual_bonus:{progress}")


def _ritual_activation_range(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — herald_of_the_gate. Arms ``ritual_range_bonus:N`` on
    summon; consumed by ``mechanics.rituals``' formation pattern matcher
    (_find_formation_candidates / _formation_candidate_ok via
    _ritual_range_bonus()) — a Formation Ritual's non-anchor nodes accept
    any of the owner's own units within Chebyshev N of the node's exact
    offset, not only the exact square, while any of the owner's summoned
    Monsters carries this status ("eligible components within 1 extra
    square as connected").
    """
    if ctx.unit is None:
        return
    bonus = ctx.effect.params.get("radius_bonus", 1)
    ctx.unit.statuses = [s for s in ctx.unit.statuses if not s.startswith("ritual_range_bonus:")]
    ctx.unit.add_status(f"ritual_range_bonus:{bonus}")


def _restore_builder(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — guild_foreman on_summon. One adjacent allied Pawn that
    has already spent its once-per-match Builder token
    (``builder_available == False``) regains it. Picks the first such
    Pawn found among the 8 neighbouring squares (``max_targets: 1``).
    """
    from game.core.events import BuilderRestored
    from game.core.phases import PieceType

    if ctx.unit is None or ctx.position is None or ctx.state is None:
        return
    pos = ctx.position
    owner = ctx.unit.owner
    for df in (-1, 0, 1):
        for dr in (-1, 0, 1):
            if df == 0 and dr == 0:
                continue
            nf, nr = pos.file + df, pos.rank + dr
            if not (0 <= nf <= 7 and 0 <= nr <= 7):
                continue
            from game.chess.pieces import Position as _Pos
            neighbour = ctx.state.board.get_unit(_Pos(nf, nr))
            if (
                neighbour is None
                or neighbour.owner != owner
                or neighbour.piece.piece_type != PieceType.PAWN
                or neighbour.builder_available
            ):
                continue
            neighbour.builder_available = True
            ctx.events.append(BuilderRestored(
                player_id=owner,
                piece_id=neighbour.piece.id,
                restored_by_piece_id=ctx.unit.piece.id,
            ))
            return


def _capture_then_retreat(ctx: "EffectContext") -> None:
    """
    IMPLEMENTED — dusk_reaver after-capture effect.

    After capturing, the attacker may retreat up to ``max_distance`` squares
    (Chebyshev) provided the destination is empty and, if
    ``must_move_away_from_enemy_king`` is set, no closer to the enemy King
    than the attacker's current position.

    Structurally identical to ``movement._reposition_unit`` (blade_dancer) —
    reuses the same REPOSITION PendingDecision / RepositionUnit resolver —
    with the extra enemy-King-distance filter applied to the option list.
    """
    from game.chess.pieces import Position
    from game.core.phases import DecisionType
    from game.core.state import PendingDecision

    params  = ctx.effect.params
    trigger = params.get("trigger", "")
    if trigger != "after_capture":
        return

    if ctx.unit is None or ctx.position is None or ctx.state is None:
        return

    max_dist = params.get("max_distance", 1)
    must_flee = params.get("must_move_away_from_enemy_king", False)
    attacker_pos = ctx.position

    king_dist = None
    if must_flee:
        opponent = "black" if ctx.unit.owner == "white" else "white"
        king_result = ctx.state.board.find_king(opponent)
        if king_result is not None:
            king_pos, _ = king_result
            king_dist = max(
                abs(attacker_pos.file - king_pos.file),
                abs(attacker_pos.rank - king_pos.rank),
            )

    options: list[tuple[int, int]] = []
    for df in range(-max_dist, max_dist + 1):
        for dr in range(-max_dist, max_dist + 1):
            if df == 0 and dr == 0:
                continue
            nf = attacker_pos.file + df
            nr = attacker_pos.rank + dr
            if not (0 <= nf <= 7 and 0 <= nr <= 7):
                continue
            if ctx.state.board.get_unit(Position(nf, nr)) is not None:
                continue
            if king_dist is not None:
                king_result = ctx.state.board.find_king(
                    "black" if ctx.unit.owner == "white" else "white"
                )
                if king_result is not None:
                    king_pos, _ = king_result
                    new_dist = max(abs(nf - king_pos.file), abs(nr - king_pos.rank))
                    if new_dist < king_dist:
                        continue  # would move closer to the enemy King
            options.append((nf, nr))

    if options and ctx.state.pending_decision is None:
        ctx.state.pending_decision = PendingDecision(
            player_id=ctx.unit.owner,
            decision_type=DecisionType.REPOSITION,
            options=options,
            min_choices=1,
            max_choices=1,
            context={
                "piece_id": ctx.unit.piece.id,
                "current_pos": (attacker_pos.file, attacker_pos.rank),
            },
        )


META_HANDLERS: dict[str, object] = {
    "spell_radius_bonus":               _spell_radius_bonus,
    "copy_effect":                      _copy_effect,
    "vessel_support":                   _vessel_support,
    "ignore_terrain":                   _ignore_terrain,
    "building_damage_bonus":            _building_damage_bonus,
    "building_capture_protection":      _building_capture_protection,
    "weakened_target_bonus":            _weakened_target_bonus,
    "restore_effect_charge":            _restore_effect_charge,
    "challenge_unit":                   _challenge_unit,
    "territory_bonus":                  _territory_bonus,
    "graveyard_inspect":                _graveyard_inspect,
    "graveyard_counter":                _graveyard_counter,
    "capture_protection_from_counter":  _capture_protection_from_counter,
    "dismiss_monster":                  _dismiss_monster,
    "graveyard_scaling_movement":       _graveyard_scaling_movement,
    "death_trigger_draw":               _death_trigger_draw,
    "sacrifice_bonus":                  _sacrifice_bonus,
    "ritual_activation_range":          _ritual_activation_range,
    "restore_builder":                  _restore_builder,
    "capture_then_retreat":             _capture_then_retreat,
}
