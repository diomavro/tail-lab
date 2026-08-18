import { FeedbackPanel } from './components/FeedbackPanel'
import { VixStretchTile } from './components/VixStretchTile'
import { PutLab } from './components/putlab/PutLab'
import './App.css'

function App() {
  return (
    <main>
      <h1>tail-lab</h1>
      <PutLab />
      <VixStretchTile />
      <FeedbackPanel />
    </main>
  )
}

export default App
