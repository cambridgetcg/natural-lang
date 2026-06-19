#!/usr/bin/env python3
"""natural — the CLI for the natural language programming language."""
import sys, os, json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from parser import parse, to_state_dict
from interpreter import execute, format_result

def main():
    if len(sys.argv) < 2:
        print("natural — a programming language where code is English")
        print()
        print("Usage:")
        print("  natural parse <file>     Parse a .natural file, output JSON")
        print("  natural run <file>       Execute a .natural file (cross-check claims)")
        print("  natural discover         Discover all STATE.md files on the machine")
        sys.exit(0)
    
    cmd = sys.argv[1]
    
    if cmd == "parse" and len(sys.argv) > 2:
        with open(sys.argv[2]) as f:
            text = f.read()
        program = parse(text)
        state = to_state_dict(program)
        print(json.dumps(state, indent=2))
    
    elif cmd == "run" and len(sys.argv) > 2:
        path = sys.argv[2]
        project = os.path.dirname(os.path.abspath(path))
        result = execute(path, project)
        print(format_result(result))
    
    elif cmd == "discover":
        os.system("python3 " + os.path.expanduser("~/.hermes/scripts/discover.py"))
    
    else:
        print(f"unknown command: {cmd}")
        sys.exit(1)

if __name__ == "__main__":
    main()
