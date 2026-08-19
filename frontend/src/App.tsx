import { FeedbackPanel } from './components/FeedbackPanel'
import { LeaderboardTile } from './components/LeaderboardTile'
import { VixStretchTile } from './components/VixStretchTile'
import { PutLab } from './components/putlab/PutLab'
import './App.css'

function App() {
  return (
    <main>
      <h1>tail-lab</h1>
      <LeaderboardTile />
      <PutLab />
      <VixStretchTile />
      <FeedbackPanel />
    </main>
  )
}

export default App
