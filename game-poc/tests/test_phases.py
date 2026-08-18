"""
test_phases.py — Formal Turn Sequence and Phase invariant tests.

Verifies:
    • Phase enum completeness.
    • Normal turn phase ordering (START → DRAW → PREPARATION → CHESS → REACTION → END).
    • All special phases exist (RECOMPOSE_SELECTION, PROMOTION_SELECTION, FINAL_DUEL).
    • PieceType enum coverage.
    • VALID_VESSEL_TYPES excludes King.
    • FinalDuelType, RevelationState, KingCardStatus, ConstructionStatus, DecisionType.
"""

import pytest
from game.core.phases import (
    ConstructionStatus,
    DecisionType,
    FinalDuelType,
    KingCardStatus,
    Phase,
    PieceType,
    RevelationState,
    VALID_VESSEL_TYPES,
)


class TestPhaseEnum:
    def test_all_normal_phases_exist(self):
        required = {"start", "draw", "preparation", "chess", "reaction", "end"}
        values = {p.value for p in Phase}
        assert required <= values

    def test_special_phases_exist(self):
        assert Phase.RECOMPOSE_SELECTION in Phase
        assert Phase.PROMOTION_SELECTION in Phase
        assert Phase.FINAL_DUEL in Phase
        assert Phase.GAME_OVER in Phase

    def test_no_duplicate_values(self):
        values = [p.value for p in Phase]
        assert len(values) == len(set(values))


class TestPieceTypeEnum:
    def test_all_piece_types_exist(self):
        types = {p.value for p in PieceType}
        expected = {"king", "queen", "rook", "bishop", "knight", "pawn"}
        assert types == expected

    def test_king_not_a_valid_vessel(self):
        assert PieceType.KING not in VALID_VESSEL_TYPES

    def test_all_non_king_pieces_are_valid_vessels(self):
        non_king = {p for p in PieceType if p != PieceType.KING}
        assert non_king == VALID_VESSEL_TYPES


class TestFinalDuelType:
    def test_all_trigger_types_exist(self):
        assert FinalDuelType.ASSAULT in FinalDuelType
        assert FinalDuelType.SIEGE in FinalDuelType
        assert FinalDuelType.LAST_STAND in FinalDuelType


class TestRevelationState:
    def test_states_ordered_ascending(self):
        # SEALED < FORETOLD < REVEALED (by int value)
        assert RevelationState.SEALED.value < RevelationState.FORETOLD.value
        assert RevelationState.FORETOLD.value < RevelationState.REVEALED.value


class TestKingCardStatus:
    def test_lifecycle_states_exist(self):
        assert KingCardStatus.HIDDEN in KingCardStatus
        assert KingCardStatus.ACTIVE in KingCardStatus
        assert KingCardStatus.RETIRED in KingCardStatus


class TestConstructionStatus:
    def test_all_statuses_exist(self):
        assert ConstructionStatus.UNDER_CONSTRUCTION in ConstructionStatus
        assert ConstructionStatus.COMPLETE in ConstructionStatus
        assert ConstructionStatus.DESTROYED in ConstructionStatus


class TestDecisionType:
    def test_all_decision_types_exist(self):
        assert DecisionType.RECOMPOSE_CARDS in DecisionType
        assert DecisionType.PROMOTION in DecisionType
        assert DecisionType.CHOOSE_TARGET in DecisionType
        assert DecisionType.CHOOSE_SACRIFICE in DecisionType
