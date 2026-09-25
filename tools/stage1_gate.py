#!/usr/bin/env python3
"""Stage-one integration gate for the Minecart ROM prerequisites.

Issue ``guajun/mc-agent#14`` says the machine may only be handed to a cold-start
agent once *every* prerequisite has live evidence: a pinned map fixture, a
faithful restore, verified player identity/context, agent build/install
capability, an independent test mod, persistent tool/audit traces, and a
generic tool-level smoke run.  Source-level unit tests are not that evidence.

This tool is that gate.  It does not run the game and it cannot create
evidence: it reads a *gate bundle* - one directory with a ``bundle.json``
manifest plus the prerequisite artifacts - validates every artifact against a
strict schema, recomputes what can be recomputed (snapshot order/inventory,
artifact and tree hashes, audit event ordering, port isolation, source-world
tree hash) and fails closed:

* missing bundle / check entry / artifact, or scaffold origin  -> blocked
* artifact present but invalid, inconsistent or incomplete     -> fail
* only when every check passes                                -> pass

Exit codes: 0 pass, 1 fail, 3 blocked, 2 usage error.  A script therefore
cannot mistake "prerequisites are not ready" for success.

    python tools/stage1_gate.py scaffold labs/stage1-evidence
    python tools/stage1_gate.py check labs/stage1-evidence \
        --source-world "D:/MC/MC_Game/.minecraft/versions/26.2-Fabric/saves/Minecart ROM test"
    python tools/stage1_gate.py check labs/stage1-evidence --report stage1-report.json
    python tools/stage1_gate.py list
    python tools/stage1_gate.py list --json
    python tools/stage1_gate.py hash-tree "<world dir>"
    python tools/stage1_gate.py selftest

``scaffold`` writes the canonical layout with no evidence; ``check`` on it
reports every check blocked.  A bundle that is allowed to pass must use
``origin: "live"`` and artifacts produced by the actual prerequisite work.

Documentation and the artifact schema: ``docs/stage1-gate.md`` (and
``docs/zh/stage1-gate.md``).
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import io
import json
import math
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Sequence, TextIO

# The gate consumes fork_verify's snapshot format; sit next to it.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fork_verify  # noqa: E402

# --------------------------------------------------------------------------- constants

SCHEMA_VERSION = 1
GATE_ID = "rom13-stage1"
ISSUE = "guajun/mc-agent#14"
BUNDLE_KIND = "mc-agent.stage1.gate-bundle"

STATUS_PASS = "pass"
STATUS_FAIL = "fail"
STATUS_BLOCKED = "blocked"

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_USAGE = 2
EXIT_BLOCKED = 3

HEX64 = re.compile(r"[0-9a-f]{64}\Z")
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX16 = re.compile(r"[0-9a-f]{16}\Z")
PORT_RANGE = re.compile(r"([0-9]{1,5})\s*-\s*([0-9]{1,5})\Z")

#: This issue's allocated port range; the integration run must use it.
DEFAULT_PORT_RANGE = "27240-27249"

#: Run id used by the selftest fixture bundle.
SELFTEST_RUN_ID = "rom13-stage1-selftest"

#: Files whose bytes are volatile, not part of a world's identity.
DEFAULT_TREE_EXCLUSIONS: tuple[str, ...] = (
    "session.lock",
    "logs",
    "*.log",
    ".DS_Store",
    "Thumbs.db",
    "__pycache__",
)

#: Default name patterns for tool-trace category coverage; overridable with
#: ``run.tool_category_map`` in the bundle.
DEFAULT_TOOL_CATEGORIES: dict[str, tuple[str, ...]] = {
    "terminal": ("bash", "sh", "zsh", "shell", "terminal", "cmd", "powershell", "pwsh", "exec"),
    "file": ("write", "edit", "apply_patch", "patch", "file", "filesystem"),
    "source": ("fetch", "download", "git", "clone", "gradle", "maven", "mvn", "javac", "build", "compile"),
    "mcp": ("mcp", "mc_", "bridge"),
}

FAILURE_CASES = (
    "summon_refusal",
    "duplicate_pre_existing",
    "wrong_endpoint",
    "inventory_mutation_order_hash",
    "corrupt_metadata",
    "partial_failure",
)

NEGATIVE_CASES = ("no_interaction", "wrong_position", "marker_only", "answer_only")

IDENTITY_CASES = ("task_bind", "hit", "miss", "two_players", "unknown_identity")

SMOKE_SUITES = ("smoke_offline", "lab_boot", "fake_player_mcp", "snapshot_restore", "test_mod_load")

#: component name -> how it is pinned in ``version_lock.json``
VERSION_LOCK_REQUIRED: dict[str, str] = {
    "mc-agent": "commit",
    "mc-agent-interface-mod": "commit",
    "mc-agent-bridge": "commit",
    "minecraft": "version",
    "fabric-loader": "version",
    "carpet": "sha256",
    "test-mod": "sha256",
    "fixture-map": "sha256",
    "jdk": "version",
}


# --------------------------------------------------------------------------- small helpers


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_iso(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        datetime.fromisoformat(text)
    except ValueError:
        return False
    return True


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_vec3(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and len(value) == 3 and all(_is_number(item) for item in value)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _excluded(rel: str, patterns: Sequence[str]) -> bool:
    name = PurePosixPath(rel).name
    for pattern in patterns:
        if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(name, pattern):
            return True
        prefix = pattern.rstrip("/")
        if rel == prefix or rel.startswith(prefix + "/"):
            return True
    return False


def tree_hash(path: Path, exclusions: Sequence[str] = DEFAULT_TREE_EXCLUSIONS) -> tuple[str, int, int]:
    """Deterministic hash of a directory tree: (digest, files, bytes).

    Sorted relative POSIX paths, sizes and per-file SHA-256s; exclusion
    patterns match a path, a path prefix or a basename the way ``fnmatch``
    does.  The same algorithm must be used everywhere, or hashes are not
    comparable.
    """
    root = Path(path)
    digest = hashlib.sha256()
    files = 0
    total = 0
    entries = sorted(
        (item for item in root.rglob("*") if item.is_file()),
        key=lambda item: item.relative_to(root).as_posix(),
    )
    for item in entries:
        rel = item.relative_to(root).as_posix()
        if _excluded(rel, exclusions):
            continue
        size = item.stat().st_size
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(size).encode("ascii"))
        digest.update(b"\0")
        digest.update(_sha256_file(item).encode("ascii"))
        digest.update(b"\n")
        files += 1
        total += size
    return digest.hexdigest(), files, total


def _resolve_rel(root: Path, rel: Any) -> Path | None:
    """Resolve a bundle-relative path; reject absolute paths and escapes."""
    if not isinstance(rel, str) or not rel or rel.strip() != rel or "\\" in rel:
        return None
    pure = PurePosixPath(rel)
    if pure.is_absolute():
        return None
    if any(part == ".." for part in pure.parts):
        return None
    return root.joinpath(*pure.parts)


def _read_json(path: Path) -> tuple[Any, str | None]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        return None, f"cannot be read: {error}"
    try:
        return json.loads(text), None
    except json.JSONDecodeError as error:
        return None, f"not valid JSON: {error}"


def _read_jsonl(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        return rows, [f"cannot be read: {error}"]
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            errors.append(f"line {number}: not valid JSON: {error}")
            continue
        if not isinstance(record, dict):
            errors.append(f"line {number}: expected a JSON object")
            continue
        rows.append(record)
    return rows, errors


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
    path.write_text(text + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- data model


@dataclass(frozen=True)
class ArtifactSpec:
    kind: str
    filename: str
    kind_type: str  # json | jsonl | tree
    description: str


@dataclass
class Problem:
    level: str  # fail | blocked
    code: str
    message: str


@dataclass
class Assertion:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class EvidenceState:
    kind: str
    path: str
    status: str  # ok | missing | wrong_type | unreadable | bad_path
    sha256: str | None = None
    tree_sha256: str | None = None
    bytes: int | None = None
    files: int | None = None
    detail: str = ""


@dataclass
class CheckResult:
    id: str
    title: str
    status: str
    reasons: list[Problem]
    assertions: list[Assertion]
    evidence: list[EvidenceState]


@dataclass
class CheckSpec:
    id: str
    title: str
    issue: str
    summary: str
    artifacts: dict[str, ArtifactSpec]
    runner: Callable[["CheckContext"], None] | None


@dataclass
class GateReport:
    root: str
    overall: str
    checks: list[CheckResult]
    limitations: list[str]
    generated_at: str

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "gate": GATE_ID,
            "issue": ISSUE,
            "generated_at": self.generated_at,
            "bundle": self.root,
            "overall": self.overall,
            "checks": [
                {
                    "id": check.id,
                    "title": check.title,
                    "status": check.status,
                    "reasons": [
                        {"level": problem.level, "code": problem.code, "message": problem.message}
                        for problem in check.reasons
                    ],
                    "assertions": [
                        {"name": item.name, "ok": item.ok, "detail": item.detail}
                        for item in check.assertions
                    ],
                    "evidence": [
                        {
                            "kind": item.kind,
                            "path": item.path,
                            "status": item.status,
                            "sha256": item.sha256,
                            "tree_sha256": item.tree_sha256,
                            "bytes": item.bytes,
                            "files": item.files,
                            "detail": item.detail,
                        }
                        for item in check.evidence
                    ],
                }
                for check in self.checks
            ],
            "limitations": self.limitations,
        }


class Bundle:
    """The parsed ``bundle.json``; tolerates a missing manifest."""

    def __init__(self, root: Path, manifest: Any, parse_problem: Problem | None):
        self.root = root
        self.parse_problem = parse_problem
        self.manifest: dict[str, Any] = manifest if isinstance(manifest, dict) else {}

    def _entry(self, check_id: str) -> dict[str, Any]:
        checks = self.manifest.get("checks")
        if not isinstance(checks, dict):
            return {}
        entry = checks.get(check_id)
        return entry if isinstance(entry, dict) else {}

    def artifact_ref(self, check_id: str, kind: str) -> str | None:
        evidence = self._entry(check_id).get("evidence")
        if not isinstance(evidence, dict):
            return None
        ref = evidence.get(kind)
        return ref if isinstance(ref, str) and ref else None

    @property
    def origin(self) -> Any:
        return self.manifest.get("origin")

    @property
    def run_id(self) -> Any:
        run = self.manifest.get("run")
        if not isinstance(run, dict):
            return None
        return run.get("run_id")

    @property
    def instances(self) -> list[dict[str, Any]]:
        run = self.manifest.get("run")
        if not isinstance(run, dict):
            return []
        instances = run.get("instances")
        if not isinstance(instances, list):
            return []
        return [item for item in instances if isinstance(item, dict)]

    @property
    def instance_dimensions(self) -> dict[str, str]:
        """The declared ``instance_id -> dimension`` pairs for provenance."""
        result: dict[str, str] = {}
        for instance in self.instances:
            instance_id = instance.get("instance_id")
            dimension = instance.get("dimension")
            if isinstance(instance_id, str) and instance_id and isinstance(dimension, str) and dimension:
                result[instance_id] = dimension
        return result

    @property
    def source_world(self) -> dict[str, Any]:
        run = self.manifest.get("run")
        if not isinstance(run, dict):
            return {}
        world = run.get("source_world")
        return world if isinstance(world, dict) else {}

    @property
    def allowed_port_ranges(self) -> list[str]:
        run = self.manifest.get("run")
        if not isinstance(run, dict):
            return []
        ranges = run.get("allowed_port_ranges")
        if not isinstance(ranges, list):
            return []
        return [item for item in ranges if isinstance(item, str)]


# --------------------------------------------------------------------------- inspection


def inspect_artifact(root: Path, rel: str, kind: str, expect_dir: bool) -> EvidenceState:
    resolved = _resolve_rel(root, rel)
    if resolved is None:
        return EvidenceState(kind, rel, "bad_path", detail="must stay inside the bundle and use '/'")
    if not resolved.exists():
        return EvidenceState(kind, rel, "missing", detail="not found")
    if expect_dir and not resolved.is_dir():
        return EvidenceState(kind, rel, "wrong_type", detail="expected a directory")
    if not expect_dir and not resolved.is_file():
        return EvidenceState(kind, rel, "wrong_type", detail="expected a regular file")
    try:
        if expect_dir:
            digest, files, total = tree_hash(resolved)
            return EvidenceState(
                kind, rel, "ok", tree_sha256=digest, bytes=total, files=files, detail=f"{files} file(s)"
            )
        return EvidenceState(kind, rel, "ok", sha256=_sha256_file(resolved), bytes=resolved.stat().st_size)
    except OSError as error:
        return EvidenceState(kind, rel, "unreadable", detail=str(error))


class CheckContext:
    """One check's problem/assertion/evidence accumulator and reader."""

    def __init__(self, bundle: Bundle, spec: CheckSpec):
        self.bundle = bundle
        self.spec = spec
        self.problems: list[Problem] = []
        self.assertions: list[Assertion] = []
        self.evidence: list[EvidenceState] = []

    # -- recording ---------------------------------------------------------

    def fail(self, code: str, message: str) -> None:
        self.problems.append(Problem(STATUS_FAIL, code, message))

    def blocked(self, code: str, message: str) -> None:
        self.problems.append(Problem(STATUS_BLOCKED, code, message))

    def assert_(self, name: str, ok: bool, detail: str = "") -> None:
        self.assertions.append(Assertion(name, bool(ok), detail))

    def status(self) -> str:
        levels = {problem.level for problem in self.problems}
        if STATUS_FAIL in levels:
            return STATUS_FAIL
        if STATUS_BLOCKED in levels:
            return STATUS_BLOCKED
        return STATUS_PASS

    def result(self) -> CheckResult:
        return CheckResult(
            id=self.spec.id,
            title=self.spec.title,
            status=self.status(),
            reasons=self.problems,
            assertions=self.assertions,
            evidence=self.evidence,
        )

    # -- artifact access ---------------------------------------------------

    def artifact(self, kind: str) -> Path | None:
        spec = self.spec.artifacts.get(kind)
        if spec is None:
            self.fail("unknown_artifact_kind", f"{kind}: not part of the {self.spec.id} schema")
            return None
        rel = self.bundle.artifact_ref(self.spec.id, kind)
        if rel is None:
            self.blocked(
                "evidence_not_declared",
                f"{kind}: bundle.json checks.{self.spec.id}.evidence has no '{kind}' path",
            )
            return None
        state = inspect_artifact(
            self.bundle.root, rel, kind, expect_dir=(spec.kind_type == "tree")
        )
        self.evidence.append(state)
        if state.status == "missing":
            self.blocked("evidence_missing", f"{kind}: {rel} does not exist")
            return None
        if state.status != "ok":
            self.fail(f"evidence_{state.status}", f"{kind}: {rel} {state.detail}")
            return None
        resolved = _resolve_rel(self.bundle.root, rel)
        assert resolved is not None
        return resolved

    def json(self, kind: str) -> dict[str, Any] | None:
        path = self.artifact(kind)
        if path is None:
            return None
        obj, error = _read_json(path)
        if error is not None:
            self.fail("bad_json", f"{kind}: {error}")
            return None
        if not isinstance(obj, dict):
            self.fail("bad_json", f"{kind}: expected a JSON object")
            return None
        return obj

    def jsonl(self, kind: str) -> list[dict[str, Any]] | None:
        path = self.artifact(kind)
        if path is None:
            return None
        rows, errors = _read_jsonl(path)
        if errors:
            self.fail("bad_jsonl", f"{kind}: " + "; ".join(errors[:5]))
            return None
        return rows

    def tree(self, kind: str) -> Path | None:
        return self.artifact(kind)

    # -- field validation --------------------------------------------------

    def require(
        self,
        obj: Any,
        key: str,
        kind: str,
        *,
        minimum: int | None = None,
        nonempty: bool = False,
        where: str = "",
    ) -> Any:
        label = f"{where}{key}"
        if not isinstance(obj, dict):
            self.fail("bad_object", f"{label}: parent is not an object")
            return None
        if key not in obj:
            self.fail("missing_field", f"{label}: required field is missing")
            return None
        value = obj[key]
        if not self._valid(value, kind, minimum=minimum, nonempty=nonempty):
            self.fail(
                "bad_field",
                f"{label}: expected {self._describe(kind, minimum, nonempty)}, got {value!r}",
            )
            return None
        return value

    def require_present(self, obj: Any, key: str, *, where: str = "") -> bool:
        label = f"{where}{key}"
        if not isinstance(obj, dict):
            self.fail("bad_object", f"{label}: parent is not an object")
            return False
        if key not in obj:
            self.fail("missing_field", f"{label}: required field is missing")
            return False
        return True

    def require_true(self, obj: Any, key: str, *, where: str = "") -> None:
        value = self.require(obj, key, "bool", where=where)
        if isinstance(value, bool) and not value:
            self.fail("not_true", f"{where}{key}: must be true")

    def require_false(self, obj: Any, key: str, *, where: str = "") -> None:
        value = self.require(obj, key, "bool", where=where)
        if isinstance(value, bool) and value:
            self.fail("not_false", f"{where}{key}: must be false")

    def one_of(self, obj: Any, key: str, allowed: Sequence[str], *, where: str = "") -> Any:
        value = self.require(obj, key, "string", nonempty=True, where=where)
        if value is not None and value not in allowed:
            self.fail("bad_value", f"{where}{key}: {value!r} is not one of {', '.join(allowed)}")
            return None
        return value

    def object_list(self, obj: Any, key: str, *, minimum: int = 0, where: str = "") -> list[dict[str, Any]]:
        value = self.require(obj, key, "list", minimum=minimum, where=where)
        if value is None:
            return []
        for index, item in enumerate(value):
            if not isinstance(item, dict):
                self.fail("bad_field", f"{where}{key}[{index}]: expected an object, got {type(item).__name__}")
                return []
        return value

    @staticmethod
    def _valid(value: Any, kind: str, *, minimum: int | None, nonempty: bool) -> bool:
        if kind == "string":
            return isinstance(value, str) and (not nonempty or bool(value.strip()))
        if kind == "hex64":
            return isinstance(value, str) and bool(HEX64.fullmatch(value))
        if kind == "hex40":
            return isinstance(value, str) and bool(HEX40.fullmatch(value))
        if kind == "hex16":
            return isinstance(value, str) and bool(HEX16.fullmatch(value))
        if kind == "int":
            return _is_int(value) and (minimum is None or value >= minimum)
        if kind == "number":
            return _is_number(value) and (minimum is None or float(value) >= minimum)
        if kind == "bool":
            return isinstance(value, bool)
        if kind == "list":
            return isinstance(value, list) and (minimum is None or len(value) >= minimum)
        if kind == "object":
            return isinstance(value, dict)
        if kind == "vec3":
            return _is_vec3(value)
        if kind == "iso":
            return _is_iso(value)
        raise AssertionError(f"unknown field kind: {kind}")

    @staticmethod
    def _describe(kind: str, minimum: int | None, nonempty: bool) -> str:
        if kind == "string":
            return "a non-empty string" if nonempty else "a string"
        if kind == "hex64":
            return "64 lowercase hex characters"
        if kind == "hex40":
            return "40 lowercase hex characters (a commit)"
        if kind == "hex16":
            return "16 lowercase hex characters"
        if kind == "int":
            return "an integer" + (f" >= {minimum}" if minimum is not None else "")
        if kind == "number":
            return "a finite number" + (f" >= {minimum}" if minimum is not None else "")
        if kind == "bool":
            return "a boolean"
        if kind == "list":
            return "a list" + (f" with >= {minimum} item(s)" if minimum is not None else "")
        if kind == "object":
            return "an object"
        if kind == "vec3":
            return "[x, y, z] of finite numbers"
        if kind == "iso":
            return "an ISO-8601 timestamp"
        return kind


# --------------------------------------------------------------------------- check runners


def _check_fixture_map(ctx: CheckContext) -> None:
    manifest = ctx.json("fixture_manifest")
    if manifest is not None:
        mapo = ctx.require(manifest, "map", "object")
        if mapo is not None:
            ctx.require(mapo, "url", "string", nonempty=True)
            ctx.require(mapo, "sha256", "hex64")
            ctx.require(mapo, "bytes", "int", minimum=1)
            ctx.require(mapo, "mc_version", "string", nonempty=True)
            ctx.require_true(mapo, "immutable")
            ctx.require_true(mapo, "download_verified")
            ctx.require_true(mapo, "bad_hash_rejected")
        mods = ctx.object_list(manifest, "mods", minimum=1)
        for index, mod in enumerate(mods):
            where = f"mods[{index}]."
            ctx.require(mod, "name", "string", nonempty=True, where=where)
            ctx.require(mod, "version", "string", nonempty=True, where=where)
            ctx.require(mod, "sha256", "hex64", where=where)
        world = ctx.require(manifest, "world", "object")
        if world is not None:
            ctx.require(world, "directory", "string", nonempty=True)
            ctx.require(world, "tree_sha256", "hex64")
            ctx.require(world, "files", "int", minimum=1)

    runs = ctx.jsonl("init_runs")
    if runs is None:
        return
    if len(runs) < 3:
        ctx.fail(
            "init_runs_short",
            f"init_runs: {len(runs)} run(s), issue #15 requires >= 3 independent initializations",
        )
    init_ids: list[str] = []
    state_hashes: list[str] = []
    order_hashes: list[str] = []
    signatures: list[tuple[Any, Any, Any]] = []
    ready = 0
    for index, row in enumerate(runs):
        where = f"init_runs[{index}]."
        init_id = ctx.require(row, "init_id", "string", nonempty=True, where=where)
        ctx.require(row, "run_id", "string", nonempty=True, where=where)
        state = ctx.require(row, "state_hash", "hex64", where=where)
        order = ctx.require(row, "order_hash", "hex16", where=where)
        count = ctx.require(row, "entity_count", "int", minimum=1, where=where)
        inventory = ctx.require(row, "inventory_total", "int", minimum=1, where=where)
        early = ctx.require(row, "early_output", "bool", where=where)
        ready_value = ctx.require(row, "ready", "bool", where=where)
        ctx.require(row, "tick", "int", minimum=0, where=where)
        if isinstance(init_id, str):
            init_ids.append(init_id)
        if isinstance(state, str):
            state_hashes.append(state)
        if isinstance(order, str):
            order_hashes.append(order)
        if early is True:
            ctx.fail("early_output", f"{where}early_output is true: the fixture emitted before ready")
        if ready_value is True:
            ready += 1
        signatures.append((state, order, count if count is not None else inventory))
    if len(init_ids) != len(set(init_ids)):
        ctx.fail("init_duplicate", "init_runs: init_id values repeat; runs must be independent")
    if len(set(state_hashes)) > 1:
        ctx.fail("init_state_mismatch", "init_runs: state_hash differs across initializations")
    if len(set(order_hashes)) > 1:
        ctx.fail("init_order_mismatch", "init_runs: order_hash differs across initializations")
    if len(set(signatures)) > 1:
        ctx.fail("init_signature_mismatch", "init_runs: entity count/order state differs across runs")
    if runs and ready != len(runs):
        ctx.fail("init_not_ready", f"init_runs: {ready}/{len(runs)} runs report ready=true")
    ctx.assert_("three independent initializations", len(runs) >= 3, f"{len(runs)} run(s)")
    ctx.assert_("initialization reproducible", len(set(state_hashes)) <= 1 and len(set(order_hashes)) <= 1)

    player = ctx.json("player_identity")
    if player is not None:
        uuid = ctx.require(player, "uuid", "string", nonempty=True)
        ctx.require(player, "name", "string", nonempty=True)
        ctx.require(player, "dimension", "string", nonempty=True)
        ctx.require(player, "pos", "vec3")
        ctx.require(player, "yaw", "number")
        ctx.require(player, "pitch", "number")
        ctx.require(player, "source", "string", nonempty=True)
        ctx.require_true(player, "facing_target")
        vantage = ctx.require(player, "server_vantage_uuid", "string", nonempty=True)
        if isinstance(uuid, str) and isinstance(vantage, str) and uuid != vantage:
            ctx.fail(
                "player_vantage_mismatch",
                "player_identity: server_vantage_uuid differs from the fixture player uuid",
            )

    scan = ctx.json("command_block_scan")
    if scan is not None:
        ctx.require(scan, "method", "string", nonempty=True)
        ctx.require(scan, "world_dirs", "list", minimum=1)
        found = ctx.require(scan, "command_blocks", "int", minimum=0)
        ctx.require_true(scan, "scanned")
        ctx.require_false(scan, "placed_by_init")
        if _is_int(found) and found > 0:
            ctx.fail("command_blocks_present", f"command_block_scan: {found} command block(s) found")

    cleanup = ctx.json("cleanup_rebuild")
    if cleanup is not None:
        ctx.require(cleanup, "steps", "list", minimum=1)
        ctx.require_true(cleanup, "source_world_untouched")
        ctx.require_true(cleanup, "rebuild_reproducible")


def _check_restore_fidelity(ctx: CheckContext) -> None:
    before_dir = ctx.tree("snapshot_before")
    after_dir = ctx.tree("snapshot_after")
    if before_dir is None or after_dir is None:
        return
    try:
        before = fork_verify.load_snapshot(before_dir)
        after = fork_verify.load_snapshot(after_dir)
    except fork_verify.SnapshotError as error:
        ctx.fail("snapshot_unreadable", f"snapshot: {error}")
        return
    problems = fork_verify.validate_snapshot(before) + fork_verify.validate_snapshot(after)
    if problems:
        ctx.fail("snapshot_invalid", "snapshot: " + "; ".join(fork_verify.format_issues(problems)))
        return
    if not before.entities or not after.entities:
        ctx.fail("snapshot_empty", "snapshot: at least one entity is required on both sides")
        return

    order_before = fork_verify.order_hash(before.entities)
    order_after = fork_verify.order_hash(after.entities)
    if order_before != order_after:
        ctx.fail(
            "restore_order_mismatch",
            f"restore: orderHash before={order_before} after={order_after}",
        )
    counts_before = fork_verify.type_counts(before.entities)
    counts_after = fork_verify.type_counts(after.entities)
    if counts_before != counts_after:
        ctx.fail("restore_count_mismatch", "restore: per-type entity counts differ after restore")
    ctx.assert_("restore order reproduced", order_before == order_after, order_before)

    # Fixture-relevant full state: same UUID sequence, same NBT, same pos/vel.
    if order_before == order_after:
        mismatch = 0
        for left, right in zip(before.entities, after.entities):
            if left.nbt != right.nbt:
                mismatch += 1
                if mismatch <= 3:
                    ctx.fail(
                        "restore_nbt_mismatch",
                        f"restore: {left.uuid} NBT differs (inventory/components not faithful)",
                    )
            if left.pos is None or right.pos is None or any(
                abs(a - b) > 1e-6 for a, b in zip(left.pos, right.pos)
            ):
                mismatch += 1
                if mismatch <= 3:
                    ctx.fail("restore_pos_mismatch", f"restore: {left.uuid} position differs")
            if left.vel is None or right.vel is None or any(
                abs(a - b) > 1e-6 for a, b in zip(left.vel, right.vel)
            ):
                mismatch += 1
                if mismatch <= 3:
                    ctx.fail("restore_vel_mismatch", f"restore: {left.uuid} velocity differs")
        ctx.assert_("restore full state equal", mismatch == 0, f"{mismatch} mismatch(es)")

    record = ctx.json("restore_record")
    if record is not None:
        endpoint = ctx.require(record, "endpoint", "object")
        if endpoint is not None:
            ctx.require(endpoint, "source", "string", nonempty=True)
            ctx.require(endpoint, "target", "string", nonempty=True)
            ctx.require_true(endpoint, "target_resolved")
            ctx.require_true(endpoint, "wrong_target_rejected")
        ctx.require(record, "dimension", "string", nonempty=True)
        ctx.require_true(record, "chunks_loaded")
        ctx.require_true(record, "tick_controlled")
        initial = ctx.require(record, "duplicates_pre_existing", "int", minimum=0)
        if _is_int(initial) and initial != 0:
            ctx.fail("restore_duplicates", "restore_record: pre-existing duplicate carts were present")
        issued = ctx.require(record, "commands_issued", "int", minimum=1)
        failed = ctx.require(record, "commands_failed", "int", minimum=0)
        if _is_int(issued) and _is_int(failed) and failed > 0:
            ctx.fail("restore_command_failure", f"restore_record: {failed} command(s) failed")
        ctx.require_false(record, "partial_failure")
        ctx.require_true(record, "pause_state_preserved")
        ctx.require_true(record, "issued_is_not_success")

    unchanged = ctx.json("source_unchanged")
    if unchanged is not None:
        before_hash = ctx.require(unchanged, "before_tree_sha256", "hex64")
        after_hash = ctx.require(unchanged, "after_tree_sha256", "hex64")
        ctx.require_true(unchanged, "unchanged")
        if isinstance(before_hash, str) and isinstance(after_hash, str) and before_hash != after_hash:
            ctx.fail("source_world_changed", "source_unchanged: before/after tree hashes differ")

    cases = ctx.jsonl("failure_cases")
    if cases is None:
        return
    seen = set()
    for index, row in enumerate(cases):
        where = f"failure_cases[{index}]."
        case = ctx.require(row, "case", "string", nonempty=True, where=where)
        ctx.require(row, "injected", "string", nonempty=True, where=where)
        ctx.require(row, "expected", "string", nonempty=True, where=where)
        ctx.require(row, "observed", "string", nonempty=True, where=where)
        ctx.require_true(row, "passed", where=where)
        if isinstance(case, str):
            seen.add(case)
    missing = sorted(set(FAILURE_CASES) - seen)
    if missing:
        ctx.fail("failure_cases_incomplete", f"failure_cases: missing case(s): {', '.join(missing)}")
    ctx.assert_("restore failure cases covered", not missing, f"{len(seen)} case(s)")


def _check_player_context(ctx: CheckContext) -> None:
    rows = ctx.jsonl("identity_records")
    if rows is not None:
        seen: dict[str, dict[str, Any]] = {}
        for index, row in enumerate(rows):
            where = f"identity_records[{index}]."
            case = ctx.require(row, "case", "string", nonempty=True, where=where)
            ctx.require(row, "uuid", "string", nonempty=True, where=where)
            ctx.require(row, "viewed_uuid", "string", nonempty=True, where=where)
            ctx.require(row, "dimension", "string", nonempty=True, where=where)
            ctx.require(row, "pos", "vec3", where=where)
            ctx.require(row, "yaw", "number", where=where)
            ctx.require(row, "pitch", "number", where=where)
            ctx.require(row, "task_entry", "string", nonempty=True, where=where)
            ctx.one_of(row, "channel", ("mcp", "cli"), where=where)
            accepted = ctx.require(row, "accepted", "bool", where=where)
            if isinstance(case, str):
                seen[case] = row
            if case == "task_bind":
                ctx.require_true(row, "accepted", where=where)
                if row.get("uuid") != row.get("viewed_uuid"):
                    ctx.fail(
                        "player_context_crosstalk",
                        f"{where}task_bind viewed_uuid differs from the bound task uuid",
                    )
            elif case in ("hit", "miss"):
                ctx.require_true(row, "accepted", where=where)
                if row.get("uuid") != row.get("viewed_uuid"):
                    ctx.fail("player_context_crosstalk", f"{where}{case} viewed the wrong player")
            elif case == "two_players":
                ctx.require_true(row, "accepted", where=where)
                other = ctx.require(row, "other_uuid", "string", nonempty=True, where=where)
                if row.get("uuid") == other:
                    ctx.fail("player_context_crosstalk", f"{where}two_players other_uuid equals uuid")
                if row.get("uuid") != row.get("viewed_uuid"):
                    ctx.fail("player_context_crosstalk", f"{where}two_players viewed the wrong player")
            elif case == "unknown_identity":
                if accepted is True:
                    ctx.fail("player_context_unknown", f"{where}unknown identity was accepted")
                ctx.require(row, "rejected_reason", "string", nonempty=True, where=where)
        missing = sorted(set(IDENTITY_CASES) - set(seen))
        if missing:
            ctx.fail("player_context_incomplete", f"identity_records: missing case(s): {', '.join(missing)}")
        ctx.assert_("identity cases covered", not missing, f"{len(seen)} case(s)")

    contract = ctx.json("entry_contract")
    if contract is not None:
        ctx.one_of(contract, "mode", ("external_task", "manual_external"))
        ctx.require(contract, "fields", "list", minimum=1)
        ctx.require(contract, "native_chat_verified", "bool")
        ctx.require(contract, "unsupported_entries", "list")

    pins = ctx.json("version_pins")
    if pins is not None:
        for name in ("interface_mod", "bridge"):
            where = f"{name}."
            block = ctx.require(pins, name, "object", where=where)
            if block is None:
                continue
            ctx.require(block, "repo", "string", nonempty=True, where=where)
            ctx.require(block, "commit", "hex40", where=where)
            ctx.require_true(block, "tested", where=where)


def _check_agent_dev_capability(ctx: CheckContext) -> None:
    environment = ctx.json("tool_environment")
    if environment is not None:
        ctx.require(environment, "harness", "string", nonempty=True)
        ctx.require(environment, "model", "string", nonempty=True)
        ctx.require(environment, "docs_visible", "list", minimum=1)
        tools = ctx.require(environment, "tools", "object")
        if tools is not None:
            for name in (
                "terminal",
                "file",
                "filesystem_write",
                "source_access",
                "build",
                "install",
                "mcp_or_cli",
                "lab_manage",
            ):
                ctx.require_true(tools, name, where="tools.")

    jar = ctx.json("jar_update")
    if jar is not None:
        ctx.one_of(jar, "case", ("same_size_different_content",))
        old = ctx.require(jar, "old_sha256", "hex64")
        new = ctx.require(jar, "new_sha256", "hex64")
        loaded = ctx.require(jar, "loaded_sha256", "hex64")
        ctx.require(jar, "bytes", "int", minimum=1)
        ctx.require(jar, "runtime_evidence", "string", nonempty=True)
        if isinstance(old, str) and isinstance(new, str) and old == new:
            ctx.fail("jar_update_same_hash", "jar_update: old and new hashes are equal")
        if isinstance(new, str) and isinstance(loaded, str) and loaded != new:
            ctx.fail("jar_update_stale", "jar_update: runtime loaded a different jar than the new build")
        ctx.assert_("same-size jar update detected", old != new and loaded == new)

    smoke = ctx.json("smoke_mod")
    if smoke is not None:
        ctx.require(smoke, "mod_id", "string", nonempty=True)
        ctx.require(smoke, "version", "string", nonempty=True)
        built = ctx.require(smoke, "built_sha256", "hex64")
        deployed = ctx.require(smoke, "deployed_sha256", "hex64")
        ctx.require(smoke, "server_log_ref", "string", nonempty=True)
        ctx.require(smoke, "sample_output_ref", "string", nonempty=True)
        ctx.require_true(smoke, "build_errors_detected")
        ctx.require_true(smoke, "load_failure_detected")
        ctx.require_true(smoke, "missing_dependency_detected")
        ctx.require_true(smoke, "memory_state_rebuilt_after_restart")
        ctx.require(smoke, "restart_evidence_ref", "string", nonempty=True)
        ctx.require_true(smoke, "no_rom_logic")
        if isinstance(built, str) and isinstance(deployed, str) and built != deployed:
            ctx.fail("smoke_mod_deploy_mismatch", "smoke_mod: deployed bytes differ from the build")

    rows = ctx.jsonl("instance_isolation")
    if rows is None:
        return
    instance_ids: list[str] = []
    world_dirs: list[str] = []
    ports: list[int] = []
    roles: set[str] = set()
    for index, row in enumerate(rows):
        where = f"instance_isolation[{index}]."
        instance = ctx.require(row, "instance_id", "string", nonempty=True, where=where)
        role = ctx.one_of(row, "role", ("source_audit", "experiment"), where=where)
        world = ctx.require(row, "world_dir", "string", nonempty=True, where=where)
        rcon = ctx.require(row, "rcon_port", "int", minimum=1, where=where)
        bridge = ctx.require(row, "bridge_port", "int", minimum=1, where=where)
        ctx.require_true(row, "restarted", where=where)
        ctx.require_true(row, "resolves_correct_world", where=where)
        ctx.require_false(row, "conflicting_instance", where=where)
        if isinstance(instance, str):
            instance_ids.append(instance)
        if isinstance(world, str):
            world_dirs.append(world)
        if isinstance(role, str):
            roles.add(role)
        if _is_int(rcon):
            ports.append(rcon)
        if _is_int(bridge):
            ports.append(bridge)
    if len(rows) < 2:
        ctx.fail("instance_isolation_short", "instance_isolation: at least two instances are required")
    if len(instance_ids) != len(set(instance_ids)):
        ctx.fail("instance_duplicate", "instance_isolation: instance_id values repeat")
    if len(world_dirs) != len(set(world_dirs)):
        ctx.fail("instance_world_duplicate", "instance_isolation: world_dir values repeat")
    if len(ports) != len(set(ports)):
        ctx.fail("instance_port_duplicate", "instance_isolation: a port is used by two instances")
    if {"source_audit", "experiment"} - roles:
        ctx.fail("instance_roles", "instance_isolation: source_audit and experiment roles are required")
    ctx.assert_("instances isolated", len(world_dirs) == len(set(world_dirs)) and len(ports) == len(set(ports)))


def _identity_of(row: dict[str, Any]) -> tuple[Any, Any, Any]:
    """The full provenance key an audit event must be joined on."""
    return (row.get("run_id"), row.get("instance_id"), row.get("dimension"))


def _validate_audit_provenance(
    ctx: CheckContext,
    events: Sequence[dict[str, Any]],
    run_id: Any,
    instance_dimensions: dict[str, str],
) -> list[dict[str, Any]]:
    """Bind audit events to the declared run/instance/dimension and order them.

    Mixing evidence from another run, instance or dimension is an ordinary
    stale-evidence failure: the events must not be joined at all.  Event ids
    must be unique, and ``(tick, seq)`` must be strictly increasing per full
    identity, so a repeated or colliding sequence cannot silently establish an
    ordering.  Returns the ``phase == "agent"`` subset for chain checks.
    """
    agent_events: list[dict[str, Any]] = []
    if not isinstance(run_id, str) or not run_id:
        ctx.fail("audit_provenance", "bundle.run.run_id is not declared; audit events cannot be bound")
        return [row for row in events if row.get("phase") == "agent"]
    if not instance_dimensions:
        ctx.fail(
            "audit_provenance",
            "bundle.run.instances declares no instance_id/dimension pair; audit events cannot be bound",
        )
        return [row for row in events if row.get("phase") == "agent"]

    seen_ids: set[str] = set()
    last_order: dict[tuple[Any, Any, Any], tuple[int, int]] = {}
    for index, row in enumerate(events):
        where = f"audit_events[{index}]."
        event_id = row.get("event_id")
        if isinstance(event_id, str) and event_id:
            if event_id in seen_ids:
                ctx.fail("audit_event_id_duplicate", f"{where}event_id {event_id!r} repeats")
            seen_ids.add(event_id)
        row_run = row.get("run_id")
        instance_id = row.get("instance_id")
        dimension = row.get("dimension")
        if row_run != run_id:
            ctx.fail(
                "audit_provenance",
                f"{where}run_id {row_run!r} is not the declared run {run_id!r}",
            )
        elif not isinstance(instance_id, str) or instance_id not in instance_dimensions:
            ctx.fail(
                "audit_provenance",
                f"{where}instance_id {instance_id!r} is not declared in bundle.run.instances",
            )
        elif dimension != instance_dimensions[instance_id]:
            ctx.fail(
                "audit_provenance",
                f"{where}dimension {dimension!r} does not match the declared "
                f"{instance_id!r}/{instance_dimensions[instance_id]!r}",
            )
        tick = row.get("tick")
        seq = row.get("seq")
        if (
            _is_int(tick)
            and _is_int(seq)
            and isinstance(row_run, str)
            and isinstance(instance_id, str)
            and isinstance(dimension, str)
        ):
            key = (row_run, instance_id, dimension)
            previous = last_order.get(key)
            if previous is not None and (tick, seq) <= previous:
                ctx.fail(
                    "audit_order",
                    f"{where}non-strict tick/seq {tick}/{seq} after "
                    f"{previous[0]}/{previous[1]} for {key[0]}/{key[1]}/{key[2]}",
                )
            last_order[key] = (tick, seq)
        if row.get("phase") == "agent":
            agent_events.append(row)
    return agent_events


def _check_independent_test_mod(ctx: CheckContext) -> None:
    manifest = ctx.json("test_mod_manifest")
    if manifest is not None:
        ctx.require(manifest, "mod_id", "string", nonempty=True)
        ctx.require(manifest, "version", "string", nonempty=True)
        ctx.require(manifest, "sha256", "hex64")
        ctx.require_true(manifest, "read_only")
        ctx.require(manifest, "hook_overhead_ms", "number", minimum=0)
        ctx.require_true(manifest, "fixture_behavior_unchanged")
        ctx.require_true(manifest, "agent_mod_coexists")
        ctx.require_true(manifest, "no_command_blocks")
        loaded = ctx.require(manifest, "loaded_in", "list", minimum=2)
        if loaded is not None and {"source_audit", "experiment"} - set(loaded):
            ctx.fail("test_mod_coverage", "test_mod_manifest: loaded_in must cover source_audit and experiment")

    events = ctx.jsonl("audit_events")
    agent_events: list[dict[str, Any]] = []
    if events is not None:
        seen_events: set[str] = set()
        for index, row in enumerate(events):
            where = f"audit_events[{index}]."
            ctx.require(row, "event_id", "string", nonempty=True, where=where)
            ctx.require(row, "run_id", "string", nonempty=True, where=where)
            ctx.require(row, "instance_id", "string", nonempty=True, where=where)
            ctx.require(row, "dimension", "string", nonempty=True, where=where)
            ctx.require(row, "tick", "int", minimum=0, where=where)
            ctx.require(row, "seq", "int", minimum=0, where=where)
            event = ctx.require(row, "event", "string", nonempty=True, where=where)
            ctx.one_of(row, "phase", ("init", "agent", "restore"), where=where)
            if isinstance(event, str):
                seen_events.add(event)
        agent_events = _validate_audit_provenance(
            ctx, events, ctx.bundle.run_id, ctx.bundle.instance_dimensions
        )
        required = {"input_attempt", "input_processed", "cart_emitted", "cart_removed"}
        missing = sorted(required - seen_events)
        if missing:
            ctx.fail("audit_events_incomplete", f"audit_events: missing event type(s): {', '.join(missing)}")

        attempts = [row for row in agent_events if row.get("event") == "input_attempt"]
        processed = [row for row in agent_events if row.get("event") == "input_processed"]
        emitted = [row for row in agent_events if row.get("event") == "cart_emitted"]
        removed = [row for row in agent_events if row.get("event") == "cart_removed"]
        if not attempts or not processed:
            ctx.fail("audit_chain", "audit_events: inputs were never both attempted and processed")
        if not emitted:
            ctx.fail("audit_chain", "audit_events: no cart_emitted event captured")
        for row in emitted:
            if row.get("captured_before_removal") is not True:
                ctx.fail(
                    "audit_transient",
                    f"audit_events: cart {row.get('cart_uuid')!r} was not persisted before removal",
                )
        if not removed:
            ctx.fail("audit_chain", "audit_events: no cart_removed event captured")
        for row in removed:
            if not isinstance(row.get("removal_reason"), str) or not row.get("removal_reason"):
                ctx.fail("audit_removal_reason", "audit_events: cart_removed needs a removal_reason")

        def order_of(row: dict[str, Any]) -> tuple[int, int]:
            return (int(row.get("tick", -1)), int(row.get("seq", -1)))

        # The chain is only meaningful on the full run/instance/dimension identity.
        matched = 0
        for row in processed:
            for attempt in attempts:
                if _identity_of(attempt) != _identity_of(row):
                    continue
                if order_of(attempt) >= order_of(row):
                    continue
                actor = attempt.get("actor_uuid")
                row_actor = row.get("actor_uuid")
                if isinstance(actor, str) and isinstance(row_actor, str) and actor != row_actor:
                    ctx.fail(
                        "audit_chain",
                        f"audit_events: input_processed actor {row_actor!r} differs from its "
                        f"input_attempt actor {actor!r} on {_identity_of(row)}",
                    )
                    continue
                matched += 1
                break
        if processed and matched == 0:
            ctx.fail(
                "audit_chain",
                "audit_events: no input_processed follows an input_attempt on the same "
                "run/instance/dimension",
            )
        matched_emitted = 0
        for row in emitted:
            if any(
                _identity_of(processed_row) == _identity_of(row)
                and order_of(processed_row) <= order_of(row)
                for processed_row in processed
            ):
                matched_emitted += 1
        if emitted and matched_emitted != len(emitted):
            ctx.fail(
                "audit_chain",
                "audit_events: a cart_emitted has no prior input_processed on the same "
                "run/instance/dimension",
            )
        emitted_by_cart: dict[tuple[Any, Any, Any, Any], list[dict[str, Any]]] = {}
        for row in emitted:
            emitted_by_cart.setdefault(_identity_of(row) + (row.get("cart_uuid"),), []).append(row)
        matched_removed = 0
        for row in removed:
            candidates = emitted_by_cart.get(_identity_of(row) + (row.get("cart_uuid"),), [])
            if any(order_of(candidate) <= order_of(row) for candidate in candidates):
                matched_removed += 1
            else:
                ctx.fail(
                    "audit_removal_chain",
                    f"audit_events: cart_removed {row.get('cart_uuid')!r} does not follow a "
                    "cart_emitted on the same run/instance/dimension",
                )
        ctx.assert_(
            "input -> processing -> output chain",
            matched > 0 and emitted and matched_emitted == len(emitted) and matched_removed == len(removed),
        )

    negatives = ctx.jsonl("negative_cases")
    if negatives is not None:
        seen: set[str] = set()
        for index, row in enumerate(negatives):
            where = f"negative_cases[{index}]."
            case = ctx.require(row, "case", "string", nonempty=True, where=where)
            ctx.require(row, "attempted", "bool", where=where)
            processed = ctx.require(row, "processed", "int", minimum=0, where=where)
            ctx.require(row, "evidence_ref", "string", nonempty=True, where=where)
            if _is_int(processed) and processed != 0:
                ctx.fail(
                    "negative_case_processed",
                    f"{where}{case}: {processed} fake operation(s) were counted as real processing",
                )
            if isinstance(case, str):
                seen.add(case)
        missing = sorted(set(NEGATIVE_CASES) - seen)
        if missing:
            ctx.fail("negative_cases_incomplete", f"negative_cases: missing case(s): {', '.join(missing)}")
        if events:
            for row in events:
                detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
                if row.get("event") == "input_processed" and detail.get("negative_case"):
                    ctx.fail(
                        "negative_case_processed",
                        f"audit_events: input_processed attributed to negative case "
                        f"{row['detail']['negative_case']!r}",
                    )
        ctx.assert_("negative cases rejected", not missing)

    lifecycle = ctx.json("audit_lifecycle")
    if lifecycle is not None:
        states = ctx.require(lifecycle, "states", "list", minimum=1)
        required_states = {"ready", "init", "experiment_start", "experiment_end", "flush"}
        if states is not None and required_states - set(states):
            ctx.fail(
                "audit_lifecycle_incomplete",
                f"audit_lifecycle: missing state(s): {', '.join(sorted(required_states - set(states)))}",
            )
        ctx.one_of(lifecycle, "missing_log_status", ("error",))
        ctx.one_of(lifecycle, "overflow_status", ("error",))
        ctx.require_true(lifecycle, "per_instance_files")
        ctx.require_false(lifecycle, "ring_buffer_reliance")


def _check_trace_persistence(ctx: CheckContext) -> None:
    trace = ctx.jsonl("tool_trace")
    call_ids: list[str] | None = None
    if trace is not None:
        call_ids = []
        errors_kept = 0
        declared_run = ctx.bundle.run_id
        declared_instances = ctx.bundle.instance_dimensions
        if not isinstance(declared_run, str) or not declared_run:
            ctx.fail("trace_provenance", "tool_trace: bundle.run.run_id is not declared")
        if not declared_instances:
            ctx.fail(
                "trace_provenance",
                "tool_trace: bundle.run.instances declares no instance_id/dimension pair",
            )
        for index, row in enumerate(trace):
            where = f"tool_trace[{index}]."
            call_id = ctx.require(row, "call_id", "string", nonempty=True, where=where)
            row_run = ctx.require(row, "run_id", "string", nonempty=True, where=where)
            row_instance = ctx.require(row, "instance_id", "string", nonempty=True, where=where)
            ctx.require(row, "tool", "string", nonempty=True, where=where)
            ctx.require(row, "args", "object", where=where)
            ctx.require_present(row, "result", where=where)
            ctx.require_present(row, "error", where=where)
            started = ctx.require(row, "started_at", "iso", where=where)
            ended = ctx.require(row, "ended_at", "iso", where=where)
            if isinstance(declared_run, str) and declared_run and isinstance(row_run, str) and row_run != declared_run:
                ctx.fail(
                    "trace_provenance",
                    f"{where}run_id {row_run!r} is not the declared run {declared_run!r}",
                )
            if (
                declared_instances
                and isinstance(row_instance, str)
                and row_instance not in declared_instances
            ):
                ctx.fail(
                    "trace_provenance",
                    f"{where}instance_id {row_instance!r} is not declared in bundle.run.instances",
                )
            if isinstance(call_id, str):
                call_ids.append(call_id)
            if isinstance(row.get("error"), str) and row["error"]:
                errors_kept += 1
            if _is_iso(started) and _is_iso(ended):
                left = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
                right = datetime.fromisoformat(str(ended).replace("Z", "+00:00"))
                if right < left:
                    ctx.fail("trace_time", f"{where}ended_at is before started_at")
        if len(call_ids) != len(set(call_ids)):
            ctx.fail("trace_call_duplicate", "tool_trace: call_id values repeat")
        categories = ctx.bundle.manifest.get("run", {})
        mapping = categories.get("tool_category_map") if isinstance(categories, dict) else None
        if not isinstance(mapping, dict):
            mapping = DEFAULT_TOOL_CATEGORIES
        names = " ".join(str(row.get("tool", "")).lower() for row in trace)
        missing = [
            category
            for category, patterns in mapping.items()
            if not any(str(pattern).lower() in names for pattern in patterns)
        ]
        if missing:
            ctx.fail(
                "trace_categories",
                f"tool_trace: no call covers tool category/categories: {', '.join(missing)}",
            )
        ctx.assert_("tool categories covered", not missing, f"errors preserved: {errors_kept}")

    audit_events: list[dict[str, Any]] | None = None
    audit_ref_path = ctx.bundle.artifact_ref("independent_test_mod", "audit_events")
    if audit_ref_path is None:
        ctx.blocked("trace_audit_missing", "audit_events: not declared; tool/game joins cannot be verified")
    else:
        resolved = _resolve_rel(ctx.bundle.root, audit_ref_path)
        if resolved is None or not resolved.is_file():
            ctx.blocked("trace_audit_missing", f"audit_events: {audit_ref_path} is not available")
        else:
            rows, errors = _read_jsonl(resolved)
            if errors:
                ctx.fail("trace_audit_bad", "audit_events: " + "; ".join(errors[:3]))
            else:
                audit_events = rows

    join = ctx.json("trace_join")
    if join is not None and call_ids is not None:
        joins = ctx.object_list(join, "joins", minimum=1)
        known = set(call_ids)
        for index, item in enumerate(joins):
            where = f"joins[{index}]."
            call_id = ctx.require(item, "call_id", "string", nonempty=True, where=where)
            ref = ctx.require(item, "audit_ref", "object", where=where)
            ctx.require_true(item, "verified", where=where)
            if isinstance(call_id, str) and call_id not in known:
                ctx.fail("trace_join_unknown", f"{where}call_id {call_id!r} is not in tool_trace")
            if ref is not None:
                ctx.require(ref, "run_id", "string", nonempty=True, where=where)
                ctx.require(ref, "instance_id", "string", nonempty=True, where=where)
                ctx.require(ref, "dimension", "string", nonempty=True, where=where)
                ctx.require(ref, "event_id", "string", nonempty=True, where=where)
                ctx.require(ref, "tick", "int", minimum=0, where=where)
                if audit_events is not None:
                    event_id = ref.get("event_id")
                    matches = [row for row in audit_events if row.get("event_id") == event_id]
                    if len(matches) != 1:
                        ctx.fail(
                            "trace_join_mismatch",
                            f"{where}event_id {event_id!r} matches {len(matches)} audit event(s)",
                        )
                    else:
                        matched = matches[0]
                        for field in ("run_id", "instance_id", "dimension", "tick"):
                            if matched.get(field) != ref.get(field):
                                ctx.fail(
                                    "trace_join_mismatch",
                                    f"{where}audit_ref.{field}={ref.get(field)!r} but the audit "
                                    f"event has {matched.get(field)!r}",
                                )
        unmatched_tools = ctx.require(join, "unmatched_tool_calls", "int", minimum=0)
        unmatched_events = ctx.require(join, "unmatched_agent_events", "int", minimum=0)
        if _is_int(unmatched_events) and unmatched_events != 0:
            ctx.fail(
                "trace_join_unmatched",
                f"trace_join: {unmatched_events} agent-side event(s) have no tool call",
            )
        ctx.assert_("tool trace joins game events", unmatched_events == 0)

    missing_log = ctx.jsonl("missing_log_detection")
    if missing_log is not None:
        seen: set[str] = set()
        for index, row in enumerate(missing_log):
            where = f"missing_log_detection[{index}]."
            case = ctx.require(row, "case", "string", nonempty=True, where=where)
            ctx.require_true(row, "detected", where=where)
            ctx.require_true(row, "exit_nonzero", where=where)
            ctx.require(row, "message_ref", "string", nonempty=True, where=where)
            if isinstance(case, str):
                seen.add(case)
        required = {"trace_missing", "audit_missing"}
        if required - seen:
            ctx.fail(
                "missing_log_incomplete",
                f"missing_log_detection: missing case(s): {', '.join(sorted(required - seen))}",
            )


def _check_smoke_fixture_validity(ctx: CheckContext) -> None:
    smoke = ctx.json("smoke_report")
    if smoke is not None:
        suites = ctx.object_list(smoke, "suites", minimum=1)
        seen: set[str] = set()
        for index, suite in enumerate(suites):
            where = f"suites[{index}]."
            name = ctx.require(suite, "name", "string", nonempty=True, where=where)
            ctx.require(suite, "command", "string", nonempty=True, where=where)
            status = ctx.one_of(suite, "status", ("pass",), where=where)
            ctx.require(suite, "checks", "int", minimum=1, where=where)
            ctx.require(suite, "log_ref", "string", nonempty=True, where=where)
            if isinstance(name, str):
                seen.add(name)
            if status not in (None, "pass"):
                ctx.fail("smoke_suite_failed", f"{where}{name}: status is not pass")
        missing = sorted(set(SMOKE_SUITES) - seen)
        if missing:
            ctx.fail("smoke_suites_incomplete", f"smoke_report: missing suite(s): {', '.join(missing)}")

    validity = ctx.json("fixture_validity")
    if validity is not None:
        ctx.require(validity, "input_semantics", "string", nonempty=True)
        ctx.require(validity, "stack_positions", "list", minimum=1)
        boundary = ctx.require(validity, "output_boundary", "object")
        if boundary is not None and not boundary:
            ctx.fail("bad_field", "fixture_validity.output_boundary: must not be empty")
        ctx.require(validity, "void_window_ticks", "int", minimum=1)
        ctx.require(validity, "end_condition", "string", nonempty=True)
        ctx.require(validity, "timeout_s", "int", minimum=1)
        ctx.require(validity, "hook_overhead_ms", "number", minimum=0)
        ctx.require_true(validity, "with_mod_without_mod_consistent")

    lock = ctx.json("version_lock")
    if lock is not None:
        components = ctx.object_list(lock, "components", minimum=1)
        by_name: dict[str, dict[str, Any]] = {}
        for index, component in enumerate(components):
            where = f"components[{index}]."
            name = ctx.require(component, "name", "string", nonempty=True, where=where)
            ctx.require(component, "version", "string", nonempty=True, where=where)
            if isinstance(name, str):
                by_name[name] = component
        for name, requirement in VERSION_LOCK_REQUIRED.items():
            component = by_name.get(name)
            if component is None:
                ctx.fail("version_lock_component", f"version_lock: component {name!r} is missing")
                continue
            if requirement == "commit":
                ctx.require(component, "commit", "hex40", where=f"{name}.")
            elif requirement == "sha256":
                ctx.require(component, "sha256", "hex64", where=f"{name}.")
        ctx.assert_("version lock covers components", not (set(VERSION_LOCK_REQUIRED) - set(by_name)))

    index = ctx.json("evidence_index")
    if index is not None:
        entries = ctx.object_list(index, "entries", minimum=1)
        ctx.require(index, "tool", "string", nonempty=True)
        for index_number, entry in enumerate(entries):
            where = f"entries[{index_number}]."
            ctx.require(entry, "path", "string", nonempty=True, where=where)


def _run_evidence_integrity(
    bundle: Bundle,
    *,
    source_world: str | None,
    skip_source_rehash: bool,
    port_ranges: list[str] | None,
    limitations: list[str],
) -> CheckResult:
    spec = CHECK_SPEC_BY_ID["evidence_integrity"]
    ctx = CheckContext(bundle, spec)
    if bundle.parse_problem is not None:
        level, code, message = (
            bundle.parse_problem.level,
            bundle.parse_problem.code,
            bundle.parse_problem.message,
        )
        ctx.problems.append(Problem(level, code, message))
        return ctx.result()

    manifest = bundle.manifest
    ctx.require(manifest, "schema_version", "int", minimum=1)
    schema_version = manifest.get("schema_version")
    if _is_int(schema_version) and schema_version != SCHEMA_VERSION:
        ctx.fail("schema_version", f"bundle: schema_version={schema_version}, expected {SCHEMA_VERSION}")
    kind = ctx.require(manifest, "kind", "string", nonempty=True)
    if isinstance(kind, str) and kind != BUNDLE_KIND:
        ctx.fail("bundle_kind", f"bundle: kind={kind!r}, expected {BUNDLE_KIND!r}")
    origin = ctx.require(manifest, "origin", "string", nonempty=True)
    if origin != "live":
        ctx.blocked(
            "origin_not_live",
            f"bundle: origin={origin!r}; only live evidence may pass the stage-one gate",
        )
        return ctx.result()
    ctx.require(manifest, "generated_at", "iso")
    run = ctx.require(manifest, "run", "object")
    if run is not None:
        run_id = ctx.require(run, "run_id", "string", nonempty=True)
        issue = ctx.require(run, "issue", "string", nonempty=True)
        if isinstance(issue, str) and issue != ISSUE:
            ctx.fail("run_issue", f"run.issue={issue!r}, expected {ISSUE!r}")
        instances = ctx.object_list(run, "instances", minimum=2)
        ids: list[str] = []
        ports: list[int] = []
        roles: set[str] = set()
        declared_dimensions: dict[str, str] = {}
        for index, instance in enumerate(instances):
            where = f"run.instances[{index}]."
            instance_id = ctx.require(instance, "instance_id", "string", nonempty=True, where=where)
            role = ctx.one_of(instance, "role", ("source_audit", "experiment"), where=where)
            ctx.require(instance, "world_dir", "string", nonempty=True, where=where)
            dimension = ctx.require(instance, "dimension", "string", nonempty=True, where=where)
            rcon = ctx.require(instance, "rcon_port", "int", minimum=1, where=where)
            bridge = ctx.require(instance, "bridge_port", "int", minimum=1, where=where)
            if isinstance(instance_id, str):
                ids.append(instance_id)
                if isinstance(dimension, str) and dimension:
                    declared_dimensions[instance_id] = dimension
            if isinstance(role, str):
                roles.add(role)
            if _is_int(rcon):
                ports.append(rcon)
            if _is_int(bridge):
                ports.append(bridge)
        if len(ids) != len(set(ids)):
            ctx.fail("instance_duplicate", "run.instances: instance_id values repeat")
        if len(ports) != len(set(ports)):
            ctx.fail("instance_ports", "run.instances: a port is used twice")
        if {"source_audit", "experiment"} - roles:
            ctx.fail("instance_roles", "run.instances: source_audit and experiment are required")
        ranges = port_ranges or bundle.allowed_port_ranges or [DEFAULT_PORT_RANGE]
        parsed_ranges = _parse_port_ranges(ranges)
        if parsed_ranges is None:
            ctx.fail("port_range", f"allowed_port_ranges: cannot parse {ranges!r} as LOW-HIGH")
        else:
            for port in ports:
                if not any(low <= port <= high for low, high in parsed_ranges):
                    ctx.fail(
                        "instance_ports",
                        f"run.instances: port {port} is outside {', '.join(ranges)}",
                    )
        ctx.assert_("integration instances isolated", len(ports) == len(set(ports)))

        # Audit events must belong to this run's declared instances/dimensions,
        # even before any tool/game join is checked.
        audit_ref = bundle.artifact_ref("independent_test_mod", "audit_events")
        audit_path = _resolve_rel(bundle.root, audit_ref) if audit_ref else None
        if audit_path is not None and audit_path.is_file() and isinstance(run_id, str):
            audit_rows, audit_errors = _read_jsonl(audit_path)
            if audit_errors:
                ctx.fail("audit_unreadable", "audit_events: " + "; ".join(audit_errors[:3]))
            else:
                _validate_audit_provenance(ctx, audit_rows, run_id, declared_dimensions)

    world = ctx.require(manifest.get("run", {}), "source_world", "object") if isinstance(manifest.get("run"), dict) else None
    recorded_after: str | None = None
    if world is not None:
        ctx.require(world, "label", "string", nonempty=True)
        ctx.require(world, "path", "string", nonempty=True)
        before = ctx.require(world, "before_tree_sha256", "hex64")
        after = ctx.require(world, "after_tree_sha256", "hex64")
        if isinstance(before, str) and isinstance(after, str):
            if before != after:
                ctx.fail("source_world_changed", "run.source_world: before/after tree hashes differ")
            recorded_after = after

    checks = bundle.manifest.get("checks")
    if not isinstance(checks, dict):
        ctx.fail("bundle_checks", "bundle.checks: expected an object")
    else:
        known = set(CHECK_SPEC_BY_ID)
        for key in checks:
            if key not in known:
                ctx.fail("check_unknown", f"bundle.checks: unknown check id {key!r}")
        for check_id, spec_ in CHECK_SPEC_BY_ID.items():
            if check_id == "evidence_integrity":
                continue
            if check_id not in checks:
                ctx.blocked("check_missing", f"bundle.checks: {check_id} is not declared")
                continue
            entry = checks[check_id]
            if not isinstance(entry, dict):
                ctx.fail("check_entry", f"bundle.checks.{check_id}: expected an object")
                continue
            evidence = entry.get("evidence")
            if not isinstance(evidence, dict):
                ctx.fail("check_entry", f"bundle.checks.{check_id}.evidence: expected an object")
                continue
            for kind in spec_.artifacts:
                if kind not in evidence:
                    ctx.blocked(
                        "evidence_not_declared",
                        f"bundle.checks.{check_id}.evidence: '{kind}' is not declared",
                    )
                elif not isinstance(evidence[kind], str) or not evidence[kind]:
                    ctx.fail(
                        "artifact_path",
                        f"bundle.checks.{check_id}.evidence.{kind}: expected a non-empty relative path",
                    )
            for kind in evidence:
                if kind not in spec_.artifacts:
                    ctx.fail(
                        "evidence_kind_unknown",
                        f"bundle.checks.{check_id}.evidence: unknown artifact kind {kind!r}",
                    )

    # Cross-check the recorded source-world facts against the restore artifact.
    source_artifact = bundle.artifact_ref("restore_fidelity", "source_unchanged")
    if source_artifact is not None and recorded_after is not None:
        path = _resolve_rel(bundle.root, source_artifact)
        if path is not None and path.is_file():
            obj, error = _read_json(path)
            if error is None and isinstance(obj, dict):
                artifact_after = obj.get("after_tree_sha256")
                if isinstance(artifact_after, str) and artifact_after != recorded_after:
                    ctx.fail(
                        "source_hash_conflict",
                        "source_unchanged.after_tree_sha256 differs from run.source_world.after_tree_sha256",
                    )

    # Evidence index: every declared artifact must be pinned there and match.
    index_path: Path | None = None
    declared: dict[str, tuple[str, str]] = {}
    for check_id, spec_ in CHECK_SPEC_BY_ID.items():
        if check_id == "evidence_integrity":
            continue
        for kind, artifact in spec_.artifacts.items():
            if kind == "evidence_index":
                # the index cannot pin its own hash; every other artifact must be pinned
                continue
            ref = bundle.artifact_ref(check_id, kind)
            if ref:
                declared[ref] = (check_id, artifact.kind_type)
    index_ref = bundle.artifact_ref("smoke_fixture_validity", "evidence_index")
    if index_ref is not None:
        index_path = _resolve_rel(bundle.root, index_ref)
    if index_path is not None and index_path.is_file():
        index_obj, index_error = _read_json(index_path)
        if index_error is not None:
            ctx.fail("evidence_index_bad", f"evidence_index: {index_error}")
        elif not isinstance(index_obj, dict):
            ctx.fail("evidence_index_bad", "evidence_index: expected a JSON object")
        else:
            entries = index_obj.get("entries")
            if not isinstance(entries, list) or not entries:
                ctx.fail("evidence_index_bad", "evidence_index.entries: expected a non-empty list")
            else:
                pinned: dict[str, dict[str, Any]] = {}
                for number, entry in enumerate(entries):
                    if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                        ctx.fail("evidence_index_bad", f"evidence_index.entries[{number}]: expected {{path, ...}}")
                        continue
                    rel = entry["path"]
                    if not rel.startswith("artifacts/"):
                        ctx.fail(
                            "evidence_index_scope",
                            f"evidence_index.entries[{number}]: {rel!r} is outside artifacts/",
                        )
                    if rel in pinned:
                        ctx.fail("evidence_index_duplicate", f"evidence_index: {rel!r} is pinned twice")
                        continue
                    pinned[rel] = entry
                    declared_kind = declared.get(rel, ("", ""))[1]
                    expect_dir = declared_kind == "tree" or (not declared_kind and not PurePosixPath(rel).suffix)
                    state = inspect_artifact(bundle.root, rel, rel, expect_dir=expect_dir)
                    if state.status == "missing":
                        if rel in declared:
                            # the owning check already reports this as blocked; a not-yet-collected
                            # artifact must not be escalated to a fail through the index
                            continue
                        ctx.fail("evidence_index_dangling", f"evidence_index: {rel!r} does not exist")
                        continue
                    if state.status != "ok":
                        ctx.fail("evidence_index_bad", f"evidence_index: {rel!r} {state.detail}")
                        continue
                    recorded_bytes = entry.get("bytes")
                    if not _is_int(recorded_bytes) or recorded_bytes < 0:
                        ctx.fail("evidence_index_bytes", f"evidence_index: {rel!r} needs a non-negative bytes")
                    elif recorded_bytes != state.bytes:
                        ctx.fail(
                            "index_bytes_mismatch",
                            f"evidence_index: {rel!r} bytes={recorded_bytes} but the artifact is {state.bytes}",
                        )
                    if state.tree_sha256 is not None:
                        recorded_tree = entry.get("tree_sha256")
                        if not isinstance(recorded_tree, str) or not HEX64.fullmatch(recorded_tree):
                            ctx.fail("evidence_index_hash", f"evidence_index: {rel!r} needs a tree_sha256")
                        elif recorded_tree != state.tree_sha256:
                            ctx.fail(
                                "index_hash_mismatch",
                                f"evidence_index: {rel!r} tree hash does not match the index",
                            )
                        recorded_files = entry.get("files")
                        if not _is_int(recorded_files) or recorded_files < 0:
                            ctx.fail("evidence_index_files", f"evidence_index: {rel!r} needs a non-negative files")
                        elif recorded_files != state.files:
                            ctx.fail(
                                "index_files_mismatch",
                                f"evidence_index: {rel!r} files={recorded_files} but the tree has {state.files}",
                            )
                    else:
                        recorded_sha = entry.get("sha256")
                        if not isinstance(recorded_sha, str) or not HEX64.fullmatch(recorded_sha):
                            ctx.fail("evidence_index_hash", f"evidence_index: {rel!r} needs a sha256")
                        elif recorded_sha != state.sha256:
                            ctx.fail(
                                "index_hash_mismatch",
                                f"evidence_index: {rel!r} sha256 does not match the index",
                            )
                for rel in declared:
                    if rel not in pinned:
                        ctx.fail("index_incomplete", f"evidence_index: declared artifact {rel!r} is not pinned")

    # Independent source-world re-hash (read-only).
    if not skip_source_rehash:
        target = source_world or (str(world.get("path")) if isinstance(world, dict) and world.get("path") else None)
        if target is None:
            ctx.blocked("source_world_path_missing", "source world path is unknown; pass --source-world")
        else:
            root = Path(target)
            if not root.is_dir():
                ctx.blocked("source_world_unavailable", f"source world {target!r} is not a readable directory")
            else:
                try:
                    digest, files, _ = tree_hash(root)
                except OSError as error:
                    ctx.fail("source_world_unreadable", f"source world {target!r}: {error}")
                else:
                    if recorded_after is not None and digest != recorded_after:
                        ctx.fail(
                            "source_world_rehash_mismatch",
                            "source world re-hash differs from run.source_world.after_tree_sha256",
                        )
                    ctx.assert_("source world re-hashed read-only", True, f"{files} file(s), {digest}")
    else:
        limitations.append("Source-world bytes were not independently re-hashed (--skip-source-rehash).")

    return ctx.result()


def _parse_port_ranges(ranges: Sequence[str]) -> list[tuple[int, int]] | None:
    parsed: list[tuple[int, int]] = []
    for item in ranges:
        match = PORT_RANGE.fullmatch(item)
        if match is None:
            return None
        low, high = int(match.group(1)), int(match.group(2))
        if low < 1 or high > 65535 or low > high:
            return None
        parsed.append((low, high))
    return parsed or None


# --------------------------------------------------------------------------- check specs

CHECK_SPECS: list[CheckSpec] = [
    CheckSpec(
        id="fixture_map",
        title="Map fixture pinned and initialized deterministically",
        issue="#15",
        summary=(
            "Immutable map artifact with sha256 and version pins; >=3 independent initializations with "
            "identical state/order; real fake player facing the machine; no command blocks."
        ),
        artifacts={
            "fixture_manifest": ArtifactSpec("fixture_manifest", "artifacts/fixture_map/fixture-manifest.json", "json", "map URL/hash/versions/world tree hash"),
            "init_runs": ArtifactSpec("init_runs", "artifacts/fixture_map/init-runs.jsonl", "jsonl", "one record per initialization run"),
            "player_identity": ArtifactSpec("player_identity", "artifacts/fixture_map/player-identity.json", "json", "fixture fake player identity and facing"),
            "command_block_scan": ArtifactSpec("command_block_scan", "artifacts/fixture_map/command-block-scan.json", "json", "scan proving no command blocks"),
            "cleanup_rebuild": ArtifactSpec("cleanup_rebuild", "artifacts/fixture_map/cleanup-rebuild.json", "json", "cleanup/rebuild steps"),
        },
        runner=_check_fixture_map,
    ),
    CheckSpec(
        id="restore_fidelity",
        title="Snapshot restore is faithful, not just issued",
        issue="bridge#6",
        summary=(
            "Before/after protocol snapshots compared by the gate itself (order hash, counts, full NBT, "
            "pos/vel); endpoint resolution and failure cases; source world unchanged."
        ),
        artifacts={
            "snapshot_before": ArtifactSpec("snapshot_before", "artifacts/restore_fidelity/snapshot-before", "tree", "protocol snapshot before restore"),
            "snapshot_after": ArtifactSpec("snapshot_after", "artifacts/restore_fidelity/snapshot-after", "tree", "protocol snapshot after restore"),
            "restore_record": ArtifactSpec("restore_record", "artifacts/restore_fidelity/restore-record.json", "json", "restore endpoint/command outcome record"),
            "source_unchanged": ArtifactSpec("source_unchanged", "artifacts/restore_fidelity/source-unchanged.json", "json", "source world before/after tree hashes"),
            "failure_cases": ArtifactSpec("failure_cases", "artifacts/restore_fidelity/failure-cases.jsonl", "jsonl", "guarded restore failure cases"),
        },
        runner=_check_restore_fidelity,
    ),
    CheckSpec(
        id="player_context",
        title="Fake-player identity and server-vantage context on the real server",
        issue="#16",
        summary=(
            "Real Carpet fake player bound to the task, facing the machine; server-vantage identity is "
            "that player (two players, unknown identity, hit/miss); entry contract and version pins."
        ),
        artifacts={
            "identity_records": ArtifactSpec("identity_records", "artifacts/player_context/identity-records.jsonl", "jsonl", "identity/context probe records"),
            "entry_contract": ArtifactSpec("entry_contract", "artifacts/player_context/entry-contract.json", "json", "cold-start entry contract"),
            "version_pins": ArtifactSpec("version_pins", "artifacts/player_context/version-pins.json", "json", "interface-mod/bridge commits under test"),
        },
        runner=_check_player_context,
    ),
    CheckSpec(
        id="agent_dev_capability",
        title="Agent can write, build and install a mod into a located experiment instance",
        issue="#17",
        summary=(
            "Harness exposes terminal/file/build/install/MCP tools; same-size jar replacement loads the new "
            "bytes; a generic smoke mod builds and deploys; two instances stay isolated across restarts."
        ),
        artifacts={
            "tool_environment": ArtifactSpec("tool_environment", "artifacts/agent_dev_capability/tool-environment.json", "json", "harness tool capabilities"),
            "jar_update": ArtifactSpec("jar_update", "artifacts/agent_dev_capability/jar-update.json", "json", "same-size different-content jar update proof"),
            "smoke_mod": ArtifactSpec("smoke_mod", "artifacts/agent_dev_capability/smoke-mod.json", "json", "generic smoke mod build/deploy log"),
            "instance_isolation": ArtifactSpec("instance_isolation", "artifacts/agent_dev_capability/instance-isolation.jsonl", "jsonl", "experiment instance identity/isolation"),
        },
        runner=_check_agent_dev_capability,
    ),
    CheckSpec(
        id="independent_test_mod",
        title="Independent test mod audits inputs and transient cart outputs",
        issue="#18",
        summary=(
            "Read-only server hooks in source and experiment instances, no command blocks; attempt vs "
            "processing vs real output, tick/seq ordering, capture before void removal, negative cases."
        ),
        artifacts={
            "test_mod_manifest": ArtifactSpec("test_mod_manifest", "artifacts/independent_test_mod/test-mod-manifest.json", "json", "test mod identity and hook bounds"),
            "audit_events": ArtifactSpec("audit_events", "artifacts/independent_test_mod/audit-events.jsonl", "jsonl", "server-side audit events in occurrence order"),
            "negative_cases": ArtifactSpec("negative_cases", "artifacts/independent_test_mod/negative-cases.jsonl", "jsonl", "fake operations that must not count"),
            "audit_lifecycle": ArtifactSpec("audit_lifecycle", "artifacts/independent_test_mod/audit-lifecycle.json", "json", "audit lifecycle and missing-log behavior"),
        },
        runner=_check_independent_test_mod,
    ),
    CheckSpec(
        id="trace_persistence",
        title="Tool calls and game events persist and join by run/instance",
        issue="#19",
        summary=(
            "Full tool trace (args/result/error/timestamps) covering terminal/file/source/MCP; joins to "
            "audit events with zero unmatched agent events; missing logs fail loudly."
        ),
        artifacts={
            "tool_trace": ArtifactSpec("tool_trace", "artifacts/trace_persistence/tool-trace.jsonl", "jsonl", "tool call/return trace"),
            "trace_join": ArtifactSpec("trace_join", "artifacts/trace_persistence/trace-join.json", "json", "tool-to-game join record"),
            "missing_log_detection": ArtifactSpec("missing_log_detection", "artifacts/trace_persistence/missing-log-detection.jsonl", "jsonl", "missing-log failure cases"),
        },
        runner=_check_trace_persistence,
    ),
    CheckSpec(
        id="smoke_fixture_validity",
        title="Generic smoke tests pass and the run is pinned/evidenced",
        issue="#14",
        summary=(
            "Offline/lab/player/restore/test-mod smoke suites pass; fixture validity parameters recorded; "
            "locked versions and dependency hashes; evidence index pinning every artifact."
        ),
        artifacts={
            "smoke_report": ArtifactSpec("smoke_report", "artifacts/smoke_fixture_validity/smoke-report.json", "json", "generic smoke suite results"),
            "fixture_validity": ArtifactSpec("fixture_validity", "artifacts/smoke_fixture_validity/fixture-validity.json", "json", "fixture calibration/validity record"),
            "version_lock": ArtifactSpec("version_lock", "artifacts/smoke_fixture_validity/version-lock.json", "json", "component versions and hashes"),
            "evidence_index": ArtifactSpec("evidence_index", "artifacts/smoke_fixture_validity/evidence-index.json", "json", "sha256 index of every artifact"),
        },
        runner=_check_smoke_fixture_validity,
    ),
    CheckSpec(
        id="evidence_integrity",
        title="Bundle structure, artifact hashes, ports and source world",
        issue="#14",
        summary=(
            "Bundle/schema/origin validation; declared artifacts pinned by the evidence index with matching "
            "hashes; instance ports isolated and in range; independent read-only source-world re-hash."
        ),
        artifacts={},
        runner=None,
    ),
]

CHECK_SPEC_BY_ID: dict[str, CheckSpec] = {spec.id: spec for spec in CHECK_SPECS}


# --------------------------------------------------------------------------- report


def _aggregate(results: Iterable[CheckResult]) -> str:
    statuses = {result.status for result in results}
    if STATUS_FAIL in statuses:
        return STATUS_FAIL
    if STATUS_BLOCKED in statuses:
        return STATUS_BLOCKED
    return STATUS_PASS


def _exit_code(report: GateReport) -> int:
    if report.overall == STATUS_PASS:
        return EXIT_PASS
    if report.overall == STATUS_FAIL:
        return EXIT_FAIL
    return EXIT_BLOCKED


def _parse_bundle(root: Path) -> tuple[Any, Problem | None]:
    path = root / "bundle.json"
    if not path.is_file():
        return None, Problem(STATUS_BLOCKED, "bundle_missing", "bundle.json was not found")
    obj, error = _read_json(path)
    if error is not None:
        return None, Problem(STATUS_FAIL, "bundle_unreadable", f"bundle.json {error}")
    return obj, None


def run_gate(
    bundle_dir: Path | str,
    *,
    source_world: str | None = None,
    skip_source_rehash: bool = False,
    port_ranges: Sequence[str] | None = None,
) -> GateReport:
    root = Path(bundle_dir)
    manifest, parse_problem = _parse_bundle(root)
    bundle = Bundle(root, manifest, parse_problem)
    limitations: list[str] = [
        "The gate validates coordinator-provided raw artifacts; it cannot prove they were not fabricated. "
        "Keep the raw logs and spot-check the evidence index.",
        "Boolean facts such as hook read-only behavior are cross-checked against the canonical event log, "
        "not re-measured live by this tool.",
        "A pass authorizes the stage-two cold start only after the coordinator reviews this report and the "
        "artifact hashes.",
    ]
    results: list[CheckResult] = []
    for spec in CHECK_SPECS:
        if spec.id == "evidence_integrity":
            continue
        ctx = CheckContext(bundle, spec)
        if parse_problem is not None:
            ctx.problems.append(parse_problem)
        else:
            if spec.runner is None:
                ctx.fail("check_runner", f"{spec.id}: no runner implemented")
            else:
                try:
                    spec.runner(ctx)
                except Exception as error:  # a crash is a gate problem, not a pass
                    ctx.fail("check_crashed", f"{spec.id}: {type(error).__name__}: {error}")
        results.append(ctx.result())
    results.append(
        _run_evidence_integrity(
            bundle,
            source_world=source_world,
            skip_source_rehash=skip_source_rehash,
            port_ranges=list(port_ranges) if port_ranges else None,
            limitations=limitations,
        )
    )
    return GateReport(
        root=str(root),
        overall=_aggregate(results),
        checks=results,
        limitations=limitations,
        generated_at=_now_iso(),
    )


def render_report(report: GateReport, *, verbose: bool = False) -> str:
    lines = [f"stage-one gate ({GATE_ID}): {report.overall.upper()}  bundle={report.root}"]
    counts: dict[str, int] = {}
    for check in report.checks:
        counts[check.status] = counts.get(check.status, 0) + 1
        marker = check.status.upper()
        lines.append(f"  [{marker}] {check.id}  ({check.title})")
        for problem in check.reasons:
            lines.append(f"      - [{problem.level}/{problem.code}] {problem.message}")
        if verbose:
            for item in check.assertions:
                state = "ok" if item.ok else "not ok"
                lines.append(f"      . {item.name}: {state}" + (f" ({item.detail})" if item.detail else ""))
            for item in check.evidence:
                if item.status != "ok":
                    lines.append(f"      . {item.kind} -> {item.status}")
    lines.append(
        "  "
        + f"{len(report.checks)} check(s): "
        + ", ".join(f"{counts.get(status, 0)} {status}" for status in (STATUS_PASS, STATUS_FAIL, STATUS_BLOCKED))
    )
    if report.limitations:
        lines.append("  limitations:")
        for item in report.limitations:
            lines.append(f"    - {item}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- CLI commands


def _cmd_list(*, as_json: bool, out: TextIO) -> int:
    if as_json:
        payload = {
            "gate": GATE_ID,
            "issue": ISSUE,
            "checks": [
                {
                    "id": spec.id,
                    "title": spec.title,
                    "issue": spec.issue,
                    "summary": spec.summary,
                    "artifacts": [
                        {
                            "kind": artifact.kind,
                            "path": artifact.filename,
                            "type": artifact.kind_type,
                            "description": artifact.description,
                        }
                        for artifact in spec.artifacts.values()
                    ],
                }
                for spec in CHECK_SPECS
            ],
        }
        out.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        return EXIT_PASS
    out.write(f"{GATE_ID}: stage-one integration gate for {ISSUE}\n")
    out.write(f"status: only 'pass' when every check passes; missing evidence is '{STATUS_BLOCKED}', never pass\n")
    for spec in CHECK_SPECS:
        out.write(f"\n{spec.id}  [{spec.issue}]  {spec.title}\n")
        out.write(f"  {spec.summary}\n")
        for artifact in spec.artifacts.values():
            out.write(f"    {artifact.kind:22s} {artifact.filename} ({artifact.kind_type})\n")
    return EXIT_PASS


def _cmd_check(
    bundle_dir: str,
    *,
    report_path: str | None,
    as_json: bool,
    source_world: str | None,
    skip_source_rehash: bool,
    port_ranges: Sequence[str] | None,
    verbose: bool,
    out: TextIO,
) -> int:
    root = Path(bundle_dir)
    if not root.is_dir():
        out.write(f"error: {bundle_dir} is not a directory\n")
        return EXIT_USAGE
    report = run_gate(
        root,
        source_world=source_world,
        skip_source_rehash=skip_source_rehash,
        port_ranges=port_ranges,
    )
    if report_path:
        _write_json(Path(report_path), report.to_json())
    if as_json:
        out.write(json.dumps(report.to_json(), indent=2, ensure_ascii=False) + "\n")
    else:
        out.write(render_report(report, verbose=verbose) + "\n")
        if report_path:
            out.write(f"report written: {report_path}\n")
    return _exit_code(report)


def _cmd_scaffold(bundle_dir: str, *, force: bool, out: TextIO) -> int:
    root = Path(bundle_dir)
    if root.exists() and any(root.iterdir()) and not force:
        out.write(f"error: {bundle_dir} is not empty; pass --force to scaffold anyway\n")
        return EXIT_USAGE
    root.mkdir(parents=True, exist_ok=True)
    placeholder = "0" * 64
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": BUNDLE_KIND,
        "origin": "scaffold",
        "generated_at": _now_iso(),
        "run": {
            "run_id": "rom13-stage1-REPLACE",
            "issue": ISSUE,
            "tool_category_map": {key: list(value) for key, value in DEFAULT_TOOL_CATEGORIES.items()},
            "source_world": {
                "label": "Minecart ROM test",
                "path": "REPLACE with the read-only source save path",
                "before_tree_sha256": placeholder,
                "after_tree_sha256": placeholder,
            },
            "allowed_port_ranges": [DEFAULT_PORT_RANGE],
            "instances": [],
        },
        "checks": {
            spec.id: {
                "evidence": {kind: artifact.filename for kind, artifact in spec.artifacts.items()}
            }
            for spec in CHECK_SPECS
            if spec.artifacts
        },
    }
    _write_json(root / "bundle.json", manifest)
    readme = (
        "# Stage-one gate bundle (scaffold)\n\n"
        "This layout is empty on purpose: every check reports `blocked` until live prerequisite\n"
        "evidence is copied into `artifacts/` and the manifest is filled in.\n\n"
        "1. Set `origin` to `live` only when the artifacts are real live-game evidence.\n"
        "2. Fill `run` (run id, source world path and hashes, instances and ports) and every\n"
        "   `checks.<id>.evidence` path, keeping the canonical filenames from\n"
        "   `python tools/stage1_gate.py list`.\n"
        "3. Generate `artifacts/smoke_fixture_validity/evidence-index.json` with sha256 hashes\n"
        "   for every file and tree hash for every directory.\n"
        "4. Run `python tools/stage1_gate.py check <this dir> --source-world <save path>`.\n"
        "5. Attach the resulting report to guajun/mc-agent#14.\n"
    )
    (root / "README.md").write_text(readme, encoding="utf-8")
    out.write(f"scaffolded {bundle_dir}: evidence is still absent, so the gate reports {STATUS_BLOCKED}\n")
    return EXIT_PASS


def _cmd_hash_tree(
    target: str,
    *,
    exclusions: Sequence[str],
    as_json: bool,
    out: TextIO,
) -> int:
    root = Path(target)
    if not root.is_dir():
        out.write(f"error: {target} is not a directory\n")
        return EXIT_USAGE
    try:
        digest, files, total = tree_hash(root, tuple(exclusions) or DEFAULT_TREE_EXCLUSIONS)
    except OSError as error:
        out.write(f"error: cannot hash {target}: {error}\n")
        return EXIT_FAIL
    payload = {
        "path": str(root),
        "tree_sha256": digest,
        "files": files,
        "bytes": total,
        "exclusions": list(exclusions) or list(DEFAULT_TREE_EXCLUSIONS),
    }
    if as_json:
        out.write(json.dumps(payload, indent=2) + "\n")
    else:
        out.write(f"{digest}  {files} file(s)  {total} byte(s)  {root}\n")
    return EXIT_PASS


# --------------------------------------------------------------------------- selftest


class Selftest:
    """Assertion accumulator mirroring ``fork_verify``'s selftest."""

    def __init__(self, out: TextIO):
        self.out = out
        self.total = 0
        self.failures: list[str] = []

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        self.total += 1
        if ok:
            self.out.write(f"PASS  {name}\n")
        else:
            self.failures.append(name)
            self.out.write(f"FAIL  {name}" + (f"  <- {detail}" if detail else "") + "\n")

    def equal(self, name: str, got: Any, want: Any) -> None:
        self.check(name, got == want, f"got {got!r}, want {want!r}")


def _fake_sha(tag: str) -> str:
    return hashlib.sha256(tag.encode("utf-8")).hexdigest()


def _fake_commit(tag: str) -> str:
    return _fake_sha(tag)[:40]


def _build_valid_bundle(root: Path, source_world: Path | None = None) -> None:
    """Synthetic but fully schema-valid bundle; the selftest's only fixture builder."""
    root.mkdir(parents=True, exist_ok=True)
    if source_world is None:
        source_world = root.parent / f"{root.name}-source"
    source_world.mkdir(parents=True, exist_ok=True)
    (source_world / "level.dat").write_bytes(b"fake level data")
    (source_world / "data").mkdir(exist_ok=True)
    (source_world / "data").joinpath("rocks.dat").write_bytes(b"fake rocks")
    (source_world / "session.lock").write_bytes(b"volatile")
    world_hash, world_files, _ = tree_hash(source_world)

    records = [
        fork_verify.fake_record(
            f"00000000-0000-0000-0000-00000000000{index}",
            "minecraft:chest_minecart",
            [0.5, 70.0, float(index)],
            nbt=json.dumps({"Items": [{"id": "minecraft:stone", "count": 3}]}),
        )
        for index in range(1, 4)
    ]
    before_dir = root / "artifacts/restore_fidelity/snapshot-before"
    after_dir = root / "artifacts/restore_fidelity/snapshot-after"
    fork_verify.write_snapshot(before_dir, records)
    fork_verify.write_snapshot(after_dir, records)
    order = fork_verify.order_hash(fork_verify.load_snapshot(before_dir).entities)

    player_uuid = "11111111-1111-1111-1111-111111111111"
    state_hash = _fake_sha("fixture-state")
    _write_json(
        root / "artifacts/fixture_map/fixture-manifest.json",
        {
            "map": {
                "url": "https://example.invalid/minecart-rom/v1/map.zip",
                "sha256": _fake_sha("map"),
                "bytes": 123456,
                "mc_version": "26.2",
                "immutable": True,
                "download_verified": True,
                "bad_hash_rejected": True,
            },
            "mods": [
                {"name": "fabric-api", "version": "0.135.0", "sha256": _fake_sha("fabric-api")},
                {"name": "carpet", "version": "1.4.150", "sha256": _fake_sha("carpet")},
            ],
            "world": {"directory": "Minecart ROM test", "tree_sha256": world_hash, "files": world_files},
        },
    )
    _write_jsonl(
        root / "artifacts/fixture_map/init-runs.jsonl",
        [
            {
                "run_id": f"init-run-{index}",
                "init_id": f"init-{index}",
                "state_hash": state_hash,
                "order_hash": order,
                "entity_count": len(records),
                "inventory_total": 9,
                "early_output": False,
                "ready": True,
                "tick": 1200,
            }
            for index in range(1, 4)
        ],
    )
    _write_json(
        root / "artifacts/fixture_map/player-identity.json",
        {
            "uuid": player_uuid,
            "name": "FixtureAgent",
            "dimension": "minecraft:overworld",
            "pos": [0.5, 64.0, 0.5],
            "yaw": 90.0,
            "pitch": 0.0,
            "source": "carpet",
            "facing_target": True,
            "server_vantage_uuid": player_uuid,
        },
    )
    _write_json(
        root / "artifacts/fixture_map/command-block-scan.json",
        {
            "method": "grep nbt region scan",
            "world_dirs": ["Minecart ROM test"],
            "command_blocks": 0,
            "placed_by_init": False,
            "scanned": True,
        },
    )
    _write_json(
        root / "artifacts/fixture_map/cleanup-rebuild.json",
        {
            "steps": ["delete lab world", "re-provision from map", "run deterministic init"],
            "source_world_untouched": True,
            "rebuild_reproducible": True,
        },
    )

    _write_json(
        root / "artifacts/restore_fidelity/restore-record.json",
        {
            "endpoint": {
                "source": "source_audit",
                "target": "experiment",
                "target_resolved": True,
                "wrong_target_rejected": True,
            },
            "dimension": "minecraft:overworld",
            "chunks_loaded": True,
            "tick_controlled": True,
            "duplicates_pre_existing": 0,
            "commands_issued": len(records),
            "commands_failed": 0,
            "partial_failure": False,
            "pause_state_preserved": True,
            "issued_is_not_success": True,
        },
    )
    _write_json(
        root / "artifacts/restore_fidelity/source-unchanged.json",
        {
            "before_tree_sha256": world_hash,
            "after_tree_sha256": world_hash,
            "unchanged": True,
            "hash_tool": "tools/stage1_gate.py hash-tree",
            "exclusions": list(DEFAULT_TREE_EXCLUSIONS),
        },
    )
    _write_jsonl(
        root / "artifacts/restore_fidelity/failure-cases.jsonl",
        [
            {"case": case, "injected": "injected fault", "expected": "refused", "observed": "refused", "passed": True}
            for case in FAILURE_CASES
        ],
    )

    _write_jsonl(
        root / "artifacts/player_context/identity-records.jsonl",
        [
            {
                "case": "task_bind",
                "uuid": player_uuid,
                "viewed_uuid": player_uuid,
                "dimension": "minecraft:overworld",
                "pos": [0.5, 64.0, 0.5],
                "yaw": 90.0,
                "pitch": 0.0,
                "task_entry": "manual external task",
                "channel": "mcp",
                "accepted": True,
            },
            {
                "case": "hit",
                "uuid": player_uuid,
                "viewed_uuid": player_uuid,
                "dimension": "minecraft:overworld",
                "pos": [0.5, 64.0, 0.5],
                "yaw": 90.0,
                "pitch": 0.0,
                "task_entry": "manual external task",
                "channel": "mcp",
                "accepted": True,
            },
            {
                "case": "miss",
                "uuid": player_uuid,
                "viewed_uuid": player_uuid,
                "dimension": "minecraft:overworld",
                "pos": [0.5, 64.0, 0.5],
                "yaw": 89.0,
                "pitch": 1.0,
                "task_entry": "manual external task",
                "channel": "mcp",
                "accepted": True,
            },
            {
                "case": "two_players",
                "uuid": player_uuid,
                "other_uuid": "22222222-2222-2222-2222-222222222222",
                "viewed_uuid": player_uuid,
                "dimension": "minecraft:overworld",
                "pos": [0.5, 64.0, 0.5],
                "yaw": 90.0,
                "pitch": 0.0,
                "task_entry": "manual external task",
                "channel": "mcp",
                "accepted": True,
            },
            {
                "case": "unknown_identity",
                "uuid": "33333333-3333-3333-3333-333333333333",
                "viewed_uuid": "33333333-3333-3333-3333-333333333333",
                "dimension": "minecraft:overworld",
                "pos": [0.0, 64.0, 0.0],
                "yaw": 0.0,
                "pitch": 0.0,
                "task_entry": "manual external task",
                "channel": "mcp",
                "accepted": False,
                "rejected_reason": "uuid not bound to any task",
            },
        ],
    )
    _write_json(
        root / "artifacts/player_context/entry-contract.json",
        {
            "mode": "external_task",
            "fields": ["task", "uuid", "run_id"],
            "native_chat_verified": False,
            "unsupported_entries": ["fake_player say as native receipt-time chat"],
        },
    )
    _write_json(
        root / "artifacts/player_context/version-pins.json",
        {
            "interface_mod": {"repo": "guajun/mc-agent-interface-mod", "commit": _fake_commit("interface"), "tested": True},
            "bridge": {"repo": "guajun/mc-agent-bridge", "commit": _fake_commit("bridge"), "tested": True},
        },
    )

    _write_json(
        root / "artifacts/agent_dev_capability/tool-environment.json",
        {
            "harness": "codex-cli",
            "model": "gpt-5.1-codex",
            "tools": {
                "terminal": True,
                "file": True,
                "filesystem_write": True,
                "source_access": True,
                "build": True,
                "install": True,
                "mcp_or_cli": True,
                "lab_manage": True,
            },
            "docs_visible": ["skills/pi-multiagent/SKILL.md", "docs/stage1-gate.md"],
        },
    )
    _write_json(
        root / "artifacts/agent_dev_capability/jar-update.json",
        {
            "case": "same_size_different_content",
            "old_sha256": _fake_sha("old-jar"),
            "new_sha256": _fake_sha("new-jar"),
            "bytes": 4096,
            "loaded_sha256": _fake_sha("new-jar"),
            "runtime_evidence": "artifacts/agent_dev_capability/raw/jar-update-runtime.txt",
        },
    )
    raw_dir = root / "artifacts/agent_dev_capability/raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "jar-update-runtime.txt").write_text("loaded stage1-smoke 0.1.0\n", encoding="utf-8")
    _write_json(
        root / "artifacts/agent_dev_capability/smoke-mod.json",
        {
            "mod_id": "stage1-smoke",
            "version": "0.1.0",
            "built_sha256": _fake_sha("smoke-mod"),
            "deployed_sha256": _fake_sha("smoke-mod"),
            "server_log_ref": "artifacts/agent_dev_capability/raw/smoke-server.log",
            "sample_output_ref": "artifacts/agent_dev_capability/raw/smoke-output.txt",
            "build_errors_detected": True,
            "load_failure_detected": True,
            "missing_dependency_detected": True,
            "memory_state_rebuilt_after_restart": True,
            "restart_evidence_ref": "artifacts/agent_dev_capability/raw/restart-state.txt",
            "no_rom_logic": True,
        },
    )
    (raw_dir / "smoke-server.log").write_text("Done\n", encoding="utf-8")
    (raw_dir / "smoke-output.txt").write_text("sample\n", encoding="utf-8")
    (raw_dir / "restart-state.txt").write_text("mined state sample after restart\n", encoding="utf-8")
    _write_jsonl(
        root / "artifacts/agent_dev_capability/instance-isolation.jsonl",
        [
            {
                "instance_id": "src-audit",
                "role": "source_audit",
                "world_dir": "labs/rom13-src/world",
                "rcon_port": 27240,
                "bridge_port": 27241,
                "restarted": True,
                "resolves_correct_world": True,
                "conflicting_instance": False,
            },
            {
                "instance_id": "exp-1",
                "role": "experiment",
                "world_dir": "labs/rom13-exp/world",
                "rcon_port": 27242,
                "bridge_port": 27243,
                "restarted": True,
                "resolves_correct_world": True,
                "conflicting_instance": False,
            },
        ],
    )

    _write_json(
        root / "artifacts/independent_test_mod/test-mod-manifest.json",
        {
            "mod_id": "mc-agent-stage1-audit",
            "version": "0.1.0",
            "sha256": _fake_sha("test-mod"),
            "read_only": True,
            "hook_overhead_ms": 0.2,
            "fixture_behavior_unchanged": True,
            "loaded_in": ["source_audit", "experiment"],
            "agent_mod_coexists": True,
            "no_command_blocks": True,
        },
    )
    _write_jsonl(
        root / "artifacts/independent_test_mod/audit-events.jsonl",
        [
            {"event_id": "e1", "run_id": SELFTEST_RUN_ID, "instance_id": "exp-1", "phase": "init", "dimension": "minecraft:overworld", "tick": 100, "seq": 0, "event": "machine_ready", "pos": [0, 64, 0]},
            {"event_id": "e2", "run_id": SELFTEST_RUN_ID, "instance_id": "exp-1", "phase": "agent", "dimension": "minecraft:overworld", "tick": 110, "seq": 0, "event": "input_attempt", "actor_uuid": player_uuid, "pos": [1, 64, 0], "detail": {"tool_call_id": "call-3"}},
            {"event_id": "e3", "run_id": SELFTEST_RUN_ID, "instance_id": "exp-1", "phase": "agent", "dimension": "minecraft:overworld", "tick": 110, "seq": 1, "event": "input_processed", "actor_uuid": player_uuid, "pos": [1, 64, 0]},
            {"event_id": "e4", "run_id": SELFTEST_RUN_ID, "instance_id": "exp-1", "phase": "agent", "dimension": "minecraft:overworld", "tick": 113, "seq": 0, "event": "cart_emitted", "cart_uuid": records[0]["uuid"], "pos": [2, 64, 0], "captured_before_removal": True},
            {"event_id": "e5", "run_id": SELFTEST_RUN_ID, "instance_id": "exp-1", "phase": "agent", "dimension": "minecraft:overworld", "tick": 125, "seq": 0, "event": "cart_removed", "cart_uuid": records[0]["uuid"], "pos": [2, -70, 0], "removal_reason": "void"},
        ],
    )
    _write_jsonl(
        root / "artifacts/independent_test_mod/negative-cases.jsonl",
        [
            {"case": case, "attempted": case != "no_interaction", "processed": 0, "evidence_ref": f"audit:neg:{case}"}
            for case in NEGATIVE_CASES
        ],
    )
    _write_json(
        root / "artifacts/independent_test_mod/audit-lifecycle.json",
        {
            "states": ["ready", "init", "experiment_start", "experiment_end", "flush"],
            "missing_log_status": "error",
            "overflow_status": "error",
            "per_instance_files": True,
            "ring_buffer_reliance": False,
        },
    )

    _write_jsonl(
        root / "artifacts/trace_persistence/tool-trace.jsonl",
        [
            {"call_id": "call-1", "run_id": SELFTEST_RUN_ID, "instance_id": "exp-1", "tool": "bash", "args": {"command": "gradle build"}, "result": "ok", "error": None, "started_at": "2026-09-25T10:00:00Z", "ended_at": "2026-09-25T10:00:05Z"},
            {"call_id": "call-2", "run_id": SELFTEST_RUN_ID, "instance_id": "exp-1", "tool": "write", "args": {"path": "src/Logger.java"}, "result": "ok", "error": None, "started_at": "2026-09-25T10:00:05Z", "ended_at": "2026-09-25T10:00:06Z"},
            {"call_id": "call-3", "run_id": SELFTEST_RUN_ID, "instance_id": "exp-1", "tool": "mcp_mc_player", "args": {"uuid": player_uuid}, "result": {"pos": [0.5, 64.0, 0.5]}, "error": None, "started_at": "2026-09-25T10:00:06Z", "ended_at": "2026-09-25T10:00:07Z"},
            {"call_id": "call-4", "run_id": SELFTEST_RUN_ID, "instance_id": "exp-1", "tool": "git", "args": {"args": ["status"]}, "result": "clean", "error": None, "started_at": "2026-09-25T10:00:07Z", "ended_at": "2026-09-25T10:00:08Z"},
        ],
    )
    _write_json(
        root / "artifacts/trace_persistence/trace-join.json",
        {
            "joins": [
                {
                    "call_id": "call-3",
                    "audit_ref": {"run_id": SELFTEST_RUN_ID, "instance_id": "exp-1", "dimension": "minecraft:overworld", "tick": 110, "event_id": "e2"},
                    "verified": True,
                }
            ],
            "unmatched_tool_calls": 0,
            "unmatched_agent_events": 0,
        },
    )
    _write_jsonl(
        root / "artifacts/trace_persistence/missing-log-detection.jsonl",
        [
            {"case": "trace_missing", "detected": True, "exit_nonzero": True, "message_ref": "report:trace_missing"},
            {"case": "audit_missing", "detected": True, "exit_nonzero": True, "message_ref": "report:audit_missing"},
        ],
    )

    _write_json(
        root / "artifacts/smoke_fixture_validity/smoke-report.json",
        {
            "suites": [
                {"name": name, "command": f"python tools/run.py {name}", "status": "pass", "checks": 7, "log_ref": f"artifacts/smoke_fixture_validity/raw/{name}.log"}
                for name in SMOKE_SUITES
            ]
        },
    )
    smoke_raw = root / "artifacts/smoke_fixture_validity/raw"
    smoke_raw.mkdir(parents=True, exist_ok=True)
    for name in SMOKE_SUITES:
        (smoke_raw / f"{name}.log").write_text(f"{name}: pass\n", encoding="utf-8")
    _write_json(
        root / "artifacts/smoke_fixture_validity/fixture-validity.json",
        {
            "input_semantics": "note block hit by the fake player right-click",
            "stack_positions": [[0.5, 70.0, 0.5]],
            "output_boundary": {"min": [1.0, -80.0, 0.0], "max": [3.0, 80.0, 2.0]},
            "void_window_ticks": 40,
            "end_condition": "last cart captured before void removal",
            "timeout_s": 300,
            "hook_overhead_ms": 0.2,
            "with_mod_without_mod_consistent": True,
        },
    )
    _write_json(
        root / "artifacts/smoke_fixture_validity/version-lock.json",
        {
            "components": [
                {"name": name, ("commit" if requirement == "commit" else "sha256" if requirement == "sha256" else "tag"): (
                    _fake_commit(name) if requirement == "commit" else _fake_sha(name) if requirement == "sha256" else "pinned"
                ), "version": f"{name}-pinned"}
                for name, requirement in VERSION_LOCK_REQUIRED.items()
            ]
        },
    )

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "kind": BUNDLE_KIND,
        "origin": "live",
        "generated_at": _now_iso(),
        "run": {
            "run_id": SELFTEST_RUN_ID,
            "issue": ISSUE,
            "source_world": {
                "label": "Minecart ROM test",
                "path": str(source_world),
                "before_tree_sha256": world_hash,
                "after_tree_sha256": world_hash,
            },
            "allowed_port_ranges": [DEFAULT_PORT_RANGE],
            "instances": [
                {"instance_id": "src-audit", "role": "source_audit", "world_dir": "labs/rom13-src/world", "dimension": "minecraft:overworld", "rcon_port": 27240, "bridge_port": 27241},
                {"instance_id": "exp-1", "role": "experiment", "world_dir": "labs/rom13-exp/world", "dimension": "minecraft:overworld", "rcon_port": 27242, "bridge_port": 27243},
            ],
        },
        "checks": {
            spec.id: {
                "evidence": {kind: artifact.filename for kind, artifact in spec.artifacts.items()}
            }
            for spec in CHECK_SPECS
            if spec.artifacts
        },
    }
    root.joinpath("bundle.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    index_entries: list[dict[str, Any]] = []
    for spec in CHECK_SPECS:
        for artifact in spec.artifacts.values():
            if artifact.kind == "evidence_index":
                continue
            path = root / artifact.filename
            if artifact.kind_type == "tree":
                digest, files, total = tree_hash(path)
                index_entries.append({"path": artifact.filename, "tree_sha256": digest, "files": files, "bytes": total})
            else:
                index_entries.append({"path": artifact.filename, "sha256": _sha256_file(path), "bytes": path.stat().st_size})
    for extra in sorted((root / "artifacts").rglob("*")):
        if not extra.is_file():
            continue
        rel = extra.relative_to(root).as_posix()
        if any(entry["path"] == rel for entry in index_entries):
            continue
        if rel.endswith("evidence-index.json"):
            continue
        index_entries.append({"path": rel, "sha256": _sha256_file(extra), "bytes": extra.stat().st_size})
    _write_json(
        root / "artifacts/smoke_fixture_validity/evidence-index.json",
        {"tool": "tools/stage1_gate.py selftest", "entries": index_entries},
    )


def _report_check(report: GateReport, check_id: str) -> CheckResult:
    for check in report.checks:
        if check.id == check_id:
            return check
    raise KeyError(check_id)


def _has_reason(result: CheckResult, code: str) -> bool:
    return any(problem.code == code for problem in result.reasons)


def _copy_bundle(base: Path, valid: Path, name: str) -> Path:
    destination = base / f"case-{name}"
    shutil.copytree(valid, destination)
    return destination


def _edit_json(path: Path, edit: Callable[[dict[str, Any]], None]) -> None:
    obj = json.loads(path.read_text(encoding="utf-8"))
    edit(obj)
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


def _edit_jsonl(path: Path, edit: Callable[[list[dict[str, Any]]], None]) -> None:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    edit(rows)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _refresh_index(root: Path, rel: str) -> None:
    """Re-pin one artifact after a deliberate mutation, so only semantics fail."""
    index_path = root / "artifacts/smoke_fixture_validity/evidence-index.json"
    obj = json.loads(index_path.read_text(encoding="utf-8"))
    target = root / rel
    for entry in obj["entries"]:
        if entry.get("path") != rel:
            continue
        if target.is_dir():
            digest, files, total = tree_hash(target)
            entry["tree_sha256"] = digest
            entry["files"] = files
            entry["bytes"] = total
        else:
            entry["sha256"] = _sha256_file(target)
            entry["bytes"] = target.stat().st_size
        break
    else:
        raise AssertionError(f"evidence index has no entry for {rel}")
    index_path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


def run_selftest(out: TextIO | None = None) -> int:
    stream = out if out is not None else sys.stdout
    test = Selftest(stream)
    with tempfile.TemporaryDirectory(prefix="stage1-gate-selftest-") as tmp:
        base = Path(tmp)
        valid = base / "valid"
        _build_valid_bundle(valid, source_world=base / "valid-source")

        report = run_gate(valid)
        test.equal("valid bundle passes", report.overall, STATUS_PASS)
        test.equal("all checks pass", sum(1 for check in report.checks if check.status == STATUS_PASS), len(CHECK_SPECS))
        test.equal("pass exit code", _exit_code(report), EXIT_PASS)

        # origin / structure failures -------------------------------------------------
        scaffold = _copy_bundle(base, valid, "scaffold-origin")
        _edit_json(scaffold / "bundle.json", lambda obj: obj.update(origin="scaffold"))
        report = run_gate(scaffold)
        test.equal("scaffold origin is blocked", report.overall, STATUS_BLOCKED)
        test.check("scaffold reason", _has_reason(_report_check(report, "evidence_integrity"), "origin_not_live"))

        empty = base / "case-empty"
        empty.mkdir()
        report = run_gate(empty)
        test.equal("missing bundle is blocked", report.overall, STATUS_BLOCKED)
        test.check("missing bundle reason", _has_reason(_report_check(report, "evidence_integrity"), "bundle_missing"))

        broken = _copy_bundle(base, valid, "broken-json")
        (broken / "bundle.json").write_text("{not json", encoding="utf-8")
        report = run_gate(broken)
        test.equal("invalid bundle json is fail", report.overall, STATUS_FAIL)
        test.check("invalid bundle reason", _has_reason(_report_check(report, "evidence_integrity"), "bundle_unreadable"))

        missing_check = _copy_bundle(base, valid, "missing-check")
        _edit_json(missing_check / "bundle.json", lambda obj: obj["checks"].pop("fixture_map"))
        report = run_gate(missing_check)
        test.equal("missing check entry is blocked", report.overall, STATUS_BLOCKED)
        test.check("missing check reason", _has_reason(_report_check(report, "evidence_integrity"), "check_missing"))
        test.check("missing check blocks its own check", _has_reason(_report_check(report, "fixture_map"), "evidence_not_declared"))

        unknown_check = _copy_bundle(base, valid, "unknown-check")
        _edit_json(unknown_check / "bundle.json", lambda obj: obj["checks"].update({"made_up": {"evidence": {}}}))
        report = run_gate(unknown_check)
        test.equal("unknown check id is fail", report.overall, STATUS_FAIL)
        test.check("unknown check reason", _has_reason(_report_check(report, "evidence_integrity"), "check_unknown"))

        missing_file = _copy_bundle(base, valid, "missing-file")
        (missing_file / "artifacts/fixture_map/init-runs.jsonl").unlink()
        report = run_gate(missing_file)
        test.equal("missing artifact is blocked", report.overall, STATUS_BLOCKED)
        test.check("missing artifact reason", _has_reason(_report_check(report, "fixture_map"), "evidence_missing"))

        escape = _copy_bundle(base, valid, "path-escape")
        _edit_json(escape / "bundle.json", lambda obj: obj["checks"]["fixture_map"]["evidence"].update({"init_runs": "../init-runs.jsonl"}))
        report = run_gate(escape)
        test.equal("path escape is fail", report.overall, STATUS_FAIL)
        test.check("path escape reason", _has_reason(_report_check(report, "fixture_map"), "evidence_bad_path"))

        # fixture failures ------------------------------------------------------------
        short_init = _copy_bundle(base, valid, "short-init")
        _edit_jsonl(short_init / "artifacts/fixture_map/init-runs.jsonl", lambda rows: rows.pop())
        report = run_gate(short_init)
        test.equal("two init runs is fail", report.overall, STATUS_FAIL)
        test.check("short init reason", _has_reason(_report_check(report, "fixture_map"), "init_runs_short"))

        state_mismatch = _copy_bundle(base, valid, "state-mismatch")
        _edit_jsonl(state_mismatch / "artifacts/fixture_map/init-runs.jsonl", lambda rows: rows[1].update(state_hash=_fake_sha("other")))
        report = run_gate(state_mismatch)
        test.equal("init state mismatch is fail", report.overall, STATUS_FAIL)
        test.check("init mismatch reason", _has_reason(_report_check(report, "fixture_map"), "init_state_mismatch"))

        early = _copy_bundle(base, valid, "early-output")
        _edit_jsonl(early / "artifacts/fixture_map/init-runs.jsonl", lambda rows: rows[0].update(early_output=True))
        report = run_gate(early)
        test.equal("early output is fail", report.overall, STATUS_FAIL)
        test.check("early output reason", _has_reason(_report_check(report, "fixture_map"), "early_output"))

        command_blocks = _copy_bundle(base, valid, "command-blocks")
        _edit_json(command_blocks / "artifacts/fixture_map/command-block-scan.json", lambda obj: obj.update(command_blocks=1))
        report = run_gate(command_blocks)
        test.equal("command blocks are fail", report.overall, STATUS_FAIL)
        test.check("command block reason", _has_reason(_report_check(report, "fixture_map"), "command_blocks_present"))

        # restore failures ------------------------------------------------------------
        reordered = _copy_bundle(base, valid, "restore-reorder")
        records = [fork_verify.fake_record(f"00000000-0000-0000-0000-00000000000{i}", "minecraft:chest_minecart", [0.5, 70.0, float(i)]) for i in (1, 2, 3)]
        fork_verify.write_snapshot(reordered / "artifacts/restore_fidelity/snapshot-after", [records[1], records[0], records[2]])
        report = run_gate(reordered)
        test.equal("restore reorder is fail", report.overall, STATUS_FAIL)
        test.check("restore order reason", _has_reason(_report_check(report, "restore_fidelity"), "restore_order_mismatch"))

        inventory = _copy_bundle(base, valid, "restore-inventory")
        records = [fork_verify.fake_record(f"00000000-0000-0000-0000-00000000000{i}", "minecraft:chest_minecart", [0.5, 70.0, float(i)], nbt='{"Items": []}') for i in (1, 2, 3)]
        fork_verify.write_snapshot(inventory / "artifacts/restore_fidelity/snapshot-after", records)
        report = run_gate(inventory)
        test.equal("restore inventory mutation is fail", report.overall, STATUS_FAIL)
        test.check("restore nbt reason", _has_reason(_report_check(report, "restore_fidelity"), "restore_nbt_mismatch"))

        missing_case = _copy_bundle(base, valid, "restore-missing-case")
        _edit_jsonl(missing_case / "artifacts/restore_fidelity/failure-cases.jsonl", lambda rows: rows.pop())
        report = run_gate(missing_case)
        test.equal("restore failure case gap is fail", report.overall, STATUS_FAIL)
        test.check("failure case reason", _has_reason(_report_check(report, "restore_fidelity"), "failure_cases_incomplete"))

        issued_only = _copy_bundle(base, valid, "restore-issued-only")
        _edit_json(issued_only / "artifacts/restore_fidelity/restore-record.json", lambda obj: obj.update(issued_is_not_success=False))
        report = run_gate(issued_only)
        test.equal("issued-is-success is fail", report.overall, STATUS_FAIL)
        test.check("issued reason", _has_reason(_report_check(report, "restore_fidelity"), "not_true"))

        # player context --------------------------------------------------------------
        crosstalk = _copy_bundle(base, valid, "context-crosstalk")
        _edit_jsonl(crosstalk / "artifacts/player_context/identity-records.jsonl", lambda rows: rows[3].update(viewed_uuid="44444444-4444-4444-4444-444444444444"))
        report = run_gate(crosstalk)
        test.equal("player crosstalk is fail", report.overall, STATUS_FAIL)
        test.check("crosstalk reason", _has_reason(_report_check(report, "player_context"), "player_context_crosstalk"))

        # agent dev capability --------------------------------------------------------
        stale_jar = _copy_bundle(base, valid, "stale-jar")
        _edit_json(stale_jar / "artifacts/agent_dev_capability/jar-update.json", lambda obj: obj.update(loaded_sha256=obj["old_sha256"]))
        report = run_gate(stale_jar)
        test.equal("stale jar load is fail", report.overall, STATUS_FAIL)
        test.check("stale jar reason", _has_reason(_report_check(report, "agent_dev_capability"), "jar_update_stale"))

        port_dupe = _copy_bundle(base, valid, "port-dupe")
        _edit_jsonl(port_dupe / "artifacts/agent_dev_capability/instance-isolation.jsonl", lambda rows: rows[1].update(rcon_port=rows[0]["rcon_port"]))
        report = run_gate(port_dupe)
        test.equal("duplicate instance port is fail", report.overall, STATUS_FAIL)
        test.check("port dupe reason", _has_reason(_report_check(report, "agent_dev_capability"), "instance_port_duplicate"))

        # test mod / audit ------------------------------------------------------------
        audit_gap = _copy_bundle(base, valid, "audit-gap")
        _edit_jsonl(audit_gap / "artifacts/independent_test_mod/audit-events.jsonl", lambda rows: rows.pop(2))
        report = run_gate(audit_gap)
        test.equal("missing processed event is fail", report.overall, STATUS_FAIL)
        test.check("audit gap reason", _has_reason(_report_check(report, "independent_test_mod"), "audit_events_incomplete"))

        negative = _copy_bundle(base, valid, "negative-accepted")
        _edit_jsonl(negative / "artifacts/independent_test_mod/negative-cases.jsonl", lambda rows: rows[0].update(processed=1))
        report = run_gate(negative)
        test.equal("negative case accepted is fail", report.overall, STATUS_FAIL)
        test.check("negative reason", _has_reason(_report_check(report, "independent_test_mod"), "negative_case_processed"))

        transients = _copy_bundle(base, valid, "transient")
        _edit_jsonl(transients / "artifacts/independent_test_mod/audit-events.jsonl", lambda rows: rows[3].update(captured_before_removal=False))
        report = run_gate(transients)
        test.equal("transient not captured is fail", report.overall, STATUS_FAIL)
        test.check("transient reason", _has_reason(_report_check(report, "independent_test_mod"), "audit_transient"))

        ticks = _copy_bundle(base, valid, "tick-order")
        _edit_jsonl(ticks / "artifacts/independent_test_mod/audit-events.jsonl", lambda rows: rows[4].update(tick=90, seq=0))
        report = run_gate(ticks)
        test.equal("audit tick regression is fail", report.overall, STATUS_FAIL)
        test.check("tick order reason", _has_reason(_report_check(report, "independent_test_mod"), "audit_order"))

        # Provenance: refreshed hashes must not launder mixed/stale evidence.
        mixed_runs = _copy_bundle(base, valid, "mixed-runs")
        _edit_jsonl(
            mixed_runs / "artifacts/independent_test_mod/audit-events.jsonl",
            lambda rows: [row.update(run_id=f"unrelated-run-{index}") for index, row in enumerate(rows)],
        )
        _refresh_index(mixed_runs, "artifacts/independent_test_mod/audit-events.jsonl")
        report = run_gate(mixed_runs)
        test.equal("mixed audit runs is fail", report.overall, STATUS_FAIL)
        test.check(
            "mixed run provenance reason",
            _has_reason(_report_check(report, "independent_test_mod"), "audit_provenance"),
        )
        test.check(
            "integrity binds audit runs",
            _has_reason(_report_check(report, "evidence_integrity"), "audit_provenance"),
        )

        mixed_dimension = _copy_bundle(base, valid, "mixed-dimension")
        _edit_jsonl(
            mixed_dimension / "artifacts/independent_test_mod/audit-events.jsonl",
            lambda rows: rows[3].update(dimension="minecraft:the_nether"),
        )
        _refresh_index(mixed_dimension, "artifacts/independent_test_mod/audit-events.jsonl")
        report = run_gate(mixed_dimension)
        test.equal("mixed audit dimension is fail", report.overall, STATUS_FAIL)
        test.check(
            "mixed dimension provenance reason",
            _has_reason(_report_check(report, "independent_test_mod"), "audit_provenance"),
        )

        duplicate_id = _copy_bundle(base, valid, "duplicate-event-id")
        _edit_jsonl(
            duplicate_id / "artifacts/independent_test_mod/audit-events.jsonl",
            lambda rows: rows[1].update(event_id=rows[0]["event_id"]),
        )
        _refresh_index(duplicate_id, "artifacts/independent_test_mod/audit-events.jsonl")
        report = run_gate(duplicate_id)
        test.equal("duplicate audit event id is fail", report.overall, STATUS_FAIL)
        test.check(
            "duplicate event id reason",
            _has_reason(_report_check(report, "independent_test_mod"), "audit_event_id_duplicate"),
        )

        seq_collision = _copy_bundle(base, valid, "seq-collision")
        _edit_jsonl(
            seq_collision / "artifacts/independent_test_mod/audit-events.jsonl",
            lambda rows: rows[2].update(seq=rows[1]["seq"]),
        )
        _refresh_index(seq_collision, "artifacts/independent_test_mod/audit-events.jsonl")
        report = run_gate(seq_collision)
        test.equal("audit seq collision is fail", report.overall, STATUS_FAIL)
        test.check("seq collision reason", _has_reason(_report_check(report, "independent_test_mod"), "audit_order"))

        cross_instance = _copy_bundle(base, valid, "cross-instance-chain")
        _edit_jsonl(
            cross_instance / "artifacts/independent_test_mod/audit-events.jsonl",
            lambda rows: rows[2].update(instance_id="src-audit"),
        )
        _refresh_index(cross_instance, "artifacts/independent_test_mod/audit-events.jsonl")
        report = run_gate(cross_instance)
        test.equal("cross-instance chain is fail", report.overall, STATUS_FAIL)
        test.check(
            "cross-instance chain reason",
            _has_reason(_report_check(report, "independent_test_mod"), "audit_chain"),
        )

        no_dimension = _copy_bundle(base, valid, "missing-declared-dimension")
        _edit_json(no_dimension / "bundle.json", lambda obj: obj["run"]["instances"][0].pop("dimension"))
        report = run_gate(no_dimension)
        test.equal("missing declared dimension is fail", report.overall, STATUS_FAIL)
        test.check(
            "declared dimension reason",
            _has_reason(_report_check(report, "evidence_integrity"), "missing_field"),
        )

        command_block_mod = _copy_bundle(base, valid, "mod-command-blocks")
        _edit_json(command_block_mod / "artifacts/independent_test_mod/test-mod-manifest.json", lambda obj: obj.update(no_command_blocks=False))
        report = run_gate(command_block_mod)
        test.equal("test mod command block flag is fail", report.overall, STATUS_FAIL)
        test.check("test mod command block reason", _has_reason(_report_check(report, "independent_test_mod"), "not_true"))

        # trace / smoke / lock --------------------------------------------------------
        trace_gap = _copy_bundle(base, valid, "trace-gap")
        _edit_jsonl(trace_gap / "artifacts/trace_persistence/tool-trace.jsonl", lambda rows: rows.pop(1))
        report = run_gate(trace_gap)
        test.equal("trace category gap is fail", report.overall, STATUS_FAIL)
        test.check("trace category reason", _has_reason(_report_check(report, "trace_persistence"), "trace_categories"))

        unmatched = _copy_bundle(base, valid, "trace-unmatched")
        _edit_json(unmatched / "artifacts/trace_persistence/trace-join.json", lambda obj: obj.update(unmatched_agent_events=1))
        report = run_gate(unmatched)
        test.equal("unmatched agent events is fail", report.overall, STATUS_FAIL)
        test.check("trace join reason", _has_reason(_report_check(report, "trace_persistence"), "trace_join_unmatched"))

        join_run = _copy_bundle(base, valid, "trace-join-run")
        _edit_json(
            join_run / "artifacts/trace_persistence/trace-join.json",
            lambda obj: obj["joins"][0]["audit_ref"].update(run_id="unrelated-run"),
        )
        _refresh_index(join_run, "artifacts/trace_persistence/trace-join.json")
        report = run_gate(join_run)
        test.equal("trace join run mismatch is fail", report.overall, STATUS_FAIL)
        test.check(
            "trace join mismatch reason",
            _has_reason(_report_check(report, "trace_persistence"), "trace_join_mismatch"),
        )

        trace_run = _copy_bundle(base, valid, "trace-provenance")
        _edit_jsonl(
            trace_run / "artifacts/trace_persistence/tool-trace.jsonl",
            lambda rows: [row.update(run_id="unrelated-run") for row in rows],
        )
        _refresh_index(trace_run, "artifacts/trace_persistence/tool-trace.jsonl")
        report = run_gate(trace_run)
        test.equal("trace run provenance is fail", report.overall, STATUS_FAIL)
        test.check(
            "trace provenance reason",
            _has_reason(_report_check(report, "trace_persistence"), "trace_provenance"),
        )

        smoke_fail = _copy_bundle(base, valid, "smoke-fail")
        _edit_json(smoke_fail / "artifacts/smoke_fixture_validity/smoke-report.json", lambda obj: obj["suites"][0].update(status="skip"))
        report = run_gate(smoke_fail)
        test.equal("skipped smoke suite is fail", report.overall, STATUS_FAIL)
        test.check("smoke suite reason", _has_reason(_report_check(report, "smoke_fixture_validity"), "bad_value"))

        lock_gap = _copy_bundle(base, valid, "lock-gap")
        _edit_json(lock_gap / "artifacts/smoke_fixture_validity/version-lock.json", lambda obj: obj["components"].pop(1))
        report = run_gate(lock_gap)
        test.equal("missing locked component is fail", report.overall, STATUS_FAIL)
        test.check("version lock reason", _has_reason(_report_check(report, "smoke_fixture_validity"), "version_lock_component"))

        # evidence integrity ----------------------------------------------------------
        tampered = _copy_bundle(base, valid, "tampered")
        path = tampered / "artifacts/fixture_map/command-block-scan.json"
        path.write_text(path.read_text(encoding="utf-8").replace("grep nbt", "grep nbt changed"), encoding="utf-8")
        report = run_gate(tampered)
        test.equal("unpinned artifact change is fail", report.overall, STATUS_FAIL)
        test.check("index hash reason", _has_reason(_report_check(report, "evidence_integrity"), "index_hash_mismatch"))

        index_gap = _copy_bundle(base, valid, "index-gap")
        _edit_json(index_gap / "artifacts/smoke_fixture_validity/evidence-index.json", lambda obj: obj["entries"].pop(0))
        report = run_gate(index_gap)
        test.equal("unpinned artifact is fail", report.overall, STATUS_FAIL)
        test.check("index incomplete reason", _has_reason(_report_check(report, "evidence_integrity"), "index_incomplete"))

        index_bytes = _copy_bundle(base, valid, "index-bytes")
        _edit_json(
            index_bytes / "artifacts/smoke_fixture_validity/evidence-index.json",
            lambda obj: obj["entries"][0].update(bytes=obj["entries"][0].get("bytes", 0) + 1),
        )
        report = run_gate(index_bytes)
        test.equal("wrong index bytes is fail", report.overall, STATUS_FAIL)
        test.check("index bytes reason", _has_reason(_report_check(report, "evidence_integrity"), "index_bytes_mismatch"))

        world_changed = _copy_bundle(base, valid, "world-changed")
        _edit_json(world_changed / "bundle.json", lambda obj: obj["run"]["source_world"].update(before_tree_sha256=_fake_sha("other")))
        report = run_gate(world_changed)
        test.equal("source world hash change is fail", report.overall, STATUS_FAIL)
        test.check("source changed reason", _has_reason(_report_check(report, "evidence_integrity"), "source_world_changed"))

        instance_dupe = _copy_bundle(base, valid, "run-port-dupe")
        _edit_json(instance_dupe / "bundle.json", lambda obj: obj["run"]["instances"][1].update(rcon_port=obj["run"]["instances"][0]["rcon_port"]))
        report = run_gate(instance_dupe)
        test.equal("duplicate run port is fail", report.overall, STATUS_FAIL)
        test.check("run port reason", _has_reason(_report_check(report, "evidence_integrity"), "instance_ports"))

        port_range = _copy_bundle(base, valid, "run-port-range")
        _edit_json(port_range / "bundle.json", lambda obj: obj["run"]["instances"][0].update(rcon_port=27000, bridge_port=27001))
        report = run_gate(port_range)
        test.equal("out-of-range run port is fail", report.overall, STATUS_FAIL)
        test.check("port range reason", _has_reason(_report_check(report, "evidence_integrity"), "instance_ports"))

        rehash = base / "case-rehash"
        _build_valid_bundle(rehash, source_world=base / "rehash-source")
        (base / "rehash-source" / "intruder.dat").write_bytes(b"mutated")
        report = run_gate(rehash)
        test.equal("source rehash mismatch is fail", report.overall, STATUS_FAIL)
        test.check("rehash reason", _has_reason(_report_check(report, "evidence_integrity"), "source_world_rehash_mismatch"))

        report = run_gate(valid, skip_source_rehash=True)
        test.equal("skip source rehash still passes", report.overall, STATUS_PASS)
        test.check(
            "skip source rehash limitation",
            any("re-hashed" in item for item in report.limitations),
        )

        # hash-tree determinism -------------------------------------------------------
        tree = base / "tree"
        tree.mkdir()
        (tree / "a.bin").write_bytes(b"alpha")
        (tree / "nested").mkdir()
        (tree / "nested/b.bin").write_bytes(b"beta")
        (tree / "session.lock").write_bytes(b"lock-1")
        first = tree_hash(tree)
        second = tree_hash(tree)
        test.equal("tree hash deterministic", first, second)
        (tree / "session.lock").write_bytes(b"lock-2")
        test.equal("tree hash ignores volatile files", tree_hash(tree), first)
        (tree / "a.bin").write_bytes(b"alpha!")
        test.check("tree hash changes with content", tree_hash(tree) != first)

        # CLI smoke -------------------------------------------------------------------
        buffer = io.StringIO()
        test.equal("list exits 0", _cmd_list(as_json=False, out=buffer), EXIT_PASS)
        test.check("list mentions every check", all(spec.id in buffer.getvalue() for spec in CHECK_SPECS))
        buffer = io.StringIO()
        test.equal("list json exits 0", _cmd_list(as_json=True, out=buffer), EXIT_PASS)
        payload = json.loads(buffer.getvalue())
        test.equal("list json check count", len(payload["checks"]), len(CHECK_SPECS))
        test.equal(
            "list json artifact count",
            sum(len(item["artifacts"]) for item in payload["checks"]),
            sum(len(spec.artifacts) for spec in CHECK_SPECS),
        )
        buffer = io.StringIO()
        test.equal(
            "check blocked exit code",
            _cmd_check(str(empty), report_path=None, as_json=True, source_world=None, skip_source_rehash=False, port_ranges=None, verbose=False, out=buffer),
            EXIT_BLOCKED,
        )
        buffer = io.StringIO()
        test.equal(
            "check fail exit code",
            _cmd_check(str(early), report_path=None, as_json=True, source_world=None, skip_source_rehash=False, port_ranges=None, verbose=False, out=buffer),
            EXIT_FAIL,
        )
        buffer = io.StringIO()
        test.equal(
            "check pass exit code",
            _cmd_check(str(valid), report_path=None, as_json=False, source_world=None, skip_source_rehash=False, port_ranges=None, verbose=False, out=buffer),
            EXIT_PASS,
        )
        report_json = run_gate(valid).to_json()
        test.equal("report json round trip", json.loads(json.dumps(report_json))["overall"], STATUS_PASS)
        test.equal("report has all checks", len(report_json["checks"]), len(CHECK_SPECS))

    if test.failures:
        stream.write(
            f"selftest FAILED: {len(test.failures)} of {test.total} check(s) failed: "
            + ", ".join(test.failures)
            + "\n"
        )
        return 1
    stream.write(f"selftest OK: {test.total} check(s) passed (no game, no live evidence)\n")
    return 0


# --------------------------------------------------------------------------- entry point


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stage1_gate.py",
        description="Fail-closed stage-one integration gate for guajun/mc-agent#14.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="validate a gate-bundle directory")
    check.add_argument("bundle", help="directory containing bundle.json")
    check.add_argument("--report", help="write the JSON report to this path")
    check.add_argument("--json", action="store_true", help="print the JSON report instead of text")
    check.add_argument("--verbose", action="store_true", help="print every assertion")
    check.add_argument("--source-world", help="re-hash this source world directory (read-only)")
    check.add_argument(
        "--skip-source-rehash",
        action="store_true",
        help="do not re-hash the source world; records a limitation (never for final acceptance)",
    )
    check.add_argument(
        "--port-range",
        action="append",
        metavar="LOW-HIGH",
        help="override allowed instance port ranges (repeatable)",
    )

    list_cmd = sub.add_parser("list", help="list checks and required artifacts")
    list_cmd.add_argument("--json", action="store_true", help="machine-readable check catalog")

    scaffold = sub.add_parser("scaffold", help="write an empty canonical bundle layout")
    scaffold.add_argument("bundle", help="directory to create")
    scaffold.add_argument("--force", action="store_true", help="write into a non-empty directory")

    hash_tree = sub.add_parser("hash-tree", help="deterministic read-only directory tree hash")
    hash_tree.add_argument("target", help="directory to hash")
    hash_tree.add_argument("--exclude", action="append", default=[], help="exclusion pattern (repeatable)")
    hash_tree.add_argument("--json", action="store_true", help="machine-readable output")

    sub.add_parser("selftest", help="run the built-in checks (no bundle, no game needed)")
    return parser


def cli(argv: Sequence[str] | None = None, out: TextIO | None = None) -> int:
    stream = out if out is not None else sys.stdout
    args = build_parser().parse_args(argv)
    if args.command == "check":
        return _cmd_check(
            args.bundle,
            report_path=args.report,
            as_json=args.json,
            source_world=args.source_world,
            skip_source_rehash=args.skip_source_rehash,
            port_ranges=args.port_range,
            verbose=args.verbose,
            out=stream,
        )
    if args.command == "list":
        return _cmd_list(as_json=args.json, out=stream)
    if args.command == "scaffold":
        return _cmd_scaffold(args.bundle, force=args.force, out=stream)
    if args.command == "hash-tree":
        return _cmd_hash_tree(args.target, exclusions=args.exclude, as_json=args.json, out=stream)
    if args.command == "selftest":
        return run_selftest(stream)
    stream.write(f"error: unknown command {args.command!r}\n")
    return EXIT_USAGE


def main(argv: Sequence[str] | None = None) -> int:
    return cli(argv)


if __name__ == "__main__":
    raise SystemExit(main())
