#!/usr/bin/env python3
"""Offline tests for the rom20 operator preparation recipe.

    python -m unittest discover -s tools/tests -v

These tests only exercise the pure guards/helpers of
``examples/coldstart/prepare-rom20.py``.  They never launch Minecraft, never
start a lab or bridge, and never touch the network.
"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
PREP_PATH = ROOT / "examples" / "coldstart" / "prepare-rom20.py"

_spec = importlib.util.spec_from_file_location("prepare_rom20", PREP_PATH)
prepare = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(prepare)

LAUNCH_ENV = {
    "pi": {"command": "C:/pi/pi.cmd", "version": "0.87.1"},
    "model": {"provider": "deepseek", "model": "deepseek/deepseek-flash", "thinking": "max"},
    "sessions": {"agent_raw": "F:/sessions/agent.jsonl"},
}

MANIFEST = {
    "game": {"minecraft": "26.2", "fabric_loader": "0.19.5", "fabric_installer": "1.1.2"},
    "mods": [
        {"name": "fabric-api", "version": "0.161.0+26.2", "filename": "fabric-api-0.161.0+26.2.jar"},
        {"name": "carpet", "version": "26.2+v260616", "filename": "fabric-carpet-26.2+v260616.jar"},
        {"name": "mc-agent-interface-mod", "version": "0.6.0"},
    ],
}


class LaunchCommandTests(unittest.TestCase):
    def test_powershell_with_explicit_session(self) -> None:
        text = prepare.launch_command(LAUNCH_ENV, Path("F:/ws"))
        self.assertIn("Set-Location -LiteralPath", text)
        self.assertIn('& "C:/pi/pi.cmd"', text)
        self.assertIn(f'--session "{Path(LAUNCH_ENV["sessions"]["agent_raw"])}"', text)
        self.assertIn("--print --no-context-files", text)
        self.assertIn("--thinking max", text)

    def test_no_cmd_exe_syntax(self) -> None:
        text = prepare.launch_command(LAUNCH_ENV, Path("F:/ws"))
        self.assertNotIn("cd /d", text)
        self.assertNotIn('set "', text)
        self.assertNotIn("$env:PI_SESSION_FILE", text)


class RunIdTests(unittest.TestCase):
    def test_accepts_single_path_components(self) -> None:
        for value in ("rom20-20260926T060500Z", "a", "A.b_c-9", "run.2026"):
            self.assertEqual(prepare.validate_run_id(value), value)

    def test_rejects_traversal_and_odd_names(self) -> None:
        for value in ("", ".", "..", "../x", "a/b", "a\\b", "-lead", ".lead", "trail-", "a b", "C:"):
            with self.subTest(value=value):
                with self.assertRaises(prepare.PrepError):
                    prepare.validate_run_id(value)

    def test_contained_child_stays_inside(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "labs"
            root.mkdir()
            child = prepare.contained_child(root, "run-1", "run root")
            self.assertIn(str(root.resolve()), str(child))

    def test_contained_child_refuses_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "labs"
            root.mkdir()
            with self.assertRaises(prepare.PrepError):
                prepare.contained_child(root, "../outside", "run root")


class RemoveTreeTests(unittest.TestCase):
    def test_removes_inside_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "labs"
            child = root / "run-1"
            (child / "sub").mkdir(parents=True)
            (child / "sub" / "f.txt").write_text("x", "utf-8")
            prepare.remove_tree_safely(child, root=root, what="run root")
            self.assertFalse(child.exists())

    def test_refuses_outside_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "labs"
            outside = Path(tmp) / "outside"
            root.mkdir()
            outside.mkdir()
            with self.assertRaises(prepare.PrepError):
                prepare.remove_tree_safely(outside, root=root, what="outside")

    def test_refuses_reparse_point(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "labs"
            child = root / "run-1"
            child.mkdir(parents=True)
            with mock.patch.object(prepare, "is_reparse_point", return_value=True):
                with self.assertRaises(prepare.PrepError):
                    prepare.remove_tree_safely(child, root=root, what="run root")


class ModSetTests(unittest.TestCase):
    def test_prune_keeps_only_allowed(self) -> None:
        self.assertEqual(
            prepare.mods_to_prune(["a.jar", "b.jar", "c.jar"], {"b.jar"}),
            ["a.jar", "c.jar"],
        )

    def test_allowed_names_include_pins_and_jars(self) -> None:
        env = {"interface_jar": {"path": "x/iface.jar"}, "audit_jar": {"path": "y/audit.jar"}}
        self.assertEqual(
            prepare.allowed_mod_names(env, MANIFEST),
            {"fabric-api-0.161.0+26.2.jar", "fabric-carpet-26.2+v260616.jar", "iface.jar", "audit.jar"},
        )


class BridgeKillPlanTests(unittest.TestCase):
    RECORD = {
        "pid": 100, "pid_created": 1000.0, "listen_pid": 200, "listen_created": 1000.5, "api_port": 27190,
    }

    @staticmethod
    def lookup(mapping):
        return lambda pid: mapping.get(pid)

    def test_kills_only_matching_identities(self) -> None:
        plan = prepare.bridge_kill_plan(
            self.RECORD, None,
            self.lookup({
                100: {"exe": "python.exe", "created": 1000.0},
                200: {"exe": "python.exe", "created": 1000.5},
            }),
        )
        self.assertEqual(plan["targets"], [100, 200])
        self.assertEqual(plan["skipped"], [])

    def test_start_time_mismatch_is_skipped(self) -> None:
        plan = prepare.bridge_kill_plan(
            self.RECORD, None,
            self.lookup({
                100: {"exe": "python.exe", "created": 9000.0},
                200: {"exe": "python.exe", "created": 1000.5},
            }),
        )
        self.assertEqual(plan["targets"], [200])
        self.assertTrue(any("mismatch" in entry["reason"] for entry in plan["skipped"]))

    def test_missing_recorded_time_is_skipped(self) -> None:
        record = dict(self.RECORD)
        del record["pid_created"]
        plan = prepare.bridge_kill_plan(
            record, None,
            self.lookup({
                100: {"exe": "python.exe", "created": 1000.0},
                200: {"exe": "python.exe", "created": 1000.5},
            }),
        )
        self.assertEqual(plan["targets"], [200])
        self.assertTrue(any("no recorded start time" in entry["reason"] for entry in plan["skipped"]))

    def test_non_python_image_is_skipped(self) -> None:
        plan = prepare.bridge_kill_plan(
            self.RECORD, None,
            self.lookup({
                100: {"exe": "not-python.exe", "created": 1000.0},
                200: {"exe": "python.exe", "created": 1000.5},
            }),
        )
        self.assertEqual(plan["targets"], [200])
        self.assertTrue(any("not a python image" in entry["reason"] for entry in plan["skipped"]))

    def test_unrecorded_socket_owner_fails_closed(self) -> None:
        with self.assertRaises(prepare.PrepError):
            prepare.bridge_kill_plan(self.RECORD, 999, self.lookup({}))

    def test_dead_pids_are_skipped(self) -> None:
        plan = prepare.bridge_kill_plan(self.RECORD, None, self.lookup({}))
        self.assertEqual(plan["targets"], [])
        self.assertEqual(len(plan["skipped"]), 2)


class BlankLabTests(unittest.TestCase):
    def test_blank_void_lab_passes(self) -> None:
        result = prepare.assert_blank_lab({"world": "void"}, {"carts": {"tracked": 0}}, "rom20-exp")
        self.assertEqual(result, {"world": "void", "tracked_carts": 0})

    def test_non_void_world_fails(self) -> None:
        with self.assertRaises(prepare.PrepError):
            prepare.assert_blank_lab({"world": "copied save"}, {"carts": {"tracked": 0}}, "rom20-exp")

    def test_tracked_carts_fail(self) -> None:
        with self.assertRaises(prepare.PrepError):
            prepare.assert_blank_lab({"world": "void"}, {"carts": {"tracked": 3}}, "rom20-exp")


class VersionTests(unittest.TestCase):
    def test_derives_from_manifest_and_jar_name(self) -> None:
        versions = prepare.derive_versions(MANIFEST, "mc-minecart-audit-0.1.0.jar")
        self.assertEqual(versions["minecraft"], "26.2")
        self.assertEqual(versions["fabric_loader"], "0.19.5")
        self.assertEqual(versions["fabric_installer"], "1.1.2")
        self.assertEqual(versions["fabric_api"], "0.161.0+26.2")
        self.assertEqual(versions["carpet"], "26.2+v260616")
        self.assertEqual(versions["interface_mod"], "0.6.0")
        self.assertEqual(versions["minecart_audit"], "0.1.0")

    def test_missing_version_fails_closed(self) -> None:
        manifest = {"game": dict(MANIFEST["game"]), "mods": [{"name": "fabric-api", "version": "1"}]}
        with self.assertRaises(prepare.PrepError):
            prepare.derive_versions(manifest, "mc-minecart-audit-0.1.0.jar")

    def test_unparseable_audit_jar_fails_closed(self) -> None:
        with self.assertRaises(prepare.PrepError):
            prepare.derive_versions(MANIFEST, "mc-minecart-audit.jar")


class LabStopGuardTests(unittest.TestCase):
    def _lab(self, tmp: str) -> Path:
        lab = Path(tmp)
        (lab / "lab.json").write_text(
            json.dumps({"serverPort": 27192, "rconPort": 27193}), "utf-8"
        )
        return lab

    def test_occupied_lab_port_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lab = self._lab(tmp)
            with mock.patch.object(
                prepare, "port_owner", side_effect=lambda port: 4242 if port == 27192 else None
            ):
                with self.assertRaises(prepare.PrepError):
                    prepare._assert_lab_ports_free(lab, "rom20-src")

    def test_free_lab_ports_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lab = self._lab(tmp)
            with mock.patch.object(prepare, "port_owner", return_value=None):
                prepare._assert_lab_ports_free(lab, "rom20-src")


class DependencyPinTests(unittest.TestCase):
    def _env(self, tmp: str) -> dict:
        bridge = Path(tmp) / "bridge"
        loop = Path(tmp) / "agent-loop"
        bridge.mkdir()
        loop.mkdir()
        return {
            "bridge": {"worktree": str(bridge), "commit": "aaa"},
            "agent_loop": {"root": str(loop), "commit": "bbb"},
        }

    def test_matching_clean_pins_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env(tmp)
            roots = {Path(env["bridge"]["worktree"]): "aaa", Path(env["agent_loop"]["root"]): "bbb"}
            with mock.patch.object(prepare, "git_required", side_effect=lambda root, *args: roots[root]), \
                 mock.patch.object(prepare, "git_status_lines", return_value=[]), \
                 mock.patch.object(prepare, "git", return_value="tree"):
                result = prepare.assert_dependencies(env)
            self.assertTrue(result["bridge"]["clean"])
            self.assertTrue(result["agent_loop"]["clean"])

    def test_head_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env(tmp)
            with mock.patch.object(prepare, "git_required", return_value="other"), \
                 mock.patch.object(prepare, "git_status_lines", return_value=[]):
                with self.assertRaises(prepare.PrepError):
                    prepare.assert_dependencies(env)

    def test_dirty_checkout_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = self._env(tmp)
            roots = {Path(env["bridge"]["worktree"]): "aaa", Path(env["agent_loop"]["root"]): "bbb"}
            with mock.patch.object(prepare, "git_required", side_effect=lambda root, *args: roots[root]), \
                 mock.patch.object(prepare, "git_status_lines", return_value=[" M bridge.py"]):
                with self.assertRaises(prepare.PrepError):
                    prepare.assert_dependencies(env)


class RequireInputTests(unittest.TestCase):
    def test_missing_file_is_a_named_prep_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(prepare.PrepError) as caught:
                prepare.require_file(Path(tmp) / "nope.json", "test input")
            self.assertIn("test input", str(caught.exception))
            self.assertIn("nope.json", str(caught.exception))

    def test_bad_json_is_a_prep_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            path.write_text("not json", "utf-8")
            with self.assertRaises(prepare.PrepError):
                prepare.require_json(path, "bad record")


class EnvironmentRecordTests(unittest.TestCase):
    def test_missing_record_is_a_prep_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(prepare.PrepError):
                prepare.load_env(Path(tmp) / "missing.json")

    def test_record_without_required_keys_is_a_prep_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "env.json"
            path.write_text(json.dumps({"schema": "mc-agent/rom20-environment/1"}), "utf-8")
            with self.assertRaises(prepare.PrepError) as caught:
                prepare.load_env(path)
            self.assertIn("missing required keys", str(caught.exception))


class StageSelectionTests(unittest.TestCase):
    def test_default_is_all_stages(self) -> None:
        self.assertEqual(prepare.ordered_stages(""), list(prepare.STAGES))

    def test_ordered_subset(self) -> None:
        self.assertEqual(prepare.ordered_stages("clean,context,source"), ["clean", "context", "source"])

    def test_rejects_unknown_duplicate_and_unordered(self) -> None:
        for value in ("bogus", "clean,clean", "context,clean"):
            with self.subTest(value=value):
                with self.assertRaises(prepare.PrepError):
                    prepare.ordered_stages(value)


class FailureRecordTests(unittest.TestCase):
    def test_write_failure_creates_bounded_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            operator = Path(tmp)
            prepare.write_failure(operator, "rom20-test", "fixture", "boom", ["workspace", "clean"])
            payload = json.loads((operator / "failure.json").read_text("utf-8"))
            self.assertEqual(payload["run_id"], "rom20-test")
            self.assertEqual(payload["stage"], "fixture")
            self.assertEqual(payload["error"], "boom")
            self.assertEqual(payload["receipts_written"], ["clean", "workspace"])
            self.assertEqual(payload["schema"], "mc-agent/rom20-prep-failure/1")


class GitStateTests(unittest.TestCase):
    def test_unreadable_git_is_unknown_not_clean(self) -> None:
        with mock.patch.object(prepare, "git_status_lines", return_value=None):
            entry = prepare.git_state_entry(Path("does-not-matter"))
            self.assertIsNone(entry["clean"])
            self.assertIsNone(entry["dirty_entries"])

    def test_status_decides_clean(self) -> None:
        with mock.patch.object(prepare, "git_status_lines", return_value=[]):
            self.assertTrue(prepare.git_state_entry(Path("x"))["clean"])
        with mock.patch.object(prepare, "git_status_lines", return_value=[" M file"]):
            self.assertFalse(prepare.git_state_entry(Path("x"))["clean"])


class ReceiptTests(unittest.TestCase):
    def test_receipt_records_run_id_and_refuses_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            receipts = prepare.Receipts(Path(tmp) / "operator", "rom20-test")
            receipts.write("workspace", {"workspace": "x"})
            payload = json.loads((Path(tmp) / "operator" / "receipts" / "workspace.json").read_text("utf-8"))
            self.assertEqual(payload["run_id"], "rom20-test")
            with self.assertRaises(prepare.PrepError):
                receipts.write("workspace", {"workspace": "y"})


class FixtureHelperTests(unittest.TestCase):
    def test_uuid_from_ints(self) -> None:
        self.assertEqual(
            prepare.uuid_from_ints("[I; 1052844757, -64534506, -1102594165, -388504211]"),
            "3ec122d5-fc27-4816-be47-bf8be8d7e56d",
        )

    def test_duplicate_item_detection(self) -> None:
        duplicate = {"carts": [{"items": [{"id": "minecraft:stone"}, {"id": "minecraft:stone"}]}]}
        distinct = {"carts": [{"items": [{"id": "minecraft:stone"}]}]}
        self.assertTrue(prepare.challenge_has_duplicate_items(duplicate))
        self.assertFalse(prepare.challenge_has_duplicate_items(distinct))


if __name__ == "__main__":
    unittest.main()
