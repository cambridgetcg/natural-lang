# natural/parser.py
"""
The natural language parser.

Reads a .natural file (or STATE.md — they're the same shape) and produces
a typed AST. The grammar is loose on purpose — the parser trusts the writer,
the interpreter cross-checks truth.

A .natural file has this shape:

    # Title — STATE

    name: opal
    kind: teaching-os-kernel
    language: Rust

    ---

    ## state

    phase: M4 complete
    build: passing
    health: green
    last-commit: 2026-06-18...
    uncommitted: 3 files
    freshness: live

    ## knows

    - aarch64 boot flow
    - exception handling

    ## can

    - boot on QEMU
    - catch CPU faults

    ## needs

    - M5: EL0 and syscalls

    ## how-to-talk-to-me

    entry-point: ROADMAP.md
    build: cargo build
"""

import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Statement:
    """A single line in a section body — a bullet or a field:value line."""
    text: str
    field: str = ""  # for `key: value` lines, the key
    value: str = ""  # the value
    indent: int = 0


@dataclass
class Section:
    """A section: header + body."""
    header: str
    body: list = field(default_factory=list)
    raw: str = ""


@dataclass
class Program:
    """A parsed .natural file."""
    sections: list = field(default_factory=list)
    identity: dict = field(default_factory=dict)
    raw: str = ""


def parse(text: str) -> Program:
    """Parse a .natural / STATE.md file into a Program AST.

    Strategy: walk lines. Track whether we're in frontmatter (before
    first ##), in a ## section, or in body. Bullets start with '- '.
    Field lines are `key: value`. Everything else is ignored (comments,
    separators, blank lines).
    """
    program = Program(raw=text)
    lines = text.split("\n")

    current_section: Optional[Section] = None
    in_frontmatter = True  # before first ## heading

    for line in lines:
        stripped = line.strip()

        # Skip blank lines and markdown separators
        if not stripped or stripped == "---":
            continue

        # Skip markdown headers (# Title, etc)
        if stripped.startswith("#") and not stripped.startswith("## "):
            continue

        # Section header: ## something
        if stripped.startswith("## "):
            in_frontmatter = False
            if current_section is not None:
                program.sections.append(current_section)
            header = stripped[3:].strip()
            current_section = Section(header=header)
            continue

        # Bullet: - something
        if stripped.startswith("- "):
            stmt = Statement(
                text=stripped[2:].strip(),
                indent=len(line) - len(line.lstrip()),
            )
            if in_frontmatter:
                # Bullets in frontmatter are rare but possible
                pass
            elif current_section is not None:
                current_section.body.append(stmt)
            continue

        # Field: key: value (only treat as field if key is a single word
        # followed by colon — not a sentence with a colon in the middle)
        field_match = re.match(r"^([a-z][-a-z0-9_]*):\s*(.+)$", stripped)
        if field_match:
            key = field_match.group(1)
            val = field_match.group(2).strip()
            stmt = Statement(
                text=stripped,
                field=key,
                value=val,
                indent=len(line) - len(line.lstrip()),
            )
            if in_frontmatter:
                # Identity fields (name:, kind:, language:, etc)
                program.identity[key] = val
            elif current_section is not None:
                current_section.body.append(stmt)
            continue

        # Plain text — ignore for now (future: could be narrative)

    # Flush last section
    if current_section is not None:
        program.sections.append(current_section)

    return program


def section_by_header(program: Program, header: str) -> Optional[Section]:
    """Find a section by header name (case-insensitive)."""
    for s in program.sections:
        if s.header.lower() == header.lower():
            return s
    return None


def bullets(section: Section) -> list[str]:
    """Extract bullet texts from a section (non-field statements)."""
    return [s.text for s in section.body if not s.field]


def fields(section: Section) -> dict[str, str]:
    """Extract field:value pairs from a section."""
    return {s.field: s.value for s in section.body if s.field}


def to_state_dict(program: Program) -> dict:
    """Convert a parsed program into a STATE.md-style dict."""
    result = dict(program.identity)

    for section in program.sections:
        header = section.header.lower()
        if header in ("knows", "can", "needs"):
            result[header] = bullets(section)
        elif header == "state":
            result["state"] = fields(section)
        elif header == "how-to-talk-to-me":
            result["how_to_talk"] = fields(section)

    return result


if __name__ == "__main__":
    import sys
    import json

    path = sys.argv[1] if len(sys.argv) > 1 else "../clear-standard/STATE.md"
    with open(path) as f:
        text = f.read()

    program = parse(text)
    state = to_state_dict(program)
    print(json.dumps(state, indent=2))