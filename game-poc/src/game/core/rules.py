"""
RulesEngine — Stage 0 / Stage 1
=================================

The single authoritative source of game rules.

Responsibilities:
    1. Validate that an Action is legal in the current GameState.
    2. Execute the action: advance the state and produce Events.
    3. Detect check / checkmate / Final Duel triggers.
    4. Enumerate all legal actions for the current player.

Invariants enforced (from phase0.md §18):

    TURN_OWNERSHIP       — Only active_player may submit voluntary actions.
    PHASE_OWNERSHIP      — MovePiece only in CHESS phase.
    CHESS_MOVE_LIMIT     — Max one chess move per turn.
    PREPARATION_LIMIT    — Max one major preparation action per turn.
    VESSEL_OWNERSHIP     — Cannot transform an opponent's piece.
    VESSEL_COMPATIBILITY — Monster can only use supported vessel classes.
    KING_NOT_VESSEL      — King cannot be a Monster vessel.
    BUILDER_IS_PAWN      — Only Pawns may build.
    BUILDER_ONCE         — Pawn that already built cannot build again.
    HIDDEN_INFO          — Observation must never expose opponent's private cards.
    FINAL_DUEL_CAPTURE   — King capture never directly sets winner.

Stage 0 chess rules (simple subset, full movement in Stage 1):
    • Pieces move according to standard chess movement.
    • Captures are detected and produce PieceCaptured events.
    • Check is detected after every move.
    • Checkmate triggers FinalDuelTriggered instead of GameOver.
    • Promotion is detected when a Pawn reaches the back rank.

Stage 1 upgrades (this file):
    • MovePiece now uses chess.movement for fully-legal moves.
    • Castle action supported (kingside + queenside).
    • En passant capture detected and resolved.
    • Stalemate triggers FinalDuelTriggered(LAST_STAND).
    • Castling rights revoked on King/Rook move or Rook capture.
    • En passant target updated after every double pawn push.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from game.logger import get_logger

_log = get_logger(__name__)

from game.chess.board import BoardState
from game.chess.check import is_checkmate, is_stalemate
from game.chess.movement import (
    can_castle_kingside,
    can_castle_queenside,
    get_legal_moves,
    get_pseudo_legal_moves,
    has_any_legal_move,
    is_in_check,
)
from game.chess.pieces import Position, UnitInstance
from game.chess.promotion import apply_promotion, is_promotion_rank
from game.core.actions import (
    Action,
    ActivateMonsterAbility,
    ActivateRitual,
    ActivateSpell,
    ActivateTrap,
    Castle,
    ChangeKing,
    CoronateKing,
    DeclareRecompose,
    DiscardCard,
    DismissMonster,
    EndPreparation,
    EndTurn,
    MovePiece,
    PlaceTrap,
    PromotePawn,
    RepositionUnit,
    SelectRecomposeCards,
    StartConstruction,
    SummonMonster,
)
from game.core.events import (
    CardDiscarded,
    CardDrawn,
    CastlingPerformed,
    CheckDetected,
    CheckResolved,
    ChessMoveUsed,
    DeckRecycled,
    EnPassantCapture,
    Event,
    FinalDuelTriggered,
    MonsterAbilityActivated,
    MonsterDestroyed,
    MonsterDismissed,
    PhaseAdvanced,
    PieceCaptured,
    PieceMoved,
    PiecePromoted,
    PrepActionUsed,
    RecomposeInitiated,
    StalemateDetected,
    TurnEnded,
    TurnStarted,
)
from game.core.phases import (
    DecisionType,
    FinalDuelType,
    HAND_SIZE_LIMIT,
    KingCardStatus,
    Phase,
    PieceType,
)
from game.core.rng import DeterministicRNG
from game.core.state import (
    BuildingPoolEntry,
    DuelState,
    GameState,
    KingCardState,
    PendingDecision,
    PlayerState,
    TrapInstance,
)

if TYPE_CHECKING:
    pass


# ─────────────────────────────────────────────────────────────────────────────
# Validation errors
# ─────────────────────────────────────────────────────────────────────────────

class IllegalActionError(Exception):
    """Raised when an action violates a game rule."""


# ─────────────────────────────────────────────────────────────────────────────
# Phase transition helper
# ─────────────────────────────────────────────────────────────────────────────

_NORMAL_PHASE_ORDER = [
    Phase.START,
    Phase.DRAW,
    Phase.PREPARATION,
    Phase.CHESS,
    Phase.REACTION,
    Phase.END,
]


def next_phase(current: Phase) -> Phase:
    """Return the next phase in the normal turn sequence."""
    try:
        idx = _NORMAL_PHASE_ORDER.index(current)
        if idx + 1 < len(_NORMAL_PHASE_ORDER):
            return _NORMAL_PHASE_ORDER[idx + 1]
    except ValueError:
        pass
    raise ValueError(f"No automatic next phase for {current!r}")


# ─────────────────────────────────────────────────────────────────────────────
# RulesEngine
# ─────────────────────────────────────────────────────────────────────────────

class RulesEngine:
    """
    The engine that owns and enforces all game rules.

    Public API:
        engine = RulesEngine(registry=registry)   # Stage 5: registry optional
        engine.execute(state, action, rng) → list[Event]  + mutates state

    Internal helpers produce events and mutate state atomically.

    Stage 5 note: pass a CardRegistry at construction time to enable:
        • vessel compatibility enforcement (VESSEL_COMPATIBILITY invariant)
        • on-summon effect resolution
        • monster movement additions in legal-move enumeration
    """

    def __init__(self, registry: "object | None" = None) -> None:
        self._registry = registry

    # ── Public entry point ───────────────────────────────────────────────

    def execute(
        self,
        state: GameState,
        action: Action,
        rng: DeterministicRNG,
    ) -> list[Event]:
        """
        Validate and execute ``action`` against ``state``.
        Mutates ``state`` and returns the list of Events produced.

        Raises ``IllegalActionError`` if the action is invalid.
        """
        _log.debug("DISPATCH %s  player=%s  phase=%s",
                   type(action).__name__, action.player_id, state.phase.value)
        try:
            self._validate_ownership(state, action)
        except IllegalActionError as exc:
            _log.warning("OWNERSHIP FAIL  %s: %s", type(action).__name__, exc)
            raise

        events: list[Event] = []

        # Dispatch
        try:
            if isinstance(action, MovePiece):
                events = self._execute_move_piece(state, action, rng)
            elif isinstance(action, Castle):
                events = self._execute_castle(state, action)
            elif isinstance(action, SummonMonster):
                events = self._execute_summon_monster(state, action, rng, registry=self._registry)
            elif isinstance(action, RepositionUnit):
                events = self._execute_reposition_unit(state, action)
            elif isinstance(action, DismissMonster):
                events = self._execute_dismiss_monster(state, action, registry=self._registry)
            elif isinstance(action, ActivateMonsterAbility):
                events = self._execute_activate_monster_ability(state, action, rng, registry=self._registry)
            elif isinstance(action, ActivateSpell):
                events = self._execute_activate_spell(state, action)
            elif isinstance(action, PlaceTrap):
                events = self._execute_place_trap(state, action)
            elif isinstance(action, StartConstruction):
                events = self._execute_start_construction(state, action)
            elif isinstance(action, CoronateKing):
                events = self._execute_coronate_king(state, action)
            elif isinstance(action, ChangeKing):
                events = self._execute_change_king(state, action)
            elif isinstance(action, DeclareRecompose):
                events = self._execute_declare_recompose(state, action, rng)
            elif isinstance(action, SelectRecomposeCards):
                events = self._execute_select_recompose(state, action, rng)
            elif isinstance(action, PromotePawn):
                events = self._execute_promote_pawn(state, action)
            elif isinstance(action, DiscardCard):
                events = self._execute_discard_card(state, action)
            elif isinstance(action, EndPreparation):
                events = self._execute_end_preparation(state, action)
            elif isinstance(action, EndTurn):
                events = self._execute_end_turn(state, action)
            else:
                raise IllegalActionError(
                    f"Unrecognized or not-yet-implemented action: {type(action).__name__}"
                )
        except IllegalActionError as exc:
            _log.warning("ILLEGAL  %s: %s", type(action).__name__, exc)
            raise
        except Exception as exc:
            _log.error("UNEXPECTED ERROR in %s: %s", type(action).__name__, exc, exc_info=True)
            raise

        _log.debug("DONE     %s  → events=[%s]",
                   type(action).__name__,
                   ", ".join(type(e).__name__ for e in events))
        state.event_log.extend(events)
        return events

    # ── Legal action generation ──────────────────────────────────────────

    def get_legal_actions(
        self,
        state: GameState,
        player_id: str,
        registry: "object | None" = None,
    ) -> list[Action]:
        """
        Enumerate all actions the player may legally take right now.

        Stage 1 scope (adds over Stage 0):
            • CHESS → Castle actions (kingside / queenside when legal)
            • CHESS → En passant moves included in MovePiece candidates
            • Empty CHESS → stalemate / checkmate handled automatically

        Stage 5 scope:
            • PREPARATION → DismissMonster for each deployed monster
            • PREPARATION → SummonMonster uses registry for vessel compatibility
            • CHESS → movement additions from monster alter_movement effects
        """
        # Use per-call registry if given, else fall back to engine-level registry
        registry = registry or self._registry
        if state.active_player != player_id:
            return []

        if state.is_game_over():
            return []

        phase = state.phase
        ps = state.get_player(player_id)
        actions: list[Action] = []

        if phase == Phase.DRAW:
            # Automatic — no player choice.
            return []

        if phase == Phase.DISCARD:
            # Player must discard cards until hand ≤ HAND_SIZE_LIMIT.
            # Each DiscardCard is a separate action; player picks one card to discard.
            for card_id in ps.hand:
                actions.append(DiscardCard(player_id=player_id, card_id=card_id))
            return actions

        if phase == Phase.RECOMPOSE_SELECTION:
            pd = state.pending_decision
            if pd is not None and pd.player_id == player_id:
                # Player must choose exactly pd.min_choices cards.
                from itertools import combinations
                n = pd.min_choices
                hand = ps.hand
                for combo in combinations(hand, n):
                    actions.append(
                        SelectRecomposeCards(
                            player_id=player_id,
                            card_ids=list(combo),
                        )
                    )
            return actions

        if phase == Phase.PROMOTION_SELECTION:
            pd = state.pending_decision
            if pd is not None and pd.player_id == player_id:
                pos_tuple = pd.context.get("position")
                if pos_tuple:
                    ppos = Position(*pos_tuple)
                    for pt in ("queen", "rook", "bishop", "knight"):
                        actions.append(
                            PromotePawn(
                                player_id=player_id,
                                position=ppos,
                                piece_type=pt,
                            )
                        )
            return actions

        if phase == Phase.PREPARATION:
            actions.append(EndPreparation(player_id=player_id))
            if not ps.preparation_action_used:
                from game.cards.card import MonsterCard
                # Summon Monster: for each monster in hand × valid vessels
                for card_id in ps.hand:
                    if registry and card_id in registry:
                        card = registry.get(card_id)
                        if isinstance(card, MonsterCard):
                            for pos, unit in state.board.all_units_for(player_id):
                                if unit.monster_id is None and card.supports_vessel(
                                    unit.piece.piece_type.value
                                ) and unit.piece.piece_type != PieceType.KING:
                                    actions.append(
                                        SummonMonster(
                                            player_id=player_id,
                                            card_id=card_id,
                                            vessel_position=pos,
                                        )
                                    )
                # Stage 5: Dismiss Monster (return card to hand, restore vessel)
                for pos, unit in state.board.all_units_for(player_id):
                    if unit.monster_id is not None:
                        actions.append(
                            DismissMonster(
                                player_id=player_id,
                                unit_position=pos,
                            )
                        )
                # Declare Recompose (always available, risky)
                if ps.hand:
                    actions.append(DeclareRecompose(player_id=player_id))
                # Coronate King (if no active king and not in check)
                if ps.active_king is None and not ps.is_in_check():
                    for kcs in ps.king_pool:
                        if kcs.status == KingCardStatus.HIDDEN:
                            actions.append(
                                CoronateKing(
                                    player_id=player_id,
                                    king_card_id=kcs.king_card_id,
                                )
                            )
            return actions

        if phase == Phase.CHESS:
            # Stage 5: if a REPOSITION decision is pending, only reposition actions
            # are legal until the player resolves it.
            pd = state.pending_decision
            if pd is not None and pd.decision_type == DecisionType.REPOSITION and pd.player_id == player_id:
                piece_id = pd.context.get("piece_id")
                cur_tuple = pd.context.get("current_pos")
                if piece_id and cur_tuple:
                    from_pos = Position(*cur_tuple)
                    for opt in pd.options:
                        to_pos = Position(*opt)
                        actions.append(RepositionUnit(
                            player_id=player_id,
                            from_position=from_pos,
                            to_position=to_pos,
                        ))
                return actions

            if not ps.chess_move_used:
                ep = state.en_passant_target
                cr = ps.castling_rights
                for pos, unit in state.board.all_units_for(player_id):
                    for target in get_legal_moves(
                        state.board, pos, unit,
                        en_passant_target=ep,
                        castling_rights=cr,
                        player_id=player_id,
                        registry=registry,
                    ):
                        actions.append(
                            MovePiece(
                                player_id=player_id,
                                source=pos,
                                target=target,
                            )
                        )
                # Castling actions
                if can_castle_kingside(state.board, player_id, cr):
                    actions.append(Castle(player_id=player_id, side="kingside"))
                if can_castle_queenside(state.board, player_id, cr):
                    actions.append(Castle(player_id=player_id, side="queenside"))

            # Stage 5: activated monster abilities (available any time during CHESS)
            if registry is not None:
                from game.mechanics.effects.registry import ACTIVATED_EFFECT_TYPES
                from game.cards.card import MonsterCard as _MC
                for pos, unit in state.board.all_units_for(player_id):
                    if unit.monster_id is None:
                        continue
                    try:
                        card = registry.get(unit.monster_id)
                    except KeyError:
                        continue
                    if not isinstance(card, _MC):
                        continue
                    for eff in card.effects:
                        if eff.type in ACTIVATED_EFFECT_TYPES:
                            actions.append(
                                ActivateMonsterAbility(
                                    player_id=player_id,
                                    unit_position=pos,
                                    ability_id=eff.type,
                                )
                            )

            if not actions:
                # No moves available — checkmate or stalemate handled by engine
                actions.append(EndTurn(player_id=player_id))
            return actions

        if phase == Phase.REACTION:
            # Stage 0: no reaction actions yet; automatic pass
            return [EndTurn(player_id=player_id)]

        if phase in (Phase.END, Phase.REACTION):
            return [EndTurn(player_id=player_id)]

        return []

    # ── Validation helpers ───────────────────────────────────────────────

    def _validate_ownership(self, state: GameState, action: Action) -> None:
        """Invariant: only active_player may submit voluntary actions."""
        # SelectRecomposeCards and PromotePawn can come from either player
        # during interrupt phases, but player_id must still match pending.
        if state.phase in (Phase.RECOMPOSE_SELECTION, Phase.PROMOTION_SELECTION):
            pd = state.pending_decision
            if pd and action.player_id != pd.player_id:
                raise IllegalActionError(
                    f"Decision belongs to {pd.player_id!r}, "
                    f"not {action.player_id!r}."
                )
            return
        if action.player_id != state.active_player:
            raise IllegalActionError(
                f"It is {state.active_player!r}'s turn, "
                f"not {action.player_id!r}'s."
            )

    def _require_phase(self, state: GameState, *phases: Phase) -> None:
        if state.phase not in phases:
            raise IllegalActionError(
                f"Action not allowed in phase {state.phase!r}. "
                f"Expected one of: {[p.value for p in phases]}"
            )

    def _require_no_prep_used(self, state: GameState, player_id: str) -> None:
        if state.get_player(player_id).preparation_action_used:
            raise IllegalActionError("Preparation action already used this turn.")

    def _mark_prep_used(
        self,
        state: GameState,
        player_id: str,
        action_name: str,
        events: list[Event],
    ) -> None:
        state.get_player(player_id).preparation_action_used = True
        events.append(PrepActionUsed(player_id=player_id, action_type=action_name))

    def _advance_phase(
        self,
        state: GameState,
        player_id: str,
        events: list[Event],
    ) -> None:
        old = state.phase
        new = next_phase(old)
        state.phase = new
        events.append(PhaseAdvanced(player_id=player_id, from_phase=old, to_phase=new))
        # Auto-execute DRAW phase
        if new == Phase.DRAW:
            self._auto_draw(state, player_id, events)
        if new == Phase.START:
            pass  # start-of-turn effects (Stage 1+)

    # ── Phase auto-executors ─────────────────────────────────────────────

    def _auto_start_turn(
        self,
        state: GameState,
        player_id: str,
        rng: DeterministicRNG,
        events: list[Event],
    ) -> None:
        """Called at the beginning of a player's turn."""
        ps = state.get_player(player_id)
        ps.reset_turn_flags()
        events.append(TurnStarted(player_id=player_id, turn_number=state.turn_number))
        # Advance through START → DRAW → PREPARATION automatically
        old = state.phase
        state.phase = Phase.DRAW
        events.append(PhaseAdvanced(player_id=player_id, from_phase=old, to_phase=Phase.DRAW))
        self._auto_draw(state, player_id, events, rng=rng)

    def _auto_draw(
        self,
        state: GameState,
        player_id: str,
        events: list[Event],
        rng: "DeterministicRNG | None" = None,
    ) -> None:
        """
        Draw 1 card from deck to hand.

        If the deck is empty, recycle the graveyard: shuffle it back into a
        fresh deck (requires ``rng``; if None the graveyard order is preserved)
        before drawing.  If both deck and graveyard are empty, no card is drawn.
        """
        ps = state.get_player(player_id)
        if not ps.deck and ps.graveyard:
            # Recycle: graveyard → new deck
            ps.deck = list(ps.graveyard)
            ps.graveyard.clear()
            if rng is not None:
                rng.shuffle(ps.deck)
            events.append(DeckRecycled(
                player_id=player_id,
                card_count=len(ps.deck),
            ))
        if ps.deck:
            card_id = ps.deck.pop(0)
            ps.hand.append(card_id)
            events.append(CardDrawn(player_id=player_id, card_id=card_id))
        # Advance to PREPARATION
        state.phase = Phase.PREPARATION
        events.append(PhaseAdvanced(
            player_id=player_id,
            from_phase=Phase.DRAW,
            to_phase=Phase.PREPARATION,
        ))

    # ── Action executors ─────────────────────────────────────────────────

    def _execute_move_piece(
        self,
        state: GameState,
        action: MovePiece,
        rng: DeterministicRNG,
    ) -> list[Event]:
        self._require_phase(state, Phase.CHESS)
        ps = state.get_player(action.player_id)
        if ps.chess_move_used:
            raise IllegalActionError("Chess move already used this turn.")

        unit = state.board.get_unit(action.source)
        if unit is None:
            raise IllegalActionError(f"No piece at {action.source}.")
        if unit.owner != action.player_id:
            raise IllegalActionError("Cannot move an opponent's piece.")

        ep = state.en_passant_target
        cr = ps.castling_rights
        legal = get_legal_moves(
            state.board, action.source, unit,
            en_passant_target=ep,
            castling_rights=cr,
            player_id=action.player_id,
            registry=self._registry,
        )
        if action.target not in legal:
            raise IllegalActionError(
                f"Illegal move: {action.source} → {action.target}."
            )

        events: list[Event] = []

        # ── En passant detection ──────────────────────────────────────────
        is_ep = (
            unit.piece.piece_type == PieceType.PAWN
            and ep is not None
            and action.target == ep
            and state.board.get_unit(action.target) is None
        )
        if is_ep:
            direction = 1 if unit.owner == "white" else -1
            captured_sq = Position(action.target.file, action.target.rank - direction)
            ep_captured_unit = state.board.remove_unit(captured_sq)
            state.board.move_unit(action.source, action.target)
            events.append(PieceMoved(
                piece_id=unit.piece.id,
                owner=unit.owner,
                source=action.source,
                target=action.target,
            ))
            if ep_captured_unit is not None:
                opp = state.get_player(state.opponent_of(action.player_id))
                opp.captured_pieces.append(ep_captured_unit.piece.id)
                events.append(EnPassantCapture(
                    player_id=action.player_id,
                    pawn_piece_id=unit.piece.id,
                    source=action.source,
                    target=action.target,
                    captured_pawn_piece_id=ep_captured_unit.piece.id,
                    captured_pawn_at=captured_sq,
                ))
        else:
            # ── Normal move ───────────────────────────────────────────────
            # Stage 5: check capture protection BEFORE the board mutation.
            from game.mechanics.monsters import apply_capture_protection
            target_unit = state.board.get_unit(action.target)
            shield_absorbed = (
                target_unit is not None
                and target_unit.monster_id is not None
                and apply_capture_protection(target_unit)
            )

            captured = state.board.move_unit(action.source, action.target)

            if shield_absorbed and captured is not None:
                # The capture was blocked by the shield.
                # Restore: move attacker back to source, defender back to target.
                attacker = state.board.remove_unit(action.target)
                if attacker is not None:
                    state.board.place_unit(action.source, attacker)
                state.board.place_unit(action.target, captured)
                from game.core.events import CaptureBlocked
                shields_left = next(
                    (int(s.split(":")[1]) for s in captured.statuses
                     if s.startswith("shield:")),
                    0,
                )
                events.append(CaptureBlocked(
                    attacker_piece_id=unit.piece.id,
                    defender_piece_id=captured.piece.id,
                    position=action.target,
                    shields_remaining=shields_left,
                ))
            else:
                events.append(PieceMoved(
                    piece_id=unit.piece.id,
                    owner=unit.owner,
                    source=action.source,
                    target=action.target,
                ))
                if captured is not None:
                    opp = state.get_player(state.opponent_of(action.player_id))
                    opp.captured_pieces.append(captured.piece.id)
                    # ── Stage 5: monster destruction event ──────────────
                    if captured.monster_id is not None:
                        events.append(MonsterDestroyed(
                            player_id=captured.owner,
                            card_id=captured.monster_id,
                            vessel_piece_id=captured.piece.id,
                            position=action.target,
                            destroyed_by_piece_id=unit.piece.id,
                        ))
                    events.append(PieceCaptured(
                        piece_id=captured.piece.id,
                        owner=captured.owner,
                        captured_at=action.target,
                        captured_by_piece_id=unit.piece.id,
                    ))

                    # ── Stage 5: after-capture effects on the attacker ───
                    # (freeze_square for iron_vanguard, reposition for blade_dancer)
                    if unit.monster_id is not None and self._registry is not None:
                        try:
                            attacker_card = self._registry.get(unit.monster_id)
                        except KeyError:
                            attacker_card = None
                        from game.cards.card import MonsterCard as _MC
                        if attacker_card is not None and isinstance(attacker_card, _MC):
                            from game.mechanics.monsters import apply_after_capture_effects
                            apply_after_capture_effects(
                                state, unit, action.target, attacker_card, events
                            )

                # ── Stage 5: damage aura check after any (non-blocked) move ─
                # Check if the moving piece entered an enemy damage aura.
                # Must run AFTER the piece has landed at action.target.
                from game.mechanics.monsters import check_damage_aura
                check_damage_aura(state, unit, action.target, events, self._registry)

            # King capture → Final Duel (never sets winner directly)
            if captured is not None and not shield_absorbed and captured.piece.piece_type == PieceType.KING:
                duel = DuelState(
                    duel_type=FinalDuelType.ASSAULT,
                    attacker=action.player_id,
                    defender=captured.owner,
                )
                state.duel = duel
                state.phase = Phase.FINAL_DUEL
                events.append(FinalDuelTriggered(
                    attacker=action.player_id,
                    defender=captured.owner,
                    duel_type=FinalDuelType.ASSAULT,
                    trigger_position=action.target,
                ))
                self._revoke_castling_rights_for_captured_rook(
                    state, captured, action.target
                )
                return events

        # ── Update castling rights ────────────────────────────────────────
        self._update_castling_rights(state, action.player_id, unit, action.source)

        # ── Update en passant target ──────────────────────────────────────
        state.en_passant_target = self._compute_en_passant_target(
            unit, action.source, action.target
        )

        # ── Promotion ─────────────────────────────────────────────────────
        if is_promotion_rank(action.target, action.player_id):
            if unit.piece.piece_type == PieceType.PAWN:
                state.pending_decision = PendingDecision(
                    player_id=action.player_id,
                    decision_type=DecisionType.PROMOTION,
                    options=["queen", "rook", "bishop", "knight"],
                    min_choices=1,
                    max_choices=1,
                    context={"position": (action.target.file, action.target.rank)},
                )
                state.phase = Phase.PROMOTION_SELECTION

        ps.chess_move_used = True
        events.append(ChessMoveUsed(player_id=action.player_id))

        # ── Check status update ───────────────────────────────────────────
        self._update_check_status(state, events)

        # ── Checkmate / stalemate detection ──────────────────────────────
        opp_id = state.opponent_of(action.player_id)
        opp_ps = state.get_player(opp_id)
        opp_ep = state.en_passant_target   # already cleared above (en passant consumed)
        opp_cr = opp_ps.castling_rights

        if is_checkmate(state.board, opp_id, opp_ep, opp_cr):
            state.duel = DuelState(
                duel_type=FinalDuelType.SIEGE,
                attacker=action.player_id,
                defender=opp_id,
            )
            state.phase = Phase.FINAL_DUEL
            events.append(FinalDuelTriggered(
                attacker=action.player_id,
                defender=opp_id,
                duel_type=FinalDuelType.SIEGE,
            ))
        elif is_stalemate(state.board, opp_id, opp_ep, opp_cr):
            events.append(StalemateDetected(player_id=opp_id))
            state.duel = DuelState(
                duel_type=FinalDuelType.LAST_STAND,
                attacker=action.player_id,
                defender=opp_id,
            )
            state.phase = Phase.FINAL_DUEL
            events.append(FinalDuelTriggered(
                attacker=action.player_id,
                defender=opp_id,
                duel_type=FinalDuelType.LAST_STAND,
            ))

        # If not in an interrupt phase, advance to REACTION.
        # Exception: if a REPOSITION decision is pending (blade_dancer after-capture),
        # stay in CHESS so the player can execute RepositionUnit before REACTION.
        if state.phase == Phase.CHESS:
            if (state.pending_decision is not None
                    and state.pending_decision.decision_type == DecisionType.REPOSITION):
                pass  # stay in CHESS; REACTION will fire after RepositionUnit resolves
            else:
                old = state.phase
                state.phase = Phase.REACTION
                events.append(PhaseAdvanced(
                    player_id=action.player_id,
                    from_phase=old,
                    to_phase=Phase.REACTION,
                ))
        return events

    def _execute_castle(
        self,
        state: GameState,
        action: Castle,
    ) -> list[Event]:
        """
        Execute a castling move.  Both King and Rook are moved atomically.
        Castling rights are fully revoked for the player afterwards.
        """
        self._require_phase(state, Phase.CHESS)
        ps = state.get_player(action.player_id)
        if ps.chess_move_used:
            raise IllegalActionError("Chess move already used this turn.")

        cr = ps.castling_rights
        if action.side == "kingside":
            if not can_castle_kingside(state.board, action.player_id, cr):
                raise IllegalActionError(
                    f"Kingside castling is not legal for {action.player_id!r}."
                )
        elif action.side == "queenside":
            if not can_castle_queenside(state.board, action.player_id, cr):
                raise IllegalActionError(
                    f"Queenside castling is not legal for {action.player_id!r}."
                )
        else:
            raise IllegalActionError(f"Unknown castling side: {action.side!r}")

        rank = 0 if action.player_id == "white" else 7
        king_from = Position(4, rank)  # e-file

        if action.side == "kingside":
            king_to = Position(6, rank)   # g-file
            rook_from = Position(7, rank)  # h-file
            rook_to = Position(5, rank)    # f-file
        else:
            king_to = Position(2, rank)   # c-file
            rook_from = Position(0, rank)  # a-file
            rook_to = Position(3, rank)    # d-file

        # Move both pieces
        state.board.move_unit(king_from, king_to)
        state.board.move_unit(rook_from, rook_to)

        # Revoke all castling rights
        cr.revoke_all()

        # Clear en passant
        state.en_passant_target = None

        ps.chess_move_used = True
        events: list[Event] = [
            CastlingPerformed(
                player_id=action.player_id,
                side=action.side,
                king_from=king_from,
                king_to=king_to,
                rook_from=rook_from,
                rook_to=rook_to,
            ),
            ChessMoveUsed(player_id=action.player_id),
        ]

        self._update_check_status(state, events)

        # Check for checkmate/stalemate after castling
        opp_id = state.opponent_of(action.player_id)
        opp_ps = state.get_player(opp_id)
        if is_checkmate(state.board, opp_id, state.en_passant_target, opp_ps.castling_rights):
            state.duel = DuelState(
                duel_type=FinalDuelType.SIEGE,
                attacker=action.player_id,
                defender=opp_id,
            )
            state.phase = Phase.FINAL_DUEL
            events.append(FinalDuelTriggered(
                attacker=action.player_id,
                defender=opp_id,
                duel_type=FinalDuelType.SIEGE,
            ))
        elif is_stalemate(state.board, opp_id, state.en_passant_target, opp_ps.castling_rights):
            events.append(StalemateDetected(player_id=opp_id))
            state.duel = DuelState(
                duel_type=FinalDuelType.LAST_STAND,
                attacker=action.player_id,
                defender=opp_id,
            )
            state.phase = Phase.FINAL_DUEL
            events.append(FinalDuelTriggered(
                attacker=action.player_id,
                defender=opp_id,
                duel_type=FinalDuelType.LAST_STAND,
            ))

        if state.phase == Phase.CHESS:
            old = state.phase
            state.phase = Phase.REACTION
            events.append(PhaseAdvanced(
                player_id=action.player_id,
                from_phase=old,
                to_phase=Phase.REACTION,
            ))
        return events

    def _execute_summon_monster(
        self,
        state: GameState,
        action: SummonMonster,
        rng: DeterministicRNG,
        registry: "object | None" = None,
    ) -> list[Event]:
        """
        Stage 5 — full vessel compatibility check + on-summon effects.

        Invariants enforced:
            VESSEL_OWNERSHIP      — only own pieces
            KING_NOT_VESSEL       — King excluded
            VESSEL_COMPATIBILITY  — piece type must be in supported_vessels
        """
        from game.cards.card import MonsterCard
        from game.core.events import MonsterSummoned
        from game.mechanics.monsters import apply_on_summon_effects

        self._require_phase(state, Phase.PREPARATION)
        self._require_no_prep_used(state, action.player_id)
        ps = state.get_player(action.player_id)

        if action.card_id not in ps.hand:
            raise IllegalActionError(f"Card {action.card_id!r} not in hand.")

        unit = state.board.get_unit(action.vessel_position)
        if unit is None:
            raise IllegalActionError(
                f"No piece at vessel position {action.vessel_position}."
            )
        if unit.owner != action.player_id:
            raise IllegalActionError("Cannot transform opponent's piece.")  # VESSEL_OWNERSHIP
        if unit.piece.piece_type == PieceType.KING:
            raise IllegalActionError("King cannot be a Monster vessel.")    # KING_NOT_VESSEL
        if unit.monster_id is not None:
            raise IllegalActionError("Piece is already hosting a Monster.")

        # ── Stage 5: vessel compatibility check via registry ─────────────
        if registry is not None:
            try:
                card = registry.get(action.card_id)
            except KeyError:
                raise IllegalActionError(f"Unknown card: {action.card_id!r}")
            if not isinstance(card, MonsterCard):
                raise IllegalActionError(
                    f"Card {action.card_id!r} is not a Monster card."
                )
            if not card.supports_vessel(unit.piece.piece_type.value):
                raise IllegalActionError(
                    f"Monster {action.card_id!r} cannot use "
                    f"{unit.piece.piece_type.value!r} as a vessel. "
                    f"Supported: {list(card.supported_vessels)}"
                )  # VESSEL_COMPATIBILITY
        else:
            card = None

        unit.monster_id = action.card_id
        ps.hand.remove(action.card_id)

        events: list[Event] = []
        self._mark_prep_used(state, action.player_id, "SummonMonster", events)
        events.append(MonsterSummoned(
            player_id=action.player_id,
            card_id=action.card_id,
            vessel_piece_id=unit.piece.id,
            position=action.vessel_position,
        ))

        # ── Stage 5: on-summon effects ────────────────────────────────────
        if card is not None and isinstance(card, MonsterCard):
            apply_on_summon_effects(state, unit, card, events, rng=rng)

        return events

    def _execute_dismiss_monster(
        self,
        state: GameState,
        action: DismissMonster,
        registry: "object | None" = None,
    ) -> list[Event]:
        """
        Stage 5 — Dismiss a monster from a vessel.

        The monster card is returned to the owner's hand.
        The vessel piece is restored (monster_id cleared, statuses wiped).
        This is a preparation action.
        """
        self._require_phase(state, Phase.PREPARATION)
        self._require_no_prep_used(state, action.player_id)

        unit = state.board.get_unit(action.unit_position)
        if unit is None:
            raise IllegalActionError(f"No piece at {action.unit_position}.")
        if unit.owner != action.player_id:
            raise IllegalActionError("Cannot dismiss an opponent's monster.")
        if unit.monster_id is None:
            raise IllegalActionError("No monster on this piece to dismiss.")

        card_id = unit.monster_id

        # Remove monster from vessel
        unit.monster_id = None
        # Clear all monster-applied statuses (shield, spell_radius_bonus, stealth, etc.)
        unit.statuses = [
            s for s in unit.statuses
            if not any(
                s.startswith(prefix) for prefix in (
                    "shield:", "spell_radius_bonus:", "stealth",
                    "ritual_boost:", "frozen:",
                )
            )
        ]

        # Return card to hand (optional dismiss effect: return to deck instead
        # is reserved for future YAML-driven dismiss_effect field)
        ps = state.get_player(action.player_id)
        ps.hand.append(card_id)

        events: list[Event] = []
        self._mark_prep_used(state, action.player_id, "DismissMonster", events)
        events.append(MonsterDismissed(
            player_id=action.player_id,
            card_id=card_id,
            vessel_piece_id=unit.piece.id,
            position=action.unit_position,
        ))
        return events

    def _execute_activate_monster_ability(
        self,
        state: GameState,
        action: ActivateMonsterAbility,
        rng: DeterministicRNG,
        registry: "object | None" = None,
    ) -> list[Event]:
        """
        Stage 5 — Fire a monster's activated ability.

        Validates that:
          • The action is in Phase.CHESS.
          • The acting player owns the unit.
          • The unit has a monster.
          • The requested ability_id is an activatable effect on that monster.

        Then resolves the effect via the EffectContext/resolve_effect pipeline
        and emits a MonsterAbilityActivated event.
        """
        from game.cards.card import MonsterCard
        from game.mechanics.effects.registry import (
            ACTIVATED_EFFECT_TYPES,
            EffectContext,
            resolve_effect,
        )

        self._require_phase(state, Phase.CHESS)

        unit = state.board.get_unit(action.unit_position)
        if unit is None:
            raise IllegalActionError(f"No piece at {action.unit_position}.")
        if unit.owner != action.player_id:
            raise IllegalActionError("Cannot activate an opponent's monster ability.")
        if unit.monster_id is None:
            raise IllegalActionError("No monster on this piece.")

        if registry is None:
            raise IllegalActionError("No card registry available to resolve abilities.")

        try:
            card = registry.get(unit.monster_id)
        except KeyError:
            raise IllegalActionError(f"Unknown monster card: {unit.monster_id!r}")

        if not isinstance(card, MonsterCard):
            raise IllegalActionError(f"Card {unit.monster_id!r} is not a Monster card.")

        # Find the matching effect
        matching = [
            eff for eff in card.effects
            if eff.type == action.ability_id and eff.type in ACTIVATED_EFFECT_TYPES
        ]
        if not matching:
            raise IllegalActionError(
                f"Monster {unit.monster_id!r} has no activatable ability {action.ability_id!r}."
            )
        effect_entry = matching[0]

        events: list[Event] = []

        ctx = EffectContext(
            state=state,
            effect=effect_entry,
            events=events,
            unit=unit,
            position=action.unit_position,
            card=card,
            rng=rng,
            registry=registry,
            trigger="activated",
            extra={"target": action.target},
        )
        resolve_effect(ctx)

        events.append(MonsterAbilityActivated(
            player_id=action.player_id,
            card_id=unit.monster_id,
            ability_id=action.ability_id,
            unit_position=action.unit_position,
            target=action.target,
        ))
        return events

    def _execute_reposition_unit(
        self,
        state: GameState,
        action: RepositionUnit,
    ) -> list[Event]:
        """
        Stage 5 — resolve a REPOSITION PendingDecision (blade_dancer after-capture).

        Moves the piece from ``from_position`` to ``to_position`` (must be in the
        pending options list).  Clears the PendingDecision afterwards.
        """
        self._require_phase(state, Phase.CHESS)
        pd = state.pending_decision
        if pd is None or pd.decision_type != DecisionType.REPOSITION:
            raise IllegalActionError("No pending Reposition decision.")
        if (action.to_position.file, action.to_position.rank) not in pd.options:
            raise IllegalActionError(
                f"Position {action.to_position} not in reposition options."
            )
        unit = state.board.get_unit(action.from_position)
        if unit is None:
            raise IllegalActionError(f"No piece at {action.from_position}.")
        if unit.owner != action.player_id:
            raise IllegalActionError("Cannot reposition an opponent's piece.")

        state.board.move_unit(action.from_position, action.to_position)
        state.pending_decision = None

        from game.core.events import PhaseAdvanced
        events: list[Event] = [PieceMoved(
            piece_id=unit.piece.id,
            owner=unit.owner,
            source=action.from_position,
            target=action.to_position,
        )]

        # Reposition resolves the CHESS sub-turn — now advance to REACTION.
        state.phase = Phase.REACTION
        events.append(PhaseAdvanced(
            player_id=action.player_id,
            from_phase=Phase.CHESS,
            to_phase=Phase.REACTION,
        ))
        return events

    def _execute_activate_spell(
        self,
        state: GameState,
        action: ActivateSpell,
    ) -> list[Event]:
        from game.core.events import SpellActivated

        self._require_phase(state, Phase.PREPARATION)
        self._require_no_prep_used(state, action.player_id)
        ps = state.get_player(action.player_id)

        if action.card_id not in ps.hand:
            raise IllegalActionError(f"Card {action.card_id!r} not in hand.")

        ps.hand.remove(action.card_id)
        ps.graveyard.append(action.card_id)

        events: list[Event] = []
        self._mark_prep_used(state, action.player_id, "ActivateSpell", events)
        events.append(SpellActivated(
            player_id=action.player_id,
            card_id=action.card_id,
            target=action.target,
        ))
        # Effect resolution deferred to Stage 6 EffectResolver.
        return events

    def _execute_place_trap(
        self,
        state: GameState,
        action: PlaceTrap,
    ) -> list[Event]:
        from game.core.events import TrapPlaced

        self._require_phase(state, Phase.PREPARATION)
        self._require_no_prep_used(state, action.player_id)
        ps = state.get_player(action.player_id)

        if action.card_id not in ps.hand:
            raise IllegalActionError(f"Card {action.card_id!r} not in hand.")

        ps.hand.remove(action.card_id)

        trap_id = f"trap-{action.player_id}-{len(state.traps)+1:03d}"
        trap = TrapInstance(
            id=trap_id,
            owner=action.player_id,
            card_id=action.card_id,
            position=action.position,
            radius=1,           # default; overridden by card data in Stage 6
            trigger_condition="enter_radius",
        )
        state.traps.append(trap)

        events: list[Event] = []
        self._mark_prep_used(state, action.player_id, "PlaceTrap", events)
        events.append(TrapPlaced(
            player_id=action.player_id,
            card_id=action.card_id,
            trap_instance_id=trap_id,
            position=action.position,
            radius=trap.radius,
        ))
        return events

    def _execute_start_construction(
        self,
        state: GameState,
        action: StartConstruction,
    ) -> list[Event]:
        from game.core.events import ConstructionStarted
        from game.core.phases import ConstructionStatus
        from game.core.state import BuildingInstance

        self._require_phase(state, Phase.PREPARATION)
        self._require_no_prep_used(state, action.player_id)

        unit = state.board.get_unit(action.pawn_position)
        if unit is None:
            raise IllegalActionError(f"No piece at {action.pawn_position}.")
        if unit.owner != action.player_id:
            raise IllegalActionError("Cannot use opponent's Pawn to build.")
        if unit.piece.piece_type != PieceType.PAWN:
            raise IllegalActionError("Only Pawns can build.")              # BUILDER_IS_PAWN
        if not unit.builder_available:
            raise IllegalActionError("This Pawn has already completed a building.")  # BUILDER_ONCE

        ps = state.get_player(action.player_id)
        entry = next(
            (e for e in ps.building_pool if e.building_card_id == action.building_card_id),
            None,
        )
        if entry is None or entry.copies_available <= 0:
            raise IllegalActionError(
                f"Building {action.building_card_id!r} not available in pool."
            )
        entry.copies_available -= 1

        building_id = f"bld-{action.player_id}-{len(state.buildings)+1:03d}"
        building = BuildingInstance(
            id=building_id,
            owner=action.player_id,
            building_card_id=action.building_card_id,
            position=action.pawn_position,
            status=ConstructionStatus.UNDER_CONSTRUCTION,
            builder_piece_id=unit.piece.id,
            remaining_turns=2,  # default; overridden by building definition Stage 8
        )
        state.buildings.append(building)

        events: list[Event] = []
        self._mark_prep_used(state, action.player_id, "StartConstruction", events)
        events.append(ConstructionStarted(
            player_id=action.player_id,
            building_card_id=action.building_card_id,
            building_instance_id=building_id,
            position=action.pawn_position,
            builder_piece_id=unit.piece.id,
        ))
        return events

    def _execute_coronate_king(
        self,
        state: GameState,
        action: CoronateKing,
    ) -> list[Event]:
        from game.core.events import KingCoronated

        self._require_phase(state, Phase.PREPARATION)
        self._require_no_prep_used(state, action.player_id)
        ps = state.get_player(action.player_id)

        if ps.is_in_check():
            raise IllegalActionError("Cannot Coronate while in check.")

        kcs = next(
            (k for k in ps.king_pool if k.king_card_id == action.king_card_id),
            None,
        )
        if kcs is None:
            raise IllegalActionError(f"King card {action.king_card_id!r} not in pool.")
        if kcs.status != KingCardStatus.HIDDEN:
            raise IllegalActionError(f"King card {action.king_card_id!r} is already active or retired.")

        kcs.status = KingCardStatus.ACTIVE
        ps.active_king = action.king_card_id

        events: list[Event] = []
        self._mark_prep_used(state, action.player_id, "CoronateKing", events)
        events.append(KingCoronated(player_id=action.player_id, king_card_id=action.king_card_id))
        return events

    def _execute_change_king(
        self,
        state: GameState,
        action: ChangeKing,
    ) -> list[Event]:
        from game.core.events import KingSuccession

        self._require_phase(state, Phase.PREPARATION)
        self._require_no_prep_used(state, action.player_id)
        ps = state.get_player(action.player_id)

        if ps.is_in_check():
            raise IllegalActionError("Cannot perform Succession while in check.")
        if ps.active_king is None:
            raise IllegalActionError("No active King to succeed. Use CoronateKing first.")

        old_kcs = next(
            (k for k in ps.king_pool if k.king_card_id == ps.active_king), None
        )
        new_kcs = next(
            (k for k in ps.king_pool if k.king_card_id == action.king_card_id), None
        )
        if new_kcs is None or new_kcs.status != KingCardStatus.HIDDEN:
            raise IllegalActionError(f"King card {action.king_card_id!r} unavailable for succession.")

        # Retire old king
        if old_kcs:
            old_kcs.status = KingCardStatus.RETIRED
            ps.retired_kings.append(ps.active_king)

        new_kcs.status = KingCardStatus.ACTIVE
        old_id = ps.active_king
        ps.active_king = action.king_card_id

        events: list[Event] = []
        self._mark_prep_used(state, action.player_id, "ChangeKing", events)
        events.append(KingSuccession(
            player_id=action.player_id,
            retired_king_card_id=old_id,
            new_king_card_id=action.king_card_id,
        ))
        return events

    def _execute_declare_recompose(
        self,
        state: GameState,
        action: DeclareRecompose,
        rng: DeterministicRNG,
    ) -> list[Event]:
        self._require_phase(state, Phase.PREPARATION)
        self._require_no_prep_used(state, action.player_id)
        ps = state.get_player(action.player_id)

        if not ps.hand:
            raise IllegalActionError("Cannot Recompose with empty hand.")

        n = rng.recompose_count()
        n = min(n, len(ps.hand))  # can't return more than you have

        state.pending_decision = PendingDecision(
            player_id=action.player_id,
            decision_type=DecisionType.RECOMPOSE_CARDS,
            options=list(ps.hand),
            min_choices=n,
            max_choices=n,
        )
        state.phase = Phase.RECOMPOSE_SELECTION

        events: list[Event] = []
        self._mark_prep_used(state, action.player_id, "DeclareRecompose", events)
        events.append(RecomposeInitiated(
            player_id=action.player_id,
            required_count=n,
        ))
        return events

    def _execute_select_recompose(
        self,
        state: GameState,
        action: SelectRecomposeCards,
        rng: DeterministicRNG,
    ) -> list[Event]:
        from game.core.events import CardsReturnedToDeck, RecomposeResolved

        self._require_phase(state, Phase.RECOMPOSE_SELECTION)
        pd = state.pending_decision
        if pd is None or pd.decision_type != DecisionType.RECOMPOSE_CARDS:
            raise IllegalActionError("No pending Recompose decision.")

        n = pd.min_choices
        if len(action.card_ids) != n:
            raise IllegalActionError(
                f"Must return exactly {n} cards; got {len(action.card_ids)}."
            )

        ps = state.get_player(action.player_id)
        for cid in action.card_ids:
            if cid not in ps.hand:
                raise IllegalActionError(f"Card {cid!r} not in hand.")

        # Return cards to deck, shuffle, draw replacements
        for cid in action.card_ids:
            ps.hand.remove(cid)
            ps.deck.append(cid)
        rng.shuffle(ps.deck)

        drawn: list[str] = []
        for _ in range(n):
            if ps.deck:
                card = ps.deck.pop(0)
                ps.hand.append(card)
                drawn.append(card)

        state.pending_decision = None
        state.phase = Phase.CHESS  # Advance to chess phase after recompose

        events: list[Event] = [
            CardsReturnedToDeck(
                player_id=action.player_id,
                card_ids=tuple(action.card_ids),
            ),
            RecomposeResolved(
                player_id=action.player_id,
                returned_card_ids=tuple(action.card_ids),
                drawn_card_ids=tuple(drawn),
            ),
        ]
        return events

    def _execute_promote_pawn(
        self,
        state: GameState,
        action: PromotePawn,
    ) -> list[Event]:
        self._require_phase(state, Phase.PROMOTION_SELECTION)
        pd = state.pending_decision
        if pd is None or pd.decision_type != DecisionType.PROMOTION:
            raise IllegalActionError("No pending promotion decision.")

        unit = state.board.get_unit(action.position)
        if unit is None:
            raise IllegalActionError(f"No unit at {action.position} to promote.")

        old_id, new_id = apply_promotion(state.board, action.position, action.piece_type)

        events: list[Event] = [
            PiecePromoted(
                piece_id=old_id,
                owner=unit.owner,
                position=action.position,
                from_type="pawn",
                to_type=action.piece_type,
                new_piece_id=new_id,
            )
        ]
        state.pending_decision = None
        state.phase = Phase.REACTION
        return events

    def _execute_end_preparation(
        self,
        state: GameState,
        action: EndPreparation,
    ) -> list[Event]:
        self._require_phase(state, Phase.PREPARATION)
        events: list[Event] = []
        old = state.phase
        state.phase = Phase.CHESS
        events.append(PhaseAdvanced(
            player_id=action.player_id,
            from_phase=old,
            to_phase=Phase.CHESS,
        ))
        return events

    def _execute_discard_card(
        self,
        state: GameState,
        action: DiscardCard,
    ) -> list[Event]:
        """Discard one card from hand to graveyard during Phase.DISCARD."""
        self._require_phase(state, Phase.DISCARD)
        ps = state.get_player(action.player_id)
        if action.card_id not in ps.hand:
            raise IllegalActionError(
                f"Card {action.card_id!r} is not in {action.player_id}'s hand."
            )
        ps.hand.remove(action.card_id)
        ps.graveyard.append(action.card_id)
        events: list[Event] = [
            CardDiscarded(player_id=action.player_id, card_id=action.card_id)
        ]
        # If hand is now within limit, leave DISCARD phase → END
        if len(ps.hand) <= HAND_SIZE_LIMIT:
            old = state.phase
            state.phase = Phase.END
            events.append(PhaseAdvanced(
                player_id=action.player_id,
                from_phase=old,
                to_phase=Phase.END,
            ))
        return events

    def _execute_end_turn(
        self,
        state: GameState,
        action: EndTurn,
    ) -> list[Event]:
        # Allow from END, REACTION, CHESS (no moves), or DISCARD (after discarding to ≤ limit)
        self._require_phase(state, Phase.END, Phase.REACTION, Phase.CHESS, Phase.DISCARD)
        ps = state.get_player(action.player_id)
        events: list[Event] = []

        # Hand-size enforcement: if hand > limit, redirect to DISCARD phase
        if len(ps.hand) > HAND_SIZE_LIMIT:
            old = state.phase
            state.phase = Phase.DISCARD
            events.append(PhaseAdvanced(
                player_id=action.player_id,
                from_phase=old,
                to_phase=Phase.DISCARD,
            ))
            return events

        events.append(TurnEnded(
            player_id=action.player_id,
            turn_number=state.turn_number,
        ))

        # ── Stage 5: tick down frozen squares ────────────────────────────
        # Effect format: "frozen:<turns>:<owner_player_id>"
        # Decrement only when the player who DIDN'T set the freeze ends their
        # turn (i.e., the opponent of the freezer calls EndTurn).  This ensures
        # the freeze blocks for exactly <turns> full opponent turns.
        ending_player = action.player_id
        for sq in state.board.squares.values():
            remaining = []
            for eff in sq.temporary_effects:
                if eff.startswith("frozen:"):
                    parts = eff.split(":")
                    n = int(parts[1])
                    owner = parts[2] if len(parts) > 2 else None
                    # Decrement only if the player ending their turn is NOT the owner
                    if owner is None or owner != ending_player:
                        n -= 1
                    if n > 0:
                        owner_tag = f":{owner}" if owner else ""
                        remaining.append(f"frozen:{n}{owner_tag}")
                    # else: expired, don't add back
                else:
                    remaining.append(eff)
            sq.temporary_effects = remaining

        # Switch to other player
        opponent = state.opponent_of(action.player_id)
        state.active_player = opponent

        # Advance turn counter after both players have moved
        players = list(state.players.keys())
        if action.player_id == players[-1]:
            state.turn_number += 1

        # Begin next player's START → DRAW → PREPARATION sequence
        state.phase = Phase.START
        # (The auto-start is triggered by the next call from the game loop)

        return events

    # ── Castling rights helpers (Stage 1) ────────────────────────────────

    def _update_castling_rights(
        self,
        state: GameState,
        player_id: str,
        unit: UnitInstance,
        source: Position,
    ) -> None:
        """
        Revoke castling rights after a King or Rook move.
        Called after every successful MovePiece.
        """
        ps = state.get_player(player_id)
        cr = ps.castling_rights
        rank = 0 if player_id == "white" else 7

        if unit.piece.piece_type == PieceType.KING:
            cr.revoke_all()
        elif unit.piece.piece_type == PieceType.ROOK:
            if source == Position(0, rank):   # a-file rook
                cr.queenside = False
            elif source == Position(7, rank): # h-file rook
                cr.kingside = False

    def _revoke_castling_rights_for_captured_rook(
        self,
        state: GameState,
        captured_unit: UnitInstance,
        captured_at: Position,
    ) -> None:
        """
        If a Rook was captured, revoke the opponent's castling right for that side.
        Called when a piece capture event is detected.
        """
        if captured_unit.piece.piece_type != PieceType.ROOK:
            return
        opp_id = captured_unit.owner
        opp_ps = state.get_player(opp_id)
        rank = 0 if opp_id == "white" else 7
        if captured_at == Position(0, rank):
            opp_ps.castling_rights.queenside = False
        elif captured_at == Position(7, rank):
            opp_ps.castling_rights.kingside = False

    @staticmethod
    def _compute_en_passant_target(
        unit: UnitInstance,
        source: Position,
        target: Position,
    ) -> "Position | None":
        """
        Return the en passant target square if the move was a double pawn push,
        otherwise None.  Clears en passant for any other move type.
        """
        if unit.piece.piece_type != PieceType.PAWN:
            return None
        if abs(target.rank - source.rank) == 2:
            # Double push — en passant target is the square the pawn passed through
            ep_rank = (source.rank + target.rank) // 2
            return Position(source.file, ep_rank)
        return None

    # ── Check status update ──────────────────────────────────────────────

    def _update_check_status(
        self,
        state: GameState,
        events: list[Event],
    ) -> None:
        """Recompute and cache check flags after a board change."""
        for pid in state.players:
            ps = state.get_player(pid)
            was_in_check = ps.is_in_check()
            now_in_check = is_in_check(state.board, pid)
            ps.set_check(now_in_check)
            if not was_in_check and now_in_check:
                events.append(CheckDetected(player_id=pid))
            elif was_in_check and not now_in_check:
                events.append(CheckResolved(player_id=pid))
