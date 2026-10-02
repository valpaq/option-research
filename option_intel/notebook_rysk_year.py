from datetime import datetime

from option_intel.notebook import code, md


def cells(manifest, asof, snapshot_url, window, chain):
    start, end = (datetime.fromisoformat(d) for d in window)
    return [
        md(
            f"# Rysk V12: {start:%d %B %Y} to {end:%d %B %Y}",
            "Expiry cohort on HyperEVM; both 08:00 UTC boundaries included.",
            "Start from a clean kernel. This notebook verifies input hashes, then "
            "regenerates the tables and charts.",
            "Option result is writer premium minus oracle-derived payoff. The combined "
            "scenario adds a proxy for the change in call collateral value while each "
            "option was open. Redemption transfers and wallet-wide returns are outside "
            "this calculation.",
        ),
        code(
            "import csv, gzip, hashlib, json, sys, tempfile",
            "from pathlib import Path",
            'ROOT = next(p for p in (Path.cwd().resolve(), *Path.cwd().resolve().parents) if (p / "option_intel").is_dir())',
            "sys.path.insert(0, str(ROOT))",
            'BUNDLE = next(p for p in (ROOT / "research/rysk/rysk-year", ROOT / "articles/rysk/rysk-year") if (p / "data/CHECKSUMS.txt").is_file())',
            'SNAPSHOT = BUNDLE / "data"',
            'scratch = tempfile.TemporaryDirectory(prefix="rysk-year-")',
            "WORK = Path(scratch.name)",
            "expected = {line.split()[1]: line.split()[0] for line in "
            '(SNAPSHOT / "CHECKSUMS.txt").read_text().splitlines()}',
            "for name, sha in expected.items():",
            '    payload = gzip.decompress((SNAPSHOT / (name + ".gz")).read_bytes())',
            "    assert hashlib.sha256(payload).hexdigest() == sha, name",
            "    (WORK / name).write_bytes(payload)",
            'print(f"Verified {len(expected)} input files")',
            'config = json.loads((WORK / "data__cache__rysk-year-provenance.json").read_text())',
            'for name, sha in config["input_sha256"].items():',
            "    assert hashlib.sha256((WORK / name).read_bytes()).hexdigest() == sha, name",
            'for name, sha in config["code_sha256"].items():',
            "    assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == sha, "
            'f"Code differs from snapshot: {name}"',
            'print(json.dumps({k: v for k, v in config.items() if k != "code_sha256"}, indent=2))',
        ),
        md(
            "## Sources and reconstruction",
            "The collector discovers option-token series from Rysk factory logs, probes "
            "each series contract for strike, expiry, option side and asset addresses, "
            "then scans their Transfer logs in bounded block ranges. For each candidate "
            "transaction it reads the receipt: a mint identifies a sale, token transfers "
            "identify the writer, gross stablecoin paid from the maker-side contract, "
            "other stablecoin outflows and the writer-to-pool collateral transfer. The sales "
            "ledger contains primary writes only: no-mint transactions and ambiguous "
            "multi-series mints are excluded, and accepted rows are deduplicated by "
            "transaction hash. The provenance records incremental scan boundaries, "
            "the terminal block and hash, and an overlapping receipt replay range. "
            "It is not a full receipt archive or fresh replay of the older ledger.",
            "The oracle snapshot contains ExpiryPriceUpdated events keyed by chain, "
            "underlying address and 08:00 UTC expiry. Hyperliquid hourly/four-hourly "
            "candle closes and Binance minute observations supply writing-price proxies. "
            "All fetched observations used here are frozen in the input bundle.",
            "The pinned collateral receipts supply transfer addresses, raw amounts and "
            "log indices for the receipt-backed rows. A separate full-cohort receipt scan "
            "splits maker-side payments by recipient; receipts for every transfer "
            "to the less-used address are pinned. RyskHype currently labels the "
            "addresses feeRecipient and gasFeeRecipient in its read-as-proxy "
            "getters. Those getters were checked on 2026-09-27; "
            "historical recipient settings were not reconstructed.",
        ),
        md(
            "## Compute the expiry cohort",
            "Reconstruction uses a temporary copy of the inputs and makes no network "
            "requests. Missing spot hours or oracle prices, duplicate sales, and dropped "
            "eligible rows stop the calculation.",
        ),
        code(
            "import socket",
            "from unittest.mock import patch",
            "from option_intel import rysk_year",
            'with patch.object(socket.socket, "connect", side_effect=RuntimeError("Offline notebook")):',
            "    rows, published = rysk_year.calculate(WORK, config)",
            '(BUNDLE / "figures.json").write_text(json.dumps(published, indent=2) + "\\n")',
            'rysk_year.export_tables(rows, published, (BUNDLE / "tables"))',
            'print(json.dumps(published["coverage"], indent=2))',
            'print(published["window"])',
        ),
        md(
            "## Audit the collected rows against the calculated cohort",
            "This cell checks every eligible source row against the reconstructed result. "
            "It resolves each settlement price by the exact underlying address on that "
            "row's option token, independently of the library's ticker lookup. "
            "The payment check starts with the gross stablecoin transfers recorded in the "
            "source ledger and confirms that the writer premium is gross minus all "
            "non-writer outflows. The ledger labels those outflows `fee`, although "
            "they include transfers to the address now configured as gasFeeRecipient. "
            "The pinned payment audit identifies the recipients and checks each "
            "transfer to that address "
            "against its receipt.",
        ),
        code(
            "import math",
            "from collections import Counter",
            "from datetime import datetime",
            "raw_sales = []",
            'for filename in ("data__onchain__rysk__sales.csv", "data__onchain__rysk-eth__sales.csv"):',
            '    with gzip.open(SNAPSHOT / (filename + ".gz"), "rt") as stream:',
            "        raw_sales.extend(csv.DictReader(stream))",
            'start = datetime.fromisoformat(config["window"]["start"])',
            'end = datetime.fromisoformat(config["window"]["end"])',
            'source = [r for r in raw_sales if r["chain"] == config["chain"] '
            'and start.date() <= datetime.fromisoformat(r["expiry"]).date() <= end.date()]',
            'source_by_tx = {r["tx_hash"]: r for r in source}',
            "result_by_tx = {r.tx_hash: r for r in rows}",
            'exposure_anomalies = {r["tx_hash"]: r for r in published["collateral_exposure_anomalies"]}',
            "assert len(source) == len(source_by_tx) == len(result_by_tx) == len(rows)",
            "assert source_by_tx.keys() == result_by_tx.keys()",
            "tokens = {}",
            'for filename in ("data__cache__otokens-rysk.json", "data__cache__otokens-rysk-eth.json"):',
            "    tokens.update(json.loads((WORK / filename).read_text()))",
            'with (WORK / "data__cache__oracle-prices.csv").open() as stream:',
            "    exact_oracle = {(chain, address.lower(), int(expiry)): float(price) "
            "for chain, address, expiry, price in csv.reader(stream)}",
            "for tx, sale in source_by_tx.items():",
            "    result = result_by_tx[tx]",
            '    token = tokens[sale["otoken"]]',
            '    assert token["strike"] == float(sale["strike"]) == result.strike',
            '    assert token["is_put"] == (sale["option_type"] == "P")',
            '    assert token["expiry_ts"] == int(result.expired_at.timestamp())',
            '    assert sale["writer"].lower() == result.writer.lower()',
            '    assert sale["underlying"] == result.underlying',
            '    assert sale["option_type"] == result.option_type.value',
            '    assert math.isclose(float(sale["contracts"]), result.contracts, abs_tol=1e-8)',
            '    assert math.isclose(float(sale["premium"]) - float(sale["fee"]), '
            "result.premium, abs_tol=1e-7)",
            '    assert math.isclose(float(sale["fee"]), result.fee, abs_tol=1e-7)',
            "    if tx in exposure_anomalies:",
            "        anomaly = exposure_anomalies[tx]",
            '        assert float(sale["collateral_amount"]) == '
            'anomaly["writer_to_pool_transfer"] == result.collateral_amount',
            '    elif sale["collateral_symbol"] != "stHYPE":',
            '        assert math.isclose(float(sale["collateral_amount"]), '
            "result.collateral_amount, abs_tol=1e-8)",
            '    key = (sale["chain"], token["underlying_address"].lower(), '
            "int(result.expired_at.timestamp()))",
            "    assert math.isclose(exact_oracle[key], result.spot_at_expiry, abs_tol=1e-8)",
            'gross = math.fsum(float(r["premium"]) for r in source)',
            'non_writer = math.fsum(float(r["fee"]) for r in source)',
            "net = math.fsum(r.premium for r in rows)",
            "assert math.isclose(gross - non_writer, net, abs_tol=1e-6)",
            'assert math.isclose(non_writer, published["fee"]["total"], abs_tol=1e-6)',
            'assert math.isclose(published["fee"]["rysk_fee_recipient_total"] + '
            'published["fee"]["gas_fee_recipient_total"], non_writer, abs_tol=1e-6)',
            'assert sum(float(r["fee"]) > 0 for r in source) == published["fee"]["sales_charged"]',
            'assert sum(r["collateral_symbol"] in ("stHYPE", "wstHYPE") for r in source) == '
            'published["accounting_audit"]["receipt_backed_collateral_rows"]',
            'assert len(exposure_anomalies) == published["accounting_audit"]["oversized_transfer_rows"]',
            'print("Source rows by chain:", dict(Counter(r["chain"] for r in raw_sales)))',
            'print("Cohort rows / unique receipts / exact-address oracle matches:", '
            "len(source), len(source_by_tx), len(result_by_tx))",
            'print("Written before expiry-window start:", sum(r.written_at < start for r in rows))',
            'print("Gross outflow / non-writer outflow / writer premium:", gross, non_writer, net)',
            'print("Current feeRecipient address / current gasFeeRecipient address / transactions:", '
            'published["fee"]["rysk_fee_recipient_total"], '
            'published["fee"]["gas_fee_recipient_total"], '
            'published["fee"]["gas_transactions"])',
            'print("Rows with non-writer outflow / without:", '
            'published["fee"]["sales_charged"], published["fee"]["sales_free"])',
            'print("Receipt-backed collateral rows:", '
            'published["accounting_audit"]["receipt_backed_collateral_rows"])',
            'print("Oversized collateral transfer and 50-unit sensitivity:", exposure_anomalies)',
        ),
        md(
            "## Measures and independent reconciliation",
            "For each option, payoff = contracts × max(expiry price − strike, 0) for a call; "
            "for a put the price difference is reversed. Option P&L = writer premium − payoff.",
            "The combined scenario adds collateral units × (underlying expiry price − underlying "
            "writing price) for calls. Stablecoin collateral is held at face value. Each collateral "
            "token is treated as one underlying unit: staking exchange rates, PT discounts and "
            "wrapper basis are not measured. This is an estimate, not token-specific investment P&L.",
            "Strike distance is K / S at writing − 1; a threshold of ten percent means "
            "K > 1.10 × S. The asset breakdown includes every underlying in the cohort.",
            "The wallet mean is the sum divided by the number of addresses. The median is the "
            "middle wallet after sorting. Means add across components; medians generally do not.",
            "For rows covered by the included receipt subset, collateral quantities are reconstructed "
            "from transfers of the token named by the option contract. Remaining rows use the "
            "source ledger collateral quantities. Where a token emits share and balance representations, only "
            "the matching token address is included. The pinned receipt evidence and selected log "
            "indices are exported in tables/collateral-reconstruction.json.",
            "One kHYPE writer-to-pool transfer is 5,000 units while the same transaction "
            "mints a 50-unit call. The historical contract implementation deposits the "
            "signed collateralAmount field separately from the minted quantity; the "
            "transaction signed 5,000 kHYPE as collateral. The proxy retains those "
            "5,000 units. tables/collateral-exposure-anomalies.json shows a 50-unit "
            "counterfactual, not a correction. Neither case measures the kHYPE token "
            "exchange rate or exact collateral-token P&L.",
            "A separate decimal-arithmetic calculation derives payoff and aggregates addresses "
            "without calling the main P&L functions. Its assertions compare totals, mean, median "
            "and positive counts to the canonical results.",
        ),
        code(
            'audit = published["accounting_audit"]',
            'for basis in ("option_only", "combined_proxy"):',
            "    print(basis, json.dumps(audit[basis], indent=2))",
            'print("Collateral scenario increment:", audit["collateral_proxy_change"])',
            'print("Mean increment:", audit["mean_change"])',
            'print("Wallet sign transitions:", audit["transitions"])',
            'print("Collateral receipt coverage:", audit["receipt_backed_collateral_rows"])',
            'print("Oversized transfer rows:", audit["oversized_transfer_rows"])',
            'for token in audit["by_collateral"]:',
            "    print(token)",
            'print("Largest collateral-scenario contributions:")',
            'for wallet in audit["top_changes"]:',
            "    print(wallet)",
        ),
        md("## Published metrics", "This cell prints the metrics calculated above and used in the article."),
        code(
            "from option_intel.prose import flatten",
            "for name, value in flatten(published).items():",
            '    print(f"{name:<65} {value:,.10g}")',
        ),
        md(
            "## The three covered-call outcomes",
            "First separate calls in the money at expiry; only then split the worthless "
            "calls by the direction of the asset move. This includes calls opened in the money.",
        ),
        code('for name in ("fell", "under", "through"):', '    print(name, published["buckets"][name])'),
        md("## Generate charts", "Draw PNG and SVG files from the rows and metrics calculated above."),
        code(
            "from option_intel import charts_rysk_year",
            "import os",
            "import matplotlib.pyplot as plt",
            'os.environ["SOURCE_DATE_EPOCH"] = str(int(datetime.fromisoformat(config["asof"]).timestamp()))',
            'plt.rcParams["svg.hashsalt"] = "rysk-year-" + config["asof"]',
            'plt.rcParams.update({"savefig.transparent": False, "figure.facecolor": "white", '
            '"axes.facecolor": "white", "savefig.facecolor": "white"})',
            "from IPython.display import Image, display",
            "for name, draw in charts_rysk_year.FIGURES.items():",
            '    path = draw(rows, published, (BUNDLE / "charts") / f"{name}.png")',
            '    draw(rows, published, (BUNDLE / "charts") / f"{name}.svg")',
            "    display(Image(filename=str(path)))",
        ),
        md(
            "## Interpretation",
            "A wallet is an address, not a person. Results sum individual option "
            "lifetimes, so collateral reused across trades is counted in successive "
            "intervals. Gas, hedges, financing and outside positions are excluded.",
            "Writing prices use cached Hyperliquid candles and, near the strike, "
            "pinned Binance minute observations where available. Expiry payoff uses "
            "only protocol oracle prices. The combined scenario values each collateral "
            "unit as one underlying unit; staking rates, PT discounts and wrapper "
            "basis are not measured. Token-specific collateral P&L and wallet-wide "
            "returns cannot be inferred from this scenario.",
        ),
    ]
