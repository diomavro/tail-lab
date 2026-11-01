import type { ReactNode, ThHTMLAttributes } from 'react'
import type { ConceptId } from '../../content/concepts'
import { ConceptInfo } from './ConceptInfo'

/* A table header that explains itself: the label plus a ConceptInfo (a short
 * tooltip on hover or focus, the full concept on click).
 *
 * `title` is not accepted: the browser's native tooltip would sit on top of
 * ConceptInfo's, so a header's one-line hint is the concept's `short` now.
 * A header that needs no explanation (a row index, a name, a row label) is
 * written as a raw `<th data-noexplain="index|name|row-label">` instead;
 * tests/test_frontend_explained_headers.py allows nothing else.
 */
export function Th({
  concept,
  children,
  ...rest
}: { concept: ConceptId; children: ReactNode } & Omit<ThHTMLAttributes<HTMLTableCellElement>, 'title'>) {
  return (
    <th {...rest}>
      {children}
      <ConceptInfo id={concept} />
    </th>
  )
}
