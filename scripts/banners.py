#!/usr/bin/env python3
"""Generate the typewriter banners as self-hosted SVGs.

The banners used to come from readme-typing-svg.demolab.com, which put a
third-party service in the README's render path: if it is down or rate
limited, the profile shows broken images. The text is static, so generate
it once here and commit the result. Re-run after editing BANNERS.
"""

import hashlib
import os
import re
import unicodedata

# Selected per scheme, not one value reused: the old #58a6ff sat at 2.53:1 on
# GitHub's light theme, under the 3:1 floor for graphics.
ACCENT_DARK = "#58a6ff"
ACCENT_LIGHT = "#0969da"
FONT = 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace'
OUT_DIR = "assets"
README = "README.md"

# Characters appear one at a time and then stay put. No cursor and no loop:
# a caret blinking forever sits beside text people are reading.
CHAR_MS = 55
FADE_MS = 90

BANNERS = [
    {
        "file": "banner-top.svg",
        "text": "☕ coffee → 💻 code → 🔧 tinker → repeat",
        "size": 12,
    },
    {
        "file": "banner-hacks.svg",
        "text": "old curiosities, new experiments — held together with duct tape and caffeine.",
        "size": 14,
    },
]


def char_width(ch, size):
    """Monospace advance, counting emoji as double width.

    Deliberately generous: the viewer's fallback monospace is unknown, and a
    too-narrow estimate clips the last word. Overshooting only leaves
    transparent space at the right edge.
    """
    if unicodedata.east_asian_width(ch) in ("W", "F") or ord(ch) > 0x2600:
        return size * 1.30
    return size * 0.65


def esc(text):
    return (text.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))


def render(text, size):
    width = int(sum(char_width(c, size) for c in text)) + 10
    height = int(size * 1.7)
    baseline = int(size * 1.2)

    spans = "".join(
        f'<tspan style="animation-delay:{i * CHAR_MS}ms">{esc(ch)}</tspan>'
        if ch != " " else "<tspan> </tspan>"
        for i, ch in enumerate(text)
    )

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}"
     viewBox="0 0 {width} {height}" role="img" aria-label="{esc(text)}">
  <style>
    text {{ font-family: {FONT}; font-size: {size}px; fill: {ACCENT_DARK}; }}
    @media (prefers-color-scheme: light) {{
      text {{ fill: {ACCENT_LIGHT}; }}
    }}
    /* forwards, not both: "both" applies the from-keyframe (opacity 0)
       during the pre-play delay, so a renderer that parses the animation
       but never ticks its timeline — some static SVG rasterizers, some
       markdown clients — shows every character invisible. "forwards"
       leaves the delay period on the element's own resting style, which
       is full opacity by default, so those renderers fall back correct. */
    tspan {{ opacity: 1; animation: appear {FADE_MS}ms ease-out forwards; }}
    @keyframes appear {{ from {{ opacity: 0; }} to {{ opacity: 1; }} }}
    @media (prefers-reduced-motion: reduce) {{
      tspan {{ animation: appear 200ms ease-out forwards; animation-delay: 0ms !important; }}
    }}
  </style>
  <text x="1" y="{baseline}" xml:space="preserve">{spans}</text>
</svg>
'''


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    readme = open(README, encoding="utf-8").read()

    for b in BANNERS:
        svg = render(b["text"], b["size"])
        path = os.path.join(OUT_DIR, b["file"])
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(svg)

        # Same cache problem as the chart: a fixed path keeps serving the
        # bytes GitHub already proxied, so fingerprint the URL.
        digest = hashlib.sha256(svg.encode("utf-8")).hexdigest()[:10]
        readme = re.sub(
            re.escape(f"{OUT_DIR}/{b['file']}") + r"(\?v=[a-f0-9]+)?",
            f"{OUT_DIR}/{b['file']}?v={digest}",
            readme,
        )
        total = len(b["text"]) * CHAR_MS + FADE_MS
        print(f"{path}  settles in {total}ms  v={digest}")

    with open(README, "w", encoding="utf-8") as fh:
        fh.write(readme)


if __name__ == "__main__":
    main()
