"""Egg-price timeline w/ HPAI outbreak annotation — WildSeek demo asset.

Source: BLS via FRED APU0000708111 (US city avg, grade-A large eggs,
per dozen). Public CSV, no key. Regenerable: python egg_prices.py
"""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=APU0000708111"
OUT = Path(__file__).resolve().parent.parent / "docs/assets/egg_prices.png"
OUT.parent.mkdir(parents=True, exist_ok=True)

OUTBREAK = "2022-02-08"   # first HPAI commercial-flock confirmation (USDA)


def main() -> None:
    df = pd.read_csv(URL, comment="#").rename(
        columns={c: "price" for c in ("APU0000708111", "value")})
    df["observation_date"] = pd.to_datetime(df["observation_date"])
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df = df.dropna()
    df = df[df.observation_date >= "2006-01-01"]

    peak_i = df["price"].idxmax()
    peak = df.loc[peak_i]
    tr_win = df[df.observation_date.between("2020-01-01", "2020-12-01")]
    trough = tr_win.loc[tr_win["price"].idxmin()]

    fig, ax = plt.subplots(figsize=(14, 7), dpi=150)
    fig.patch.set_facecolor("#0a0f14")
    ax.set_facecolor("#0a0f14")

    ax.plot(df.observation_date, df.price, color="#4ade80", lw=1.6,
            marker="o", ms=2.6, mfc="#4ade80", mec="none", zorder=3)
    ax.fill_between(df.observation_date, df.price, 0,
                    color="#4ade80", alpha=0.16, zorder=2)
    ax.axvspan(pd.Timestamp(OUTBREAK), df.observation_date.max(),
               color="#4ade80", alpha=0.05, zorder=1)

    ax.annotate("Avian flu\noutbreak",
                xy=(pd.Timestamp(OUTBREAK), 1.6), xytext=(
                    pd.Timestamp("2019-03-01"), 3.3),
                color="#c9d1d9", fontsize=12, ha="center",
                arrowprops=dict(arrowstyle="-", color="#8b949e",
                                lw=1.1))
    ax.axvline(pd.Timestamp(OUTBREAK), color="#8b949e",
               lw=0.9, ls=":", alpha=0.7)

    for d, v, lbl, dy in [(trough.observation_date, trough.price,
                           f"${trough.price:.2f}", -0.45),
                          (peak.observation_date, peak.price,
                           f"${peak.price:.2f}", 0.35)]:
        ax.scatter([d], [v], s=40, color="#e6edf3", zorder=4)
        ax.annotate(lbl, xy=(d, v), xytext=(d, v + dy),
                    color="#e6edf3", fontsize=13, ha="center",
                    fontweight="bold",
                    arrowprops=dict(arrowstyle="-", color="#8b949e"))

    ax.annotate("Eggs", xy=(df.observation_date.iloc[-1],
                              df.price.iloc[-1] + 0.12),
                xytext=(10, 0), textcoords="offset points",
                color="#4ade80", fontsize=13, fontweight="bold")

    ax.set_xlim(df.observation_date.min(), df.observation_date.max())
    ax.set_ylim(0, df.price.max() * 1.12)
    ax.yaxis.set_major_formatter("${x:.0f}")
    ax.xaxis.set_major_locator(mdates.YearLocator(2))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    for s in ax.spines.values():
        s.set_color("#30363d")
    ax.tick_params(colors="#8b949e", labelsize=11)
    ax.grid(axis="y", color="#21262d", ls="--", alpha=0.6)
    ax.set_title("Food prices", color="#e6edf3",
                 fontsize=26, fontweight="bold", pad=46, loc="center",
                 fontfamily="monospace")
    fig.text(0.5, 0.872, "Average egg price (dozen) — BLS/FRED "
             "APU0000708111 · shaded = HPAI outbreak",
             color="#8b949e", fontsize=11, ha="center")
    fig.text(0.02, 0.5, "USD", color="#8b949e", fontsize=10,
             rotation=90, va="center")
    fig.tight_layout(rect=(0.02, 0.0, 0.97, 0.97))
    fig.savefig(OUT, facecolor=fig.get_facecolor())
    print(f"-> {OUT} | trough {trough.observation_date:%Y-%m} "
          f"${trough.price} | peak {peak.observation_date:%Y-%m} "
          f"${peak.price}")


if __name__ == "__main__":
    main()
