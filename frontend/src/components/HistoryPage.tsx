import { useEffect, useState } from 'react'
import { api, ApiError, fmtDate, fmtHours } from '../api'
import type { EntryHistoryRecord, ExportRecord } from '../types'

export default function HistoryPage() {
  const [exports, setExports] = useState<ExportRecord[]>([])
  const [changes, setChanges] = useState<EntryHistoryRecord[]>([])
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')

  useEffect(() => { void load() }, [])

  async function load() {
    const [x, h] = await Promise.all([
      api.get<ExportRecord[]>('/api/exports'),
      api.get<EntryHistoryRecord[]>('/api/entries/history'),
    ])
    setExports(x)
    setChanges(h)
  }

  async function run(fn: () => Promise<unknown>, ok: string) {
    setError(''); setMessage('')
    try { await fn(); setMessage(ok); await load() }
    catch (err) { setError(err instanceof ApiError ? err.message : 'Fehlgeschlagen') }
  }

  function revoke(e: ExportRecord) {
    if (!window.confirm(
      `Export #${e.id} zurücknehmen?\n\nDie ${e.row_count} Zeilen gelten danach wieder als ` +
      `offen und werden beim nächsten Export erneut ausgegeben. Nur sinnvoll, wenn die ` +
      `Datei nicht in SAP angekommen ist.`)) return
    void run(() => api.post(`/api/exports/${e.id}/revoke`), `Export #${e.id} zurückgenommen.`)
  }

  function remove(e: ExportRecord) {
    if (!window.confirm(
      `Export #${e.id} aus der Historie löschen?\n\nEintrag und Datei werden entfernt. ` +
      `Die Zeilen bleiben als gebucht markiert und werden nicht erneut exportiert.\n\n` +
      `Sollen sie stattdessen wieder ausgegeben werden, nimm den Export vorher zurück.`)) return
    void run(() => api.del(`/api/exports/${e.id}`), `Export #${e.id} gelöscht.`)
  }

  function removeAll(revokeToo: boolean) {
    const rows = exports.reduce((s, e) => s + e.row_count, 0)
    const text = revokeToo
      ? `Gesamte Export-Historie löschen UND alle ${rows} Zeilen wieder auf offen setzen?\n\n` +
        `Alles wird beim nächsten Export erneut ausgegeben. Bei bereits in SAP gebuchten ` +
        `Zeiten führt das zu Doppelbuchungen.`
      : `Gesamte Export-Historie löschen?\n\n${exports.length} Einträge und die zugehörigen ` +
        `Dateien werden entfernt. Der Buchungsstatus bleibt erhalten, es wird nichts erneut ` +
        `exportiert.`
    if (!window.confirm(text)) return
    void run(
      () => api.del(`/api/exports?confirm=true&revoke=${revokeToo}`),
      'Export-Historie gelöscht.')
  }

  function clearChanges() {
    if (!window.confirm(
      `Änderungsprotokoll löschen?\n\n${changes.length} Einträge werden entfernt. ` +
      `Zieltabelle, Buchungsstatus und Export-Historie bleiben unberührt.`)) return
    void run(() => api.del('/api/entries/history?confirm=true'), 'Änderungsprotokoll gelöscht.')
  }

  return (
    <div className="space-y-4 max-w-5xl">
      <div className="panel">
        <div className="panel-head">Export-Historie</div>
        <div className="panel-body space-y-3">
          {exports.length === 0 ? (
            <p className="text-cats-muted">Noch keine Exporte erzeugt.</p>
          ) : (
            <>
              <table className="table-cats">
                <thead>
                  <tr>
                    <th className="w-14">#</th>
                    <th>Datei</th>
                    <th className="w-44">Zeitraum</th>
                    <th className="w-16 text-right">Zeilen</th>
                    <th className="w-20 text-right">Stunden</th>
                    <th className="w-40">erzeugt</th>
                    <th className="w-56"></th>
                  </tr>
                </thead>
                <tbody>
                  {exports.map(e => (
                    <tr key={e.id}>
                      <td className="num">{e.id}</td>
                      <td className="font-mono">{e.filename}</td>
                      <td className="font-mono whitespace-nowrap">
                        {fmtDate(e.date_from)} – {fmtDate(e.date_to)}
                      </td>
                      <td className="num">{e.row_count}</td>
                      <td className="num">{fmtHours(e.total_hours)}</td>
                      <td className="font-mono">{new Date(e.created_at).toLocaleString('de-DE')}</td>
                      <td className="flex gap-1">
                        <button className="btn flex-1"
                                onClick={() => api.download(e.download_url, e.filename)}>
                          Download
                        </button>
                        <button className="btn" title="Zurücknehmen: Zeilen wieder offen"
                                onClick={() => revoke(e)}>↺</button>
                        <button className="btn-danger" title="Aus der Historie löschen"
                                onClick={() => remove(e)}>×</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>

              <div className="flex flex-wrap gap-2 items-center">
                <button className="btn" onClick={() => removeAll(false)}>
                  Historie löschen
                </button>
                <button className="btn-danger" onClick={() => removeAll(true)}>
                  Historie löschen und alles wieder freigeben
                </button>
              </div>
              <p className="text-cats-muted">
                <strong>Löschen</strong> räumt nur auf — die Zeilen bleiben als gebucht
                markiert. <strong>Freigeben</strong> setzt sie zusätzlich auf offen, sodass
                sie erneut exportiert werden. Für bereits in SAP gebuchte Zeiten bedeutet
                das eine Doppelbuchung.
              </p>
            </>
          )}
        </div>
      </div>

      <div className="panel">
        <div className="panel-head">Änderungsprotokoll</div>
        <div className="panel-body space-y-3">
          {changes.length === 0 ? (
            <p className="text-cats-muted">
              Noch nichts ersetzt. Hier landen die alten Zeilen, wenn ein korrigiertes PDF
              einen bereits berechneten Zeitraum überschreibt.
            </p>
          ) : (
            <>
              <table className="table-cats">
                <thead>
                  <tr>
                    <th className="w-32">Tag</th>
                    <th>ersetzte Zeilen</th>
                    <th className="w-32">war exportiert</th>
                    <th className="w-40">ersetzt am</th>
                  </tr>
                </thead>
                <tbody>
                  {changes.map((c, i) => (
                    <tr key={i}>
                      <td className="font-mono whitespace-nowrap">{fmtDate(c.work_date)}</td>
                      <td className="font-mono">
                        {c.payload.map((r, j) => (
                          <div key={j}>{r.wbs_element} · {fmtHours(r.hours)} h</div>
                        ))}
                      </td>
                      <td className={c.was_exported ? 'text-brand-red font-bold' : 'text-cats-muted'}>
                        {c.was_exported ? 'ja' : 'nein'}
                      </td>
                      <td className="font-mono">{new Date(c.replaced_at).toLocaleString('de-DE')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <button className="btn" onClick={clearChanges}>Änderungsprotokoll löschen</button>
            </>
          )}
        </div>
      </div>

      {message && <p className="text-brand-dark font-bold">{message}</p>}
      {error && <p className="text-brand-red">{error}</p>}
    </div>
  )
}
