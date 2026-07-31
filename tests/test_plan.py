from pathlib import Path

import pytest

from mulled_biopixi.plan import load_build_plan, PlanError

LOCK = """\
version: 7
environments:
  default:
    channels:
      - url: https://conda.anaconda.org/conda-forge/
      - url: https://conda.anaconda.org/bioconda/
    packages:
      linux-64:
        - conda: https://conda.anaconda.org/bioconda/linux-64/samtools-1.17-hd87286a_2.tar.bz2
        - conda: https://conda.anaconda.org/conda-forge/linux-64/bamtools-2.5.2-hdcf5f25_5.conda
        - conda_source: local-tool[abc123] @ ./recipes/local-tool
"""


def project(tmp_path: Path, manifest: str, lock: str = LOCK) -> Path:
    tmp_path.joinpath("pixi.toml").write_text(manifest)
    tmp_path.joinpath("pixi.lock").write_text(lock)
    return tmp_path


def test_registry_plan_is_pinned_and_sorted(tmp_path: Path):
    root = project(
        tmp_path,
        """\
[workspace]
channels = ["conda-forge", "bioconda"]
platforms = ["linux-64"]

[dependencies]
samtools = ">=1"
bamtools = "*"
""",
    )

    plan = load_build_plan(root)

    assert plan.target_string == ("bamtools=2.5.2--hdcf5f25_5,samtools=1.17--hd87286a_2")
    assert plan.mulled_channels == ("conda-forge", "bioconda")


def test_target_override_and_channel_prefix_are_preserved(tmp_path: Path):
    root = project(
        tmp_path,
        """\
[workspace]
channels = ["ome", "conda-forge"]
platforms = ["linux-64", "osx-arm64"]

[dependencies]
samtools = "1.16.*"

[target.linux-64.dependencies]
samtools = { version = "1.17", channel = "bioconda" }
""",
    )

    assert load_build_plan(root).target_string == "bioconda::samtools=1.17--hd87286a_2"


def test_local_package_uses_declared_version_and_file_channel(tmp_path: Path):
    recipe = tmp_path / "recipes" / "local-tool"
    recipe.mkdir(parents=True)
    recipe.joinpath("pixi.toml").write_text("""\
[package]
name = "local-tool"
version = "0.3.0"

[package.build]
backend = { name = "pixi-build-rattler-build", version = "*" }
""")
    root = project(
        tmp_path,
        """\
[workspace]
preview = ["pixi-build"]
channels = ["conda-forge"]
platforms = ["linux-64"]

[dependencies]
local-tool = { path = "./recipes/local-tool" }
""",
    )

    plan = load_build_plan(root)

    assert plan.target_string == "local-tool=0.3.0"
    assert plan.local_packages[0].local_path == recipe
    assert plan.mulled_channels == (
        (tmp_path / ".mulled-biopixi" / "channel").resolve().as_uri(),
        "conda-forge",
    )


def test_missing_linux_lock_is_rejected(tmp_path: Path):
    root = project(
        tmp_path,
        """\
[workspace]
channels = ["conda-forge"]
platforms = ["linux-64"]
[dependencies]
samtools = "*"
""",
        """\
environments:
  default:
    packages:
      osx-arm64: []
""",
    )

    with pytest.raises(PlanError, match="no default-environment linux-64 solve"):
        load_build_plan(root)


def test_recursive_path_dependency_is_an_explicit_limit(tmp_path: Path):
    recipe = tmp_path / "recipes" / "local-tool"
    recipe.mkdir(parents=True)
    recipe.joinpath("pixi.toml").write_text("""\
[package]
name = "local-tool"
version = "0.3.0"
[package.run-dependencies]
child = { path = "../child" }
""")
    root = project(
        tmp_path,
        """\
[workspace]
channels = ["conda-forge"]
platforms = ["linux-64"]
[dependencies]
local-tool = { path = "./recipes/local-tool" }
""",
    )

    with pytest.raises(PlanError, match="recursive local dependency"):
        load_build_plan(root)


def test_moved_local_package_makes_lock_stale(tmp_path: Path):
    recipe = tmp_path / "recipes" / "local-tool"
    recipe.mkdir(parents=True)
    recipe.joinpath("pixi.toml").write_text("""\
[package]
name = "local-tool"
version = "0.3.0"
""")
    root = project(
        tmp_path,
        """\
[workspace]
channels = ["conda-forge"]
platforms = ["linux-64"]
[dependencies]
local-tool = { path = "./recipes/local-tool" }
""",
        LOCK.replace("./recipes/local-tool", "./old/local-tool"),
    )

    with pytest.raises(PlanError, match="pixi.lock is stale"):
        load_build_plan(root)
