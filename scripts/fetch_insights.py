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


class GitHubApiError(RuntimeError):
    def __init__(self, status_code: int, path: str, body: str):
        super().__init__(f"GitHub API error {status_code} for {path}: {body}")
        self.status_code = status_code
        self.path = path


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
        raise GitHubApiError(exc.code, path, body) from exc


def fetch_open_pr_count(repo: str, token: str | None) -> int:
    query = urllib.parse.quote_plus(f"repo:{repo} is:pr is:open")
    data = github_get_json(f"/search/issues?q={query}&per_page=1", token)
    return int(data.get("total_count", 0))


def fetch_latest_release(repo: str, token: str | None) -> str | None:
    try:
        data = github_get_json(f"/repos/{repo}/releases/latest", token)
    except GitHubApiError as exc:
        # Not all repos have releases.
        if exc.status_code == 404:
            return None
        raise
    tag = data.get("tag_name")
    return str(tag) if tag else None


def fetch_traffic(repo: str, token: str | None) -> Dict[str, Any]:
    traffic = {
        "traffic_status": "unavailable",
        "traffic_error": "unknown",
        "views_14d_total": None,
        "views_14d_unique": None,
        "clones_14d_total": None,
        "clones_14d_unique": None,
        "traffic_daily": [],
    }

    if not token:
        traffic["traffic_error"] = "missing_token"
        return traffic

    try:
        views = github_get_json(f"/repos/{repo}/traffic/views", token)
        clones = github_get_json(f"/repos/{repo}/traffic/clones", token)
    except GitHubApiError as exc:
        if exc.status_code in (403, 404):
            traffic["traffic_error"] = f"http_{exc.status_code}"
            return traffic
        raise

    traffic["traffic_status"] = "ok"
    traffic["traffic_error"] = ""
    traffic["views_14d_total"] = int(views.get("count", 0))
    traffic["views_14d_unique"] = int(views.get("uniques", 0))
    traffic["clones_14d_total"] = int(clones.get("count", 0))
    traffic["clones_14d_unique"] = int(clones.get("uniques", 0))

    views_by_date = {
        str(v.get("timestamp", ""))[:10]: {
            "views_total": int(v.get("count", 0)),
            "views_unique": int(v.get("uniques", 0)),
        }
        for v in views.get("views", [])
        if str(v.get("timestamp", ""))
    }
    clones_by_date = {
        str(c.get("timestamp", ""))[:10]: {
            "clones_total": int(c.get("count", 0)),
            "clones_unique": int(c.get("uniques", 0)),
        }
        for c in clones.get("clones", [])
        if str(c.get("timestamp", ""))
    }

    merged_dates = sorted(set(views_by_date.keys()) | set(clones_by_date.keys()))
    traffic["traffic_daily"] = [
        {
            "date": date,
            "views_total": views_by_date.get(date, {}).get("views_total", 0),
            "views_unique": views_by_date.get(date, {}).get("views_unique", 0),
            "clones_total": clones_by_date.get(date, {}).get("clones_total", 0),
            "clones_unique": clones_by_date.get(date, {}).get("clones_unique", 0),
        }
        for date in merged_dates
    ]
    return traffic


def fetch_popular_sources(repo: str, token: str | None) -> Dict[str, Any]:
    sources = {
        "referrers_status": "unavailable",
        "referrers_error": "unknown",
        "popular_referrers": [],
        "paths_status": "unavailable",
        "paths_error": "unknown",
        "popular_paths": [],
    }

    if not token:
        sources["referrers_error"] = "missing_token"
        sources["paths_error"] = "missing_token"
        return sources

    try:
        referrers = github_get_json(f"/repos/{repo}/traffic/popular/referrers", token)
        sources["referrers_status"] = "ok"
        sources["referrers_error"] = ""
        sources["popular_referrers"] = [
            {
                "referrer": str(item.get("referrer", "")),
                "count": int(item.get("count", 0)),
                "uniques": int(item.get("uniques", 0)),
            }
            for item in referrers
            if str(item.get("referrer", ""))
        ]
    except GitHubApiError as exc:
        if exc.status_code in (403, 404):
            sources["referrers_error"] = f"http_{exc.status_code}"
        else:
            raise

    try:
        paths = github_get_json(f"/repos/{repo}/traffic/popular/paths", token)
        sources["paths_status"] = "ok"
        sources["paths_error"] = ""
        sources["popular_paths"] = [
            {
                "path": str(item.get("path", "")),
                "title": str(item.get("title", "")),
                "count": int(item.get("count", 0)),
                "uniques": int(item.get("uniques", 0)),
            }
            for item in paths
            if str(item.get("path", ""))
        ]
    except GitHubApiError as exc:
        if exc.status_code in (403, 404):
            sources["paths_error"] = f"http_{exc.status_code}"
        else:
            raise

    return sources


def collect_repo_metrics(repo: str, token: str | None) -> Dict[str, Any]:
    repo_data = github_get_json(f"/repos/{repo}", token)
    traffic = fetch_traffic(repo, token)
    sources = fetch_popular_sources(repo, token)

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
        **traffic,
        **sources,
    }


def ensure_dirs(base: pathlib.Path) -> Dict[str, pathlib.Path]:
    snapshots_dir = base / "snapshots"
    reports_dir = base / "reports"
    metrics_dir = base / "metrics"
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)
    metrics_dir.mkdir(parents=True, exist_ok=True)
    return {"snapshots": snapshots_dir, "reports": reports_dir, "metrics": metrics_dir}


def build_run_summary(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    traffic_ok = sum(1 for i in items if i.get("traffic_status") == "ok")
    referrers_ok = sum(1 for i in items if i.get("referrers_status") == "ok")
    paths_ok = sum(1 for i in items if i.get("paths_status") == "ok")
    return {
        "status": "success",
        "completed_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "repositories_total": len(items),
        "repositories_with_traffic": traffic_ok,
        "repositories_with_referrers": referrers_ok,
        "repositories_with_paths": paths_ok,
    }


def write_snapshot(
    snapshots_dir: pathlib.Path,
    items: List[Dict[str, Any]],
    day: dt.date,
    run_summary: Dict[str, Any],
) -> pathlib.Path:
    path = snapshots_dir / f"{day.isoformat()}.json"
    payload = {
        "date": day.isoformat(),
        "run": run_summary,
        "count": len(items),
        "repositories": items,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def upsert_repo_history_csv(csv_path: pathlib.Path, item: Dict[str, Any], day: dt.date) -> None:
    fieldnames = [
        "date",
        "stars",
        "forks",
        "watchers",
        "open_issues",
        "open_pull_requests",
        "pushed_at",
        "latest_release",
        "traffic_status",
        "traffic_error",
        "views_14d_total",
        "views_14d_unique",
        "clones_14d_total",
        "clones_14d_unique",
    ]

    rows: List[Dict[str, str]] = []
    if csv_path.exists():
        with csv_path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("date") != day.isoformat():
                    rows.append({k: row.get(k, "") for k in fieldnames})

    rows.append(
        {
            "date": day.isoformat(),
            "stars": str(item["stars"]),
            "forks": str(item["forks"]),
            "watchers": str(item["watchers"]),
            "open_issues": str(item["open_issues"]),
            "open_pull_requests": str(item["open_pull_requests"]),
            "pushed_at": str(item["pushed_at"] or ""),
            "latest_release": str(item["latest_release"] or ""),
            "traffic_status": str(item.get("traffic_status") or "unavailable"),
            "traffic_error": str(item.get("traffic_error") or ""),
            "views_14d_total": str(item.get("views_14d_total") or ""),
            "views_14d_unique": str(item.get("views_14d_unique") or ""),
            "clones_14d_total": str(item.get("clones_14d_total") or ""),
            "clones_14d_unique": str(item.get("clones_14d_unique") or ""),
        }
    )

    rows.sort(key=lambda r: r["date"])

    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def upsert_all_history_csvs(metrics_dir: pathlib.Path, items: List[Dict[str, Any]], day: dt.date) -> None:
    for item in items:
        owner, name = item["repository"].split("/", 1)
        owner_dir = metrics_dir / owner
        owner_dir.mkdir(parents=True, exist_ok=True)
        upsert_repo_history_csv(owner_dir / f"{name}.csv", item, day)



def format_report(day: dt.date, items: List[Dict[str, Any]]) -> str:
    lines = [
        f"# OSS Insights Report - {day.isoformat()}",
        "",
        f"Tracked repositories: {len(items)}",
        "",
        "| Repository | Stars | Forks | Watchers | Open Issues | Open PRs | Views (14d) | Unique Views (14d) | Clones (14d) | Unique Clones (14d) | Latest Release | Last Push | Traffic Status |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---|",
    ]

    for item in sorted(items, key=lambda x: x["repository"]):
        if item.get("traffic_status") == "ok":
            traffic_status_display = "ok"
        else:
            traffic_error = item.get("traffic_error") or "unknown"
            traffic_status_display = f"unavailable ({traffic_error})"

        lines.append(
            "| {repository} | {stars} | {forks} | {watchers} | {open_issues} | {open_pull_requests} | {views_14d_total} | {views_14d_unique} | {clones_14d_total} | {clones_14d_unique} | {latest_release} | {pushed_at} | {traffic_status} |".format(
                repository=item["repository"],
                stars=item["stars"],
                forks=item["forks"],
                watchers=item["watchers"],
                open_issues=item["open_issues"],
                open_pull_requests=item["open_pull_requests"],
                views_14d_total=item.get("views_14d_total") if item.get("views_14d_total") is not None else "-",
                views_14d_unique=item.get("views_14d_unique") if item.get("views_14d_unique") is not None else "-",
                clones_14d_total=item.get("clones_14d_total") if item.get("clones_14d_total") is not None else "-",
                clones_14d_unique=item.get("clones_14d_unique") if item.get("clones_14d_unique") is not None else "-",
                latest_release=item["latest_release"] or "-",
                pushed_at=item["pushed_at"] or "-",
                traffic_status=traffic_status_display,
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
    run_summary = build_run_summary(items)

    snapshot_path = write_snapshot(paths["snapshots"], items, today, run_summary)
    upsert_all_history_csvs(paths["metrics"], items, today)

    report = format_report(today, items)
    write_reports(paths["reports"], today, report)

    print(f"Wrote snapshot: {snapshot_path}")
    print(f"Updated metrics: {paths['metrics']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
