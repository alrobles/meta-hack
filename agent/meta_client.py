"""Meta Model API client with per-call cost accounting.

OpenAI-compatible surface. Key comes from the MODEL_API_KEY env var —
export it from your key store, never hardcode.

    export MODEL_API_KEY="<your key>"

Pricing: docs/meta-api.md. Contributor-mode models are ~20x cheaper on
output but grant Meta training rights on the content — use only for
public data (papers, abstracts).
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field

import requests

BASE_URL = os.environ.get("META_BASE_URL", "https://api.meta.ai/v1")

# USD per 1M tokens: (cached_input, input, output)
PRICES = {
    "muse-wonder-1.0": (0.20, 4.00, 20.00),
    "muse-spark-1.3": (0.15, 1.25, 4.25),
    "muse-spark-1.2": (0.15, 1.25, 4.25),
    "muse-spark-1.1": (0.15, 1.25, 4.25),
    "muse-spark-1.3-contributor": (0.002, 0.10, 0.20),
    "muse-spark-1.2-contributor": (0.002, 0.10, 0.20),
}

MODEL_EXTRACTION = "muse-spark-1.2-contributor"  # bulk lit-mining
MODEL_AGENT = "muse-spark-1.3"                   # orchestration brain
MODEL_SHOWCASE = "muse-wonder-1.0"               # final/demo reasoning
MODEL_LOCAL = "muse-glimmer"                     # self-hosted 30B on KU HPC

# Fireworks.ai (OpenAI-compatible) — $100 credit; System-1 bulk triage
# backend when glimmer isn't up. Key via FIREWORKS_API_KEY.
FW_BASE_URL = "https://api.fireworks.ai/inference/v1"
MODEL_FW_JUDGE = "accounts/fireworks/models/muse-spark-open"
FW_PRICES = {MODEL_FW_JUDGE: (0.90, 0.90, 0.90)}   # ~per 1M tok

# Non-Muse providers live outside the product stack — internal dev
# tooling only, excluded from the public snapshot (Muse-only rule).
LOCAL_BASE_URL = "http://127.0.0.1:20007/v1"      # local tunnel
LOCAL_MI210_BASE_URL = "http://127.0.0.1:20009/v1"  # local tunnel
LOCAL_MI210_B_URL = "http://127.0.0.1:20010/v1"     # failover tunnel
LOCAL_BASE_URLS = (LOCAL_BASE_URL, LOCAL_MI210_BASE_URL, LOCAL_MI210_B_URL)


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0

    @classmethod
    def from_response(cls, u: dict) -> Usage:
        d = (u or {}).get("completion_tokens_details") or {}
        p = (u or {}).get("prompt_tokens_details") or {}
        return cls(
            prompt_tokens=(u or {}).get("prompt_tokens", 0),
            completion_tokens=(u or {}).get("completion_tokens", 0),
            cached_tokens=p.get("cached_tokens", 0),
            reasoning_tokens=d.get("reasoning_tokens", 0),
        )

    def cost_usd(self, model: str) -> float:
        cached_in, pin, pout = PRICES.get(
            model, FW_PRICES.get(model, (0.0, 0.0, 0.0)))
        billable = self.prompt_tokens - self.cached_tokens
        return (
            billable * pin + self.cached_tokens * cached_in + self.completion_tokens * pout
        ) / 1e6


@dataclass
class Spend:
    total_usd: float = 0.0
    calls: int = 0
    by_model: dict = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, model: str, cost: float) -> None:
        with self._lock:
            self.total_usd += cost
            self.calls += 1
            self.by_model[model] = self.by_model.get(model, 0.0) + cost

    def report(self) -> str:
        lines = [f"${self.total_usd:.4f} across {self.calls} calls"]
        lines += [f"  {m}: ${c:.4f}" for m, c in sorted(self.by_model.items())]
        return "\n".join(lines)


class MetaClient:
    def __init__(self, api_key: str | None = None, base_url: str = BASE_URL):
        self.key = api_key or os.environ["MODEL_API_KEY"]
        self.base = base_url.rstrip("/")
        self.spend = Spend()
        self._fallback_bases: tuple[str, ...] = ()
        self._http = requests.Session()
        self._http.headers["Authorization"] = f"Bearer {self.key}"

    @classmethod
    def local(cls) -> MetaClient:
        """Self-hosted Muse Glimmer (free) — requires the local tunnel
        on LOCAL_BASE_URL."""
        return cls(api_key="ollama", base_url=LOCAL_BASE_URL)

    @classmethod
    def local_mi210(cls) -> MetaClient:
        """Self-hosted Muse Glimmer, GPU instance with automatic
        failover to a second tunnel on connection error."""
        c = cls(api_key="llama", base_url=LOCAL_MI210_BASE_URL)
        c._fallback_bases = (LOCAL_MI210_BASE_URL, LOCAL_MI210_B_URL)
        return c

    @classmethod
    def fireworks(cls) -> MetaClient:
        """Fireworks.ai hosted inference (OpenAI-compatible) — bulk
        System-1 triage. Key from FIREWORKS_API_KEY."""
        try:
            key = os.environ["FIREWORKS_API_KEY"]
        except KeyError:
            raise SystemExit("FIREWORKS_API_KEY is not set "
                             "(export it or use --backend local)")
        return cls(api_key=key, base_url=FW_BASE_URL)

    def list_models(self) -> list[str]:
        r = self._http.get(f"{self.base}/models", timeout=30)
        r.raise_for_status()
        return [m["id"] for m in r.json()["data"]]

    def chat(
        self,
        messages: list[dict],
        model: str = MODEL_AGENT,
        reasoning_effort: str = "low",
        max_tokens: int = 4096,
        temperature: float | None = None,
        retries: int = 3,
        **extra,
    ) -> dict:
        """Returns {content, usage, cost_usd, model, finish_reason}."""
        body = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            **extra,
        }
        # Ollama's OpenAI shim rejects reasoning_effort ("does not support
        # thinking") — only send it to the hosted Meta API.
        if not self.base.startswith(LOCAL_BASE_URLS):
            body["reasoning_effort"] = reasoning_effort
        if temperature is not None:
            body["temperature"] = temperature

        bases = self._fallback_bases or (self.base,)
        for attempt in range(retries):
            try:
                r = self._http.post(
                    f"{bases[attempt % len(bases)]}/chat/completions",
                    json=body, timeout=180,
                )
            except requests.ConnectionError:
                if attempt < retries - 1:
                    continue
                raise
            if r.status_code == 429 and attempt < retries - 1:
                wait = float(r.headers.get("Retry-After", 2 ** attempt))
                time.sleep(wait + 0.25)
                continue
            r.raise_for_status()
            break

        data = r.json()
        choice = data["choices"][0]
        usage = Usage.from_response(data.get("usage"))
        cost = usage.cost_usd(model)
        self.spend.add(model, cost)
        return {
            "content": choice["message"].get("content"),
            # Ollama uses `reasoning`, llama.cpp `reasoning_content`;
            # hosted Meta folds CoT into usage stats only.
            "reasoning": choice["message"].get("reasoning")
            or choice["message"].get("reasoning_content"),
            "finish_reason": choice.get("finish_reason"),
            "model": model,
            "usage": usage,
            "cost_usd": cost,
        }

    def count_tokens(self, messages: list[dict], model: str = MODEL_AGENT) -> int:
        r = self._http.post(
            f"{self.base}/chat/completions/token_count",
            json={"model": model, "messages": messages},
            timeout=30,
        )
        if r.status_code == 404:
            return -1  # endpoint name TBD; cost estimate falls back
        r.raise_for_status()
        return r.json().get("total_tokens", -1)
