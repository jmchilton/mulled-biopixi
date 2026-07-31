"""Translate a narrow Biopixi/Pixi project into a mulled build plan."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple
from urllib.parse import unquote, urlparse

import yaml

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised by the Python 3.9 package installation
    import tomli as tomllib


class PlanError(ValueError):
    """The project cannot safely be translated by mulled-biopixi."""


@dataclass(frozen=True)
class Target:
    """One direct Conda dependency passed to mulled."""

    name: str
    version: str
    build: Optional[str] = None
    channel: Optional[str] = None
    local_path: Optional[Path] = None

    @property
    def package(self) -> str:
        return f"{self.channel}::{self.name}" if self.channel else self.name

    @property
    def display(self) -> str:
        value = f"{self.package}={self.version}"
        return f"{value}--{self.build}" if self.build else value


@dataclass(frozen=True)
class BuildPlan:
    """All inputs needed to publish local packages and call Galaxy mulled."""

    project_root: Path
    manifest_path: Path
    lock_path: Path
    local_channel: Path
    channels: Tuple[str, ...]
    targets: Tuple[Target, ...]

    @property
    def local_packages(self) -> Tuple[Target, ...]:
        return tuple(target for target in self.targets if target.local_path is not None)

    @property
    def mulled_channels(self) -> Tuple[str, ...]:
        channels: List[str] = []
        if self.local_packages:
            channels.append(self.local_channel.as_uri())
        channels.extend(self.channels)
        return tuple(dict.fromkeys(channels))

    @property
    def target_string(self) -> str:
        return ",".join(target.display for target in self.targets)


@dataclass(frozen=True)
class _LockedPackage:
    name: str
    version: Optional[str]
    build: Optional[str]
    source: str
    is_source: bool = False


def _load_toml(path: Path) -> Mapping[str, Any]:
    try:
        with path.open("rb") as handle:
            value = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise PlanError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PlanError(f"{path} is not a TOML table")
    return value


def _table(value: Any, label: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise PlanError(f"{label} must be a table")
    return value


def _effective_dependencies(manifest: Mapping[str, Any]) -> Dict[str, Any]:
    dependencies = dict(_table(manifest.get("dependencies"), "[dependencies]"))
    target_tables = _table(manifest.get("target"), "[target]")
    linux = _table(target_tables.get("linux-64"), "[target.linux-64]")
    dependencies.update(_table(linux.get("dependencies"), "[target.linux-64.dependencies]"))
    return dependencies


def _package_from_url(url: str) -> _LockedPackage:
    parsed = urlparse(url)
    filename = unquote(Path(parsed.path).name)
    if filename.endswith(".tar.bz2"):
        stem = filename[: -len(".tar.bz2")]
    elif filename.endswith(".conda"):
        stem = filename[: -len(".conda")]
    else:
        stem = filename
    parts = stem.rsplit("-", 2)
    if len(parts) != 3:
        return _LockedPackage(stem, None, None, url)
    name, version, build = parts
    return _LockedPackage(name, version, build, url)


def _package_from_source(raw: str) -> _LockedPackage:
    name = raw.split("[", 1)[0].split(" ", 1)[0].strip()
    separator = raw.find(" @ ")
    source = raw[separator + 3 :].strip() if separator >= 0 else "?"
    return _LockedPackage(name, None, None, source, is_source=True)


def _load_linux_lock(path: Path) -> Dict[str, _LockedPackage]:
    try:
        data = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise PlanError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise PlanError(f"{path} is not a Pixi lock mapping")

    environments = _table(data.get("environments"), "pixi.lock environments")
    environment = environments.get("default")
    if environment is None and len(environments) == 1:
        environment = next(iter(environments.values()))
    environment = _table(environment, "pixi.lock default environment")
    packages = _table(environment.get("packages"), "pixi.lock environment packages")
    if "linux-64" not in packages:
        raise PlanError("pixi.lock has no default-environment linux-64 solve")
    entries = packages["linux-64"]
    if not isinstance(entries, list):
        raise PlanError("pixi.lock linux-64 packages must be a list")

    result: Dict[str, _LockedPackage] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if isinstance(entry.get("conda"), str):
            package = _package_from_url(entry["conda"])
        elif isinstance(entry.get("conda_source"), str):
            package = _package_from_source(entry["conda_source"])
        elif isinstance(entry.get("pypi"), str):
            raise PlanError("pixi.lock contains a PyPI package; Biopixi L1 requires Conda identities")
        else:
            continue
        result[package.name] = package
    return result


def _local_package(name: str, specification: Mapping[str, Any], project_root: Path) -> Tuple[str, Path]:
    raw_path = specification.get("path")
    if not isinstance(raw_path, str):
        raise PlanError(f"{name}: path must be a string")
    if Path(raw_path).is_absolute():
        raise PlanError(f"{name}: a Biopixi L1 path dependency must be relative")

    try:
        package_path = (project_root / raw_path).resolve(strict=True)
    except OSError as exc:
        raise PlanError(f"{name}: local package path {raw_path!r} does not exist") from exc
    if project_root != package_path and project_root not in package_path.parents:
        raise PlanError(f"{name}: local package path escapes the project root")
    package_manifest_path = package_path / "pixi.toml"
    package_manifest = _load_toml(package_manifest_path)
    package = _table(package_manifest.get("package"), f"{package_manifest_path} [package]")
    if package.get("name") != name:
        raise PlanError(f"{name}: local package declares name {package.get('name')!r}")
    version = package.get("version")
    if not isinstance(version, str) or not version or any(c in version for c in "*<>=,|~^ "):
        raise PlanError(f"{name}: local package must declare one concrete string version")

    for section in ("run-dependencies", "host-dependencies", "build-dependencies"):
        values = _table(package.get(section), f"{package_manifest_path} [package.{section}]")
        for child_name, child in values.items():
            if isinstance(child, dict) and "path" in child:
                raise PlanError(
                    f"{name}: recursive local dependency {child_name!r} is not supported; "
                    "publish the local package graph first"
                )
    return version, package_path


def _registry_channel(name: str, specification: Any) -> Optional[str]:
    if isinstance(specification, str):
        return None
    if not isinstance(specification, dict):
        raise PlanError(f"{name}: unsupported dependency specification {specification!r}")
    unsupported = {key for key in ("git", "url") if key in specification}
    if unsupported:
        raise PlanError(f"{name}: {next(iter(unsupported))}= dependencies are outside Biopixi L1")
    channel = specification.get("channel")
    if channel is not None and not isinstance(channel, str):
        raise PlanError(f"{name}: channel must be a string")
    return channel


def load_build_plan(project: Path | str = ".", local_channel: Optional[Path | str] = None) -> BuildPlan:
    """Load a deterministic linux-64 mulled build plan from a Pixi project."""

    requested = Path(project).expanduser()
    manifest_path = requested if requested.name == "pixi.toml" else requested / "pixi.toml"
    try:
        manifest_path = manifest_path.resolve(strict=True)
    except OSError as exc:
        raise PlanError(f"no pixi.toml found at {manifest_path}") from exc
    project_root = manifest_path.parent
    lock_path = project_root / "pixi.lock"
    if not lock_path.is_file():
        raise PlanError(f"no pixi.lock found beside {manifest_path}; run `pixi lock` first")

    manifest = _load_toml(manifest_path)
    workspace = _table(manifest.get("workspace"), "[workspace]")
    platforms = workspace.get("platforms")
    if not isinstance(platforms, list) or "linux-64" not in platforms:
        raise PlanError("[workspace].platforms must include linux-64")
    channels_value = workspace.get("channels")
    if not isinstance(channels_value, list) or not channels_value:
        raise PlanError("[workspace].channels must be a non-empty list")
    if not all(isinstance(channel, str) for channel in channels_value):
        raise PlanError("mulled-biopixi supports string entries in [workspace].channels")
    channels = tuple(channels_value)

    if manifest.get("pypi-dependencies"):
        raise PlanError("[pypi-dependencies] is outside Biopixi L1")
    target_tables = _table(manifest.get("target"), "[target]")
    linux = _table(target_tables.get("linux-64"), "[target.linux-64]")
    if linux.get("pypi-dependencies"):
        raise PlanError("[target.linux-64.pypi-dependencies] is outside Biopixi L1")

    dependencies = _effective_dependencies(manifest)
    if not dependencies:
        raise PlanError("the effective linux-64 environment has no Conda dependencies")
    locked = _load_linux_lock(lock_path)

    targets: List[Target] = []
    for name in sorted(dependencies):
        specification = dependencies[name]
        if isinstance(specification, dict) and "path" in specification:
            version, package_path = _local_package(name, specification, project_root)
            package = locked.get(name)
            if package is None or not package.is_source:
                raise PlanError(f"pixi.lock is stale: no resolved linux-64 source package for {name}")
            locked_path = (project_root / package.source).resolve()
            if locked_path != package_path:
                raise PlanError(
                    f"pixi.lock is stale: {name} resolves from {package.source!r}, "
                    f"not {str(specification['path'])!r}"
                )
            target = Target(name=name, version=version, local_path=package_path)
        else:
            package = locked.get(name)
            if package is None or package.version is None:
                raise PlanError(f"pixi.lock is stale: no resolved linux-64 package for {name}")
            channel = _registry_channel(name, specification)
            target = Target(
                name=name,
                version=package.version,
                build=package.build,
                channel=channel,
            )
        targets.append(target)

    channel_path = (
        Path(local_channel).expanduser() if local_channel is not None else project_root / ".mulled-biopixi" / "channel"
    ).resolve()
    return BuildPlan(
        project_root=project_root,
        manifest_path=manifest_path,
        lock_path=lock_path,
        local_channel=channel_path,
        channels=channels,
        targets=tuple(targets),
    )
