import type { UniverseMember } from '../../../api/client'
import { Portfolio } from '../Portfolio'

// "Mix a basket of puts" -- the fragile-basket preset + custom-mix builder.
export function PortfolioView({ universe }: { universe: UniverseMember[] }) {
  return (
    <>
      <p className="view-intro">
        <strong>What does a basket look like?</strong> Blend several OOM-put legs into one hedge &mdash; load the
        live fragile basket or build your own mix &mdash; and see the combined P&amp;L and how staggered legs
        diversify the bleed.
      </p>
      <Portfolio universe={universe} />
    </>
  )
}
