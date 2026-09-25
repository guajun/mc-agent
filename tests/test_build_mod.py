"""Focused tests for tools/build_mod.py.

These cover packaging without invoking javac: resource substitution, the
deterministic jar writer, and the source-tree guards. The live compile is part
of the lab smoke verification, not this suite.

Run from the repository root:

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


build_mod = load_module("build_mod", "tools/build_mod.py")


class SubstituteTests(unittest.TestCase):
    def test_version_substituted_in_text_resources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fabric.mod.json"
            path.write_text('{"version": "${version}"}', "utf-8")
            self.assertEqual(
                build_mod.substituted_bytes(path, "1.2.3"),
                b'{"version": "1.2.3"}',
            )

    def test_binary_resources_are_not_touched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "icon.png"
            data = b"\x89PNG${version}"
            path.write_bytes(data)
            self.assertEqual(build_mod.substituted_bytes(path, "1.2.3"), data)


class PackJarTests(unittest.TestCase):
    def pack(self, work: Path, class_bytes: bytes, version: str, compression: str = "store") -> bytes:
        classes = work / "classes"
        classes.mkdir(parents=True, exist_ok=True)
        (classes / "Smoke.class").write_bytes(class_bytes)
        resources = work / "src" / "main" / "resources"
        resources.mkdir(parents=True, exist_ok=True)
        (resources / "fabric.mod.json").write_text('{"version": "${version}"}', "utf-8")
        out = work / "out.jar"
        build_mod.pack_jar(
            out,
            classes,
            work,
            [resources / "fabric.mod.json"],
            version,
            compression,
        )
        return out.read_bytes()

    def test_same_length_version_edits_keep_size_but_change_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            first = self.pack(work, b"A" * 128, "AAAA")
            second = self.pack(work, b"B" * 128, "BBBB")
            self.assertEqual(len(first), len(second))
            self.assertNotEqual(first, second)

    def test_identical_inputs_produce_identical_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "one"
            work.mkdir()
            first = self.pack(work, b"same", "0.1.0")
            second = self.pack(work, b"same", "0.1.0")
            self.assertEqual(first, second)

    def test_jar_contains_classes_and_version_substituted_resource(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            data = self.pack(work, b"bytecode", "9.9.9")
            jar = work / "out.jar"
            with zipfile.ZipFile(jar) as archive:
                names = sorted(archive.namelist())
                self.assertIn("Smoke.class", names)
                self.assertIn("fabric.mod.json", names)
                self.assertIn(b"9.9.9", archive.read("fabric.mod.json"))


class SourceGuardTests(unittest.TestCase):
    def test_missing_java_tree_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                build_mod.source_files(Path(tmp))

    def test_missing_resources_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "src" / "main" / "java").mkdir(parents=True)
            with self.assertRaises(SystemExit):
                build_mod.resource_files(Path(tmp))


if __name__ == "__main__":
    unittest.main()
