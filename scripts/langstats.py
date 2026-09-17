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

# Icon outlines vendored from Simple Icons (CC0) and devicon (MIT) so
# nothing is fetched at build or render time. Each entry carries its own
# viewBox, since the two sets use different grids. A language with no icon
# simply renders its name.
ICONS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons.json")
START = "<!-- langstats:start -->"
END = "<!-- langstats:end -->"

# GitHub strips inline CSS from README HTML, so the chart ships as an SVG in
# this repo. Colors follow the README's own palette.
# Sequential ramp: magnitude gets one hue, light to dark, shaded by value
# (never by rank, or a language repaints when the daily order shifts). Each
# scheme gets its own selected steps rather than one value reused, and every
# step clears 3:1 against its surface.
RAMP_DARK = ["#1f6feb", "#388bfd", "#4493f8", "#58a6ff"]
# Capped short of navy-black: going darker by value is the textbook
# sequential move, but running it to the floor painted the leading square
# almost black instead of blue.
RAMP_LIGHT = ["#0969da", "#0757ba", "#0a4faf", "#0a3980"]
ICON_DARK, ICON_LIGHT = "#58a6ff", "#0969da"
ACCENT = RAMP_DARK[-1]
LINE_HEIGHT = 22
FONT_SIZE = 13
CHAR_W = 7.85          # advance width of the fallback monospace at 13px
CELL = 9               # square size, GitHub's contribution-graph unit
CELL_GAP = 3
CELL_R = 2
CELLS = 14             # squares per row — enough resolution, narrow canvas
ICON = 14              # icon box, drawn from a 24x24 viewBox
ICON_GAP = 8

# One reveal, played once, on a surface a visitor sees for the first time.
# Squares light up left to right over a dim, static grid.
CELL_ANIM_MS = 260
ROW_STAGGER_MS = 55
CELL_STAGGER_MS = 10
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
    grid_x = name_x + round(name_w * CHAR_W) + 16
    grid_w = round(CELLS * (CELL + CELL_GAP) - CELL_GAP)
    width = grid_x + grid_w + 4
    top = LINE_HEIGHT * 2
    # Rows are baselines, so the last one sits at top + (n-1) * LINE_HEIGHT;
    # reserving a full row after it left a block of dead space below.
    height = top + LINE_HEIGHT * (len(ranked) - 1) + 8

    # Squares are scaled against the leading language rather than a full
    # 100% grid: at true scale the top language fills under a third of the
    # row and the tail is invisible. Ratios between languages are preserved.
    top_pct = max(pct for _, pct in ranked)
    summary = ", ".join(name for name, _ in ranked)

    rows = []
    for i, (name, pct) in enumerate(ranked):
        y = top + i * LINE_HEIGHT
        ratio = pct / top_pct
        lit = max(1, round(CELLS * ratio))
        # Four buckets of the ramp, by value — never by rank.
        shade = 3 if ratio >= 0.75 else 2 if ratio >= 0.5 else 1 if ratio >= 0.25 else 0
        sq_y = y - CELL

        def square(k, cls, delay=None):
            style = f' style="animation-delay:{delay}ms"' if delay is not None else ""
            return (
                f'<rect class="{cls}"{style} '
                f'x="{grid_x + k * (CELL + CELL_GAP)}" y="{sq_y}" '
                f'width="{CELL}" height="{CELL}" rx="{CELL_R}" />'
            )

        # Full grid first, lit squares over it, so a row reads as filling up
        # rather than as missing squares while the reveal runs.
        squares = "".join(square(k, "off") for k in range(CELLS))
        squares += "".join(
            square(k, f"on s{shade}", i * ROW_STAGGER_MS + k * CELL_STAGGER_MS)
            for k in range(lit)
        )

        glyph = ""
        if name in icons:
            spec = icons[name]
            grid = float(spec.get("viewBox", "0 0 24 24").split()[2])
            scale = ICON / grid
            paths = spec.get("paths") or [spec["d"]]
            shapes = "".join(f'<path class="icon" d="{d}" />' for d in paths)
            glyph = (
                f'<g transform="translate(0,{y - ICON + 2}) scale({scale:.4f})">'
                f'{shapes}</g>'
            )
        rows.append(
            f'  {glyph}<text class="name" x="{name_x}" y="{y}">{esc(name)}</text>\n'
            f'  {squares}'
        )

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}"
     viewBox="0 0 {width} {height}" role="img" aria-label="Languages by use, most used first: {esc(summary)}">
  <style>
    text {{
      font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
      font-size: {FONT_SIZE}px;
    }}
    .name, .prompt {{ fill: #8b949e; }}
    .icon {{ fill: {ICON_DARK}; }}
    .off {{ fill: {ICON_DARK}; opacity: 0.15; }}
    .s0 {{ fill: {RAMP_DARK[0]}; }}
    .s1 {{ fill: {RAMP_DARK[1]}; }}
    .s2 {{ fill: {RAMP_DARK[2]}; }}
    .s3 {{ fill: {RAMP_DARK[3]}; }}
    /* Opacity only, from a visible floor. A geometry-based reveal holds a
       square at zero size through its stagger delay, so any context that
       shows a static first frame would draw an empty grid. */
    .on {{
      animation: lightup {CELL_ANIM_MS}ms cubic-bezier(0.23, 1, 0.32, 1) both;
    }}
    @keyframes lightup {{
      from {{ opacity: 0.35; }}
      to   {{ opacity: 1; }}
    }}
    @media (prefers-reduced-motion: reduce) {{
      .on {{
        animation: fadein 200ms ease-out both;
        animation-delay: 0ms !important;
      }}
      @keyframes fadein {{ from {{ opacity: 0.35; }} to {{ opacity: 1; }} }}
    }}
    @media (prefers-color-scheme: light) {{
      .name, .prompt {{ fill: #57606a; }}
      .icon {{ fill: {ICON_LIGHT}; }}
      .off {{ fill: {ICON_LIGHT}; opacity: 0.13; }}
      .s0 {{ fill: {RAMP_LIGHT[0]}; }}
      .s1 {{ fill: {RAMP_LIGHT[1]}; }}
      .s2 {{ fill: {RAMP_LIGHT[2]}; }}
      .s3 {{ fill: {RAMP_LIGHT[3]}; }}
    }}
  </style>
  <text class="prompt" x="1" y="{LINE_HEIGHT - 6}">$ ls -l ~/languages</text>
{chr(10).join(rows)}
</svg>
"""


def render_readme_block(svg):
    # Browsers and GitHub's image proxy cache by URL, so a chart republished
    # at a fixed path keeps serving the old bytes. Fingerprint the URL with
    # the content hash: it only changes when the chart does, and when it
    # changes nothing has cached it yet.
    digest = hashlib.sha256(svg.encode("utf-8")).hexdigest()[:10]
    # Floated left so hand-written content after the marker block (a project
    # list, kept outside the markers so this script never overwrites it)
    # wraps beside the chart instead of stacking under it. No table, so no
    # border and no zebra striping — GitHub forces both onto every <table>.
    return "\n".join([
        START,
        f'<img src="{SVG_PATH}?v={digest}" align="left" '
        f'alt="Languages ranked by use, most used first" />',
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
