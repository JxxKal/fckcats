import { useEffect, useState } from 'react'
import { api, ApiError, fmtDate, fmtHours } from '../api'
import type { ExportRecord } from '../types'

export default function HistoryPage() {
  const [exports, setExports] = useState<ExportRecord[]>([])
  const [error, setError] = useState('')

  useEffect(() => { void load() }, [])

  async function load() {
    setExports(await api.get<ExportRecord[]>('/api/exports'))
  }

  async function revoke(id: number) {
    if (!window.confirm(
      'Die Zeilen dieses Exports werden wieder als offen markiert. ' +
      'Nur sinnvoll, wenn die Datei nicht in SAP angekommen ist. Fortfahren?')) return
    try {
      await api.del(`/api/exports/${id}`)
      await load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Zurücknehmen fehlgeschlagen')
    }
  }

  return (
    <div className="panel max-w-5xl">
      <div className="panel-head">Export-Historie</div>
      <div className="panel-body">
        {error && <p className="text-brand-red mb-2">{error}</p>}
        {exports.length === 0 ? (
          <p className="text-cats-muted">Noch keine Exporte erzeugt.</p>
        ) : (
          <table className="table-cats">
            <thead>
              <tr>
                <th className="w-16">#</th>
                <th>Datei</th>
                <th className="w-48">Zeitraum</th>
                <th className="w-20 text-right">Zeilen</th>
                <th className="w-24 text-right">Stunden</th>
                <th className="w-44">erzeugt</th>
                <th className="w-40"></th>
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
                  <td className="font-mono">
                    {new Date(e.created_at).toLocaleString('de-DE')}
                  </td>
                  <td className="flex gap-1">
                    <button className="btn flex-1"
                            onClick={() => api.download(e.download_url, e.filename)}>
                      Download
                    </button>
                    <button className="btn" title="Export zurücknehmen"
                            onClick={() => revoke(e.id)}>↺</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
