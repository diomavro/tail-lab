import type { RegimeLabel } from '../../api/client'

/* One palette for the regime bands, so "crisis" is the same colour on every tab
 * that draws it (Regime, Marks). */

export const REGIME_ORDER: RegimeLabel[] = ['calm', 'elevated', 'crisis']
export const REGIME_FILL: Record<RegimeLabel, string> = {
  calm: 'var(--ink-25)',
  elevated: 'var(--warn-fill)',
  crisis: 'var(--mag)',
}
