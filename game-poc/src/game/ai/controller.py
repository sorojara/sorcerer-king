"""
PlayerController Interface — Stage 0
======================================

All players — human and AI — interact with the game through the same interface.

Architecture (from README §37, §43):

    GameState
        │
        ▼
    Observation
        │
        ├── Human Controller (UI input)
        └── AI Controller (algorithm)
              │
              ▼
           Action
              │
              ▼
        RulesEngine (validates)
              │
              ▼
        Events → new GameState

The ``PlayerController`` ABC enforces this contract.
No controller ever receives a GameState.  Period.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from game.core.actions import Action
    from game.core.observation import Observation


class PlayerController(ABC):
    """
    Abstract base class for all player types.

    Contract:
        • Receives an Observation (never a GameState).
        • Receives the list of legal actions from the engine.
        • Returns exactly one Action from the legal list.
    """

    @abstractmethod
    def choose_action(
        self,
        observation: "Observation",
        legal_actions: "list[Action]",
    ) -> "Action":
        """
        Choose one action from ``legal_actions``.

        Implementors MUST return an action from the provided list.
        Returning an action not in ``legal_actions`` is a bug.
        The engine re-validates regardless.
        """
        ...

    @property
    def player_id(self) -> str:
        """Return the player ID this controller is assigned to."""
        return self._player_id

    @player_id.setter
    def player_id(self, value: str) -> None:
        self._player_id = value

    def __repr__(self) -> str:  # pragma: no cover
        cls = self.__class__.__name__
        pid = getattr(self, "_player_id", "?")
        return f"{cls}(player_id={pid!r})"
