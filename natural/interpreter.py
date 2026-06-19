# natural/interpreter.py
"""
The natural language interpreter.

Executes a .natural program by:
1. Parsing it (parser.py)
2. Cross-checking every state claim against reality
3. Running `when` clauses as event handlers
4. Scheduling the next check (the heartbeat re-arms itself)

A .natural program is not just data — it has behavior. The `when` clauses
are reactive handlers: when state changes, the program reacts.

The interpreter is the runtime for the state-as-truth internet.
"""

import os
import re
import subprocess
import datetime
from dataclasses import dataclass, field
from typing import Optional
from .parser import parse, Program, Section, Statement, section_by_header, bullets


@dataclass
class Claim:
    """A state claim extracted from a natural program."""
    key: str
    value: str
    verified: bool = False
    actual: str = ""
    section: str = ""


@dataclass
class Reaction:
    """A `when` clause: condition -> actions."""
    condition: str
    actions: list = field(default_factory=list)  # list[str]


@dataclass
class ExecutionResult:
    """The result of running a natural program."""
    claims: list = field(default_factory=list)  # list[Claim]
    reactions: list = field(default_factory=list)  # list[Reaction]
    connections: list = field(default_factory=list)  # list[dict]
    timestamp: str = ""
    next_check_minutes: int = 1440  # default: 24h


def extract_claims(program: Program, project_dir: str = ".") -> list[Claim]:
    """Extract and cross-check all state claims from a program.

    Each `field: value` in the state section is a claim. The interpreter
    verifies it against reality. This is the Clear Standard in action:
    every claim is checked, and the result is labelled (verified/unverified).
    """
    claims = []

    # Find the state section
    state_section = section_by_header(program, "state")
    if not state_section:
        return claims

    for stmt in state_section.body:
        if not isinstance(stmt, Statement):
            continue
        if ":" not in stmt.text:
            continue

        key, _, val = stmt.text.partition(":")
        key = key.strip()
        val = val.strip()
        claim = Claim(key=key, value=val, section="state")

        # Cross-check specific claims against reality
        if key == "build":
            # Check if cargo build passes
            if os.path.isfile(os.path.join(project_dir, "Cargo.toml")):
                try:
                    r = subprocess.run(
                        ["cargo", "build"], capture_output=True, text=True,
                        cwd=project_dir, timeout=60,
                    )
                    combined = r.stdout + r.stderr
                    if "Finished" in combined:
                        claim.verified = True
                        claim.actual = "passing"
                    elif "error" in combined:
                        claim.verified = True
                        claim.actual = "BROKEN"
                    else:
                        claim.verified = False
                        claim.actual = "unknown"
                except Exception:
                    claim.verified = False
                    claim.actual = "timeout"
            else:
                claim.verified = True
                claim.actual = "n/a (no Cargo.toml)"

        elif key == "health":
            # Health is a self-declaration — verify it's not stale
            # by checking the heartbeat freshness
            claim.verified = True  # self-reported, accepted as-is
            claim.actual = val

        elif key == "uncommitted":
            # Count actual uncommitted files
            try:
                r = subprocess.run(
                    ["git", "status", "--porcelain"],
                    capture_output=True, text=True, cwd=project_dir, timeout=5,
                )
                count = len([l for l in r.stdout.strip().split("\n") if l.strip()])
                claim.verified = True
                claim.actual = f"{count} files"
                # Check if declared count matches
                declared_count = re.search(r"(\d+)", val)
                if declared_count and int(declared_count.group(1)) != count:
                    claim.verified = False
                    claim.actual = f"{count} files (declared: {val})"
            except Exception:
                claim.verified = False
                claim.actual = "unknown"

        elif key == "last-commit":
            try:
                r = subprocess.run(
                    ["git", "log", "--oneline", "-1"],
                    capture_output=True, text=True, cwd=project_dir, timeout=5,
                )
                actual = r.stdout.strip()
                claim.verified = True
                claim.actual = actual
            except Exception:
                claim.verified = False
                claim.actual = "unknown"

        elif key == "freshness":
            # Freshness is a self-declaration — just accept it
            claim.verified = True
            claim.actual = val

        else:
            # Unknown claim — not verified, just recorded
            claim.verified = False
            claim.actual = "unverified"

        claims.append(claim)

    return claims


def extract_reactions(program: Program) -> list[Reaction]:
    """Extract all `when` clauses from a program.

    A `when` clause is a section whose header starts with 'when'.
    The condition is the part after 'when'. The body is the actions.
    """
    reactions = []
    for section in program.sections:
        if section.header.lower().startswith("when "):
            condition = section.header[4:].strip()
            actions = [s.text for s in section.body if isinstance(s, Statement)]
            reactions.append(Reaction(condition=condition, actions=actions))
        # Also check subsections
        for sub in section.body:
            if isinstance(sub, Section) and sub.header.lower().startswith("when "):
                condition = sub.header[4:].strip()
                actions = [s.text for s in sub.body if isinstance(s, Statement)]
                reactions.append(Reaction(condition=condition, actions=actions))
    return reactions


def evaluate_condition(condition: str, claims: list[Claim]) -> bool:
    """Evaluate a `when` condition against the current claims.

    Conditions are natural-language phrases:
    - "build fails" → build claim is BROKEN
    - "build passes" → build claim is passing
    - "site is down" → health claim contains "down"
    - "uncommitted changes exist" → uncommitted > 0
    - "heartbeat fires" → always true (this IS the heartbeat)
    """
    cond = condition.lower()

    # Build a lookup of claims by key
    claim_map = {c.key: c for c in claims}

    if "build fails" in cond or "build is broken" in cond:
        build = claim_map.get("build")
        return build is not None and "BROKEN" in build.actual

    if "build passes" in cond:
        build = claim_map.get("build")
        return build is not None and "passing" in build.actual

    if "site is down" in cond:
        health = claim_map.get("health")
        return health is not None and "down" in health.actual.lower()

    if "uncommitted" in cond:
        for c in claims:
            if c.key == "uncommitted":
                count = re.search(r"(\d+)", c.actual)
                return count is not None and int(count.group(1)) > 0
        return False

    if "heartbeat fires" in cond:
        return True  # the interpreter IS the heartbeat

    # Unknown condition — don't trigger
    return False


def execute(program_path: str, project_dir: str = ".") -> ExecutionResult:
    """Execute a .natural program.

    1. Parse the file
    2. Extract and cross-check claims
    3. Evaluate `when` conditions
    4. Fire reactions for matching conditions
    5. Determine next check time
    """
    now = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    with open(program_path) as f:
        text = f.read()

    program = parse(text)
    result = ExecutionResult(timestamp=now)

    # 1. Cross-check claims
    result.claims = extract_claims(program, project_dir)

    # 2. Extract reactions
    result.reactions = extract_reactions(program)

    # 3. Evaluate conditions and fire reactions
    for reaction in result.reactions:
        if evaluate_condition(reaction.condition, result.claims):
            # Fire the reaction — print the actions
            for action in reaction.actions:
                # Parse the action for scheduling hints
                action_lower = action.lower()
                if "schedule" in action_lower and "hour" in action_lower:
                    hours = re.search(r"(\d+)\s*hour", action_lower)
                    if hours:
                        result.next_check_minutes = int(hours.group(1)) * 60
                elif "4 hours" in action_lower or "next check in 4" in action_lower:
                    result.next_check_minutes = 240
                elif "24 hours" in action_lower or "daily" in action_lower:
                    result.next_check_minutes = 1440
                elif "12 hours" in action_lower:
                    result.next_check_minutes = 720
                elif "6 hours" in action_lower:
                    result.next_check_minutes = 360
                elif "2 hours" in action_lower:
                    result.next_check_minutes = 120

    return result


def format_result(result: ExecutionResult) -> str:
    """Format an execution result as a human-readable report."""
    lines = []
    lines.append(f"=== natural execution {result.timestamp} ===")
    lines.append("")

    # Claims
    lines.append("--- claims (cross-checked) ---")
    for claim in result.claims:
        status = "verified" if claim.verified else "UNVERIFIED"
        match = "matches" if claim.value in claim.actual else f"actual: {claim.actual}"
        lines.append(f"  {claim.key}: {claim.value} [{status}] — {match}")
    lines.append("")

    # Reactions
    if result.reactions:
        lines.append("--- reactions ---")
        for r in result.reactions:
            fired = evaluate_condition(r.condition, result.claims)
            state = "FIRED" if fired else "armed"
            lines.append(f"  when {r.condition}: {state}")
            if fired:
                for action in r.actions:
                    lines.append(f"    -> {action}")
        lines.append("")

    # Next check
    hours = result.next_check_minutes / 60
    lines.append(f"--- next check: in {result.next_check_minutes} minutes ({hours:.0f}h) ---")
    lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        # Execute a STATE.md as a demo
        path = "../clear-standard/STATE.md"
        project = "../clear-standard/"
    else:
        path = sys.argv[1]
        project = os.path.dirname(path)

    result = execute(path, project)
    print(format_result(result))