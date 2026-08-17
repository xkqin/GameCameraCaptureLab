from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .paths import resolve_project_path


def _require_git() -> str:
    git = shutil.which("git")
    if not git:
        raise RuntimeError("Git was not found. Install Git and make sure it is on PATH.")
    return git


def ensure_laion_repo(repo_url: str, repo_dir: str | Path) -> Path:
    return clone_or_update_repo(repo_url, repo_dir)


def clone_or_update_repo(repo_url: str, repo_dir: str | Path) -> Path:
    git = _require_git()
    target = resolve_project_path(repo_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        subprocess.run([git, "clone", repo_url, str(target)], check=True)
    else:
        subprocess.run([git, "-C", str(target), "pull", "--ff-only"], check=True)
    verify_laion_repo(target)
    return target


def verify_laion_repo(repo_dir: str | Path) -> bool:
    target = resolve_project_path(repo_dir)
    if not target.exists():
        raise FileNotFoundError(f"LAION aesthetic-predictor repo is missing: {target}")
    if not (target / ".git").exists():
        raise RuntimeError(f"Directory exists but is not a git checkout: {target}")
    candidates = list(target.rglob("*.pth")) + list(target.rglob("*.pt"))
    readmes = list(target.glob("README*"))
    if not candidates and not readmes:
        raise RuntimeError(f"LAION repo does not look complete: {target}")
    return True
