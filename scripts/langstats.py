#!/usr/bin/env python3
"""Rebuild the language-usage block in README.md from GitHub repo language bytes.

Talks only to api.github.com and rewrites the text between the langstats
markers, so the README has no third-party render server in its critical path.
"""

import hashlib
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

SVG_PATH = os.environ.get("LANGSTATS_SVG", "assets/langstats.svg")
START = "<!-- langstats:start -->"
END = "<!-- langstats:end -->"

# GitHub strips inline CSS from README HTML, so the chart ships as an SVG in
# this repo. One accent per scheme, matching the banner; bars are scaled
# against the leading language so the tail stays visible.
ACCENT_DARK, ACCENT_LIGHT = "#58a6ff", "#0969da"
MUTED_DARK, MUTED_LIGHT = "#8b949e", "#57606a"
FONT_SIZE = 13
CHAR_W = 7.85          # advance width of the fallback monospace at 13px
LINE_HEIGHT = 22
BAR_W = 180
BAR_H = 6


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


def esc(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_svg(ranked):
    name_w = round(max(len(name) for name, _ in ranked) * CHAR_W)
    bar_x = name_w + 16
    width = bar_x + BAR_W + 2
    top = LINE_HEIGHT * 2
    height = top + LINE_HEIGHT * (len(ranked) - 1) + 8
    lead = max(pct for _, pct in ranked)
    summary = ", ".join(name for name, _ in ranked)

    rows = []
    for i, (name, pct) in enumerate(ranked):
        y = top + i * LINE_HEIGHT
        bar_y = y - 4 - BAR_H // 2   # centred on the text's x-height
        fill = max(BAR_H, round(BAR_W * pct / lead))
        rows.append(
            f'  <text class="name" x="1" y="{y}">{esc(name)}</text>\n'
            f'  <rect class="track" x="{bar_x}" y="{bar_y}" width="{BAR_W}" '
            f'height="{BAR_H}" rx="{BAR_H / 2:g}" />\n'
            f'  <rect class="bar" x="{bar_x}" y="{bar_y}" width="{fill}" '
            f'height="{BAR_H}" rx="{BAR_H / 2:g}" />'
        )

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}"
     viewBox="0 0 {width} {height}" role="img" aria-label="Languages by use, most used first: {esc(summary)}">
  <style>
    text {{
      font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
      font-size: {FONT_SIZE}px;
      fill: {MUTED_DARK};
    }}
    .bar {{ fill: {ACCENT_DARK}; }}
    .track {{ fill: {ACCENT_DARK}; opacity: 0.15; }}
    @media (prefers-color-scheme: light) {{
      text {{ fill: {MUTED_LIGHT}; }}
      .bar, .track {{ fill: {ACCENT_LIGHT}; }}
    }}
  </style>
  <text x="1" y="{LINE_HEIGHT - 6}">$ ls ~/languages</text>
{chr(10).join(rows)}
</svg>
"""


def render_readme_block(svg):
    # Browsers and GitHub's image proxy cache by URL, so a chart republished
    # at a fixed path keeps serving the old bytes. Fingerprint the URL with
    # the content hash: it only changes when the chart does, and when it
    # changes nothing has cached it yet.
    digest = hashlib.sha256(svg.encode("utf-8")).hexdigest()[:10]
    return "\n".join([
        START,
        f'<img src="{SVG_PATH}?v={digest}" '
        f'alt="Languages ranked by use, most used first" />',
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
    svg = render_svg(ranked)
    changed = False

    os.makedirs(os.path.dirname(SVG_PATH) or ".", exist_ok=True)
    existing = ""
    if os.path.exists(SVG_PATH):
        with open(SVG_PATH, encoding="utf-8") as fh:
            existing = fh.read()
    if existing != svg:
        with open(SVG_PATH, "w", encoding="utf-8") as fh:
            fh.write(svg)
        changed = True

    block = render_readme_block(svg)

    with open(README, encoding="utf-8") as fh:
        readme = fh.read()
    if START not in readme or END not in readme:
        print(f"markers {START} / {END} not found in {README}", file=sys.stderr)
        return 1
    updated = re.sub(
        re.escape(START) + r".*?" + re.escape(END), lambda _: block, readme, flags=re.S
    )

    if updated != readme:
        with open(README, "w", encoding="utf-8") as fh:
            fh.write(updated)
        changed = True

    print("updated" if changed else "no change")
    return 0


if __name__ == "__main__":
    sys.exit(main())
