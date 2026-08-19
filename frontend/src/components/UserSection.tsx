import { useEffect, useState } from 'react'
import { api, ApiError } from '../api'
import type { AdminUser } from '../types'

/** Benutzerverwaltung in den Einstellungen. Nur fuer Administratoren. */
export default function UserSection() {
  const [users, setUsers] = useState<AdminUser[]>([])
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [showNew, setShowNew] = useState(false)
  const [pwFor, setPwFor] = useState<AdminUser | null>(null)

  useEffect(() => { void load() }, [])

  async function load() {
    try {
      setUsers(await api.get<AdminUser[]>('/api/admin/users'))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Laden fehlgeschlagen')
    }
  }

  async function run(fn: () => Promise<unknown>, ok: string) {
    setError(''); setMessage('')
    try { await fn(); setMessage(ok); await load() }
    catch (err) { setError(err instanceof ApiError ? err.message : 'Fehlgeschlagen') }
  }

  const admins = users.filter(u => u.role === 'admin' && u.active).length

  return (
    <div className="panel">
      <div className="panel-head">Benutzer</div>
      <div className="panel-body space-y-3">
        <table className="table-cats">
          <thead>
            <tr>
              <th>Benutzer</th>
              <th>Anzeigename</th>
              <th className="w-32">Anmeldung</th>
              <th className="w-32">Rolle</th>
              <th className="w-20 text-right">Zeilen</th>
              <th className="w-40">letzter Login</th>
              <th className="w-56"></th>
            </tr>
          </thead>
          <tbody>
            {users.map(u => (
              <tr key={u.id} className={u.active ? '' : 'opacity-50'}>
                <td className="font-mono">
                  {u.username}
                  {!u.active && <span className="text-brand-red"> (deaktiviert)</span>}
                  {u.must_change_password && (
                    <span className="text-cats-muted"> · Passwortwechsel offen</span>
                  )}
                </td>
                <td>{u.display_name || '—'}</td>
                <td>
                  {u.source === 'saml'
                    ? <span className="text-brand-dark font-bold">SAML</span>
                    : <span className="text-cats-muted">lokal</span>}
                </td>
                <td>
                  <select className="field w-full" value={u.role}
                          onChange={e => run(
                            () => api.patch(`/api/admin/users/${u.id}`, { role: e.target.value }),
                            `Rolle von ${u.username} geändert.`)}>
                    <option value="user">Benutzer</option>
                    <option value="admin">Administrator</option>
                  </select>
                </td>
                <td className="num text-cats-muted">{u.entry_count}</td>
                <td className="font-mono text-cats-muted">
                  {u.last_login ? new Date(u.last_login).toLocaleString('de-DE') : '—'}
                </td>
                <td className="flex gap-1">
                  {u.source === 'local' ? (
                    <button className="btn flex-1" onClick={() => { setPwFor(u); setMessage('') }}>
                      Passwort
                    </button>
                  ) : (
                    <span className="flex-1 px-2 py-1 text-cats-muted" title="Das Kennwort verwaltet der Identity Provider">
                      via IdP
                    </span>
                  )}
                  <button className="btn flex-1"
                          disabled={u.active && u.role === 'admin' && admins <= 1}
                          title={u.active && u.role === 'admin' && admins <= 1
                            ? 'Der letzte aktive Administrator kann nicht deaktiviert werden'
                            : undefined}
                          onClick={() => run(
                            () => api.patch(`/api/admin/users/${u.id}`, { active: !u.active }),
                            `${u.username} ${u.active ? 'deaktiviert' : 'aktiviert'}.`)}>
                    {u.active ? 'Deaktivieren' : 'Aktivieren'}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        <p className="text-cats-muted">
          SAML-Benutzer erscheinen hier automatisch nach ihrer ersten Anmeldung. Ihr
          Kennwort verwaltet der Identity Provider; hier lassen sich nur Rolle und
          Zugang steuern.
        </p>

        <button className="btn-primary" onClick={() => { setShowNew(!showNew); setMessage('') }}>
          {showNew ? 'Abbrechen' : 'Lokalen Benutzer anlegen'}
        </button>

        {showNew && (
          <form className="border border-cats-border p-3 space-y-2 bg-cats-rowalt"
                onSubmit={e => {
                  e.preventDefault()
                  const f = new FormData(e.currentTarget)
                  const form = e.currentTarget
                  void run(async () => {
                    await api.post('/api/admin/users', {
                      username: f.get('username'),
                      password: f.get('password'),
                      display_name: f.get('display_name') || '',
                      email: f.get('email') || '',
                      role: f.get('role'),
                    })
                    form.reset()
                    setShowNew(false)
                  }, 'Benutzer angelegt. Das Startpasswort muss beim ersten Login geändert werden.')
                }}>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="label block mb-1">Benutzername</label>
                <input name="username" required className="field w-full" autoComplete="off" />
              </div>
              <div>
                <label className="label block mb-1">Anzeigename</label>
                <input name="display_name" className="field w-full" />
              </div>
              <div>
                <label className="label block mb-1">E-Mail</label>
                <input name="email" type="email" className="field w-full" />
              </div>
              <div>
                <label className="label block mb-1">Rolle</label>
                <select name="role" className="field w-full" defaultValue="user">
                  <option value="user">Benutzer</option>
                  <option value="admin">Administrator</option>
                </select>
              </div>
              <div className="col-span-2">
                <label className="label block mb-1">Startpasswort (mindestens 12 Zeichen)</label>
                <input name="password" required minLength={12} className="field w-full"
                       autoComplete="new-password" />
              </div>
            </div>
            <button className="btn-primary">Anlegen</button>
          </form>
        )}

        {pwFor && (
          <form className="border border-brand p-3 space-y-2 bg-cats-rowalt"
                onSubmit={e => {
                  e.preventDefault()
                  const f = new FormData(e.currentTarget)
                  void run(async () => {
                    await api.post(`/api/admin/users/${pwFor.id}/password`, {
                      new_password: f.get('new_password'),
                      must_change: f.get('must_change') === 'on',
                    })
                    setPwFor(null)
                  }, `Passwort von ${pwFor.username} gesetzt.`)
                }}>
            <div className="font-bold text-brand-darker">
              Passwort für {pwFor.username} setzen
            </div>
            <div>
              <label className="label block mb-1">Neues Passwort (mindestens 12 Zeichen)</label>
              <input name="new_password" type="text" required minLength={12}
                     className="field w-full" autoComplete="new-password" />
            </div>
            <label className="flex items-center gap-2">
              <input type="checkbox" name="must_change" defaultChecked />
              <span>Wechsel beim nächsten Login erzwingen</span>
            </label>
            <p className="text-cats-muted">
              Mit dieser Option kennt nach dem nächsten Login niemand außer der Person
              selbst ihr Passwort.
            </p>
            <div className="flex gap-2">
              <button className="btn-primary">Setzen</button>
              <button type="button" className="btn" onClick={() => setPwFor(null)}>Abbrechen</button>
            </div>
          </form>
        )}

        {message && <p className="text-brand-dark font-bold">{message}</p>}
        {error && <p className="text-brand-red">{error}</p>}
      </div>
    </div>
  )
}
