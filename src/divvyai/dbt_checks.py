"""Run the existing warehouse checks and return a combined process result."""

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run dbt receipt-freshness and station snapshot quality checks."
    )
    parser.add_argument("--project-dir", type=Path, default=Path("dbt"))
    parser.add_argument("--profiles-dir", type=Path, required=True)
    parser.add_argument(
        "--dbt-executable",
        type=Path,
        default=Path(sys.executable).with_name("dbt"),
        help="dbt executable; defaults to the active Python environment's dbt.",
    )
    args = parser.parse_args(argv)
    for flag in ("project_dir", "profiles_dir"):
        if not getattr(args, flag).is_dir():
            parser.error(f"--{flag.replace('_', '-')} must name an existing directory")

    checks = (
        (
            "source_freshness",
            ["source", "freshness", "--select", "source:divvy.station_records"],
        ),
        ("snapshot_quality", ["test", "--select", "assert_station_snapshot_quality"]),
    )
    failed: list[str] = []
    for name, command in checks:
        print(json.dumps({"event": "dbt_check_started", "check": name}), flush=True)
        try:
            result = subprocess.run(
                [
                    str(args.dbt_executable),
                    "--warn-error-options",
                    json.dumps(
                        {"error": ["NoNodesForSelectionCriteria", "NothingToDo"]}
                    ),
                    *command,
                    "--project-dir",
                    str(args.project_dir.resolve()),
                    "--profiles-dir",
                    str(args.profiles_dir.resolve()),
                ],
                check=False,
                timeout=120,
            )
            return_code = result.returncode
            reason = "completed"
        except subprocess.TimeoutExpired:
            return_code = 1
            reason = "timed_out"
        except OSError as error:
            return_code = 1
            reason = f"could_not_start: {error}"

        if return_code != 0:
            failed.append(name)
        print(
            json.dumps(
                {
                    "event": "dbt_check_finished",
                    "check": name,
                    "severity": "ERROR" if return_code else "INFO",
                    "return_code": return_code,
                    "reason": reason,
                }
            ),
            flush=True,
        )

    print(
        json.dumps(
            {
                "event": "dbt_checks_completed",
                "severity": "ERROR" if failed else "INFO",
                "status": "failed" if failed else "passed",
                "failed_checks": failed,
            }
        ),
        flush=True,
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
