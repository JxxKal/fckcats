import { useEffect, useState } from 'react'
import { api, ApiError, dataKey } from '../api'
import type { PrivacySettings } from '../types'

/** Datenschutz-Einstellungen des angemeldeten Benutzers. */
export default function PrivacyPage() {
  const [p, setP] = useState<PrivacySettings | null>(null)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [showPass, setShowPass] = useState(false)

  useEffect(() => { void load() }, [])

  async function load() {
    try {
      setP(await api.get<PrivacySettings>('/api/privacy'))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Laden fehlgeschlagen')
    }
  }

  async function run(fn: () => Promise<unknown>, ok: string) {
    setError(''); setMessage('')
    try { await fn(); setMessage(ok); await load() }
    catch (err) { setError(err instanceof ApiError ? err.message : 'Fehlgeschlagen') }
  }

  async function switchMode(mode: 'persistent' | 'ephemeral') {
    if (!p || mode === p.storage_mode) return
    if (mode === 'ephemeral') {
      const n = p.stored_total
      if (n > 0 && !window.confirm(
        `Auf "nichts speichern" umstellen?\n\n${p.stored.workdays} Arbeitstage, ` +
        `${p.stored.entries} Zieltabellen-Zeilen, ${p.stored.uploads} PDFs, ` +
        `${p.stored.exports} Exporte und ${p.stored.history} Protokolleinträge ` +
        `werden dabei unwiderruflich gelöscht.\n\n` +
        `Deine CATS-Config bleibt erhalten.`)) return
    }
    await run(
      () => api.put('/api/privacy/storage-mode', { mode, confirm_purge: true }),
      mode === 'ephemeral'
        ? 'Speicherung abgeschaltet. Gespeicherte Daten wurden gelöscht.'
        : 'Speicherung eingeschaltet.')
  }

  function purge() {
    if (!p) return
    if (!window.confirm(
      `Alle gespeicherten Arbeitszeitdaten löschen?\n\n${p.stored_total} Datensätze ` +
      `insgesamt. Die CATS-Config bleibt erhalten.`)) return
    void run(() => api.del('/api/privacy/data?confirm=true'), 'Daten gelöscht.')
  }

  if (!p) return <p className="text-cats-muted">Wird geladen …</p>

  const ephemeral = p.storage_mode === 'ephemeral'

  return (
    <div className="space-y-4 max-w-3xl">
      <div className="panel">
        <div className="panel-head">Speicherung deiner Daten</div>
        <div className="panel-body space-y-3">
          <label className="flex gap-3 items-start cursor-pointer">
            <input type="radio" name="mode" className="mt-1" checked={!ephemeral}
                   onChange={() => switchMode('persistent')} />
            <span>
              <strong>Speichern und Historie führen</strong>
              <span className="block text-cats-muted">
                Arbeitszeiten, Zieltabelle, hochgeladene PDFs und Exporte bleiben im
                Workspace — verschlüsselt. Die App weiß dann, was bereits nach CATS
                gebucht wurde, warnt vor Doppelbuchungen und kann korrigierte PDFs
                gegen den Bestand abgleichen.
              </span>
            </span>
          </label>

          <label className="flex gap-3 items-start cursor-pointer">
            <input type="radio" name="mode" className="mt-1" checked={ephemeral}
                   onChange={() => switchMode('ephemeral')} />
            <span>
              <strong>Nichts speichern</strong>
              <span className="block text-cats-muted">
                Reines Import- und Export-Werkzeug: PDF hochladen, Verteilung
                berechnen, XLSX herunterladen — danach bleibt nichts zurück. Du musst
                selbst im Blick behalten, welche Zeiträume du schon nach CATS gebucht
                hast; die App kann davor nicht mehr warnen.
              </span>
            </span>
          </label>

          {ephemeral && (
            <p className="border border-brand-yellow bg-cats-fieldkey p-2">
              Es wird derzeit nichts gespeichert. Der Export läuft direkt aus der
              Vorschau des Imports.
            </p>
          )}
        </div>
      </div>

      {!ephemeral && (
        <div className="panel">
          <div className="panel-head">Gespeicherter Bestand</div>
          <div className="panel-body space-y-3">
            <table className="table-cats">
              <tbody>
                <tr><td className="w-64">Arbeitstage</td><td className="num">{p.stored.workdays}</td></tr>
                <tr><td>Zeilen der Zieltabelle</td><td className="num">{p.stored.entries}</td></tr>
                <tr><td>hochgeladene PDFs</td><td className="num">{p.stored.uploads}</td></tr>
                <tr><td>erzeugte Exporte</td><td className="num">{p.stored.exports}</td></tr>
                <tr><td>Protokolleinträge</td><td className="num">{p.stored.history}</td></tr>
              </tbody>
            </table>
            <button className="btn-danger" onClick={purge} disabled={p.stored_total === 0}>
              Alle gespeicherten Daten löschen
            </button>
            <p className="text-cats-muted">
              Die CATS-Config bleibt dabei erhalten.
            </p>
          </div>
        </div>
      )}

      <div className="panel">
        <div className="panel-head">Verschlüsselung</div>
        <div className="panel-body space-y-3">
          <p>
            Alles, was gespeichert wird, liegt verschlüsselt: Stunden, WBS-Elemente,
            Personalnummer, die hochgeladenen PDFs und die erzeugten XLSX. Wer an die
            Platte, ein Backup oder einen Datenbank-Abzug kommt, kann damit nichts
            anfangen.
          </p>

          {p.encryption === 'master' ? (
            <>
              <p className="text-cats-muted">
                Der Schlüssel wird aus dem Server-Schlüssel abgeleitet. Das schützt
                gegen kopierte Volumes, Backups und ausgebaute Platten — wer aber
                Zugriff auf den Server samt Konfiguration hat, kommt an die Daten.
                Mit einer eigenen Passphrase sperrst du auch den Betreiber aus.
              </p>
              <button className="btn" onClick={() => setShowPass(!showPass)}>
                {showPass ? 'Abbrechen' : 'Eigene Passphrase einrichten'}
              </button>

              {showPass && (
                <form className="border border-brand p-3 space-y-2 bg-cats-rowalt"
                      onSubmit={e => {
                        e.preventDefault()
                        const f = new FormData(e.currentTarget)
                        const pass = String(f.get('passphrase'))
                        if (pass !== String(f.get('repeat'))) {
                          setError('Die Eingaben stimmen nicht überein.'); return
                        }
                        if (!window.confirm(
                          'Passphrase setzen?\n\nOhne sie kommt niemand mehr an deine ' +
                          'Daten — auch der Betreiber nicht. Geht sie verloren, sind ' +
                          'die gespeicherten Daten unwiederbringlich weg. Es gibt kein ' +
                          'Zurücksetzen.')) return
                        void run(async () => {
                          const res = await api.post<{ data_key: string }>(
                            '/api/privacy/passphrase', { passphrase: pass })
                          dataKey.set(res.data_key)
                          setShowPass(false)
                        }, 'Passphrase gesetzt.')
                      }}>
                  <div>
                    <label className="label block mb-1">Passphrase (mindestens 12 Zeichen)</label>
                    <input name="passphrase" type="password" required minLength={12}
                           className="field w-full" autoComplete="new-password" />
                  </div>
                  <div>
                    <label className="label block mb-1">Wiederholen</label>
                    <input name="repeat" type="password" required minLength={12}
                           className="field w-full" autoComplete="new-password" />
                  </div>
                  <p className="text-brand-red">
                    Es gibt keine Wiederherstellung. Notiere sie an einem sicheren Ort.
                  </p>
                  <button className="btn-primary">Passphrase setzen</button>
                </form>
              )}
            </>
          ) : (
            <>
              <p className="border border-brand bg-cats-rowalt p-2">
                <strong>Eigene Passphrase aktiv.</strong> Der Schlüssel wird nur mit
                deiner Passphrase ausgewickelt. Du gibst sie nach jeder Anmeldung
                einmal ein; sie gilt dann für den Browser-Tab.
              </p>
              <p className="text-cats-muted">
                Ehrlich gesagt: während du arbeitest, läuft der Schlüssel durch den
                Arbeitsspeicher des Servers — die PDF-Auswertung und die Verteilung
                passieren dort. Gegen jemanden, der den laufenden Server kontrolliert,
                hilft auch eine Passphrase nicht.
              </p>
              <div className="flex flex-wrap gap-2">
                <button className="btn" onClick={() => {
                  const cur = window.prompt('Aktuelle Passphrase:')
                  if (!cur) return
                  const next = window.prompt('Neue Passphrase (mindestens 12 Zeichen):')
                  if (!next) return
                  void run(async () => {
                    const res = await api.post<{ data_key: string }>(
                      '/api/privacy/passphrase/change',
                      { current_passphrase: cur, new_passphrase: next })
                    dataKey.set(res.data_key)
                  }, 'Passphrase geändert.')
                }}>
                  Passphrase ändern
                </button>
                <button className="btn" onClick={() => {
                  const cur = window.prompt(
                    'Passphrase entfernen — zur Bestätigung die aktuelle eingeben:')
                  if (!cur) return
                  void run(async () => {
                    await api.delWithBody('/api/privacy/passphrase', { passphrase: cur })
                    dataKey.clear()
                  }, 'Passphrase entfernt. Der Server-Schlüssel übernimmt wieder.')
                }}>
                  Passphrase entfernen
                </button>
              </div>
            </>
          )}
        </div>
      </div>

      {message && <p className="text-brand-dark font-bold">{message}</p>}
      {error && <p className="text-brand-red">{error}</p>}
    </div>
  )
}
