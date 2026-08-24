"""
test_ai_stage6_analysis.py — corpus analysis (README §53).

§53's first goals for self-play are counting questions, not learning ones:
evaluate balance, estimate card strength, discover Ritual patterns, find
abusive Building combinations, find dominant King succession paths.

The thing worth testing hardest is not the arithmetic but the *claim*: card
strength is conditioned on the deal, because the deck is randomised and
what a bot chooses to play is not.  A test that only checked the means
would pass just as happily on the contaminated version.

Corpora here are fabricated rather than played, so a known effect can be
planted and then looked for.
"""

from __future__ import annotations

import json

import pytest

from game import analysis
from game.analysis import (
    MIN_SAMPLES,
    PlayerMatch,
    Rate,
    Split,
    balance,
    card_strength,
    full_report,
    load_player_matches,
    render_text,
    set_thresholds,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fabricating a corpus
# ─────────────────────────────────────────────────────────────────────────────

def _player(deck, played=(), buildings=(), rituals=(),
            archetype="warrior", king="marshal_king", kings=()):
    return {
        "archetype": archetype,
        "active_king": king,
        "kings_played": list(kings or ([king] if king else [])),
        "deck_card_ids": list(deck),
        "cards_played_by_id": {c: 1 for c in played},
        "buildings_built_by_id": {b: 1 for b in buildings},
        "rituals_completed_by_id": {r: 1 for r in rituals},
    }


def _match(seed, winner, white, black, completed=True,
           end_reason="final_duel_victory", turns=50):
    return {
        "seed": seed,
        "white": "heuristic",
        "black": "heuristic",
        "completed": completed,
        "winner": winner,
        "end_reason": end_reason,
        "turns": turns,
        "final_duel": True,
        "final_duel_trigger": "assault",
        "telemetry": {"players": {"white": white, "black": black}},
    }


def _write(tmp_path, matches, name="matches-0000.jsonl"):
    corpus = tmp_path / "corpus"
    corpus.mkdir(exist_ok=True)
    with open(corpus / name, "w", encoding="utf-8") as fh:
        for m in matches:
            fh.write(json.dumps(m) + "\n")
    return corpus


@pytest.fixture(autouse=True)
def _restore_thresholds():
    """Each test gets the module defaults back, whatever it moved them to."""
    yield
    set_thresholds(30, 2.0)


# ─────────────────────────────────────────────────────────────────────────────
# Rates
# ─────────────────────────────────────────────────────────────────────────────

class TestRate:
    def test_a_draw_is_half_a_win(self):
        rate = Rate()
        for score in (1.0, 0.0, 0.5, 0.5):
            rate.add(score)
        assert rate.n == 4
        assert rate.score == 0.5
        assert rate.wins == 1 and rate.draws == 2

    def test_a_certain_outcome_has_no_error(self):
        rate = Rate()
        for _ in range(50):
            rate.add(1.0)
        assert rate.score == 1.0
        assert rate.stderr == 0.0

    def test_error_shrinks_as_the_sample_grows(self):
        small, large = Rate(), Rate()
        for i in range(20):
            small.add(float(i % 2))
        for i in range(2000):
            large.add(float(i % 2))
        assert large.stderr < small.stderr / 5

    def test_an_empty_rate_is_harmless(self):
        rate = Rate()
        assert rate.score == 0.0 and rate.stderr == 0.0
        assert rate.as_dict()["n"] == 0


class TestSplit:
    def _split(self, with_scores, without_scores):
        split = Split(key="x")
        for s in with_scores:
            split.with_.add(s)
        for s in without_scores:
            split.without.add(s)
        return split

    def test_delta_is_with_minus_without(self):
        split = self._split([1.0] * 40, [0.0] * 40)
        assert split.delta == 1.0

    def test_a_big_clean_difference_is_significant(self):
        split = self._split([1.0] * 40 + [0.0] * 10, [0.0] * 40 + [1.0] * 10)
        assert split.significant

    def test_a_small_sample_is_never_significant(self):
        """Ten observations cannot establish anything, however clean."""
        split = self._split([1.0] * 10, [0.0] * 10)
        assert split.with_.n < MIN_SAMPLES
        assert not split.significant

    def test_perfect_separation_is_maximally_significant(self):
        """
        Zero spread in both groups is certainty, not ignorance — the naive
        formula divides by a zero error and would call it noise.
        """
        split = self._split([1.0] * 40, [0.0] * 40)
        assert split.stderr == 0.0
        assert split.sigmas == analysis.MAX_SIGMAS
        assert split.significant

    def test_identical_groups_are_not_significant(self):
        split = self._split([1.0, 0.0] * 40, [1.0, 0.0] * 40)
        assert split.delta == 0.0
        assert not split.significant


# ─────────────────────────────────────────────────────────────────────────────
# Loading
# ─────────────────────────────────────────────────────────────────────────────

class TestLoading:
    def test_both_sides_of_a_match_become_rows(self, tmp_path):
        corpus = _write(tmp_path, [
            _match(1, "white", _player(["a"]), _player(["b"])),
        ])
        rows = load_player_matches(corpus)
        assert len(rows) == 2
        assert {r.player_id for r in rows} == {"white", "black"}

    def test_scores_are_one_half_zero(self, tmp_path):
        corpus = _write(tmp_path, [
            _match(1, "white", _player(["a"]), _player(["b"])),
            _match(2, None, _player(["a"]), _player(["b"]), end_reason="repetition"),
        ])
        rows = {(r.seed, r.player_id): r.score for r in load_player_matches(corpus)}
        assert rows[(1, "white")] == 1.0
        assert rows[(1, "black")] == 0.0
        assert rows[(2, "white")] == rows[(2, "black")] == 0.5

    def test_unfinished_matches_are_dropped(self, tmp_path):
        """No outcome means no contribution — not a manufactured draw."""
        corpus = _write(tmp_path, [
            _match(1, "white", _player(["a"]), _player(["b"])),
            _match(2, None, _player(["a"]), _player(["b"]),
                   completed=False, end_reason=None),
        ])
        rows = load_player_matches(corpus)
        assert len(rows) == 2
        assert all(r.seed == 1 for r in rows)

    def test_a_corpus_predating_card_identity_still_loads(self, tmp_path):
        """
        Telemetry only started recording *which* cards in §53; a corpus
        written before that has the counts and none of the ids.  It should
        degrade to "no card data" rather than crash, so an old corpus can
        still answer the balance questions it does have data for.
        """
        legacy = {"archetype": "warrior", "active_king": "marshal_king",
                  "cards_played": 4, "kings_played": ["marshal_king"]}
        corpus = _write(tmp_path, [_match(1, "white", legacy, legacy)])

        rows = load_player_matches(corpus)
        assert len(rows) == 2
        assert all(r.deck == frozenset() for r in rows)

        report = full_report(corpus)
        assert report["balance"]["finished"] == 1
        assert report["card_strength"]["cards"] == []

    def test_card_and_king_identity_survives_the_round_trip(self, tmp_path):
        corpus = _write(tmp_path, [
            _match(1, "white",
                   _player(["a", "b"], played=["a"], buildings=["watchtower"],
                           rituals=["dragon_rite"], kings=["k1", "k2"]),
                   _player(["c"])),
        ])
        white = next(r for r in load_player_matches(corpus) if r.player_id == "white")
        assert white.deck == {"a", "b"}
        assert white.played == {"a"}
        assert white.buildings == {"watchtower"}
        assert white.rituals == {"dragon_rite"}
        assert white.king_path == ("k1", "k2")


# ─────────────────────────────────────────────────────────────────────────────
# Card strength — the claim the module is built on
# ─────────────────────────────────────────────────────────────────────────────

class TestCardStrength:
    def _corpus_with_a_strong_card(self, tmp_path, n=120):
        """
        ``bomb`` is dealt to white in half the matches and white wins exactly
        those.  Nothing else differs, so nothing else should show up.
        """
        matches = []
        for i in range(n):
            white_has_bomb = i % 2 == 0
            matches.append(_match(
                i,
                "white" if white_has_bomb else "black",
                _player(["bomb", "filler"] if white_has_bomb else ["filler"]),
                _player(["filler"]),
            ))
        return _write(tmp_path, matches)

    def test_a_planted_effect_is_found(self, tmp_path):
        rows = load_player_matches(self._corpus_with_a_strong_card(tmp_path))
        report = card_strength(rows)
        bomb = next(e for e in report["cards"] if e["key"] == "bomb")

        # Not 1.0: the comparison group is "every player not dealt it",
        # which includes the opponents of the players who were — and half
        # of those won.
        assert bomb["delta"] > 0.5
        assert bomb["significant"]
        assert report["strongest"][0]["key"] == "bomb"

    def test_a_card_everyone_holds_shows_nothing(self, tmp_path):
        """``filler`` is in every deck, so it has no comparison group."""
        rows = load_player_matches(self._corpus_with_a_strong_card(tmp_path))
        report = card_strength(rows)
        filler = next(e for e in report["cards"] if e["key"] == "filler")

        assert filler["without"]["n"] == 0
        assert not filler["significant"]

    def test_the_deal_is_the_denominator_not_the_play(self, tmp_path):
        """
        The contaminated version of this metric conditions on the card being
        *played*.  Here a card is played only by the side that was already
        winning, and is otherwise inert — conditioning on play would rank it
        top; conditioning on the deal must not.
        """
        matches = []
        for i in range(120):
            white_wins = i % 2 == 0
            # Both sides always hold it; only the winner ever plays it.
            white = _player(["passenger"], played=["passenger"] if white_wins else [])
            black = _player(["passenger"], played=[] if white_wins else ["passenger"])
            matches.append(_match(i, "white" if white_wins else "black", white, black))

        rows = load_player_matches(_write(tmp_path, matches))
        report = card_strength(rows)
        passenger = next(e for e in report["cards"] if e["key"] == "passenger")

        assert passenger["without"]["n"] == 0        # nobody lacks it
        assert not passenger["significant"]          # so nothing can be claimed
        assert passenger["play_rate"] == 0.5         # the bot's habit, reported

    def test_play_rate_is_reported_per_deck_appearance(self, tmp_path):
        matches = [
            _match(i, "white",
                   _player(["c"], played=["c"] if i < 30 else []),
                   _player(["d"]))
            for i in range(60)
        ]
        rows = load_player_matches(_write(tmp_path, matches))
        report = card_strength(rows)
        assert next(e for e in report["cards"] if e["key"] == "c")["play_rate"] == 0.5

    def test_a_card_never_played_is_called_out(self, tmp_path):
        matches = [
            _match(i, "white", _player(["dead_weight"]), _player(["x"]))
            for i in range(40)
        ]
        rows = load_player_matches(_write(tmp_path, matches))
        assert "dead_weight" in card_strength(rows)["never_played"]

    def test_strongest_and_weakest_never_name_the_same_card(self, tmp_path):
        """With few signals both slices would otherwise return the same rows."""
        rows = load_player_matches(self._corpus_with_a_strong_card(tmp_path))
        report = card_strength(rows)
        overlap = {e["key"] for e in report["strongest"]} & {
            e["key"] for e in report["weakest"]
        }
        assert not overlap


# ─────────────────────────────────────────────────────────────────────────────
# Balance
# ─────────────────────────────────────────────────────────────────────────────

class TestBalance:
    def test_first_player_advantage_is_measured(self, tmp_path):
        corpus = _write(tmp_path, [
            _match(i, "white", _player(["a"]), _player(["b"])) for i in range(80)
        ])
        report = balance(load_player_matches(corpus), corpus)

        assert report["first_player_advantage"]["delta"] == 1.0
        assert report["first_player_advantage"]["significant"]
        assert report["white"]["score"] == 1.0
        assert report["black"]["score"] == 0.0

    def test_a_fair_game_shows_no_advantage(self, tmp_path):
        corpus = _write(tmp_path, [
            _match(i, "white" if i % 2 else "black", _player(["a"]), _player(["b"]))
            for i in range(80)
        ])
        report = balance(load_player_matches(corpus), corpus)
        assert report["first_player_advantage"]["delta"] == 0.0
        assert not report["first_player_advantage"]["significant"]

    def test_draws_and_end_reasons_are_counted(self, tmp_path):
        corpus = _write(tmp_path, [
            _match(1, "white", _player(["a"]), _player(["b"])),
            _match(2, None, _player(["a"]), _player(["b"]), end_reason="repetition"),
            _match(3, None, _player(["a"]), _player(["b"]), end_reason="no_progress"),
        ])
        report = balance(load_player_matches(corpus), corpus)

        assert report["decisive"] == 1
        assert report["drawn"] == 2
        assert report["draw_rate"] == pytest.approx(2 / 3, abs=1e-4)
        assert report["end_reasons"]["repetition"] == 1

    def test_unfinished_matches_are_reported_not_hidden(self, tmp_path):
        corpus = _write(tmp_path, [
            _match(1, "white", _player(["a"]), _player(["b"])),
            _match(2, None, _player(["a"]), _player(["b"]),
                   completed=False, end_reason=None),
        ])
        report = balance(load_player_matches(corpus), corpus)
        assert report["matches"] == 2
        assert report["finished"] == 1
        assert report["unfinished"] == 1

    def test_match_length_percentiles(self, tmp_path):
        corpus = _write(tmp_path, [
            _match(i, "white", _player(["a"]), _player(["b"]), turns=i)
            for i in range(1, 101)
        ])
        turns = balance(load_player_matches(corpus), corpus)["turns"]
        assert turns["median"] == 51
        assert turns["p10"] < turns["median"] < turns["p90"]


# ─────────────────────────────────────────────────────────────────────────────
# The rest of §53's list
# ─────────────────────────────────────────────────────────────────────────────

class TestOtherSections:
    def _mixed(self, tmp_path, n=120):
        matches = []
        for i in range(n):
            white_wins = i % 2 == 0
            matches.append(_match(
                i, "white" if white_wins else "black",
                _player(["a"], buildings=["forge", "wall"] if white_wins else ["hut"],
                        rituals=["rite"] if white_wins else (),
                        archetype="warrior", king="marshal_king",
                        kings=["marshal_king", "shadow_regent"]),
                _player(["b"], buildings=["hut"], archetype="spellcaster",
                        king="shadow_regent", kings=["shadow_regent"]),
            ))
        return _write(tmp_path, matches)

    def test_building_pairs_are_enumerated(self, tmp_path):
        report = full_report(self._mixed(tmp_path))
        pairs = {e["key"] for e in report["buildings"]["pairs"]}
        assert "forge + wall" in pairs

    def test_ritual_completion_is_split_out(self, tmp_path):
        report = full_report(self._mixed(tmp_path))
        assert report["rituals"]["any_ritual"]["with"]["n"] == 60
        assert report["rituals"]["any_ritual"]["delta"] > 0.5

    def test_succession_paths_are_kept_in_order(self, tmp_path):
        report = full_report(self._mixed(tmp_path))
        paths = {e["path"] for e in report["kings"]["succession_paths"]}
        assert "marshal_king → shadow_regent" in paths

    def test_archetypes_are_rated(self, tmp_path):
        report = full_report(self._mixed(tmp_path))
        rated = {e["archetype"]: e["score"] for e in report["archetypes"]["by_archetype"]}
        assert rated["warrior"] == 0.5 and rated["spellcaster"] == 0.5

    def test_correlational_sections_say_so(self, tmp_path):
        """
        Buildings, Kings and Rituals are chosen, not dealt.  The report must
        not present them with the same authority as card strength.
        """
        report = full_report(self._mixed(tmp_path))
        for section in ("buildings", "kings", "rituals"):
            assert "correlational" in report[section]["note"]


# ─────────────────────────────────────────────────────────────────────────────
# Report and rendering
# ─────────────────────────────────────────────────────────────────────────────

class TestReport:
    def test_an_empty_corpus_says_so_rather_than_dividing_by_zero(self, tmp_path):
        corpus = _write(tmp_path, [])
        report = full_report(corpus)
        assert "error" in report
        assert "no finished matches" in render_text(report)

    def test_a_corpus_of_only_unfinished_matches_is_empty_too(self, tmp_path):
        corpus = _write(tmp_path, [
            _match(1, None, _player(["a"]), _player(["b"]), completed=False),
        ])
        assert "error" in full_report(corpus)

    def test_the_report_is_json_serialisable(self, tmp_path):
        corpus = _write(tmp_path, [
            _match(i, "white", _player(["a"]), _player(["b"])) for i in range(40)
        ])
        json.dumps(full_report(corpus))          # must not raise

    def test_rendering_covers_every_section(self, tmp_path):
        corpus = _write(tmp_path, [
            _match(i, "white" if i % 2 else "black",
                   _player(["a"], buildings=["forge"], rituals=["rite"]),
                   _player(["b"]))
            for i in range(80)
        ])
        text = render_text(full_report(corpus))
        for heading in ("CORPUS", "BALANCE", "CARD STRENGTH", "RITUALS",
                        "BUILDINGS", "KINGS", "ARCHETYPES"):
            assert heading in text

    def test_thresholds_can_be_tightened(self, tmp_path):
        """A finding that does not survive a stricter bar should stop showing."""
        corpus = _write(tmp_path, [
            _match(i, "white" if i % 3 else "black",
                   _player(["edge"] if i % 3 else []), _player(["b"]))
            for i in range(90)
        ])
        rows = load_player_matches(corpus)

        set_thresholds(30, 2.0)
        loose = len(card_strength(rows)["signals"])
        set_thresholds(30, 50.0)
        strict = len(card_strength(rows)["signals"])
        assert strict < loose

    def test_the_bar_scales_to_what_is_on_screen(self):
        assert analysis._bar(0.0).strip() == "│"
        assert "█" in analysis._bar(0.05, scale=0.05)
        # A delta far past the scale saturates rather than overflowing.
        assert len(analysis._bar(99.0, scale=0.05)) == len(analysis._bar(0.0))


class TestVesselAvailability:
    """
    Monster play rate grouped by which Vessels the card can ride.  It
    started as a query about one suspicious card and turned into a property
    of the pool: a player has eight Pawns and one Queen, so a Queen-only
    Monster is a dead card in hand almost every game regardless of quality.
    """

    def _registry(self, mapping):
        """
        A registry of real ``MonsterCard`` instances — the analysis checks
        ``isinstance``, so a stand-in class would be skipped silently and
        the test would pass on an empty report.  MonsterCard is frozen, and
        only ``supported_vessels`` is read.
        """
        from game.cards.card import MonsterCard

        class FakeRegistry:
            pass

        registry = FakeRegistry()
        registry._cards = {}
        for card_id, vessels in mapping.items():
            card = MonsterCard.__new__(MonsterCard)
            object.__setattr__(card, "supported_vessels", vessels)
            registry._cards[card_id] = card
        return registry

    def test_groups_are_ordered_by_how_often_they_are_played(self, tmp_path):
        from game.analysis import vessels

        matches = []
        for i in range(60):
            # "easy" is always played, "hard" never.
            matches.append(_match(
                i, "white",
                _player(["easy", "hard"], played=["easy"]),
                _player(["easy", "hard"], played=["easy"]),
            ))
        rows = load_player_matches(_write(tmp_path, matches))
        report = vessels(rows, self._registry({
            "easy": ["pawn"], "hard": ["queen"],
        }))

        groups = {g["vessels"]: g["mean_play_rate"] for g in report["by_vessel_group"]}
        assert groups["pawn"] == 1.0
        assert groups["queen"] == 0.0
        assert report["by_vessel_group"][0]["vessels"] == "queen"   # least played first

    def test_vessel_names_are_sorted_so_a_group_has_one_name(self):
        from game.analysis import vessels

        report = vessels([], self._registry({"a": ["queen", "bishop"]}))
        assert report["by_vessel_group"] == [] or (
            report["by_vessel_group"][0]["vessels"] == "bishop/queen"
        )

    def test_no_registry_is_not_an_error(self):
        from game.analysis import vessels

        assert "note" in vessels([], None)

    def test_the_section_reaches_the_rendered_report(self, tmp_path):
        corpus = _write(tmp_path, [
            _match(i, "white", _player(["a"], played=["a"]), _player(["b"]))
            for i in range(40)
        ])
        report = full_report(corpus, registry=self._registry({"a": ["pawn"]}))
        assert "MONSTER PLAY RATE BY VESSEL" in render_text(report)
