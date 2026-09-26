#!/usr/bin/env python3
"""Demo for the Minecart ROM regression, built only from committed artifacts.

Reads the committed issue-#21 evidence package (no game, no network, no model)
and prints:

* the **regression** results - per run/generation: restore, audit verdict,
  observed pop order and the ordered inventories captured live by the agent
  logger, with cold/jar-iteration flags clearly labelled;
* the **negative** table with each probe's injection and fail-closed reason;
* the **original autonomous** cold-start answer from the #20 package, clearly
  labelled as model-capability evidence that this regression did not re-test.

    python examples/minecart-rom/regression/demo/demo.py
    python examples/minecart-rom/regression/demo/demo.py --evidence docs/evidence/rom21-regression
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


def read_json(path: Path, default=None):
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def items_text(items) -> str:
    parts = []
    for item in items or []:
        if isinstance(item, (list, tuple)) and len(item) == 3:
            parts.append(f"{item[1]} x{item[2]} (slot {item[0]})")
        else:
            parts.append(f"{item.get('id')} x{item.get('count')} (slot {item.get('slot')})")
    return ", ".join(parts)


def print_regression(evidence: Path) -> bool:
    manifest = read_json(evidence / "manifest.json")
    if manifest is None:
        print(f"error: no committed regression evidence at {evidence}", file=sys.stderr)
        return False
    print("=" * 78)
    print("REGRESSION RESULTS (scripted real-game runs; no model was called)")
    print("=" * 78)
    ok = True
    for run in manifest["runs"]:
        run_dir = evidence / "runs" / run["runId"]
        record = read_json(run_dir / "run.json", {})
        flags = []
        if record.get("cold"):
            flags.append("cold download")
        if record.get("jarIteration"):
            flags.append("same-size jar iteration")
        print(f"\nrun {run['runId']}  config={run['config']}  {'; '.join(flags) or 'warm cache'}")
        print(f"  frozen HEAD: {record.get('provenanceStart', {}).get('head', '?')[:40]}  "
              f"clean={record.get('provenanceStart', {}).get('clean')}")
        for generation in record.get("generations", []):
            verdict = generation.get("verificationVerdict")
            ok = ok and verdict == "pass"
            print(f"  [{generation['label']}] logger session {generation['ordinal']}, "
                  f"jar {generation['loggerJar']['sha256'][:12]} ({generation['loggerJar']['size']} B), "
                  f"audit={generation['auditCheck']['verdict']}, verify={verdict}")
            for index, cart in enumerate(generation.get("answer", []), start=1):
                print(f"      {index}. {cart['uuid']}  {items_text(cart['items'])}")
    return ok


def print_negatives(evidence: Path) -> bool:
    summary = read_json(evidence / "negatives" / "summary.json")
    if summary is None:
        print("error: no committed negative summary", file=sys.stderr)
        return False
    print("\n" + "=" * 78)
    print("DECLARED NEGATIVES (each must fail closed)")
    print("=" * 78)
    ok = True
    for case in summary["cases"]:
        ok = ok and bool(case["failClosed"])
        print(f"  [{'ok' if case['failClosed'] else 'FAIL'}] {case['case']:<24} {case['kind']:<7} "
              f"injected: {case['injected']}")
        print(f"       -> {case['verdict']}, failing checks: {', '.join(case.get('failedChecks') or [])}")
    print(f"  all fail-closed: {summary['allFailClosed']}")
    return ok and summary["allFailClosed"]


def print_original() -> None:
    answer = read_json(ROOT / "docs" / "evidence" / "rom20-coldstart" / "result" / "answer.json")
    print("\n" + "=" * 78)
    print("ORIGINAL AUTONOMOUS COLD START (#20, model capability evidence)")
    print("This was produced by the autonomous model session, NOT by the regression;")
    print("the regression does not re-test model autonomy.")
    print("=" * 78)
    if answer is None:
        print("  (#20 answer artifact not present in this checkout)")
        return
    print(f"  run {answer['run_id']}: {answer.get('method', '')[:100]}...")
    for cart in answer["carts"]:
        print(f"      {cart['order']}. {cart['uuid']}  {items_text(cart['items'])}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--evidence", default=str(ROOT / "docs" / "evidence" / "rom21-regression"))
    args = parser.parse_args(argv)
    evidence = Path(args.evidence)
    if not evidence.is_absolute():
        evidence = ROOT / evidence
    regression_ok = print_regression(evidence)
    negatives_ok = print_negatives(evidence)
    print_original()
    print("\n" + "=" * 78)
    print(f"demo verdict: {'PASS' if regression_ok and negatives_ok else 'FAIL'}")
    return 0 if regression_ok and negatives_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
