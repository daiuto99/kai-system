#!/usr/bin/env python3
"""HARDEN-1 ruff baseline: fail CI only for findings not already recorded."""

import argparse
import json
from pathlib import Path


def finding_key(service: str, finding: dict) -> dict:
    location = finding["location"]
    return {"service": service, "filename": finding["filename"], "code": finding["code"], "row": location["row"], "column": location["column"]}


def load_report(service: str, report_path: Path) -> list[dict]:
    return [finding_key(service, finding) for finding in json.loads(report_path.read_text())]


def read_baseline(path: Path) -> set[str]:
    data = json.loads(path.read_text())
    if data.get("schema_version") != 1:
        raise ValueError("unsupported ruff baseline schema")
    return {json.dumps(finding, sort_keys=True) for finding in data["findings"]}


def write_baseline(path: Path, reports: list[tuple[str, Path]]) -> None:
    findings = [finding for service, report_path in reports for finding in load_report(service, report_path)]
    findings.sort(key=lambda item: (item["service"], item["filename"], item["row"], item["column"], item["code"]))
    path.write_text(json.dumps({"schema_version": 1, "findings": findings}, indent=2) + "\n")


def verify(baseline_path: Path, reports: list[tuple[str, Path]]) -> list[dict]:
    baseline = read_baseline(baseline_path)
    current = [finding for service, report_path in reports for finding in load_report(service, report_path)]
    return [finding for finding in current if json.dumps(finding, sort_keys=True) not in baseline]


def parse_report(value: str) -> tuple[str, Path]:
    service, separator, path = value.partition(":")
    if not separator or not service or not path:
        raise argparse.ArgumentTypeError("report must be SERVICE:PATH")
    return service, Path(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--report", action="append", type=parse_report, required=True)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    if args.write:
        write_baseline(args.baseline, args.report)
        print(f"Wrote {args.baseline}")
        return 0
    new_findings = verify(args.baseline, args.report)
    if new_findings:
        print("[FAIL] New ruff findings not in HARDEN-1 baseline:")
        for finding in new_findings:
            print(f"{finding['service']}:{finding['filename']}:{finding['row']}:{finding['column']} {finding['code']}")
        return 1
    print("[PASS] Ruff findings match HARDEN-1 baseline (no new violations).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
