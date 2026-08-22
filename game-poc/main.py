"""
main.py — Sorcerer King Stage 3 Pygame entry point
===================================================

Usage:
    cd sorcerer-king/game-poc
    python main.py            # random seed
    python main.py --seed 42  # reproducible game

Controls:
    Left-click  — select a piece, then click a highlighted square to move
    ESC         — deselect the current piece
    Q           — quit

    The sidebar's "♔ White" / "♚ Black" buttons choose who plays each side,
    cycling HUMAN → RANDOM AI → HEURISTIC AI → SEARCH AI — so any two AI
    models can be matched against each other, or against you.

Headless simulation (no UI):
    python -m game.sim --matches 50 --white heuristic --black random
    python -m game.sim --matches 20 --white search --black heuristic

Note:
    Pygame must be installed:
        pip install pygame>=2.5
    Or install with optional UI deps:
        pip install -e ".[ui]"
"""

from __future__ import annotations

import argparse
import random
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sorcerer King — Stage 3 Pygame PoC"
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="RNG seed for reproducible games (omit for random seed)",
    )
    args = parser.parse_args()

    seed = args.seed if args.seed is not None else random.randint(0, 2**31 - 1)
    print(f"Game seed: {seed}  (pass --seed {seed} to replay this game)")
    print("Logging to logs/game.log")

    # Initialise logging before anything else
    from game.logger import setup_logging, get_logger
    setup_logging()
    log = get_logger(__name__)
    log.info("=" * 60)
    log.info("Sorcerer King starting — seed=%d", seed)
    log.info("=" * 60)

    # Import here so the import error is readable before pygame init
    try:
        from game.ui.pygame_app import AppController
    except ImportError as exc:
        print(f"Import error: {exc}")
        print("Install pygame:  pip install pygame>=2.5")
        log.critical("pygame import failed: %s", exc)
        sys.exit(1)

    AppController(seed=seed).run()
    log.info("Sorcerer King exited cleanly.")


if __name__ == "__main__":
    main()
