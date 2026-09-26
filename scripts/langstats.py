#!/usr/bin/env python3
"""Rebuild the language-usage block in README.md from this week's commits.

Talks only to api.github.com and rewrites the text between the langstats
markers, so the README has no third-party render server in its critical path.

This looks at *recent activity*, not lifetime totals: it walks commits
authored by USER in the last WINDOW_DAYS across all owned repos, maps each
changed file's extension to a language, and ranks by lines touched. A repo
that's 90% JavaScript by history but untouched this week contributes nothing;
one stray Kotlin fix this week outranks it. That also means a quiet week
shows fewer languages, or none at all, and that's shown honestly rather than
padded out with old totals.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

USER = os.environ.get("LANGSTATS_USER", "MKS-01")
README = os.environ.get("LANGSTATS_README", "README.md")

TOP_N = 6          # languages to list
WINDOW_DAYS = 7    # how far back to look for commits

# Private repos are counted when a token that can see them is supplied via
# LANGSTATS_TOKEN. Only language names and line-change counts are used: no
# repo name, commit message, or diff content from a private repo reaches the
# README or the logs. Note the aggregate is still a disclosure — a language
# that exists only in a private repo becomes visible by appearing at all.
INCLUDE_PRIVATE = True

# This repo only holds the generator, so counting it would let the chart
# measure itself. Names are matched case-insensitively.
EXCLUDE_REPOS = {"mks-01"}

# Bounds the number of per-commit API calls a single run makes, so a heavy
# week (or a bulk-import commit) can't blow the run past GitHub's rate limit
# or turn a weekly job into a long one.
MAX_COMMITS = 250

# Extension -> language, covering what MKS actually writes. Deliberately
# narrow and extension-only (no content sniffing like GitHub's own Linguist),
# so anything not listed here — markup, build config, generated files,
# lockfiles — is silently left out rather than guessed at.
EXT_LANG = {
    ".py": "Python",
    ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript",
    ".go": "Go",
    ".kt": "Kotlin", ".kts": "Kotlin",
    ".java": "Java",
    ".swift": "Swift",
    ".c": "C", ".h": "C",
    ".cpp": "C++", ".cc": "C++", ".cxx": "C++", ".hpp": "C++", ".hh": "C++",
    ".m": "Objective-C", ".mm": "Objective-C",
    ".sh": "Shell", ".bash": "Shell", ".zsh": "Shell",
    ".rs": "Rust",
    ".php": "PHP",
    ".cs": "C#",
    ".dart": "Dart",
    ".lua": "Lua",
    ".scala": "Scala",
    ".rb": "Ruby",
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
    """One page of owned repos, sorted most-recently-pushed first so the
    commit budget below goes to repos likely to have this week's activity,
    including private ones when the token allows."""
    if INCLUDE_PRIVATE and os.environ.get("LANGSTATS_TOKEN"):
        try:
            return api(
                "/user/repos?per_page=100&affiliation=owner&visibility=all"
                f"&sort=pushed&direction=desc&page={page}"
            )
        except urllib.error.HTTPError as exc:
            if exc.code not in (401, 403):
                raise
            print("token cannot list private repos, using public only", file=sys.stderr)
    return api(
        f"/users/{USER}/repos?per_page=100&type=owner"
        f"&sort=pushed&direction=desc&page={page}"
    )


def repos():
    page = 1
    while True:
        batch = repo_page(page)
        if not batch:
            return
        for repo in batch:
            if repo["fork"] or repo.get("archived"):
                continue
            if repo.get("private") and not INCLUDE_PRIVATE:
                continue
            if repo["name"].lower() in EXCLUDE_REPOS:
                continue
            yield repo
        page += 1


def collect_week():
    """{language: lines touched} from commits authored by USER in the last
    WINDOW_DAYS, capped at MAX_COMMITS commits total across all repos."""
    since = (datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    weights = {}
    budget = MAX_COMMITS
    for repo in repos():
        if budget <= 0:
            break
        page = 1
        while budget > 0:
            try:
                commits = api(
                    f"/repos/{repo['full_name']}/commits"
                    f"?since={since}&author={USER}&per_page=100&page={page}"
                )
            except urllib.error.HTTPError as exc:
                if exc.code in (409, 404):   # empty repo, or no access
                    break
                raise
            if not commits:
                break
            for commit in commits:
                if budget <= 0:
                    break
                budget -= 1
                try:
                    detail = api(f"/repos/{repo['full_name']}/commits/{commit['sha']}")
                except urllib.error.HTTPError:
                    continue
                for f in detail.get("files") or []:
                    ext = os.path.splitext(f.get("filename", ""))[1].lower()
                    lang = EXT_LANG.get(ext)
                    if not lang:
                        continue
                    changed = f.get("changes", f.get("additions", 0) + f.get("deletions", 0))
                    weights[lang] = weights.get(lang, 0) + changed
            if len(commits) < 100:
                break
            page += 1
    return weights


def rank(weights):
    return sorted(weights.items(), key=lambda kv: kv[1], reverse=True)[:TOP_N]


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
    # Trailing comment on the command line itself, not an extra line: this
    # is a real 7-day window, not just how often the job re-runs, so say
    # what the data covers rather than how often it's rebuilt.
    prompt = "$ ls ~/languages   # past 7 days"
    if not ranked:
        lines = ["# quiet week — no commits in the last 7 days"]
    else:
        lines = columnize([name for name, _ in ranked])
    return "\n".join([START, "```console", prompt, *lines, "```", END])


def main():
    try:
        weights = collect_week()
    except urllib.error.HTTPError as exc:
        print(f"github api error: {exc.code} {exc.reason}", file=sys.stderr)
        return 1

    ranked = rank(weights)
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
