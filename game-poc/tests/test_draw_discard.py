"""
test_draw_discard.py — Card draw, discard, and deck-recycle mechanics (Stage 4).

Covers:
    • Game.new() uses default 20-card PoC deck, deals 5 opening hands.
    • Each player draws 1 card at the start of their turn (via auto-start).
    • EndTurn redirects to Phase.DISCARD when hand > HAND_SIZE_LIMIT (6).
    • DiscardCard removes a card from hand and puts it in graveyard.
    • After discarding to limit, phase exits DISCARD → END; EndTurn succeeds.
    • Graveyard is recycled into the deck when the deck is empty.
    • DiscardCard on a card not in hand raises IllegalActionError.
    • DiscardCard outside of DISCARD phase raises IllegalActionError.
"""

from __future__ import annotations

import pytest

from game.core.actions import DiscardCard, EndPreparation, EndTurn, MovePiece
from game.core.events import CardDiscarded, CardDrawn, DeckRecycled
from game.core.game import Game
from game.core.phases import HAND_SIZE_LIMIT, Phase
from game.core.rules import IllegalActionError
from game.chess.pieces import Position


# ── Helpers ──────────────────────────────────────────────────────────────────

POC_DECK = [
    "dark_magician", "apprentice_mage", "arcane_sentinel",
    "wyrm_knight", "dragon_herald", "iron_vanguard",
    "blade_dancer", "shadow_wolf", "stone_golem", "ritual_acolyte",
    "arcane_reposition", "veil_of_stillness", "cursed_ground",
    "ritual_insight", "shatter_trap", "pit_trap", "ward_of_binding",
    "alarm_beacon", "counter_strike", "time_anchor",
]


def _make_game(seed: int = 0) -> Game:
    return Game.new(seed=seed, white_deck=list(POC_DECK), black_deck=list(POC_DECK))


def _do_white_turn(game: Game, w_src: str = "e2", w_dst: str = "e4") -> None:
    """White: EndPrep → MovePiece → EndTurn. Leaves game in Phase.START for black."""
    game.execute(EndPreparation(player_id="white"))
    game.execute(MovePiece(
        player_id="white",
        source=Position.from_algebraic(w_src),
        target=Position.from_algebraic(w_dst),
    ))
    game.execute(EndTurn(player_id="white"))


def _do_black_turn(game: Game, b_src: str = "e7", b_dst: str = "e5") -> None:
    """Black: triggers auto-start (START→DRAW→PREP), EndPrep → MovePiece → EndTurn."""
    # Game.execute() auto-advances START→DRAW→PREP when phase==START
    game.execute(EndPreparation(player_id="black"))
    game.execute(MovePiece(
        player_id="black",
        source=Position.from_algebraic(b_src),
        target=Position.from_algebraic(b_dst),
    ))
    game.execute(EndTurn(player_id="black"))


# ── Default deck tests ────────────────────────────────────────────────────────

class TestDefaultDeck:
    def test_default_deck_opens_5_cards(self):
        game = Game.new(seed=0)
        assert len(game.state.players["white"].hand) == 5
        assert len(game.state.players["black"].hand) == 5

    def test_default_deck_leaves_15_in_deck(self):
        game = Game.new(seed=0)
        assert len(game.state.players["white"].deck) == 15
        assert len(game.state.players["black"].deck) == 15

    def test_different_seeds_give_different_hands(self):
        g1 = Game.new(seed=1)
        g2 = Game.new(seed=2)
        assert list(g1.state.players["white"].hand) != list(g2.state.players["white"].hand)


# ── Draw mechanic ─────────────────────────────────────────────────────────────

class TestDrawMechanic:
    def test_black_draws_at_start_of_their_turn(self):
        """
        After white ends their turn, black is in Phase.START.
        When black's first execute() is called, Game auto-advances
        START→DRAW (draw 1 card)→PREPARATION.
        """
        game = _make_game()
        initial_black_hand = len(game.state.players["black"].hand)
        initial_black_deck = len(game.state.players["black"].deck)

        _do_white_turn(game)

        # game is now at Phase.START for black
        assert game.state.phase == Phase.START
        assert game.state.active_player == "black"

        # Trigger auto-start: execute EndPreparation for black
        game.execute(EndPreparation(player_id="black"))

        # After auto-start, black has drawn 1 card and is now in CHESS
        assert game.state.phase == Phase.CHESS
        assert len(game.state.players["black"].hand) == initial_black_hand + 1
        assert len(game.state.players["black"].deck) == initial_black_deck - 1

    def test_card_drawn_event_emitted(self):
        game = _make_game()
        pre_log_len = len(game.state.event_log)

        _do_white_turn(game)
        game.execute(EndPreparation(player_id="black"))  # triggers auto-start → draws

        new_events = game.state.event_log[pre_log_len:]
        assert any(
            isinstance(e, CardDrawn) and e.player_id == "black"
            for e in new_events
        )

    def test_deck_shrinks_by_one_on_draw(self):
        game = _make_game()
        initial_deck = len(game.state.players["black"].deck)

        _do_white_turn(game)
        game.execute(EndPreparation(player_id="black"))

        assert len(game.state.players["black"].deck) == initial_deck - 1


# ── Discard mechanic ──────────────────────────────────────────────────────────

class TestDiscardPhase:
    def _game_with_bloated_hand(self, extra_cards: int = 1) -> tuple[Game, str]:
        """
        Return a game where white has HAND_SIZE_LIMIT + extra_cards cards,
        plus the algebraic square of the last white pawn moved (for the chess move).
        White's hand is padded by moving cards from deck before the chess phase.
        """
        game = _make_game()
        ps = game.state.players["white"]
        for _ in range(extra_cards):
            if ps.deck:
                ps.hand.append(ps.deck.pop(0))
        return game

    def test_end_turn_with_overfull_hand_enters_discard_phase(self):
        game = self._game_with_bloated_hand(extra_cards=1)  # 6 cards → exactly at limit, need >6
        # Give 2 extra cards to get 7 total
        ps = game.state.players["white"]
        if ps.deck:
            ps.hand.append(ps.deck.pop(0))
        assert len(ps.hand) == 7

        game.execute(EndPreparation(player_id="white"))
        game.execute(MovePiece(
            player_id="white",
            source=Position.from_algebraic("d2"),
            target=Position.from_algebraic("d4"),
        ))
        game.execute(EndTurn(player_id="white"))
        assert game.state.phase == Phase.DISCARD

    def test_discard_removes_card_from_hand_to_graveyard(self):
        game = self._game_with_bloated_hand(extra_cards=2)  # 7 cards
        game.execute(EndPreparation(player_id="white"))
        game.execute(MovePiece(
            player_id="white",
            source=Position.from_algebraic("d2"),
            target=Position.from_algebraic("d4"),
        ))
        game.execute(EndTurn(player_id="white"))
        assert game.state.phase == Phase.DISCARD

        ps = game.state.players["white"]
        card_to_discard = ps.hand[0]
        hand_size_before = len(ps.hand)

        game.execute(DiscardCard(player_id="white", card_id=card_to_discard))

        assert len(ps.hand) == hand_size_before - 1
        assert card_to_discard not in ps.hand
        assert card_to_discard in ps.graveyard

    def test_card_discarded_event_emitted(self):
        game = self._game_with_bloated_hand(extra_cards=2)
        game.execute(EndPreparation(player_id="white"))
        game.execute(MovePiece(
            player_id="white",
            source=Position.from_algebraic("d2"),
            target=Position.from_algebraic("d4"),
        ))
        game.execute(EndTurn(player_id="white"))

        ps = game.state.players["white"]
        card_to_discard = ps.hand[0]
        pre_log_len = len(game.state.event_log)

        game.execute(DiscardCard(player_id="white", card_id=card_to_discard))

        new_events = game.state.event_log[pre_log_len:]
        assert any(
            isinstance(e, CardDiscarded) and e.card_id == card_to_discard
            for e in new_events
        )

    def test_discard_one_card_exits_discard_phase(self):
        """With 7 cards, discarding 1 brings hand to 6 → phase exits DISCARD → END."""
        game = self._game_with_bloated_hand(extra_cards=2)  # 7 cards
        game.execute(EndPreparation(player_id="white"))
        game.execute(MovePiece(
            player_id="white",
            source=Position.from_algebraic("d2"),
            target=Position.from_algebraic("d4"),
        ))
        game.execute(EndTurn(player_id="white"))
        assert game.state.phase == Phase.DISCARD

        ps = game.state.players["white"]
        card = ps.hand[0]
        game.execute(DiscardCard(player_id="white", card_id=card))

        # Hand is now at HAND_SIZE_LIMIT → phase should have advanced to END
        assert game.state.phase == Phase.END
        assert len(ps.hand) == HAND_SIZE_LIMIT

    def test_end_turn_after_discard_switches_player(self):
        """After clearing the discard requirement, EndTurn advances to black."""
        game = self._game_with_bloated_hand(extra_cards=2)
        game.execute(EndPreparation(player_id="white"))
        game.execute(MovePiece(
            player_id="white",
            source=Position.from_algebraic("d2"),
            target=Position.from_algebraic("d4"),
        ))
        game.execute(EndTurn(player_id="white"))
        assert game.state.phase == Phase.DISCARD

        ps = game.state.players["white"]
        game.execute(DiscardCard(player_id="white", card_id=ps.hand[0]))
        assert game.state.phase == Phase.END

        game.execute(EndTurn(player_id="white"))
        assert game.state.active_player == "black"

    def test_discard_wrong_card_raises(self):
        game = self._game_with_bloated_hand(extra_cards=2)
        game.execute(EndPreparation(player_id="white"))
        game.execute(MovePiece(
            player_id="white",
            source=Position.from_algebraic("d2"),
            target=Position.from_algebraic("d4"),
        ))
        game.execute(EndTurn(player_id="white"))
        assert game.state.phase == Phase.DISCARD

        with pytest.raises(IllegalActionError, match="not in"):
            game.execute(DiscardCard(player_id="white", card_id="not_a_real_card"))

    def test_discard_in_wrong_phase_raises(self):
        game = _make_game()
        ps = game.state.players["white"]
        card = ps.hand[0]
        # Trying to discard during PREPARATION — should raise
        with pytest.raises(IllegalActionError):
            game.execute(DiscardCard(player_id="white", card_id=card))


# ── Graveyard recycle ─────────────────────────────────────────────────────────

class TestDeckRecycle:
    def test_draw_recycles_graveyard_when_deck_empty(self):
        """
        If a player's deck is empty at draw time, their graveyard is shuffled
        back into a fresh deck before drawing.
        """
        game = _make_game()
        white_ps = game.state.players["white"]

        # Move deck into graveyard to simulate empty deck
        white_ps.graveyard.extend(white_ps.deck)
        white_ps.deck.clear()
        assert len(white_ps.deck) == 0
        graveyard_size = len(white_ps.graveyard)
        assert graveyard_size > 0

        # Complete a full round (white → black) so white draws on their next turn
        _do_white_turn(game)       # white's turn; deck empty so no draw for white
        _do_black_turn(game)       # black's turn; triggers white's next draw on START

        # White's next turn auto-start should have recycled and drawn
        # Trigger white's second turn draw
        game.execute(EndPreparation(player_id="white"))  # auto-start fires here

        # After recycle, white's graveyard should be empty (moved to deck, then drawn)
        assert len(white_ps.graveyard) == 0
        # Deck should be graveyard_size - 1 (recycled then 1 drawn)
        assert len(white_ps.deck) == graveyard_size - 1

    def test_deck_recycled_event_emitted(self):
        game = _make_game()
        white_ps = game.state.players["white"]

        white_ps.graveyard.extend(white_ps.deck)
        white_ps.deck.clear()

        pre_log_len = len(game.state.event_log)

        _do_white_turn(game)
        _do_black_turn(game)
        game.execute(EndPreparation(player_id="white"))  # triggers auto-start → recycle + draw

        new_events = game.state.event_log[pre_log_len:]
        assert any(
            isinstance(e, DeckRecycled) and e.player_id == "white"
            for e in new_events
        ), f"Expected DeckRecycled event, got: {[type(e).__name__ for e in new_events]}"

    def test_no_draw_when_deck_and_graveyard_both_empty(self):
        game = _make_game()
        white_ps = game.state.players["white"]

        # Clear both
        white_ps.deck.clear()
        white_ps.graveyard.clear()
        initial_hand_size = len(white_ps.hand)

        # Complete a full round so white would draw on their next turn
        _do_white_turn(game)
        _do_black_turn(game)

        # Trigger white's auto-start — no draw should happen
        game.execute(EndPreparation(player_id="white"))

        # Hand should not have grown
        assert len(white_ps.hand) == initial_hand_size
