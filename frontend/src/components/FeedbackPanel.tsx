import { useState } from 'react'
import { submitFeedback, type FeedbackKind } from '../api/client'

type SubmitState =
  | { status: 'idle' }
  | { status: 'submitting' }
  | { status: 'success' }
  | { status: 'error'; message: string }

export function FeedbackPanel() {
  const [text, setText] = useState('')
  const [kind, setKind] = useState<FeedbackKind>('issue')
  const [state, setState] = useState<SubmitState>({ status: 'idle' })

  const handleTextChange = (value: string) => {
    setText(value)
    if (state.status === 'success' || state.status === 'error') setState({ status: 'idle' })
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    const trimmed = text.trim()
    if (!trimmed) return
    setState({ status: 'submitting' })
    try {
      await submitFeedback(trimmed, kind)
      setText('')
      setState({ status: 'success' })
    } catch (err: unknown) {
      setState({
        status: 'error',
        message: err instanceof Error ? err.message : String(err),
      })
    }
  }

  return (
    <div className="tile feedback-panel">
      <h2>Feedback</h2>
      <form onSubmit={(e) => void handleSubmit(e)}>
        <fieldset className="feedback-kind">
          <label>
            <input
              type="radio"
              name="feedback-kind"
              value="big_picture"
              checked={kind === 'big_picture'}
              onChange={() => setKind('big_picture')}
            />
            Big-picture / goal (permanent)
          </label>
          <label>
            <input
              type="radio"
              name="feedback-kind"
              value="issue"
              checked={kind === 'issue'}
              onChange={() => setKind('issue')}
            />
            Bug / issue (until fixed)
          </label>
        </fieldset>
        <textarea
          value={text}
          onChange={(e) => handleTextChange(e.target.value)}
          placeholder="What should the daily agent know?"
          maxLength={4000}
          rows={2}
          disabled={state.status === 'submitting'}
        />
        <button type="submit" disabled={state.status === 'submitting' || !text.trim()}>
          {state.status === 'submitting' ? 'Submitting…' : 'Submit'}
        </button>
        {state.status === 'success' && <p className="feedback-success">Saved.</p>}
        {state.status === 'error' && <p className="tile-error">{state.message}</p>}
      </form>
    </div>
  )
}
