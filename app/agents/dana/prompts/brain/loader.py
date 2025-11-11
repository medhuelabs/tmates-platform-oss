"""Assembler for Dana's brain prompt sections."""

from __future__ import annotations

from pathlib import Path

_SECTION_FILES: tuple[tuple[str, str], ...] = (
    ("system_instructions", "system.txt"),
    ("cognition", "cognition.txt"),
    ("affect", "affect.txt"),
    ("behavior", "behavior.txt"),
)


def load_brain_prompt() -> str:
    """Load prompt sections and wrap them in XML-style tags."""
    base_dir = Path(__file__).resolve().parent
    parts: list[str] = []

    for tag, filename in _SECTION_FILES:
        file_path = base_dir / filename
        if not file_path.exists():
            continue

        content = file_path.read_text().strip()
        if not content:
            continue

        parts.append(f"<{tag}>\n{content}\n</{tag}>")

    return "\n".join(parts)

