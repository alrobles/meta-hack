"""Approver — quorum 3/4 de cuatro revisores (docs/FACTORY.md).

  codigo    gates duros: conteos, bounds, PNGs no vacios, geojson valido
  seguridad la corrida no colapsa datos vs. el run previo
  ux        meta.json completo + archivos que lee el frontend existen
  brain     muse-spark lee el manifest y emite veredicto razonado

Aprueba si codigo+seguridad pasan Y >=3/4 en total. El resultado cae en
<serve>/qa_report.json; el fallo bloquea deploy y escala al canal humano.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

META_KEYS_UI = ("suit_bbox", "risk_png", "risk_bbox",
                "reservoir_sum_png", "n_species", "n_detections")


def _meta(serve: Path) -> dict | None:
    """meta.json absent/corrupt -> reviewers report a failed check instead
    of crashing the approver."""
    try:
        return json.loads((serve / "meta.json").read_text())
    except Exception:
        return None


def _codigo(serve: Path) -> list[dict]:
    c = []
    m = _meta(serve)
    if m is None:
        return [{"name": "meta.json readable", "ok": False}]
    c.append({"name": "n_species>=1000",
              "ok": (m.get("n_species") or 0) >= 1000,
              "value": m.get("n_species")})
    c.append({"name": "n_detections>=8000",
              "ok": (m.get("n_detections") or 0) >= 8000,
              "value": m.get("n_detections")})
    sus = serve / "susceptibility.parquet"
    if sus.exists():
        s = pd.read_parquet(sus)
        c.append({"name": "p_host bounds",
                  "ok": bool(s["p_host"].between(0, 1).all()),
                  "value": f"{s.p_host.min():.3f}-{s.p_host.max():.3f}"})
        c.append({"name": "top-10 mostly known hosts",
                  "ok": bool(s.nlargest(10, "p_host")["label"]
                             .eq(1).mean() >= 0.7),
                  "value": f"{s.nlargest(10,'p_host')['label'].mean():.0%}"})
    else:
        c.append({"name": "susceptibility.parquet", "ok": False})
    det = serve / "detections.geojson"
    try:
        n = len(json.loads(det.read_text()).get("features", []))
        c.append({"name": "detections.geojson", "ok": n >= 8000,
                  "value": n})
    except Exception as ex:  # noqa: BLE001
        c.append({"name": "detections.geojson parse", "ok": False,
                  "value": str(ex)[:80]})
    return c


def _seguridad(serve: Path, data: Path) -> list[dict]:
    c = []
    prev = data / "detect_seen.json"
    m = _meta(serve)
    if m is None:
        return [{"name": "meta.json readable", "ok": False}]
    if prev.exists():
        p = json.loads(prev.read_text())
        drop = (p.get("n", 0) - (m.get("n_detections") or 0)) \
            / max(p.get("n", 1), 1)
        c.append({"name": "no detection collapse (>5% drop fails)",
                  "ok": drop < 0.05,
                  "value": f"{drop:+.1%}"})
    for png in ("risk.png", "reservoir_sum.png"):
        p = serve / png
        c.append({"name": f"{png} non-trivial",
                  "ok": p.exists() and p.stat().st_size > 50_000,
                  "value": p.stat().st_size if p.exists() else 0})
    return c


def _ux(serve: Path) -> list[dict]:
    m = _meta(serve)
    if m is None:
        return [{"name": "meta.json readable", "ok": False}]
    missing = [k for k in META_KEYS_UI if k not in m]
    c = [{"name": "meta.json ui keys", "ok": not missing,
          "value": f"missing: {missing}" if missing else "all"}]
    for png in ("risk.png", "reservoir_sum.png", "reservoir_max.png",
                "reservoir_prob.png"):
        c.append({"name": f"{png} exists",
                  "ok": (serve / png).exists()})
    return c


def _brain(man: dict | None) -> list[dict]:
    if man is None:
        return [{"name": "brain review", "ok": False,
                 "value": "no manifest"}]
    try:
        from .meta_client import MODEL_AGENT, MetaClient
        c = MetaClient()
        r = c.chat([
            {"role": "system", "content":
             "Eres el revisor 'brain' de una fabrica de datos "
             "cientificos (HPAI). Constitucion: rutinas van directo; "
             "refits o degradacion de datos son criticos. Responde SOLO "
             "JSON: {\"approve\": bool, \"reason\": str}"},
            {"role": "user", "content":
             "Manifiesto de la corrida nocturna:\n"
             + json.dumps(man, default=str)[:4000]}],
            model=MODEL_AGENT, max_tokens=300)
        j = json.loads(r["content"].strip().strip("`")
                       .removeprefix("json").strip())
        return [{"name": "brain review", "ok": bool(j["approve"]),
                 "value": j.get("reason", "")[:200]}]
    except Exception as ex:  # noqa: BLE001
        return [{"name": "brain review", "ok": False,
                 "value": f"abstain: {ex}"[:120]}]


def check(serve: Path | str = "app/data", data: Path | str = "data",
          manifest: dict | None = None) -> dict:
    serve, data = Path(serve), Path(data)
    reviews = {"codigo": _codigo(serve), "seguridad": _seguridad(serve, data),
               "ux": _ux(serve), "brain": _brain(manifest)}
    verdict = {k: all(c["ok"] for c in v) for k, v in reviews.items()}
    n_pass = sum(verdict.values())
    hard = verdict["codigo"] and verdict["seguridad"]
    passed = hard and n_pass >= 3
    report = {"pass": passed, "verdict": verdict,
              "quorum": f"{n_pass}/4", "reviews": reviews}
    (serve / "qa_report.json").write_text(json.dumps(report, indent=1))
    return report


if __name__ == "__main__":
    import sys
    r = check()
    print(json.dumps(r, indent=1))
    sys.exit(0 if r["pass"] else 1)
