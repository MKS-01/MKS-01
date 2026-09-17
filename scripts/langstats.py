#!/usr/bin/env python3
"""Rebuild the language-usage block in README.md from GitHub repo language bytes.

Talks only to api.github.com and rewrites the text between the langstats
markers, so the README has no third-party render server in its critical path.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request

USER = os.environ.get("LANGSTATS_USER", "MKS-01")
README = os.environ.get("LANGSTATS_README", "README.md")

TOP_N = 8          # languages to list
BAR_WIDTH = 18     # characters in the bar
INCLUDE_FORKS = False

# A 92 MB JavaScript repo shouldn't drown out six Kotlin ones, so blend raw
# byte share with how many repos the language shows up in (both weights
# together should add up to 1.0; 1/0 is pure bytes, 0/1 is pure repo count).
SIZE_WEIGHT = 0.85
COUNT_WEIGHT = 0.15

# Build-system and markup noise that says nothing about what MKS writes.
EXCLUDE = {
    "HTML", "CSS", "SCSS", "Makefile", "CMake", "Dockerfile", "Batchfile",
    "Starlark", "Roff", "TeX", "Vim Script", "PowerShell", "Ruby",
}

FILLED, EMPTY = "█", "░"
START = "<!-- langstats:start -->"
END = "<!-- langstats:end -->"


def api(path):
    req = urllib.request.Request(
        f"https://api.github.com{path}",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "langstats",
        },
    )
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def collect():
    """Return {language: total_bytes}, {language: repo_count}, repos_scanned."""
    totals, counts, scanned = {}, {}, 0
    page = 1
    while True:
        repos = api(f"/users/{USER}/repos?per_page=100&type=owner&page={page}")
        if not repos:
            break
        for repo in repos:
            if repo["fork"] and not INCLUDE_FORKS:
                continue
            if repo.get("archived") or repo.get("private"):
                continue
            langs = api(f"/repos/{USER}/{repo['name']}/languages")
            langs = {k: v for k, v in langs.items() if k not in EXCLUDE}
            if not langs:
                continue
            scanned += 1
            for name, size in langs.items():
                totals[name] = totals.get(name, 0) + size
                counts[name] = counts.get(name, 0) + 1
        page += 1
    return totals, counts, scanned


def rank(totals, counts):
    """Blend byte share and repo spread into a percentage per language."""
    total_bytes = sum(totals.values()) or 1
    total_repos = sum(counts.values()) or 1
    scores = {
        name: ((size / total_bytes) ** SIZE_WEIGHT)
        * ((counts[name] / total_repos) ** COUNT_WEIGHT)
        for name, size in totals.items()
    }
    top = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:TOP_N]
    shown = sum(score for _, score in top) or 1
    return [(name, 100 * score / shown) for name, score in top]


def render(ranked, scanned, total_bytes):
    width = max(len(name) for name, _ in ranked)
    lines = [START, "<pre>", "$ ls -l ~/languages", ""]
    for name, pct in ranked:
        filled = round(pct / 100 * BAR_WIDTH)
        bar = FILLED * filled + EMPTY * (BAR_WIDTH - filled)
        lines.append(f"{name.ljust(width)}  {bar}  {pct:4.1f}%")
    mb = total_bytes / 1_000_000
    lines += [
        "",
        f"across {scanned} repos · {mb:.1f} MB analyzed",
        "</pre>",
        END,
    ]
    return "\n".join(lines)


def main():
    try:
        totals, counts, scanned = collect()
    except urllib.error.HTTPError as exc:
        print(f"github api error: {exc.code} {exc.reason}", file=sys.stderr)
        return 1
    if not totals:
        print("no language data found", file=sys.stderr)
        return 1

    block = render(rank(totals, counts), scanned, sum(totals.values()))

    with open(README, encoding="utf-8") as fh:
        readme = fh.read()
    if START not in readme or END not in readme:
        print(f"markers {START} / {END} not found in {README}", file=sys.stderr)
        return 1
    updated = re.sub(
        re.escape(START) + r".*?" + re.escape(END), lambda _: block, readme, flags=re.S
    )

    if updated == readme:
        print("no change")
        return 0
    with open(README, "w", encoding="utf-8") as fh:
        fh.write(updated)
    print("updated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
