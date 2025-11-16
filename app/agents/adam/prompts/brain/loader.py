"""Assembler for Adam's brain prompt sections."""

from __future__ import annotations

from pathlib import Path

_NESTED_SECTIONS: tuple[tuple[str, str], ...] = (
    ("identity", "identity.txt"),
    ("affect", "affect.txt"),
    ("behavior", "behavior.txt"),
    ("cognition", "cognition.txt"),
)


def load_brain_prompt() -> str:
    """Load prompt sections and wrap them in nested XML-style tags."""
    base_dir = Path(__file__).resolve().parent
    parts: list[str] = []
    
    # Load system.txt as the top-level system section
    system_path = base_dir / "system.txt"
    if system_path.exists():
        system_content = system_path.read_text().strip()
        if system_content:
            parts.append(f"<system>\n{system_content}\n</system>")
    
    # Build persona sections
    persona_parts: list[str] = []
    for tag, filename in _NESTED_SECTIONS:
        file_path = base_dir / filename
        if not file_path.exists():
            continue

        content = file_path.read_text().strip()
        if not content:
            continue

        persona_parts.append(f"<{tag}>\n{content}\n</{tag}>")
    
    # Wrap persona sections in <persona> tag
    if persona_parts:
        parts.append("<persona>")
        parts.extend(persona_parts)
        parts.append("</persona>")
    
    # Load world file
    world_path = base_dir / "world.txt"
    if world_path.exists():
        world_content = world_path.read_text().strip()
        if world_content:
            parts.append(f"<world>\n{world_content}\n</world>")
    
    # Load instructions file last
    instructions_path = base_dir / "instructions.txt"
    if instructions_path.exists():
        instructions_content = instructions_path.read_text().strip()
        if instructions_content:
            parts.append(f"<instructions>\n{instructions_content}\n</instructions>")
    
    return "\n".join(parts)

