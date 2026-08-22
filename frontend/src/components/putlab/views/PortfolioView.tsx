import type { UniverseMember } from '../../../api/client'
import { Portfolio } from '../Portfolio'
import type { PutLabControls } from '../types'

// "What does a basket look like?" -- the fragile-basket preset + custom-mix
// builder. The rail's lookback seeds the basket's window so the two views open
// on the same question.
export function PortfolioView({
  universe,
  controls,
}: {
  universe: UniverseMember[]
  controls: PutLabControls
}) {
  return <Portfolio universe={universe} controls={controls} />
}
