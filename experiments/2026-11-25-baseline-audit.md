# News AI Personal — Baseline Audit 2026-11-25

## Scope
Read-only audit of `upgrade/news-pipeline-v1`. No production settings, secrets, schedules, or push behavior were changed.

## Repository and safety state
- Repository: `xuechenrenn-ops/Personal-News-Intelligence`
- Development branch: `upgrade/news-pipeline-v1`
- Default branch: `main` (read-only)
- Current pipeline workflow supports `workflow_dispatch` only; no GitHub Actions schedule.
- `push_enabled=false` remains the safe preview mode.
- Workflow permissions are `contents: read`.
- Baichuan endpoint constant exists in code, but Baichuan is not called by the current daily pipeline.

## Current model call topology
On a normal run:
1. DeepSeek medical candidate screening.
2. DeepSeek medical evidence review, only when first-stage medical results exist.
3. DeepSeek general-news selection.
4. DeepSeek NSFC selection, only when eligible NSFC candidates exist.

Therefore, the current full run can make up to four model requests, with two serial calls in the medical lane. NSFC and medical second-stage calls are conditional.

## Main measurement gap
The current `call_model()` implementation reads response content but does not persist or print provider usage metadata. It therefore does not provide a reliable per-run baseline for:
- prompt/input tokens;
- completion/output tokens;
- cache-hit/cache-miss tokens (where exposed);
- per-call and total model cost;
- model latency and failure/retry counts.

Without these measurements, a DeepSeek-vs-Baichuan comparison would be speculative. First optimization priority should be low-overhead usage instrumentation and a fixed-sample replay harness, not immediate production routing changes.

## Pricing snapshot (official provider pages checked 2026-11-25)
- DeepSeek official rate card for `deepseek-flash`: off-peak input ¥1 / million tokens, output ¥4 / million tokens; peak input ¥2 / million, output ¥8 / million. Cache-hit input is listed separately at ¥0.02 / million off-peak and ¥0.04 / million peak. Actual charge depends on returned usage and cache accounting.
- Baichuan official pricing page for Baichuan-M3-Plus: input ¥0.005 / 1K tokens, output ¥0.009 / 1K tokens; the page also lists an additional ¥0.03 per medical-search invocation for M3-Plus. Whether/how that search can be disabled must be confirmed in the exact API contract before live comparison.
- On listed token rates alone, Baichuan-M3-Plus is materially more expensive than DeepSeek-Flash for routine full-volume screening. It should not be put into the default daily path without measured quality gains. A small, targeted second-opinion experiment may still be worthwhile after instrumentation.

Sources:
- DeepSeek official pricing: https://api-docs.deepseek.com/zh-cn/quick_start/pricing/
- Baichuan official pricing: https://platform.baichuan-ai.com/prices

## First experiment decision
**Experiment 0 — establish a reproducible cost baseline.**
1. Add usage/latency accounting around each model response, recording only non-sensitive numeric metadata.
2. Preserve current DeepSeek-only production behavior.
3. Build a fixed, anonymized/synthetic or previously cached candidate fixture for offline comparison.
4. Compare: DeepSeek single-pass, current DeepSeek multi-stage, and only then DeepSeek + a limited Baichuan second opinion.
5. Never log prompts, candidate bodies, API keys, or full response bodies in cost telemetry.
6. Keep `push_enabled=false`; do not enable GitHub Actions schedule or merge to `main`.

## Current limitations / unknowns
- Historical runs do not provide model token usage, so past costs cannot be reconstructed reliably from logs.
- Actual API balance, remaining credits, and account-specific discounts are not accessible through the repository metadata.
- No live Baichuan request was made; its actual response usage and medical-search charge are not experimentally verified.
- No claim is made that either model has superior news-selection quality; fixed-input evaluation is still required.
