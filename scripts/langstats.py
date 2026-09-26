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

TOP_N = 6          # languages to list
# Private repos are counted when a token that can see them is supplied via
# LANGSTATS_TOKEN. Only the language totals are used: no repo name, count,
# description or byte figure from a private repo reaches the chart or the
# logs. Note the aggregate is still a disclosure — a language that exists
# only in a private repo becomes visible by appearing at all.
INCLUDE_PRIVATE = True

# A 92 MB JavaScript repo shouldn't drown out six Kotlin ones, so blend raw
# byte share with how many repos the language shows up in (both weights
# together should add up to 1.0; 1/0 is pure bytes, 0/1 is pure repo count).
SIZE_WEIGHT = 0.85
COUNT_WEIGHT = 0.15

# This repo only holds the generator, so counting it would let the chart
# measure itself. Names are matched case-insensitively.
EXCLUDE_REPOS = {"mks-01"}

# Build-system and markup noise that says nothing about what MKS writes.
EXCLUDE = {
    "HTML", "CSS", "SCSS", "Makefile", "CMake", "Dockerfile", "Batchfile",
    "Starlark", "Roff", "TeX", "Vim Script", "PowerShell", "Ruby",
}

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
    token = os.environ.get("LANGSTATS_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def repo_page(page):
    """One page of owned repos, including private ones when the token allows."""
    if INCLUDE_PRIVATE and os.environ.get("LANGSTATS_TOKEN"):
        try:
            return api(
                "/user/repos?per_page=100&affiliation=owner"
                f"&visibility=all&page={page}"
            )
        except urllib.error.HTTPError as exc:
            if exc.code not in (401, 403):
                raise
            print("token cannot list private repos, using public only", file=sys.stderr)
    return api(f"/users/{USER}/repos?per_page=100&type=owner&page={page}")


def collect():
    """Return {language: total_bytes}, {language: repo_count}."""
    totals, counts = {}, {}
    page = 1
    while True:
        repos = repo_page(page)
        if not repos:
            break
        for repo in repos:
            if repo["fork"]:          # someone else's code
                continue
            if repo.get("archived"):
                continue
            if repo.get("private") and not INCLUDE_PRIVATE:
                continue
            if repo["name"].lower() in EXCLUDE_REPOS:
                continue
            langs = api(f"/repos/{repo['full_name']}/languages")
            langs = {k: v for k, v in langs.items() if k not in EXCLUDE}
            if not langs:
                continue
            for name, size in langs.items():
                totals[name] = totals.get(name, 0) + size
                counts[name] = counts.get(name, 0) + 1
        page += 1
    return totals, counts


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


TERM_WIDTH = 80    # assumed terminal width, for column layout
COL_GAP = 2        # spaces between columns, matching real `ls`


def columnize(names, width=TERM_WIDTH, gap=COL_GAP):
    """Lay names out the way `ls` does: down each column, then across,
    using as many columns as fit the assumed terminal width."""
    n = len(names)
    for cols in range(min(n, width), 0, -1):
        rows = -(-n // cols)
        if (cols - 1) * rows >= n:
            continue   # trailing column would be empty at this column count
        col_widths = [
            max(len(names[c * rows + r]) for r in range(rows)
                if c * rows + r < n)
            for c in range(cols)
        ]
        total = sum(col_widths) + gap * (cols - 1)
        if total <= width:
            lines = []
            for r in range(rows):
                cells = []
                for c in range(cols):
                    i = c * rows + r
                    if i < n:
                        cells.append(names[i].ljust(col_widths[c]))
                lines.append((" " * gap).join(cells).rstrip())
            return lines
    return names


def render_readme_block(ranked):
    # Plain text in a code block, laid out in `ls`-style columns (down each
    # column, then across) rather than one line of names: no image to
    # cache-bust, nothing that can fail to render.
    lines = columnize([name for name, _ in ranked])
    return "\n".join([
        START,
        "```console",
        "$ ls ~/languages",
        *lines,
        "```",
        END,
    ])


def main():
    try:
        totals, counts = collect()
    except urllib.error.HTTPError as exc:
        print(f"github api error: {exc.code} {exc.reason}", file=sys.stderr)
        return 1
    if not totals:
        print("no language data found", file=sys.stderr)
        return 1

    ranked = rank(totals, counts)
    block = render_readme_block(ranked)

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
