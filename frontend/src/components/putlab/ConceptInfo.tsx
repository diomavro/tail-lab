import type { MouseEvent } from 'react'
import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { CONCEPTS } from '../../content/concepts'

// A tiny inline "ⓘ" affordance next to any label that has a deep concept
// explanation in content/concepts.ts. Renders a fixed-position popover
// (portaled to document.body) so it is never clipped by a horizontally
// scrolling ancestor like the .lb-scroll leaderboard tables. See the
// Learn tab (views/LearnView.tsx) for the full concept reference -- this
// is the "look it up right here" version.

const POP_WIDTH = 300

export function ConceptInfo({ id }: { id: string }) {
  const concept = CONCEPTS[id]
  const [open, setOpen] = useState(false)
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null)
  const buttonRef = useRef<HTMLButtonElement | null>(null)
  const popRef = useRef<HTMLDivElement | null>(null)

  const close = () => setOpen(false)

  const toggle = (e: MouseEvent) => {
    e.stopPropagation()
    if (open) {
      close()
      return
    }
    const rect = buttonRef.current?.getBoundingClientRect()
    if (!rect) return
    setPos({
      top: rect.bottom + 6,
      left: Math.max(12, Math.min(rect.left, window.innerWidth - POP_WIDTH - 12)),
    })
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

  if (!concept) return null

  return (
    <>
      <button
        ref={buttonRef}
        type="button"
        className="concept-info"
        aria-label={`What is ${concept.term}?`}
        aria-expanded={open}
        onClick={toggle}
      >
        ⓘ
      </button>
      {open &&
        pos &&
        createPortal(
          <div
            ref={popRef}
            className="putlab-root concept-pop"
            role="dialog"
            aria-label={`About ${concept.term}`}
            style={{ top: pos.top, left: pos.left, width: POP_WIDTH }}
          >
            <div className="concept-pop-term">{concept.term}</div>
            <p className="concept-pop-short">{concept.short}</p>
            {concept.formula && <pre className="mono concept-pop-formula">{concept.formula}</pre>}
            <p className="concept-pop-intuition">{concept.intuition}</p>
            <p className="concept-pop-how">
              <strong>How to read it.</strong> {concept.howToRead}
            </p>
          </div>,
          document.body,
        )}
    </>
  )
}
