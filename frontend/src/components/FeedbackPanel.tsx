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
    // Rendered inside .putlab-root, so it wears the workspace's own classes --
    // App.css's `.tile` chrome would print a bordered card in the middle of a
    // page whose whole argument is that hierarchy comes from type, not boxes.
    <div className="pl-feedback">
      <div className="pl-kicker" style={{ marginBottom: 8 }}>
        Feedback
      </div>
      <form onSubmit={(e) => void handleSubmit(e)} className="pl-feedback-form">
        <fieldset className="pl-feedback-kind">
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
          className="pl-input"
          value={text}
          onChange={(e) => handleTextChange(e.target.value)}
          placeholder="What should the daily agent know?"
          aria-label="Feedback"
          maxLength={4000}
          rows={2}
          disabled={state.status === 'submitting'}
        />
        <button
          type="submit"
          className="pl-btn pl-btn-secondary"
          disabled={state.status === 'submitting' || !text.trim()}
        >
          {state.status === 'submitting' ? 'Submitting…' : 'Send feedback'}
        </button>
        {state.status === 'success' && <p className="pl-micro">Saved.</p>}
        {state.status === 'error' && (
          <p className="pl-status pl-status-error" role="alert">
            {state.message}
          </p>
        )}
      </form>
    </div>
  )
}
