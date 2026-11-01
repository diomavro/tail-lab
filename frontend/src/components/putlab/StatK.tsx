import type { ReactNode } from 'react'
import type { ConceptId } from '../../content/concepts'
import { ConceptInfo } from './ConceptInfo'

/* The label of a figure: `.pl-stat-k` over a stat tile, or `.pl-readout-k`
 * inside a readout line. ConceptInfo sits INSIDE the labelled element, and
 * `concept` is required, so a label cannot ship unexplained. The class names
 * appear only here (tests/test_frontend_explained_headers.py). */
export function StatK({
  concept,
  variant = 'stat',
  children,
}: {
  concept: ConceptId
  variant?: 'stat' | 'readout'
  children: ReactNode
}) {
  return variant === 'stat' ? (
    <div className="pl-stat-k">
      {children}
      <ConceptInfo id={concept} />
    </div>
  ) : (
    <span className="pl-readout-k">
      {children}
      <ConceptInfo id={concept} />
    </span>
  )
}
