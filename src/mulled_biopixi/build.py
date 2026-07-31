"""Execute a build plan using Pixi and Galaxy's mulled implementation."""

from __future__ import annotations

import hashlib
import inspect
import os
import platform
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable, List, Tuple
from urllib.error import URLError
from urllib.request import urlopen

from .plan import BuildPlan, PlanError

CommandRunner = Callable[..., subprocess.CompletedProcess]
OpenUrl = Callable[..., BinaryIO]

INVOLUCRO_VERSION = "1.2.0"
INVOLUCRO_RELEASE = f"https://github.com/involucro/involucro/releases/download/v{INVOLUCRO_VERSION}"
BUILD_IMAGES = (
    "quay.io/condaforge/miniforge3:latest",
    "quay.io/bioconda/base-glibc-busybox-bash:latest",
    "quay.io/bioconda/base-glibc-debian-bash:latest",
)
SUPPORTED_COMMANDS = ("build", "build-and-test")


@dataclass(frozen=True)
class InvolucroAsset:
    name: str
    sha256: str


# Digests published by the official GitHub v1.2.0 release. The executable must run on the host;
# its --platform argument decides which architecture its Docker containers use.
INVOLUCRO_ASSETS = {
    ("Darwin", "arm64"): InvolucroAsset(
        "involucro.darwin", "dbae6312b51e1853654a439905cb4d867d09c2570ab1bd5deecc56fb986038db"
    ),
    ("Darwin", "x86_64"): InvolucroAsset(
        "involucro.darwin", "dbae6312b51e1853654a439905cb4d867d09c2570ab1bd5deecc56fb986038db"
    ),
    ("Linux", "aarch64"): InvolucroAsset(
        "involucro.linux-arm64", "d860466ac078b927f141c6e3b6969e64c4a9acc57dc573b10a37ed19906e7e8a"
    ),
    ("Linux", "arm64"): InvolucroAsset(
        "involucro.linux-arm64", "d860466ac078b927f141c6e3b6969e64c4a9acc57dc573b10a37ed19906e7e8a"
    ),
    ("Linux", "amd64"): InvolucroAsset(
        "involucro.linux-amd64", "114e769c1d7a5a5c8be751e9eaf593d0495128a00f9bc6a88c0ccf3fb5827597"
    ),
    ("Linux", "x86_64"): InvolucroAsset(
        "involucro.linux-amd64", "114e769c1d7a5a5c8be751e9eaf593d0495128a00f9bc6a88c0ccf3fb5827597"
    ),
    ("Linux", "armv7l"): InvolucroAsset(
        "involucro.linux-armv7", "ab6a062c2077c299106b44eca7af28c966feccf70cc6d5a23075a0c61b9b8b80"
    ),
}


def publish_commands(plan: BuildPlan, pixi: str = "pixi") -> List[List[str]]:
    """Return the local-package publication commands in execution order."""

    return [
        [
            pixi,
            "publish",
            "--path",
            str(target.local_path),
            "--target-channel",
            str(plan.local_channel),
            "--target-platform",
            "linux-64",
        ]
        for target in plan.local_packages
    ]


def publish_local_packages(
    plan: BuildPlan,
    pixi: str = "pixi",
    runner: CommandRunner = subprocess.run,
) -> None:
    """Build direct path dependencies into the plan's indexed local channel."""

    if not plan.local_packages:
        return
    if shutil.which(pixi) is None:
        raise PlanError(f"cannot publish local packages: {pixi!r} is not on PATH")
    plan.local_channel.mkdir(parents=True, exist_ok=True)
    for command in publish_commands(plan, pixi):
        runner(command, cwd=plan.project_root, check=True)


def _involucro_asset() -> InvolucroAsset:
    host = (platform.system(), platform.machine().lower())
    try:
        return INVOLUCRO_ASSETS[host]
    except KeyError:
        raise PlanError(f"Involucro {INVOLUCRO_VERSION} has no pinned binary for {host[0]}/{host[1]}") from None


def involucro_cache_path(plan: BuildPlan) -> Path:
    """Return the project-local path for the compatibility binary."""

    return plan.project_root / ".mulled-biopixi" / "involucro" / INVOLUCRO_VERSION / "involucro"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_involucro(
    plan: BuildPlan,
    *,
    open_url: OpenUrl = urlopen,
) -> Path:
    """Download and verify Involucro 1.2.0 when stable Galaxy needs the platform shim."""

    asset = _involucro_asset()
    destination = involucro_cache_path(plan)
    if destination.is_file() and _sha256(destination) == asset.sha256:
        return destination

    destination.parent.mkdir(parents=True, exist_ok=True)
    url = f"{INVOLUCRO_RELEASE}/{asset.name}"
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as output:
            temporary = Path(output.name)
            try:
                with open_url(url, timeout=60) as response:
                    shutil.copyfileobj(response, output)
            except (OSError, URLError) as exc:
                raise PlanError(f"failed to download pinned Involucro from {url}: {exc}") from exc
        actual = _sha256(temporary)
        if actual != asset.sha256:
            raise PlanError(f"Involucro checksum mismatch for {url}: expected {asset.sha256}, got {actual}")
        temporary.chmod(temporary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        os.replace(temporary, destination)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return destination


def pull_build_images(
    *,
    runner: CommandRunner = subprocess.run,
) -> None:
    """Pre-pull amd64 images without using Involucro's obsolete Docker pull API."""

    if shutil.which("docker") is None:
        raise PlanError("cannot build a mulled image: 'docker' is not on PATH")
    for image in BUILD_IMAGES:
        runner(["docker", "pull", "--platform", "linux/amd64", image], check=True)


def _galaxy_mulled_api() -> Tuple[Callable, Callable]:
    try:
        from galaxy.tool_util.deps.mulled.mulled_build import build_target, mull_targets
    except ImportError as exc:  # pragma: no cover - packaging/install failure
        raise PlanError("Galaxy mulled support is unavailable; install `galaxy-tool-util[mulled]`") from exc
    return build_target, mull_targets


def _supports_target_platform(mull_targets: Callable) -> bool:
    return "target_platform" in inspect.signature(mull_targets).parameters


def _compatibility_context(path: Path, verbose: bool):
    from galaxy.tool_util.deps.mulled.mulled_build import InvolucroContext

    class TargetPlatformContext(InvolucroContext):
        def build_command(self, involucro_args):
            command = super().build_command(involucro_args)
            return command[:2] + ["--platform", "linux/amd64"] + command[2:]

    return TargetPlatformContext(involucro_bin=str(path), verbose="3" if verbose else "2")


def check_mulled_platform(*, dry_run: bool = False) -> None:
    """Fail before local publication when Galaxy cannot produce the profile platform."""

    _build_target, mull_targets = _galaxy_mulled_api()
    if not _supports_target_platform(mull_targets):
        _involucro_asset()


def run_mulled(
    plan: BuildPlan,
    *,
    command: str = "build",
    dry_run: bool = False,
    test: str = "true",
    verbose: bool = False,
    use_mamba: bool = False,
) -> int:
    """Call Galaxy's Python API, adapting to stable and development releases."""

    if command not in SUPPORTED_COMMANDS:
        supported = ", ".join(SUPPORTED_COMMANDS)
        raise PlanError(f"unsupported command {command!r}; expected one of: {supported}")

    check_mulled_platform(dry_run=dry_run)
    build_target, mull_targets = _galaxy_mulled_api()

    targets = [build_target(target.package, version=target.version, build=target.build) for target in plan.targets]
    kwargs = {
        "command": command,
        "channels": list(plan.mulled_channels),
        "dry_run": dry_run,
        "test": test,
        "verbose": verbose,
        "use_mamba": use_mamba,
        # Galaxy's default is a mutable list which file:// handling appends to.
        "binds": ["build/dist:/usr/local/"],
        # A planning operation must not start a Docker/Conda metadata probe.
        "determine_base_image": not dry_run,
    }
    if not dry_run:
        # Involucro 1.2.0 embeds Docker API 1.32. Docker 29 rejects pull operations below API
        # 1.40, although all of Involucro's build/container operations still work. Keep pulls on
        # the current Docker CLI for both the compatibility shim and Galaxy's native platform API.
        pull_build_images()

    if _supports_target_platform(mull_targets):
        kwargs["target_platform"] = "linux/amd64"
    else:
        if dry_run:
            path = involucro_cache_path(plan)
        else:
            path = ensure_involucro(plan)
        kwargs["involucro_context"] = _compatibility_context(path, verbose)
    return int(mull_targets(targets, **kwargs))
