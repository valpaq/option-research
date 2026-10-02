from __future__ import annotations

import hashlib


def cell_id(source: str) -> str:

    return hashlib.sha256(source.encode()).hexdigest()[:12]


def md(*lines: str) -> dict:
    source = "\n".join(lines)
    return {"cell_type": "markdown", "id": cell_id(source), "metadata": {}, "source": source}


def code(*lines: str) -> dict:
    source = "\n".join(lines)
    return {
        "cell_type": "code",
        "id": cell_id(source),
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": source,
    }


def header(
    title: str, asof, window: tuple[str, str], manifest: list, *, covering: str = "options settled"
) -> dict:

    from datetime import datetime

    checks = "\n".join(f"| `{rel.name}` | `{sha[:16]}…` | {size:,} |" for rel, sha, size in manifest)
    return md(
        f"# {title} — the numbers, reproducible",
        "",
        f"Generated {asof:%d %B %Y}, covering {covering} between",
        f"{datetime.fromisoformat(window[0]):%d %B %Y} and {datetime.fromisoformat(window[1]):%d %B %Y}.",
        "",
        "Everything below is computed here, in this notebook, from the",
        "pinned input files in `data/`. Nothing is copied in from anywhere.",
        "",
        "## The inputs, pinned",
        "",
        "| file | sha-256 | bytes |",
        "|---|---|---:|",
        checks,
        "",
        "Full digests are in `data/CHECKSUMS.txt`. Same bytes in, same",
        "numbers out.",
    )


def unpack(snapshot_url: str) -> dict:

    return code(
        "import gzip, hashlib, shutil, sys, urllib.request",
        "from datetime import UTC, datetime",
        "from pathlib import Path",
        "",
        "sys.path.insert(0, '../..')   # the library lives at the repository root",
        "",
        "# Published copy of the exact inputs, so this runs without the repo.",
        f"SNAPSHOT_URL = {snapshot_url!r}",
        "SNAPSHOT = Path('data')",
        "if not SNAPSHOT.exists() and SNAPSHOT_URL:",
        "    SNAPSHOT = Path('snapshot'); SNAPSHOT.mkdir(exist_ok=True)",
        "    archive = SNAPSHOT / 'snapshot.tar.gz'",
        "    print(f'fetching {SNAPSHOT_URL}')",
        "    urllib.request.urlretrieve(SNAPSHOT_URL, archive)",
        "    shutil.unpack_archive(archive, SNAPSHOT)",
        "assert SNAPSHOT.exists(), (",
        "    'No snapshot found. Either run this inside the repository, or set '",
        "    'SNAPSHOT_URL to the published archive.'",
        ")",
        "WORK = Path('_unpacked'); WORK.mkdir(exist_ok=True)",
        "",
        "# Unpack the frozen inputs and verify each against its recorded digest,",
        "# so a silently altered file fails here rather than downstream.",
        "expected = {}",
        "for line in (SNAPSHOT / 'CHECKSUMS.txt').read_text().splitlines():",
        "    sha, rel, *_ = line.split()",
        "    expected[Path(rel).name] = sha",
        "",
        "for packed in sorted(SNAPSHOT.glob('*.gz')):",
        "    name = packed.name[:-3]",
        "    target = WORK / name",
        "    with gzip.open(packed, 'rb') as src, target.open('wb') as dst:",
        "        shutil.copyfileobj(src, dst)",
        "    got = hashlib.sha256(target.read_bytes()).hexdigest()",
        "    assert got == expected[name], f'{name} does not match its checksum'",
        "    print(f'{name:<34} verified  {target.stat().st_size:>12,} bytes')",
    )


def rebuild(window: tuple[str, str], chain: str, *, pool: bool = False) -> dict:

    return code(
        "from option_intel import figures, oracle, settlement",
        "from option_intel.spot import HyperliquidSpot",
        "from option_intel.reconstruct import load_writes",
        "",
        f"START = datetime.fromisoformat({window[0]!r})",
        f"END = datetime.fromisoformat({window[1]!r})",
        "ASOF = datetime.now(UTC)",
        "",
        "spot = HyperliquidSpot(cache_path=WORK / 'data__cache__spot-hourly.csv')",
        "prices = oracle.OraclePrices(",
        "    cache_path=WORK / 'data__cache__oracle-prices.csv')",
        "addresses = {k: v for c in sorted(WORK.glob('*otokens-*.json'))",
        "             for k, v in oracle.asset_addresses(c).items()}",
        "",
        "writes = load_writes(sorted(WORK.glob('*sales.csv')), spot)",
        "book, why = settlement.settle(",
        "    writes, spot, asof=ASOF, oracle=prices, asset_addresses=addresses,",
        ")",
        "",
        "# Minute-accurate spot for the writes that sit on their own strike.",
        "# The same call `figures.settled` makes, so this notebook and the",
        "# published figures.json cannot answer differently.",
        "book = figures.refine(book, figures.refined_spot(",
        "    WORK / 'data__cache__spot-refined.csv'))",
        f"rows = [r for r in book if r.chain == {chain!r}",
        "        and START <= r.expired_at <= END]",
        *(
            (
                f"pool = [w for w in writes if w.chain == {chain!r}]",
                "print(f'{len(rows):,} settled in the window, '",
                "      f'{len(pool):,} writes in the next-write pool')",
            )
            if pool
            else ("print(f'{len(rows):,} settled in the window')",)
        ),
    )


def canonical() -> list[dict]:

    return [
        md(
            "## Every number this piece may quote",
            "",
            "`figures.json` is the canonical set: one computation, one file,",
            "written beside the article. The checker refuses any figure in the",
            "prose that is not in it, so the list below is the complete",
            "vocabulary the piece has. Anything in the article is in here,",
            "under a name that says what it is.",
        ),
        code(
            "import json",
            "from option_intel.prose import flatten",
            "",
            "published = json.loads(Path('figures.json').read_text())",
            "values = flatten(published)",
            "print(f'{len(values)} canonical values')",
            "print()",
            "width = max(len(k) for k in values)",
            "for key, value in values.items():",
            "    if abs(value) >= 1e4 or (value and abs(value) < 1e-3):",
            "        shown = f'{value:,.4g}'",
            "    elif float(value).is_integer():",
            "        shown = f'{int(value):,}'",
            "    else:",
            "        shown = f'{value:,.6f}'.rstrip('0').rstrip('.')",
            "    print(f'{key:<{width}}  {shown}')",
        ),
    ]


def draw_figures(charts_module: str, only: list[str] | None = None) -> list[dict]:

    chosen = "" if only is None else "\n".join([f"WANTED = {only!r}", ""])
    picked = "FIGURES.items()" if only is None else "[(n, d) for n, d in FIGURES.items() if n in WANTED]"
    return [
        md(
            "## The figures in the piece",
            "",
            "Drawn by the cell below from the canonical `figures.json`",
            "shipped beside this notebook. There is no second path that",
            "produces them.",
        ),
        code(
            "import json",
            f"from option_intel.{charts_module} import FIGURES",
            "",
            *([chosen.rstrip()] if chosen else []),
            "published = json.loads(Path('figures.json').read_text())",
            f"for name, draw in {picked}:",
            "    made = draw(rows, published, Path('charts') / f'{name}.png')",
            "    print(f'{made.name:<28}{made.stat().st_size / 1e3:>6.0f}KB')",
        ),
        code(
            "from IPython.display import Image, display",
            "",
            f"for name, _ in {picked}:",
            "    display(Image(filename=str(Path('charts') / f'{name}.png')))",
        ),
    ]


def notebook(cells: list[dict]) -> dict:
    return {
        "cells": cells,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python", "version": "3.11"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def settled_rows(window: tuple[str, str], chain: str) -> list[dict]:

    return [
        md(
            "## Rebuilding the book",
            "",
            "Each write is reconstructed from its own transaction receipt, then",
            "followed to expiry and settled at the price the protocol's own oracle",
            "published for that asset and expiry. Options still open are excluded",
            "entirely rather than estimated.",
        ),
        code(
            "from option_intel import figures, oracle, settlement, writers",
            "from option_intel.model import OptionType",
            "from option_intel.spot import HyperliquidSpot",
            "from option_intel.reconstruct import load_writes",
            "",
            "# Fixed dates, not a rolling window, so this reproduces the",
            "# published figures on any day it is run.",
            f"START = datetime.fromisoformat({window[0]!r})",
            f"END = datetime.fromisoformat({window[1]!r})",
            "ASOF = datetime.now(UTC)",
            "",
            "spot = HyperliquidSpot(cache_path=WORK / 'data__cache__spot-hourly.csv')",
            "prices = oracle.OraclePrices(",
            "    cache_path=WORK / 'data__cache__oracle-prices.csv')",
            "addresses = {k: v for c in sorted(WORK.glob('*otokens-*.json'))",
            "             for k, v in oracle.asset_addresses(c).items()}",
            "print(f'{len(addresses)} option-token addresses resolved')",
            "",
            "book, why = settlement.settle(",
            "    load_writes(sorted(WORK.glob('*sales.csv')), spot),",
            "    spot, asof=ASOF, oracle=prices, asset_addresses=addresses,",
            ")",
            "",
            "# Minute-accurate spot for the writes that sit on their own strike.",
            "# The same call `figures.settled` makes, so this notebook and the",
            "# published figures.json cannot answer differently.",
            "book = figures.refine(book, figures.refined_spot(",
            "    WORK / 'data__cache__spot-refined.csv'))",
            f"rows = [r for r in book if r.chain == {chain!r}",
            "        and START <= r.expired_at <= END]",
            "print(f'{len(book):,} settled in total')",
            "print(f'{len(rows):,} on HyperEVM between {START:%d %b %Y} and {END:%d %b %Y}')",
            "print('priced by:', {k: v for k, v in why.items() if k.startswith('priced_by')})",
        ),
    ]
