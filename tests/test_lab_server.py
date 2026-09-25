"""Focused tests for the lab deployment logic in tools/lab_server.py.

These do not start a game server: they cover the pieces the live lab test
depends on - content-hash replacement, required-mod and drift checks, port
identity, and the per-lab identity payload.

Run from the repository root:

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


lab_server = load_module("lab_server", "tools/lab_server.py")


class DeployFileTests(unittest.TestCase):
    def test_same_size_different_content_is_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "new.jar"
            target = Path(tmp) / "mod.jar"
            source.write_bytes(b"A" * 32)
            target.write_bytes(b"B" * 32)  # same size, different bytes

            result = lab_server.deploy_file(source, target, "mod under test")

            self.assertEqual(result["action"], "replaced")
            self.assertEqual(target.read_bytes(), b"A" * 32)
            self.assertEqual(lab_server.sha256_file(target), result["sha256"])

    def test_identical_content_is_left_alone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "new.jar"
            target = Path(tmp) / "mod.jar"
            source.write_bytes(b"same bytes")
            target.write_bytes(b"same bytes")

            result = lab_server.deploy_file(source, target, "mod under test")

            self.assertEqual(result["action"], "unchanged")

    def test_deployed_bytes_are_verified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "new.jar"
            target = Path(tmp) / "sub" / "mod.jar"
            source.write_bytes(b"payload")
            result = lab_server.deploy_file(source, target, "mod under test")
            self.assertEqual(result["action"], "installed")
            self.assertEqual(lab_server.sha256_file(target), lab_server.sha256_file(source))
            self.assertFalse((target.parent / "mod.jar.deploying").exists())

    def test_copy_if_different_is_hash_based(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "a.jar"
            target = Path(tmp) / "b.jar"
            source.write_bytes(b"1" * 64)
            target.write_bytes(b"2" * 64)
            lab_server.copy_if_different(source, target)
            self.assertEqual(target.read_bytes(), b"1" * 64)


class DeploymentCheckTests(unittest.TestCase):
    def make_lab(self, tmp: Path, state: dict) -> Path:
        lab = tmp / "lab-a"
        (lab / "mods").mkdir(parents=True)
        (lab / "lab.json").write_text(json.dumps(state), "utf-8")
        return lab

    def test_missing_required_mod_is_a_problem(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lab = self.make_lab(Path(tmp), {"mods": [], "requiredMods": ["test-mod.jar"]})

            actual, problems = lab_server.check_deployment(lab, json.loads((lab / "lab.json").read_text()))

            self.assertEqual(actual, [])
            self.assertTrue(any("required mod test-mod.jar" in problem for problem in problems))

    def test_same_size_content_drift_is_a_problem(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lab = self.make_lab(Path(tmp), {})
            jar = lab / "mods" / "logger.jar"
            jar.write_bytes(b"A" * 32)
            state = {
                "mods": [{"name": "logger.jar", "sha256": lab_server.sha256_file(jar), "size": 32}],
                "requiredMods": [],
            }
            (lab / "lab.json").write_text(json.dumps(state), "utf-8")
            jar.write_bytes(b"B" * 32)  # rebuilt, same size, new bytes

            actual, problems = lab_server.check_deployment(lab, state)

            self.assertTrue(actual[0]["drift"])
            self.assertTrue(any("does not match the provisioned" in problem for problem in problems))

    def test_allow_drift_suppresses_only_the_hash_problem(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lab = self.make_lab(Path(tmp), {})
            jar = lab / "mods" / "logger.jar"
            jar.write_bytes(b"A" * 32)
            recorded = lab_server.sha256_file(jar)
            jar.write_bytes(b"B" * 32)
            state = {
                "mods": [{"name": "logger.jar", "sha256": recorded, "size": 32}],
                "requiredMods": ["absent-test-mod.jar"],
            }

            actual, problems = lab_server.check_deployment(lab, state, allow_drift=True)

            self.assertTrue(actual[0]["drift"])
            self.assertEqual(len(problems), 1)
            self.assertIn("required mod absent-test-mod.jar", problems[0])

    def test_legacy_name_only_mods_still_load(self) -> None:
        records = lab_server.mod_records({"mods": ["fabric-api.jar", "carpet.jar"]})
        self.assertEqual([record["name"] for record in records], ["fabric-api.jar", "carpet.jar"])
        self.assertEqual(lab_server.mod_names({"mods": ["fabric-api.jar"]}), ["fabric-api.jar"])


class PortIdentityTests(unittest.TestCase):
    def test_explicit_port_of_another_lab_is_rejected(self) -> None:
        with self.assertRaises(SystemExit):
            lab_server.choose_port(27172, {27172}, "server-vantage")

    def test_open_port_is_allowed_when_it_is_this_labs_identity(self) -> None:
        # A lab's bridge may be listening while its Minecraft server is stopped.
        self.assertEqual(
            lab_server.choose_port(27173, {27176}, "bridge API", allow_open=True),
            27173,
        )

    def test_auto_port_avoids_recorded_ports(self) -> None:
        used = {1, 2, 3}
        chosen = lab_server.choose_port(0, used, "game")
        self.assertNotIn(chosen, {1, 2, 3})
        self.assertIn(chosen, used)


class StartGuardTests(unittest.TestCase):
    def test_start_refuses_missing_required_mod_before_spawning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            labs = Path(tmp)
            lab = labs / "lab-a"
            (lab / "mods").mkdir(parents=True)
            (lab / "logs").mkdir()
            (lab / "launcher.jar").write_bytes(b"jar")
            (lab / "server.properties").write_text("server-port=1\n", "utf-8")
            (lab / "rcon.json").write_text(json.dumps({"host": "127.0.0.1", "port": 1, "password": "x"}), "utf-8")
            (lab / "eula.txt").write_text("eula=true\n", "utf-8")
            (lab / "lab.json").write_text(
                json.dumps(
                    {
                        "name": "lab-a",
                        "launcher": "launcher.jar",
                        "mods": [{"name": "test-mod.jar", "sha256": "0" * 64, "required": True}],
                        "requiredMods": ["test-mod.jar"],
                    }
                ),
                "utf-8",
            )
            args = argparse.Namespace(
                name="lab-a", java="", memory="", jvm_arg=[], allow_mod_drift=False, wait=0.0
            )

            with mock.patch.object(lab_server, "LABS", labs), \
                    mock.patch.object(lab_server, "spawn_detached") as spawn:
                with self.assertRaises(SystemExit):
                    lab_server.cmd_start(args)
                spawn.assert_not_called()


class VerifyGuardTests(unittest.TestCase):
    def test_verify_requires_a_running_vantage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            labs = Path(tmp)
            lab = labs / "lab-a"
            (lab / "mods").mkdir(parents=True)
            (lab / "world").mkdir()
            (lab / "world" / "level.dat").write_bytes(b"level")
            (lab / "mc-agent-server").mkdir()
            (lab / "mc-agent-server" / "port.txt").write_text("27172", "utf-8")
            (lab / "lab.json").write_text(
                json.dumps(
                    {
                        "name": "lab-a",
                        "mods": [],
                        "worldDir": str(lab / "world"),
                        "serverDir": str(lab / "mc-agent-server"),
                        "portFile": str(lab / "mc-agent-server" / "port.txt"),
                        "serverVantagePort": 27172,
                    }
                ),
                "utf-8",
            )
            args = argparse.Namespace(name="lab-a", require_vantage=True, json=False)

            with mock.patch.object(lab_server, "LABS", labs):
                exit_code = lab_server.cmd_verify(args)

            self.assertEqual(exit_code, 1)


class IdentityTests(unittest.TestCase):
    def test_two_labs_share_no_identity_field(self) -> None:
        fields = (
            "instanceId",
            "worldDir",
            "auditDir",
            "serverDir",
            "serverVantagePort",
            "bridgeApiPort",
            "serverPort",
            "rconPort",
        )
        with tempfile.TemporaryDirectory() as tmp:
            labs = Path(tmp)
            payloads = []
            for index, name in enumerate(("lab-a", "lab-b"), start=1):
                lab = labs / name
                (lab / "mods").mkdir(parents=True)
                state = {
                    "name": name,
                    "instanceId": f"instance-{index}",
                    "worldDir": f"/worlds/{name}",
                    "auditDir": f"/audit/{name}",
                    "serverDir": f"/server/{name}",
                    "serverVantagePort": 27170 + index,
                    "bridgeApiPort": 27180 + index,
                    "serverPort": 27190 + index,
                    "rconPort": 27195 + index,
                    "mods": [],
                }
                (lab / "lab.json").write_text(json.dumps(state), "utf-8")
                payloads.append(lab_server.identity_payload(lab, state))

            for field in fields:
                self.assertNotEqual(payloads[0][field], payloads[1][field], field)


if __name__ == "__main__":
    unittest.main()
