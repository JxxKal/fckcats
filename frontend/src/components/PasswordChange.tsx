import { useState } from 'react'
import { api, ApiError, token } from '../api'

interface Props {
  onDone: () => void
}

/** Erzwungener Wechsel des Bootstrap-Passworts beim ersten Login. */
export default function PasswordChange({ onDone }: Props) {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [repeat, setRepeat] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const tooShort = next.length > 0 && next.length < 12
  const mismatch = repeat.length > 0 && next !== repeat

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    setBusy(true)
    try {
      const res = await api.post<{ token: string }>('/api/auth/password', {
        current_password: current, new_password: next,
      })
      token.set(res.token)
      onDone()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Änderung fehlgeschlagen')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="min-h-screen flex flex-col items-center justify-center gap-6 p-6">
      <img src="/logo.svg" alt="fckcats" className="w-52" />
      <div className="panel w-full max-w-sm">
        <div className="panel-head">Passwort ändern</div>
        <form onSubmit={submit} className="panel-body space-y-3">
          <p className="text-cats-muted">
            Das Startpasswort muss vor der ersten Nutzung geändert werden.
          </p>
          <div>
            <label className="label block mb-1">Aktuelles Passwort</label>
            <input type="password" className="field w-full" value={current}
                   autoComplete="current-password"
                   onChange={e => setCurrent(e.target.value)} />
          </div>
          <div>
            <label className="label block mb-1">Neues Passwort</label>
            <input type="password" className="field w-full" value={next}
                   autoComplete="new-password"
                   onChange={e => setNext(e.target.value)} />
            {tooShort && <p className="text-brand-red">Mindestens 12 Zeichen.</p>}
          </div>
          <div>
            <label className="label block mb-1">Neues Passwort wiederholen</label>
            <input type="password" className="field w-full" value={repeat}
                   autoComplete="new-password"
                   onChange={e => setRepeat(e.target.value)} />
            {mismatch && <p className="text-brand-red">Die Eingaben stimmen nicht überein.</p>}
          </div>
          {error && <p className="text-brand-red">{error}</p>}
          <button className="btn-primary w-full"
                  disabled={busy || !current || next.length < 12 || next !== repeat}>
            Passwort ändern
          </button>
        </form>
      </div>
    </div>
  )
}
