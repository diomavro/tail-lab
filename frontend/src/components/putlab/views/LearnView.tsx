import {
  CATEGORY_LABEL,
  CATEGORY_ORDER,
  CONCEPT_LIST,
  CONCEPTS,
  type Concept,
  type ConceptCategory,
} from '../../../content/concepts'

// "What do these mean?" -- the concept reference. Renders CONCEPT_LIST grouped
// by CATEGORY_ORDER with a sticky category rail. Each concept is a card
// anchored by its id, so seeAlso links scroll to the referenced concept. This
// is the one view where vertical scrolling is expected (it's a reference doc);
// long formulas wrap rather than scroll sideways.
const grouped: [ConceptCategory, Concept[]][] = CATEGORY_ORDER.map(
  (cat) => [cat, CONCEPT_LIST.filter((c) => c.category === cat)] as [ConceptCategory, Concept[]],
).filter(([, list]) => list.length > 0)

export function LearnView() {
  return (
    <div className="learn">
      <nav className="learn-nav" aria-label="Concept categories">
        <div className="eyebrow" style={{ marginBottom: 10 }}>
          Reference
        </div>
        <ul>
          {grouped.map(([cat]) => (
            <li key={cat}>
              <a href={`#cat-${cat}`}>{CATEGORY_LABEL[cat]}</a>
            </li>
          ))}
        </ul>
      </nav>

      <div className="learn-body">
        {grouped.map(([cat, list]) => (
          <section key={cat} className="learn-section" id={`cat-${cat}`}>
            <h2 className="learn-cat">{CATEGORY_LABEL[cat]}</h2>
            <div className="learn-cards">
              {list.map((c) => (
                <article key={c.id} id={c.id} className="learn-card">
                  <h3 className="learn-term">{c.term}</h3>
                  <p className="learn-short">{c.short}</p>
                  {c.formula && <pre className="mono learn-formula">{c.formula}</pre>}
                  <p className="learn-intuition">{c.intuition}</p>
                  <p className="learn-howto">
                    <span className="learn-howto-k">How to read it</span>
                    {c.howToRead}
                  </p>
                  {c.seeAlso && c.seeAlso.length > 0 && (
                    <div className="learn-see">
                      <span className="learn-see-k">See also</span>
                      {c.seeAlso.map((id) => (
                        <a key={id} href={`#${id}`} className="learn-see-link">
                          {CONCEPTS[id]?.term ?? id}
                        </a>
                      ))}
                    </div>
                  )}
                </article>
              ))}
            </div>
          </section>
        ))}
      </div>
    </div>
  )
}
