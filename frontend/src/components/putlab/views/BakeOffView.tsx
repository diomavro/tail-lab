import { BakeOff } from '../BakeOff'
import type { PutLabControls } from '../types'

// "Is the screen any good?" -- the tab that tests the fragility ranking's own
// premise. It sits between Portfolio and Regime because it is a question about
// the tool rather than about a position.
export function BakeOffView({ controls }: { controls: PutLabControls }) {
  return <BakeOff controls={controls} />
}
