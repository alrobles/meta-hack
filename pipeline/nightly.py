"""Nightly conveyor — the WildSeek risk-intelligence factory.

Factory roles -> Muse surfaces:
  ENTRADA   fetch_aphis.py             -> fresh APHIS layers (local API)
  SPEC      agent/planner.py           -> data/run_spec.json
  BUILDER   hpc sbatch + sacct poll     -> risk chain (+refit if spec)
  REFIT     local labels->dist_frame->model (GBIF API + sklearn venv)
  APPROVER  agent/qa.py on exported data
  PRODUCCION rsync -> deploy host + /health verify
  BRAIN     run manifest; muse-glimmer (MI210, free) writes the shift log
  SELF-HEAL on stage failure -> `muse exec` gets the traceback, patches,
            stage retries once. Opt-in via WILDSEEK_SELFHEAL=1 or --heal;
            off by default (no unattended code edits without a knob).

Usage:  python pipeline/nightly.py           # full run
        python pipeline/nightly.py --dry     # spec only, no compute
Cron:   15 3 * * * cd <repo> && WILDSEEK_BEEG=... WILDSEEK_DEPLOY=... \\
            MODEL_API_KEY=... .venv/bin/python pipeline/nightly.py

Env: WILDSEEK_BEEG    staging dir on the HPC cluster (required full run)
     WILDSEEK_DEPLOY  rsync target  host:dir/  for serve_data (required)
     WILDSEEK_KUHPC   ssh alias for the cluster (default "kuhpc")
     WILDSEEK_SELFHEAL=1 allow `muse exec --yolo` self-heal (default off)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = ROOT / ".venv/bin/python"
KUHPC = os.environ.get("WILDSEEK_KUHPC", "kuhpc")
BEEG = os.environ.get("WILDSEEK_BEEG", "")
TERMINAL = os.environ.get("WILDSEEK_DEPLOY", "")
RUNS = ROOT / "data/runs"

SLURM_DONE = {"COMPLETED", "FAILED", "CANCELLED", "TIMEOUT", "BOOT_FAIL",
              "NODE_FAIL", "OUT_OF_MEMORY", "PREEMPTED", "DEADLINE"}


def sh(cmd: str, check: bool = True, timeout: int = 3600) -> str:
    r = subprocess.run(cmd, shell=True, capture_output=True,
                       text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"FAILED [{cmd[:80]}]:\n"
                           f"{r.stdout[-1500:]}\n{r.stderr[-1500:]}")
    return (r.stdout + r.stderr).strip()


def need(val: str, var: str) -> str:
    if not val:
        raise RuntimeError(f"set {var} first (see module docstring)")
    return val


def wait_slurm(jobid: str, poll: int = 120, budget: float = 6.5 * 3600
               ) -> str:
    """Poll sacct until a terminal state. Submit+poll (not sbatch --wait)
    because the job outlives any subprocess timeout — --wait used to get
    killed at the timeout and mark a live job as failed. Budget exceeds
    the sixhour partition cap (6h) so a healthy job always resolves."""
    t0 = time.time()
    while time.time() - t0 < budget:
        out = sh(f"ssh {KUHPC} 'sacct -j {jobid} -n -o State | head -1'",
                 check=False, timeout=60)
        state = (out.split() or [""])[0].upper()
        if state in SLURM_DONE:
            return state
        time.sleep(poll)
    return "POLL_TIMEOUT"


def self_heal(stage_name: str, err: str, heal: bool) -> bool:
    """Hand the traceback to Muse Code; returns True if it produced an
    edit worth retrying. muse exec runs in this repo — it can read the
    failing script and patch it. Disabled unless WILDSEEK_SELFHEAL=1 or
    --heal (unattended --yolo edits in prod are opt-in, not default)."""
    if not heal:
        print("self-heal disabled (WILDSEEK_SELFHEAL=1 or --heal to arm)",
              flush=True)
        return False
    prompt = (
        f"The nightly WildSeek pipeline stage '{stage_name}' just failed "
        f"with this error:\n\n{err[-3000:]}\n\nDiagnose and apply the "
        f"minimal fix to the offending script so the stage can re-run. "
        f"Do not redesign; patch only what the traceback demands.")
    r = subprocess.run(["muse", "exec", prompt, "--yolo"],
                       cwd=ROOT, capture_output=True, text=True,
                       timeout=900)
    out = r.stdout + r.stderr
    print("=== MUSE HEAL ===\n", out[-2000:], flush=True)
    return r.returncode == 0 and len(out) > 100


def stage(name, retries=1):
    def deco(fn):
        def wrap(man):
            err = None
            for attempt in range(1 + retries):
                t0 = time.time()
                print(f"=== {name} try{attempt} "
                      f"{datetime.now():%H:%M:%S}", flush=True)
                try:
                    out = fn(man)
                    man["stages"][name] = {
                        "ok": True, "secs": round(time.time() - t0, 1),
                        **({"out": out} if isinstance(out, dict) else {})}
                    return
                except Exception as ex:  # noqa: BLE001
                    err = str(ex)
                    print(f"{name} FAILED: {err[:400]}", flush=True)
                    if attempt < retries:
                        healed = self_heal(name, err,
                                           bool(man.get("heal")))
                        man["stages"].setdefault("heals", []).append(
                            {"stage": name, "attempt": attempt,
                             "healed": healed})
                        if not healed:
                            break
            raise RuntimeError(err)
        return wrap
    return deco


@stage("ENTRADA")
def entrada(man):
    print(sh(f"{PY} {ROOT}/pipeline/fetch_aphis.py", timeout=1200)[-800:])


@stage("SPEC")
def spec(man):
    sys.path.insert(0, str(ROOT))
    from agent import planner
    s = planner.plan(ROOT / "data", ROOT / "data/serve")
    (ROOT / "data/run_spec.json").write_text(
        json.dumps(planner.asdict(s), indent=1, default=str))
    man["spec"] = planner.asdict(s)
    print(json.dumps(man["spec"], indent=1, default=str))


@stage("REFIT")
def refit(man):
    """Only runs when the spec demands it (new host species)."""
    print(sh(f"{PY} {ROOT}/pipeline/species_labels.py", timeout=1800)[-400:])
    print(sh(f"{PY} {ROOT}/pipeline/dist_frame.py", timeout=1800)[-600:])
    print(sh(f"{PY} {ROOT}/pipeline/suscept_model.py", timeout=2400)[-800:])
    sh(f"scp -q data/serve/susceptibility.parquet "
       f"{KUHPC}:{need(BEEG, 'WILDSEEK_BEEG')}/", timeout=120)


@stage("BUILDER")
def builder(man):
    beeg = need(BEEG, "WILDSEEK_BEEG")
    sh("rsync -a data/aphis_*.parquet data/aphis_flyways.json "
       "data/run_spec.json data/detect_seen.json "
       f"{KUHPC}:{beeg}/", timeout=300)
    refit_on = man["spec"]["stages"].get("refit")
    script = "nightly_refit.sbatch" if refit_on else "nightly.sbatch"
    # logs/ must exist before submit — relative #SBATCH --output resolves
    # against the submit cwd at job start, slurm won't create it
    sh(f"ssh {KUHPC} 'mkdir -p {beeg}/logs'", timeout=60)
    job = sh(f"ssh {KUHPC} 'cd {beeg} && sbatch --parsable "
             f"--export=ALL,WILDSEEK_BEEG={beeg} {script}'", timeout=120)
    toks = job.strip().split()
    jobid = toks[-1].split(";")[0] if toks else ""
    if not jobid.isdigit():
        raise RuntimeError(f"sbatch returned no jobid: {job[:200]}")
    print("submitted job", jobid, flush=True)
    state = wait_slurm(jobid)
    man["stages"].setdefault("slurm", {})[script] = \
        {"job": jobid, "state": state}
    if state != "COMPLETED":
        raise RuntimeError(f"slurm {jobid} ({script}) -> {state}")


@stage("APPROVER")
def approver(man):
    stg = ROOT / "data/stage_serve"
    stg.mkdir(exist_ok=True)
    sh(f"rsync -a --delete {KUHPC}:{need(BEEG, 'WILDSEEK_BEEG')}"
       f"/serve_data/ {stg}/", timeout=900)
    sys.path.insert(0, str(ROOT))
    from agent import qa
    rep = qa.check(stg, ROOT / "data", manifest=man)
    man["qa"] = rep
    if not rep["pass"]:
        raise RuntimeError("QA GATES FAILED — deploy blocked")


@stage("PRODUCCION")
def produccion(man):
    # doble via (FACTORY.md): refit del modelo = tarea critica ->
    # el deploy a produccion se hace, pero la corrida queda marcada
    # needs_human_release -> solo el humano dispara release.sh al repo
    # publico.
    man["critical"] = bool(man["spec"]["stages"].get("refit"))
    man["needs_human_release"] = man["critical"]
    stg = ROOT / "data/stage_serve"
    sh(f"rsync -a {stg}/ {need(TERMINAL, 'WILDSEEK_DEPLOY')}", timeout=900)
    health = sh("curl -s -m 10 https://wildseek.ecoseek.org/health",
                check=False)
    man["health"] = health[:200]
    print("health:", health[:200])


@stage("BRIEFING")
def briefing_stage(man):
    """Re-bake the public voice (wonder) over tonight's artifacts.
    The local app/data mirror is refreshed from staging first so the
    briefing reads tonight's facts, not yesterday's."""
    sh(f"rsync -a --delete {ROOT}/data/stage_serve/ "
       f"{ROOT}/app/data/", timeout=900)
    print(sh(f"{PY} {ROOT}/pipeline/precompute_agent.py",
             timeout=1200)[-400:])
    tgt = need(TERMINAL, "WILDSEEK_DEPLOY")
    sh(f"rsync -a app/data/briefing.json {tgt}", timeout=300)
    sh(f"rsync -a --include='explain_*.json' --exclude='*' "
       f"app/data/ {tgt}", timeout=300)


@stage("BRAIN")
def brain(man):
    try:
        sys.path.insert(0, str(ROOT))
        from agent.meta_client import MODEL_LOCAL, MetaClient
        c = MetaClient.local_mi210()
        r = c.chat([{"role": "user", "content":
                   "Eres el shift-log de una fabrica de inteligencia de "
                   "riesgo HPAI. Resume este manifiesto de corrida en UNA "
                   "linea (espanol, tono operario de fabrica): "
                   + json.dumps(man, default=str)[:3000]}],
                  model=MODEL_LOCAL, max_tokens=900)
        man["shift_log"] = r["content"] or ""
    except Exception as ex:  # noqa: BLE001
        man["shift_log"] = f"(glimmer unavailable: {ex})"
    print(man["shift_log"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--heal", action="store_true",
                    help="allow muse exec --yolo self-heal on stage "
                         "failure (same as WILDSEEK_SELFHEAL=1)")
    a = ap.parse_args()

    RUNS.mkdir(parents=True, exist_ok=True)
    man = {"run": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "stages": {},
           "heal": a.heal or
                   os.environ.get("WILDSEEK_SELFHEAL", "") == "1"}
    try:
        for s in (entrada, spec):
            s(man)
        if not a.dry:
            rest = ([refit] if man["spec"]["stages"].get("refit")
                    else []) + [builder, approver, produccion,
                                briefing_stage]
            for s in rest + [brain]:
                s(man)
        else:
            brain(man)
    except Exception as ex:  # noqa: BLE001
        man["failed"] = str(ex)
        print("RUN FAILED:", ex)
    out = RUNS / f"nightly_{datetime.now():%Y%m%d_%H%M}.json"
    out.write_text(json.dumps(man, indent=1, default=str))
    print("manifest:", out)
    sys.exit(1 if "failed" in man else 0)


if __name__ == "__main__":
    main()
