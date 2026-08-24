from __future__ import annotations

import os
import select
import signal
import socket
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/isolated-live-smoke.sh"
REGISTRY_ID = "registry-id-task14"
NODE_ID = "node-id-task14"
NETWORK_ID = "network-id-task14"

FAKE_RUNTIME = r"""#!/usr/bin/env python3
from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path
from types import FrameType

state = Path(os.environ["TASK14_FAKE_STATE"])
command = Path(sys.argv[0]).name
args = sys.argv[1:]
registry = state / "registry"
node = state / "node"
network = state / "network"
members = state / "members"
created = "2099-01-01T00:00:00.000000000Z"


def emit(value: str) -> None:
    sys.stdout.write(f"{value}\n")


if command == "curl" or command == "kubectl":
    raise SystemExit(0)

if command == "kind":
    if args[:2] == ["get", "clusters"]:
        raise SystemExit(0)
    if args[:2] == ["create", "cluster"]:
        (state / "kind-pid").write_text(str(os.getpid()), encoding="utf-8")
        emit("FAKE_KIND_PREPARING")
        sys.stdout.flush()
        hung = os.environ.get("TASK14_FAKE_SCENARIO") == "hung"

        def handle_term(_signum: int, _frame: FrameType | None) -> None:
            (state / "term-pid").write_text(str(os.getpid()), encoding="utf-8")
            if not hung:
                raise SystemExit(143)

        signal.signal(signal.SIGTERM, handle_term)
        if os.environ.get("TASK14_FAKE_SCENARIO") == "prepublish":
            while True:
                time.sleep(60)
        node.write_text("node-id-task14", encoding="utf-8")
        network.write_text("present", encoding="utf-8")
        members.write_text("node-id-task14\n", encoding="utf-8")
        while True:
            time.sleep(60)
    raise SystemExit(97)

if command != "docker":
    raise SystemExit(97)

if args[:2] == ["info", "--format"]:
    emit("2098-12-31T23:59:59.000000000Z")
    raise SystemExit(0)

if args[:2] == ["container", "inspect"]:
    target = args[-1]
    current_node_id = node.read_text(encoding="utf-8") if node.exists() else ""
    exists = (
        target in {"three-t-pipeline-registry", "registry-id-task14"} and registry.exists()
    ) or (
        target == current_node_id and node.exists()
    )
    if not exists:
        raise SystemExit(1)
    if "--format" not in args:
        raise SystemExit(0)
    template = args[args.index("--format") + 1]
    if ".Id" in template:
        emit("registry-id-task14" if target != current_node_id else current_node_id)
    elif "task-run" in template:
        emit((state / "token").read_text(encoding="utf-8"))
    elif "io.x-k8s.kind.cluster" in template:
        emit("three-t-pipeline-smoke" if target == current_node_id else "")
    elif ".Name" in template:
        emit("/three-t-pipeline-smoke-control-plane")
    elif ".Created" in template:
        emit(created)
    raise SystemExit(0)

if args[:2] == ["ps", "-aq"]:
    query = " ".join(args)
    if "io.x-k8s.kind.cluster=three-t-pipeline-smoke" in query:
        if node.exists():
            count_path = state / "node-query-count"
            count = int(count_path.read_text(encoding="utf-8")) + 1 if count_path.exists() else 1
            count_path.write_text(str(count), encoding="utf-8")
            if os.environ.get("TASK14_FAKE_REPLACE_NODE") == "1" and count == 2:
                node.write_text("replacement-node-id", encoding="utf-8")
                members.write_text("replacement-node-id\n", encoding="utf-8")
            emit(node.read_text(encoding="utf-8"))
    elif "three-t.dev/task=14-isolated-live" in query:
        if registry.exists():
            emit("registry-id-task14")
    else:
        if registry.exists():
            emit("registry-id-task14")
        if node.exists():
            emit("node-id-task14")
    raise SystemExit(0)

if args[:2] == ["network", "ls"]:
    if network.exists():
        emit("network-id-task14")
    raise SystemExit(0)

if args[:2] == ["network", "inspect"]:
    if not network.exists() or args[-1] not in {"kind", "network-id-task14"}:
        raise SystemExit(1)
    if "--format" not in args:
        raise SystemExit(0)
    template = args[args.index("--format") + 1]
    if ".Id" in template:
        emit("network-id-task14")
    elif ".Name" in template:
        emit("kind")
    elif ".Created" in template:
        emit(created)
    elif ".Containers" in template:
        sys.stdout.write(members.read_text(encoding="utf-8"))
    raise SystemExit(0)

if args and args[0] == "run":
    token_arg = next(value for value in args if value.startswith("three-t.dev/task-run="))
    (state / "token").write_text(token_arg.split("=", 1)[1], encoding="utf-8")
    registry.write_text("present", encoding="utf-8")
    emit("registry-id-task14")
    raise SystemExit(0)

if args[:2] == ["rm", "-f"]:
    for target in args[2:]:
        if target == "registry-id-task14":
            registry.unlink(missing_ok=True)
        elif target == "node-id-task14":
            if node.exists() and node.read_text(encoding="utf-8") == target:
                node.unlink()
                members.write_text("", encoding="utf-8")
        with (state / "deletions").open("a", encoding="utf-8") as stream:
            stream.write(f"container:{target}\n")
    raise SystemExit(0)

if args[:2] == ["network", "rm"]:
    with (state / "deletions").open("a", encoding="utf-8") as stream:
        stream.write(f"network:{args[2]}\n")
    if args[2] == "network-id-task14":
        network.unlink(missing_ok=True)
        members.unlink(missing_ok=True)
    raise SystemExit(0)

raise SystemExit(97)
"""


def _port_is_free(port: int) -> bool:
    try:
        with socket.socket() as probe:
            probe.settimeout(0.1)
            return probe.connect_ex(("127.0.0.1", port)) != 0
    except PermissionError:
        return True


def build_fake_environment(
    tmp_path: Path, *, scenario: str, replace_node: bool = False
) -> tuple[dict[str, str], Path]:
    state = tmp_path / "state"
    state.mkdir()
    if scenario == "baseline-network":
        (state / "network").write_text("present", encoding="utf-8")
        (state / "members").write_text("", encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    runtime = fake_bin / "runtime"
    _ = runtime.write_text(FAKE_RUNTIME, encoding="utf-8")
    _ = runtime.chmod(0o755)
    for name in ("curl", "docker", "kind", "kubectl"):
        (fake_bin / name).symlink_to(runtime)
    return (
        {
            **os.environ,
            "HOME": str(tmp_path / "home"),
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "TASK14_FAKE_SCENARIO": scenario,
            "TASK14_FAKE_REPLACE_NODE": "1" if replace_node else "0",
            "TASK14_FAKE_STATE": str(state),
            "TMPDIR": str(tmp_path),
        },
        state,
    )


@pytest.mark.parametrize(
    "scenario",
    ["cooperative", "hung", "prepublish", "baseline-network"],
    ids=["cooperative-child", "hung-child", "before-publication", "preserve-baseline-network"],
)
def test_term_during_kind_preparation_reaps_exact_child_and_removes_partial_resources(
    tmp_path: Path, scenario: str
) -> None:
    # Given: a stateful Kind child that publishes a node and network before it finishes.
    env, state = build_fake_environment(tmp_path, scenario=scenario)
    evidence = tmp_path / "must-not-publish.json"
    stdout_path = tmp_path / "stdout"
    stderr_path = tmp_path / "stderr"
    with (
        stdout_path.open("w", encoding="utf-8") as stdout_stream,
        stderr_path.open("w", encoding="utf-8") as stderr_stream,
    ):
        process = subprocess.Popen(
            [
                str(SCRIPT),
                "--create",
                "--registry",
                "127.0.0.1:5001",
                "--build-push-local",
                "--run",
                "--cleanup",
                "--collect",
                str(evidence),
            ],
            cwd=ROOT,
            env=env,
            stdout=stdout_stream,
            stderr=stderr_stream,
            text=True,
        )
        marker = state / "kind-pid"
        for _ in range(100):
            if marker.exists():
                break
            _ = select.select([], [], [], 0.05)
        assert marker.exists(), "fake Kind never reached its preparation boundary"
        child_pid = int(marker.read_text(encoding="utf-8"))

        # When: TERM is delivered twice while Kind is still preparing resources.
        os.kill(process.pid, signal.SIGTERM)
        term_marker = state / "term-pid"
        for _ in range(100):
            if term_marker.exists():
                break
            _ = select.select([], [], [], 0.05)
        assert term_marker.exists(), "TERM was not forwarded to the exact Kind child"
        assert int(term_marker.read_text(encoding="utf-8")) == child_pid
        os.kill(process.pid, signal.SIGTERM)
        _ = process.wait(timeout=8)
    stdout = stdout_path.read_text(encoding="utf-8")
    stderr = stderr_path.read_text(encoding="utf-8")
    try:
        os.kill(child_pid, 0)
    except ProcessLookupError:
        child_was_live = False
    else:
        child_was_live = True
        os.kill(child_pid, signal.SIGKILL)

    # Then: interruption is bounded, exact-ID cleanup is complete, and success is unpublished.
    assert process.returncode == 130, stderr
    assert "ISOLATED_LIVE_SUCCEEDED" not in stdout + stderr
    assert not child_was_live
    assert not (state / "registry").exists()
    assert not (state / "node").exists()
    assert (state / "network").exists() == (scenario == "baseline-network")
    if scenario == "baseline-network":
        assert (state / "members").read_text(encoding="utf-8") == ""
    assert not evidence.exists()
    assert not list(tmp_path.glob("three-t-task14.*"))
    assert _port_is_free(5001)
    assert _port_is_free(19000)
