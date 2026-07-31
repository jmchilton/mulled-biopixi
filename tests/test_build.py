import hashlib
import io
import stat
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any, Dict

import pytest

from mulled_biopixi.build import (
    _compatibility_context,
    ensure_involucro,
    InvolucroAsset,
    publish_commands,
    publish_local_packages,
    pull_build_images,
    run_mulled,
)
from mulled_biopixi.plan import BuildPlan, PlanError, Target


def build_plan(tmp_path: Path) -> BuildPlan:
    return BuildPlan(
        project_root=tmp_path,
        manifest_path=tmp_path / "pixi.toml",
        lock_path=tmp_path / "pixi.lock",
        local_channel=tmp_path / "channel",
        channels=("conda-forge",),
        targets=(Target("samtools", "1.17", build="hd87286a_2"),),
    )


def test_publish_command_targets_indexed_linux_channel(tmp_path: Path):
    package = tmp_path / "recipe"
    package.mkdir()
    plan = BuildPlan(
        project_root=tmp_path,
        manifest_path=tmp_path / "pixi.toml",
        lock_path=tmp_path / "pixi.lock",
        local_channel=tmp_path / "channel",
        channels=("conda-forge",),
        targets=(Target("tool", "1.0", local_path=package),),
    )

    assert publish_commands(plan) == [
        [
            "pixi",
            "publish",
            "--path",
            str(package),
            "--target-channel",
            str(tmp_path / "channel"),
            "--target-platform",
            "linux-64",
        ]
    ]


def test_publish_executes_from_project_root(tmp_path: Path, monkeypatch):
    package = tmp_path / "recipe"
    package.mkdir()
    plan = BuildPlan(
        project_root=tmp_path,
        manifest_path=tmp_path / "pixi.toml",
        lock_path=tmp_path / "pixi.lock",
        local_channel=tmp_path / "channel",
        channels=("conda-forge",),
        targets=(Target("tool", "1.0", local_path=package),),
    )
    calls = []
    monkeypatch.setattr("mulled_biopixi.build.shutil.which", lambda _: "/bin/pixi")

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return CompletedProcess(command, 0)

    publish_local_packages(plan, runner=runner)

    assert plan.local_channel.is_dir()
    assert calls[0][1] == {"cwd": tmp_path, "check": True}


def test_compatibility_context_injects_target_platform(tmp_path: Path):
    context = _compatibility_context(tmp_path / "involucro", verbose=False)

    assert context.build_command(["-f", "invfile.lua", "build"]) == [
        str(tmp_path / "involucro"),
        "-v=2",
        "--platform",
        "linux/amd64",
        "-f",
        "invfile.lua",
        "build",
    ]


def test_involucro_download_is_pinned_verified_and_cached(tmp_path: Path, monkeypatch):
    content = b"a pretend involucro binary"
    digest = hashlib.sha256(content).hexdigest()
    monkeypatch.setattr(
        "mulled_biopixi.build._involucro_asset",
        lambda: InvolucroAsset("involucro.test", digest),
    )
    calls = []

    def open_url(url, **kwargs):
        calls.append((url, kwargs))
        return io.BytesIO(content)

    plan = build_plan(tmp_path)
    path = ensure_involucro(plan, open_url=open_url)
    cached = ensure_involucro(plan, open_url=open_url)

    assert cached == path
    assert path.read_bytes() == content
    assert path.stat().st_mode & stat.S_IXUSR
    assert len(calls) == 1
    assert calls[0][0].endswith("/v1.2.0/involucro.test")
    assert calls[0][1] == {"timeout": 60}


def test_build_images_are_pulled_for_amd64(monkeypatch):
    calls = []
    monkeypatch.setattr("mulled_biopixi.build.shutil.which", lambda _: "/usr/bin/docker")

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return CompletedProcess(command, 0)

    pull_build_images(runner=runner)

    assert len(calls) == 3
    assert all(call[0][0:4] == ["docker", "pull", "--platform", "linux/amd64"] for call in calls)
    assert all(call[1] == {"check": True} for call in calls)


def test_stable_galaxy_uses_compatibility_context(tmp_path: Path, monkeypatch):
    captured: Dict[str, Any] = {}

    def build_target(name, version=None, build=None):
        return name, version, build

    def mull_targets(targets, **kwargs):
        captured.update(targets=targets, kwargs=kwargs)
        return 0

    binary = tmp_path / "involucro"
    monkeypatch.setattr("mulled_biopixi.build._galaxy_mulled_api", lambda: (build_target, mull_targets))
    monkeypatch.setattr("mulled_biopixi.build.ensure_involucro", lambda plan: binary)
    monkeypatch.setattr("mulled_biopixi.build.pull_build_images", lambda: None)

    assert run_mulled(build_plan(tmp_path)) == 0

    context = captured["kwargs"]["involucro_context"]
    assert context.build_command(["build"])[:4] == [
        str(binary),
        "-v=2",
        "--platform",
        "linux/amd64",
    ]
    assert "target_platform" not in captured["kwargs"]


def test_new_galaxy_uses_native_target_platform(tmp_path: Path, monkeypatch):
    captured: Dict[str, Any] = {}

    def build_target(name, version=None, build=None):
        return name, version, build

    def mull_targets(targets, target_platform=None, **kwargs):
        captured.update(target_platform=target_platform, kwargs=kwargs)
        return 0

    monkeypatch.setattr("mulled_biopixi.build._galaxy_mulled_api", lambda: (build_target, mull_targets))
    monkeypatch.setattr(
        "mulled_biopixi.build.ensure_involucro",
        lambda plan: (_ for _ in ()).throw(AssertionError("compatibility download used")),
    )
    monkeypatch.setattr("mulled_biopixi.build.pull_build_images", lambda: None)

    assert run_mulled(build_plan(tmp_path)) == 0
    assert captured["target_platform"] == "linux/amd64"
    assert "involucro_context" not in captured["kwargs"]


def test_push_capable_command_is_rejected_before_calling_galaxy(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "mulled_biopixi.build._galaxy_mulled_api",
        lambda: (_ for _ in ()).throw(AssertionError("Galaxy API called")),
    )

    with pytest.raises(PlanError, match="unsupported command 'all'"):
        run_mulled(build_plan(tmp_path), command="all")
