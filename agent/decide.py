"""T1 typed decision layer for the xnicher modeling campaign.

Per-species campaign statistics -> muse-spark strict-JSON decision.
jev pattern (TypeSafe System One): one structured call emits the
decision vector; deterministic machinery executes it. The model never
fits anything — it triages fit diagnostics.

decision_log entries land in data/decisions_{cls}.jsonl — the
auditable record that is the hackathon story.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

from .meta_client import MODEL_AGENT, MetaClient

DECISION_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "niche_fit_decision",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "decision": {
                    "type": "string",
                    "enum": ["accept", "refit_more_starts",
                             "switch_family", "flag_review", "drop"],
                },
                "switch_to": {
                    "type": "string",
                    "enum": ["none", "ip_weighted", "skew_normal",
                             "skew_t", "ncst"],
                },
                "reason": {"type": "string"},
                "confidence": {
                    "type": "string",
                    "enum": ["high", "medium", "low"],
                },
            },
            "required": ["decision", "switch_to", "reason", "confidence"],
            "additionalProperties": False,
        },
    },
}

INSTRUCTIONS = """\
You are the System-1 triage layer for an ellipsoidal niche-model
campaign (88 temperature-precipitation variable pairs per species,
same likelihood, AIC/BIC ranking, 30-yr ERA5 normals, GBIF cells).

Given one species' fit-diagnostics JSON, emit the action:

- accept: a clear winner exists (accepted_global, sane AIC spread).
- refit_more_starts: winner is accepted_noise/needs_more_starts and
  convergence may improve with more starts.
- switch_family: pattern suggests likelihood-family change
  (persistent non-convergence, very high rel_gap, or heavy sampling
  bias -> ip_weighted; asymmetric env_occ -> skew_normal/skew_t).
- flag_review: degenerate or implausible (e.g. tiny n, pathological
  gap) — escalate to System-2.
- drop: hopeless (n too small, no pair ever converged).

Set switch_to to the target family when decision=switch_family else
"none". Reason <= 200 chars, machine-actionable.

Note: only AMBIGUOUS species reach you — clear winners are auto-accepted
upstream, so an "accept" here must still be justified by the diagnostics.
Sparse n (<60), near-tied AIC spreads, marine-dominated ranges, or
degenerate M are legitimate reasons for flag_review / drop / refit even
when the scan reports accepted_global.
"""

@dataclass
class FitSummary:
    specieskey: int
    species: str
    n_cells: int
    n_pairs_tried: int
    n_accepted: int
    best_aic: float | None
    aic_spread_top5: float | None
    flags: dict
    context: str = ""


def decide_species(client: MetaClient, s: FitSummary,
                   model: str = MODEL_AGENT) -> dict:
    user = (
        f"species={s.species} (key {s.specieskey}), occ_cells={s.n_cells}, "
        f"pairs_tried={s.n_pairs_tried}, accepted={s.n_accepted}, "
        f"best_aic={s.best_aic}, aic_spread_top5={s.aic_spread_top5}, "
        f"flags={json.dumps(s.flags)}"
    )
    if s.context:
        user += f"\ntriage_context={s.context}"
    r = client.chat(
        model=model,
        messages=[
            {"role": "system", "content": INSTRUCTIONS},
            {"role": "user", "content": user},
        ],
        response_format=DECISION_SCHEMA,
        temperature=0.0,
    )
    out = json.loads(r["content"])
    out["specieskey"] = s.specieskey
    out["species"] = s.species
    out["model"] = model
    out["decided_at"] = time.time()
    return out
