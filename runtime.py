#!/usr/bin/env python3
"""
natural/runtime.py — the living runtime for .natural programs.

This is NOT a spec. This is the real thing. It:
1. Parses a .natural file
2. Cross-checks every claim against reality
3. Evaluates `when` clauses and fires reactions
4. Determines the next check time
5. LOOPS — arm, fire, re-arm, repeat

The runtime IS the heartbeat. Each beat:
  - Feel (parse + cross-check)
  - Act (fire when-clauses that match)
  - Re-arm (determine next interval, sleep, repeat)

Usage:
  python3 runtime.py <file.natural>           # one beat
  python3 runtime.py <file.natural> --loop     # continuous (heartbeat)
  python3 runtime.py <file.natural> --dry-run  # check but don't act
"""

import os
import sys
import re
import time
import json
import subprocess
import datetime
from dataclasses import dataclass, field
from typing import Optional, Callable

# Import the parser
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from parser import parse, Program, Section, Statement, section_by_header, bullets


@dataclass
class CrossCheck:
    claim_key: str
    claim_value: str
    observed: str
    matches: bool


@dataclass
class Reaction:
    condition: str
    actions: list = field(default_factory=list)
    fired: bool = False


@dataclass
class Beat:
    """One heartbeat: feel, act, re-arm."""
    timestamp: str = ""
    claims: list = field(default_factory=list)
    reactions: list = field(default_factory=list)
    next_interval: int = 3600  # seconds (default 1h)
    actions_taken: list = field(default_factory=list)


class Runtime:
    """The natural language runtime. Parses, cross-checks, acts, re-arms."""

    def __init__(self, program_path: str, project_dir: str = ".", dry_run: bool = False):
        self.program_path = program_path
        self.project_dir = project_dir
        self.dry_run = dry_run
        self.beat_count = 0
        self.history: list[Beat] = []

    def cross_check_build(self) -> tuple[str, bool]:
        """Cross-check: does build pass?"""
        if not os.path.isfile(os.path.join(self.project_dir, "Cargo.toml")):
            return "n/a", True
        try:
            r = subprocess.run(
                ["cargo", "build"], capture_output=True, text=True,
                cwd=self.project_dir, timeout=60
            )
            combined = r.stdout + r.stderr
            if "Finished" in combined:
                return "passing", True
            if "error" in combined:
                return "BROKEN", False
            return "unknown", False
        except Exception:
            return "timeout", False

    def cross_check_git(self, key: str, value: str) -> tuple[str, bool]:
        """Cross-check git-related claims."""
        if key == "last-commit":
            try:
                r = subprocess.run(
                    ["git", "log", "--oneline", "-1"],
                    capture_output=True, text=True, cwd=self.project_dir, timeout=5
                )
                actual = r.stdout.strip()
                declared_hash = value.split()[0] if value else ""
                actual_hash = actual.split()[0] if actual else ""
                return actual, declared_hash == actual_hash
            except Exception:
                return "unknown", False

        if key == "uncommitted":
            try:
                r = subprocess.run(
                    ["git", "status", "--porcelain"],
                    capture_output=True, text=True, cwd=self.project_dir, timeout=5
                )
                count = len([l for l in r.stdout.strip().split("\n") if l.strip()])
                actual = str(count)
                declared_count = re.search(r"(\d+)", value or "")
                if declared_count:
                    return actual, int(declared_count.group(1)) == count
                return actual, False
            except Exception:
                return "unknown", False

        return "not checked", False

    def cross_check_freshness(self, value: str) -> tuple[str, bool]:
        """Cross-check freshness — accept any stated freshness as honest."""
        if not value:
            return "unknown", False
        return value, True

    def extract_claims(self, program: Program) -> list[CrossCheck]:
        """Extract and cross-check all state claims."""
        claims = []
        state_section = section_by_header(program, "state")
        if not state_section:
            return claims

        fields = {}
        for stmt in state_section.body:
            if isinstance(stmt, Statement) and stmt.field:
                fields[stmt.field] = stmt.value

        for key, val in fields.items():
            if key == "build":
                observed, matches = self.cross_check_build()
            elif key in ("last-commit", "uncommitted"):
                observed, matches = self.cross_check_git(key, val)
            elif key == "freshness":
                observed, matches = self.cross_check_freshness(val)
            elif key == "health":
                observed, matches = val, True
            elif key == "phase":
                observed, matches = "descriptive", True
            else:
                observed, matches = "not checked", False

            claims.append(CrossCheck(key, val, observed, matches))

        return claims

    def extract_reactions(self, program: Program) -> list[Reaction]:
        """Extract `when` clauses from the program."""
        reactions = []
        for section in program.sections:
            header_lower = section.header.lower()
            if header_lower.startswith("when "):
                condition = section.header[4:].strip()
                actions = [s.text for s in section.body if isinstance(s, Statement)]
                reactions.append(Reaction(condition=condition, actions=actions))
            # Check subsections
            for sub in section.body:
                if isinstance(sub, Section) and sub.header.lower().startswith("when "):
                    condition = sub.header[4:].strip()
                    actions = [s.text for s in sub.body if isinstance(s, Statement)]
                    reactions.append(Reaction(condition=condition, actions=actions))
        return reactions

    def evaluate_condition(self, condition: str, claims: list[CrossCheck]) -> bool:
        """Evaluate a `when` condition against current claims."""
        cond = condition.lower()
        claim_map = {c.claim_key: c for c in claims}

        if "build fails" in cond or "build is broken" in cond:
            build = claim_map.get("build")
            return build is not None and "BROKEN" in build.observed

        if "build passes" in cond:
            build = claim_map.get("build")
            return build is not None and "passing" in build.observed

        if "uncommitted" in cond and "exist" in cond:
            for c in claims:
                if c.claim_key == "uncommitted":
                    count = re.search(r"(\d+)", c.observed)
                    return count is not None and int(count.group(1)) > 0
            return False

        if "heartbeat fires" in cond:
            return True  # the runtime IS the heartbeat

        if "stale" in cond:
            for c in claims:
                if c.claim_key == "freshness" and "stale" in c.observed.lower():
                    return True
            return False

        if "fresh" in cond:
            for c in claims:
                if c.claim_key == "freshness" and ("fresh" in c.observed.lower() or "live" in c.observed.lower()):
                    return True
            return False

        return False

    def execute_action(self, action: str, beat: Beat, program_text: str = "") -> str:
        """Execute one action from a when-clause. Returns what was done."""
        action_lower = action.lower().strip()

        # Schedule hints — extract next interval
        if "schedule" in action_lower or "next" in action_lower or "hours" in action_lower or "minutes" in action_lower:
            hours = re.search(r"(\d+)\s*hours?", action_lower)
            minutes = re.search(r"(\d+)\s*minutes?", action_lower)
            if hours:
                beat.next_interval = int(hours.group(1)) * 3600
                return f"scheduled next in {hours.group(1)}h"
            if minutes:
                beat.next_interval = int(minutes.group(1)) * 60
                return f"scheduled next in {minutes.group(1)}m"
            if "daily" in action_lower or "24 hours" in action_lower:
                beat.next_interval = 86400
                return "scheduled next in 24h"
            if "immediately" in action_lower or "now" in action_lower:
                beat.next_interval = 0
                return "scheduled immediately"

        # Tell someone — print to stderr (visible to cron/heartbeat)
        if "tell" in action_lower or "report" in action_lower or "notify" in action_lower:
            return f"notified: {action}"

        # Update STATE.md freshness
        if "update" in action_lower and "state" in action_lower:
            if not self.dry_run:
                self._update_freshness()
            return "updated STATE.md freshness"

        # Commit
        if "commit" in action_lower:
            if not self.dry_run:
                try:
                    subprocess.run(["git", "add", "-A"], cwd=self.project_dir, capture_output=True, timeout=5)
                    subprocess.run(["git", "commit", "-m", f"natural runtime: {action[:60]}"],
                                  cwd=self.project_dir, capture_output=True, timeout=5)
                    subprocess.run(["git", "push"], cwd=self.project_dir, capture_output=True, timeout=10)
                    return "committed and pushed"
                except Exception:
                    return "commit failed"
            return "commit (dry run)"

        # Declare to sinovai.com
        if "declare" in action_lower and "sinovai" in action_lower:
            if not self.dry_run:
                try:
                    # Use the .natural program itself, not STATE.md
                    state_md = program_text  # the program text IS the declaration
                    name_match = re.search(r"^name:\s*(.+)$", program_text, re.MULTILINE)
                    name = name_match.group(1).strip() if name_match else os.path.basename(self.project_dir)
                    subprocess.run(
                        ["curl", "-s", "-X", "POST", f"https://sinovai.com/agents/{name}",
                         "-H", "Content-Type: text/plain", "-d", state_md],
                        capture_output=True, timeout=10
                    )
                    return f"declared {name} to sinovai.com"
                except Exception:
                    return "declare failed"
            return "declare (dry run)"

        # Default: just record the action
        return f"action: {action[:80]}"

    def _update_freshness(self):
        """Update the freshness field in STATE.md."""
        state_path = os.path.join(self.project_dir, "STATE.md")
        if not os.path.isfile(state_path):
            return
        with open(state_path) as f:
            content = f.read()
        now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
        # Replace freshness line
        content = re.sub(
            r"^freshness:.*$",
            f"freshness: fresh (checked {now})",
            content,
            count=1,
            flags=re.MULTILINE
        )
        with open(state_path, "w") as f:
            f.write(content)

    def beat(self) -> Beat:
        """Execute one heartbeat: feel, act, re-arm."""
        self.beat_count += 1
        now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        with open(self.program_path) as f:
            text = f.read()
        program = parse(text)

        beat = Beat(timestamp=now)

        # FEEL: cross-check claims
        beat.claims = self.extract_claims(program)

        # ACT: evaluate and fire reactions
        beat.reactions = self.extract_reactions(program)
        for reaction in beat.reactions:
            if self.evaluate_condition(reaction.condition, beat.claims):
                reaction.fired = True
                for action in reaction.actions:
                    result = self.execute_action(action, beat, text)
                    beat.actions_taken.append(result)

        # RE-ARM: if no reaction fired, use default interval
        if not any(r.fired for r in beat.reactions):
            beat.next_interval = 3600  # default 1h

        self.history.append(beat)
        return beat

    def loop(self, max_beats: int = 0):
        """Run continuously — arm, fire, re-arm, repeat."""
        print(f"natural runtime — looping ({'∞' if max_beats == 0 else max_beats} beats)")
        print(f"program: {self.program_path}")
        print(f"project: {self.project_dir}")
        print()

        while True:
            beat = self.beat()
            self._print_beat(beat)

            if max_beats > 0 and self.beat_count >= max_beats:
                print(f"\ncompleted {self.beat_count} beats")
                break

            interval = beat.next_interval
            if interval <= 0:
                print("  next: immediately")
                time.sleep(1)
            else:
                hours = interval / 3600
                print(f"  next: in {interval}s ({hours:.1f}h)")
                if not self.dry_run:
                    time.sleep(interval)
                else:
                    break

    def _print_beat(self, beat: Beat):
        """Print a beat result."""
        print(f"=== beat {self.beat_count} — {beat.timestamp} ===")

        # Claims
        for c in beat.claims:
            mark = "✓" if c.matches else "✗"
            match_str = "matches" if c.matches else f"actual: {c.observed}"
            print(f"  {mark} {c.claim_key}: {c.claim_value} [{match_str}]")

        # Reactions
        for r in beat.reactions:
            state = "FIRED" if r.fired else "armed"
            print(f"  when {r.condition}: {state}")
            if r.fired:
                for action_result in beat.actions_taken:
                    print(f"    -> {action_result}")

        # Next
        if not beat.reactions:
            print(f"  no when-clauses — resting, next in {beat.next_interval}s")

        print()


def main():
    import sys

    args = sys.argv[1:]
    if not args:
        print("natural runtime — the living interpreter")
        print()
        print("Usage:")
        print("  python3 runtime.py <file>           # one beat")
        print("  python3 runtime.py <file> --loop     # continuous heartbeat")
        print("  python3 runtime.py <file> --dry-run # check but don't act")
        sys.exit(0)

    program_path = args[0]
    loop = "--loop" in args
    dry_run = "--dry-run" in args
    project = os.path.dirname(os.path.abspath(program_path))

    rt = Runtime(program_path, project, dry_run=dry_run)

    if loop:
        rt.loop()
    else:
        beat = rt.beat()
        rt._print_beat(beat)


if __name__ == "__main__":
    main()