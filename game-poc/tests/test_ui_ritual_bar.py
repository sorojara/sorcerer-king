"""
Stage 11 UI — the Ritual strip above the board, and the full-screen card zoom.

Two features, one file because they share the headless-pygame harness:

  • ui/ritual_bar.py — both players' Ritual pools rendered above the board.
    The interesting behaviour is the information boundary: the viewer's own
    row is complete, the rival's row carries only what its RevelationState
    has leaked, and which row is which swaps with the turn.

  • ui/overlays.py CardZoomOverlay — right-click a card in the CardViewer to
    read it full-screen, click outside to dismiss.

Everything runs against the real AppController on SDL's dummy video driver,
same as the UI tests in test_personality_bots.py.
"""

from __future__ import annotations

import os

import pytest

from game.core.phases import RevelationState as RS

pygame = pytest.importorskip("pygame")


def _app():
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    from game.ui.pygame_app import AppController

    return AppController()


def _rows(app):
    """The bar's chips split into (top row = black, bottom row = white)."""
    from game.ui.ritual_bar import BAR_HEIGHT

    mid = BAR_HEIGHT // 2
    chips = app._ritual_bar._chip_rects
    return (
        [cid for cid, r in chips if r.centery < mid],
        [cid for cid, r in chips if r.centery >= mid],
    )


# ─────────────────────────────────────────────────────────────────────────────
# Window geometry
# ─────────────────────────────────────────────────────────────────────────────

class TestWindowGeometry:
    def test_the_board_starts_below_the_ritual_strip(self):
        from game.ui.board_view import BOARD_OFFSET_Y
        from game.ui.ritual_bar import BAR_HEIGHT

        assert BOARD_OFFSET_Y == BAR_HEIGHT
        assert BAR_HEIGHT > 0

    def test_the_window_is_tall_enough_for_strip_board_and_hand(self):
        from game.ui.board_view import BOARD_PIXEL_SIZE
        from game.ui.hand_view import HandView
        from game.ui.pygame_app import WIN_H, _HAND_Y
        from game.ui.ritual_bar import BAR_HEIGHT

        assert _HAND_Y == BAR_HEIGHT + BOARD_PIXEL_SIZE
        assert WIN_H == _HAND_Y + HandView.HEIGHT

    def test_the_debug_hand_row_fits_under_the_main_one(self):
        """
        The black-hand toggle grows the window by exactly one hand row, and
        that row is drawn BELOW the main one — drawing it above would put it
        on top of the board's bottom rank.
        """
        from game.ui.hand_view import HandView
        from game.ui.pygame_app import WIN_H, _HAND_Y, _WIN_H_DEBUG

        assert _WIN_H_DEBUG == WIN_H + HandView.HEIGHT
        assert _HAND_Y + HandView.HEIGHT * 2 == _WIN_H_DEBUG

    def test_clicks_in_the_strip_are_not_read_as_board_squares(self):
        from game.ui.board_view import BOARD_OFFSET_X
        from game.ui.ritual_bar import BAR_HEIGHT

        app = _app()
        assert app._board_view.pos_from_click(BOARD_OFFSET_X + 10, BAR_HEIGHT // 2) is None
        assert app._board_view.pos_from_click(BOARD_OFFSET_X + 10, BAR_HEIGHT + 10) is not None


# ─────────────────────────────────────────────────────────────────────────────
# The strip's information boundary
# ─────────────────────────────────────────────────────────────────────────────

class TestRitualStripInformation:
    def test_both_pools_get_a_chip(self):
        app = _app()
        app._render()
        black, white = _rows(app)
        assert len(black) == len(app._game.state.get_player("black").ritual_pool)
        assert len(white) == len(app._game.state.get_player("white").ritual_pool)

    def test_the_viewers_own_rituals_are_all_identified(self):
        app = _app()
        app._render()
        _black, white = _rows(app)
        assert white == [
            rs.ritual_id for rs in app._game.state.get_player("white").ritual_pool
        ]

    def test_a_sealed_rival_ritual_has_no_identity_to_inspect(self):
        app = _app()
        for rs in app._game.state.get_player("black").ritual_pool:
            rs.revelation = RS.SEALED
        app._render()
        black, _white = _rows(app)
        assert black == [None] * len(black)

    def test_a_foretold_rival_ritual_still_has_no_identity(self):
        """FORETOLD leaks archetype + Vessel, never the card itself."""
        app = _app()
        for rs in app._game.state.get_player("black").ritual_pool:
            rs.revelation = RS.FORETOLD
        app._render()
        black, _white = _rows(app)
        assert black == [None] * len(black)

    def test_a_revealed_rival_ritual_is_named_and_inspectable(self):
        app = _app()
        pool = app._game.state.get_player("black").ritual_pool
        pool[0].revelation = RS.REVEALED
        app._render()
        black, _white = _rows(app)
        assert black[0] == pool[0].ritual_id
        assert black[1:] == [None] * len(black[1:])

    def test_the_same_ritual_is_shown_on_one_side_only(self):
        """
        The tightest form of the boundary: give both players the same card,
        reveal it on the viewer's side only, and it must appear exactly once.
        """
        app = _app()
        st = app._game.state
        shared = st.get_player("white").ritual_pool[0].ritual_id
        st.get_player("black").ritual_pool[0].ritual_id = shared
        st.get_player("black").ritual_pool[0].revelation = RS.SEALED
        app._render()
        black, white = _rows(app)
        assert white.count(shared) == 1
        assert black.count(shared) == 0

    def test_right_clicking_a_chip_opens_it_in_the_card_viewer(self):
        app = _app()
        app._render()
        own_id, rect = next(
            (cid, r) for cid, r in app._ritual_bar._chip_rects if cid is not None
        )
        app._handle_event(pygame.event.Event(
            pygame.MOUSEBUTTONDOWN, button=3, pos=rect.center,
        ))
        assert app._viewer_card_id == own_id

    def test_right_clicking_a_hidden_chip_opens_nothing(self):
        app = _app()
        for rs in app._game.state.get_player("black").ritual_pool:
            rs.revelation = RS.SEALED
        app._render()
        hidden_rect = next(
            r for cid, r in app._ritual_bar._chip_rects if cid is None
        )
        app._viewer_card_id = None
        app._handle_event(pygame.event.Event(
            pygame.MOUSEBUTTONDOWN, button=3, pos=hidden_rect.center,
        ))
        assert app._viewer_card_id is None


# ─────────────────────────────────────────────────────────────────────────────
# Whose perspective the strip shows
# ─────────────────────────────────────────────────────────────────────────────

class TestRitualStripPerspective:
    def test_the_perspective_follows_the_turn_in_a_hotseat_match(self):
        app = _app()
        st = app._game.state
        for ps in (st.get_player("white"), st.get_player("black")):
            for rs in ps.ritual_pool:
                rs.revelation = RS.SEALED

        app._render()
        black, white = _rows(app)
        assert black == [None] * len(black)      # rival, sealed
        assert all(cid is not None for cid in white)

        st.active_player = "black"
        app._render()
        black, white = _rows(app)
        assert all(cid is not None for cid in black)
        assert white == [None] * len(white)      # now white is the rival

    def test_an_ai_turn_does_not_expose_the_bots_pool(self):
        """
        With a human waiting on an AI, the strip keeps the human's view —
        same rule the hand strip already uses. A bot's sealed Rituals are
        hidden information no matter whose turn it is.
        """
        app = _app()
        st = app._game.state
        app._player_modes["black"] = "ai"
        for rs in st.get_player("black").ritual_pool:
            rs.revelation = RS.SEALED
        st.active_player = "black"

        app._render()
        black, white = _rows(app)
        assert black == [None] * len(black)
        assert all(cid is not None for cid in white)


# ─────────────────────────────────────────────────────────────────────────────
# The Ritual picker — revelation and activation in one menu
# ─────────────────────────────────────────────────────────────────────────────

class TestRitualPicker:
    def _app_with(self, *revelations):
        """An app whose white pool is set to the given revelation states."""
        app = _app()
        pool = app._game.state.get_player("white").ritual_pool
        for rstate, rev in zip(pool, revelations):
            rstate.revelation = rev
        return app

    def test_every_owned_ritual_gets_a_row(self):
        app = _app()
        app._do_ritual()
        assert len(app._ritual_picker._rows) == len(
            app._game.state.get_player("white").ritual_pool
        )

    def test_a_sealed_ritual_offers_its_reveal_step(self):
        app = self._app_with(RS.SEALED)
        app._do_ritual()
        label, key = app._ritual_picker._rows[0]
        assert "sealed" in label and "can reveal" in label
        assert key == app._game.state.get_player("white").ritual_pool[0].ritual_id

    def test_a_reveal_never_fires_off_the_first_click(self):
        """
        Spending information is irreversible, so picking the Ritual opens its
        action list rather than revealing on the spot.
        """
        app = self._app_with(RS.SEALED)
        app._do_ritual()
        rid = app._game.state.get_player("white").ritual_pool[0].ritual_id
        app._on_ritual_picker_choice(rid)
        assert app._ritual_picker is not None
        assert app._ritual_picker_mode == "choose_action"
        assert app._game.state.get_player("white").ritual_pool[0].revelation == RS.SEALED

    def test_confirming_the_step_advances_it(self):
        app = self._app_with(RS.SEALED)
        app._do_ritual()
        rid = app._game.state.get_player("white").ritual_pool[0].ritual_id
        app._on_ritual_picker_choice(rid)
        app._on_ritual_picker_choice(0)
        assert app._game.state.get_player("white").ritual_pool[0].revelation == RS.FORETOLD

    def test_the_step_label_names_both_ends(self):
        app = self._app_with(RS.FORETOLD)
        app._do_ritual()
        app._on_ritual_picker_choice(
            app._game.state.get_player("white").ritual_pool[0].ritual_id
        )
        label, _key = app._ritual_picker._rows[0]
        assert "foretold" in label and "revealed" in label

    def test_a_ritual_with_nothing_to_offer_is_disabled(self):
        """REVEALED, no legal sacrifice, nothing left to reveal → dead row."""
        app = self._app_with(RS.REVEALED)
        app._do_ritual()
        label, key = app._ritual_picker._rows[0]
        assert key is None
        assert "no sacrifice available" in label

    def test_the_second_reveal_this_turn_is_not_offered(self):
        app = self._app_with(RS.SEALED, RS.SEALED)
        app._game.state.get_player("white").ritual_reveal_used_this_turn = True
        app._do_ritual()
        assert all(key is None for _label, key in app._ritual_picker._rows)
        assert "revealed once already" in app._ritual_picker._rows[0][0]

    def test_the_strip_shows_whether_the_reveal_is_still_available(self):
        app = _app()
        app._render()
        assert app._current_obs().own_ritual_reveal_used is False
        app._game.state.get_player("white").ritual_reveal_used_this_turn = True
        assert app._current_obs().own_ritual_reveal_used is True
        app._render()   # must not raise with the budget spent

    def test_the_button_survives_spending_the_preparation_action(self):
        """RevealRitual is free, so the picker must stay reachable."""
        app = _app()
        app._game.state.get_player("white").preparation_action_used = True
        app._render()
        assert app._sidebar._btn_ritual is not None
        assert app._sidebar._btn_ritual_enabled


# ─────────────────────────────────────────────────────────────────────────────
# Full-screen card zoom
# ─────────────────────────────────────────────────────────────────────────────

class TestCardZoom:
    def _zoomed(self):
        """An app with a Ritual open in the CardViewer, already rendered."""
        app = _app()
        app._viewer_card_id = app._game.state.get_player("white").ritual_pool[0].ritual_id
        app._render()
        return app

    def test_right_clicking_the_viewer_card_zooms_it(self):
        app = self._zoomed()
        card_id, rect = app._card_viewer._card_block_rects[0]
        app._handle_event(pygame.event.Event(
            pygame.MOUSEBUTTONDOWN, button=3, pos=rect.center,
        ))
        assert app._card_zoom_id == card_id

    def test_a_ritual_also_offers_the_monster_it_summons(self):
        app = self._zoomed()
        blocks = dict(app._card_viewer._card_block_rects)
        ritual = app._registry.get(app._viewer_card_id)
        assert ritual.summon_monster_id in blocks

    def test_clicking_outside_the_big_card_closes_it(self):
        app = self._zoomed()
        app._card_zoom_id = app._viewer_card_id
        app._render()
        outside = (app._card_zoom._rect.left - 5, app._card_zoom._rect.centery)
        app._handle_event(pygame.event.Event(
            pygame.MOUSEBUTTONDOWN, button=1, pos=outside,
        ))
        assert app._card_zoom_id is None

    def test_clicking_the_big_card_itself_keeps_it_open(self):
        app = self._zoomed()
        app._card_zoom_id = app._viewer_card_id
        app._render()
        app._handle_event(pygame.event.Event(
            pygame.MOUSEBUTTONDOWN, button=1, pos=app._card_zoom._rect.center,
        ))
        assert app._card_zoom_id is not None

    def test_escape_closes_it(self):
        app = self._zoomed()
        app._card_zoom_id = app._viewer_card_id
        app._render()
        app._handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE))
        assert app._card_zoom_id is None

    def test_it_is_modal_over_the_board(self):
        """A click that lands on a piece while zoomed must not select it."""
        app = self._zoomed()
        app._card_zoom_id = app._viewer_card_id
        app._render()
        # A white pawn's square, comfortably inside the big card's footprint.
        from game.ui.board_view import BOARD_OFFSET_X, BOARD_OFFSET_Y, SQUARE_SIZE

        square = (
            BOARD_OFFSET_X + SQUARE_SIZE * 4 + SQUARE_SIZE // 2,
            BOARD_OFFSET_Y + SQUARE_SIZE * 6 + SQUARE_SIZE // 2,
        )
        assert app._card_zoom._rect.collidepoint(square)
        app._handle_event(pygame.event.Event(
            pygame.MOUSEBUTTONDOWN, button=1, pos=square,
        ))
        assert app._selected_pos is None
        assert app._card_zoom_id is not None
