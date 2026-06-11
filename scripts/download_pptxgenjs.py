#!/usr/bin/env python3
"""
Download pptxgenjs and all its npm dependencies WITHOUT npm.
Resolves the full dependency tree from the npm registry, downloads
each tarball, and extracts them into node_modules/ ready for use.

Usage (on online machine):
    # With proxy:
    set HTTPS_PROXY=http://proxy.company.com:8080   (Windows CMD)
    $env:HTTPS_PROXY = "http://proxy.company.com:8080"  (PowerShell)
    python scripts/download_pptxgenjs.py

    # Without proxy:
    python scripts/download_pptxgenjs.py

After this completes, zip node_modules/ and copy to the target machine.
"""

import json
import os
import re
import sys
import tarfile
import tempfile
import urllib.request
import urllib.error
from pathlib import Path

REGISTRY = "https://registry.npmjs.org"
ROOT_PACKAGE = "pptxgenjs"
OUTPUT_DIR = Path("node_modules")

# ── proxy setup ───────────────────────────────────────────────────────────────

_proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or ""
if _proxy:
    print(f"Using proxy: {_proxy}")
    _opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"https": _proxy, "http": _proxy})
    )
    urllib.request.install_opener(_opener)

# ── semver helpers ─────────────────────────────────────────────────────────────

def _ver_tuple(v: str):
    """Convert '1.2.3' to (1, 2, 3) for comparison, ignoring pre-release."""
    parts = re.split(r"[-+]", v)[0].split(".")
    result = []
    for p in parts:
        m = re.match(r"^\d+", p)
        result.append(int(m.group()) if m else 0)
    while len(result) < 3:
        result.append(0)
    return tuple(result)


def _resolve_version(available: list[str], spec: str) -> str:
    """
    Simplified semver resolution covering ^, ~, >=, exact, *, latest.
    Falls back to overall latest if no match found.
    """
    spec = spec.strip()
    if not spec or spec in ("*", "latest", "x"):
        return max(available, key=_ver_tuple)

    # Handle "||" by trying each alternative
    if "||" in spec:
        for part in spec.split("||"):
            try:
                return _resolve_version(available, part.strip())
            except Exception:
                pass
        return max(available, key=_ver_tuple)

    # Strip leading whitespace/v
    spec = spec.lstrip("v")

    # Range with hyphen: "1.2.3 - 2.0.0"
    hyphen = re.match(r"^([\d.]+)\s+-\s+([\d.]+)$", spec)
    if hyphen:
        lo = _ver_tuple(hyphen.group(1))
        hi = _ver_tuple(hyphen.group(2))
        candidates = [v for v in available if lo <= _ver_tuple(v) <= hi]
        return max(candidates, key=_ver_tuple) if candidates else max(available, key=_ver_tuple)

    # Caret: ^1.2.3 → >=1.2.3 <2.0.0
    m = re.match(r"^\^(\d+)\.(\d+)\.(\d+)", spec)
    if m:
        major, minor, patch = int(m[1]), int(m[2]), int(m[3])
        lo = (major, minor, patch)
        hi = (major + 1, 0, 0)
        candidates = [v for v in available if lo <= _ver_tuple(v) < hi]
        return max(candidates, key=_ver_tuple) if candidates else max(available, key=_ver_tuple)

    # Tilde: ~1.2.3 → >=1.2.3 <1.3.0
    m = re.match(r"^~(\d+)\.(\d+)\.(\d+)", spec)
    if m:
        major, minor, patch = int(m[1]), int(m[2]), int(m[3])
        lo = (major, minor, patch)
        hi = (major, minor + 1, 0)
        candidates = [v for v in available if lo <= _ver_tuple(v) < hi]
        return max(candidates, key=_ver_tuple) if candidates else max(available, key=_ver_tuple)

    # Tilde with major.minor only: ~1.2
    m = re.match(r"^~(\d+)\.(\d+)$", spec)
    if m:
        major, minor = int(m[1]), int(m[2])
        lo = (major, minor, 0)
        hi = (major, minor + 1, 0)
        candidates = [v for v in available if lo <= _ver_tuple(v) < hi]
        return max(candidates, key=_ver_tuple) if candidates else max(available, key=_ver_tuple)

    # >= or >
    m = re.match(r"^>=?(\d[\d.]*)", spec)
    if m:
        lo = _ver_tuple(m[1])
        strict = not spec.startswith(">=")
        candidates = [v for v in available if (_ver_tuple(v) > lo) if strict else (_ver_tuple(v) >= lo)]
        return max(candidates, key=_ver_tuple) if candidates else max(available, key=_ver_tuple)

    # Exact
    if re.match(r"^\d+\.\d+\.\d+$", spec):
        return spec if spec in available else max(available, key=_ver_tuple)

    # Partial: "1.x", "1.2.x", "1"
    m = re.match(r"^(\d+)(?:\.(\d+))?(?:\.[xX*])?$", spec)
    if m:
        major = int(m[1])
        minor_str = m[2]
        if minor_str is not None:
            minor = int(minor_str)
            candidates = [v for v in available if _ver_tuple(v)[0] == major and _ver_tuple(v)[1] == minor]
        else:
            candidates = [v for v in available if _ver_tuple(v)[0] == major]
        return max(candidates, key=_ver_tuple) if candidates else max(available, key=_ver_tuple)

    # Give up: return latest
    return max(available, key=_ver_tuple)


# ── npm registry helpers ───────────────────────────────────────────────────────

_pkg_cache: dict[str, dict] = {}


def _fetch_packument(name: str) -> dict:
    if name in _pkg_cache:
        return _pkg_cache[name]
    url = f"{REGISTRY}/{name.replace('/', '%2F')}"
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Registry error for '{name}': {e.code} {e.reason}") from e
    _pkg_cache[name] = data
    return data


def _stable_versions(packument: dict) -> list[str]:
    """Return all non-pre-release version strings."""
    return [v for v in packument["versions"] if not re.search(r"[-]", v)]


# ── dependency resolution ──────────────────────────────────────────────────────

resolved: dict[str, str] = {}  # name -> resolved version


def resolve(name: str, spec: str = "latest") -> None:
    if name in resolved:
        return
    print(f"  resolving  {name}@{spec} ", end="", flush=True)
    try:
        packument = _fetch_packument(name)
    except Exception as e:
        print(f"  ERROR: {e}", flush=True)
        return

    versions = _stable_versions(packument) or list(packument["versions"])
    if spec in ("latest", "*", ""):
        version = packument["dist-tags"].get("latest", max(versions, key=_ver_tuple))
    else:
        version = _resolve_version(versions, spec)

    print(f"→ {version}", flush=True)
    resolved[name] = version

    version_data = packument["versions"][version]
    for dep_name, dep_spec in version_data.get("dependencies", {}).items():
        if dep_name not in resolved:
            resolve(dep_name, dep_spec)


# ── download + extract ─────────────────────────────────────────────────────────

def _safe_extract(tf: tarfile.TarFile, dest: Path) -> None:
    """Extract npm tarball (has 'package/' prefix) into dest/, Windows-safe."""
    dest.mkdir(parents=True, exist_ok=True)
    for member in tf.getmembers():
        # npm tarballs wrap everything in "package/"
        name = member.name
        if name.startswith("package/"):
            name = name[len("package/"):]
        elif name.startswith("./"):
            name = name[2:]

        if not name:
            continue

        # Security: skip absolute paths and parent traversal
        if name.startswith("/") or ".." in name.split("/"):
            continue

        target = dest / Path(name)
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
        elif member.isfile():
            target.parent.mkdir(parents=True, exist_ok=True)
            with tf.extractfile(member) as src, open(target, "wb") as dst:
                dst.write(src.read())


def download_and_extract(name: str, version: str) -> None:
    # Scoped packages: @scope/pkg → node_modules/@scope/pkg/
    dest = OUTPUT_DIR / name
    if dest.exists():
        return

    packument = _fetch_packument(name)
    tarball_url = packument["versions"][version]["dist"]["tarball"]

    print(f"  downloading {name}@{version}", flush=True)
    with tempfile.NamedTemporaryFile(suffix=".tgz", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        urllib.request.urlretrieve(tarball_url, tmp_path)
        with tarfile.open(tmp_path, "r:gz") as tf:
            _safe_extract(tf, dest)
    finally:
        tmp_path.unlink(missing_ok=True)


# ── main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    print(f"\n=== Resolving dependency tree for {ROOT_PACKAGE} ===\n")
    resolve(ROOT_PACKAGE)

    print(f"\n=== Downloading {len(resolved)} package(s) ===\n")
    OUTPUT_DIR.mkdir(exist_ok=True)
    for pkg_name, pkg_version in resolved.items():
        download_and_extract(pkg_name, pkg_version)

    print(f"\n=== Done ===")
    print(f"Extracted {len(resolved)} package(s) into {OUTPUT_DIR.resolve()}/")
    print()
    print("Next steps:")
    print("  1. Zip node_modules/ and package.json (if it exists)")
    print("     PowerShell: Compress-Archive -Path node_modules,package.json -DestinationPath ..\\mcp-node-modules.zip -Force")
    print("  2. Copy mcp-node-modules.zip to the target machine")
    print("  3. On target: Expand-Archive mcp-node-modules.zip -DestinationPath D:\\FAC_Job\\MCP_server -Force")
    print("  4. Verify:   node -e \"require('pptxgenjs'); console.log('OK')\"")


if __name__ == "__main__":
    main()
