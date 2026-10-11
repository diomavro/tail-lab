import type { TabId } from './PutLab'

// Screen and Backtest are gone as separate tabs -- picking a name and reading
// its result were on two tabs bridged by a ranking pinned above both, so the
// ranking had to stay on screen permanently to make the split workable. Both
// now live in Workspace, with the ranking as a one-line strip at its top.
// Recommendations went the same way (docs/adr/0028 amendment): it re-sorted
// the strip's own read, so it is now the strip's best-return order.
export const TABS: { id: TabId; label: string }[] = [
  { id: 'workspace', label: 'Workspace' },
  { id: 'portfolio', label: 'Portfolio' },
  // After Portfolio: it asks the portfolio question about the hedge itself --
  // how much of the book should carry it, judged on the growth of the whole.
  { id: 'book', label: 'Book' },
  { id: 'bakeoff', label: 'Bake-off' },
  { id: 'regime', label: 'Regime' },
  { id: 'surface', label: 'Surface' },
  { id: 'glossary', label: 'Glossary' },
]

export const TAB_LABEL = Object.fromEntries(TABS.map((t) => [t.id, t.label])) as Record<TabId, string>
