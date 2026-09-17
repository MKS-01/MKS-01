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

# This repo only contains the generator, so counting it would let the chart
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
# this repo. Colors follow the README's own palette.
ACCENT = "#58a6ff"
LINE_HEIGHT = 22
FONT_SIZE = 13
CHAR_W = 7.85          # advance width of the fallback monospace at 13px
CELLS = 22             # segments in each meter
CELL_W = 7
CELL_GAP = 2.6
CELL_H = 9


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
            if repo["name"].lower() in EXCLUDE_REPOS:
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


def esc(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_svg(ranked, scanned, total_bytes):
    name_w = max(len(name) for name, _ in ranked)
    meter_x = round(name_w * CHAR_W) + 16
    meter_w = round(CELLS * (CELL_W + CELL_GAP) - CELL_GAP)
    width = meter_x + meter_w + 62
    top = LINE_HEIGHT * 2
    height = top + LINE_HEIGHT * len(ranked) + LINE_HEIGHT + 8
    mb = total_bytes / 1_000_000

    # Segments are scaled against the leading language rather than a full
    # 100% track: at true scale the top language fills under a third of the
    # row and the tail is invisible. Ratios between languages are preserved.
    top_pct = max(pct for _, pct in ranked)

    summary = ", ".join(f"{name} {pct:.1f} percent" for name, pct in ranked)
    rows = []
    for i, (name, pct) in enumerate(ranked):
        y = top + i * LINE_HEIGHT
        lit = max(1, round(CELLS * pct / top_pct))
        cells = "".join(
            f'<rect class="{"on" if k < lit else "off"}" '
            f'x="{meter_x + k * (CELL_W + CELL_GAP):.1f}" y="{y - CELL_H + 1}" '
            f'width="{CELL_W}" height="{CELL_H}" rx="1" />'
            for k in range(CELLS)
        )
        rows.append(
            f'  <text class="name" x="1" y="{y}">{esc(name)}</text>\n'
            f'  {cells}\n'
            f'  <text class="pct" x="{width - 8}" y="{y}">{pct:.1f}%</text>'
        )

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}"
     viewBox="0 0 {width} {height}" role="img" aria-label="Language usage: {esc(summary)}">
  <style>
    text {{
      font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
      font-size: {FONT_SIZE}px;
    }}
    .name, .prompt {{ fill: #8b949e; }}
    .pct {{ fill: #8b949e; text-anchor: end; }}
    .on {{ fill: {ACCENT}; }}
    .off {{ fill: {ACCENT}; opacity: 0.20; }}
    @media (prefers-color-scheme: light) {{
      .name, .pct, .prompt {{ fill: #57606a; }}
      .off {{ opacity: 0.22; }}
    }}
  </style>
  <text class="prompt" x="1" y="{LINE_HEIGHT - 6}">$ ls -l ~/languages</text>
{chr(10).join(rows)}
  <text class="prompt" x="1" y="{height - 8}">across {scanned} repos \u00b7 {mb:.1f} MB analyzed</text>
</svg>
"""


def render_readme_block():
    return "\n".join([
        START,
        f'<img src="{SVG_PATH}" alt="Language usage by share of code across public repos" />',
        END,
    ])


def main():
    try:
        totals, counts, scanned = collect()
    except urllib.error.HTTPError as exc:
        print(f"github api error: {exc.code} {exc.reason}", file=sys.stderr)
        return 1
    if not totals:
        print("no language data found", file=sys.stderr)
        return 1

    ranked = rank(totals, counts)
    svg = render_svg(ranked, scanned, sum(totals.values()))
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

    block = render_readme_block()

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
