import type { RegimeVerdictResponse, Verdict } from '../../api/client'

// The Put Lab's live regime verdict (docs/adr/0015): split the current
// backtest by the regime each roll was entered in, and show whether the
// strategy paid off across regimes (confirmed), in only one (regime_only), or
// none (failed). This is the visible half of the memory layer -- computed live
// from the backtest you're looking at, no stored history required. The
// persistent memory (run_count, cross-session prior art) is recorded by the
// daily agent and layers on top.

const VERDICT_COPY: Record<Verdict, { label: string; badge: string; note: string }> = {
  confirmed: {
    label: 'confirmed',
    badge: 'confirmed',
    note: 'paid off across more than one market regime — not a one-crash fluke.',
  },
  regime_only: {
    label: 'regime only',
    badge: 'regime_only',
    note: 'paid off in only one regime — not a standalone edge, just a bet on that regime recurring.',
  },
  failed: {
    label: 'failed',
    badge: 'failed',
    note: 'never recovered its premium in any regime over this window.',
  },
  untested: {
    label: 'untested',
    badge: 'regime_only',
    note: 'no regime could be labeled for this window yet.',
  },
}

const fmtPct = (n: number) => `${n >= 0 ? '+' : '−'}${Math.abs(n * 100).toFixed(0)}%`

export function MemoryTeaser({ verdict }: { verdict: RegimeVerdictResponse | null }) {
  const copy = verdict ? VERDICT_COPY[verdict.verdict] : null

  return (
    <section className="panel mem" style={{ marginBottom: 0 }}>
      <div className="panel-head">
        <div>
          <span className="eyebrow">The memory layer &middot; live verdict</span>
          <h2 style={{ marginTop: 4 }}>Did this only work in one regime?</h2>
          <div className="hint">
            Split by the regime each roll was <em>entered</em> in &mdash; paid off in one regime only is{' '}
            <em>regime-only</em>, never &ldquo;confirmed&rdquo;.
          </div>
        </div>
        {verdict && copy && (
          <span className={`badge ${copy.badge}`} title={verdict.rule_hash}>
            {copy.label}
          </span>
        )}
      </div>

      {verdict && copy ? (
        <>
          <div className="hint" style={{ marginBottom: 8 }}>{copy.note}</div>
          <div className="mem-grid">
            {verdict.slices.length === 0 && (
              <div className="mem-card">
                <div className="meta" style={{ color: 'var(--faint)' }}>
                  No regime could be labeled for this window (needs VIX history spanning the backtest).
                </div>
              </div>
            )}
            {verdict.slices.map((s) => (
              <div className="mem-card" key={s.regime}>
                <div className="eyebrow" style={{ marginBottom: 8 }}>
                  {s.regime} &middot; {s.n_cycles} roll{s.n_cycles === 1 ? '' : 's'}
                </div>
                <div className="rule" style={{ color: s.paid_off ? 'var(--gain)' : 'var(--loss)' }}>
                  {fmtPct(s.roi_on_premium)} on premium
                </div>
                <div className="meta" style={{ color: 'var(--faint)' }}>
                  {s.paid_off ? 'net-paid in this regime' : 'bled in this regime'}
                </div>
              </div>
            ))}
          </div>
        </>
      ) : (
        <div className="hint">Run a backtest above to see its regime verdict.</div>
      )}
    </section>
  )
}
