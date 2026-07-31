# mulled-biopixi

[![Python CI](https://github.com/jmchilton/mulled-biopixi/actions/workflows/ci.yml/badge.svg)](https://github.com/jmchilton/mulled-biopixi/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/mulled-biopixi.svg)](https://pypi.org/project/mulled-biopixi/)
[![Python versions](https://img.shields.io/pypi/pyversions/mulled-biopixi.svg)](https://pypi.org/project/mulled-biopixi/)

`mulled-biopixi` turns a Biopixi-compatible Pixi environment into a
local mulled container build. It reads `pixi.toml` from the current directory by default, uses the
`linux-64` solve in `pixi.lock` to pin the direct Conda dependencies, publishes direct L1 path
packages into an indexed local channel, and calls Galaxy's `mull_targets` implementation.

## Installation

Install the latest release from [PyPI](https://pypi.org/project/mulled-biopixi/) with `uv`:

```console
uv tool install mulled-biopixi
```

Alternatively, use `pipx install mulled-biopixi`. Upgrade an existing `uv` installation with
`uv tool upgrade mulled-biopixi`.

Then run it in a Biopixi-compatible project:

```console
mulled-biopixi --dry-run
mulled-biopixi --command build-and-test --test 'samtools --version'
```

The dry run is useful on any host. A real build needs Docker. With current stable
`galaxy-tool-util`, the CLI downloads a checksum-pinned Involucro 1.2.0 into the project's ignored
`.mulled-biopixi/` cache and injects `--platform linux/amd64`, allowing Docker Desktop to emulate
the Biopixi profile platform on Apple Silicon. Galaxy's development implementation exposes this
platform directly, and the compatibility path switches off automatically when that API is present.
The shim also pre-pulls the amd64 Conda and destination base images with the modern Docker CLI;
this avoids Involucro's older embedded Docker client attempting a pull against newer daemons. The
pre-pull safeguard remains active when Galaxy supplies native platform support because its current
Involucro 1.2.0 binary has the same Docker 29 limitation.

## Translation contract

- The manifest selects the effective direct dependencies: `[dependencies]` plus Linux target
  overrides from `[target.linux-64.dependencies]`.
- The lock supplies exact registry-package versions and build strings. Manifest ranges alone are
  not reproducible container inputs.
- Explicit dependency channel qualifiers are preserved in the mulled package name.
- A direct path dependency gets its concrete version from its package `pixi.toml`, is built with
  `pixi publish --target-channel ... --target-platform linux-64`, and is then resolved by mulled
  from that local `file://` channel.
- Only roots are passed to mulled. Conda resolves their transitive closure inside the image, which
  matches the Biopixi L4 target-set rule.

This is intentionally not another complete Biopixi validator. It rejects unsupported shapes when
they would make the build ambiguous, while relying on the caller's L1-or-higher assumption for the
full profile contract.

## Current limits

- Recursive path-package graphs are detected but not published. Supporting them needs a proper
  topological package publication workflow and a decision about rewriting source dependencies.
- The local channel lives at `.mulled-biopixi/channel` by default and is not pushed anywhere.
- `osx-arm64` can be present in the workspace, but container construction always uses the
  profile's `linux-64` environment and produces `linux/amd64`. Foreign-architecture builds depend
  on the Docker daemon's binfmt/QEMU support and will be slower than native builds.
- The integration uses Galaxy's importable `mull_targets` function. That API is practical and
  tested within Galaxy, but is not documented as a separately versioned public library contract.
- Galaxy's base-image selection performs one full Conda metadata search per root. Under amd64
  emulation this can dominate build time even when the eventual package solve and image wrap are
  quick.

## Development

Create the locked development environment and run the complete local CI suite:

```console
uv sync
make ci
```

The individual commands are `make format`, `make lint`, `make typing`, `make test`, and
`make dist`. Install the repository hook with `make pre-commit`. Maintainers can follow the
[release checklist](https://github.com/jmchilton/mulled-biopixi/blob/main/RELEASING.md) to publish a
version through PyPI Trusted Publishing.

This project is licensed under the [MIT License](LICENSE).
