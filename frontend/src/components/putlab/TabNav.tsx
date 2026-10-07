import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from 'react'
import type { TabId } from './PutLab'
import { TABS } from './tabs'

/** Where the active tab sits from the row's left edge once scrolled to. */
const NARROW_INSET_PX = 16

// The workspace tab bar. An ARIA tablist with roving focus: Left/Right arrows
// move between tabs (wrapping), and the newly selected tab takes focus.
//
// On a narrow page the row does not wrap: it scrolls sideways, and the active
// tab is scrolled into view by setting the row's own scrollLeft. Not
// scrollIntoView -- that scrolls every ancestor too, and would jump the page
// vertically on a tab change. A fade on the right edge says more tabs follow.
export function TabNav({
  activeTab,
  onChange,
  narrow,
}: {
  activeTab: TabId
  onChange: (t: TabId) => void
  narrow: boolean
}) {
  const navRef = useRef<HTMLElement>(null)
  const [atEnd, setAtEnd] = useState(true)

  const checkEnd = useCallback(() => {
    const nav = navRef.current
    if (!nav) return
    setAtEnd(nav.scrollLeft + nav.clientWidth >= nav.scrollWidth - 4)
  }, [])

  useEffect(() => {
    const nav = navRef.current
    if (!nav) return
    if (narrow) {
      const btn = document.getElementById(`tab-${activeTab}`)
      if (btn) {
        const delta = btn.getBoundingClientRect().left - nav.getBoundingClientRect().left
        nav.scrollLeft = Math.max(0, nav.scrollLeft + delta - NARROW_INSET_PX)
      }
    }
    checkEnd()
  }, [activeTab, narrow, checkEnd])

  // A width change without a tab change (rotating a phone) moves the end too.
  useEffect(() => {
    const nav = navRef.current
    if (!nav) return
    const ro = new ResizeObserver(checkEnd)
    ro.observe(nav)
    return () => ro.disconnect()
  }, [checkEnd])

  const onKeyDown = (e: KeyboardEvent<HTMLButtonElement>) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
    e.preventDefault()
    const idx = TABS.findIndex((t) => t.id === activeTab)
    const nextIdx = e.key === 'ArrowRight' ? (idx + 1) % TABS.length : (idx - 1 + TABS.length) % TABS.length
    const nextId = TABS[nextIdx]!.id
    onChange(nextId)
    // preventScroll: the row's own scroll effect places the tab; a default
    // focus scroll would fight it.
    document.getElementById(`tab-${nextId}`)?.focus({ preventScroll: true })
  }

  return (
    <div className="pl-tabs-wrap">
      <nav className="pl-tabs" role="tablist" aria-label="Put Lab views" ref={navRef} onScroll={checkEnd}>
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
      {narrow && !atEnd && <div className="pl-tabs-fade" aria-hidden="true" />}
    </div>
  )
}
