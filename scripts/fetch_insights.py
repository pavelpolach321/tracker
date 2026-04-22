#!/usr/bin/env python3
"""Collect daily OSS repository insights and persist long-term history."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import pathlib
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect GitHub repository insights")
    parser.add_argument("--config", required=True, help="Path to config/repos.json")
    parser.add_argument("--out-dir", required=True, help="Output directory (e.g. data)")
    return parser.parse_args()


def load_repo_list(config_path: pathlib.Path) -> List[str]:
    data = json.loads(config_path.read_text(encoding="utf-8"))
    repos = data.get("repositories", [])
    if not isinstance(repos, list) or not repos:
        raise ValueError("config must include a non-empty 'repositories' list")
    for repo in repos:
        if not isinstance(repo, str) or "/" not in repo:
            raise ValueError(f"Invalid repository format: {repo!r}")
    return repos


def github_get_json(path: str, token: str | None) -> Any:
    url = f"https://api.github.com{path}"
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "oss-insights-tracker",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API error {exc.code} for {path}: {body}") from exc


def fetch_open_pr_count(repo: str, token: str | None) -> int:
    query = urllib.parse.quote_plus(f"repo:{repo} is:pr is:open")
    data = github_get_json(f"/search/issues?q={query}&per_page=1", token)
    return int(data.get("total_count", 0))


def fetch_latest_release(repo: str, token: str | None) -> str | None:
    try:
        data = github_get_json(f"/repos/{repo}/releases/latest", token)
    except RuntimeError as exc:
        # Not all repos have releases.
        if " 404 " in str(exc):
            return None
        raise
    tag = data.get("tag_name")
    return str(tag) if tag else None


def collect_repo_metrics(repo: str, token: str | None) -> Dict[str, Any]:
    repo_data = github_get_json(f"/repos/{repo}", token)

    return {
        "repository": repo,
        "captured_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "stars": int(repo_data.get("stargazers_count", 0)),
        "forks": int(repo_data.get("forks_count", 0)),
        "watchers": int(repo_data.get("subscribers_count", 0)),
        "open_issues": int(repo_data.get("open_issues_count", 0)),
        "open_pull_requests": fetch_open_pr_count(repo, token),
        "default_branch": repo_data.get("default_branch"),
        "pushed_at": repo_data.get("pushed_at"),
        "latest_release": fetch_latest_release(repo, token),
    }


def ensure_dirs(base: pathlib.Path) -> Dict[str, pathlib.Path]:
    snapshots_dir = base / "snapshots"
    reports_dir = base / "reports"
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)
    return {"snapshots": snapshots_dir, "reports": reports_dir}


def write_snapshot(snapshots_dir: pathlib.Path, items: List[Dict[str, Any]], day: dt.date) -> pathlib.Path:
    path = snapshots_dir / f"{day.isoformat()}.json"
    payload = {
        "date": day.isoformat(),
        "count": len(items),
        "repositories": items,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def upsert_history_csv(csv_path: pathlib.Path, items: List[Dict[str, Any]], day: dt.date) -> None:
    fieldnames = [
        "date",
        "repository",
        "stars",
        "forks",
        "watchers",
        "open_issues",
        "open_pull_requests",
        "pushed_at",
        "latest_release",
    ]

    rows: List[Dict[str, str]] = []
    if csv_path.exists():
        with csv_path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Remove existing rows for the same day to make reruns idempotent.
                if row.get("date") != day.isoformat():
                    rows.append({k: row.get(k, "") for k in fieldnames})

    for item in items:
        rows.append(
            {
                "date": day.isoformat(),
                "repository": item["repository"],
                "stars": str(item["stars"]),
                "forks": str(item["forks"]),
                "watchers": str(item["watchers"]),
                "open_issues": str(item["open_issues"]),
                "open_pull_requests": str(item["open_pull_requests"]),
                "pushed_at": str(item["pushed_at"] or ""),
                "latest_release": str(item["latest_release"] or ""),
            }
        )

    rows.sort(key=lambda r: (r["date"], r["repository"]))

    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def format_report(day: dt.date, items: List[Dict[str, Any]]) -> str:
    lines = [
        f"# OSS Insights Report - {day.isoformat()}",
        "",
        f"Tracked repositories: {len(items)}",
        "",
        "| Repository | Stars | Forks | Watchers | Open Issues | Open PRs | Latest Release | Last Push |",
        "|---|---:|---:|---:|---:|---:|---|---|",
    ]

    for item in sorted(items, key=lambda x: x["repository"]):
        lines.append(
            "| {repository} | {stars} | {forks} | {watchers} | {open_issues} | {open_pull_requests} | {latest_release} | {pushed_at} |".format(
                repository=item["repository"],
                stars=item["stars"],
                forks=item["forks"],
                watchers=item["watchers"],
                open_issues=item["open_issues"],
                open_pull_requests=item["open_pull_requests"],
                latest_release=item["latest_release"] or "-",
                pushed_at=item["pushed_at"] or "-",
            )
        )

    lines.extend(
        [
            "",
            "This report is generated from daily snapshots in `data/snapshots/` and can be analyzed over any period.",
        ]
    )

    return "\n".join(lines) + "\n"


def write_reports(reports_dir: pathlib.Path, day: dt.date, report_content: str) -> None:
    dated_path = reports_dir / f"{day.isoformat()}.md"
    latest_path = reports_dir / "latest.md"
    dated_path.write_text(report_content, encoding="utf-8")
    latest_path.write_text(report_content, encoding="utf-8")


def main() -> int:
    args = parse_args()
    token = os.getenv("GH_TOKEN")

    config_path = pathlib.Path(args.config)
    out_dir = pathlib.Path(args.out_dir)
    today = dt.datetime.now(dt.timezone.utc).date()

    repos = load_repo_list(config_path)
    paths = ensure_dirs(out_dir)

    items = [collect_repo_metrics(repo, token) for repo in repos]

    snapshot_path = write_snapshot(paths["snapshots"], items, today)
    history_path = out_dir / "metrics_history.csv"
    upsert_history_csv(history_path, items, today)

    report = format_report(today, items)
    write_reports(paths["reports"], today, report)

    print(f"Wrote snapshot: {snapshot_path}")
    print(f"Updated history: {history_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
