# Meta Model API — facts for this project

Base URL `https://api.meta.ai/v1` — OpenAI-compatible (responses, chat
completions) + Anthropic-compatible (messages). Key: `MODEL_API_KEY`,
sourced from `~/env/meta-key` (never commit).

## Models available on our key

`muse-wonder-1.0` · `muse-spark-1.3` · `muse-spark-1.2` · `muse-spark-1.1`
· `muse-spark-1.3-contributor` · `muse-spark-1.2-contributor` ·
`sam-3.1` · `muse-image-1.0` · `muse-voice-transcribe-1.0`

## Pricing (per 1M tokens unless noted)

| Model | Cached in | Input | Output |
|---|---|---|---|
| muse-wonder-1.0 | $0.20 | $4.00 | $20.00 |
| muse-spark-* (standard) | $0.15 | $1.25 | $4.25 |
| muse-spark-*-contributor | $0.002 | $0.10 | $0.20 |

- Muse Image: **$0.01/image** (web+image search included)
- SAM 3.1: **$2.50 / 1K images**, $0.20 / 1K video frames (min 50 frames)
- Voice transcribe: $0.18/hour audio
- Web search grounding: **$2.50 / 1K queries**
- No long-context premium; Meta's injected steering tokens aren't billed.

**Contributor mode caveat**: Meta may train on your content. Fine for
public literature/PDF extraction; do NOT use for private data.

## Rate limits (per team)

- Standard spark/wonder: 3,000 RPM / 4M TPM
- Contributor spark: **150 RPM** / 3M TPM ← throttle bulk jobs
- SAM: 3,000 RPM (no TPM); Image: 150 RPM
- Background responses: 600 submissions/min cap

## Reasoning models

Every spark/wonder call spends `reasoning_tokens` before emitting text —
`finish_reason: "length"` + `content: null` means it ran out of budget.
Always set `max_tokens` generously and `reasoning_effort` deliberately.

## Budget strategy for the $150

- Bulk literature extraction → `muse-spark-1.2-contributor` (public data,
  ~20x cheaper; 5K papers ≈ $2)
- Agent brain / demo-quality calls → `muse-spark-1.3` standard
- The one showcase deliberation → `muse-wonder-1.0`
- SAM + Muse Image → effectively free at our scale
- Prompt caching ON for the extraction schema/instructions prefix

## Capabilities worth using (rubric: "beyond basic integration")

- **Structured output** (JSON schema) — host/pathogen extraction contract
- **Search grounding** — agent cites real sources live
- **File handling** — upload paper PDFs directly into extraction calls
- **Tool calling** — agent orchestrates ecoagent/phylopred/geoseek tools
- **Token counting** endpoint — pre-flight cost estimation
- **Background responses** — async for bulk jobs
