#!/usr/bin/env python3
"""Offline tests for the issue-#21 minecart regression tooling.

No game, no network: these tests exercise config validation, the semantic
verifier (including every negative fail-closed path), the same-size jar
provenance rules and the scene-property override logic.
"""

from __future__ import annotations

import copy
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

    def test_synthetic_rejects_armed_outside_session(self) -> None:
        run, logger_rows, audit_rows, extras = rv._synthetic_run()
        session_start = next(row for row in audit_rows if row.get("type") == "session_start")
        for label, tick, wall in (("future", 999999, 4070908800000),
                                  ("cross-session", 0, session_start["wall"] - 5000)):
            with self.subTest(label=label):
                mutated = copy.deepcopy(logger_rows)
                for row in mutated:
                    if row.get("event") == "logger_armed":
                        row["tick"] = tick
                        row["wall"] = wall
                report = rv.verify_run(run, mutated, audit_rows, **extras)
                self.assertEqual(report["verdict"], "fail")
                self.assertIn("logger_session", report["failures"])

    def test_synthetic_rejects_source_observation_without_hashes(self) -> None:
        run, logger_rows, audit_rows, extras = rv._synthetic_run()
        for label, source in (
            ("present-only", {"source_save_present": True}),
            ("empty-objects", {"before": {}, "after": {}}),
            ("missing-after", {"before": run["sourceWorld"]["before"]}),
        ):
            with self.subTest(label=label):
                mutated = copy.deepcopy(run)
                mutated["sourceWorld"] = source
                report = rv.verify_run(mutated, logger_rows, audit_rows, **extras)
                self.assertEqual(report["verdict"], "fail")
                self.assertIn("source_world", report["failures"])

    def test_synthetic_rejects_wrong_capture_dimension(self) -> None:
        run, logger_rows, audit_rows, extras = rv._synthetic_run()
        mutated = copy.deepcopy(logger_rows)
        for row in mutated:
            if row.get("event") in ("cart_observed", "cart_void_capture"):
                row["dimension"] = "minecraft:the_nether"
        report = rv.verify_run(run, mutated, audit_rows, **extras)
        self.assertEqual(report["verdict"], "fail")
        self.assertIn("logger_session", report["failures"])

    def test_synthetic_rejects_wrong_audit_dimension(self) -> None:
        run, logger_rows, audit_rows, extras = rv._synthetic_run()
        mutated = copy.deepcopy(audit_rows)
        for row in mutated:
            if row.get("type") in ("cart_exit", "cart_remove"):
                row["level"] = "minecraft:the_nether"
        report = rv.verify_run(run, logger_rows, mutated, **extras)
        self.assertEqual(report["verdict"], "fail")
        self.assertIn("audit_oracle", report["failures"])

    def test_synthetic_rejects_capture_after_removal(self) -> None:
        run, logger_rows, audit_rows, extras = rv._synthetic_run()
        mutated_run = copy.deepcopy(run)
        mutated_run["operations"] = dict(run["operations"], transientMaxDeltaTicks=200)
        mutated_logger = copy.deepcopy(logger_rows)
        observed = [row for row in mutated_logger if row.get("event") == "cart_observed"]
        removes = [row for row in audit_rows if row.get("type") == "cart_remove"
                   and row.get("reason") == "DISCARDED"]
        for observation, removal in zip(observed, removes):
            observation["tick"] = removal["tick"] + 1
        report = rv.verify_run(mutated_run, mutated_logger, audit_rows, **extras)
        self.assertEqual(report["verdict"], "fail")
        self.assertIn("capture_timing", report["failures"])

    def test_synthetic_requires_agent_uuid(self) -> None:
        run, logger_rows, audit_rows, extras = rv._synthetic_run()
        mutated = copy.deepcopy(run)
        mutated.pop("agentUuid")
        report = rv.verify_run(mutated, logger_rows, audit_rows, **extras)
        self.assertEqual(report["verdict"], "fail")
        self.assertIn("run_manifest", report["failures"])

    def test_synthetic_requires_operator_match(self) -> None:
        run, logger_rows, audit_rows, extras = rv._synthetic_run()
        mutated = copy.deepcopy(audit_rows)
        for row in mutated:
            if row.get("type") == "input_processed":
                row["operator"] = {"uuid": "11111111-2222-3333-4444-555555555555", "name": "Other"}
        report = rv.verify_run(run, logger_rows, mutated, **extras)
        self.assertEqual(report["verdict"], "fail")
        self.assertIn("agent_operations", report["failures"])


COMMITTED_SAMPLES = sorted(
    (ROOT / "docs" / "evidence" / "rom21-regression" / "runs").glob("*/verify-*")
)


def load_verify_sample(path: Path) -> tuple[dict, list, list, dict, dict]:
    run = json.loads((path / "run.json").read_text(encoding="utf-8"))
    logger_rows = [
        json.loads(line)
        for line in (path / "logger.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    audit_rows = [
        json.loads(line)
        for line in (path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    audit_check = json.loads((path / "audit-check.json").read_text(encoding="utf-8"))
    restore = json.loads((path / "restore-verify.json").read_text(encoding="utf-8"))
    return run, logger_rows, audit_rows, audit_check, restore


@unittest.skipUnless(COMMITTED_SAMPLES, "committed regression samples are required")
class CommittedSampleTests(unittest.TestCase):
    """The coordinator-reproduced false positives, against the real samples."""

    def setUp(self) -> None:
        self.run, self.logger_rows, self.audit_rows, self.audit_check, self.restore = load_verify_sample(
            COMMITTED_SAMPLES[0]
        )

    def _verify(self, run=None, logger_rows=None, audit_rows=None) -> dict:
        return rv.verify_run(
            run if run is not None else self.run,
            logger_rows if logger_rows is not None else self.logger_rows,
            audit_rows if audit_rows is not None else self.audit_rows,
            audit_check=self.audit_check,
            restore_verify=self.restore,
        )

    def test_all_committed_generation_inputs_still_pass(self) -> None:
        for path in COMMITTED_SAMPLES:
            run, logger_rows, audit_rows, audit_check, restore = load_verify_sample(path)
            report = rv.verify_run(run, logger_rows, audit_rows, audit_check=audit_check, restore_verify=restore)
            self.assertEqual(report["verdict"], "pass", f"{path}: {report['problems']}")

    def test_real_sample_rejects_future_arm(self) -> None:
        logger_rows = copy.deepcopy(self.logger_rows)
        for row in logger_rows:
            if row.get("event") == "logger_armed":
                row["tick"] = 999999
                row["wall"] = 4070908800000
                row["at"] = "2099-01-01T00:00:00.000Z"
        report = self._verify(logger_rows=logger_rows)
        self.assertEqual(report["verdict"], "fail")
        self.assertIn("logger_session", report["failures"])

    def test_real_sample_rejects_late_and_cross_session_arm(self) -> None:
        first_input = next(row for row in self.audit_rows if row.get("type") == "input_processed")
        session_start = next(row for row in self.audit_rows if row.get("type") == "session_start")
        for label, tick, wall in (
            ("late", first_input["tick"] + 10, first_input["wall"] + 1000),
            ("cross-session", 0, session_start["wall"] - 5000),
        ):
            with self.subTest(label=label):
                logger_rows = copy.deepcopy(self.logger_rows)
                for row in logger_rows:
                    if row.get("event") == "logger_armed":
                        row["tick"] = tick
                        row["wall"] = wall
                report = self._verify(logger_rows=logger_rows)
                self.assertEqual(report["verdict"], "fail")
                self.assertIn("logger_session", report["failures"])

    def test_real_sample_rejects_source_observation_bypass(self) -> None:
        variants = {
            "present-only": {"source_save_present": True},
            "empty-objects": {"before": {}, "after": {}},
            "present-objects": {"before": {"source_save_present": True},
                                "after": {"source_save_present": True}},
            "missing-after": {"before": self.run["sourceWorld"]["before"]},
            "hash-mismatch": {"before": dict(self.run["sourceWorld"]["before"], tree_sha256="d" * 64),
                              "after": self.run["sourceWorld"]["after"]},
        }
        for label, source in variants.items():
            with self.subTest(label=label):
                run = copy.deepcopy(self.run)
                run["sourceWorld"] = source
                report = self._verify(run=run)
                self.assertEqual(report["verdict"], "fail")
                self.assertIn("source_world", report["failures"])

    def test_real_sample_rejects_wrong_capture_dimension(self) -> None:
        logger_rows = copy.deepcopy(self.logger_rows)
        for row in logger_rows:
            if row.get("event") in ("cart_observed", "cart_void_capture"):
                row["dimension"] = "minecraft:the_nether"
        report = self._verify(logger_rows=logger_rows)
        self.assertEqual(report["verdict"], "fail")
        self.assertIn("logger_session", report["failures"])

    def test_real_sample_rejects_wrong_audit_dimension(self) -> None:
        audit_rows = copy.deepcopy(self.audit_rows)
        for row in audit_rows:
            if row.get("type") in ("cart_exit", "cart_remove", "cart_tracked"):
                if "level" in row:
                    row["level"] = "minecraft:the_nether"
                if "dimension" in row:
                    row["dimension"] = "minecraft:the_nether"
        report = self._verify(audit_rows=audit_rows)
        self.assertEqual(report["verdict"], "fail")
        self.assertIn("audit_oracle", report["failures"])

    def test_real_sample_rejects_missing_agent_identity(self) -> None:
        run = copy.deepcopy(self.run)
        run.pop("agentUuid")
        report = self._verify(run=run)
        self.assertEqual(report["verdict"], "fail")
        self.assertIn("run_manifest", report["failures"])
        audit_rows = copy.deepcopy(self.audit_rows)
        for row in audit_rows:
            if row.get("type") == "input_processed":
                row["operator"] = {"uuid": "11111111-2222-3333-4444-555555555555", "name": "Other"}
        report = self._verify(audit_rows=audit_rows)
        self.assertEqual(report["verdict"], "fail")
        self.assertIn("agent_operations", report["failures"])

    def test_real_sample_rejects_capture_after_removal(self) -> None:
        run = copy.deepcopy(self.run)
        run["operations"] = dict(run["operations"], transientMaxDeltaTicks=200)
        logger_rows = copy.deepcopy(self.logger_rows)
        observed = [row for row in logger_rows if row.get("event") == "cart_observed"]
        removes = [
            row for row in self.audit_rows
            if row.get("type") == "cart_remove" and row.get("reason") == "DISCARDED"
        ]
        self.assertEqual(len(observed), len(removes))
        for observation, removal in zip(observed, removes):
            observation["tick"] = removal["tick"] + 1
        report = self._verify(run=run, logger_rows=logger_rows)
        self.assertEqual(report["verdict"], "fail")
        self.assertIn("capture_timing", report["failures"])

    def test_real_sample_offline_negatives_still_fail_closed(self) -> None:
        for case in ("output_order_wrong", "audit_missing", "wrong_instance"):
            report = rv.offline_negative(COMMITTED_SAMPLES[0], case)
            self.assertTrue(report["failClosed"], f"{case}: {report}")
            self.assertIn(report["expectedCheck"], report["failedChecks"])


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
