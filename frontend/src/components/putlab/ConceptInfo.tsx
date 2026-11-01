import type { MouseEvent, PointerEvent } from 'react'
import { useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { CONCEPTS, type Concept, type ConceptId } from '../../content/concepts'

// An inline "ⓘ" affordance next to any label that has a deep concept
// explanation in content/concepts.ts. Renders a fixed-position popover
// (portaled to document.body) so it is never clipped by a horizontally
// scrolling ancestor like the ranking or ledger tables. See the Glossary tab
// (views/GlossaryView.tsx) for the full reference -- this is the "look it up
// right here" version.
//
// Two tiers (docs/adr/0033). Tier 1 is a tooltip carrying `concept.short`,
// shown on a MOUSE hover or on keyboard focus (:focus-visible) -- never on a
// touch tap, where it would race the dialog. Tier 2 is the click: the dialog,
// unchanged. The tooltip is suppressed while the dialog is open, so a reader
// never sees two surfaces, and closes on Escape or any scroll. It is portaled
// and fixed like the dialog, so a `.pl-scroll` table cannot clip it, and it
// starts flush against the button (no gap to cross, and it takes no pointer).
//
// The button is 24px square. It used to be 14x14, which is under any reasonable
// floor for a pointer target and well under it for a touch one.

const POP_WIDTH = 300
const TIP_WIDTH = 260
// Below this much room under the button the dialog opens above it.
const POP_ROOM = 340
// Likewise for the tooltip.
const TIP_ROOM = 110

/** A scroll event can be delivered a frame after the scroll that caused it, so
 *  one fired by a scroll BEFORE the surface opened must not close it: a
 *  document scroll counts only if the page actually moved since. Any other
 *  scroll (a `.pl-scroll` table, say) always counts. */
function scrollClosesSurface(e: Event, origin: { x: number; y: number }): boolean {
  if (e.target !== document) return true
  return window.scrollX !== origin.x || window.scrollY !== origin.y
}

type TipPos = { left: number; top?: number; bottom?: number }

export function ConceptInfo({ id }: { id: ConceptId }) {
  const concept: Concept = CONCEPTS[id]
  const tipId = useId()
  const [tipPos, setTipPos] = useState<TipPos | null>(null)
  const [open, setOpen] = useState(false)
  const [pos, setPos] = useState<TipPos | null>(null)
  const buttonRef = useRef<HTMLButtonElement | null>(null)
  const popRef = useRef<HTMLDivElement | null>(null)
  // Where the page was scrolled when the dialog / tooltip opened.
  const openAt = useRef({ x: 0, y: 0 })

  const close = () => setOpen(false)

  /** Put the tooltip flush against the button where it is NOW. */
  const placeTip = () => {
    const rect = buttonRef.current?.getBoundingClientRect()
    if (!rect) return
    const left = Math.max(12, Math.min(rect.left, window.innerWidth - TIP_WIDTH - 12))
    setTipPos(
      window.innerHeight - rect.bottom < TIP_ROOM
        ? { left, bottom: window.innerHeight - rect.top }
        : { left, top: rect.bottom },
    )
  }
  const showTip = () => {
    openAt.current = { x: window.scrollX, y: window.scrollY }
    placeTip()
  }
  const hideTip = () => setTipPos(null)
  const onPointerEnter = (e: PointerEvent) => {
    if (e.pointerType === 'mouse') showTip()
  }
  const onFocus = () => {
    if (buttonRef.current?.matches(':focus-visible')) showTip()
  }

  const toggle = (e: MouseEvent) => {
    e.stopPropagation()
    hideTip()
    if (open) {
      close()
      return
    }
    const rect = buttonRef.current?.getBoundingClientRect()
    if (!rect) return
    openAt.current = { x: window.scrollX, y: window.scrollY }
    const left = Math.max(12, Math.min(rect.left, window.innerWidth - POP_WIDTH - 12))
    // A label near the foot of the screen opens the dialog above itself,
    // rather than below the fold where it cannot be read.
    setPos(
      window.innerHeight - rect.bottom < POP_ROOM
        ? { left, bottom: window.innerHeight - rect.top + 6 }
        : { left, top: rect.bottom + 6 },
    )
    setOpen(true)
  }

  useEffect(() => {
    if (!open) return
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close()
    }
    const onOutside = (e: Event) => {
      const target = e.target as Node
      if (popRef.current?.contains(target) || buttonRef.current?.contains(target)) return
      if (e.type === 'scroll' && !scrollClosesSurface(e, openAt.current)) return
      close()
    }
    document.addEventListener('keydown', onKeyDown)
    document.addEventListener('mousedown', onOutside)
    window.addEventListener('scroll', onOutside, true)
    window.addEventListener('resize', onOutside)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.removeEventListener('mousedown', onOutside)
      window.removeEventListener('scroll', onOutside, true)
      window.removeEventListener('resize', onOutside)
    }
  }, [open])

  useEffect(() => {
    if (!tipPos) return
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') hideTip()
    }
    document.addEventListener('keydown', onKeyDown)
    const onScroll = (e: Event) => {
      const button = buttonRef.current
      // Tabbing to an ⓘ that sits off-screen inside a sideways-scrolling table
      // makes the browser scroll that table to it, and the scroll event lands
      // AFTER the tooltip opened. That scroll is the focus, not the reader
      // leaving, so while the button holds focus a scroll of a container that
      // holds it moves the tooltip with the button; it closes only if the
      // button has been scrolled out of that container altogether.
      if (button && e.target instanceof Element && e.target.contains(button) && button.matches(':focus')) {
        const b = button.getBoundingClientRect()
        const c = e.target.getBoundingClientRect()
        if (b.right <= c.left || b.left >= c.right || b.bottom <= c.top || b.top >= c.bottom) hideTip()
        else placeTip()
        return
      }
      if (scrollClosesSurface(e, openAt.current)) hideTip()
    }
    window.addEventListener('scroll', onScroll, true)
    window.addEventListener('resize', hideTip)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      window.removeEventListener('scroll', onScroll, true)
      window.removeEventListener('resize', hideTip)
    }
  }, [tipPos])

  const tipShown = tipPos !== null && !open

  return (
    <>
      <button
        ref={buttonRef}
        type="button"
        className="pl-concept"
        data-concept={id}
        aria-label={`What is ${concept.term}?`}
        aria-expanded={open}
        aria-describedby={tipId}
        onClick={toggle}
        onPointerEnter={onPointerEnter}
        onPointerLeave={hideTip}
        onFocus={onFocus}
        onBlur={hideTip}
      >
        ⓘ
      </button>
      {/* The description is always there for a screen reader to find, so the
          trigger's aria-describedby never dangles; while the tooltip is shown
          the portaled tooltip takes this id over. Not rendered with a role, so
          it is not a second tooltip. */}
      {!tipShown && createPortal(<span id={tipId} hidden>{concept.short}</span>, document.body)}
      {tipShown &&
        createPortal(
          <div
            id={tipId}
            role="tooltip"
            className="putlab-root pl-concept-tip"
            style={{ left: tipPos.left, top: tipPos.top, bottom: tipPos.bottom, width: TIP_WIDTH }}
          >
            {concept.short}
          </div>,
          document.body,
        )}
      {open &&
        pos &&
        createPortal(
          <div
            ref={popRef}
            className="putlab-root pl-concept-pop"
            role="dialog"
            aria-label={`About ${concept.term}`}
            style={{ top: pos.top, bottom: pos.bottom, left: pos.left, width: POP_WIDTH }}
          >
            <div className="pl-concept-pop-term">{concept.term}</div>
            <p className="pl-concept-pop-short">{concept.short}</p>
            {concept.formula && <pre className="pl-concept-pop-formula">{concept.formula}</pre>}
            <p className="pl-gloss-body">{concept.intuition}</p>
            <p className="pl-concept-pop-how">
              <strong>How to read it.</strong> {concept.howToRead}
            </p>
          </div>,
          document.body,
        )}
    </>
  )
}
