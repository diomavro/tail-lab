import type { KeyboardEvent } from 'react'
import type { TabId } from './PutLab'

const TABS: { id: TabId; label: string }[] = [
  { id: 'screen', label: 'Screen' },
  { id: 'backtest', label: 'Backtest' },
  { id: 'portfolio', label: 'Portfolio' },
  { id: 'regime', label: 'Regime' },
  { id: 'learn', label: 'Learn' },
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
    <div className="tabnav" role="tablist" aria-label="Put Lab views">
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
            className={`tab${selected ? ' tab-active' : ''}`}
            onClick={() => onChange(t.id)}
            onKeyDown={onKeyDown}
          >
            {t.label}
          </button>
        )
      })}
    </div>
  )
}
