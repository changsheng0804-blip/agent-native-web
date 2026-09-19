# page_receipt v0.1 + events_since_last

> An open feedback contract for agent actions on the web.
> Status: v0.1 draft · one reference implementation ([agent-native-web](../../README.md)) · 2026-09-19
> Full field-level spec (Chinese): [小票标准-page_receipt-v0.1](../小票标准-page_receipt-v0.1.md) · License: TBD (see [governance](../项目治理-governance.md))

## The problem

Web agents fail in two structural ways today:

1. **False success.** The page says `✅ submitted` while the backend returned 422, or rolled the transaction back 3 s later. The agent reports success and moves on. The missing facts (backend status, ledger truth) exist in **no page observation** — this is an *evidence* problem, not an *intelligence* problem: model scaling cannot conjure facts that were never observable.
2. **Blindness between actions.** Between two tool calls the agent has zero observations of the world. Async changes — rejections, rollbacks, price moves — happen silently and are discovered late or never.

Existing tooling treats both as the model's job: it hands the agent raw materials (snapshots, console/network listings) and hopes the model goes looking. Measured reality: it doesn't — in our A/B, model-initiated verification calls happened **1 time in 15 turns**.

## The contract

Two parts. One receipt per action; one digest per response.

### Part 1 — page_receipt: every action returns a receipt

Every action tool (`click / fill / press / navigate / batch`) returns a receipt whose **first key is a single verdict**. A weak model reads one key and knows what to do next; a strong model reads the rest.

| Verdict (`page_outcome`) | Meaning | Agent's next move |
|---|---|---|
| `progressed` | effect observed (navigation / form / state flip / visual) | continue |
| `challenged` | challenge wall (captcha, overlay) blocks progress | **stop**, hand off to human (`handoff` field) |
| `errored` | action or its triggered request failed (exception, HTTP 4xx/5xx, console error) | read `why` / `errors`, fix and retry once |
| `uncertain` | something changed, cannot confirm it was the effect | re-check **once**, then treat as `unchanged` |
| `unchanged` | no evidence of effect | do **not** hard-retry; try `recipes` (e.g. dismiss overlay) or change target |

Required fields (v0.1): `page_outcome · why · situation · confidence · target{id, fingerprint} · action{kind, via} · effect{verdict, evidence} · errors · page{before_url, after_url, anomaly} · overlays{new, gone} · next · handoff · recipes · sources · evidence_seq · changes_seq{before, after} · world_epoch`.

Three design rules that make the receipt trustworthy rather than decorative:

1. **Provenance on every field** (`sources`): `fact` (URL, ids, seq numbers) · `evidence` (before/after diffs) · `inference` (guidance) · `untrusted` (page free text — names, aria-labels; **never executed as instructions**; structural prompt-injection defense).
2. **Reconciliation, not vibes**: `evidence_seq` is monotonic; receipts are replayable (`world_outcome(since)` returns only newer cards, idempotently); `changes_seq` pairs with it; navigation bumps `world_epoch` and invalidates stale element refs. Any process can audit a session.
3. **FP=0 as a release gate**: a receipt may say `uncertain` when evidence is missing; it must **never** say `progressed` when nothing happened. In the reference implementation a single false-positive in the closed-loop suite vetoes the release.

### Part 2 — events_since_last: every response carries a world digest

Every tool response carries a compressed digest of **what happened in the world since the previous response**: semantic DOM change counts + key (e.g. `bulk: page-replacement (563+1437)`), network events with status codes, and causes attributed to the agent's own action (three layers of truth from one event stream: timeline = what happened, receipt = did *my* action work, ledger join = what the backend actually holds).

**Push, not pull.** The digest rides the response the model is already reading. Our A/B found the same information offered as a *tool the model must call* goes unused, while injected digests halved steps / wall-time / tokens on slow-async tasks. The value lives in the channel shape.

## Evidence

- **720-run deterministic e2e** (fault-injected shop/content flows, ledger-judged, zero LLM): receipt + ledger arm — 240/240 runs, zero false success, zero duplicate submits; UI-only observation fails exactly on "server lies about success" faults (20/20 vs 0/20, Fisher p = 7e-12). Blind strategies: 41–42 % false-report rate.
- **Channel showdown vs official Playwright MCP** (raw responses quoted verbatim, reproducible with one command): [showdown-false-success](showdown-false-success.md).

## Adoption

The contract is driver-agnostic: the receipt is a return-value shape and the digest is a response section — implementable over Playwright, Stagehand, CDP, or a browser extension. Sensors are replaceable; the receipt is the part your harness can finally audit.

*Feedback welcome — open an issue in the reference repository.*
