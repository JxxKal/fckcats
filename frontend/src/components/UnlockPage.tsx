import { useState } from 'react'
import { api, ApiError, dataKey, token } from '../api'

interface Props {
  onUnlocked: () => void
}

/** Abfrage der Passphrase nach der Anmeldung, wenn der Workspace gesperrt ist. */
export default function UnlockPage({ onUnlocked }: Props) {
  const [passphrase, setPassphrase] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setError(''); setBusy(true)
    try {
      const res = await api.post<{ data_key: string }>('/api/privacy/unlock', { passphrase })
      dataKey.set(res.data_key)
      onUnlocked()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Entsperren fehlgeschlagen')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="min-h-screen flex flex-col items-center justify-center gap-6 p-6">
      <img src="/logo.svg" alt="fckcats" className="w-52" />
      <div className="panel w-full max-w-sm">
        <div className="panel-head">Workspace entsperren</div>
        <form onSubmit={submit} className="panel-body space-y-3">
          <p className="text-cats-muted">
            Deine Daten sind mit einer eigenen Passphrase verschlüsselt. Ohne sie
            kommt auch der Server nicht heran.
          </p>
          <div>
            <label className="label block mb-1" htmlFor="pass">Passphrase</label>
            <input id="pass" type="password" className="field w-full" value={passphrase}
                   autoFocus autoComplete="current-password"
                   onChange={e => setPassphrase(e.target.value)} />
          </div>
          {error && <p className="text-brand-red">{error}</p>}
          <button className="btn-primary w-full" disabled={busy || !passphrase}>
            {busy ? 'Wird entsperrt …' : 'Entsperren'}
          </button>
          <button type="button" className="btn w-full"
                  onClick={() => { token.clear(); window.location.reload() }}>
            Abmelden
          </button>
        </form>
      </div>
    </div>
  )
}
