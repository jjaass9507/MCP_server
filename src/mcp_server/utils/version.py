"""Report which code this server process is actually running.

Exists because the plant deployment updates via `git pull` into a live
source tree: when tool behavior looks stale, the first question is always
"which commit is this server actually running, and is it importing the
repo's src/ or a stale copy in site-packages?". system_info() exposes this
to the agent, and server startup logs it.
"""

import pathlib
import subprocess


def code_info() -> dict:
    """Return {"code_path", "git_commit", "git_branch"} for the running code.

    git values fall back to "unknown" when git is unavailable or the code
    is not inside a git checkout (e.g. installed as a wheel — which itself
    is a useful signal, since the plant deploy should always be a checkout).
    """
    package_dir = pathlib.Path(__file__).resolve().parents[1]  # .../mcp_server
    repo_root = package_dir.parents[1]  # src/mcp_server -> src -> repo root

    def _git(*args: str) -> str:
        try:
            out = subprocess.run(
                ["git", *args],
                cwd=repo_root, capture_output=True, text=True, timeout=5,
            ).stdout.strip()
            return out or "unknown"
        except Exception:
            return "unknown"

    return {
        "code_path": str(package_dir),
        "git_commit": _git("rev-parse", "--short", "HEAD"),
        "git_branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
    }
