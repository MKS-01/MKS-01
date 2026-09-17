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

TOP_N = 10         # languages to list
BAR_WIDTH = 18     # characters in the bar
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

# Icon outlines vendored from Simple Icons (CC0) so nothing is fetched at
# build or render time. A language with no icon simply renders its name.
ICONS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons.json")
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
ICON = 14              # icon box, drawn from a 24x24 viewBox
ICON_GAP = 8

# One reveal, played once, on a surface a visitor sees for the first time.
# Lit cells light up left to right over the dim track; the track never moves.
CELL_ANIM_MS = 180
CELL_STAGGER_MS = 8
ROW_STAGGER_MS = 50
SHOW_PERCENT = False   # the score is a blend, not a real share of code
SHOW_FOOTER = False    # keep the chart generic, without repo or byte counts


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
    """Return {language: total_bytes}, {language: repo_count}, repos_scanned."""
    totals, counts, scanned = {}, {}, 0
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


def load_icons():
    try:
        with open(ICONS_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def esc(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_svg(ranked, scanned, total_bytes):
    icons = load_icons()
    name_w = max(len(name) for name, _ in ranked)
    name_x = ICON + ICON_GAP
    meter_x = name_x + round(name_w * CHAR_W) + 16
    meter_w = round(CELLS * (CELL_W + CELL_GAP) - CELL_GAP)
    width = meter_x + meter_w + (62 if SHOW_PERCENT else 8)
    top = LINE_HEIGHT * 2
    height = top + LINE_HEIGHT * len(ranked) + 8
    if SHOW_FOOTER:
        height += LINE_HEIGHT
    mb = total_bytes / 1_000_000

    # Segments are scaled against the leading language rather than a full
    # 100% track: at true scale the top language fills under a third of the
    # row and the tail is invisible. Ratios between languages are preserved.
    top_pct = max(pct for _, pct in ranked)

    if SHOW_PERCENT:
        summary = ", ".join(f"{name} {pct:.1f} percent" for name, pct in ranked)
    else:
        summary = ", ".join(name for name, _ in ranked)

    footer = ""
    if SHOW_FOOTER:
        footer = (
            f'\n  <text class="prompt" x="1" y="{height - 8}">'
            f'across {scanned} repos \u00b7 {mb:.1f} MB analyzed</text>'
        )
    rows = []
    for i, (name, pct) in enumerate(ranked):
        y = top + i * LINE_HEIGHT
        lit = max(1, round(CELLS * pct / top_pct))

        def cell(k, cls, delay=None):
            style = f' style="animation-delay:{delay}ms"' if delay is not None else ""
            return (
                f'<rect class="{cls}"{style} '
                f'x="{meter_x + k * (CELL_W + CELL_GAP):.1f}" y="{y - CELL_H + 1}" '
                f'width="{CELL_W}" height="{CELL_H}" rx="1" />'
            )

        # Full track first, lit cells over it, so a row reads as filling up
        # rather than as missing segments while the reveal runs.
        cells = "".join(cell(k, "off") for k in range(CELLS))
        cells += "".join(
            cell(k, "on", i * ROW_STAGGER_MS + k * CELL_STAGGER_MS)
            for k in range(lit)
        )
        glyph = ""
        if name in icons:
            scale = ICON / 24
            glyph = (
                f'<g transform="translate(0,{y - ICON + 2}) scale({scale:.4f})">'
                f'<path class="icon" d="{icons[name]["d"]}" /></g>'
            )
        row = (
            f'  {glyph}<text class="name" x="{name_x}" y="{y}">{esc(name)}</text>\n'
            f'  {cells}'
        )
        if SHOW_PERCENT:
            row += f'\n  <text class="pct" x="{width - 8}" y="{y}">{pct:.1f}%</text>'
        rows.append(row)

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}"
     viewBox="0 0 {width} {height}" role="img" aria-label="Languages by use, most used first: {esc(summary)}">
  <style>
    text {{
      font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
      font-size: {FONT_SIZE}px;
    }}
    .name, .prompt {{ fill: #8b949e; }}
    .pct {{ fill: #8b949e; text-anchor: end; }}
    .icon {{ fill: {ACCENT}; }}
    .off {{ fill: {ACCENT}; opacity: 0.20; }}
    .on {{
      fill: {ACCENT};
      transform-box: fill-box;
      transform-origin: center;
      animation: lightup {CELL_ANIM_MS}ms cubic-bezier(0.23, 1, 0.32, 1) both;
    }}
    @keyframes lightup {{
      from {{ opacity: 0; transform: scale(0.85); }}
      to   {{ opacity: 1; transform: scale(1); }}
    }}
    @media (prefers-reduced-motion: reduce) {{
      .on {{
        animation: fadein 200ms ease-out both;
        animation-delay: 0ms !important;
      }}
      @keyframes fadein {{ from {{ opacity: 0; }} to {{ opacity: 1; }} }}
    }}
    @media (prefers-color-scheme: light) {{
      .name, .pct, .prompt {{ fill: #57606a; }}
      .off {{ opacity: 0.22; }}
    }}
  </style>
  <text class="prompt" x="1" y="{LINE_HEIGHT - 6}">$ ls -l ~/languages</text>
{chr(10).join(rows)}{footer}
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
        f'<img src="{SVG_PATH}?v={digest}" alt="Languages ranked by use, most used first" />',
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
