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

Building (Stage 8):
    id, name, type: "building"
    size:           "small" | "medium" | "major" — determines its Building
                     Pool point cost (see BUILDING_POINT_COST). Buildings are
                     NOT part of the Main Deck (README §12) — they live in a
                     player's separate, public Building Pool and are loaded
                     from data/buildings.yaml, not sampled into a deck.
    construction_turns: int — number of the owner's own end-of-turn ticks
                     the Builder Pawn must survive before the Building
                     completes (README §12.2).
    radius:         int — the Building's own custom aura range (Stage 8:
                     capture-protection / spell-radius / trap-radius
                     bonuses), used by its ``effects``. Distinct from
                     ``territory_radius`` below.
    effects:        list[EffectEntry] — see mechanics/buildings.py for the
                     currently-dispatched effect types.
    description:    str

    Derived (Stage 9, see BuildingCard properties):
        territory_radius — how far this Building's own Territory extends,
                     derived from ``size`` via BUILDING_TERRITORY_RADIUS
                     (README §13).

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
    BUILDING = "building"
    KING = "king"
    RITUAL = "ritual"


class BuildingSize(Enum):
    """README §12.1 — Building Pool budget tiers."""

    SMALL = "small"
    MEDIUM = "medium"
    MAJOR = "major"


# README §12.1 example budget: Small=1, Medium=2, Major=3 points.
BUILDING_POINT_COST: dict[BuildingSize, int] = {
    BuildingSize.SMALL: 1,
    BuildingSize.MEDIUM: 2,
    BuildingSize.MAJOR: 3,
}

# Stage 9 — Territory radius derived from Building size (README §13 /
# user brief: "increase size of radius over size of building"). Kept
# separate from BuildingCard.radius (each Building's own custom aura
# range from Stage 8 — capture-protection, spell-radius, trap-radius
# bonuses) so retuning one never silently retunes the other.
BUILDING_TERRITORY_RADIUS: dict[BuildingSize, int] = {
    BuildingSize.SMALL: 1,
    BuildingSize.MEDIUM: 2,
    BuildingSize.MAJOR: 3,
}


# Stage 13 — Building durability (integrity), derived from ``size``. This is
# how many successful hostile actions a COMPLETE Building absorbs before it
# collapses (see mechanics/buildings.py damage_building). Kept as its own
# table for the same reason as the two above: retuning the siege economy
# must never silently retune Territory reach or Pool cost.
BUILDING_BASE_INTEGRITY: dict[BuildingSize, int] = {
    BuildingSize.SMALL: 1,
    BuildingSize.MEDIUM: 2,
    BuildingSize.MAJOR: 3,
}


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
    # Stage 11: True for a Monster that only ever enters play as the payoff
    # of a Ritual (README §14 "Ritual Monsters exist in a separate Ritual /
    # Extra Pool. They are not normally drawn."). Excluded from
    # Game._build_deck_from_registry's Main Deck sampling; otherwise behaves
    # exactly like any other MonsterCard once summoned (same on-summon
    # effects, same board rendering — a Ritual Monster is not a distinct
    # runtime type, just a card that reaches the board a different way).
    ritual_only: bool = False

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


@dataclass(frozen=True)
class BuildingCard:
    """
    Stage 8 — a Building Pool entry definition (README §11–§12).

    Buildings are never drawn — a player's Building Pool is a fixed,
    public pre-match selection (see PlayerState.building_pool /
    BuildingPoolEntry in core/state.py), loaded from data/buildings.yaml
    rather than sampled into a Main Deck.
    """

    id: str
    name: str
    size: BuildingSize
    construction_turns: int
    radius: int                          # area of influence
    effects: tuple[EffectEntry, ...]
    description: str = ""
    image_path: str = ""

    @property
    def card_type(self) -> CardType:
        return CardType.BUILDING

    @property
    def cost(self) -> int:
        """Building Pool point cost, derived from ``size`` (README §12.1)."""
        return BUILDING_POINT_COST[self.size]

    @property
    def territory_radius(self) -> int:
        """
        Stage 9 — how far this Building's own Territory extends beyond its
        square, derived from ``size`` (README §13 / user brief: "increase
        size of radius over size of building").
        """
        return BUILDING_TERRITORY_RADIUS[self.size]

    @property
    def base_integrity(self) -> int:
        """
        Stage 13 — how many successful hostile actions this Building absorbs
        once COMPLETE, derived from ``size`` (BUILDING_BASE_INTEGRITY).
        Copied onto BuildingInstance.max_integrity at construction start so
        registry-less unit tests still have a sane value to work with.
        """
        return BUILDING_BASE_INTEGRITY[self.size]


@dataclass(frozen=True)
class KingCard:
    """
    Stage 10 — one entry in the King Pool pre-match selection (README §16-§20).

    Each player selects a pool of 3 King Cards before the match, drawn from
    the shared roster in data/kings.yaml (see mechanics/kings.py for the
    random-assignment algorithm). All begin hidden; the first Coronation is
    free (README §17); later Succession has an increasing cost (README §19).

    ``effects``     — passive kingdom-wide policy effects while this King is
                       ACTIVE. Dispatched by mechanics/kings.py, NOT the
                       per-unit EFFECT_REGISTRY (registry.py) — King effects
                       are global player-level auras, not attached to a card
                       instance on the board. Only a subset are wired to
                       real systems today; the rest are documented stubs
                       (mirrors the Building/Monster STUB convention).
    ``duel_effect`` — single effect used only during the Final Duel
                       (Stage 12+). Always a stub today — Final Duel doesn't
                       exist yet.
    """

    id: str
    name: str
    title: str = ""
    archetype_support: tuple[str, ...] = ()
    effects: tuple[EffectEntry, ...] = ()
    duel_ability: str | None = None
    duel_effect: EffectEntry | None = None
    description: str = ""
    image_path: str = ""

    @property
    def card_type(self) -> CardType:
        return CardType.KING


@dataclass(frozen=True)
class RitualCard:
    """
    Stage 11 — one entry in the shared Ritual roster (README §14-§15).

    Each player draws a random 3-of-N ``ritual_pool`` from the shared
    roster at game setup (mechanics/rituals.py assign_random_ritual_pool),
    mirroring KingCard's own "pool of 3" model. All begin SEALED.

    ``condition_type`` selects which of README §14.1's condition families
    this Ritual uses (the PoC folds "Control" and "Tactical" into "state" —
    see mechanics/rituals.py's module docstring for the reasoning):

        "formation" — ``pattern`` must match a formation of the owner's own
                       pieces (README's Knight/Pawn-Bishop example).
        "material"  — the sacrificed pieces' combined chess value must reach
                       ``min_material`` (pawn=1, knight=3, bishop=3, rook=5,
                       queen=9).
        "state"     — a named predicate in ``state_checks`` about the
                       current game state must hold (e.g. "queen_lost").

    In every case, the LAST position in ActivateRitual.sacrifice_positions
    is the Ritual's Vessel — it survives and hosts ``summon_monster_id``,
    exactly like a normal SummonMonster (README §3). Every earlier position
    is a pure sacrifice: removed from the board entirely. ``required_vessel``
    (shown to the opponent once FORETOLD — README §15's example) constrains
    the Vessel's piece type; None means any non-King piece qualifies.

    ``pattern`` entries (formation only) are
    ``{"offset": [file_delta, rank_delta], "piece_type": str, "anchor": bool}``
    — offsets are relative to the Vessel (the anchor node, offset (0, 0)) in
    ABSOLUTE board coordinates, not mirrored per player color. This is a
    deliberate PoC simplification (README §57 lists "precise Monster
    movement rules" etc. as open questions) — the same literal geometry
    applies to both players rather than adding orientation-mirroring logic
    that has no gameplay payoff yet.

    ``reveal_progress_threshold`` — how much ``ritual_progress_boost``
    (ritual_acolyte) accumulation is needed to advance one revelation step
    (see mechanics/rituals.py advance_ritual_progress).
    """

    id: str
    name: str
    archetype: str
    condition_type: str                          # "formation" | "material" | "state"
    required_vessel: str | None = None
    pattern: tuple[dict[str, Any], ...] = ()      # formation nodes
    min_material: int = 0
    state_checks: tuple[str, ...] = ()
    min_sacrifices: int = 1
    reveal_progress_threshold: int = 3
    summon_monster_id: str = ""
    description: str = ""
    image_path: str = ""

    @property
    def card_type(self) -> CardType:
        return CardType.RITUAL


# Union
AnyCard = MonsterCard | SpellCard | TrapCard | BuildingCard | KingCard | RitualCard


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

    def all_buildings(self) -> list[BuildingCard]:
        return [c for c in self._cards.values() if isinstance(c, BuildingCard)]

    def all_kings(self) -> list[KingCard]:
        return [c for c in self._cards.values() if isinstance(c, KingCard)]

    def all_rituals(self) -> list[RitualCard]:
        return [c for c in self._cards.values() if isinstance(c, RitualCard)]

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


def _build_single_effect(raw: "dict[str, Any] | None") -> "EffectEntry | None":
    """King cards carry ``duel_effect`` as one mapping, not a list."""
    if not raw:
        return None
    return EffectEntry(type=raw["type"], params=raw.get("params", {}))


def load_registry_from_yaml(data_dir: "str | Path") -> CardRegistry:  # type: ignore[name-defined]
    """
    Load all card YAML files from ``data_dir`` and return a populated registry.

    Expected files:
        {data_dir}/monsters.yaml
        {data_dir}/spells.yaml
        {data_dir}/traps.yaml
        {data_dir}/buildings.yaml
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
                    ritual_only=d.get("ritual_only", False),
                )
            )

    # ── Ritual Monsters (Stage 11) ───────────────────────────────────────
    # Separate file (README §14 "Ritual Monsters exist in a separate
    # Ritual / Extra Pool") — same MonsterCard schema, but every card
    # loaded here is forced ritual_only=True regardless of the YAML,
    # so Game._build_deck_from_registry's Main Deck sampling (which reads
    # registry.all_monsters()) can never pick one up.
    ritual_monsters_path = data_dir / "ritual_monsters.yaml"
    if ritual_monsters_path.exists():
        docs = yaml.safe_load(ritual_monsters_path.read_text())
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
                    ritual_only=True,
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

    # ── Buildings (Stage 8) ───────────────────────────────────────────────
    buildings_path = data_dir / "buildings.yaml"
    if buildings_path.exists():
        docs = yaml.safe_load(buildings_path.read_text())
        for d in (docs or []):
            registry.register(
                BuildingCard(
                    id=d["id"],
                    name=d["name"],
                    size=BuildingSize(d.get("size", "small")),
                    construction_turns=d.get("construction_turns", 2),
                    radius=d.get("radius", 1),
                    effects=_build_effects(d.get("effects", [])),
                    description=d.get("description", ""),
                    image_path=d.get("image", f"{d['id']}.png"),
                )
            )

    # ── Kings (Stage 10) ──────────────────────────────────────────────────
    kings_path = data_dir / "kings.yaml"
    if kings_path.exists():
        docs = yaml.safe_load(kings_path.read_text())
        for d in (docs or []):
            registry.register(
                KingCard(
                    id=d["id"],
                    name=d["name"],
                    title=d.get("title", ""),
                    archetype_support=tuple(d.get("archetype_support", [])),
                    effects=_build_effects(d.get("effects", [])),
                    duel_ability=d.get("duel_ability"),
                    duel_effect=_build_single_effect(d.get("duel_effect")),
                    description=d.get("description", ""),
                    image_path=d.get("image", f"{d['id']}.png"),
                )
            )

    # ── Rituals (Stage 11) ───────────────────────────────────────────────
    rituals_path = data_dir / "rituals.yaml"
    if rituals_path.exists():
        docs = yaml.safe_load(rituals_path.read_text())
        for d in (docs or []):
            registry.register(
                RitualCard(
                    id=d["id"],
                    name=d["name"],
                    archetype=d.get("archetype", "generic"),
                    condition_type=d["condition"]["type"],
                    required_vessel=d.get("required_vessel"),
                    pattern=tuple(d["condition"].get("pattern", [])),
                    min_material=d["condition"].get("min_material", 0),
                    state_checks=tuple(d["condition"].get("state_checks", [])),
                    min_sacrifices=d.get("min_sacrifices", 1),
                    reveal_progress_threshold=d.get("reveal_progress_threshold", 3),
                    summon_monster_id=d["summon"],
                    description=d.get("description", ""),
                    image_path=d.get("image", f"{d['id']}.png"),
                )
            )

    return registry
