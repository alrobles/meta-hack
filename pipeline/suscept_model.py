"""P(species is an H5N1 host) from proximity-to-infected features.

Modernized take on the PNAS susceptibility sims (scripts/09): an
interpretable logistic model on degree-2 interaction terms (the paper's
step_interact spirit) vs a HistGradientBoosting challenger, compared by
repeated stratified CV; the winner is refit on bootstrap resamples for
per-species prediction intervals.

    python3 pipeline/suscept_model.py [--bootstrap 200] [--repeats 20]

Input : data/dist_frame.parquet with columns
        specieskey, species, label, max_j_env_pos, max_j_geo_pos,
        min_d_phy_pos, mean_j_env_pos5, mean_j_geo_pos5, phy_source,
        has_env, has_geo, has_phy
Output: <input_dir>/serve/susceptibility.parquet
        (specieskey, species, label, p_host, p05, p95, phy_source)
"""
import argparse
import os
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=FutureWarning)  # sklearn 1.9 LRCV spam

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BASE_FEATS = ["max_j_env_pos", "max_j_geo_pos", "min_d_phy_pos"]
EXT_FEATS = ["mean_j_env_pos5", "mean_j_geo_pos5"]
IND_FEATS = ["has_env", "has_geo", "has_phy"]
GRID_C = [0.01, 0.1, 1.0, 10.0]     # L2 grid for the logistic model
N_SPLITS = 5
PERM_REPEATS = 10


# ---------------------------------------------------------------- models

def model_A(seed=0, C=None):
    """Logistic on imputed+scaled degree-2 interaction terms.

    C=None -> LogisticRegressionCV tunes L2 on GRID_C by inner 3-fold CV
    (nested inside the outer CV loop, so the comparison stays honest).
    """
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression, LogisticRegressionCV
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import PolynomialFeatures, StandardScaler

    lr = (LogisticRegressionCV(Cs=GRID_C, cv=3, scoring="roc_auc",
                               max_iter=2000, random_state=seed)
          if C is None else
          LogisticRegression(C=C, max_iter=2000, random_state=seed))
    return make_pipeline(
        SimpleImputer(strategy="median"),
        StandardScaler(),
        PolynomialFeatures(degree=2, interaction_only=True,
                           include_bias=False),
        lr)


def model_B(seed=0):
    """HistGradientBoosting challenger — native NaN, no preprocessing."""
    from sklearn.ensemble import HistGradientBoostingClassifier
    return HistGradientBoostingClassifier(
        max_iter=150, learning_rate=0.08, early_stopping=False,
        random_state=seed)


# ---------------------------------------------------------------- helpers

def load_frame(path):
    if not os.path.exists(path):
        sys.exit(f"input not found: {path}\n"
                 "  (dist_frame.parquet is written upstream; "
                 "pass --input to test on synthetic data)")
    df = pd.read_parquet(path).reset_index(drop=True)
    need = ["specieskey", "species", "label"] + BASE_FEATS
    miss = [c for c in need if c not in df.columns]
    if miss:
        sys.exit(f"missing required columns: {miss}")
    df["label"] = pd.to_numeric(df["label"], errors="coerce").fillna(0)
    df["label"] = (df["label"] > 0).astype(int)
    for c in BASE_FEATS + EXT_FEATS + IND_FEATS:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    if "phy_source" not in df.columns:
        df["phy_source"] = "unknown"
    # derive missingness indicators when absent
    for ind, feats in [("has_env", ["max_j_env_pos", "mean_j_env_pos5"]),
                       ("has_geo", ["max_j_geo_pos", "mean_j_geo_pos5"]),
                       ("has_phy", ["min_d_phy_pos"])]:
        if ind not in df.columns:
            have = [f for f in feats if f in df.columns]
            if have:
                df[ind] = df[have].notna().any(axis=1).astype(float)
    return df


def usable(df, col):
    return col in df.columns and df[col].notna().any()


def make_X(df, num_cols):
    inds = [c for c in IND_FEATS if c in df.columns]
    cols = num_cols + inds
    return df[cols].astype(float), cols


def cv_eval(X, y, factory, repeats, seed):
    from sklearn.metrics import average_precision_score, roc_auc_score
    from sklearn.model_selection import RepeatedStratifiedKFold

    ns = int(min(N_SPLITS, y.sum(), len(y) - y.sum()))
    if ns < 2:
        sys.exit("too few examples of a class for stratified CV")
    rskf = RepeatedStratifiedKFold(n_splits=ns, n_repeats=repeats,
                                   random_state=seed)
    rocs, prs = [], []
    for tr, te in rskf.split(X, y):
        m = factory()
        m.fit(X.iloc[tr], y[tr])
        p = m.predict_proba(X.iloc[te])[:, 1]
        rocs.append(roc_auc_score(y[te], p))
        prs.append(average_precision_score(y[te], p))
    return np.asarray(rocs), np.asarray(prs)


def bootstrap_pred(X, y, factory, n_boot, seed):
    """Refit on n_boot resamples; per-species mean, p05, p95 of p_host."""
    rng = np.random.RandomState(seed)
    n = len(X)
    P = np.empty((n_boot, n))
    for b in range(n_boot):
        for _ in range(50):
            idx = rng.randint(0, n, n)
            if len(np.unique(y[idx])) == 2:
                break
        m = factory()
        m.fit(X.iloc[idx], y[idx])
        P[b] = m.predict_proba(X)[:, 1]
    return P.mean(axis=0), np.percentile(P, 5, axis=0), \
        np.percentile(P, 95, axis=0)


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(
        description="H5N1 host-susceptibility model "
                    "(logreg-poly2 vs HGB, repeated CV, bootstrap CIs)")
    ap.add_argument("--input", default=f"{REPO}/data/dist_frame.parquet")
    ap.add_argument("--out", default=None,
                    help="default: <input_dir>/serve/susceptibility.parquet")
    ap.add_argument("--bootstrap", type=int, default=200)
    ap.add_argument("--repeats", type=int, default=20)
    ap.add_argument("--seed", type=int, default=13)
    a = ap.parse_args()

    df = load_frame(a.input)
    y = df["label"].to_numpy()
    npos = int(y.sum())
    print(f"input      : {a.input}")
    print(f"species    : {len(df)}  positives: {npos} "
          f"({100 * npos / len(df):.1f}%)")
    if npos < 10 or len(df) - npos < 10:
        sys.exit("need >=10 positives and >=10 negatives")

    # feature sets: base 3, and +mean_*5 when those carry data (decided by CV)
    base = [c for c in BASE_FEATS if usable(df, c)]
    ext = base + [c for c in EXT_FEATS if usable(df, c)]
    dropped = [c for c in BASE_FEATS + EXT_FEATS if c not in ext]
    if dropped:
        print(f"note       : dropped all-NaN/absent features {dropped}")
    feat_sets = {"base": base}
    if len(ext) > len(base):
        feat_sets["ext"] = ext

    # ---- repeated-CV comparison: 2 models x feature sets
    print(f"\nCV         : {N_SPLITS}-fold x {a.repeats} repeats "
          f"(fold-level mean±sd)")
    print(f"  {'model':<14}{'feats':<7}{'ROC-AUC':>14}{'PR-AUC':>14}")
    rows = []
    for fs_name, cols in feat_sets.items():
        X, xcols = make_X(df, cols)
        for mname, fac in [("logreg_poly2", lambda: model_A()),
                           ("hgbt", lambda: model_B(a.seed))]:
            roc, pr = cv_eval(X, y, fac, a.repeats, a.seed)
            rows.append(dict(model=mname, feats=fs_name, X=X, xcols=xcols,
                             factory=fac, roc=roc, pr=pr))
            print(f"  {mname:<14}{fs_name:<7}"
                  f"{roc.mean():>8.3f}±{roc.std():<6.3f}"
                  f"{pr.mean():>8.3f}±{pr.std():<6.3f}")

    win = max(rows, key=lambda r: r["roc"].mean())
    print(f"\nwinner     : {win['model']} [{win['feats']}] "
          f"ROC-AUC {win['roc'].mean():.3f}, "
          f"features {win['xcols']}")

    # ---- full-data fit; freeze tuned C for cheap bootstrap refits
    X, fac = win["X"], win["factory"]
    m_full = fac().fit(X, y)
    if win["model"] == "logreg_poly2":
        best_C = float(m_full.named_steps["logisticregressioncv"].C_[0])
        print(f"tuned C    : {best_C:g}")
        boot_fac = lambda: model_A(C=best_C)
    else:
        boot_fac = fac

    # ---- permutation importance (ROC-AUC drop) on the full-data fit
    from sklearn.inspection import permutation_importance
    imp = permutation_importance(m_full, X, y, scoring="roc_auc",
                                 n_repeats=PERM_REPEATS,
                                 random_state=a.seed)
    print(f"\nperm importance (ROC-AUC drop, {PERM_REPEATS} repeats):")
    order = np.argsort(-imp.importances_mean)
    for i in order:
        print(f"  {win['xcols'][i]:<18}{imp.importances_mean[i]:>7.4f} "
              f"±{imp.importances_std[i]:.4f}")

    # ---- bootstrap prediction intervals
    print(f"\nbootstrap  : {a.bootstrap} refits of {win['model']} "
          f"on resamples")
    p_host, p05, p95 = bootstrap_pred(X, y, boot_fac, a.bootstrap, a.seed)

    out = pd.DataFrame({
        "specieskey": df["specieskey"],
        "species": df["species"],
        "label": y,
        "p_host": p_host,
        "p05": p05,
        "p95": p95,
        "phy_source": df["phy_source"],
    }).sort_values("p_host", ascending=False).reset_index(drop=True)

    out_path = a.out or os.path.join(os.path.dirname(a.input),
                                     "serve", "susceptibility.parquet")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    out.to_parquet(out_path, index=False)
    print(f"wrote      : {out_path}  ({len(out)} rows)")

    # ---- top-15 predicted susceptibles NOT in the labeled host list
    top = out[out.label == 0].head(15)
    print(f"\ntop-{len(top)} unlabeled species by p_host:")
    print(f"  {'species':<34}{'p_host':>7}  {'[p05–p95]':>17}")
    for _, r in top.iterrows():
        print(f"  {str(r.species)[:33]:<34}{r.p_host:>7.3f}"
              f"  [{r.p05:.3f}–{r.p95:.3f}]")


if __name__ == "__main__":
    main()
