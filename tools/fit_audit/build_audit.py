"""Build the fit-audit static site from exported evidence.

Reads audit_export/sp_<key>/{meta,pairs,occ_z,m_z,starts}.csv produced by
export_evidence.R on the HPC side, joins with data/fit_catalog.csv
(88-pair AIC landscape), data/species_winner.csv and agent decisions,
and writes a browsable HTML site to data/audit/:

  index.html     sortable table of all audited species — flag, rel_gap,
                 nobs, ΔAIC, inside95%, agent verdict; link per species
  sp/<key>.html  env-space: M cloud + occurrences + ellipses at
                 50/90/95/99% + top-K pair ellipses + all multi-start
                 ellipses (dispersion evidence); AIC landscape heatmap;
                 geo scatter of occurrence cells; diagnostics table

Zero dependencies beyond stdlib + plotly.js from CDN.
Usage: .venv/bin/python tools/fit_audit/build_audit.py [export_dir]
"""
import html
import json
import math
import os
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
EXPORT = Path(sys.argv[1] if len(sys.argv) > 1 else DATA / "audit_export")
SITE = DATA / "audit"

PLOTLY = '<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>'
CSS = """<style>
body{font-family:system-ui,sans-serif;margin:1.2rem;background:#0e1116;color:#dde}
a{color:#7dc4ff;text-decoration:none}a:hover{text-decoration:underline}
table{border-collapse:collapse;width:100%;font-size:.85rem}
th,td{padding:.3rem .5rem;border-bottom:1px solid #2a3140;text-align:left}
th{position:sticky;top:0;background:#161b24;cursor:pointer}
tr.flag_review td{color:#ffb86b}tr.good td{color:#9ece9e}
.badge{padding:.1rem .45rem;border-radius:.6rem;font-size:.75rem}
.b-acc{background:#1f3d2b;color:#8fe0a8}.b-noise{background:#3d331f;color:#e0c98f}
.b-rev{background:#4d2233;color:#ff9db3}.b-avg{background:#2b2f4d;color:#aab4ff}
h1,h2{font-weight:600}small{color:#8a94a6}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(680px,1fr));
gap:.4rem;align-items:start}
</style>"""


def ellipse_pts(mu, s1, s2, rho, q, n=120):
    """95/90/50% ellipse boundary of N(mu, Sigma), q = chi2 quantile."""
    import numpy as np
    mu = np.asarray(mu, dtype=float)
    Sig = np.array([[s1 * s1, rho * s1 * s2], [rho * s1 * s2, s2 * s2]])
    vals, vecs = np.linalg.eigh(Sig)
    t = np.linspace(0, 2 * np.pi, n)
    circ = np.stack([np.cos(t), np.sin(t)])
    pts = mu[:, None] + (vecs * np.sqrt(vals * q)) @ circ
    return pts[0].tolist(), pts[1].tolist()


def trace_scatter(x, y, name, color, size=3, opacity=0.8, mode="markers"):
    return dict(type="scatter", mode=mode, x=x, y=y, name=name,
                marker=dict(color=color, size=size, opacity=opacity))


def square_layout(x_title, y_title, legend=False, showlegend=True):
    """Square canvas + isotropic units (1 x-unit = 1 y-unit) — required
    to read a Mahalanobis ellipse correctly in z-space."""
    lay = dict(autosize=False, width=660, height=660,
               margin=dict(l=56, r=16, t=12, b=52),
               xaxis=dict(title=x_title, constrain="domain"),
               yaxis=dict(title=y_title, scaleanchor="x", scaleratio=1,
                          constrain="domain"))
    if legend:
        lay["legend"] = dict(orientation="h", y=-0.12)
    lay["showlegend"] = showlegend
    return lay


def square_layout_free(x_title, y_title):
    """Square canvas, no isotropy (categorical/heatmap panels)."""
    return dict(autosize=False, width=660, height=660,
                margin=dict(l=56, r=16, t=12, b=52),
                xaxis=dict(title=x_title), yaxis=dict(title=y_title))


def species_page(sp_dir):
    meta = pd.read_csv(sp_dir / "meta.csv").iloc[0]
    pairs = pd.read_csv(sp_dir / "pairs.csv") if (sp_dir / "pairs.csv").exists() else pd.DataFrame()
    occ = pd.read_csv(sp_dir / "occ_z.csv") if (sp_dir / "occ_z.csv").exists() else pd.DataFrame()
    m = pd.read_csv(sp_dir / "m_z.csv") if (sp_dir / "m_z.csv").exists() else pd.DataFrame()
    starts = pd.read_csv(sp_dir / "starts.csv") if (sp_dir / "starts.csv").exists() else pd.DataFrame()

    sp = html.escape(str(meta.species))
    plots = []

    # ---- panel 1: env space, winner pair --------------------------------
    if len(pairs) and len(occ):
        w = pairs.iloc[0]
        traces = [trace_scatter(m.t_z.tolist(), m.p_z.tolist(), "M cloud",
                                "#4a5568", 2, 0.35)]
        for q, col, nm in [(14.45, "#7dc4ff", "99%"), (9.21, "#8fe0a8", "95%"),
                           (4.61, "#e0c98f", "90%"), (1.39, "#ff9db3", "50%")]:
            ex, ey = ellipse_pts([w.mu1, w.mu2], w.s1, w.s2, w.rho, q)
            traces.append(dict(type="scatter", mode="lines", x=ex, y=ey,
                               name=f"{nm} ellipse",
                               line=dict(color=col, width=1.4)))
        # runner-up pair ellipses (dashed, thin)
        for _, r in pairs.iloc[1:].iterrows():
            ex, ey = ellipse_pts([r.mu1, r.mu2], r.s1, r.s2, r.rho, 9.21)
            traces.append(dict(type="scatter", mode="lines", x=ex, y=ey,
                               name=f"{r.temp}×{r.prec} (95%)",
                               line=dict(color="#aab4ff", width=0.9, dash="dot")))
        inside = occ[occ.inside95]; outside = occ[~occ.inside95]
        traces.append(trace_scatter(inside.t_z.tolist(), inside.p_z.tolist(),
                                    f"occ inside ({len(inside)})", "#8fe0a8", 4))
        traces.append(trace_scatter(outside.t_z.tolist(), outside.p_z.tolist(),
                                    f"occ outside ({len(outside)})", "#ff9db3", 4))
        traces.append(dict(type="scatter", mode="markers",
                           x=[w.mu1], y=[w.mu2], name="mu",
                           marker=dict(color="#fff", size=8, symbol="x")))
        plots.append(dict(
            id="env", title=f"env space — winner {w.temp}×{w.prec} "
                            f"(z-scored, Box-Cox λ on M)",
            data=traces,
            layout=square_layout(w.temp, w.prec, legend=True)))

    # ---- panel 2: multi-start dispersion --------------------------------
    if len(starts) and len(pairs):
        w = pairs.iloc[0]
        conv = starts[starts.convergence.isin([1, 2])]
        bad = starts[~starts.convergence.isin([1, 2])]
        traces = [trace_scatter(m.t_z.tolist(), m.p_z.tolist(), "M cloud",
                                "#4a5568", 2, 0.25)]
        for _, r in conv.iterrows():
            ex, ey = ellipse_pts([r.mu1, r.mu2], r.s1, r.s2, r.rho, 9.21)
            traces.append(dict(type="scatter", mode="lines", x=ex, y=ey,
                               name=f"start ll={r.loglik:.2f}",
                               line=dict(color="#8fe0a8", width=0.7),
                               showlegend=False))
        for _, r in bad.iterrows():
            ex, ey = ellipse_pts([r.mu1, r.mu2], r.s1, r.s2, r.rho, 9.21)
            traces.append(dict(type="scatter", mode="lines", x=ex, y=ey,
                               name=f"nonconv ll={r.loglik:.2f}",
                               line=dict(color="#ff9db3", width=0.7, dash="dot"),
                               showlegend=False))
        ex, ey = ellipse_pts([w.mu1, w.mu2], w.s1, w.s2, w.rho, 9.21)
        traces.append(dict(type="scatter", mode="lines", x=ex, y=ey, name="best",
                           line=dict(color="#fff", width=2.2)))
        traces.append(trace_scatter(occ.t_z.tolist(), occ.p_z.tolist(), "occ",
                                    "#7dc4ff", 3))
        plots.append(dict(
            id="starts", title=f"multi-start dispersion ({len(conv)} conv, "
                               f"{len(bad)} non-conv; rel_gap={w.rel_gap:.2g})",
            data=traces,
            layout=square_layout(w.temp, w.prec, showlegend=False)))

    # ---- panel 3: AIC landscape 11x8 ------------------------------------
    cat = LOADED["catalog"]
    ct = cat[cat.specieskey == meta.specieskey]
    if len(ct):
        temps = [f"bio{i:02d}" for i in range(1, 12)]
        precs = [f"bio{i:02d}" for i in range(12, 20)]
        z = [[float("nan")] * 8 for _ in range(11)]
        for _, r in ct.iterrows():
            if r.temp in temps and r.prec in precs:
                z[temps.index(r.temp)][precs.index(r.prec)] = r.aic
        zmin = ct.aic.min()
        zz = [[v - zmin if v == v else None for v in row] for row in z]
        traces = [dict(type="heatmap", z=list(map(list, zip(*zz))),
                       x=temps, y=precs, colorscale="Viridis",
                       colorbar=dict(title="ΔAIC"), reversescale=True)]
        plots.append(dict(
            id="aic", title="AIC landscape (Δ vs best)",
            data=traces,
            layout=square_layout_free("temp", "prec")))

    # ---- panel 4: geo scatter -------------------------------------------
    if len(occ) and occ.lat_30.notna().all():
        inside = occ[occ.inside95]; outside = occ[~occ.inside95]
        traces = [
            trace_scatter(inside.lon_30.tolist(), inside.lat_30.tolist(),
                          "inside95", "#8fe0a8", 3),
            trace_scatter(outside.lon_30.tolist(), outside.lat_30.tolist(),
                          "outside", "#ff9db3", 3)]
        plots.append(dict(
            id="geo", title="occurrence cells (1/30°)",
            data=traces,
            layout=square_layout("lon", "lat")))

    # ---- diagnostics table ----------------------------------------------
    ver = LOADED["verdicts"].get(int(meta.specieskey), {})
    rows = []
    for c in ["specieskey", "class", "n_occ", "n_m", "m_subsampled"]:
        rows.append(f"<tr><td>{c}</td><td>{html.escape(str(meta[c]))}</td></tr>")
    if len(pairs):
        w = pairs.iloc[0]
        inside_pct = 100 * float(occ.inside95.mean()) if len(occ) else float("nan")
        rows += [
            f"<tr><td>winner</td><td>{w.temp}×{w.prec}</td></tr>",
            f"<tr><td>flag</td><td>{html.escape(str(w.flag))}</td></tr>",
            f"<tr><td>rel_gap / n_conv</td><td>{w.rel_gap:.3g} / {w.n_conv}</td></tr>",
            f"<tr><td>ll spread (conv starts)</td><td>{w.ll_spread:.4g}</td></tr>",
            f"<tr><td>occ inside 95%</td><td>{inside_pct:.1f}%</td></tr>",
            f"<tr><td>mu / s / ρ</td><td>({w.mu1:.2f},{w.mu2:.2f}) / "
            f"({w.s1:.2f},{w.s2:.2f}) / {w.rho:.3f}</td></tr>"]
    if ver:
        rows.append(f"<tr><td>agent verdict</td><td><b>"
                    f"{html.escape(str(ver.get('verdict','')))}</b> — "
                    f"{html.escape(str(ver.get('why','')))[:200]}</td></tr>")
    diag = f"<table>{''.join(rows)}</table>"

    divs = []
    for i, p in enumerate(plots):
        divs.append(f"<div><h2>{html.escape(p['title'])}</h2>"
                    f"<div id='p{i}'></div>"
                    f"<script>Plotly.newPlot('p{i}',{json.dumps(p['data'])},"
                    f"{json.dumps(p['layout'])},{{responsive:true}})</script></div>")
    body = (f"<h1>{sp} <small>({int(meta.specieskey)})</small></h1>"
            f"<p><a href='../index.html'>← index</a></p>"
            f"{diag}<div class='grid'>" + "".join(divs) + "</div>")
    return (f"<!doctype html><meta charset=utf-8><title>audit {sp}</title>"
            f"{CSS}{PLOTLY}{body}")


def index_row(meta, pairs, occ):
    w = pairs.iloc[0] if len(pairs) else {}
    flag = str(w.get("flag", "?"))
    cls = {"accepted_global": "b-acc", "accepted_noise": "b-noise",
           "suggest_average": "b-avg"}.get(flag, "b-rev")
    inside = f"{100*occ.inside95.mean():.0f}%" if len(occ) else "—"
    ver = LOADED["verdicts"].get(int(meta.specieskey), {})
    vtxt = html.escape(str(ver.get("verdict", "")))
    row_cls = "flag_review" if "review" in (vtxt + flag).lower() else "good"
    return (f"<tr class='{row_cls}'>"
            f"<td><a href='sp/{int(meta.specieskey)}.html'>"
            f"{html.escape(str(meta.species))}</a></td>"
            f"<td>{meta['class']}</td>"
            f"<td>{w.get('temp','?')}×{w.get('prec','?')}</td>"
            f"<td><span class='badge {cls}'>{flag}</span></td>"
            f"<td>{float(w.get('rel_gap', float('nan'))):.3g}</td>"
            f"<td>{int(meta.n_occ)}</td><td>{inside}</td>"
            f"<td>{float(w.get('ll_spread', float('nan'))):.3g}</td>"
            f"<td>{vtxt}</td></tr>")


def load_verdicts():
    """decisions_all.jsonl (full catalog) preferred; delta csv as fallback."""
    out = {}
    jl = DATA / "serve" / "decisions_all.jsonl"
    if jl.exists():
        for line in jl.read_text().splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            k = r.get("specieskey")
            if k:
                out[int(k)] = dict(verdict=r.get("decision", ""),
                                   why=r.get("reason", ""))
    p = DATA / "serve" / "decisions_delta.csv"
    if p.exists():
        d = pd.read_csv(p)
        for _, r in d.iterrows():
            k = int(r.specieskey) if "specieskey" in r else None
            if k and k not in out:
                out[k] = dict(verdict=r.get("decision", ""), why=r.get("why", ""))
    return out


def main():
    global LOADED
    LOADED = {
        "catalog": pd.read_csv(DATA / "fit_catalog.csv"),
        "winner": pd.read_csv(DATA / "species_winner.csv"),
        "verdicts": load_verdicts(),
    }
    sp_dirs = sorted(d for d in EXPORT.iterdir()
                     if d.is_dir() and d.name.startswith("sp_"))
    if not sp_dirs:
        sys.exit(f"no sp_* dirs under {EXPORT}")
    (SITE / "sp").mkdir(parents=True, exist_ok=True)
    rows = []
    for d in sp_dirs:
        meta = pd.read_csv(d / "meta.csv").iloc[0]
        pairs = pd.read_csv(d / "pairs.csv") if (d / "pairs.csv").exists() else pd.DataFrame()
        occ = pd.read_csv(d / "occ_z.csv") if (d / "occ_z.csv").exists() else pd.DataFrame()
        (SITE / "sp" / f"{int(meta.specieskey)}.html").write_text(species_page(d))
        rows.append(index_row(meta, pairs, occ))
    head = ("<tr><th>species</th><th>class</th><th>winner</th><th>flag</th>"
            "<th>rel_gap</th><th>n_occ</th><th>in95</th><th>ll_spread</th>"
            "<th>verdict</th></tr>")
    idx = (f"<!doctype html><meta charset=utf-8><title>fit audit</title>{CSS}"
           f"<h1>WildSeek fit audit</h1><p><small>{len(rows)} species — "
           f"click a row's species name for evidence pages. Green = clean, "
           f"orange = review-worthy.</small></p><table>{head}"
           f"{''.join(rows)}</table>")
    (SITE / "index.html").write_text(idx)
    print(f"wrote {SITE}/index.html + {len(rows)} species pages")


if __name__ == "__main__":
    main()
