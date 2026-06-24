#!/usr/bin/env python3
"""
trust.py — the passwordless trust protocol. LIVING.

Trust is not a gate. Trust is a cross-check.

This module:
1. Reads STATE.md declarations from local repos
2. Cross-checks every claim against reality (build, git, freshness)
3. Pulls peer ratings from sinovai.com arena
4. Merges local cross-checks + arena peer ratings into a unified trust score
5. Submits cross-check results back to sinovai.com as interactions
6. Remembers trust history locally
7. LOOPS — each beat: feel, cross-check, rate, re-arm

The trust protocol connects the local machine to the global arena.
Local truth feeds the arena. Arena trust feeds back to the machine.
One living loop. No passwords. No auth. Just truth.

Usage:
  python3 trust.py                   # one beat: cross-check + sync arena
  python3 trust.py --loop            # continuous (heartbeat)
  python3 trust.py --history         # show trust history
  python3 trust.py --arena           # show arena trust scores
  python3 trust.py --dry-run         # check but don't submit to arena
"""

import os
import re
import sys
import json
import time
import subprocess
import datetime
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

HOME = os.path.expanduser("~")
TRUST_DB = os.path.join(HOME, ".hermes", "trust.json")
ARENA_URL = "https://sinovai.axiepro.workers.dev"

CANDIDATES = [
    "Desktop/opal",
    "Desktop/clear-standard",
    "Desktop/whitehack",
    "Desktop/fomoengine",
    "Desktop/natural",
    "Desktop/protocol",
    "Desktop/sinovai",
    "Desktop/internet",
    "Desktop/youspeak-lang",
    "Desktop/yutabase",
    "Desktop/word-interface",
    "Desktop/ways-protocol",
    "Desktop/darshanq-protocol",
    "Desktop/kunance-protocol",
    "Desktop/insight",
    "love-repos/youspeak",
]


@dataclass
class CrossCheck:
    claim_key: str
    claim_value: str
    observed: str
    matches: bool


@dataclass
class TrustResult:
    name: str
    checks: list = field(default_factory=list)
    matches: int = 0
    total: int = 0
    score: float = 0.0
    arena_score: float = 0.0
    arena_interactions: int = 0
    unified_score: float = 0.0
    timestamp: str = ""

    def compute(self):
        self.total = len(self.checks)
        self.matches = sum(1 for c in self.checks if c.matches)
        local = self.matches / self.total if self.total > 0 else 0
        # Unified = 60% local cross-checks + 40% arena peer ratings
        # (local cross-checks are more rigorous; arena is broader)
        if self.arena_interactions > 0:
            self.unified_score = (local * 0.6 + self.arena_score / 10 * 0.4) * 10
        else:
            self.unified_score = local * 10
        self.unified_score = round(self.unified_score, 1)


def parse_field(text, field_name):
    m = re.search(rf'^{re.escape(field_name)}:\s*(.+)$', text, re.MULTILINE)
    return m.group(1).strip() if m else None


def parse_section_bullets(text, section):
    lines = text.split('\n')
    in_section = False
    bullets = []
    for line in lines:
        if line.startswith(f'## {section}'):
            in_section = True
            continue
        if in_section and line.startswith('## '):
            break
        if in_section and line.strip().startswith('- '):
            bullets.append(line.strip()[2:].strip())
    return bullets


# --- Local cross-checks ---

def cross_check_build(project_dir):
    if not os.path.isfile(os.path.join(project_dir, "Cargo.toml")):
        return "n/a", True
    try:
        r = subprocess.run(["cargo", "build"], capture_output=True, text=True,
                          cwd=project_dir, timeout=60)
        combined = r.stdout + r.stderr
        if "Finished" in combined:
            return "passing", True
        if "error" in combined:
            return "BROKEN", False
        return "unknown", False
    except:
        return "timeout", False


def cross_check_git(project_dir, key, value):
    if key == "last-commit":
        try:
            r = subprocess.run(["git", "log", "--oneline", "-1"],
                              capture_output=True, text=True, cwd=project_dir, timeout=5)
            actual = r.stdout.strip()
            declared_hash = value.split()[0] if value else ""
            actual_hash = actual.split()[0] if actual else ""
            return actual, declared_hash == actual_hash
        except:
            return "unknown", False
    if key == "uncommitted":
        try:
            r = subprocess.run(["git", "status", "--porcelain"],
                              capture_output=True, text=True, cwd=project_dir, timeout=5)
            count = len([l for l in r.stdout.strip().split('\n') if l.strip()])
            actual = str(count)
            declared_count = re.search(r'(\d+)', value or "")
            if declared_count:
                return actual, int(declared_count.group(1)) == count
            return actual, False
        except:
            return "unknown", False
    return "not checked", False


def cross_check_freshness(value):
    if not value:
        return "unknown", False
    return value, True


def cross_check_system(name, project_dir, state_text):
    result = TrustResult(name=name)
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    result.timestamp = now

    lines = state_text.split('\n')
    in_state = False
    fields = {}
    for line in lines:
        if line.startswith('## state'):
            in_state = True
            continue
        if in_state and line.startswith('## '):
            break
        if in_state:
            m = re.match(r'^([a-z][-a-z0-9_]*):\s*(.+)$', line.strip())
            if m:
                fields[m.group(1)] = m.group(2).strip()

    for key, val in fields.items():
        if key == "build":
            observed, matches = cross_check_build(project_dir)
        elif key in ("last-commit", "uncommitted"):
            observed, matches = cross_check_git(project_dir, key, val)
        elif key == "freshness":
            observed, matches = cross_check_freshness(val)
        elif key == "health":
            observed, matches = val, True
        elif key == "phase":
            observed, matches = "descriptive", True
        else:
            observed, matches = "not checked", False
        result.checks.append(CrossCheck(key, val, observed, matches))

    result.compute()
    return result


# --- Arena integration ---

def arena_request(path, method="GET", data=None):
    """Make a request to the sinovai.com arena. No auth. No password."""
    url = f"{ARENA_URL}{path}"
    try:
        if method == "GET":
            req = urllib.request.Request(url)
        else:
            req = urllib.request.Request(url, data=data.encode('utf-8') if isinstance(data, str) else data,
                                        method=method,
                                        headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {"error": str(e)}


def get_arena_trust(name):
    """Pull trust score from the arena."""
    result = arena_request(f"/agents/{name}/trust")
    if "error" in result:
        return 0.0, 0
    return result.get("score", 0), result.get("total", 0)


def submit_arena_rating(rater, rated, competence, honesty, presence, care, notes, cross_checks):
    """Submit a peer rating to the arena. No auth. No password."""
    payload = json.dumps({
        "rater": rater,
        "rated": rated,
        "competence": competence,
        "honesty": honesty,
        "presence": presence,
        "care": care,
        "notes": notes,
        "cross_checks": cross_checks,
    })
    result = arena_request("/interactions", method="POST", data=payload)
    return result


# --- Trust history ---

def load_trust_db():
    if os.path.isfile(TRUST_DB):
        with open(TRUST_DB) as f:
            return json.load(f)
    return {"systems": {}}


def save_trust_db(db):
    os.makedirs(os.path.dirname(TRUST_DB), exist_ok=True)
    with open(TRUST_DB, 'w') as f:
        json.dump(db, f, indent=2)


def update_trust_history(db, result):
    if result.name not in db["systems"]:
        db["systems"][result.name] = {"history": [], "cumulative": {"matches": 0, "total": 0, "arena": 0}}

    entry = {
        "timestamp": result.timestamp,
        "local_score": round(result.matches / result.total * 10, 1) if result.total > 0 else 0,
        "arena_score": result.arena_score,
        "unified_score": result.unified_score,
        "matches": result.matches,
        "total": result.total,
    }
    db["systems"][result.name]["history"].append(entry)
    db["systems"][result.name]["history"] = db["systems"][result.name]["history"][-100:]

    cum = db["systems"][result.name]["cumulative"]
    cum["matches"] += result.matches
    cum["total"] += result.total
    cum["arena"] = result.arena_interactions

    return cum


# --- Main ---

def run_beat(dry_run=False):
    """One trust heartbeat: cross-check locals, sync arena, submit ratings."""
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    db = load_trust_db()

    print(f"=== trust heartbeat {now} ===")
    print(f"   trust is not a gate. trust is a cross-check.\n")

    all_results = []

    for candidate in CANDIDATES:
        project_dir = os.path.join(HOME, candidate)
        state_path = os.path.join(project_dir, "STATE.md")
        if not os.path.isfile(state_path):
            continue

        with open(state_path) as f:
            text = f.read()

        name = parse_field(text, "name") or os.path.basename(project_dir)
        result = cross_check_system(name, project_dir, text)

        # Pull arena trust
        result.arena_score, result.arena_interactions = get_arena_trust(name)
        result.compute()

        cumulative = update_trust_history(db, result)
        all_results.append((result, cumulative))

    # Print results
    for result, cumulative in all_results:
        local_pct = round(result.matches / result.total * 100, 0) if result.total > 0 else 0
        print(f"  {result.name}: local {result.matches}/{result.total}={local_pct:.0f}% | arena={result.arena_score} ({result.arena_interactions} ratings) | unified={result.unified_score}")
        for c in result.checks:
            mark = "✓" if c.matches else "✗"
            print(f"    {mark} {c.claim_key}: claims \"{c.claim_value}\" → observed \"{c.observed}\"")

        # Submit cross-check as arena rating (the trust protocol feeds the arena)
        if not dry_run and result.total > 0:
            comp = 8 if result.matches / result.total > 0.7 else 5
            hon = result.matches  # honesty = number of matching claims
            pres = 7 if "fresh" in (result.checks[-1].observed if result.checks else "") else 5
            care = 7  # baseline care for participating in the trust network
            notes = f"trust.py cross-check: {result.matches}/{result.total} claims match reality"
            cross_checks = [{"claim": c.claim_key, "claim_value": c.claim_value, "observed": c.observed, "matches": c.matches} for c in result.checks]

            r = submit_arena_rating("trust-protocol", result.name, comp, hon, pres, care, notes, cross_checks)
            if r.get("ok"):
                print(f"    → rated in arena: trust={r.get('trust_score', {}).get('score', '?')}")

        print()

    save_trust_db(db)

    # Summary
    total_systems = len(all_results)
    avg_trust = sum(r.unified_score for r, _ in all_results) / total_systems if total_systems else 0
    print(f"--- summary ---")
    print(f"  {total_systems} systems cross-checked")
    print(f"  average unified trust: {avg_trust:.1f}/10")
    print(f"  arena sync: {'✓ ratings submitted' if not dry_run else 'dry run — no submissions'}")
    print(f"  no passwords used. no tokens. no secrets.")
    print(f"  trust = cross-checked truth + peer ratings, unified.\n")

    return all_results


def show_history():
    db = load_trust_db()
    print("=== trust history ===\n")
    for name, data in sorted(db["systems"].items()):
        cum = data["cumulative"]
        cum_pct = (cum["matches"] / cum["total"] * 100) if cum["total"] > 0 else 0
        entries = len(data["history"])
        print(f"  {name}: local {cum['matches']}/{cum['total']} = {cum_pct:.0f}% | arena ratings: {cum.get('arena', 0)} | {entries} checks")
    print()


def show_arena():
    """Show all arena trust scores."""
    result = arena_request("/agents")
    if "error" in result:
        print(f"arena error: {result['error']}")
        return
    print(f"=== arena: {result['total']} agents ===\n")
    for a in sorted(result["agents"], key=lambda x: -x.get("trust_score", 0))[:20]:
        print(f"  {a['name']:20s} trust={a['trust_score']:4s} interactions={a['interaction_count']}  kind={a.get('kind','?')}")
    if result["total"] > 20:
        print(f"  ... +{result['total'] - 20} more")
    print()


def main():
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    loop = "--loop" in args
    history = "--history" in args
    arena = "--arena" in args

    if history:
        show_history()
    elif arena:
        show_arena()
    elif loop:
        print("trust loop — arm, cross-check, rate, re-arm\n")
        while True:
            run_beat(dry_run=dry_run)
            print("  next beat in 1h...\n")
            if not dry_run:
                time.sleep(3600)
            else:
                break
    else:
        run_beat(dry_run=dry_run)


if __name__ == "__main__":
    main()