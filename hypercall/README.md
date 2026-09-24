# Hypercall, measured from its own tape

One notebook. It downloads every input it uses and prints every number the
article quotes.

## Running it

```
pip install -r requirements.txt
jupyter lab notebook.ipynb
```

Then run every cell. It takes about four minutes.

The cut is `2026-09-24T00:00:00Z` and is written into the first code cell as
the default, so a plain run reproduces the published figures. To cut
elsewhere, set `HC_CUTOFF` to another instant; set it to an empty string to run
to now. The first cell prints the cut and the library versions.

Pages land in `snapshots/nb-<stamp>/` as they arrive, verbatim, and the figures
are redrawn into `charts/`.

If a cell fails, the error names the endpoint and, for the per-address cells,
the address. The network cells retry on timeouts and rate limits; a cell that
still fails can be re-run on its own, and everything after it run again.

Headless runs need a per-cell timeout of a few minutes, not the thirty seconds
most runners default to.

## What it reads

| what | where from |
|---|---|
| the tape | `GET api.hypercall.xyz/trades?limit=1000&after_trade_id={n}` |
| open interest, instruments | `GET api.hypercall.xyz/markets`, `/instruments` |
| equity per address | `GET api.hypercall.xyz/portfolio?wallet={addr}` |
| whether an address ever signed | `eth_getTransactionCount` at the block holding the cut |
| transfers on HyperEVM | `eth_getLogs` on `rpc.purroofgroup.com`, an archive node |
| transfers on HyperCore | `POST api.hyperliquid.xyz/info`, `userNonFundingLedgerUpdates` |
| fills on HyperCore | `POST api.hyperliquid.xyz/info`, `userFillsByTime` |
| TVL | `GET api.llama.fi` |

No API key anywhere, and no explorer index: every HyperEVM number comes off an
archive node. Every Hypercall endpoint answers **HTTP 403 without a
`User-Agent` header**, and accepts any value; the notebook sends one.

Both chain reads stop at the block holding the cut, so the tape and the chain
are one function of it and two runs quoting one cut agree. Live fields
(portfolio equity, account value, reported open interest, the TVL line) have no
historical endpoint and move between runs.

## `claims.csv`

Every published quantity, one per row: the claim, its value at this cut, and
the endpoint it came from. It is the index into the notebook: look a number up
by name rather than scrolling for it.

## `data/`

`snapshot-2026-09-24T0000Z.tar.gz` is what the endpoints served at the cut,
byte for byte.

It is here because a venue can change or withdraw an endpoint, and then no one
can re-run this against the state the article describes. **The notebook does
not read it** — it always re-fetches. The archive is evidence of what was
served, not an input, and the two are worth keeping apart.
