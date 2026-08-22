import {
  CATEGORY_LABEL,
  CATEGORY_ORDER,
  CONCEPTS,
  CONCEPT_LIST,
  type Concept,
  type ConceptCategory,
} from '../../../content/concepts'

/* "What do these mean?" — the concept reference.
 *
 * Every field is rendered VERBATIM from content/concepts.ts. That file is the
 * source of truth for what each figure on the site means, and paraphrasing it
 * into a view is exactly how the grounding errors this redesign corrects got in:
 * a hit rate described as "finished in the money", regime bands described as
 * realized volatility, an ROI described as net of brokerage. If a definition
 * reads wrong here, the fix belongs in concepts.ts.
 *
 * Two columns instead of a card grid: term and one-liner on the left, the
 * formula and the reasoning on the right, so the page can be skimmed down the
 * left rail and read across only where it matters.
 */

const grouped: [ConceptCategory, Concept[]][] = CATEGORY_ORDER.map(
  (cat) => [cat, CONCEPT_LIST.filter((c) => c.category === cat)] as [ConceptCategory, Concept[]],
).filter(([, list]) => list.length > 0)

export function GlossaryView() {
  return (
    <div>
      <h2 style={{ marginBottom: 4 }}>The glossary</h2>
      <p className="pl-lede" style={{ marginBottom: 10 }}>
        Every figure this site prints, with the formula behind it and the reason it is worth
        looking at. Definitions here are the ones the backtest actually computes.
      </p>

      {grouped.map(([cat, list]) => (
        <section key={cat} id={`cat-${cat}`}>
          <h3 className="pl-gloss-cat">{CATEGORY_LABEL[cat]}</h3>
          {list.map((c) => (
            <article key={c.id} id={c.id} className="pl-gloss-entry">
              <div>
                <div className="pl-gloss-term">{c.term}</div>
                <p className="pl-gloss-short">{c.short}</p>
              </div>
              <div>
                {c.formula && <div className="pl-gloss-formula">{c.formula}</div>}
                <p className="pl-gloss-body">{c.intuition}</p>
                <p className="pl-gloss-read">
                  <strong>How to read it.</strong> {c.howToRead}
                </p>
                {c.seeAlso && c.seeAlso.length > 0 && (
                  <div className="pl-gloss-see">
                    <span className="pl-kicker">See also</span>
                    {c.seeAlso.map((id) => (
                      <a key={id} href={`#${id}`}>
                        {CONCEPTS[id]?.term ?? id}
                      </a>
                    ))}
                  </div>
                )}
              </div>
            </article>
          ))}
        </section>
      ))}
    </div>
  )
}
