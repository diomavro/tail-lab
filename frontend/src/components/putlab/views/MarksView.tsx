import { useState } from 'react'
import type { MarksCoverage, RegimeTimelineView } from '../../../api/client'
import { MarksChart } from './MarksChart'
import { BAND_LABEL, type Measure } from './marksShared'

/* The Marks tab: a name picker and the tenor / measure / depth controls around
 * the shared MarksChart (which fetches and draws one symbol's quote coverage).
 * The chart's reading guide lives in MarksChart.tsx. */

/** Names a fresh page offers before the first answer lists them all. */
const FALLBACK_SYMBOLS = ['nvda', 'qqq', 'spy', 'tsla']
const FALLBACK_BANDS = ['<=2w', '2-6w', '6-17w']

interface Props {
  asset: string
  regimes: RegimeTimelineView | null
}

export function MarksView({ asset, regimes }: Props) {
  const [symbol, setSymbol] = useState(asset.toLowerCase())
  const [band, setBand] = useState(1)
  const [measure, setMeasure] = useState<Measure>('usable')
  const [focus, setFocus] = useState(16)
  // Kept across a failed read, so an error never strands the picker on the
  // one name that failed.
  const [known, setKnown] = useState<string[]>(FALLBACK_SYMBOLS)
  const [bands, setBands] = useState<string[]>(FALLBACK_BANDS)
  const onLoad = (data: MarksCoverage) => {
    setKnown(data.symbols)
    setBands(data.bands)
  }

  return (
    <div>
      <Header />
      <div className="pl-marks-controls">
        <label className="pl-marks-field">
          <span className="pl-kicker">Name</span>
          <select value={symbol} onChange={(e) => setSymbol(e.target.value)} aria-label="Name">
            {(known.includes(symbol) ? known : [symbol, ...known]).map((s) => (
              <option key={s} value={s}>
                {s.toUpperCase()}
              </option>
            ))}
          </select>
        </label>
        <fieldset className="pl-marks-field">
          <legend className="pl-kicker">Tenor</legend>
          <div className="pl-seg">
            {bands.map((b, i) => (
              <label className="pl-seg-opt" key={b}>
                <input type="radio" name="marks-band" checked={band === i} onChange={() => setBand(i)} />
                {BAND_LABEL[b] ?? b}
              </label>
            ))}
          </div>
        </fieldset>
        <fieldset className="pl-marks-field">
          <legend className="pl-kicker">Counts as held</legend>
          <div className="pl-seg">
            <label className="pl-seg-opt">
              <input type="radio" name="marks-measure" checked={measure === 'usable'} onChange={() => setMeasure('usable')} />
              Usable quote
            </label>
            <label className="pl-seg-opt">
              <input type="radio" name="marks-measure" checked={measure === 'traded'} onChange={() => setMeasure('traded')} />
              Traded
            </label>
          </div>
        </fieldset>
      </div>
      <MarksChart
        symbol={symbol}
        regimes={regimes}
        band={band}
        measure={measure}
        focus={focus}
        onFocus={setFocus}
        onLoad={onLoad}
      />
    </div>
  )
}

function Header() {
  return (
    <>
      <h2 style={{ marginBottom: 4 }}>Where we hold a mark</h2>
      <p className="pl-lede" style={{ marginBottom: 18 }}>
        Before a result is believed: for this name, how deep out of the money and in which years does the lake hold a
        real put quote &mdash; and in which regime was each session?
      </p>
    </>
  )
}
