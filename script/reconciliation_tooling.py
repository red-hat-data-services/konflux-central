"""Load independently selected standalone shared tooling from verified Git bytes."""

import importlib.util
from pathlib import Path
import subprocess

REVISION = "4de549ff57e1e373424aee0ea21c4b997731e7f0"


def git_read(root, *args):
    """Read local Git objects without discovering credentials or touching remotes."""
    return subprocess.check_output(["git", "-C", str(root), *args])


def load_tool(path, name):
    """Load only an approved module, bypassing package initialization and pycache."""
    if name not in ("reconciliation", "validated_pr_publisher"):
        raise ValueError("unsupported shared module")
    path = Path(path).resolve()
    if git_read(path, "rev-parse", "HEAD").decode().strip() != REVISION:
        raise ValueError("tooling checkout differs from the reviewed revision")
    relative = f"lib/{name}.py"
    filename = path / relative
    expected = git_read(path, "show", f"{REVISION}:{relative}")
    if (filename.is_symlink() or not filename.resolve().is_relative_to(path)
            or filename.read_bytes() != expected):
        raise ValueError("tooling source differs from reviewed Git contents")
    spec = importlib.util.spec_from_file_location(f"kc_{name}", filename)
    module = importlib.util.module_from_spec(spec)
    exec(compile(expected, str(filename), "exec"), module.__dict__)
    return module
