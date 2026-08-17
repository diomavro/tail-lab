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
- [ ] Create a lakeFS Cloud account + repo for the tail-lab lakehouse
      (`docs/adr/0006`).
- [ ] Create the object-storage bucket (Fly Tigris or Cloudflare R2) that
      lakeFS will version, and generate its access credentials.
- [ ] Create the Fly.io app(s) for the API/dashboard and set the required
      secrets (lakeFS credentials, object-storage credentials, FRED API
      key once obtained).
- [ ] Get a free FRED API key (fred.stlouisfed.org) — unblocks the rates
      (`docs/DATA_CONTRACTS.md` #3) and credit (#4) ingestion adapters.

## Later / optional

- [ ] Budget for a historical option-data source (ORATS, CBOE, or
      OptionMetrics) to upgrade the backtest from model-priced to
      real-quote pricing (`docs/adr/0004` — the second `OptionPricer`
      implementation is agent-buildable once this exists; the data
      subscription itself is not).
