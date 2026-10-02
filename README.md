# Option research

Notebooks, data snapshots and calculations for on-chain options research.

- [Hypercall](hypercall/notebook.ipynb) — trades, funding and rewards.
- [Rysk V12](research/rysk/rysk-year/notebook.ipynb) — 40,067 primary option sales
  across 2,856 writer addresses, expiring from 10 October 2025 to 9 October 2026.

## Run the Rysk notebook

Use Python 3.14. From the repository root:

```sh
python3.14 -m venv .venv
.venv/bin/python -m pip install -r research/rysk/rysk-year/requirements-lock.txt
.venv/bin/python -m pip install --no-deps -e .
```

Open `research/rysk/rysk-year/notebook.ipynb` with this environment and run all
cells, or execute it from the terminal:

```sh
cd research/rysk/rysk-year
../../../.venv/bin/python - <<'PY'
import nbformat
from nbclient import NotebookClient

notebook = nbformat.read('notebook.ipynb', as_version=4)
NotebookClient(notebook, timeout=None, kernel_name='python3').execute()
nbformat.write(notebook, 'notebook.ipynb')
PY
```

The calculation runs offline, verifies input and code hashes, and regenerates
the tables and charts. Installing dependencies requires network access.

The notebook reports premium minus option payoff, and a second estimate that
adds the change in call collateral value. The latter treats each collateral
token as one underlying unit; it is not a measured wallet-wide return.

See [method and evidence](research/rysk/rysk-year/REPRODUCE.md),
[input checksums](research/rysk/rysk-year/data/CHECKSUMS.txt), and
[result tables](research/rysk/rysk-year/tables/).

Run the tests from the repository root with `.venv/bin/python -m pytest`.
Hypercall has its own [setup instructions](hypercall/README.md).
