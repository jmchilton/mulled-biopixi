"""Command-line entry point."""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence

from .build import check_mulled_platform, publish_commands, publish_local_packages, run_mulled, SUPPORTED_COMMANDS
from .plan import load_build_plan, PlanError


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        prog="mulled-biopixi",
        description="Build a linux/amd64 mulled container from a Biopixi-compatible pixi.toml.",
    )
    value.add_argument(
        "project",
        nargs="?",
        default=".",
        help="Pixi project directory or pixi.toml (default: current directory)",
    )
    value.add_argument(
        "--local-channel",
        type=Path,
        help="indexed channel for L1 path packages (default: PROJECT/.mulled-biopixi/channel)",
    )
    value.add_argument("--pixi", default="pixi", help="Pixi executable (default: pixi)")
    value.add_argument(
        "--skip-local-publish",
        action="store_true",
        help="reuse packages already present in --local-channel",
    )
    value.add_argument(
        "--command",
        choices=SUPPORTED_COMMANDS,
        default="build",
        help="mulled/Involucro action (default: build)",
    )
    value.add_argument("--test", default="true", help="container test command (default: true)")
    value.add_argument("--use-mamba", action="store_true", help="ask mulled to install with Mamba")
    value.add_argument("--verbose", action="store_true")
    value.add_argument(
        "--dry-run",
        action="store_true",
        help="print publication and Involucro commands without building",
    )
    return value


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parser().parse_args(argv)
    try:
        plan = load_build_plan(args.project, args.local_channel)
        print(f"Project:  {plan.project_root}")
        print(f"Targets:  {plan.target_string}")
        print(f"Channels: {','.join(plan.mulled_channels)}")

        # Do this before publishing local packages: unsupported host/API combinations must fail
        # without leaving an expensive half-completed build behind.
        check_mulled_platform(dry_run=args.dry_run)
        if plan.local_packages and not args.skip_local_publish:
            if args.dry_run:
                for command in publish_commands(plan, args.pixi):
                    print(f"Would run: {shlex.join(command)}")
            else:
                publish_local_packages(plan, args.pixi)

        return run_mulled(
            plan,
            command=args.command,
            dry_run=args.dry_run,
            test=args.test,
            verbose=args.verbose,
            use_mamba=args.use_mamba,
        )
    except PlanError as exc:
        print(f"mulled-biopixi: error: {exc}", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as exc:
        print(f"mulled-biopixi: command failed with exit code {exc.returncode}", file=sys.stderr)
        return exc.returncode or 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
