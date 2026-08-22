"""
Telemetry and Balance Instrumentation — README §42
====================================================

Covers:
    • Every §42 metric exists on MatchStats / PlayerMatchStats.
    • The live recorder counts real gameplay correctly (draws, summons,
      spells, traps, captures, buildings, rituals, kings, checks, duel).
    • Telemetry never influences the game (same seed → same match with
      recording on and off).
    • Cross-match aggregation: first-player / archetype / King policy
      win rates, CSV + JSON export.
"""

from __future__ import annotations

import json

import pytest

from game.chess.pieces import Position as P
from game.core.actions import (
    DiscardCard,
    EndPreparation,
    EndTurn,
    MovePiece,
    SummonMonster,
)
from game.core.game import Game
from game.core.phases import Phase
from game.telemetry import (
    MatchStats,
    MatchTelemetry,
    PlayerMatchStats,
    TelemetryAggregator,
    piece_type_from_id,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _end_turn(game: Game, player: str) -> None:
    """EndTurn, resolving any forced discards on the way out."""
    while True:
        game.execute(EndTurn(player_id=player))
        if game.state.phase == Phase.DISCARD and game.state.active_player == player:
            card = game.state.get_player(player).hand[0]
            game.execute(DiscardCard(player_id=player, card_id=card))
            continue
        break


def _move(game: Game, src: str, dst: str, player: str) -> None:
    game.execute(EndPreparation(player_id=player))
    game.execute(MovePiece(player_id=player, source=P.from_algebraic(src),
                           target=P.from_algebraic(dst)))
    _end_turn(game, player)


def _scholars_mate(game: Game) -> None:
    """Deterministic fast checkmate → SIEGE Final Duel trigger."""
    _move(game, "e2", "e4", "white")
    _move(game, "e7", "e5", "black")
    _move(game, "d1", "h5", "white")
    _move(game, "b8", "c6", "black")
    _move(game, "f1", "c4", "white")
    _move(game, "g8", "f6", "black")
    game.execute(EndPreparation(player_id="white"))
    game.execute(MovePiece(player_id="white", source=P.from_algebraic("h5"),
                           target=P.from_algebraic("f7")))


# ─────────────────────────────────────────────────────────────────────────────
# README §42 coverage — every named metric has a home
# ─────────────────────────────────────────────────────────────────────────────

class TestReadme42Coverage:
    MATCH_FIELDS = [
        "duration_seconds",       # match duration
        "turn_count",             # turn count
        "first_check_turn",       # first check turn
        "total_checks",           # number of checks
        "final_duel_trigger",     # Final Duel trigger type
        "final_duel_rounds",      # Final Duel duration
        "final_duel_winner",      # Final Duel winner
    ]

    PLAYER_FIELDS = [
        "cards_played",
        "cards_remaining",
        "recomposes_used",
        "pawns_captured",
        "pawns_sacrificed",
        "pawns_used_as_vessels",
        "pawns_used_for_construction",
        "buildings_built",
        "buildings_destroyed",
        "rituals_revealed",
        "rituals_attempted",
        "rituals_completed",
        "king_coronation_turn",
        "king_successions",
        "royal_support_score",
    ]

    @pytest.mark.parametrize("field_name", MATCH_FIELDS)
    def test_match_level_metric_exists(self, field_name):
        stats = MatchStats(game_id="g", seed=0)
        assert hasattr(stats, field_name)

    @pytest.mark.parametrize("field_name", PLAYER_FIELDS)
    def test_player_level_metric_exists(self, field_name):
        assert hasattr(PlayerMatchStats(player_id="white"), field_name)

    def test_rate_metrics_exist_on_the_aggregator(self):
        agg = TelemetryAggregator()
        assert agg.first_player_win_rate is None          # first-player win rate
        assert agg.archetype_win_rates() == {}            # archetype win rate
        assert agg.king_policy_win_rates() == {}          # King policy win rate


# ─────────────────────────────────────────────────────────────────────────────
# piece_type_from_id
# ─────────────────────────────────────────────────────────────────────────────

class TestPieceTypeFromId:
    @pytest.mark.parametrize("piece_id,expected", [
        ("white-pawn-a2", "pawn"),
        ("black-queen", "queen"),
        ("white-knight-b1", "knight"),
        ("black-king", "king"),
        (None, None),
        ("", None),
        ("white-golem-thing", None),
    ])
    def test_extracts_type(self, piece_id, expected):
        assert piece_type_from_id(piece_id) == expected


# ─────────────────────────────────────────────────────────────────────────────
# Live recording against a real game
# ─────────────────────────────────────────────────────────────────────────────

class TestLiveRecording:
    def test_new_game_has_a_recorder(self):
        game = Game.new(seed=1)
        assert isinstance(game.telemetry, MatchTelemetry)
        assert game.telemetry.enabled is True
        assert game.telemetry.game_id == game.state.game_id
        assert game.telemetry.seed == 1

    def test_snapshot_reports_both_players(self):
        game = Game.new(seed=1)
        stats = game.match_stats()
        assert set(stats.players) == {"white", "black"}
        assert stats.first_player == "white"

    def test_cards_remaining_tracks_hand_plus_deck(self):
        game = Game.new(seed=1)
        game.advance_to_preparation()
        stats = game.match_stats()
        white = game.state.get_player("white")
        assert stats.players["white"].hand_remaining == len(white.hand)
        assert stats.players["white"].deck_remaining == len(white.deck)
        assert stats.players["white"].cards_remaining == len(white.hand) + len(white.deck)

    def test_draw_is_counted(self):
        # Game.new deals the opening hands directly and starts at
        # PREPARATION, so white's first DRAW phase is on turn 2.
        game = Game.new(seed=1)
        _move(game, "e2", "e4", "white")
        _move(game, "e7", "e5", "black")       # black's turn-1 draw
        game.advance_to_preparation()          # white's turn-2 draw
        stats = game.match_stats()
        assert stats.players["white"].cards_drawn >= 1
        assert stats.players["black"].cards_drawn >= 1

    def test_actions_and_plies_are_counted(self):
        game = Game.new(seed=1)
        _move(game, "e2", "e4", "white")
        stats = game.match_stats()
        assert stats.action_count >= 3         # EndPreparation, MovePiece, EndTurn
        assert stats.ply_count == 1

    def test_capture_credits_both_sides(self):
        game = Game.new(seed=1)
        _move(game, "e2", "e4", "white")
        _move(game, "d7", "d5", "black")
        _move(game, "e4", "d5", "white")       # white pawn takes black pawn
        stats = game.match_stats()
        assert stats.players["black"].pawns_captured == 1
        assert stats.players["black"].pieces_captured == 1
        assert stats.players["white"].pieces_taken == 1
        assert stats.players["white"].pawns_captured == 0

    def test_summon_counts_a_card_played_and_a_pawn_vessel(self, registry):
        game = Game.new(seed=3, registry=registry,
                        white_deck=["stone_golem"] * 20)
        game.advance_to_preparation()
        # stone_golem's vessels include pawn/rook — find any legal summon
        # on a Pawn so the Vessel metric is exercised.
        summons = [
            a for a in game.get_legal_actions("white")
            if isinstance(a, SummonMonster)
        ]
        pawn_summons = [
            a for a in summons
            if game.state.board.get_unit(a.vessel_position).piece.piece_type.value == "pawn"
        ]
        if not pawn_summons:
            pytest.skip("no Pawn vessel available for stone_golem in this position")
        game.execute(pawn_summons[0])
        stats = game.match_stats()
        assert stats.players["white"].monsters_summoned == 1
        assert stats.players["white"].cards_played == 1
        assert stats.players["white"].pawns_used_as_vessels == 1

    def test_check_metrics(self):
        game = Game.new(seed=1)
        _move(game, "e2", "e4", "white")
        _move(game, "e7", "e5", "black")
        _move(game, "d1", "h5", "white")
        _move(game, "b8", "c6", "black")
        _move(game, "f1", "c4", "white")
        _move(game, "g8", "f6", "black")
        game.execute(EndPreparation(player_id="white"))
        game.execute(MovePiece(player_id="white", source=P.from_algebraic("h5"),
                               target=P.from_algebraic("f7")))
        stats = game.match_stats()
        assert stats.total_checks >= 1
        assert stats.first_check_turn is not None
        assert stats.players["black"].checks_received >= 1
        assert stats.players["white"].checks_delivered >= 1

    def test_final_duel_metrics(self):
        game = Game.new(seed=1)
        _scholars_mate(game)
        assert game.state.phase == Phase.FINAL_DUEL
        stats = game.match_stats()
        assert stats.final_duel_triggered is True
        assert stats.final_duel_trigger == "siege"
        assert stats.final_duel_attacker == "white"
        assert stats.final_duel_defender == "black"
        # Royal Support is snapshotted at Duel start (README §27).
        assert stats.players["black"].duel_support_items >= 0
        assert stats.players["black"].royal_support_score >= 0

    def test_illegal_action_is_counted(self):
        from game.core.rules import IllegalActionError

        game = Game.new(seed=1)
        game.advance_to_preparation()
        with pytest.raises(IllegalActionError):
            # Black cannot act on white's turn.
            game.execute(EndPreparation(player_id="black"))
        assert game.match_stats().illegal_action_count == 1


# ─────────────────────────────────────────────────────────────────────────────
# Telemetry must never change the game
# ─────────────────────────────────────────────────────────────────────────────

class TestTelemetryIsPassive:
    def test_disabled_recorder_records_nothing(self):
        game = Game.new(seed=1, telemetry=False)
        _move(game, "e2", "e4", "white")
        stats = game.match_stats()
        assert stats.action_count == 0
        assert stats.players["white"].cards_drawn == 0
        # ... but end-of-match resources are still reported.
        assert stats.players["white"].cards_remaining > 0

    def test_same_seed_same_game_with_and_without_telemetry(self):
        def play(telemetry: bool) -> tuple:
            game = Game.new(seed=7, telemetry=telemetry)
            _move(game, "e2", "e4", "white")
            _move(game, "e7", "e5", "black")
            _move(game, "g1", "f3", "white")
            return (
                tuple(game.state.get_player("white").hand),
                tuple(game.state.get_player("black").hand),
                game.state.turn_number,
                game.state.active_player,
                len(game.state.event_log),
            )

        assert play(True) == play(False)


# ─────────────────────────────────────────────────────────────────────────────
# Aggregation
# ─────────────────────────────────────────────────────────────────────────────

def _stats(
    game_id: str,
    winner: str | None,
    white_archetype: str = "warrior",
    black_archetype: str = "spellcaster",
    white_king: str = "marshal_king",
    black_king: str = "shadow_regent",
    turn_count: int = 20,
) -> MatchStats:
    return MatchStats(
        game_id=game_id,
        seed=0,
        winner=winner,
        turn_count=turn_count,
        players={
            "white": PlayerMatchStats(
                player_id="white", archetype=white_archetype,
                active_king=white_king, cards_played=4,
            ),
            "black": PlayerMatchStats(
                player_id="black", archetype=black_archetype,
                active_king=black_king, cards_played=2,
            ),
        },
    )


class TestAggregator:
    def test_first_player_win_rate(self):
        agg = TelemetryAggregator()
        agg.extend([
            _stats("a", "white"),
            _stats("b", "white"),
            _stats("c", "black"),
            _stats("d", "black"),
        ])
        assert agg.first_player_win_rate == 0.5

    def test_undecided_matches_are_excluded_from_win_rates(self):
        agg = TelemetryAggregator()
        agg.extend([_stats("a", "white"), _stats("b", None)])
        assert len(agg) == 2
        assert len(agg.decided) == 1
        assert agg.first_player_win_rate == 1.0
        assert agg.summary()["unfinished"] == 1

    def test_archetype_win_rate(self):
        agg = TelemetryAggregator()
        agg.extend([_stats("a", "white"), _stats("b", "white"), _stats("c", "black")])
        rates = agg.archetype_win_rates()
        assert rates["warrior"].wins == 2
        assert rates["warrior"].matches == 3
        assert rates["spellcaster"].wins == 1
        assert rates["spellcaster"].win_rate == pytest.approx(1 / 3, abs=1e-4)

    def test_king_policy_win_rate(self):
        agg = TelemetryAggregator()
        agg.extend([_stats("a", "white"), _stats("b", "black")])
        rates = agg.king_policy_win_rates()
        assert rates["marshal_king"].wins == 1
        assert rates["shadow_regent"].wins == 1

    def test_means(self):
        agg = TelemetryAggregator()
        agg.extend([_stats("a", "white", turn_count=10),
                    _stats("b", "black", turn_count=30)])
        assert agg.mean("turn_count") == 20.0
        assert agg.player_mean("cards_played") == 3.0
        assert agg.player_mean("cards_played", "white") == 4.0

    def test_summary_is_json_serialisable(self):
        agg = TelemetryAggregator()
        agg.add(_stats("a", "white"))
        parsed = json.loads(agg.to_json())
        assert parsed["matches"] == 1
        assert parsed["first_player_win_rate"] == 1.0
        assert "archetype_win_rates" in parsed

    def test_write_csv(self, tmp_path):
        agg = TelemetryAggregator()
        agg.extend([_stats("a", "white"), _stats("b", "black")])
        out = tmp_path / "matches.csv"
        agg.write_csv(str(out))
        lines = out.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 3                      # header + 2 rows
        assert "white_cards_played" in lines[0]
        assert "black_active_king" in lines[0]

    def test_write_csv_with_no_matches_is_a_noop(self, tmp_path):
        out = tmp_path / "empty.csv"
        TelemetryAggregator().write_csv(str(out))
        assert not out.exists()


class TestMatchStatsSerialisation:
    def test_to_dict_includes_players_and_derived_flag(self):
        d = _stats("a", "white").to_dict()
        assert d["first_player_won"] is True
        assert d["players"]["white"]["cards_played"] == 4

    def test_first_player_won_is_none_without_a_winner(self):
        assert _stats("a", None).first_player_won is None

    def test_flat_row_prefixes_player_keys(self):
        row = _stats("a", "white").flat_row()
        assert row["white_cards_played"] == 4
        assert row["black_archetype"] == "spellcaster"
        assert "players" not in row
