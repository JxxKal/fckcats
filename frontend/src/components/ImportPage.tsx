import { useMemo, useState } from 'react'
import { api, ApiError, fmtDate, fmtHours, weekdayOf } from '../api'
import type { UploadResult } from '../types'

/** CATS nimmt keine Buchungen unter einer halben Stunde an. */
const MIN_BOOKABLE = 0.5

/** Zustand einer Zeile in der bearbeitbaren Tagesliste. */
interface Row {
  work_date: string
  weekday: string
  /** Anzeigewerte aus dem PDF, leer bei ergänzten Zeilen. */
  time_from: string | null
  time_to: string | null
  hours_gross: number | null
  reason: string
  /** Grund, warum der Tag nicht automatisch gebucht wird. */
  problem: 'unknown_reason' | 'incomplete' | 'excluded' | null
  /** Wird der Tag gebucht? */
  book: boolean
  /** Eingegebene Stunden als Text, damit ein Komma erlaubt ist. */
  hours: string
  /** Vom PDF vorgeschlagene Stunden. */
  suggested: number | null
  /** Vom Benutzer angefasst oder ergänzt. */
  touched: boolean
  added: boolean
  remember: boolean
}

interface Props {
  onImported: () => void
}

const parseHours = (value: string): number | null => {
  const n = Number(value.replace(',', '.'))
  return Number.isFinite(n) && n > 0 ? Math.round(n * 100) / 100 : null
}

function toRows(result: UploadResult): Row[] {
  return result.days
    .filter(d => d.work_date)
    .map(d => ({
      work_date: d.work_date!,
      weekday: d.weekday,
      time_from: d.time_from,
      time_to: d.time_to,
      hours_gross: d.hours_gross,
      reason: d.reason,
      problem: d.unknown_reason ? 'unknown_reason'
             : d.incomplete ? 'incomplete'
             : d.bookable ? null : 'excluded',
      book: d.bookable,
      hours: fmtHours(d.hours_net || d.hours_target || null),
      suggested: d.hours_net,
      touched: false,
      added: false,
      remember: Boolean(d.reason) && d.unknown_reason,
    }))
}

export default function ImportPage({ onImported }: Props) {
  const [result, setResult] = useState<UploadResult | null>(null)
  const [rows, setRows] = useState<Row[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState('')

  const summe = useMemo(
    () => rows.filter(r => r.book)
              .reduce((s, r) => s + (parseHours(r.hours) ?? 0), 0),
    [rows])
  // Ein Klaerfall bleibt offen, bis der Benutzer ihn anfasst -- auch wenn die
  // Voreinstellung "nicht buchen" lautet. Sonst fiele ein Tag mit vergessener
  // Buchung stillschweigend unter den Tisch.
  const offen = rows.filter(
    r => (r.problem === 'unknown_reason' || r.problem === 'incomplete') && !r.touched)
  const zuKlein = rows.filter(r => {
    if (!r.book) return false
    const h = parseHours(r.hours)
    return h !== null && h < MIN_BOOKABLE
  })
  const ohneStunden = rows.filter(r => r.book && parseHours(r.hours) === null)

  function patch(index: number, änderung: Partial<Row>) {
    setRows(prev => prev.map((r, i) => (i === index ? { ...r, ...änderung, touched: true } : r)))
  }

  async function upload(file: File) {
    setError(''); setDone(''); setBusy(true)
    try {
      const res = await api.upload<UploadResult>('/api/imports', file)
      setResult(res)
      setRows(toRows(res))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Upload fehlgeschlagen')
    } finally {
      setBusy(false)
    }
  }

  function addRow() {
    const letzter = rows.length ? rows[rows.length - 1].work_date : null
    const naechster = letzter
      ? new Date(new Date(letzter + 'T00:00:00').getTime() + 86400000)
          .toISOString().slice(0, 10)
      : new Date().toISOString().slice(0, 10)
    setRows([...rows, {
      work_date: naechster, weekday: '', time_from: null, time_to: null,
      hours_gross: null, reason: '', problem: null, book: true,
      hours: '', suggested: null, touched: true, added: true, remember: false,
    }])
  }

  /** Alle Zeilen, die vom automatischen Vorschlag abweichen oder bestätigt wurden. */
  function buildAdjustments() {
    return rows
      .filter(r => r.touched || r.added
                || r.problem === 'unknown_reason' || r.problem === 'incomplete')
      .map(r => ({
        work_date: r.work_date,
        action: r.book ? 'book' : 'exclude',
        hours: r.book ? parseHours(r.hours) : null,
        remember_reason: r.remember && r.reason ? r.reason : null,
      }))
  }

  async function commit(confirmOverwrite = false) {
    if (!result) return
    setError(''); setBusy(true)
    try {
      const res = await api.post<{ imported_days: number; recalculation: { rows: number } }>(
        `/api/imports/${result.upload_id}/commit`,
        { adjustments: buildAdjustments(), confirm_overwrite_exported: confirmOverwrite })
      setDone(`${res.imported_days} Tage übernommen, ${res.recalculation.rows} Zeilen erzeugt.`)
      setResult(null); setRows([])
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

  /** Verteilen und herunterladen, ohne etwas zu speichern. */
  async function exportDirect() {
    setError(''); setBusy(true)
    try {
      const days = rows
        .filter(r => r.book && parseHours(r.hours) !== null)
        .map(r => ({ work_date: r.work_date, hours: parseHours(r.hours)! }))
      if (!days.length) { setError('Es gibt keine buchbaren Tage.'); return }
      const res = await api.postDownload('/api/exports/direct', { days }, 'CATS.xlsx')
      setDone(`${res.rows} Zeilen mit ${fmtHours(res.hours)} h erzeugt. ` +
              `Es wurde nichts gespeichert.`)
      setResult(null); setRows([])
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Export fehlgeschlagen')
    } finally {
      setBusy(false)
    }
  }

  function problemText(r: Row): string {
    if (r.added) return 'von Hand ergänzt'
    switch (r.problem) {
      case 'unknown_reason': return `unbekannter Grund: ${r.reason}`
      case 'incomplete':     return 'Kommen oder Gehen fehlt'
      case 'excluded':       return r.reason || 'ohne Buchung'
      default:               return r.reason || ''
    }
  }

  const blockiert = busy || zuKlein.length > 0 || ohneStunden.length > 0 || offen.length > 0

  return (
    <div className="space-y-4 max-w-5xl">
      <div className="panel">
        <div className="panel-head">Zeitnachweis hochladen</div>
        <div className="panel-body space-y-2">
          <input type="file" accept="application/pdf" className="field"
                 onChange={e => { const f = e.target.files?.[0]; if (f) void upload(f) }} />
          <p className="text-cats-muted">
            Gebucht wird die Spalte <span className="font-mono">Prod.</span> — die
            Nettoarbeitszeit ohne Pause. Ist in der CATS-Config nicht gebuchte Zeit
            eingestellt, wird sie davon noch abgezogen.
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
                <div className="label">Tage, die gebucht werden</div>
                <div className="num text-lg">{rows.filter(r => r.book).length}</div>
              </div>
              <div>
                <div className="label">Summe</div>
                <div className="num text-lg">{fmtHours(Math.round(summe * 100) / 100)} h</div>
              </div>
              <div>
                <div className="label">noch zu entscheiden</div>
                <div className={`num text-lg ${offen.length ? 'text-brand-red' : ''}`}>
                  {offen.length}
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

          <div className="panel">
            <div className="panel-head">
              Tage prüfen und anpassen
            </div>
            <div className="panel-body space-y-3">
              <p className="text-cats-muted">
                Jede Zeile ist bearbeitbar: Stunden ändern, Tage ab- oder zuwählen,
                fehlende Tage ergänzen. Zeilen mit rotem Hinweis brauchen eine
                Entscheidung, bevor der Import möglich ist.
              </p>

              <div className="overflow-x-auto">
                <table className="table-cats">
                  <thead>
                    <tr>
                      <th className="w-16">buchen</th>
                      <th className="w-40">Tag</th>
                      <th className="w-20">von</th>
                      <th className="w-20">bis</th>
                      <th className="w-20 text-right">Std.</th>
                      <th className="w-28 text-right">Stunden</th>
                      <th>Hinweis</th>
                      <th className="w-32">merken</th>
                      <th className="w-10"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r, i) => {
                      const h = parseHours(r.hours)
                      const fehlt = r.book && h === null
                      const klein = r.book && h !== null && h < MIN_BOOKABLE
                      const zuKlaeren =
                        (r.problem === 'unknown_reason' || r.problem === 'incomplete')
                        && !r.touched
                      return (
                        <tr key={`${r.work_date}-${i}`}
                            className={r.book ? '' : 'opacity-60'}>
                          <td className="text-center">
                            <input type="checkbox" checked={r.book}
                                   onChange={e => patch(i, { book: e.target.checked })} />
                          </td>
                          <td>
                            {r.added ? (
                              <input type="date" className="field w-full" value={r.work_date}
                                     onChange={e => patch(i, { work_date: e.target.value })} />
                            ) : (
                              <span className="font-mono whitespace-nowrap">
                                {fmtDate(r.work_date)} {r.weekday || weekdayOf(r.work_date)}
                              </span>
                            )}
                          </td>
                          <td className="font-mono text-cats-muted">{r.time_from ?? ''}</td>
                          <td className="font-mono text-cats-muted">{r.time_to ?? ''}</td>
                          <td className="num text-cats-muted">{fmtHours(r.hours_gross)}</td>
                          <td>
                            <input className={`field w-full text-right
                                    ${fehlt || klein ? 'border-brand-red' : ''}`}
                                   value={r.hours} disabled={!r.book}
                                   placeholder={r.suggested ? fmtHours(r.suggested) : ''}
                                   onChange={e => patch(i, { hours: e.target.value })} />
                          </td>
                          <td className={
                            zuKlaeren || fehlt || klein ? 'text-brand-red font-bold'
                            : r.added || r.touched ? 'text-brand-dark'
                            : 'text-cats-muted'}>
                            {klein ? `unter ${fmtHours(MIN_BOOKABLE)} h — CATS lehnt das ab`
                              : fehlt ? 'Stundenzahl fehlt'
                              : r.touched && !r.added ? 'angepasst'
                              : problemText(r)}
                            {zuKlaeren && (
                              <button className="btn ml-2 py-0" title="Vorschlag übernehmen"
                                      onClick={() => patch(i, {})}>
                                so lassen
                              </button>
                            )}
                          </td>
                          <td>
                            {r.reason && r.problem === 'unknown_reason' && (
                              <label className="flex items-center gap-2">
                                <input type="checkbox" checked={r.remember}
                                       onChange={e => patch(i, { remember: e.target.checked })} />
                                <span className="text-cats-muted">künftig so</span>
                              </label>
                            )}
                          </td>
                          <td>
                            {r.added && (
                              <button className="btn w-full" title="Zeile entfernen"
                                      onClick={() => setRows(rows.filter((_, j) => j !== i))}>
                                ×
                              </button>
                            )}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>

              <button className="btn" onClick={addRow}>Tag ergänzen</button>

              {offen.length > 0 && (
                <p className="text-brand-red">
                  {offen.length} {offen.length === 1 ? 'Tag braucht' : 'Tage brauchen'} eine
                  Entscheidung: buchen und Stunden eintragen, oder mit
                  <em> so lassen</em> beim Vorschlag bleiben.
                </p>
              )}
              {zuKlein.length > 0 && (
                <p className="text-brand-red">
                  {zuKlein.length} {zuKlein.length === 1 ? 'Zeile liegt' : 'Zeilen liegen'} unter
                  der Mindestbuchung von {fmtHours(MIN_BOOKABLE)} h. CATS nimmt solche
                  Zeiten nicht an.
                </p>
              )}
              {ohneStunden.length > 0 && (
                <p className="text-brand-red">
                  {ohneStunden.length} gebuchten Tagen fehlt die Stundenzahl.
                </p>
              )}
            </div>
          </div>

          {result.storage_mode === 'ephemeral' ? (
            <div className="space-y-2">
              <button className="btn-primary" disabled={blockiert}
                      onClick={() => void exportDirect()}>
                Verteilen und als XLSX herunterladen
              </button>
              <p className="text-cats-muted">
                Für diesen Workspace ist die Speicherung abgeschaltet: Die Datei wird
                erzeugt und ausgeliefert, danach bleibt nichts zurück. Notiere dir
                selbst, welchen Zeitraum du bereits nach CATS gebucht hast.
              </p>
            </div>
          ) : (
            <button className="btn-primary" disabled={blockiert} onClick={() => void commit()}>
              Import übernehmen
            </button>
          )}
        </>
      )}
    </div>
  )
}
