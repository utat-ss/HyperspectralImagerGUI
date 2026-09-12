"""
Enforce the CI contract that pytest's exit code cannot express.

pytest exits 0 whether a test passed or skipped, so a green run does not by
itself prove that a hardware-dependent backend skipped *for the right reason*
instead of, say, erroring during collection or quietly vanishing from the run.

This script reads the JUnit XML and asserts three things:

1. Nothing failed or errored.
2. Every hardware-dependent parametrization that appears is skipped, and
   carries a non-empty reason.
3. The backends that need no hardware actually ran -- a suite where
   MockCamera silently stopped being collected would otherwise look green.

Run:  python .github/scripts/verify_skips.py report.xml
"""

import sys
import xml.etree.ElementTree as ET

# Parametrizations that require a physical device. On a runner these must
# skip; they must never fail, and never silently disappear.
HARDWARE_PARAMS = ("thorlabs", "webcam")

# Parametrizations that must genuinely execute on every runner. If these stop
# being collected, the suite is no longer testing anything.
REQUIRED_PARAMS = ("mock",)


def param_of(name: str) -> str:
    """Extract 'mock' from 'test_something[mock]'. '' if unparametrized."""
    if name.endswith("]") and "[" in name:
        return name[name.rindex("[") + 1 : -1]
    return ""


def main(path: str) -> int:
    tree = ET.parse(path)
    cases = list(tree.iter("testcase"))
    if not cases:
        print(f"FAIL: {path} contains no test cases at all")
        return 1

    problems = []
    seen_required = set()

    for case in cases:
        name = case.get("name", "")
        param = param_of(name)
        failed = case.find("failure") is not None or case.find("error") is not None
        skipped = case.find("skipped")

        if failed:
            problems.append(f"{name}: failed or errored")
            continue

        if param in HARDWARE_PARAMS:
            if skipped is None:
                # Not a problem -- it means real hardware was reachable, which
                # is a bonus on a dev machine and impossible on a runner.
                continue
            reason = (skipped.get("message") or "").strip()
            if not reason:
                problems.append(f"{name}: skipped with no reason given")

        if param in REQUIRED_PARAMS:
            if skipped is not None:
                problems.append(f"{name}: must run on every runner, but skipped")
            else:
                seen_required.add(param)

    for required in REQUIRED_PARAMS:
        if required not in seen_required:
            problems.append(f"no '{required}' test ran -- suite is not testing anything")

    if problems:
        print(f"FAIL ({len(problems)} problem(s)) in {path}:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print(f"OK: {len(cases)} test cases in {path}")
    print("  no failures; hardware-dependent params skipped with reasons")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
