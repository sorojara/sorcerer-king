"""
test_cards.py — Card schema, CardRegistry, and YAML loader tests.

Covers:
    • MonsterCard, SpellCard, TrapCard instantiation.
    • MonsterCard.supports_vessel() logic.
    • CardRegistry registration, retrieval, and collision detection.
    • YAML loader produces 20 cards (10 monsters + 5 spells + 5 traps).
    • All PoC card IDs are unique.
    • All monster cards have at least one supported vessel.
    • No monster allows King as a vessel.
    • Effect entries are correctly parsed.
"""

from __future__ import annotations

import pytest
from pathlib import Path

from game.cards.card import (
    CardRegistry,
    CardType,
    EffectEntry,
    MonsterCard,
    SpellCard,
    TrapCard,
    TrapTrigger,
    SpellType,
    load_registry_from_yaml,
)

_DATA_DIR = Path(__file__).parent.parent / "src" / "game" / "data"


class TestMonsterCard:
    def _make(self) -> MonsterCard:
        return MonsterCard(
            id="test_monster",
            name="Test Monster",
            archetype="spellcaster",
            supported_vessels=("bishop", "queen"),
            effects=(EffectEntry(type="spell_radius_bonus", params={"bonus": 1}),),
            duel_ability="test_ability",
        )

    def test_card_type(self):
        assert self._make().card_type == CardType.MONSTER

    def test_supports_bishop(self):
        card = self._make()
        assert card.supports_vessel("bishop")
        assert card.supports_vessel("BISHOP")  # case insensitive

    def test_does_not_support_king(self):
        card = self._make()
        assert not card.supports_vessel("king")

    def test_effect_params(self):
        card = self._make()
        assert card.effects[0].type == "spell_radius_bonus"
        assert card.effects[0].params["bonus"] == 1


class TestSpellCard:
    def test_card_type(self):
        card = SpellCard(
            id="test_spell",
            name="Test Spell",
            spell_type=SpellType.INSTANT,
            target_type="piece",
            effects=(EffectEntry(type="reposition_unit"),),
        )
        assert card.card_type == CardType.SPELL


class TestTrapCard:
    def test_card_type(self):
        card = TrapCard(
            id="test_trap",
            name="Test Trap",
            trigger=TrapTrigger.ENTER_RADIUS,
            radius=1,
            effects=(EffectEntry(type="destroy_piece"),),
        )
        assert card.card_type == CardType.TRAP


class TestCardRegistry:
    def test_register_and_get(self):
        registry = CardRegistry()
        card = MonsterCard(
            id="demo", name="Demo", archetype="test",
            supported_vessels=("pawn",), effects=(),
        )
        registry.register(card)
        assert registry.get("demo") is card

    def test_duplicate_raises(self):
        registry = CardRegistry()
        card = MonsterCard(
            id="demo", name="Demo", archetype="test",
            supported_vessels=("pawn",), effects=(),
        )
        registry.register(card)
        with pytest.raises(ValueError, match="Duplicate"):
            registry.register(card)

    def test_unknown_card_raises(self):
        registry = CardRegistry()
        with pytest.raises(KeyError):
            registry.get("does_not_exist")

    def test_contains_operator(self):
        registry = CardRegistry()
        card = MonsterCard(
            id="x", name="X", archetype="test",
            supported_vessels=("rook",), effects=(),
        )
        registry.register(card)
        assert "x" in registry
        assert "y" not in registry


class TestYAMLLoader:
    @pytest.fixture(scope="class")
    @classmethod
    def registry(cls):
        return load_registry_from_yaml(_DATA_DIR)

    def test_total_card_count_at_least_60(self, registry: CardRegistry):
        # 10 original + 40 new monsters + 5 spells + 5 traps = 60
        assert len(registry) >= 60

    def test_monster_count_at_least_50(self, registry: CardRegistry):
        # 10 original + 40 new = 50 monsters minimum
        assert len(registry.all_monsters()) >= 50

    def test_spell_count_is_5(self, registry: CardRegistry):
        assert len(registry.all_spells()) == 5

    def test_trap_count_is_5(self, registry: CardRegistry):
        assert len(registry.all_traps()) == 5

    def test_all_ids_are_unique(self, registry: CardRegistry):
        monsters = [c.id for c in registry.all_monsters()]
        spells = [c.id for c in registry.all_spells()]
        traps = [c.id for c in registry.all_traps()]
        all_ids = monsters + spells + traps
        assert len(all_ids) == len(set(all_ids)), "All card IDs must be unique"

    def test_no_monster_uses_king_as_vessel(self, registry: CardRegistry):
        for monster in registry.all_monsters():
            assert "king" not in monster.supported_vessels, (
                f"Monster {monster.id!r} illegally allows King as vessel"
            )

    def test_all_monsters_have_at_least_one_vessel(self, registry: CardRegistry):
        for monster in registry.all_monsters():
            assert len(monster.supported_vessels) >= 1, (
                f"Monster {monster.id!r} has no supported vessels"
            )

    def test_known_monster_ids_exist(self, registry: CardRegistry):
        expected_ids = [
            "dark_magician", "apprentice_mage", "arcane_sentinel",
            "wyrm_knight", "dragon_herald", "iron_vanguard",
            "blade_dancer", "shadow_wolf", "stone_golem", "ritual_acolyte",
        ]
        for cid in expected_ids:
            assert cid in registry, f"Expected monster {cid!r} not in registry"

    def test_known_spell_ids_exist(self, registry: CardRegistry):
        expected_ids = [
            "arcane_reposition", "veil_of_stillness",
            "cursed_ground", "ritual_insight", "shatter_trap",
        ]
        for cid in expected_ids:
            assert cid in registry, f"Expected spell {cid!r} not in registry"

    def test_known_trap_ids_exist(self, registry: CardRegistry):
        expected_ids = [
            "pit_trap", "ward_of_binding",
            "alarm_beacon", "counter_strike", "time_anchor",
        ]
        for cid in expected_ids:
            assert cid in registry, f"Expected trap {cid!r} not in registry"

    def test_dark_magician_supports_bishop_and_queen(self, registry: CardRegistry):
        dm = registry.get("dark_magician")
        assert isinstance(dm, MonsterCard)
        assert "bishop" in dm.supported_vessels
        assert "queen" in dm.supported_vessels

    def test_pit_trap_has_enter_radius_trigger(self, registry: CardRegistry):
        pt = registry.get("pit_trap")
        assert isinstance(pt, TrapCard)
        assert pt.trigger == TrapTrigger.ENTER_RADIUS
        assert pt.radius == 1

    def test_time_anchor_is_manual_trigger(self, registry: CardRegistry):
        ta = registry.get("time_anchor")
        assert isinstance(ta, TrapCard)
        assert ta.trigger == TrapTrigger.MANUAL
