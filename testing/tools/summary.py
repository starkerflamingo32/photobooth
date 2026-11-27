#!/usr/bin/env python3
"""Markdown run summary from a Playwright JSON report (for $GITHUB_STEP_SUMMARY).

  summary.py <test-results dir>

Lists failed tests with their first error line, and every annotation of type
"answer" (characterisation answers, FSD §17.5) with the test it came from.
"""

import json
import sys
from pathlib import Path


def main():
    results = Path(sys.argv[1]) / "results.json"
    if not results.exists():
        print("No Playwright report (%s): the suite did not run." % results)
        return 1
    report = json.loads(results.read_text(encoding="utf-8"))
    passed, failed, answers = [], [], []

    def walk(suite, prefix=""):
        for spec in suite.get("specs", []):
            for t in spec.get("tests", []):
                res = t.get("results", [{}])[-1]
                title = spec["title"]
                for a in t.get("annotations", []) + res.get("annotations", []):
                    if a.get("type") == "answer":
                        answers.append((title, a.get("description", "")))
                if res.get("status") == "passed":
                    passed.append(title)
                else:
                    err = (res.get("error") or {}).get(
                        "message", res.get("status", "?")
                    )
                    failed.append((title, err.strip().splitlines()[0] if err else ""))
        for s in suite.get("suites", []):
            walk(s)

    for s in report.get("suites", []):
        walk(s)
    print("**%d passed, %d failed**\n" % (len(passed), len(failed)))
    if failed:
        print("| Failed test | Error |\n|---|---|")
        for title, err in failed:
            print("| %s | %s |" % (title, err.replace("|", "\\|")[:200]))
    if answers:
        print("\n| Test | Answer |\n|---|---|")
        for title, text in answers:
            print("| %s | %s |" % (title, text.replace("|", "\\|")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
