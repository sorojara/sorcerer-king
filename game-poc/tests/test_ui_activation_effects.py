"""
Stage 14 UI — activation VFX (summon/spell/trap/ritual/coronation) and the
"Card Reveal" activation popup.

``AppController._update_activation_effects`` scans events newly appended to
``GameState.event_log`` (same length-cursor pattern as
``_update_terrain_scars``) and, for each Monster summon / Spell cast / Trap
set-or-sprung / Ritual completion / Coronation, spawns a transient board
animation (``_board_animations``) and — only when the "Card Reveal" sidebar
toggle is on, and never for a Trap merely being SET — queues an entry on
``ActivationPopup``.

These tests drive that method directly with hand-built events (real event
dataclasses, real card ids from the loaded registry) rather than playing a
full game to trigger each one — the resolution logic (find this piece's
current square, look up this card's archetype color, ...) is what's worth
covering; the engine already has its own tests for actually producing these
events.
"""

from __future__ import annotations

import os

import pytest

pygame = pytest.importorskip("pygame")


def _app():
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    from game.ui.pygame_app import AppController

    return AppController()


def _white_unit(app, piece_type):
    obs = app._current_obs()
    return next(
        u for u in obs.board.units
        if u.piece_type == piece_type and u.owner == "white"
    )


class TestSummon:
    def test_a_summon_spawns_a_board_animation(self):
        from game.core.events import MonsterSummoned

        app = _app()
        pawn = _white_unit(app, "pawn")
        obs = app._current_obs()
        app._game.state.event_log.append(MonsterSummoned(
            player_id="white", card_id="dark_magician",
            vessel_piece_id=pawn.piece_id, position=pawn.position,
        ))

        app._update_activation_effects(obs)

        assert len(app._board_animations) == 1
        anim = app._board_animations[0]
        assert anim["kind"] == "summon"
        assert anim["pos"] == pawn.position

    def test_a_summon_queues_the_card_reveal_popup_by_default(self):
        from game.core.events import MonsterSummoned

        app = _app()
        pawn = _white_unit(app, "pawn")
        obs = app._current_obs()
        app._game.state.event_log.append(MonsterSummoned(
            player_id="white", card_id="dark_magician",
            vessel_piece_id=pawn.piece_id, position=pawn.position,
        ))

        app._update_activation_effects(obs)

        assert app._activation_popup._queue == [("dark_magician", "Summoned")]

    def test_toggling_card_reveal_off_suppresses_the_popup_not_the_animation(self):
        from game.core.events import MonsterSummoned

        app = _app()
        app._show_card_reveal = False
        pawn = _white_unit(app, "pawn")
        obs = app._current_obs()
        app._game.state.event_log.append(MonsterSummoned(
            player_id="white", card_id="dark_magician",
            vessel_piece_id=pawn.piece_id, position=pawn.position,
        ))

        app._update_activation_effects(obs)

        assert len(app._board_animations) == 1        # VFX always plays
        assert app._activation_popup._queue == []      # board-center popup gated off
        assert app._sidebar_activation_overlay._queue == [("dark_magician", "Summoned")], (
            "the persistent CardViewer-sidebar overlay must always show, "
            "even with the Card Reveal toggle off — only the board-center "
            "popup is gated by it"
        )


class TestTrapSetVsTriggered:
    def test_placing_a_trap_spawns_a_discreet_animation_and_no_popup(self):
        from game.chess.pieces import Position
        from game.core.events import TrapPlaced

        app = _app()
        obs = app._current_obs()
        app._game.state.event_log.append(TrapPlaced(
            player_id="white", card_id="pit_trap", trap_instance_id="t1",
            position=Position(3, 3), radius=1,
        ))

        app._update_activation_effects(obs)

        assert len(app._board_animations) == 1
        assert app._board_animations[0]["kind"] == "trap_set"
        assert app._activation_popup._queue == [], (
            "a Trap being SET must never reveal its card — only firing does"
        )

    def test_a_trap_springing_reveals_the_card_it_was_set_with(self):
        from game.chess.pieces import Position
        from game.core.events import TrapPlaced, TrapTriggered

        app = _app()
        obs = app._current_obs()
        log = app._game.state.event_log
        log.append(TrapPlaced(
            player_id="white", card_id="pit_trap", trap_instance_id="t1",
            position=Position(3, 3), radius=1,
        ))
        log.append(TrapTriggered(trap_instance_id="t1", triggering_piece_id=None))

        app._update_activation_effects(obs)

        kinds = [a["kind"] for a in app._board_animations]
        assert kinds == ["trap_set", "trap_trigger"]
        assert app._board_animations[1]["pos"] == Position(3, 3), (
            "TrapTriggered carries no position of its own — it must be "
            "recovered from the matching TrapPlaced by trap_instance_id"
        )
        assert app._activation_popup._queue == [("pit_trap", "Trap Sprung!")]

    def test_a_trigger_for_an_unknown_trap_instance_does_not_crash(self):
        from game.core.events import TrapTriggered

        app = _app()
        obs = app._current_obs()
        app._game.state.event_log.append(
            TrapTriggered(trap_instance_id="never-placed", triggering_piece_id=None)
        )

        app._update_activation_effects(obs)  # must not raise

        assert app._board_animations == []      # no position to anchor on
        assert app._activation_popup._queue == []


class TestRitualAndCoronation:
    def test_ritual_activation_animates_the_vessel_and_reveals_two_cards(self):
        from game.core.events import RitualActivated

        app = _app()
        knight = _white_unit(app, "knight")
        obs = app._current_obs()
        app._game.state.event_log.append(RitualActivated(
            player_id="white", ritual_id="rite_of_the_wyrm",
            sacrificed_piece_ids=(knight.piece_id,),
            summoned_monster_id="dark_magician",
        ))

        app._update_activation_effects(obs)

        assert len(app._board_animations) == 1
        anim = app._board_animations[0]
        assert anim["kind"] == "ritual"
        assert anim["pos"] == knight.position
        # Ritual card first, then the Monster it summons — a short reveal
        # sequence rather than an ambiguous simultaneous stack.
        assert app._activation_popup._queue == [
            ("rite_of_the_wyrm", "Ritual Complete"),
            ("dark_magician", "Summoned"),
        ]

    def test_coronation_animates_the_kings_own_square(self):
        from game.core.events import KingCoronated

        app = _app()
        king = _white_unit(app, "king")
        obs = app._current_obs()
        app._game.state.event_log.append(
            KingCoronated(player_id="white", king_card_id="arcane_sovereign")
        )

        app._update_activation_effects(obs)

        assert len(app._board_animations) == 1
        anim = app._board_animations[0]
        assert anim["kind"] == "coronation"
        assert anim["pos"] == king.position
        assert app._activation_popup._queue == [("arcane_sovereign", "Coronation!")]

    def test_coronation_and_ritual_run_longer_than_a_plain_summon(self):
        """"Extra special" per the feature request — assert it, don't just eyeball it."""
        from game.core.events import KingCoronated, MonsterSummoned, RitualActivated

        app = _app()
        pawn = _white_unit(app, "pawn")
        knight = _white_unit(app, "knight")
        king = _white_unit(app, "king")
        obs = app._current_obs()
        log = app._game.state.event_log
        log.append(MonsterSummoned(
            player_id="white", card_id="dark_magician",
            vessel_piece_id=pawn.piece_id, position=pawn.position,
        ))
        log.append(RitualActivated(
            player_id="white", ritual_id="rite_of_the_wyrm",
            sacrificed_piece_ids=(knight.piece_id,),
            summoned_monster_id="dark_magician",
        ))
        log.append(KingCoronated(player_id="white", king_card_id="arcane_sovereign"))

        app._update_activation_effects(obs)

        by_kind = {a["kind"]: a["duration"] for a in app._board_animations}
        assert by_kind["summon"] < by_kind["ritual"] < by_kind["coronation"]


class TestSpell:
    def test_a_position_targeted_spell_animates_its_target_square(self):
        from game.chess.pieces import Position
        from game.core.events import SpellActivated

        app = _app()
        obs = app._current_obs()
        app._game.state.event_log.append(SpellActivated(
            player_id="white", card_id="arcane_reposition", target=(3, 3),
        ))

        app._update_activation_effects(obs)

        assert len(app._board_animations) == 1
        anim = app._board_animations[0]
        assert anim["kind"] == "spell"
        assert anim["pos"] == Position(3, 3)
        assert app._activation_popup._queue == [("arcane_reposition", "Spell Cast")]

    def test_a_no_target_spell_does_not_crash_and_animates_nothing(self):
        from game.core.events import SpellActivated

        app = _app()
        obs = app._current_obs()
        app._game.state.event_log.append(
            SpellActivated(player_id="white", card_id="arcane_reposition", target=None)
        )

        app._update_activation_effects(obs)  # must not raise

        assert app._board_animations == []
        # A card popup with nowhere to point still reveals the card itself.
        assert app._activation_popup._queue == [("arcane_reposition", "Spell Cast")]


class TestBookkeeping:
    def test_the_same_event_is_never_processed_twice(self):
        from game.core.events import MonsterSummoned

        app = _app()
        pawn = _white_unit(app, "pawn")
        obs = app._current_obs()
        app._game.state.event_log.append(MonsterSummoned(
            player_id="white", card_id="dark_magician",
            vessel_piece_id=pawn.piece_id, position=pawn.position,
        ))

        app._update_activation_effects(obs)
        app._update_activation_effects(obs)  # nothing new appended

        assert len(app._board_animations) == 1
        assert len(app._activation_popup._queue) == 1

    def test_a_shorter_log_than_last_seen_resets_the_cursor_instead_of_crashing(self):
        """Mirrors _update_terrain_scars's own save/load guard: a freshly
        imported save swaps the whole event_log out from under the cursor."""
        from game.core.events import MonsterSummoned

        app = _app()
        pawn = _white_unit(app, "pawn")
        obs = app._current_obs()
        app._game.state.event_log.append(MonsterSummoned(
            player_id="white", card_id="dark_magician",
            vessel_piece_id=pawn.piece_id, position=pawn.position,
        ))
        app._update_activation_effects(obs)
        assert app._anim_events_seen == len(app._game.state.event_log)

        app._game.state.event_log = []  # simulate a loaded save

        app._update_activation_effects(obs)  # must not raise / index past the end

        assert app._anim_events_seen == 0

    def test_expired_animations_are_pruned_on_the_next_update(self):
        from game.chess.pieces import Position

        app = _app()
        obs = app._current_obs()
        app._board_animations.append({
            "pos": Position(0, 0), "kind": "summon", "color": (0, 0, 0),
            "start": pygame.time.get_ticks() - 100_000, "duration": 1,
        })

        app._update_activation_effects(obs)

        assert app._board_animations == []


class TestSidebarToggle:
    def test_the_toggle_button_flips_show_card_reveal(self):
        app = _app()
        before = app._show_card_reveal

        app._toggle_card_reveal()

        assert app._show_card_reveal is not before

    def test_the_sidebar_reports_the_toggle_click(self):
        app = _app()
        obs = app._current_obs()
        app._sidebar.draw(obs, show_card_reveal=app._show_card_reveal)

        rect = app._sidebar._btn_card_reveal
        assert rect is not None
        assert app._sidebar.handle_click(rect.centerx, rect.centery) == "toggle_card_reveal"
