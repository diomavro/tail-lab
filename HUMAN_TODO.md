# Human backlog

This is Dio's queue — account setup, paid services, and anything else only
a human can action. It is separate from `AGENT_TODO.md` (`docs/adr/0002`).
**The agent appends items here when it hits a real-world blocker; it never
acts on, removes, or reorders anything in this file.**

## Setup needed for v1

- [x] Create the GitHub repo `diomavro/tail-lab`, push `main`, enable
      Actions-can-create-PRs, add `CLAUDE_CODE_OAUTH_TOKEN`.
      **Done 2026-08-17** (private repo, PR-creation enabled, secret reuses
      the subscription token from the quizkit setup). Remaining before the
      daily agent runs: commit its GitHub Actions workflow (mirrors quizkit's
      hardened pattern; uses `docs/AGENT_MISSION.md` as the prompt).
- [x] ~~Create a lakeFS Cloud account + repo for the tail-lab lakehouse~~.
      **No longer needed — 2026-08-18**: the decision changed (`docs/adr/0012`
      supersedes `docs/adr/0006`'s lakeFS plan). lakeFS Cloud isn't free at
      this scale and self-hosting lakeFS OSS is a service to run for no
      strong reason here; the lakehouse is plain immutable Parquet on Tigris
      instead, with point-in-time/immutability enforced in application code.
- [x] Create the object-storage bucket (Fly Tigris) that the lakehouse lives
      on, and generate its access credentials.
      **Done 2026-08-18**: bucket `tail-lab-lake` provisioned on Fly Tigris
      (endpoint `https://fly.storage.tigris.dev`, region `auto`). Credentials
      are staged as Fly secrets on the `tail-lab` app for prod and in a
      gitignored `.env` at the repo root for local dev
      (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_ENDPOINT_URL_S3`,
      `AWS_REGION`, `TAIL_LAB_S3_BUCKET`, `TAIL_LAB_LAKE_BACKEND=tigris`).
- [x] Deploy the Fly.io app for the API/dashboard. **Done 2026-08-18** —
      live at https://tail-lab.fly.dev on the Tigris backend (SPA + `/api/*`,
      health-checked). The lake is seeded with VIX; the dashboard renders a
      real z-score. Redeploy with `flyctl deploy -a tail-lab --remote-only`.
      (The daily *agent* still never deploys — deploys stay human/operator.)
- [x] Get a free FRED API key — unblocks the rates (`docs/DATA_CONTRACTS.md`
      #3) and credit (#4) ingestion adapters. **Done — already existed** in
      the `fred-data` MCP config (`~/.claude.json`); reused it and set it as
      the `FRED_API_KEY` repo secret (2026-08-17). For local dev, export
      `FRED_API_KEY` from that same value.

## Later / optional

- [ ] Budget for a historical option-data source (ORATS, CBOE, or
      OptionMetrics) to upgrade the backtest from model-priced to
      real-quote pricing (`docs/adr/0004` — the second `OptionPricer`
      implementation is agent-buildable once this exists; the data
      subscription itself is not).
