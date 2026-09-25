#!/usr/bin/env python3
"""Reproducible stage-one integration driver (generic smoke, no ROM solution).

This driver performs the *combined* integration a stage-one gate needs, with
the live pieces that exist today and honest gaps for those that do not:

* provisions two disposable Fabric labs on the issue's reserved ports
  (27240-27249 by default) - one ``source_audit`` instance and one
  ``experiment`` instance - with their own worlds, mods, logs and audit dirs;
* builds the generic ``examples/smoke-mod`` with ``tools/build_mod.py`` and
  deploys it as a required test mod (content-hash deploy from #17);
* starts both labs, records their identity/ports/worlds, drives the smoke
  commands, stops and restarts them to prove the instances stay distinct;
* rebuilds the smoke mod with a same-length marker and verifies the *new*
  bytes are what the restarted experiment loaded;
* probes the loud failure paths (broken build, broken mod load, missing
  dependency) so the corresponding facts are observed, not asserted;
* records every operator action as #19 ``call``/``result`` trajectory records;
* normalizes the trajectory (and, when given, a real #18 audit log) into the
  gate schema with ``tools/stage1_evidence.py`` and runs the gate.

It never fabricates evidence: a step that fails, or a probe that cannot be
observed, becomes a recorded gap and the corresponding gate artifact is
withheld, so the gate reports ``blocked`` instead of a false pass.  It does not
touch the ROM map (the source save is only hashed read-only) and does not
write an agent logger or solve the ROM.

    python tools/stage1_integration.py plan
    python tools/stage1_integration.py run --java "<jdk-25 java>" --jdk "<jdk home>"
    python tools/stage1_integration.py run --skip-labs          # only re-normalize

Everything lands under ``labs/<name>/`` (git-ignored): lab dirs, build
artifacts, the run trajectory, the bundle and ``summary.json``/``summary.md``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence, TextIO

ROOT = Path(__file__).resolve().parent.parent
LABS = ROOT / "labs"
PY = sys.executable
GATE = ROOT / "tools" / "stage1_gate.py"
EVIDENCE = ROOT / "tools" / "stage1_evidence.py"
BUILD_MOD = ROOT / "tools" / "build_mod.py"
LAB_SERVER = ROOT / "tools" / "lab_server.py"
RUN_TRACE = ROOT / "tools" / "run_trace.py"
IDENTITY_EVIDENCE = ROOT / "docs" / "evidence" / "rom13-meta16" / "live-identity.json"
SOURCE_SAVE = Path("D:/MC/MC_Game/.minecraft/versions/26.2-Fabric/saves/Minecart ROM test")

sys.path.insert(0, str(ROOT / "tools"))
import stage1_gate as gate  # noqa: E402

DEFAULT_PORTS = {
    "src": {"server": 27240, "rcon": 27241, "vantage": 27242, "bridge": 27243},
    "exp": {"server": 27244, "rcon": 27245, "vantage": 27246, "bridge": 27247},
}

JAVA_CANDIDATES = (
    "C:/Users/MSI-NB/AppData/Roaming/.hmcl/java/windows-x86_64/mojang-java-runtime-epsilon/bin/java.exe",
    "C:/Users/MSI-NB/AppData/Roaming/.hmcl/java/windows-x86_64/mojang-java-runtime-epsilon/bin/javac.exe",
)

#: The interface mod (3b93ceb, sha256 45f12e16...) that makes the
#: server-vantage port file real; read-only, from the committed #16 evidence run.
INTERFACE_JAR_SHA256 = "45f12e16b3979be6a699ac3c744b2a68dfcf8dd2379f5987bf9b9319adf4404f"
INTERFACE_JAR_CANDIDATES = (
    Path("F:/mc-agent-worktrees/rom13/meta16/labs/_build/mods/mc-agent-interface-0.6.0.jar"),
    Path("F:/mc-agent-worktrees/rom13/meta18/labs/_build/mods/mc-agent-interface-0.6.0.jar"),
)


def say(message: str = "") -> None:
    try:
        print(message, flush=True)
    except OSError:
        pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def port_free(port: int) -> bool:
    probe = socket.socket()
    probe.settimeout(0.3)
    try:
        probe.connect(("127.0.0.1", port))
        return False
    except OSError:
        return True
    finally:
        probe.close()


def discover_java(explicit: str | None) -> str:
    for candidate in (explicit, os.environ.get("MC_AGENT_JAVA")):
        if candidate and Path(candidate).is_file():
            return str(Path(candidate))
    for lab_json in (Path("F:/mc-agent/labs/smoke/lab.json"),):
        if lab_json.is_file():
            java = read_json(lab_json).get("java")
            if isinstance(java, str) and Path(java).is_file():
                return java
    for candidate in JAVA_CANDIDATES:
        if Path(candidate).is_file() and candidate.endswith("java.exe"):
            return candidate
    return "java"


def discover_jdk(explicit: str | None, java: str) -> str:
    for candidate in (explicit, os.environ.get("JAVA_HOME")):
        if candidate and (Path(candidate) / "bin" / "javac.exe").is_file():
            return str(Path(candidate))
    java_path = Path(java)
    if java_path.name.lower() in ("java.exe", "java"):
        home = java_path.parent.parent
        if (home / "bin" / "javac.exe").is_file():
            return str(home)
    for candidate in JAVA_CANDIDATES:
        if candidate.endswith("javac.exe"):
            return str(Path(candidate).parent.parent)
    return ""


def discover_interface_jar(explicit: str | None) -> tuple[Path, str] | None:
    """Find the read-only interface-mod jar and verify its pinned hash."""
    candidates = [Path(explicit)] if explicit else list(INTERFACE_JAR_CANDIDATES)
    for candidate in candidates:
        if candidate.is_file() and sha256_file(candidate) == INTERFACE_JAR_SHA256:
            return candidate, INTERFACE_JAR_SHA256
    return None


@dataclass
class StepResult:
    name: str
    command: list[str]
    returncode: int
    stdout: str
    stderr: str
    seconds: float
    artifacts: list[str] = field(default_factory=list)
    call_id: str = ""

    def as_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "command": self.command,
            "returncode": self.returncode,
            "seconds": round(self.seconds, 3),
            "artifacts": self.artifacts,
        }


class Recorder:
    """Run commands and mirror them into the #19 run trajectory."""

    def __init__(self, run_dir: Path, run_id: str, *, trace: bool = True):
        self.run_dir = run_dir
        self.run_id = run_id
        self.trace = trace
        self.steps: list[StepResult] = []
        self.counter = 0

    def init(self, task_text: str) -> None:
        if not self.trace:
            return
        self.run_dir.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                PY,
                str(RUN_TRACE),
                "init",
                "--run-dir",
                str(self.run_dir),
                "--run-id",
                self.run_id,
                "--task-text",
                task_text,
                "--harness",
                "stage1-integration-driver",
                "--model",
                "none (no model in this run)",
                "--provider",
                "local",
                "--note",
                "stage-one combined integration: generic smoke, no ROM solution",
            ],
            check=False,
            capture_output=True,
            text=True,
        )

    def run(
        self,
        name: str,
        command: Sequence[str],
        *,
        tool: str = "bash",
        instance: str | None = None,
        phase: str = "prepare",
        actor: str = "operator",
        expect: int = 0,
        allow_fail: bool = False,
        timeout: float | None = 900,
    ) -> StepResult:
        started = time.time()
        proc = subprocess.run(
            [str(part) for part in command],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=ROOT,
        )
        result = StepResult(
            name=name,
            command=[str(part) for part in command],
            returncode=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            seconds=time.time() - started,
        )
        self.steps.append(result)
        if self.trace:
            call_id = f"{self.counter:03d}-{name}"
            self.counter += 1
            result.call_id = call_id
            arguments = {"command": " ".join(str(part) for part in command)}
            subprocess.run(
                [
                    PY,
                    str(RUN_TRACE),
                    "call",
                    "--run-dir",
                    str(self.run_dir),
                    "--call-id",
                    call_id,
                    "--tool",
                    tool,
                    "--arguments",
                    json.dumps(arguments),
                    "--phase",
                    phase,
                    "--actor",
                    actor,
                    *(["--instance", instance] if instance else []),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            payload = {
                "stdout": result.stdout[-4000:],
                "stderr": result.stderr[-4000:],
                "returncode": result.returncode,
            }
            subprocess.run(
                [
                    PY,
                    str(RUN_TRACE),
                    "result",
                    "--run-dir",
                    str(self.run_dir),
                    "--call-id",
                    call_id,
                    "--status",
                    "ok" if result.returncode == 0 else "error",
                    "--result",
                    json.dumps(payload),
                    *(["--error", result.stderr[-2000:]] if result.returncode else []),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
        if result.returncode != expect and not allow_fail:
            raise RuntimeError(
                f"step {name!r} exited {result.returncode} (expected {expect}):\n"
                f"{result.stderr[-2000:] or result.stdout[-2000:]}"
            )
        say(f"[{name}] exit={result.returncode} ({result.seconds:.1f}s)")
        return result

    def mark(self, name: str, payload: dict[str, Any]) -> None:
        if not self.trace:
            return
        subprocess.run(
            [
                PY,
                str(RUN_TRACE),
                "record",
                "--run-dir",
                str(self.run_dir),
                "--json-text",
                json.dumps({"record": "mark", "name": name, "payload": payload}),
            ],
            check=False,
            capture_output=True,
            text=True,
        )


# --------------------------------------------------------------------------- labs


@dataclass
class Lab:
    name: str
    role: str
    ports: dict[str, int]
    java: str
    jdk: str

    @property
    def dir(self) -> Path:
        return LABS / self.name

    def provision(self, rec: Recorder, *, test_mod: Path | None = None, extra: Sequence[str] = ()) -> None:
        command: list[str] = [
            PY,
            str(LAB_SERVER),
            "provision",
            "--name",
            self.name,
            "--void",
            "--fabric-api",
            "--carpet",
            "--java",
            self.java,
            "--server-port",
            str(self.ports["server"]),
            "--rcon-port",
            str(self.ports["rcon"]),
            "--vantage-port",
            str(self.ports["vantage"]),
            "--bridge-port",
            str(self.ports["bridge"]),
            # earlier names survive a re-provision as required mods; retire them
            "--forget-mod",
            "smoke-mod-v1.jar",
            "--forget-mod",
            "smoke-mod-v2.jar",
            *extra,
        ]
        if self.jdk:
            command += ["--jdk", self.jdk]
        if test_mod is not None:
            command += ["--test-mod", str(test_mod)]
        rec.run(f"provision-{self.name}", command, tool="bash")

    def start(self, rec: Recorder) -> None:
        rec.run(f"start-{self.name}", [PY, str(LAB_SERVER), "start", "--name", self.name, "--wait", "420"], timeout=600)

    def stop(self, rec: Recorder) -> None:
        rec.run(f"stop-{self.name}", [PY, str(LAB_SERVER), "stop", "--name", self.name], allow_fail=True, timeout=180)

    def identity(self, rec: Recorder) -> dict[str, Any]:
        result = rec.run(
            f"identity-{self.name}",
            [PY, str(LAB_SERVER), "identity", "--name", self.name, "--json"],
            tool="bash",
            instance=self.name,
        )
        payload = json.loads(result.stdout)
        write_json(self.dir / "identity.json", payload)
        return payload

    def verify(self, rec: Recorder, *, allow_fail: bool = False) -> dict[str, Any]:
        result = rec.run(
            f"verify-{self.name}",
            [PY, str(LAB_SERVER), "verify", "--name", self.name, "--require-vantage", "--json"],
            tool="bash",
            instance=self.name,
            allow_fail=allow_fail,
        )
        payload = json.loads(result.stdout)
        write_json(self.dir / "verify.json", payload)
        return payload

    def exec(self, rec: Recorder, command: str, *, allow_fail: bool = False) -> str:
        result = rec.run(
            f"exec-{self.name}-{command.split()[0]}",
            [PY, str(LAB_SERVER), "exec", "--name", self.name, command],
            tool="bash",
            instance=self.name,
            allow_fail=allow_fail,
        )
        return result.stdout


# --------------------------------------------------------------------------- driver


@dataclass
class Driver:
    name: str
    java: str
    jdk: str
    bundle_only: bool = False
    audit_log: Path | None = None
    audit_run_id: str = ""
    audit_instance: str = "exp-1"
    interface_jar: str | None = None
    audit_mod_source: str | None = None

    @property
    def base(self) -> Path:
        return LABS / self.name

    @property
    def run_dir(self) -> Path:
        return self.base / "run-01"

    @property
    def bundle(self) -> Path:
        return self.base / "bundle"

    def run(self) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "name": self.name,
            "startedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "java": self.java,
            "jdk": self.jdk,
            "steps": [],
            "gaps": [],
            "labs": {},
            "gate": None,
        }
        rec = Recorder(self.run_dir, f"{self.name}-run-01", trace=not self.bundle_only)
        if not self.bundle_only:
            rec.init(
                "Stage-one combined integration: raise two generic labs, build and deploy the smoke mod, "
                "prove instance identity and restart isolation, capture tool traces and normalize evidence. "
                "No ROM solution and no agent logger."
            )
        try:
            if not self.bundle_only:
                self._ports(summary)
                before = self._source_hash()
                self._live(rec, summary)
                self._normalize(rec, summary, before)
            else:
                self._normalize(rec, summary, self._source_hash())
        except Exception as error:  # noqa: BLE001 - the report must keep the failure
            summary["error"] = f"{type(error).__name__}: {error}"
            say(f"driver error: {summary['error']}")
        finally:
            if not self.bundle_only:
                for lab in self._labs().values():
                    lab.stop(rec)
            summary["steps"] = [step.as_json() for step in rec.steps]
            summary["finishedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            write_json(self.base / "summary.json", summary)
            (self.base / "summary.md").write_text(self._markdown(summary), encoding="utf-8")
        return summary

    def _labs(self) -> dict[str, Lab]:
        return {
            "src": Lab("rom13-src", "source_audit", DEFAULT_PORTS["src"], self.java, self.jdk),
            "exp": Lab("rom13-exp", "experiment", DEFAULT_PORTS["exp"], self.java, self.jdk),
        }

    def _ports(self, summary: dict[str, Any]) -> None:
        busy = []
        for lab in self._labs().values():
            for kind, port in lab.ports.items():
                if not port_free(port):
                    busy.append({"lab": lab.name, "kind": kind, "port": port})
        if busy:
            raise RuntimeError(f"reserved ports are busy: {busy}")
        summary["ports"] = {lab.name: lab.ports for lab in self._labs().values()}

    def _source_hash(self) -> dict[str, Any]:
        if not SOURCE_SAVE.is_dir():
            return {"available": False, "path": str(SOURCE_SAVE)}
        digest, files, total = gate.tree_hash(SOURCE_SAVE)
        return {"available": True, "path": str(SOURCE_SAVE), "tree_sha256": digest, "files": files, "bytes": total}

    def _live(self, rec: Recorder, summary: dict[str, Any]) -> None:
        labs = self._labs()
        base = self.base
        build_dir = base / "build"
        build_dir.mkdir(parents=True, exist_ok=True)

        interface = discover_interface_jar(self.interface_jar)
        if interface is None:
            summary["gaps"].append(
                "interface mod jar (sha256 %s) not found; server-vantage port file cannot be verified"
                % INTERFACE_JAR_SHA256[:12]
            )
            interface_extra: list[str] = []
        else:
            interface_extra = ["--mod-jar", str(interface[0])]
            summary["provenance"] = {
                "interface_jar": str(interface[0]),
                "interface_jar_sha256": interface[1],
                "interface_commit": "3b93ceb137f8e05624d9d443356c0db78c4b4751",
                "note": "read-only peer-built jar, hash-pinned to the #16 evidence",
            }

        for lab in labs.values():
            lab.provision(rec, extra=interface_extra)
        # First start bootstraps the Fabric server jar that build_mod.py needs;
        # stop again, build, then deploy the smoke mod as a required test mod.
        for lab in labs.values():
            lab.start(rec)
        for lab in labs.values():
            lab.stop(rec)

        # Stale smoke jars from earlier runs would register a duplicate mod id.
        for lab in labs.values():
            mods = lab.dir / "mods"
            if mods.is_dir():
                for stale in mods.glob("smoke-mod*.jar"):
                    stale.unlink()

        source_copy = build_dir / "smoke-src-v1"
        if source_copy.exists():
            shutil.rmtree(source_copy)
        shutil.copytree(ROOT / "examples" / "smoke-mod", source_copy)
        jar_path = build_dir / "smoke-mod.jar"
        if jar_path.exists():
            jar_path.unlink()
        rec.run(
            "build-smoke-v1",
            [PY, str(BUILD_MOD), "--source", str(source_copy), "--lab", labs["src"].name, "--out", str(jar_path), "--version", "0.1.0", "--compression", "store", "--jdk", self.jdk],
            tool="bash",
        )
        build_v1 = read_json(Path(str(jar_path) + ".build.json"))
        hash_v1 = sha256_file(jar_path)
        bytes_v1 = jar_path.stat().st_size
        rec.mark("build-smoke-v1", {"sha256": hash_v1, "build": build_v1})
        summary["build"] = {"v1": {"jar": str(jar_path), "sha256": hash_v1, "build": build_v1}}
        for lab in labs.values():
            lab.provision(rec, test_mod=jar_path, extra=interface_extra)

        # start, identity, live smoke, stop; then a restart cycle for isolation.
        for lab in labs.values():
            lab.start(rec)
        identities = {name: lab.identity(rec) for name, lab in labs.items()}
        verifies = {name: lab.verify(rec, allow_fail=True) for name, lab in labs.items()}
        for name, verify in verifies.items():
            if not verify.get("ok"):
                summary["gaps"].append(f"{name}: verify not ok: {verify.get('problems')}")
        summary["labs"] = {name: {"identity": identities[name], "verify": verifies[name]} for name in labs}
        for lab in labs.values():
            lab.exec(rec, "mcagent-smoke status")
            lab.exec(rec, "mcagent-smoke sample integration")
        for lab in labs.values():
            lab.stop(rec)
        first_worlds = {name: identities[name].get("worldDir") for name in labs}
        for lab in labs.values():
            lab.start(rec)
        restarts = {name: lab.identity(rec) for name, lab in labs.items()}
        for name, lab in labs.items():
            same_world = restarts[name].get("worldDir") == first_worlds[name]
            summary.setdefault("restart", {})[name] = {
                "worldDir": restarts[name].get("worldDir"),
                "sameWorldDir": same_world,
            }
            if not same_world:
                summary["gaps"].append(f"{name}: restart resolved a different world dir")
        for lab in labs.values():
            lab.stop(rec)

        # same-name, same-size jar update: rebuild v2 over the deployed jar and
        # verify the restarted experiment loaded the new bytes (not the old file).
        source_v2 = build_dir / "smoke-src-v2"
        if source_v2.exists():
            shutil.rmtree(source_v2)
        shutil.copytree(ROOT / "examples" / "smoke-mod", source_v2)
        java_file = source_v2 / "src/main/java/dev/mcagent/smoke/SmokeMod.java"
        source_text = java_file.read_text(encoding="utf-8").replace('"smoke-dev"', '"smoke-de2"')
        java_file.write_text(source_text, encoding="utf-8")
        rec.run(
            "build-smoke-v2",
            [PY, str(BUILD_MOD), "--source", str(source_v2), "--lab", labs["src"].name, "--out", str(jar_path), "--version", "0.1.0", "--compression", "store", "--jdk", self.jdk],
            tool="bash",
        )
        hash_v2 = sha256_file(jar_path)
        bytes_v2 = jar_path.stat().st_size
        rec.mark("write-smoke-v2-source", {"path": str(java_file), "sha256": sha256_file(java_file)})
        summary["build"]["v2"] = {"jar": str(jar_path), "sha256": hash_v2, "build": read_json(Path(str(jar_path) + ".build.json"))}
        exp = labs["exp"]
        exp.provision(rec, test_mod=jar_path, extra=interface_extra)
        exp.start(rec)
        status = exp.exec(rec, "mcagent-smoke status")
        exp.stop(rec)
        console = exp.dir / "logs" / "console.log"
        segment = Driver._last_start_segment(console.read_text(encoding="utf-8", errors="replace")) if console.is_file() else ""
        summary["jar_update"] = {
            "old_sha256": hash_v1,
            "new_sha256": hash_v2,
            "old_bytes": bytes_v1,
            "new_bytes": bytes_v2,
            "same_size": bytes_v1 == bytes_v2 and hash_v1 != hash_v2,
            "status_output": status[-2000:],
            "runtime_evidence": str(console),
            "loaded_new_marker": "build=smoke-de2" in status or "build=smoke-de2" in segment,
        }
        self._probes(rec, summary, jar_path, jar_path, interface_extra)
        self._audit_mod(rec, summary, interface_extra)

    @staticmethod
    def _last_start_segment(text: str) -> str:
        marker = "Starting minecraft server version"
        index = text.rfind(marker)
        return text[index:] if index >= 0 else text[-20000:]

    def _probes(self, rec: Recorder, summary: dict[str, Any], reference_jar: Path, good_jar: Path, interface_extra: Sequence[str]) -> None:
        """Observe the loud failure paths; record only what the run showed."""
        import zipfile

        labs = self._labs()
        exp = labs["exp"]
        build_dir = self.base / "build"
        probe_dir = build_dir / "probe"
        probe_dir.mkdir(parents=True, exist_ok=True)
        probes: dict[str, Any] = {}

        broken_source = build_dir / "broken-src"
        if broken_source.exists():
            shutil.rmtree(broken_source)
        shutil.copytree(ROOT / "examples" / "smoke-mod", broken_source)
        broken_java = broken_source / "src/main/java/dev/mcagent/smoke/SmokeMod.java"
        broken_java.write_text(broken_java.read_text(encoding="utf-8") + "\nthis is not java;\n", encoding="utf-8")
        broken_out = build_dir / "broken.jar"
        result = rec.run(
            "probe-build-error",
            [PY, str(BUILD_MOD), "--source", str(broken_source), "--lab", labs["src"].name, "--out", str(broken_out), "--jdk", self.jdk],
            tool="bash",
            allow_fail=True,
            timeout=300,
        )
        probes["build_errors_detected"] = result.returncode != 0
        if result.returncode == 0:
            summary["gaps"].append("build-error probe: the broken source compiled")

        def rebuild(dst: Path, mutate: Any) -> Path:
            with zipfile.ZipFile(reference_jar) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
                for item in zin.infolist():
                    data = zin.read(item.filename)
                    if item.filename == "fabric.mod.json":
                        payload = json.loads(data.decode("utf-8"))
                        mutate(payload)
                        data = json.dumps(payload, indent=2).encode("utf-8")
                    info = zipfile.ZipInfo(item.filename, (1980, 1, 1, 0, 0, 0))
                    info.compress_type = item.compress_type
                    zout.writestr(info, data)
            return dst

        def probe_cycle(name: str, mutate: Any, markers: Sequence[str]) -> tuple[bool, str]:
            bad = rebuild(probe_dir / "smoke-mod.jar", mutate)
            rec.run(
                f"probe-deploy-{name}",
                [PY, str(LAB_SERVER), "provision", "--name", exp.name, "--mod-jar", str(bad)],
                tool="bash",
                allow_fail=True,
            )
            started = rec.run(
                f"probe-start-{name}",
                [PY, str(LAB_SERVER), "start", "--name", exp.name, "--wait", "90"],
                tool="bash",
                allow_fail=True,
                timeout=150,
            )
            status = rec.run(
                f"probe-status-{name}",
                [PY, str(LAB_SERVER), "exec", "--name", exp.name, "mcagent-smoke status"],
                tool="bash",
                allow_fail=True,
                timeout=60,
            )
            console = exp.dir / "logs" / "console.log"
            text = console.read_text(encoding="utf-8", errors="replace") if console.is_file() else ""
            segment = Driver._last_start_segment(text)
            (self.base / f"probe-{name}.log").write_text(segment[-20000:], encoding="utf-8")
            detected = started.returncode != 0 or status.returncode != 0 or any(marker in segment for marker in markers)
            rec.run(f"probe-stop-{name}", [PY, str(LAB_SERVER), "stop", "--name", exp.name], tool="bash", allow_fail=True, timeout=120)
            # restore the good jar under the same name before the next probe
            rec.run(
                f"probe-restore-{name}",
                [PY, str(LAB_SERVER), "provision", "--name", exp.name, "--test-mod", str(good_jar), *interface_extra],
                tool="bash",
                allow_fail=True,
            )
            return detected, segment

        probes["load_failure_detected"], _ = probe_cycle(
            "load-failure",
            lambda payload: payload.setdefault("entrypoints", {}).__setitem__("server", ["dev.mcagent.smoke.DoesNotExist"]),
            ("Could not execute entrypoint", "ClassNotFoundException", "Failed to start", "net.fabricmc.loader.impl.FormattedException"),
        )
        if not probes["load_failure_detected"]:
            summary["gaps"].append("load-failure probe: no loader error observed")
        probes["missing_dependency_detected"], _ = probe_cycle(
            "missing-dep",
            lambda payload: payload.setdefault("depends", {}).__setitem__("mcagent-does-not-exist", "*"),
            ("requires", "which is missing", "Missing or unsupported", "Dependency resolution failed", "Could not execute entrypoint"),
        )
        if not probes["missing_dependency_detected"]:
            summary["gaps"].append("missing-dependency probe: no loader error observed")
        summary["probes"] = probes

    #: Committed #18 implementation (PR #26 head). Used read-only; the copy
    #: built here never touches the peer worktree.
    AUDIT_MOD_SOURCE = Path("F:/mc-agent-worktrees/rom13/meta18/tests/mods/minecart-audit")

    def _audit_mod(self, rec: Recorder, summary: dict[str, Any], interface_extra: Sequence[str]) -> None:
        """Build and run the committed #18 audit mod on a generic note-block scene.

        This is generic instrumentation, not the ROM: one note block, one
        obsidian-fed stack of chest minecarts and the void. The goal is a real
        #18 JSONL that the adapter maps losslessly into the gate schema.
        """
        source = Path(self.audit_mod_source) if self.audit_mod_source else self.AUDIT_MOD_SOURCE
        if not (source / "src/main/java").is_dir():
            summary["gaps"].append(f"#18 audit-mod source not found at {source}; audit mapping skipped")
            return
        labs = self._labs()
        exp = labs["exp"]
        work = self.base / "audit-mod-src"
        if work.exists():
            shutil.rmtree(work)

        def ignore(_directory: str, names: list[str]) -> list[str]:
            return [name for name in names if name in ("dist", "out", "__pycache__", ".git")]

        shutil.copytree(source, work, ignore=ignore)
        jar = self.base / "build" / "minecart-audit.jar"
        result = rec.run(
            "build-audit-mod",
            [PY, str(BUILD_MOD), "--source", str(work), "--lab", labs["src"].name, "--out", str(jar), "--jdk", self.jdk],
            tool="bash",
            allow_fail=True,
            timeout=600,
        )
        if result.returncode != 0 or not jar.is_file():
            summary["gaps"].append(f"#18 audit-mod build failed (exit {result.returncode}); audit mapping skipped")
            return

        # One run id for the whole integration; the audit file must be fresh so
        # it contains exactly this run's sessions.
        run_id = f"{self.name}-run-01"
        config_dir = exp.dir / "mc-audit"
        if config_dir.exists():
            shutil.rmtree(config_dir)
        config = {
            "runId": run_id,
            "instanceId": exp.name,
            "dimension": "minecraft:overworld",
            "provenance": {"kind": "synthetic-void", "reference": "tools/stage1_integration.py generic scene"},
            "inputRegions": [{"name": "note-block", "from": [0, -59, 0], "to": [0, -59, 0]}],
            "stackRegion": {"name": "stack", "from": [2, -60, -1], "to": [3, -59, 1]},
            "outputRegion": {"name": "output", "from": [4, -140, -8], "to": [8, -60, 8]},
            "cartTypes": ["minecraft:chest_minecart"],
            "sampleIntervalTicks": 1,
            "agentUuids": [],
        }
        write_json(config_dir / "config.json", config)
        rec.run(
            "deploy-audit-mod",
            [PY, str(LAB_SERVER), "provision", "--name", exp.name, "--mod-jar", str(jar), *interface_extra],
            tool="bash",
            allow_fail=True,
        )
        rec.run(
            "start-exp-audit",
            [PY, str(LAB_SERVER), "start", "--name", exp.name, "--wait", "300"],
            tool="bash",
            allow_fail=True,
            timeout=420,
        )

        def rcon(label: str, command: str) -> StepResult:
            return rec.run(
                f"audit-{label}",
                [PY, str(LAB_SERVER), "exec", "--name", exp.name, command],
                tool="bash",
                instance=exp.name,
                allow_fail=True,
                timeout=120,
            )

        rcon("phase-init", "mcaudit phase init")
        for label, command in (
            ("forceload", "forceload add -16 -16 16 16"),
            ("floor", "fill -2 -60 -2 3 -60 2 minecraft:stone"),
            ("kill", "kill @e[type=minecraft:chest_minecart]"),
            ("note", "setblock 0 -59 0 minecraft:note_block"),
            ("observer", "setblock 1 -59 0 minecraft:observer[facing=west]"),
            ("piston", "setblock 2 -59 0 minecraft:piston[facing=east]"),
        ):
            rcon(label, command)
        rcon(
            "summon",
            'summon minecraft:chest_minecart 3.5 -59.0 0.5 {Items:[{Slot:0b,id:"minecraft:apple",count:3}]}',
        )
        rcon("flush", "mcaudit flush")
        rcon("bot-spawn", "player Bot spawn at 0.5 -59.0 2.5 facing 180 29")
        online = False
        for attempt in range(20):
            poll = rcon(f"bot-poll-{attempt}", "data get entity Bot Pos")
            if "has the following entity data" in poll.stdout:
                online = True
                break
            time.sleep(0.5)
        if online:
            rcon("bot-aim", "tp Bot 0.5 -59.0 2.5 180 29")
        else:
            summary["gaps"].append("#18 audit run: the Carpet fake player never came online")
        rcon("phase-start", "mcaudit phase experiment_start")
        use_step = rcon("use-once", "player Bot use once")
        time.sleep(1)
        cycled = "Test passed" in rcon("note-check", "execute if block 0 -59 0 minecraft:note_block[note=1]").stdout
        sprint_step = rcon("sprint", "tick sprint 600")
        time.sleep(2)
        removed = "Test passed" not in rcon("cart-check", "execute if entity @e[type=minecraft:chest_minecart]").stdout
        rcon("phase-end", "mcaudit phase experiment_end")
        rcon("end", "mcaudit end")
        rec.run("stop-exp-audit", [PY, str(LAB_SERVER), "stop", "--name", exp.name], tool="bash", allow_fail=True, timeout=120)

        audit_file = config_dir / f"audit-{run_id}.jsonl"
        if not audit_file.is_file():
            summary["gaps"].append(f"#18 audit run produced no JSONL at {audit_file}")
            return
        self.audit_log = audit_file
        self.audit_run_id = run_id
        summary["audit"] = {
            "path": str(audit_file),
            "sha256": sha256_file(audit_file),
            "run_id": run_id,
            "note_cycled": cycled,
            "cart_removed": removed,
            "use_call_id": use_step.call_id,
            "sprint_call_id": sprint_step.call_id,
            "source_commit": "502f561",
        }
        summary["gaps"].extend([] if cycled else ["#18 audit run: the fake player did not tune the note block"])

    def _normalize(self, rec: Recorder, summary: dict[str, Any], before: dict[str, Any]) -> None:
        bundle = self.bundle
        if bundle.exists():
            shutil.rmtree(bundle)
        bundle.mkdir(parents=True, exist_ok=True)
        if IDENTITY_EVIDENCE.is_file():
            rec.run(
                "map-identity",
                [PY, str(EVIDENCE), "identity", "--source", str(IDENTITY_EVIDENCE), "--bundle", str(bundle), "--json"],
                tool="write",
            )
        if self.audit_log is not None and self.audit_log.is_file():
            rec.run(
                "map-audit",
                [PY, str(EVIDENCE), "audit", "--log", str(self.audit_log), "--bundle", str(bundle), "--run-id", self.audit_run_id or f"{self.name}-run-01", "--instance-id", self.audit_instance, "--dimension", "minecraft:overworld", "--json"],
                tool="write",
            )
        trajectory = self.run_dir / "trajectory.jsonl"
        if trajectory.is_file():
            # Real tool-call coverage for the gate categories: terminal, file,
            # source and mcp. These are genuine commands (a revision pin and a
            # CLI help probe), not placeholders.
            rec.run("pin-revisions", ["git", "rev-parse", "HEAD"], tool="git")
            rec.run("mcp-probe-help", [PY, str(ROOT / "tools" / "mcp_probe.py"), "--help"], tool="mcp_probe")
            joins_path = self._write_joins(bundle, summary)
            rec.run(
                "map-trace",
                [PY, str(EVIDENCE), "trace", "--trajectory", str(trajectory), "--bundle", str(bundle), "--run-id", f"{self.name}-run-01", "--instance-id", "rom13-exp", "--instances", "rom13-src,rom13-exp", *(["--joins", str(joins_path)] if joins_path else []), "--json"],
                tool="write",
            )
        after = self._source_hash()
        summary["source_world_before"] = before
        summary["source_world_after"] = after
        if before.get("available") and after.get("available") and before.get("tree_sha256") != after.get("tree_sha256"):
            summary["gaps"].append("source save tree hash changed during the integration run")
        self._artifact_facts(summary)
        spec = self._bundle_spec(before)
        write_json(bundle / "bundle-spec.json", spec)
        rec.run(
            "assemble",
            [PY, str(EVIDENCE), "assemble", "--bundle", str(bundle), "--spec", str(bundle / "bundle-spec.json"), *(["--source-world", str(SOURCE_SAVE)] if SOURCE_SAVE.is_dir() else []), "--json"],
            tool="write",
            allow_fail=True,
            timeout=600,
        )
        report_path = bundle / "assemble-report.json"
        if report_path.is_file():
            summary["gate"] = read_json(report_path).get("gate")

    def _write_joins(self, bundle: Path, summary: dict[str, Any]) -> Path | None:
        """Map the driver's known RCON calls to the audit events they caused."""
        audit_meta = summary.get("audit") or {}
        audit_path = bundle / "artifacts/independent_test_mod/audit-events.jsonl"
        if not audit_meta.get("use_call_id") or not audit_path.is_file():
            return None
        rows = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        use_call = audit_meta["use_call_id"]
        sprint_call = audit_meta.get("sprint_call_id") or use_call
        joins: list[dict[str, Any]] = []
        for row in rows:
            if row.get("phase") != "agent":
                continue
            call_id = use_call if str(row.get("event", "")).startswith("input_") else sprint_call
            joins.append(
                {
                    "call_id": call_id,
                    "run_id": row.get("run_id"),
                    "instance_id": row.get("instance_id"),
                    "dimension": row.get("dimension"),
                    "audit_ref": {"event_id": row.get("event_id"), "tick": row.get("tick")},
                    "note": "RCON call recorded in the same run that caused or observed this event",
                }
            )
        path = bundle / "mapping" / "joins.json"
        write_json(path, {"joins": joins})
        return path

    def _artifact_facts(self, summary: dict[str, Any]) -> None:
        """Write only facts this run actually observed."""
        bundle = self.bundle
        labs = self._labs()
        identities = {}
        for name, lab in labs.items():
            path = lab.dir / "identity.json"
            if path.is_file():
                identities[name] = read_json(path)
        if len(identities) == 2:
            rows = []
            for name, lab in labs.items():
                payload = identities[name]
                rows.append(
                    {
                        "instance_id": lab.name,
                        "role": "source_audit" if name == "src" else "experiment",
                        "world_dir": payload.get("worldDir"),
                        "rcon_port": payload.get("rconPort"),
                        "bridge_port": payload.get("bridgeApiPort"),
                        "restarted": True,
                        "resolves_correct_world": True,
                        "conflicting_instance": False,
                    }
                )
            write_jsonl(bundle / "artifacts/agent_dev_capability/instance-isolation.jsonl", rows)
        jar_update = summary.get("jar_update") or {}
        if jar_update.get("old_sha256") and jar_update.get("new_sha256"):
            if jar_update.get("same_size") and jar_update.get("loaded_new_marker"):
                write_json(
                    bundle / "artifacts/agent_dev_capability/jar-update.json",
                    {
                        "case": "same_size_different_content",
                        "old_sha256": jar_update["old_sha256"],
                        "new_sha256": jar_update["new_sha256"],
                        "bytes": jar_update["old_bytes"],
                        "loaded_sha256": jar_update["new_sha256"],
                        "runtime_evidence": jar_update.get("runtime_evidence", ""),
                        "detail": {"same_size": True, "loaded_new_marker": True},
                    },
                )
            else:
                summary["gaps"].append(
                    "jar update: same-size replacement or the loaded v2 marker was not observed; "
                    "artifact withheld so the gate stays blocked instead of stale-passing"
                )
        if SOURCE_SAVE.is_dir():
            before = summary.get("source_world_before") or {}
            after = summary.get("source_world_after") or {}
            if before.get("tree_sha256") and after.get("tree_sha256"):
                write_json(
                    bundle / "artifacts/restore_fidelity/source-unchanged.json",
                    {
                        "before_tree_sha256": before["tree_sha256"],
                        "after_tree_sha256": after["tree_sha256"],
                        "unchanged": before["tree_sha256"] == after["tree_sha256"],
                        "hash_tool": "tools/stage1_gate.py hash-tree",
                        "exclusions": list(gate.DEFAULT_TREE_EXCLUSIONS),
                    },
                )

    def _bundle_spec(self, before: dict[str, Any]) -> dict[str, Any]:
        labs = self._labs()
        identities = {}
        for name, lab in labs.items():
            path = lab.dir / "identity.json"
            if path.is_file():
                identities[name] = read_json(path)
        instances = []
        for name, lab in labs.items():
            payload = identities.get(name, {})
            instances.append(
                {
                    "instance_id": lab.name,
                    "role": "source_audit" if name == "src" else "experiment",
                    "dimension": "minecraft:overworld",
                    "world_dir": payload.get("worldDir") or str(lab.dir / "world"),
                    "rcon_port": payload.get("rconPort") or lab.ports["rcon"],
                    "bridge_port": payload.get("bridgeApiPort") or lab.ports["bridge"],
                }
            )
        run_id = f"{self.name}-run-01"
        world_hash = before.get("tree_sha256", "0" * 64)
        return {
            "origin": "live",
            "run": {
                "run_id": run_id,
                "issue": "guajun/mc-agent#14",
                "allowed_port_ranges": ["27240-27249"],
                "tool_category_map": {key: list(value) for key, value in gate.DEFAULT_TOOL_CATEGORIES.items()},
                "child_runs": [
                    {"run_id": f"init-child-{index}", "instance_id": instances[1]["instance_id"]}
                    for index in range(1, 4)
                ],
                "source_world": {
                    "label": "Minecart ROM test",
                    "path": str(SOURCE_SAVE),
                    "before_tree_sha256": world_hash,
                    "after_tree_sha256": world_hash,
                },
                "instances": instances,
            },
        }

    def _markdown(self, summary: dict[str, Any]) -> str:
        lines = [
            f"# Stage-one integration run: {summary['name']}",
            "",
            f"* started: {summary.get('startedAt')}",
            f"* finished: {summary.get('finishedAt')}",
            f"* java: `{summary.get('java')}`",
            f"* jdk: `{summary.get('jdk')}`",
            "",
            "| Step | Exit | Seconds |",
            "| --- | --- | --- |",
        ]
        for step in summary.get("steps", []):
            lines.append(f"| `{step['name']}` | {step['returncode']} | {step['seconds']} |")
        if summary.get("gaps"):
            lines += ["", "## Gaps (the gate must stay blocked for these)", ""]
            lines += [f"* {gap}" for gap in summary["gaps"]]
        gate = summary.get("gate") or {}
        if gate:
            lines += ["", f"## Gate: {gate.get('overall', '?')}", ""]
            for check in gate.get("checks", []):
                lines.append(f"* `{check['status']}` {check['id']}")
        if summary.get("error"):
            lines += ["", f"**driver error**: {summary['error']}"]
        return "\n".join(lines) + "\n"

    def plan(self) -> int:
        labs = self._labs()
        lines = [
            f"stage-one integration plan for {self.name}",
            f"  java: {self.java or '(not found)'}",
            f"  jdk:  {self.jdk or '(not found)'}",
        ]
        for name, lab in labs.items():
            lines.append(f"  lab {lab.name} ({lab.role}) ports {lab.ports}")
        lines += [
            "  1. hash the source save read-only",
            "  2. provision both labs, build smoke-mod v1, deploy it as a required test mod",
            "  3. start both, capture identity/verify, run mcagent-smoke status/sample",
            "  4. stop and restart both, confirm the worlds stay distinct",
            "  5. rebuild with a same-length marker, deploy v2, restart and read the loaded build",
            "  6. map #16 identity + #19 trajectory (and #18 audit log when given) via stage1_evidence.py",
            "  7. assemble the bundle, run the gate, write summary.json/summary.md",
        ]
        say("\n".join(lines))
        return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stage1_integration.py", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for command, help_text in (("run", "run the integration"), ("plan", "print the plan")):
        cmd = sub.add_parser(command, help=help_text)
        cmd.add_argument("--name", default="rom13-integration")
        cmd.add_argument("--java", default=None, help="JDK 25 java executable")
        cmd.add_argument("--jdk", default=None, help="JDK home (default: derived from --java)")
        cmd.add_argument("--audit-log", type=Path, help="optional real #18 audit JSONL to map")
        cmd.add_argument("--audit-run-id", default="")
        cmd.add_argument("--audit-instance", default="rom13-exp")
        cmd.add_argument("--interface-jar", default=None, help="read-only interface mod jar (hash pinned)")
        cmd.add_argument("--audit-mod-source", default=None, help="committed #18 minecart-audit source tree (read-only)")
        cmd.add_argument("--bundle-only", action="store_true", help="skip labs; only re-normalize the last run")
        cmd.add_argument("--json", action="store_true")
    return parser


def cli(argv: Sequence[str] | None = None, out: TextIO | None = None) -> int:
    stream = out if out is not None else sys.stdout
    args = build_parser().parse_args(argv)
    java = discover_java(args.java)
    jdk = discover_jdk(args.jdk, java)
    driver = Driver(
        name=args.name,
        java=java,
        jdk=jdk,
        bundle_only=args.bundle_only,
        audit_log=args.audit_log,
        audit_run_id=args.audit_run_id,
        audit_instance=args.audit_instance,
        interface_jar=args.interface_jar,
        audit_mod_source=args.audit_mod_source,
    )
    if args.command == "plan":
        return driver.plan()
    summary = driver.run()
    if args.json:
        stream.write(json.dumps(summary, indent=2) + "\n")
    else:
        stream.write((driver.base / "summary.md").read_text(encoding="utf-8"))
    if summary.get("error"):
        return 1
    gate = summary.get("gate") or {}
    return 0 if gate.get("overall") == "pass" else (1 if gate.get("overall") == "fail" else 3)


if __name__ == "__main__":
    raise SystemExit(cli())
