import type { KeyboardEvent } from 'react'
import type { TabId } from './PutLab'

// Screen and Backtest are gone as separate tabs -- picking a name and reading
// its result were on two tabs bridged by a ranking pinned above both, so the
// ranking had to stay on screen permanently to make the split workable. Both
// now live in Workspace, with the ranking as a one-line strip at its top.
const TABS: { id: TabId; label: string }[] = [
  { id: 'workspace', label: 'Workspace' },
  // Directly after Workspace: it answers the question a reader has once they
  // have seen one name's result -- "so which of these is actually best?"
  { id: 'recommendations', label: 'Recommendations' },
  { id: 'portfolio', label: 'Portfolio' },
  { id: 'bakeoff', label: 'Bake-off' },
  { id: 'regime', label: 'Regime' },
  { id: 'glossary', label: 'Glossary' },
]

// The workspace tab bar. An ARIA tablist with roving focus: Left/Right arrows
// move between tabs (wrapping), and the newly selected tab takes focus.
export function TabNav({ activeTab, onChange }: { activeTab: TabId; onChange: (t: TabId) => void }) {
  const onKeyDown = (e: KeyboardEvent<HTMLButtonElement>) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
    e.preventDefault()
    const idx = TABS.findIndex((t) => t.id === activeTab)
    const nextIdx = e.key === 'ArrowRight' ? (idx + 1) % TABS.length : (idx - 1 + TABS.length) % TABS.length
    const nextId = TABS[nextIdx]!.id
    onChange(nextId)
    document.getElementById(`tab-${nextId}`)?.focus()
  }

  return (
    <nav className="pl-tabs" role="tablist" aria-label="Put Lab views">
      {TABS.map((t) => {
        const selected = activeTab === t.id
        return (
          <button
            key={t.id}
            type="button"
            role="tab"
            id={`tab-${t.id}`}
            aria-selected={selected}
            aria-controls={`panel-${t.id}`}
            tabIndex={selected ? 0 : -1}
            className="pl-tab"
            onClick={() => onChange(t.id)}
            onKeyDown={onKeyDown}
          >
            {t.label}
          </button>
        )
      })}
    </nav>
  )
}
