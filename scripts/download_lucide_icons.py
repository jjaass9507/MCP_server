"""
Download a curated set of Lucide SVG icons from GitHub into scripts/icons/.

Usage:
    python scripts/download_lucide_icons.py
    python scripts/download_lucide_icons.py --proxy http://proxy:8080
    python scripts/download_lucide_icons.py --list   # just print icon names

Lucide is ISC-licensed. SVGs use stroke="currentColor" so they can be
recolored at render time by replacing currentColor with any hex value.
"""

import argparse
import os
import sys
import urllib.request
from pathlib import Path

BASE_URL = "https://raw.githubusercontent.com/lucide-icons/lucide/main/icons/{name}.svg"

ICONS = [
    # Navigation / arrows
    "arrow-right", "arrow-left", "arrow-up", "arrow-down",
    "chevron-right", "chevron-left", "chevron-up", "chevron-down",
    # Basic controls
    "check", "x", "plus", "minus", "search",
    # Data / infrastructure
    "database", "cloud", "server", "network", "cpu", "hard-drive",
    "download", "upload", "refresh-cw", "git-branch",
    # Charts / analytics
    "bar-chart-2", "trending-up", "trending-down", "chart-pie", "activity",
    # People
    "users", "user", "user-check",
    # Security
    "shield", "shield-check", "lock", "key",
    # Ideas / work
    "rocket", "lightbulb", "settings", "wrench", "target",
    # Files / docs
    "file", "file-text", "folder", "clipboard",
    # Time
    "calendar", "clock",
    # Communication
    "mail", "phone", "message-square", "bell",
    # Web / code
    "globe", "code", "terminal",
    # Status
    "triangle-alert", "info", "circle-help", "zap",
    # Business
    "briefcase", "building", "dollar-sign", "award",
    # Misc
    "star", "thumbs-up",
]


def main():
    ap = argparse.ArgumentParser(description="Download Lucide SVG icons")
    ap.add_argument("--proxy", default=os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY"), help="HTTP proxy URL")
    ap.add_argument("--list", action="store_true", help="Print icon names and exit")
    ap.add_argument("--out", default=None, help="Output directory (default: scripts/icons/ next to this script)")
    args = ap.parse_args()

    if args.list:
        for name in ICONS:
            print(name)
        return

    script_dir = Path(__file__).parent
    out_dir = Path(args.out) if args.out else script_dir / "icons"
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.proxy:
        proxy_handler = urllib.request.ProxyHandler({"https": args.proxy, "http": args.proxy})
        opener = urllib.request.build_opener(proxy_handler)
        urllib.request.install_opener(opener)

    ok = 0
    fail = 0
    for name in ICONS:
        dest = out_dir / f"{name}.svg"
        if dest.exists():
            print(f"  skip  {name}.svg (already exists)")
            ok += 1
            continue
        url = BASE_URL.format(name=name)
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                svg = resp.read()
            dest.write_bytes(svg)
            print(f"  ok    {name}.svg")
            ok += 1
        except Exception as e:
            print(f"  FAIL  {name}: {e}", file=sys.stderr)
            fail += 1

    print(f"\nDone: {ok} downloaded, {fail} failed.")
    if fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
