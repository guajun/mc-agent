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

    def test_unrecorded_mod_is_a_problem(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lab = self.make_lab(Path(tmp), {"mods": [], "requiredMods": []})
            (lab / "mods" / "dropped-in.jar").write_bytes(b"jar dropped in later")
            state = {"mods": [], "requiredMods": []}

            actual, problems = lab_server.check_deployment(lab, state)

            self.assertFalse(actual[0]["recorded"])
            self.assertTrue(any("never provisioned" in problem for problem in problems))

    def test_allow_unrecorded_suppresses_that_problem(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lab = self.make_lab(Path(tmp), {})
            (lab / "mods" / "dropped-in.jar").write_bytes(b"jar dropped in later")
            state = {"mods": [], "requiredMods": []}

            _actual, problems = lab_server.check_deployment(lab, state, allow_unrecorded=True)

            self.assertEqual(problems, [])


class RequiredModTests(unittest.TestCase):
    def test_requirement_survives_reprovision_when_jar_is_absent(self) -> None:
        previous = {
            "requiredMods": ["test-mod.jar"],
            "mods": [{"name": "test-mod.jar", "sha256": "abc", "required": True}],
        }

        required = lab_server.merge_required_mods(previous, lab_server.mod_records(previous))

        self.assertIn("test-mod.jar", required)

    def test_requirement_survives_even_when_the_record_is_gone(self) -> None:
        previous = {"requiredMods": ["test-mod.jar"], "mods": []}

        required = lab_server.merge_required_mods(previous, [])

        self.assertIn("test-mod.jar", required)

    def test_forget_mod_is_the_explicit_removal(self) -> None:
        previous = {"requiredMods": ["test-mod.jar"], "mods": []}

        required = lab_server.merge_required_mods(previous, [], forget=["test-mod.jar"])

        self.assertNotIn("test-mod.jar", required)

    def test_reprovision_without_flags_keeps_an_absent_requirement(self) -> None:
        """The exact reviewer sequence: provision --test-mod, delete the jar,
        re-provision with no mod flags - the requirement must survive."""
        with tempfile.TemporaryDirectory() as tmp:
            labs = Path(tmp)
            lab = labs / "lab-a"
            (lab / "mods").mkdir(parents=True)
            (lab / "lab.json").write_text(
                json.dumps(
                    {
                        "name": "lab-a",
                        "minecraft": "26.2",
                        "loader": "0.19.5",
                        "installer": "1.1.2",
                        "launcher": "fake-launcher.jar",
                        "mods": [{"name": "test-mod.jar", "sha256": "0" * 64, "size": 3, "required": True}],
                        "requiredMods": ["test-mod.jar"],
                    }
                ),
                "utf-8",
            )
            fake_launcher = labs / "cached-launcher.jar"
            fake_launcher.write_bytes(b"launcher")
            args = argparse.Namespace(
                name="lab-a",
                mc="",
                fabric_api=False,
                carpet=False,
                mod_jar=[],
                mod_url=[],
                test_mod=[],
                require_mod=[],
                forget_mod=[],
                world="",
                void=False,
                java="",
                jdk="",
                memory="",
                loader="",
                jvm_arg=[],
                server_port=0,
                rcon_port=0,
                vantage_port=0,
                bridge_port=0,
                server_dir="",
                audit_dir="",
                force=False,
            )

            with mock.patch.object(lab_server, "LABS", labs), \
                    mock.patch.object(lab_server, "download", return_value=fake_launcher), \
                    mock.patch.object(lab_server, "check_launcher_metadata"), \
                    mock.patch.object(lab_server, "resolve_java", return_value=""), \
                    mock.patch.object(lab_server, "resolve_jdk", return_value=("", "", "")):
                exit_code = lab_server.cmd_provision(args)

            self.assertEqual(exit_code, 0)
            self.assertEqual(
                json.loads((lab / "lab.json").read_text("utf-8"))["requiredMods"],
                ["test-mod.jar"],
            )


class JvmArgTests(unittest.TestCase):
    def test_merge_jvm_args_is_idempotent_and_replaces_properties(self) -> None:
        merged = lab_server.merge_jvm_args(
            ["-Dfoo=1", "--add-opens=java.base/java.lang=ALL-UNNAMED"],
            ["-Dfoo=1", "-Dfoo=2", "--add-opens=java.base/java.lang=ALL-UNNAMED"],
        )

        self.assertEqual(merged, ["-Dfoo=2", "--add-opens=java.base/java.lang=ALL-UNNAMED"])

    def test_merge_jvm_args_keeps_distinct_arguments(self) -> None:
        merged = lab_server.merge_jvm_args(["-Xlog:gc"], ["-Dbar=3"])
        self.assertEqual(merged, ["-Xlog:gc", "-Dbar=3"])

    def _parse(self, raw: list[str]) -> argparse.Namespace:
        parser = lab_server.build_parser()
        return parser.parse_args(lab_server.normalise_jvm_args(raw, lab_server.parser_option_strings(parser)))

    def test_space_separated_value_that_starts_with_a_dash(self) -> None:
        args = self._parse(["provision", "--name", "lab-a", "--jvm-arg", "-Dfoo=1", "--void"])
        self.assertEqual(args.jvm_arg, ["-Dfoo=1"])

    def test_double_dash_jvm_arg_value(self) -> None:
        args = self._parse(
            ["start", "--name", "lab-a", "--jvm-arg", "--add-opens=java.base/java.lang=ALL-UNNAMED"]
        )
        self.assertEqual(args.jvm_arg, ["--add-opens=java.base/java.lang=ALL-UNNAMED"])

    def test_missing_value_still_fails(self) -> None:
        with self.assertRaises(SystemExit):
            self._parse(["provision", "--name", "lab-a", "--jvm-arg", "--void"])


class DeployedAtTests(unittest.TestCase):
    def test_timestamp_refreshes_only_when_bytes_changed(self) -> None:
        prior = {"deployedAt": 100.0}

        self.assertEqual(lab_server.deployed_at(prior, "unchanged", 200.0), 100.0)
        self.assertEqual(lab_server.deployed_at(prior, "replaced", 200.0), 200.0)
        self.assertEqual(lab_server.deployed_at({}, "installed", 200.0), 200.0)


class DownloadRefreshTests(unittest.TestCase):
    def test_refresh_observes_changed_bytes_at_an_unchanged_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            served = Path(tmp) / "served.jar"
            dest = Path(tmp) / "cache" / "mod.jar"
            served.write_bytes(b"first bytes")
            url = served.as_uri()

            lab_server.download(url, dest, "test mod")
            self.assertEqual(dest.read_bytes(), b"first bytes")

            served.write_bytes(b"second bytes")
            lab_server.download(url, dest, "test mod")  # cached path keeps the old bytes
            self.assertEqual(dest.read_bytes(), b"first bytes")

            lab_server.download(url, dest, "test mod", refresh=True)
            self.assertEqual(dest.read_bytes(), b"second bytes")


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

    def test_provision_port_allows_this_labs_own_recorded_port(self) -> None:
        # Explicit and carried-forward values equal to the lab's record are its
        # identity: the lab's bridge may still be listening on that port.
        self.assertEqual(lab_server.provision_port(27173, 27173, {27176}, "bridge API"), 27173)
        self.assertEqual(lab_server.provision_port(0, 27173, {27176}, "bridge API"), 27173)

    def test_provision_port_still_rejects_a_different_open_port(self) -> None:
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port = int(listener.getsockname()[1])
            with self.assertRaises(SystemExit):
                lab_server.provision_port(port, 27173, set(), "bridge API")


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
                name="lab-a",
                java="",
                memory="",
                jvm_arg=[],
                allow_mod_drift=False,
                allow_unrecorded_mod=False,
                wait=0.0,
            )

            with mock.patch.object(lab_server, "LABS", labs), \
                    mock.patch.object(lab_server, "spawn_detached") as spawn:
                with self.assertRaises(SystemExit):
                    lab_server.cmd_start(args)
                spawn.assert_not_called()

    def test_start_refuses_unrecorded_mod_but_can_be_overridden(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            labs = Path(tmp)
            lab = labs / "lab-a"
            (lab / "mods").mkdir(parents=True)
            (lab / "launcher.jar").write_bytes(b"jar")
            (lab / "mods" / "dropped-in.jar").write_bytes(b"jar")
            (lab / "server.properties").write_text("server-port=1\n", "utf-8")
            (lab / "rcon.json").write_text(json.dumps({"host": "127.0.0.1", "port": 1, "password": "x"}), "utf-8")
            (lab / "eula.txt").write_text("eula=true\n", "utf-8")
            (lab / "lab.json").write_text(
                json.dumps({"name": "lab-a", "launcher": "launcher.jar", "mods": [], "requiredMods": []}),
                "utf-8",
            )
            args = argparse.Namespace(
                name="lab-a",
                java="",
                memory="",
                jvm_arg=[],
                allow_mod_drift=False,
                allow_unrecorded_mod=False,
                wait=0.0,
            )

            with mock.patch.object(lab_server, "LABS", labs), \
                    mock.patch.object(lab_server, "spawn_detached") as spawn, \
                    mock.patch.object(lab_server, "resolve_java", return_value="java"):
                spawn.return_value = 4242
                with self.assertRaises(SystemExit):
                    lab_server.cmd_start(args)
                spawn.assert_not_called()
                args.allow_unrecorded_mod = True
                self.assertEqual(lab_server.cmd_start(args), 0)
                spawn.assert_called_once()


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

    def test_verify_rejects_a_mod_that_was_never_provisioned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            labs = Path(tmp)
            lab = labs / "lab-a"
            (lab / "mods").mkdir(parents=True)
            (lab / "mods" / "dropped-in.jar").write_bytes(b"jar")
            (lab / "world").mkdir()
            (lab / "world" / "level.dat").write_bytes(b"level")
            (lab / "lab.json").write_text(
                json.dumps(
                    {
                        "name": "lab-a",
                        "mods": [],
                        "requiredMods": [],
                        "worldDir": str(lab / "world"),
                    }
                ),
                "utf-8",
            )
            args = argparse.Namespace(name="lab-a", require_vantage=False, json=False)

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
