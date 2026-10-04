"""Client launch directory regressions, using synthetic files and no game process."""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import launch_instance as launch


class LaunchDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.minecraft = self.root / "launcher files"
        self.version = self.minecraft / "versions" / "test"
        self.version.mkdir(parents=True)
        (self.version / "test.jar").touch()
        (self.version / "natives-windows-x86_64").mkdir()
        self.metadata = {
            "mainClass": "test.Main",
            "arguments": {
                "jvm": ["-cp", "${classpath}"],
                "game": ["--gameDir", "${game_directory}", "--accessToken", "${auth_access_token}"],
            },
        }
        self.write_metadata()
        self.account = self.root / "synthetic-account.json"
        self.account.write_text(json.dumps({"accessToken": "synthetic-token"}), encoding="utf-8")

    def write_metadata(self):
        (self.version / "test.json").write_text(json.dumps(self.metadata), encoding="utf-8")

    def args(self, *extra):
        argv = [
            "launch_instance.py", "--minecraft-dir", str(self.minecraft), "--version", "test",
            "--java", "fake-java", "--account-file", str(self.account), *extra,
        ]
        with patch.object(sys, "argv", argv):
            return launch.parse_args()

    def test_default_launch_keeps_version_directory_and_no_server_override(self):
        command, cwd, _ = launch.build_command(self.args())
        self.assertEqual(cwd, self.version)
        self.assertEqual(command[command.index("--gameDir") + 1], str(self.version))
        self.assertFalse(any(value.startswith("-Dmcagent.serverDir=") for value in command))
        self.assertEqual(launch.server_directory(command, cwd), self.version / "mc-agent-server")

    def test_independent_directory_is_absolute_under_different_calling_cwds(self):
        game = self.root / "research instance"
        other_cwd = self.root / "other"
        other_cwd.mkdir()
        args = self.args("--game-dir", str(game))
        before = Path.cwd()
        try:
            commands = []
            for cwd in (self.root, other_cwd):
                os.chdir(cwd)
                command, process_cwd, _ = launch.build_command(args)
                commands.append(command)
                self.assertEqual(process_cwd, self.version)
                self.assertEqual(launch.server_directory(command, process_cwd), game / "mc-agent-server")
                self.assertEqual(command[command.index("--gameDir") + 1], str(game))
            self.assertEqual(commands[0], commands[1])
        finally:
            os.chdir(before)
        self.assertFalse(game.exists())
        self.assertFalse((self.version / "mc-agent-server").exists())

    def test_legacy_game_argument_overrides_are_normalized(self):
        for raw in (["--game-arg=--gameDir", "--game-arg=relative-game"],
                    ["--game-arg=--gameDir=relative-game"]):
            with self.subTest(raw=raw):
                command, cwd, _ = launch.build_command(self.args(*raw, "--game-arg=--demo"))
                expected = Path("relative-game").resolve()
                self.assertEqual(command.count("--gameDir"), 1)
                self.assertEqual(command[command.index("--gameDir") + 1], str(expected))
                self.assertEqual(launch.server_directory(command, cwd), expected / "mc-agent-server")
                self.assertIn("--demo", command)

    def test_explicit_server_directory_from_all_jvm_sources_wins(self):
        for source in ("property", "argument", "metadata"):
            with self.subTest(source=source):
                self.metadata["arguments"]["jvm"] = ["-cp", "${classpath}"]
                extra = ["--game-dir", str(self.root / "research")]
                if source == "property":
                    extra += ["--jvm-property", "mcagent.serverDir=chosen-server"]
                elif source == "argument":
                    extra += ["--jvm-arg=-Dmcagent.serverDir=chosen-server"]
                else:
                    self.metadata["arguments"]["jvm"].append("-Dmcagent.serverDir=chosen-server")
                self.write_metadata()
                command, cwd, _ = launch.build_command(self.args(*extra))
                overrides = [value for value in command if value.startswith("-Dmcagent.serverDir=")]
                self.assertEqual(overrides, ["-Dmcagent.serverDir=chosen-server"])
                self.assertEqual(launch.server_directory(command, cwd), self.version / "chosen-server")

    def test_conflicting_and_missing_game_directories_are_rejected(self):
        for extra in (
            ["--game-dir", "first", "--game-arg=--gameDir=second"],
            ["--game-arg=--gameDir"],
            ["--game-arg=--gameDir="],
        ):
            with self.subTest(extra=extra), self.assertRaises(SystemExit):
                launch.build_command(self.args(*extra))

    def test_launch_logs_under_research_directory_and_preserves_existing_files(self):
        game = self.root / "research"
        existing = self.version / "mc-agent-server" / "control" / "sentinel"
        existing.parent.mkdir(parents=True)
        existing.write_text("preserve", encoding="utf-8")
        args = self.args("--game-dir", str(game))
        with patch.object(launch, "parse_args", return_value=args), patch.object(launch.subprocess, "Popen") as popen:
            popen.return_value.pid = 123
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(launch.main(), 0)
            # Popen is mocked, so close the output handle the actual process would inherit.
            popen.call_args.kwargs["stdout"].close()
        self.assertEqual(popen.call_args.kwargs["cwd"], str(self.version))
        self.assertTrue((game / "mc-agent" / "game-launch.log").exists())
        self.assertFalse((self.version / "mc-agent").exists())
        self.assertEqual(existing.read_text(encoding="utf-8"), "preserve")
        self.assertIn(str(game / "mc-agent-server" / "control"), output.getvalue())
        self.assertNotIn("synthetic-token", output.getvalue())


if __name__ == "__main__":
    unittest.main()
