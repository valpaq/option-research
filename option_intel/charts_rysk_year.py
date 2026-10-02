from datetime import datetime, timedelta
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

from option_intel import article_charts as charts
from option_intel.charts import CALL, PUT, _finish


def expiry_results(rows, figures, path):
    series = figures["expiry_results"]
    dates = [datetime.fromisoformat(x["expiry"]) for x in series]
    fig, axes = plt.subplots(2, 1, figsize=(10, 6), sharex=True, sharey=True)
    for ax, side, color in zip(axes, ("calls", "puts"), (CALL, PUT), strict=True):
        ax.bar(dates, [x[side] for x in series], width=3, color=color)
        ax.axhline(0, color="#777777", linewidth=0.8)
        ax.set_ylabel(f"{side.capitalize()} estimated result")
        ax.yaxis.set_major_formatter(
            FuncFormatter(lambda x, _: ("−" if x < 0 else "") + f"${abs(x) / 1e6:g}M")
        )
        ax.grid(axis="y", alpha=0.2)
    start = datetime.fromisoformat(figures["window"]["start"])
    end = datetime.fromisoformat(figures["window"]["end"])
    ticks = [start] + [datetime(2026, m, 1, tzinfo=end.tzinfo) for m in (1, 3, 5, 7)] + [end]
    axes[-1].set_xticks(ticks)
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%d %b\n%Y"))
    axes[-1].set_xlim(start - timedelta(days=2), end + timedelta(days=2))
    axes[-1].set_xlabel("Expiry date (08:00 UTC); call collateral uses underlying-price proxy")
    fig.tight_layout()
    path.parent.mkdir(exist_ok=True)
    return _finish(fig, axes[-1], path)


def basis_comparison(rows, figures, path):
    audit = figures["accounting_audit"]
    fig, axes = plt.subplots(1, 3, figsize=(11, 4.4))
    labels = ["option leg", "with collateral"]
    names = {"mean": "mean per wallet", "median": "median per wallet", "positive_share": "wallets positive"}
    for ax, key in zip(axes, ("mean", "median", "positive_share"), strict=True):
        values = [audit[basis][key] for basis in ("option_only", "combined_proxy")]
        ax.bar([0, 1], values, color=[CALL, PUT], width=0.6)
        ax.axhline(0, color="#777777", linewidth=0.8)
        ax.set_xticks([0, 1], labels, fontsize=13)
        ax.tick_params(axis="x", length=0, pad=6)
        ax.set_yticks([])
        for side in ("left", "right", "top"):
            ax.spines[side].set_visible(False)
        formatter = (
            (lambda v: f"{v:.1%}")
            if key == "positive_share"
            else (lambda v: ("−" if v < 0 else "") + f"${abs(v):,.0f}")
        )
        for x, value in enumerate(values):
            ax.annotate(
                charts._t(formatter(value)),
                (x, value),
                xytext=(0, 6 if value >= 0 else -6),
                textcoords="offset points",
                ha="center",
                va="bottom" if value >= 0 else "top",
                fontsize=16,
                fontweight="bold",
                color="#555555",
            )
        low, high = min(0, *values), max(0, *values)
        pad = (high - low) * 0.32
        ax.set_ylim(low - (pad if low < 0 else 0), high + pad * 1.6)
        ax.text(
            0.5, 0.97, names[key], transform=ax.transAxes, ha="center", va="top", fontsize=14, color="#555555"
        )
    fig.tight_layout(w_pad=3)
    return _finish(fig, axes[-1], path)


FIGURES = {
    ("year-after-assignment" if name == "after-assignment" else name): draw
    for name, draw in charts.FIGURES.items()
}
FIGURES["expiry-results"] = expiry_results
FIGURES["basis-comparison"] = basis_comparison
FIGURES["year-persistence"] = lambda rows, figures, path: charts.persistence(figures, path)
