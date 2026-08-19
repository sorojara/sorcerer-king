"""
Card Schema — Stage 0
======================

Cards are data, not code (README §39 "Data-Driven Card Design").

A card definition lives in YAML under game/data/{monsters,spells,traps}.yaml
and is loaded once into a global CardRegistry at startup.

Schema overview:

Monster:
    id:               unique snake_case identifier
    name:             display name
    type:             "monster"
    archetype:        e.g. "spellcaster", "dragon", "warrior", "beast"
    supported_vessels: list[str]   e.g. ["bishop", "queen"]
    effects:          list[EffectEntry]
    duel_ability:     str | None   — ability tag used in Final Duel (Stage 12)
    description:      str

Spell:
    id, name, type: "spell"
    spell_type:     "instant" | "persistent" | "spatial"
    target_type:    "none" | "position" | "piece" | "trap" | "building" | "zone"
    effects:        list[EffectEntry]
    description:    str

Trap:
    id, name, type: "trap"
    trigger:        "enter_radius" | "capture" | "end_of_turn" | "manual"
    radius:         int (default 1 → 3×3 area)
    effects:        list[EffectEntry]
    description:    str

EffectEntry:
    type:     string tag matched by the EffectResolver
    params:   dict[str, Any] of effect parameters

The engine dispatches on ``EffectEntry.type`` strings, not subclasses.
Custom Python code is reserved only for effects that cannot be expressed
through the generic EffectEntry system.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CardType(Enum):
    MONSTER = "monster"
    SPELL = "spell"
    TRAP = "trap"


class SpellType(Enum):
    INSTANT = "instant"         # Resolves immediately, goes to graveyard
    PERSISTENT = "persistent"   # Stays on the board for N turns
    SPATIAL = "spatial"         # Creates a zone of influence


class TrapTrigger(Enum):
    ENTER_RADIUS = "enter_radius"   # Any piece enters the trap's radius
    CAPTURE = "capture"             # A capture happens within radius
    END_OF_TURN = "end_of_turn"     # Triggers at end of owner's turn
    MANUAL = "manual"               # Owner may manually activate
    # Declared for the expanded trap set (mechanics gated on unbuilt
    # systems — see traps.yaml's later sections); detection/dispatch for
    # these is not wired up yet, but the registry must still be able to
    # PARSE the card data without crashing.
    SUMMON = "summon"                       # An enemy Monster is summoned nearby
    RITUAL_ACTIVATION = "ritual_activation"  # An enemy attempts a Ritual nearby
    RITUAL_PROGRESS = "ritual_progress"      # An enemy advances a Ritual nearby
    FINAL_DUEL_START = "final_duel_start"    # A Final Duel begins


@dataclass(frozen=True)
class EffectEntry:
    """
    A single effect step in a card's effect list.
    The engine's EffectResolver dispatches on ``type``.

    Common effect types for Stage 0–4 cards:
        "move_piece"             – teleport/move a piece to a new square
        "destroy_piece"          – remove a piece from the board
        "reposition_unit"        – move a unit within constraints
        "block_file"             – temporarily block a file/rank
        "alter_movement"         – modify movement pattern for N turns
        "disable_building"       – prevent a building from activating
        "spell_radius_bonus"     – grant +radius to Spells cast nearby
        "capture_protection"     – unit survives one capture attempt
        "summon_pawn"            – place a Pawn on a specified square
        "damage_aura"            – destroy any piece entering radius
        "freeze_square"          – freeze a square (no movement in/out)
        "draw_card"              – owner draws a card
        "discard_card"           – owner/opponent discards a card
    """

    type: str
    params: dict[str, Any] = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# Card definition dataclasses
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class MonsterCard:
    id: str
    name: str
    archetype: str
    supported_vessels: tuple[str, ...]   # e.g. ("bishop", "queen")
    effects: tuple[EffectEntry, ...]
    duel_ability: str | None = None
    description: str = ""
    # Stage 6: filename under data/images/ for the CardViewer's image panel.
    # Defaults to "<id>.png" so every card gets a sensible path without
    # needing a YAML entry — the file itself is expected to be dropped in
    # later; the UI shows a "no image available" placeholder until then.
    image_path: str = ""

    @property
    def card_type(self) -> CardType:
        return CardType.MONSTER

    def supports_vessel(self, piece_type: str) -> bool:
        return piece_type.lower() in self.supported_vessels


@dataclass(frozen=True)
class SpellCard:
    id: str
    name: str
    spell_type: SpellType
    target_type: str        # "none" | "position" | "piece" | "trap" | "building" | "zone"
    effects: tuple[EffectEntry, ...]
    description: str = ""
    # Stage 6: canonical area of effect. Spells default to radius 0 — the
    # single square/piece they're cast on — since most Spells are surgical.
    # A "spatial" Spell that covers a wider area sets radius > 0 explicitly.
    radius: int = 0
    # "square" = N×N centred on target (default) | "row" | "column" — a
    # spell/trap covering an entire rank/file (see veil_of_stillness).
    shape: str = "square"
    # Stage 6: filename under data/images/ — see MonsterCard.image_path.
    image_path: str = ""

    @property
    def card_type(self) -> CardType:
        return CardType.SPELL


@dataclass(frozen=True)
class TrapCard:
    id: str
    name: str
    trigger: TrapTrigger
    effects: tuple[EffectEntry, ...]
    description: str = ""
    # Stage 6: canonical area of effect. Traps default to radius 1 — the
    # square they're placed on plus its ring of 8 neighbours (3×3) — since
    # a Trap is meant to threaten an area, not just its own square (README
    # §7: "Traps are visible battlefield objects with visible activation
    # areas.").
    radius: int = 1
    shape: str = "square"   # "square" | "row" | "column"
    # Number of times this Trap may trigger before it is removed from the
    # field. Defaults to 1 (single-use — consumed after resolving, matching
    # how Spells leave play immediately after casting) when a card's YAML
    # doesn't specify `charges:`. Set `charges: null` explicitly in YAML for
    # a Trap that should remain reusable indefinitely; a positive int N
    # means it survives N triggers.
    charges: int | None = 1
    # Stage 6: filename under data/images/ — see MonsterCard.image_path.
    image_path: str = ""

    @property
    def card_type(self) -> CardType:
        return CardType.TRAP


# Union
AnyCard = MonsterCard | SpellCard | TrapCard


# ─────────────────────────────────────────────────────────────────────────────
# CardRegistry
# ─────────────────────────────────────────────────────────────────────────────

class CardRegistry:
    """
    Global read-only card database.
    Loaded once from YAML at startup; used throughout the engine.
    """

    def __init__(self) -> None:
        self._cards: dict[str, AnyCard] = {}

    def register(self, card: AnyCard) -> None:
        if card.id in self._cards:
            raise ValueError(f"Duplicate card id: {card.id!r}")
        self._cards[card.id] = card

    def get(self, card_id: str) -> AnyCard:
        if card_id not in self._cards:
            raise KeyError(f"Unknown card: {card_id!r}")
        return self._cards[card_id]

    def all_monsters(self) -> list[MonsterCard]:
        return [c for c in self._cards.values() if isinstance(c, MonsterCard)]

    def all_spells(self) -> list[SpellCard]:
        return [c for c in self._cards.values() if isinstance(c, SpellCard)]

    def all_traps(self) -> list[TrapCard]:
        return [c for c in self._cards.values() if isinstance(c, TrapCard)]

    def __len__(self) -> int:
        return len(self._cards)

    def __contains__(self, card_id: str) -> bool:
        return card_id in self._cards


# ─────────────────────────────────────────────────────────────────────────────
# YAML loader
# ─────────────────────────────────────────────────────────────────────────────

def _build_effects(raw: list[dict[str, Any]]) -> tuple[EffectEntry, ...]:
    return tuple(
        EffectEntry(type=e["type"], params=e.get("params", {}))
        for e in (raw or [])
    )


def load_registry_from_yaml(data_dir: "str | Path") -> CardRegistry:  # type: ignore[name-defined]
    """
    Load all card YAML files from ``data_dir`` and return a populated registry.

    Expected files:
        {data_dir}/monsters.yaml
        {data_dir}/spells.yaml
        {data_dir}/traps.yaml
    """
    import yaml  # type: ignore[import]
    from pathlib import Path

    data_dir = Path(data_dir)
    registry = CardRegistry()

    # ── Monsters ──────────────────────────────────────────────────────────
    monsters_path = data_dir / "monsters.yaml"
    if monsters_path.exists():
        docs = yaml.safe_load(monsters_path.read_text())
        for d in (docs or []):
            registry.register(
                MonsterCard(
                    id=d["id"],
                    name=d["name"],
                    archetype=d.get("archetype", "generic"),
                    supported_vessels=tuple(d.get("supported_vessels", [])),
                    effects=_build_effects(d.get("effects", [])),
                    duel_ability=d.get("duel_ability"),
                    description=d.get("description", ""),
                    image_path=d.get("image", f"{d['id']}.png"),
                )
            )

    # ── Spells ────────────────────────────────────────────────────────────
    spells_path = data_dir / "spells.yaml"
    if spells_path.exists():
        docs = yaml.safe_load(spells_path.read_text())
        for d in (docs or []):
            registry.register(
                SpellCard(
                    id=d["id"],
                    name=d["name"],
                    spell_type=SpellType(d.get("spell_type", "instant")),
                    target_type=d.get("target_type", "none"),
                    effects=_build_effects(d.get("effects", [])),
                    description=d.get("description", ""),
                    radius=d.get("radius", 0),
                    shape=d.get("shape", "square"),
                    image_path=d.get("image", f"{d['id']}.png"),
                )
            )

    # ── Traps ─────────────────────────────────────────────────────────────
    traps_path = data_dir / "traps.yaml"
    if traps_path.exists():
        docs = yaml.safe_load(traps_path.read_text())
        for d in (docs or []):
            registry.register(
                TrapCard(
                    id=d["id"],
                    name=d["name"],
                    trigger=TrapTrigger(d.get("trigger", "enter_radius")),
                    radius=d.get("radius", 1),
                    shape=d.get("shape", "square"),
                    charges=d.get("charges", 1),
                    effects=_build_effects(d.get("effects", [])),
                    description=d.get("description", ""),
                    image_path=d.get("image", f"{d['id']}.png"),
                )
            )

    return registry
