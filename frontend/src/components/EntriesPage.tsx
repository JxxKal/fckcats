import { useEffect, useState } from 'react'
import { api, ApiError, fmtDate, fmtHours, weekdayOf } from '../api'
import type { CatsConfig, EntriesResponse, WeekGroup } from '../types'

const FULL_WEEK_DAYS = 5

export default function EntriesPage() {
  const [data, setData] = useState<EntriesResponse | null>(null)
  const [cfg, setCfg] = useState<CatsConfig | null>(null)
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  useEffect(() => { void load() }, [])

  async function load() {
    const [entries, config] = await Promise.all([
      api.get<EntriesResponse>('/api/entries'),
      api.get<CatsConfig>('/api/cats-config'),
    ])
    setData(entries)
    setCfg(config)
    const dates = entries.weeks.flatMap(w => w.rows.filter(r => !r.exported).map(r => r.work_date))
    if (dates.length) {
      setFrom(prev => prev || dates.reduce((a, b) => (a < b ? a : b)))
      setTo(prev => prev || dates.reduce((a, b) => (a > b ? a : b)))
    }
  }

  async function exportRange() {
    setError(''); setBusy(true)
    try {
      const res = await api.post<{ export_id: number; filename: string; row_count: number; download_url: string }>(
        `/api/exports?date_from=${from}&date_to=${to}`)
      await api.download(res.download_url, res.filename)
      await load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Export fehlgeschlagen')
    } finally {
      setBusy(false)
    }
  }

  async function recalc() {
    if (!window.confirm('Die offenen Zeilen werden neu verteilt. Bereits exportierte Zeilen bleiben unverändert. Fortfahren?')) return
    setError(''); setBusy(true)
    try {
      await api.post('/api/entries/recalculate')
      await load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Neuberechnung fehlgeschlagen')
    } finally {
      setBusy(false)
    }
  }

  function toggle(key: string) {
    setExpanded(prev => {
      const next = new Set(prev)
      next.has(key) ? next.delete(key) : next.add(key)
      return next
    })
  }

  function weightFor(wbs: string): number | null {
    const el = cfg?.wbs_elements.find(e => e.wbs === wbs)
    return el ? el.weight : null
  }

  function projectCapFor(wbs: string): number | null {
    const p = cfg?.projects?.find(e => e.wbs === wbs)
    return p ? p.max_hours_per_week : null
  }

  /** Stunden, die in dieser Woche an Projekte gingen. */
  function projectHours(week: WeekGroup): number {
    return Object.entries(week.per_wbs)
      .filter(([wbs]) => projectCapFor(wbs) !== null)
      .reduce((s, [, h]) => s + h, 0)
  }

  /**
   * Bezugsgröße für die Gewichtung ist der Rest nach den Projekten, nicht die
   * ganze Woche — sonst sähe jedes gewichtete Element zu klein aus.
   */
  function opsBase(week: WeekGroup): number {
    return Math.max(0, week.hours - projectHours(week))
  }

  function deviation(week: WeekGroup, wbs: string): number | null {
    const target = weightFor(wbs)
    const base = opsBase(week)
    if (target === null || base <= 0) return null
    return (week.per_wbs[wbs] ?? 0) / base * 100 - target
  }

  if (!data) return <p className="text-cats-muted">Wird geladen …</p>

  if (!data.weeks.length) {
    return (
      <div className="panel max-w-2xl">
        <div className="panel-head">Zieltabelle</div>
        <div className="panel-body text-cats-muted">
          Noch keine Zeilen vorhanden. Lade zuerst einen Zeitnachweis hoch.
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <div className="panel">
        <div className="panel-head">Export</div>
        <div className="panel-body flex flex-wrap items-end gap-3">
          <div>
            <label className="label block mb-1">von</label>
            <input type="date" className="field" value={from} onChange={e => setFrom(e.target.value)} />
          </div>
          <div>
            <label className="label block mb-1">bis</label>
            <input type="date" className="field" value={to} onChange={e => setTo(e.target.value)} />
          </div>
          <button className="btn-primary" disabled={busy || !from || !to} onClick={exportRange}>
            Als XLSX exportieren
          </button>
          <button className="btn" disabled={busy} onClick={recalc}>Offene Zeilen neu verteilen</button>
          <div className="ml-auto flex gap-6">
            <div>
              <div className="label">offen</div>
              <div className="num text-lg">{fmtHours(data.open_hours)} h</div>
            </div>
            <div>
              <div className="label">gebucht</div>
              <div className="num text-lg">{fmtHours(data.total_hours)} h</div>
            </div>
            {data.unbooked_hours > 0 && (
              <div>
                <div className="label">nicht gebucht</div>
                <div className="num text-lg text-cats-muted">
                  {fmtHours(data.unbooked_hours)} h
                </div>
              </div>
            )}
            <div>
              <div className="label">erfasst</div>
              <div className="num text-lg text-cats-muted">
                {fmtHours(data.recorded_hours)} h
              </div>
            </div>
          </div>
        </div>
        {error && <div className="panel-body pt-0 text-brand-red">{error}</div>}
      </div>

      {data.weeks.map(week => {
        const key = `${week.iso_year}-${week.iso_week}`
        const open = expanded.has(key)
        return (
          <div className="panel" key={key}>
            <button className="panel-head w-full text-left flex items-center gap-3"
                    onClick={() => toggle(key)}>
              <span>{open ? '▾' : '▸'}</span>
              <span>KW {week.iso_week} / {week.iso_year}</span>
              <span className="font-normal text-cats-muted">
                {week.days} {week.days === 1 ? 'Tag' : 'Tage'} · {fmtHours(week.hours)} h
                {week.unbooked_hours > 0 &&
                  ` von ${fmtHours(week.recorded_hours)} h erfasst`}
              </span>
              {week.exported_rows > 0 && (
                <span className="font-normal text-cats-muted">
                  · {week.exported_rows} exportiert
                </span>
              )}
              {week.open_rows === 0 && (
                <span className="ml-auto font-normal text-cats-muted">vollständig exportiert</span>
              )}
            </button>

            <div className="panel-body space-y-3">
              <table className="table-cats">
                <thead>
                  <tr>
                    <th>WBS-Element</th>
                    <th className="w-24">Art</th>
                    <th className="w-24 text-right">Stunden</th>
                    <th className="w-20 text-right">ist</th>
                    <th className="w-28 text-right">soll</th>
                    <th className="w-24 text-right">Abweichung</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(week.per_wbs).sort().map(([wbs, hours]) => {
                    const cap = projectCapFor(wbs)
                    const isProject = cap !== null
                    const target = weightFor(wbs)
                    const dev = deviation(week, wbs)
                    const base = opsBase(week)
                    // Projekte werden anteilig zur Wochenlänge gekürzt.
                    const capThisWeek = cap !== null
                      ? cap * Math.min(1, week.days / FULL_WEEK_DAYS) : null
                    return (
                      <tr key={wbs}>
                        <td className="font-mono">{wbs}</td>
                        <td className={isProject ? 'text-brand-dark font-bold' : 'text-cats-muted'}>
                          {isProject ? 'Projekt' : 'gewichtet'}
                        </td>
                        <td className="num">{fmtHours(hours)}</td>
                        <td className="num">
                          {isProject
                            ? `${(hours / week.hours * 100).toFixed(1).replace('.', ',')} %`
                            : base > 0
                              ? `${(hours / base * 100).toFixed(1).replace('.', ',')} %`
                              : '—'}
                        </td>
                        <td className="num text-cats-muted">
                          {isProject
                            ? `max ${fmtHours(capThisWeek)} h`
                            : target !== null
                              ? `${target.toFixed(1).replace('.', ',')} %`
                              : '—'}
                        </td>
                        <td className={`num ${dev !== null && Math.abs(dev) > 10 ? 'text-brand-red' : 'text-cats-muted'}`}>
                          {dev !== null ? `${dev > 0 ? '+' : ''}${dev.toFixed(1).replace('.', ',')} pp` : '—'}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
              {(projectHours(week) > 0 || week.unbooked_hours > 0) && (
                <p className="text-cats-muted">
                  {week.unbooked_hours > 0 && (
                    <>
                      {fmtHours(week.unbooked_hours)} h der erfassten{' '}
                      {fmtHours(week.recorded_hours)} h werden keinem WBS-Element
                      zugeordnet und nicht exportiert.{' '}
                    </>
                  )}
                  {projectHours(week) > 0 && (
                    <>
                      Von den gebuchten {fmtHours(week.hours)} h gehen{' '}
                      {fmtHours(projectHours(week))} h auf Projekte und{' '}
                      {fmtHours(opsBase(week))} h in die gewichtete Verteilung.
                    </>
                  )}
                </p>
              )}

              {open && (
                <table className="table-cats">
                  <thead>
                    <tr>
                      <th className="w-40">Tag</th>
                      <th>WBS-Element</th>
                      <th className="w-24 text-right">Stunden</th>
                      <th className="w-28 text-right">Tagessumme</th>
                      <th className="w-28">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {week.rows.map((r, i) => (
                      <tr key={i}>
                        <td className="font-mono whitespace-nowrap">
                          {fmtDate(r.work_date)} {weekdayOf(r.work_date)}
                        </td>
                        <td className="font-mono">{r.wbs_element}</td>
                        <td className="num">{fmtHours(r.hours)}</td>
                        <td className="num text-cats-muted">{fmtHours(r.day_hours)}</td>
                        <td className={r.exported ? 'text-cats-muted' : 'text-brand-dark font-bold'}>
                          {r.exported ? `exportiert (#${r.export_id})` : 'offen'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}
