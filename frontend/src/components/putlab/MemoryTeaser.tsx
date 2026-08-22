import type { RegimeVerdictResponse, Verdict } from '../../api/client'
import type { ResourceState } from './PutLab'

// The Put Lab's live regime verdict (docs/adr/0015): split the current backtest
// by the regime each roll was ENTERED in, and show whether the strategy paid off
// across regimes (confirmed), in only one (regime_only), or none (failed). This
// is the visible half of the memory layer -- computed live from the backtest on
// screen, no stored history required. The persistent memory (run_count,
// cross-session prior art) is recorded by the daily agent and layers on top.
//
// The verdict counts ENTRY REGIMES WITH POSITIVE ROI -- >=2 confirmed, 1
// regime-only, 0 failed, none labelled untested. It is not a return threshold,
// and copy that implies one is wrong.

const VERDICT_COPY: Record<Verdict, { label: string; tag: string; note: string }> = {
  confirmed: {
    label: 'confirmed',
    tag: 'pl-tag pl-tag-ok',
    note: 'paid off in more than one entry regime — not a one-crash fluke.',
  },
  regime_only: {
    label: 'regime only',
    tag: 'pl-tag pl-tag-mute',
    note: 'paid off in only one regime — not a standalone edge, just a bet on that regime recurring.',
  },
  failed: {
    label: 'failed',
    tag: 'pl-tag pl-tag-bad',
    note: 'never recovered its premium in any regime over this window.',
  },
  untested: {
    label: 'untested',
    tag: 'pl-tag pl-tag-outline',
    note: 'no regime could be labelled for this window yet.',
  },
}

const pct = (n: number) => `${n >= 0 ? '+' : '−'}${Math.abs(n * 100).toFixed(0)}%`

export function MemoryTeaser({ verdict }: { verdict: ResourceState<RegimeVerdictResponse> }) {
  const data = verdict.status === 'ready' ? verdict.data : null
  const copy = data ? VERDICT_COPY[data.verdict] : null

  return (
    <section aria-label="Regime verdict">
      <div className="pl-section-head">
        <h3>Did this only work in one regime?</h3>
        {data && copy && <span className={copy.tag} title={data.rule_hash}>{copy.label}</span>}
      </div>

      {data && copy ? (
        <>
          <p className="pl-note" style={{ marginBottom: 14 }}>
            Split by the regime each roll was <em>entered</em> in — {copy.note}
          </p>
          <div className="pl-mem">
            {data.slices.length === 0 && (
              <p className="pl-status">
                No regime could be labelled for this window (it needs VIX history spanning the
                backtest).
              </p>
            )}
            {data.slices.map((s) => (
              <div className="pl-mem-card" key={s.regime}>
                <div className="pl-kicker">
                  {s.regime} &middot; {s.n_cycles} roll{s.n_cycles === 1 ? '' : 's'}
                </div>
                <div className={`pl-mem-v ${s.paid_off ? 'pl-pos' : 'pl-neg'}`}>
                  {pct(s.roi_on_premium)}
                </div>
                <div className="pl-micro">{s.paid_off ? 'net-paid here' : 'bled here'}</div>
              </div>
            ))}
          </div>
        </>
      ) : (
        <p className="pl-status" role="status" aria-live="polite">
          {verdict.status === 'error'
            ? 'The regime verdict is unavailable for this window.'
            : 'Splitting this backtest by entry regime…'}
        </p>
      )}
    </section>
  )
}
