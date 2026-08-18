// "Coming next -- the memory layer" teaser. Static illustrative content: the
// memory layer (storing verdicts keyed by (rule, regime)) isn't built yet, so
// nothing here is wired to the live backtest -- port the mock's copy as-is.
export function MemoryTeaser() {
  return (
    <section className="panel mem" style={{ marginTop: 22 }}>
      <div className="panel-head">
        <div>
          <span className="eyebrow">Coming next &middot; the memory layer</span>
          <h2 style={{ marginTop: 6 }}>Before you re-run this, the lab already remembers</h2>
          <div className="hint">
            Every backtest becomes a stored verdict keyed by <span className="mono">(rule, regime)</span>. Repeats
            are skipped; a strategy that only worked in one crash is flagged <em>regime-only</em>, never
            &ldquo;confirmed&rdquo;.
          </div>
        </div>
      </div>
      <div className="mem-grid">
        <div className="mem-card">
          <div className="eyebrow" style={{ marginBottom: 8 }}>
            illustrative &middot; your test would become a node
          </div>
          <div className="rule">buy 5% OOM put &middot; 1mo &middot; SPY</div>
          <div className="meta">
            <span className="badge regime_only">regime only</span> <span>&middot;</span>{' '}
            <span>&rsquo;22 + &rsquo;25 crashes</span>
          </div>
          <div className="meta" style={{ color: 'var(--faint)' }}>
            paid off only inside stress windows &mdash; not a standalone edge
          </div>
        </div>
        <div className="mem-card">
          <div className="eyebrow" style={{ marginBottom: 8 }}>
            illustrative &middot; prior art the lab would surface
          </div>
          <div className="rule">buy 10% OOM put &middot; 1mo &middot; SPX</div>
          <div className="meta">
            <span className="badge regime_only">regime only</span> <span>&middot;</span> <span>run 2&times;</span>{' '}
            <span>&middot;</span> <span>CRASH &rsquo;22</span>
          </div>
          <div className="meta" style={{ color: 'var(--faint)' }}>
            tested 2&times; &mdash; both in one crash. Not independent confirmation.
          </div>
        </div>
      </div>
    </section>
  )
}
