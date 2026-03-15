"""
Prompt loader for GitHub AI-Radar.

Prompts are stored as plain text files in this directory.
"""

from __future__ import annotations

from pathlib import Path

_PROMPT_DIR = Path(__file__).resolve().parent


def load_prompt(name: str) -> str:
    """Load a prompt file by name (without extension).

    Example::

        system_prompt = load_prompt("detect_ai")
    """
    path = _PROMPT_DIR / f"{name}.txt"
    return path.read_text(encoding="utf-8").strip()
