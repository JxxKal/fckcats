import { useState } from 'react'
import { api, ApiError, fmtDate, fmtHours } from '../api'
import type { ParsedDay, UploadResult } from '../types'

interface Decision {
  action: 'book' | 'exclude'
  hours: string
  remember: boolean
}

interface Props {
  onImported: () => void
}

export default function ImportPage({ onImported }: Props) {
  const [result, setResult] = useState<UploadResult | null>(null)
  const [decisions, setDecisions] = useState<Record<string, Decision>>({})
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState<string>('')

  async function upload(file: File) {
    setError(''); setDone(''); setBusy(true)
    try {
      const res = await api.upload<UploadResult>('/api/imports', file)
      setResult(res)
      const init: Record<string, Decision> = {}
      for (const c of res.clarifications) {
        if (c.work_date) {
          init[c.work_date] = {
            action: 'book',
            // Komma wie im Rest der Oberflaeche; beim Senden zurueckgewandelt.
            hours: fmtHours(c.hours_net ?? c.hours_target ?? null),
            remember: Boolean(c.reason),
          }
        }
      }
      setDecisions(init)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Upload fehlgeschlagen')
    } finally {
      setBusy(false)
    }
  }

  /** Ohne Speicherung: Verteilung und XLSX in einem Zug, nichts bleibt liegen. */
  async function exportDirect() {
    if (!result) return
    setError(''); setBusy(true)
    try {
      const days = result.days
        .filter(d => d.work_date && d.bookable)
        .map(d => ({ work_date: d.work_date!, hours: d.hours_net! }))
      for (const [work_date, d] of Object.entries(decisions)) {
        if (d.action !== 'book') continue
        const day = result.clarifications.find(c => c.work_date === work_date)
        const hours = d.hours ? Number(d.hours.replace(',', '.')) : day?.hours_net
        if (hours) days.push({ work_date, hours })
      }
      if (!days.length) {
        setError('Es gibt keine buchbaren Tage.')
        return
      }
      const res = await api.postDownload('/api/exports/direct', { days }, 'CATS.xlsx')
      setDone(`${res.rows} Zeilen mit ${res.hours.toFixed(2).replace('.', ',')} h ` +
              `erzeugt. Es wurde nichts gespeichert.`)
      setResult(null)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Export fehlgeschlagen')
    } finally {
      setBusy(false)
    }
  }

  async function commit(confirmOverwrite = false) {
    if (!result) return
    setError(''); setBusy(true)
    try {
      const payload = {
        clarifications: Object.entries(decisions).map(([work_date, d]) => ({
          work_date,
          action: d.action,
          hours: d.action === 'book' && d.hours ? Number(d.hours.replace(',', '.')) : null,
          remember_reason: d.remember
            ? result.clarifications.find(c => c.work_date === work_date)?.reason || null
            : null,
        })),
        confirm_overwrite_exported: confirmOverwrite,
      }
      const res = await api.post<{ imported_days: number; recalculation: { rows: number } }>(
        `/api/imports/${result.upload_id}/commit`, payload)
      setDone(`${res.imported_days} Tage übernommen, ${res.recalculation.rows} Zeilen erzeugt.`)
      setResult(null)
      onImported()
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        const detail = err.detail as { exported_dates?: string[]; message?: string }
        if (detail?.exported_dates?.length) {
          const ok = window.confirm(
            `Diese Tage wurden bereits exportiert:\n\n` +
            detail.exported_dates.map(fmtDate).join(', ') +
            `\n\nSie werden neu berechnet, die bisherige Fassung bleibt in der Historie. Fortfahren?`)
          if (ok) { setBusy(false); return commit(true) }
        } else {
          setError(detail?.message ?? err.message)
        }
      } else {
        setError(err instanceof ApiError ? err.message : 'Übernahme fehlgeschlagen')
      }
    } finally {
      setBusy(false)
    }
  }

  function setDecision(date: string, patch: Partial<Decision>) {
    setDecisions(prev => ({ ...prev, [date]: { ...prev[date], ...patch } }))
  }

  function statusOf(d: ParsedDay) {
    if (d.bookable) return { text: 'wird gebucht', cls: 'text-brand-dark' }
    if (d.incomplete) return { text: 'Buchung unvollständig', cls: 'text-brand-red font-bold' }
    if (d.unknown_reason) return { text: 'Grund unbekannt', cls: 'text-brand-red font-bold' }
    return { text: d.reason || 'ohne Buchung', cls: 'text-cats-muted' }
  }

  return (
    <div className="space-y-4 max-w-5xl">
      <div className="panel">
        <div className="panel-head">Zeitnachweis hochladen</div>
        <div className="panel-body space-y-2">
          <input type="file" accept="application/pdf" className="field"
                 onChange={e => { const f = e.target.files?.[0]; if (f) void upload(f) }} />
          <p className="text-cats-muted">
            Gebucht wird die Spalte <span className="font-mono">Prod.</span> — die
            Nettoarbeitszeit ohne Pause.
          </p>
          {busy && <p className="text-cats-muted">Wird verarbeitet …</p>}
          {error && <p className="text-brand-red">{error}</p>}
          {done && <p className="text-brand-dark font-bold">{done}</p>}
        </div>
      </div>

      {result && (
        <>
          <div className="panel">
            <div className="panel-head">
              Erkannt: {result.employee_name} · Personalnummer {result.personnel_number} ·{' '}
              {String(result.month).padStart(2, '0')}/{result.year}
            </div>
            <div className="panel-body flex flex-wrap gap-6">
              <div>
                <div className="label">Buchbare Tage</div>
                <div className="num text-lg">{result.bookable_count}</div>
              </div>
              <div>
                <div className="label">Summe Prod.</div>
                <div className="num text-lg">{fmtHours(result.bookable_hours)} h</div>
              </div>
              <div>
                <div className="label">Klärfälle</div>
                <div className={`num text-lg ${result.clarifications.length ? 'text-brand-red' : ''}`}>
                  {result.clarifications.length}
                </div>
              </div>
            </div>
          </div>

          {result.warnings.length > 0 && (
            <div className="panel">
              <div className="panel-head bg-brand-yellow text-cats-text">Hinweise</div>
              <ul className="panel-body list-disc pl-5">
                {result.warnings.map((w, i) => <li key={i}>{w}</li>)}
              </ul>
            </div>
          )}

          {result.clarifications.length > 0 && (
            <div className="panel border-brand-red">
              <div className="panel-head bg-brand-red text-white">
                Klärfälle — bitte entscheiden
              </div>
              <div className="panel-body">
                <table className="table-cats">
                  <thead>
                    <tr>
                      <th>Tag</th><th>Grund / Problem</th><th className="w-40">Behandlung</th>
                      <th className="w-28">Stunden</th><th className="w-40">merken</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.clarifications.map(c => {
                      const key = c.work_date!
                      const d = decisions[key] ?? { action: 'book', hours: '', remember: false }
                      return (
                        <tr key={key}>
                          <td className="font-mono whitespace-nowrap">
                            {fmtDate(key)} {c.weekday}
                          </td>
                          <td>
                            {c.unknown_reason
                              ? <>unbekannter Grundtext <span className="font-mono">{c.reason}</span></>
                              : <>Kommen oder Gehen fehlt
                                  {c.time_from && <> (von {c.time_from})</>}
                                  {c.hours_target ? <> · Sollzeit {fmtHours(c.hours_target)} h</> : null}
                                </>}
                          </td>
                          <td>
                            <select className="field w-full" value={d.action}
                                    onChange={e => setDecision(key, { action: e.target.value as 'book' | 'exclude' })}>
                              <option value="book">buchen</option>
                              <option value="exclude">nicht buchen</option>
                            </select>
                          </td>
                          <td>
                            <input className="field w-full text-right" value={d.hours}
                                   disabled={d.action === 'exclude'}
                                   placeholder={c.hours_target ? fmtHours(c.hours_target) : ''}
                                   onChange={e => setDecision(key, { hours: e.target.value })} />
                          </td>
                          <td>
                            {c.reason && (
                              <label className="flex items-center gap-2">
                                <input type="checkbox" checked={d.remember}
                                       onChange={e => setDecision(key, { remember: e.target.checked })} />
                                <span className="text-cats-muted">künftig automatisch</span>
                              </label>
                            )}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          <div className="panel">
            <div className="panel-head">Alle Tage</div>
            <div className="panel-body overflow-x-auto">
              <table className="table-cats">
                <thead>
                  <tr>
                    <th>Tag</th><th>von</th><th>bis</th>
                    <th className="text-right">Std.</th>
                    <th className="text-right">Prod.</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {result.days.map(d => {
                    const s = statusOf(d)
                    return (
                      <tr key={d.day}>
                        <td className="font-mono whitespace-nowrap">
                          {String(d.day).padStart(2, '0')} {d.weekday}
                        </td>
                        <td className="font-mono">{d.time_from ?? ''}</td>
                        <td className="font-mono">{d.time_to ?? ''}</td>
                        <td className="num text-cats-muted">{fmtHours(d.hours_gross)}</td>
                        <td className="num font-bold">{d.bookable ? fmtHours(d.hours_net) : ''}</td>
                        <td className={s.cls}>{s.text}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </div>

          {result.storage_mode === 'ephemeral' ? (
            <div className="space-y-2">
              <button className="btn-primary" disabled={busy} onClick={() => void exportDirect()}>
                Verteilen und als XLSX herunterladen
              </button>
              <p className="text-cats-muted">
                Für diesen Workspace ist die Speicherung abgeschaltet: Die Datei wird
                erzeugt und ausgeliefert, danach bleibt nichts zurück. Notiere dir
                selbst, welchen Zeitraum du bereits nach CATS gebucht hast.
              </p>
            </div>
          ) : (
            <button className="btn-primary" disabled={busy} onClick={() => void commit()}>
              Import übernehmen
            </button>
          )}
        </>
      )}
    </div>
  )
}
