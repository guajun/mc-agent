#!/usr/bin/env python3
"""Offline tests for the issue-#21 minecart regression tooling.

No game, no network: these tests exercise config validation, the semantic
verifier (including every negative fail-closed path), the same-size jar
provenance rules and the scene-property override logic.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
ROOT = TOOLS.parent
sys.path.insert(0, str(TOOLS))

import rom21_regression as reg  # noqa: E402
import rom21_verify as rv  # noqa: E402

CONFIGS = ROOT / "examples" / "minecart-rom" / "regression" / "configs"


class ConfigTests(unittest.TestCase):
    def test_committed_configs_validate(self) -> None:
        paths = sorted(CONFIGS.glob("*.json"))
        self.assertGreaterEqual(len(paths), 2, "two calibrated configs are required")
        for path in paths:
            config = rv.load_config(path)
            self.assertEqual(config["expectedCartCount"], len(config["program"]["carts"]))
            self.assertEqual(rv.program_sha256(config["program"]["carts"]), config["programSha256"])

    def test_program_sha_is_order_sensitive(self) -> None:
        program = [{"items": [{"slot": 0, "id": "minecraft:stone", "count": 1}]},
                   {"items": [{"slot": 1, "id": "minecraft:dirt", "count": 2}]}]
        self.assertNotEqual(rv.program_sha256(program), rv.program_sha256(list(reversed(program))))

    def test_config_rejects_wrong_count(self) -> None:
        config = json.loads((CONFIGS / "calibrated-a.json").read_text(encoding="utf-8"))
        config["expectedCartCount"] += 1
        self.assertTrue(rv.validate_config(config))

    def test_config_rejects_tampered_program(self) -> None:
        config = json.loads((CONFIGS / "calibrated-b.json").read_text(encoding="utf-8"))
        config["program"]["carts"][0]["items"][0]["count"] += 1
        self.assertTrue(any("programSha256" in problem for problem in rv.validate_config(config)))


class VerifierTests(unittest.TestCase):
    def test_positive_and_negatives(self) -> None:
        self.assertEqual(rv.selftest(), 0)

    def test_offline_negative_cases_fail_closed(self) -> None:
        run, logger_rows, audit_rows, extras = rv._synthetic_run()
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "run.json").write_text(json.dumps(run), encoding="utf-8")
            (run_dir / "logger.jsonl").write_text(
                "\n".join(json.dumps(row) for row in logger_rows) + "\n", encoding="utf-8")
            (run_dir / "audit.jsonl").write_text(
                "\n".join(json.dumps(row) for row in audit_rows) + "\n", encoding="utf-8")
            (run_dir / "audit-check.json").write_text(json.dumps(extras["audit_check"]), encoding="utf-8")
            (run_dir / "restore-verify.json").write_text(json.dumps(extras["restore_verify"]), encoding="utf-8")
            for case in ("output_order_wrong", "audit_missing", "wrong_instance"):
                report = rv.offline_negative(run_dir, case)
                self.assertTrue(report["failClosed"], f"{case} did not fail closed: {report}")
                self.assertIn(report["expectedCheck"], report["failedChecks"])

    def test_offline_negative_without_run_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(Exception):
                rv.offline_negative(Path(tmp), "output_order_wrong")

    def test_answer_order_is_observed_not_assumed(self) -> None:
        run, logger_rows, audit_rows, extras = rv._synthetic_run()
        report = rv.verify_run(run, logger_rows, audit_rows, **extras)
        self.assertEqual(report["verdict"], "pass")
        self.assertEqual(report["observedPopOrder"], [cart["uuid"] for cart in report["oracle"]])
        self.assertIn("observed live", report["popOrderSource"])

    def test_causal_join_requires_exact_chain(self) -> None:
        run, logger_rows, audit_rows, extras = rv._synthetic_run()
        for row in audit_rows:
            if row.get("type") == "input_processed":
                row["orderingEvidence"] = False
        report = rv.verify_run(run, logger_rows, audit_rows, **extras)
        self.assertEqual(report["verdict"], "fail")
        self.assertIn("agent_operations", report["failures"])

    def test_causal_join_rejects_another_session(self) -> None:
        run, logger_rows, audit_rows, extras = rv._synthetic_run()
        for row in audit_rows:
            if row.get("type") == "input_request":
                row["session"] = "other-session"
        report = rv.verify_run(run, logger_rows, audit_rows, **extras)
        self.assertEqual(report["verdict"], "fail")
        self.assertIn("agent_operations", report["failures"])

    def test_cross_mod_digest_is_not_trusted(self) -> None:
        # The audit and logger digests are independent algorithms; the verifier
        # must compare normalized inventories, not digest strings.
        run, logger_rows, audit_rows, extras = rv._synthetic_run()
        for row in audit_rows:
            if row.get("type") in ("cart_exit", "cart_remove"):
                row["inventoryDigest"] = "audit-digest"
        report = rv.verify_run(run, logger_rows, audit_rows, **extras)
        self.assertEqual(report["verdict"], "pass")

    def test_missing_audit_session_fails(self) -> None:
        run, logger_rows, audit_rows, extras = rv._synthetic_run()
        audit_rows = [row for row in audit_rows if row.get("type") != "audit_end"]
        report = rv.verify_run(run, logger_rows, audit_rows, **extras)
        self.assertEqual(report["verdict"], "fail")
        self.assertIn("audit_oracle", report["failures"])


class DriverTests(unittest.TestCase):
    def test_scene_jvm_args_are_config_driven(self) -> None:
        scene = {"exitAxis": "x", "exitGreaterThan": 15.0}
        default = reg.scene_jvm_args(scene, 5, [])
        self.assertIn("-Dromlog.exitAxis=x", default)
        self.assertIn("-Dromlog.exitGreaterThan=15.0", default)
        self.assertIn("-Dromlog.expectedCarts=5", default)
        overridden = reg.scene_jvm_args(scene, 5, ["-Dromlog.exitGreaterThan=1.0E9"])
        self.assertNotIn("-Dromlog.exitGreaterThan=15.0", overridden)
        self.assertIn("-Dromlog.exitGreaterThan=1.0E9", overridden)

    def test_ports_are_inside_the_reserved_range(self) -> None:
        infos = reg.lab_info("test", 27200)
        for info in infos.values():
            for key in ("server", "rcon", "vantage", "bridge"):
                self.assertIn(info[key], reg.PORT_RANGE)

    def test_driver_selftest(self) -> None:
        self.assertEqual(reg.cmd_selftest(None), 0)

    @unittest.skipUnless(
        reg.AUDIT_JAR.is_file() and reg.INTERFACE_JAR.is_file(),
        "pinned game jars are host-local; the live provenance probe needs them",
    )
    def test_provenance_snapshot_shape(self) -> None:
        provenance = reg.provenance_snapshot("test")
        self.assertIn("head", provenance)
        self.assertIn("sources", provenance)
        self.assertIn("jars", provenance)
        self.assertEqual(provenance["bridge"]["pinned"], reg.BRIDGE_HEAD)


if __name__ == "__main__":
    unittest.main()
