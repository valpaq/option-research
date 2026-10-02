from __future__ import annotations

import re
from typing import Any


def numbers_in(text: str) -> list[str]:

    text = re.sub(r"`[^`]+`", " ", text)

    text = re.sub(r"SHA-\d+|ERC-\d+|EIP-\d+|Black-\d+|Rysk V\d+|max\([^)]*\)|1e\d+|\d{2}:\d{2}", " ", text)

    months = "January|February|March|April|May|June|July|August|September|October|November|December"
    text = re.sub(rf"\b\d{{1,2}} (?:{months})\b", " ", text)
    text = re.sub(r"\b\d{1,3}(st|nd|rd|th)\b|\b(19|20)\d{2}\b", " ", text)

    text = re.sub(r"\b(worst|top|best) \d+\b", " ", text)
    found = re.findall(
        r"[−+-]?\$[\d,]+(?:\.\d+)?[MK]?|[−+-]?\d[\d,]*\.\d+%|[−+-]?\d[\d,]*%|"
        r"(?<![\w.$,])\d[\d,]*\d(?![\d.%,])|"
        r"(?<![\w.$,])[−+-]?\d[\d,]*\.\d+(?![\d%])",
        text,
    )
    return [f.strip().rstrip(".,;:") for f in found]


DRAWN_NOT_QUOTED = frozenset(
    {
        "by_asset_weekly",
        "series",
        "by_week",
        "by_expiry",
        "oracle",
        "expiry_results",
        "collateral_reconstruction",
        "by_collateral",
        "top_changes",
        "provenance",
    }
)


def flatten(node, prefix: str = "") -> dict[str, float]:
    out: dict[str, float] = {}
    if isinstance(node, dict):
        for k, v in node.items():
            if k in DRAWN_NOT_QUOTED:
                continue
            out |= flatten(v, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out |= flatten(v, f"{prefix}[{i}]")
    elif isinstance(node, (int, float)) and not isinstance(node, bool):
        out[prefix] = float(node)
    return out


def renderings(value: float) -> set[str]:

    out = {f"{value:,.0f}", f"{abs(value):,.0f}"}
    for dp in (0, 1, 2):
        shown = f"{abs(value):,.{dp}f}"
        out |= {f"+${shown}", f"−${shown}", f"-${shown}", f"${shown}"}
    for dp in (0, 1, 2, 3):
        out.add(f"{value:,.{dp}f}")
        out.add(f"{abs(value):,.{dp}f}")
    for scale, suffix in ((1e6, "M"), (1e3, "K")):
        if abs(value) >= scale:
            for dp in (0, 1, 2):
                shown = f"{abs(value) / scale:,.{dp}f}{suffix}"
                out |= {shown, f"${shown}", f"−${shown}", f"+${shown}", f"-${shown}"}
    if abs(value) < 20:
        for dp in (0, 1, 2, 3):
            shown = f"{abs(value) * 100:,.{dp}f}"
            out |= {shown, f"{shown}%", f"+{shown}%", f"−{shown}%", f"-{shown}%"}
    return out


ROUNDING_TOLERANCE = 0.005


CANDIDATES = 4


def _matches(token: str, values: dict[str, float]) -> list[tuple[str, float, float]]:

    bare = token.lstrip("\u2212+-").lstrip("$").rstrip("%")
    forms = {token, bare, bare.rstrip("MK")}
    if token.startswith("$"):
        forms.add(token[1:])
    out = []
    for key, value in values.items():
        if not (forms & renderings(value)):
            continue
        try:
            written = float(bare.rstrip("MK").replace(",", ""))
        except ValueError:
            out.append((key, value, 0.0))
            continue
        for scale in (1.0, 100.0, 1e-6, 1e-3):
            target = abs(value) * scale
            if target and abs(written - target) / target <= 0.5:
                out.append((key, value, abs(written - target) / target))
                break
        else:
            out.append((key, value, 0.0))
    return sorted(out, key=lambda m: m[2])


def trace(text: str, figures: dict[str, Any], allowed: set[str]) -> list[dict[str, Any]]:

    values = flatten(figures)
    out = []
    for token in dict.fromkeys(numbers_in(text)):
        if token in allowed or token.lstrip("\u2212+-").lstrip("$").rstrip("%") in allowed:
            out.append(
                {"token": token, "key": None, "value": None, "error": 0.0, "status": "declared", "others": 0}
            )
            continue
        found = _matches(token, values)
        if not found:
            out.append(
                {"token": token, "key": None, "value": None, "error": None, "status": "untraced", "others": 0}
            )
            continue
        key, value, error = found[0]
        distinct = {round(v, 12) for _, v, _ in found}
        out.append(
            {
                "token": token,
                "key": key,
                "value": value,
                "error": error,
                "status": "rounded" if error > ROUNDING_TOLERANCE else "exact",
                "others": len(distinct) - 1,
                "candidates": [(k, v) for k, v, _ in found[:CANDIDATES]],
            }
        )
    return out


def untraced(text: str, figures: dict[str, Any], allowed: set[str]) -> list[str]:

    return sorted({m["token"] for m in trace(text, figures, allowed) if m["status"] == "untraced"})


def provenance(text: str, figures: dict[str, Any], allowed: set[str] | None = None) -> str:

    got = trace(text, figures, allowed or set())
    if not got:
        return "no figures in the prose"
    width = max(len(m["token"]) for m in got)
    lines = [f"{len(got)} figure(s) in the prose, and where each traces to", ""]
    for m in got:
        if m["status"] == "declared":
            lines.append(f"  {m['token']:<{width}}  declared in meta.json")
            continue
        if m["status"] == "untraced":
            lines.append(f"  {m['token']:<{width}}  NOTHING PRODUCED THIS")
            continue
        mark = "~" if m["status"] == "rounded" else " "
        lines.append(f"  {m['token']:<{width}} {mark} {m['key']} = {m['value']:.6g}")
        for key, value in m["candidates"][1:]:
            lines.append(f"  {'':<{width}}   or {key} = {value:.6g}")
    lines += [
        "",
        "  ~ reached by rounding. Not wrong, but worth an eye: the",
        "    closest value is not always the intended one.",
    ]
    return "\n".join(lines)


LINK_COST = 23
LIMIT = 280

IMAGE = re.compile(r"^!\[\]\((?P<path>[^)]+)\)\s*$", re.MULTILINE)
LINK = re.compile(r"https?://\S+|\{link\}")


def posts(text: str) -> list[dict]:

    out = []
    for block in re.split(r"^---+\s*$", text, flags=re.MULTILINE):
        image = None
        found = IMAGE.search(block)
        if found:
            image = found.group("path")
            block = IMAGE.sub("", block)
        body = block.strip("\n").strip()
        if body:
            out.append({"text": body, "image": image})
    return out


def length(text: str) -> int:

    return len(LINK.sub("x" * LINK_COST, text))
