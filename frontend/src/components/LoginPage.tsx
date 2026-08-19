import { useEffect, useState } from 'react'
import { api, ApiError, token } from '../api'

interface Props {
  onLoggedIn: () => void
}

export default function LoginPage({ onLoggedIn }: Props) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [samlEnabled, setSamlEnabled] = useState(false)

  useEffect(() => {
    api.get<{ enabled: boolean }>('/api/auth/saml/enabled')
      .then(r => setSamlEnabled(r.enabled))
      .catch(() => setSamlEnabled(false))
  }, [])

  // Nach dem SAML-Redirect steht das Token als Query-Parameter in der URL.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const t = params.get('saml_token')
    if (t) {
      token.set(t)
      window.history.replaceState({}, '', window.location.pathname)
      onLoggedIn()
    }
  }, [onLoggedIn])

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    setBusy(true)
    try {
      const res = await api.post<{ token: string }>('/api/auth/login', { username, password })
      token.set(res.token)
      onLoggedIn()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Anmeldung fehlgeschlagen')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="min-h-screen flex flex-col items-center justify-center gap-8 p-6">
      <div className="flex flex-col items-center gap-3">
        <img src="/logo.svg" alt="fckcats" className="w-72 max-w-full" />
        {/* Aufloesung des Kuerzels; das K in "Komplete" traegt den Witz. */}
        <p className="tracking-[0.15em] text-cats-muted">
          <span className="font-bold text-brand-darker">F</span>etch
          <span className="mx-2">–</span>
          <span className="font-bold text-brand-darker">C</span>heck
          <span className="mx-2">–</span>
          <span className="font-bold text-brand-darker">K</span>omplete
        </p>
      </div>

      <div className="panel w-full max-w-sm">
        <div className="panel-head">Anmeldung</div>
        <div className="panel-body space-y-3">
          {samlEnabled && (
            <>
              <a href="/api/auth/saml/login" className="btn-primary block text-center">
                Mit Single Sign-on anmelden
              </a>
              <div className="flex items-center gap-2 text-cats-muted">
                <hr className="flex-1 border-cats-border" />
                <span>oder lokal</span>
                <hr className="flex-1 border-cats-border" />
              </div>
            </>
          )}

          <form onSubmit={submit} className="space-y-3">
            <div>
              <label className="label block mb-1" htmlFor="username">Benutzername</label>
              <input id="username" className="field w-full" value={username}
                     autoComplete="username" autoFocus
                     onChange={e => setUsername(e.target.value)} />
            </div>
            <div>
              <label className="label block mb-1" htmlFor="password">Passwort</label>
              <input id="password" type="password" className="field w-full" value={password}
                     autoComplete="current-password"
                     onChange={e => setPassword(e.target.value)} />
            </div>
            {error && <p className="text-brand-red">{error}</p>}
            <button className="btn-primary w-full" disabled={busy || !username || !password}>
              {busy ? 'Anmeldung läuft …' : 'Anmelden'}
            </button>
          </form>
        </div>
      </div>
    </div>
  )
}
