from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


NAMES = ("articles", "research")


def _root() -> Path:
    for name in NAMES:
        if (ROOT / name).is_dir():
            return ROOT / name
    return ROOT / NAMES[0]


ARTICLES = _root()


DEPTH = 2


def homes(root: Path = ARTICLES) -> list[Path]:

    found: list[Path] = []
    for depth in range(DEPTH + 1):
        pattern = "/".join(["*"] * depth) + "/meta.json" if depth else "meta.json"
        found.extend(p.parent for p in root.glob(pattern))
    return sorted(set(found), key=lambda p: (p.parent.name, p.name))


def home(slug: str, root: Path = ARTICLES) -> Path:

    for candidate in homes(root):
        if candidate.name == slug:
            return candidate
    direct = root / slug
    if (direct / "meta.json").exists():
        return direct
    raise FileNotFoundError(f"no article named {slug} under {root}")


def group(piece: Path, root: Path = ARTICLES) -> str:

    return piece.parent.name if piece.parent != root else ""


def meta(slug: str, root: Path = ARTICLES) -> dict:
    return json.loads((home(slug, root) / "meta.json").read_text())
