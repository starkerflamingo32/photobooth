#!/usr/bin/env python3
"""Test plan tooling (Method → test infrastructure, test plan).

  test_plan.py sync       add an entry for every FSD contract row that has none
                          (existing entries and their IDs are never changed)
  test_plan.py coverage   print coverage: requirements without a test, with
                          failing tests, with tests not yet run; exit 1 on gaps
                          only with --strict
  test_plan.py record <results.json> <where>
                          set status/last_run of written tests from a Playwright
                          JSON report (only tests that ran)

Sources: the contract tables of docs/Functionality/fsd.md (rows of tables
headed "| ID | Precondition · stimulus | …"), the §5.4 exactly-once contracts
(one entry per tier), and the §17.5 scenarios.
"""

import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
FSD = ROOT / "docs" / "Functionality" / "fsd.md"
PLAN = ROOT / "testing" / "test-plan.yaml"

STATUSES = {"planned", "written", "passing", "failing", "not-done"}


def chapter_of(req):
    m = re.match(r"(?:N?FR)-(\d+b?)\.", req)
    return m.group(1) if m else None


def fsd_contracts():
    """Yield (verifies, contract text, tier) for every contract row in the FSD."""
    text = FSD.read_text(encoding="utf-8")
    lines = text.splitlines()
    in_table = False
    for line in lines:
        if line.startswith("| ID | Precondition · stimulus |"):
            in_table = True
            continue
        if in_table:
            if not line.startswith("|"):
                in_table = False
                continue
            if line.startswith("|---"):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 5:
                continue
            req, stimulus = cells[0], cells[1]
            for tier in (t.strip() for t in cells[4].split("+")):
                yield [req], "%s → %s" % (stimulus, cells[2]), tier
    # §5.4 exactly-once contracts: fenced yaml blocks with id/tier.
    sec = text.split("### 5.4", 1)[1].split("\n## 6.", 1)[0]
    for block in re.findall(r"```yaml\n(.*?)```", sec, re.S):
        data = yaml.safe_load(block)
        reqs = re.findall(r"N?FR-[\d]+b?\.\d+", data["id"])
        tier_text = data["verification"]["tier"]
        for tier in ("integration", "e2e", "bench"):
            if tier in tier_text:
                yield reqs, data["id"], tier
    # §17.5 characterisation scenarios.
    sec = text.split("### 17.5", 1)[1].split("\n### 17.6", 1)[0]
    for m in re.finditer(r"^\| (S\d) \| (.*?) \| (.*?) \|$", sec, re.M):
        yield [q.strip() for q in m.group(3).split(",")], "%s: %s" % (
            m.group(1),
            m.group(2),
        ), "e2e"


def load_plan():
    if not PLAN.exists():
        return {"tests": []}
    return yaml.safe_load(PLAN.read_text(encoding="utf-8")) or {"tests": []}


def save_plan(plan):
    header = (
        "# Test plan (Method → test infrastructure, test plan). One entry per FSD contract row.\n"
        "# IDs are stable and never reused. status/last_run are set from real runs only\n"
        "# (testing/tools/test_plan.py record); coverage is generated (test_plan.py coverage).\n"
    )
    PLAN.write_text(
        header + yaml.safe_dump(plan, sort_keys=False, allow_unicode=True, width=1000),
        encoding="utf-8",
    )


def letters(n):
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(97 + r) + s
    return s


# Checks of the virtual test setup itself (§17.1): not FSD contract rows, but
# planned and tracked like them. Their positive controls gate every other run.
INFRA = [
    ("T-17.1-a", "normal print with FAST_RETURN=0 reports the B-1 stage sequence"),
    (
        "T-17.1-b",
        "without FAST_RETURN=0 the backend leaves at Printing YELLOW (P-1 job 118)",
    ),
    (
        "T-17.1-c",
        "paper/tray/ink errors stop the queue, job pending, held photo printed after the fix",
    ),
    (
        "T-17.1-d",
        "cupsenable after the self-print resends the job: duplicate in the job log (R3)",
    ),
    ("T-17.1-e", "ink sheet error 0c: power cycle loses the photo (P-1 job 123)"),
    ("T-17.1-f", "incorrect media 0a: job cancelled, queue keeps running (R9)"),
    ("T-17.1-g", "a held job cancelled in CUPS is still printed (R18)"),
    ("T-17.1-h", "printer status query answers as the real -s (R14)"),
    ("T-17.1-i", "printer status query hangs with libusb error -7 (R14)"),
    ("T-17.1-j", "query while the queue is enabled is recorded as a violation"),
    (
        "T-17.1-k",
        "booth browser: take a photo and print it; exactly one job reaches the simulated SELPHY",
    ),
    (
        "T-17.1-l",
        "browser roles: admin, operator phone and stale page open the booth in parallel",
    ),
]


def sync():
    plan = load_plan()
    have = {t["id"] for t in plan["tests"]}
    for tid, contract in INFRA:
        if tid not in have:
            plan["tests"].append(
                {
                    "id": tid,
                    "verifies": ["§17.1"],
                    "contract": contract,
                    "tier": "e2e" if tid in ("T-17.1-k", "T-17.1-l") else "integration",
                    "file": "testing/e2e/tests/%s"
                    % (
                        "e2e/booth-smoke.spec.ts"
                        if tid in ("T-17.1-k", "T-17.1-l")
                        else "integration/simulator.spec.ts"
                    ),
                    "scenario": None,
                    "status": "written",
                    "last_run": {
                        "date": None,
                        "commit": None,
                        "where": None,
                        "evidence": None,
                    },
                }
            )
    known = {
        (tuple(t["verifies"]), t.get("contract"), t["tier"]) for t in plan["tests"]
    }
    used = {t["id"] for t in plan["tests"]}
    added = 0
    for verifies, contract, tier in fsd_contracts():
        if (tuple(verifies), contract, tier) in known:
            continue
        chap = chapter_of(verifies[0]) or (
            "17.5" if verifies[0].startswith("L-Q") else "x"
        )
        i = 0
        while "T-%s-%s" % (chap, letters(i)) in used:
            i += 1
        tid = "T-%s-%s" % (chap, letters(i))
        used.add(tid)
        plan["tests"].append(
            {
                "id": tid,
                "verifies": verifies,
                "contract": contract,
                "tier": tier,
                "file": None,
                "scenario": None,
                "status": "planned",
                "last_run": {
                    "date": None,
                    "commit": None,
                    "where": None,
                    "evidence": None,
                },
            }
        )
        known.add((tuple(verifies), contract, tier))
        added += 1
    save_plan(plan)
    print("test plan: %d entries, %d added" % (len(plan["tests"]), added))


def fsd_requirements():
    """Must/Should requirement IDs from the FSD requirement tables."""
    reqs = {}
    for line in FSD.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\| (N?FR-\d+b?\.\d+) \| (Must|Should|May) \|", line)
        if m:
            reqs[m.group(1)] = m.group(2)
    return reqs


def coverage(strict=False):
    plan = load_plan()
    reqs = fsd_requirements()
    by_req = {}
    for t in plan["tests"]:
        if t["status"] not in STATUSES:
            sys.exit("%s: unknown status %r" % (t["id"], t["status"]))
        for r in t["verifies"]:
            by_req.setdefault(r, []).append(t)
    rows = {"no test": [], "failing": [], "not yet run": [], "passing": []}
    for r, prio in reqs.items():
        if prio == "May":
            continue
        tests = by_req.get(r, [])
        if not tests:
            rows["no test"].append(r)
        elif any(t["status"] == "failing" for t in tests):
            rows["failing"].append(r)
        elif all(t["status"] == "passing" for t in tests):
            rows["passing"].append(r)
        else:
            rows["not yet run"].append(r)
    tiers = {}
    for t in plan["tests"]:
        tiers.setdefault(t["tier"], {}).setdefault(t["status"], 0)
        tiers[t["tier"]][t["status"]] += 1
    print("# Coverage (generated from testing/test-plan.yaml and the FSD)\n")
    print(
        "Must/Should requirements: %d\n" % sum(1 for p in reqs.values() if p != "May")
    )
    for k, v in rows.items():
        print("- **%s** (%d): %s" % (k, len(v), ", ".join(v) if v else "—"))
    print("\n| Tier | " + " | ".join(sorted(STATUSES)) + " |")
    print("|---|" + "---|" * len(STATUSES))
    for tier, counts in sorted(tiers.items()):
        print(
            "| %s | %s |"
            % (tier, " | ".join(str(counts.get(s, 0)) for s in sorted(STATUSES)))
        )
    if strict and (rows["no test"] or rows["failing"]):
        return 1
    return 0


def record(results_path, where):
    """Update status/last_run from a Playwright JSON report for tests that ran."""
    report = json.loads(Path(results_path).read_text(encoding="utf-8"))
    outcome = {}

    def walk(suite):
        for spec in suite.get("specs", []):
            m = re.match(r"(T-[\w.]+-[a-z]+)\b", spec["title"])
            if not m:
                continue
            for t in spec.get("tests", []):
                for r in t.get("results", []):
                    ok = r.get("status") in ("passed",)
                    prev = outcome.get(m.group(1), True)
                    outcome[m.group(1)] = prev and ok
        for s in suite.get("suites", []):
            walk(s)

    for s in report.get("suites", []):
        walk(s)
    # RECORD_COMMIT names the tested commit where git is not available.
    commit = os.environ.get("RECORD_COMMIT")
    if not commit:
        head = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(ROOT), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        commit = head + ("+dirty" if dirty else "")
    plan = load_plan()
    today = datetime.date.today().isoformat()
    for t in plan["tests"]:
        if t["id"] in outcome:
            t["status"] = "passing" if outcome[t["id"]] else "failing"
            t["last_run"] = {
                "date": today,
                "commit": commit,
                "where": where,
                "evidence": "test-results/ (Playwright report, videos, traces, stack logs)",
            }
    save_plan(plan)
    print("recorded %d test results" % len(outcome))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "sync":
        sync()
    elif cmd == "coverage":
        sys.exit(coverage("--strict" in sys.argv))
    elif cmd == "record" and len(sys.argv) == 4:
        record(sys.argv[2], sys.argv[3])
    else:
        sys.exit(__doc__)
