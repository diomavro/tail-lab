/* The README's rule: every hedge result names the accounting that produced
 * it (docs/adr/0027 §2). Every put surface outside the Book prices a fixed
 * premium budget with no book behind it -- the standalone accounting. */
export function StandaloneNote() {
  return (
    <p className="pl-note" data-testid="accounting">
      Accounting: standalone — a fixed premium budget per roll with no book behind it, so these are puts on their own,
      not hedge results (docs/adr/0027). The Book tab judges hedges.
    </p>
  )
}
