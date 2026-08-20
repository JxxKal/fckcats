import { useEffect, useState } from 'react'
import { api, ApiError, fmtHours } from '../api'
import type { CatsConfig, ProjectElement, WbsElement } from '../types'

const FULL_WEEK_DAYS = 5

export default function ConfigPage() {
  const [cfg, setCfg] = useState<CatsConfig | null>(null)
  const [personnel, setPersonnel] = useState('')
  const [elements, setElements] = useState<WbsElement[]>([])
  const [projects, setProjects] = useState<ProjectElement[]>([])
  const [priority, setPriority] = useState<'projects' | 'operations'>('projects')
  const [opsMinPct, setOpsMinPct] = useState(0)
  const [unbooked, setUnbooked] = useState(0)
  const [rules, setRules] = useState<Record<string, 'book' | 'exclude'>>({})
  const [warnings, setWarnings] = useState<string[]>([])
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)
  const [busy, setBusy] = useState(false)

  useEffect(() => { void load() }, [])

  async function load() {
    const c = await api.get<CatsConfig>('/api/cats-config')
    setCfg(c)
    setPersonnel(c.personnel_number)
    setElements(c.wbs_elements)
    setProjects(c.projects ?? [])
    setPriority(c.priority ?? 'projects')
    setOpsMinPct(c.operations_min_pct ?? 0)
    setUnbooked(c.unbooked_hours_per_week ?? 0)
    setRules(c.reason_rules)
  }

  const weekHours = cfg?.typical_week_hours ?? 38
  const total = elements.reduce((s, e) => s + (Number(e.weight) || 0), 0)
  const totalOk = elements.length === 0 || Math.abs(total - 100) < 0.01
  const projectHours = projects.reduce((s, p) => s + (Number(p.max_hours_per_week) || 0), 0)

  // Was nach Abzug der ungebuchten Zeit überhaupt zu verteilen ist.
  const distributable = Math.max(0, weekHours - (Number(unbooked) || 0))

  // Was den gewichteten Elementen in einer vollen Woche bleibt.
  const projectCap = priority === 'operations' && opsMinPct > 0
    ? distributable * (1 - opsMinPct / 100)
    : distributable
  const projectsEffective = Math.min(projectHours, projectCap)
  const opsHours = Math.max(0, distributable - projectsEffective)
  const overbooked = projectHours > projectCap + 0.001

  function touch() { setSaved(false) }

  function distributeEvenly() {
    const n = elements.length
    if (!n) return
    const each = Math.floor((100 / n) * 10) / 10
    setElements(elements.map((e, i) =>
      ({ ...e, weight: i === 0 ? Math.round((100 - each * (n - 1)) * 10) / 10 : each })))
    touch()
  }

  async function save() {
    setError('')
    setBusy(true)
    try {
      const res = await api.put<{ version: number; warnings: string[] }>('/api/cats-config', {
        personnel_number: personnel,
        wbs_elements: elements.filter(e => e.wbs.trim()),
        projects: projects.filter(p => p.wbs.trim()),
        priority,
        operations_min_pct: opsMinPct,
        unbooked_hours_per_week: unbooked,
        reason_rules: rules,
      })
      setWarnings(res.warnings)
      setSaved(true)
      await load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Speichern fehlgeschlagen')
    } finally {
      setBusy(false)
    }
  }

  if (!cfg) return <p className="text-cats-muted">Wird geladen …</p>

  return (
    <div className="space-y-4 max-w-4xl">
      <div className="panel">
        <div className="panel-head">Stammdaten</div>
        <div className="panel-body">
          <label className="label block mb-1" htmlFor="pn">Personalnummer</label>
          <input id="pn" className="field-key w-48" value={personnel}
                 onChange={e => { setPersonnel(e.target.value); touch() }} />
          <p className="text-cats-muted mt-1">
            Wird beim Import gegen den Kopf des Zeitnachweises geprüft.
          </p>
        </div>
      </div>

      <div className="panel">
        <div className="panel-head">Nicht gebuchte Zeit</div>
        <div className="panel-body space-y-2">
          <p className="text-cats-muted">
            Stunden je Woche, die keinem WBS-Element zugeordnet und nicht nach CATS
            exportiert werden — etwa Verwaltungstätigkeit. Sie werden vor allem
            anderen abgezogen; erst der Rest geht an Projekte und die gewichtete
            Verteilung.
          </p>
          <div className="flex items-center gap-2">
            <input type="number" step="0.5" min="0" max={weekHours}
                   className="field-key w-24 text-right" value={unbooked}
                   onChange={e => { setUnbooked(Number(e.target.value)); touch() }} />
            <span>h / Woche</span>
            <span className="text-cats-muted">
              bei 2 von 5 Tagen {fmtHours((Number(unbooked) || 0) * 2 / FULL_WEEK_DAYS)} h
            </span>
          </div>
          {unbooked > 0 && (
            <p className={distributable < 1 ? 'text-brand-red' : 'text-cats-muted'}>
              In einer Woche mit {weekHours} h gehen damit{' '}
              <strong>{fmtHours(distributable)} h</strong> nach CATS, {fmtHours(unbooked)} h
              bleiben ungebucht. Die exportierte Stundensumme ist entsprechend kleiner
              als der Zeitnachweis.
            </p>
          )}
        </div>
      </div>

      {/* Projekte vor Operations — so werden sie auch verteilt. */}
      <div className="panel">
        <div className="panel-head">Projekte</div>
        <div className="panel-body space-y-3">
          <p className="text-cats-muted">
            WBS-Elemente mit einer festen Obergrenze in Stunden je Woche. Sie werden
            zuerst bedient; erst was übrig bleibt, geht in die gewichtete Verteilung.
            In kürzeren Wochen wird die Obergrenze anteilig gekürzt — bei zwei von
            fünf Arbeitstagen also auf 40 %. Verfügbar sind in einer vollen Woche{' '}
            {fmtHours(distributable)} h.
          </p>

          {projects.length > 0 && (
            <table className="table-cats">
              <thead>
                <tr>
                  <th>WBS-Element</th>
                  <th className="w-36">max. h / Woche</th>
                  <th className="w-32">bei 2 von 5 Tagen</th>
                  <th className="w-10"></th>
                </tr>
              </thead>
              <tbody>
                {projects.map((p, i) => (
                  <tr key={i}>
                    <td>
                      <input className="field w-full" value={p.wbs}
                             placeholder="DEO1111-NP/PJ00-O51.0000"
                             onChange={e => {
                               setProjects(projects.map((x, j) =>
                                 j === i ? { ...x, wbs: e.target.value } : x))
                               touch()
                             }} />
                    </td>
                    <td>
                      <input type="number" step="0.5" min="0" max="80"
                             className="field w-full text-right" value={p.max_hours_per_week}
                             onChange={e => {
                               setProjects(projects.map((x, j) =>
                                 j === i ? { ...x, max_hours_per_week: Number(e.target.value) } : x))
                               touch()
                             }} />
                    </td>
                    <td className="num text-cats-muted">
                      {fmtHours((Number(p.max_hours_per_week) || 0) * 2 / FULL_WEEK_DAYS)} h
                    </td>
                    <td>
                      <button className="btn w-full" title="Zeile entfernen"
                              onClick={() => { setProjects(projects.filter((_, j) => j !== i)); touch() }}>
                        ×
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr>
                  <td className="border border-cats-border px-2 py-1 font-bold">Summe</td>
                  <td className={`border border-cats-border px-2 py-1 num font-bold
                                  ${overbooked ? 'text-brand-red' : ''}`}>
                    {fmtHours(projectHours)} h
                  </td>
                  <td className="border border-cats-border" colSpan={2}></td>
                </tr>
              </tfoot>
            </table>
          )}

          <button className="btn"
                  onClick={() => { setProjects([...projects, { wbs: '', max_hours_per_week: 0 }]); touch() }}>
            Projekt hinzufügen
          </button>
        </div>
      </div>

      <div className="panel">
        <div className="panel-head">Operations oder andere gewichtet verteilte WBS-Elemente</div>
        <div className="panel-body space-y-3">
          <p className="text-cats-muted">
            Diese Elemente teilen unter sich auf, was nach der ungebuchten Zeit und
            den Projekten übrig bleibt — in einer vollen Woche also rund{' '}
            <strong>{fmtHours(opsHours)} h</strong>. Die Gewichtungen müssen zusammen
            100 % ergeben.
          </p>

          {elements.length > 0 && (
            <table className="table-cats">
              <thead>
                <tr>
                  <th>WBS-Element</th>
                  <th className="w-28">Gewichtung</th>
                  <th className="w-32">davon h / Woche</th>
                  <th className="w-10"></th>
                </tr>
              </thead>
              <tbody>
                {elements.map((e, i) => {
                  const hours = (opsHours * (Number(e.weight) || 0)) / 100
                  const tooSmall = e.wbs.trim() !== '' && hours > 0 && hours < 1
                  return (
                    <tr key={i}>
                      <td>
                        <input className="field w-full" value={e.wbs}
                               placeholder="DEO2222-NP/PJ00-O51.0000"
                               onChange={ev => {
                                 setElements(elements.map((x, j) =>
                                   j === i ? { ...x, wbs: ev.target.value } : x))
                                 touch()
                               }} />
                      </td>
                      <td>
                        <input type="number" step="0.1" min="0" max="100"
                               className="field w-full text-right" value={e.weight}
                               onChange={ev => {
                                 setElements(elements.map((x, j) =>
                                   j === i ? { ...x, weight: Number(ev.target.value) } : x))
                                 touch()
                               }} />
                      </td>
                      <td className={`num ${tooSmall ? 'text-brand-red' : 'text-cats-muted'}`}>
                        {fmtHours(hours)} h
                      </td>
                      <td>
                        <button className="btn w-full" title="Zeile entfernen"
                                onClick={() => { setElements(elements.filter((_, j) => j !== i)); touch() }}>
                          ×
                        </button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
              <tfoot>
                <tr>
                  <td className="border border-cats-border px-2 py-1 font-bold">Summe</td>
                  <td className={`border border-cats-border px-2 py-1 num font-bold
                                  ${totalOk ? '' : 'text-brand-red'}`}>
                    {total.toFixed(1).replace('.', ',')} %
                  </td>
                  <td className="border border-cats-border px-2 py-1 num text-cats-muted">
                    {fmtHours(opsHours)} h
                  </td>
                  <td className="border border-cats-border"></td>
                </tr>
              </tfoot>
            </table>
          )}

          <div className="flex flex-wrap gap-2">
            <button className="btn"
                    onClick={() => { setElements([...elements, { wbs: '', weight: 0 }]); touch() }}>
              Zeile hinzufügen
            </button>
            {elements.length > 0 && (
              <button className="btn" onClick={distributeEvenly}>Gleichmäßig verteilen</button>
            )}
          </div>

          {!totalOk && (
            <p className="text-brand-red">
              Die Summe muss exakt 100 % ergeben. Aktuell fehlen{' '}
              {(100 - total).toFixed(1).replace('.', ',')} Prozentpunkte.
            </p>
          )}
          {elements.length > 0 && opsHours < 1 && (
            <p className="text-brand-red">
              Die Projekte belegen die ganze Woche — für die gewichtete Verteilung
              bleibt nichts übrig.
            </p>
          )}
        </div>
      </div>

      {projects.length > 0 && elements.length > 0 && (
        <div className="panel">
          <div className="panel-head">Vorrang bei Knappheit</div>
          <div className="panel-body space-y-3">
            <p className="text-cats-muted">
              Greift nur, wenn die Projekt-Obergrenzen mehr ergeben, als die Woche
              hergibt. Sonst bekommen beide Seiten, was ihnen zusteht.
            </p>

            <label className="flex gap-3 items-start cursor-pointer">
              <input type="radio" name="prio" className="mt-1"
                     checked={priority === 'projects'}
                     onChange={() => { setPriority('projects'); touch() }} />
              <span>
                <strong>Projekte zuerst</strong>
                <span className="block text-cats-muted">
                  Die Projekte teilen die Woche im Verhältnis ihrer Obergrenzen unter
                  sich auf. Die gewichteten Elemente gehen in solchen Wochen leer aus.
                </span>
              </span>
            </label>

            <label className="flex gap-3 items-start cursor-pointer">
              <input type="radio" name="prio" className="mt-1"
                     checked={priority === 'operations'}
                     onChange={() => { setPriority('operations'); touch() }} />
              <span>
                <strong>Operations zuerst</strong>
                <span className="block text-cats-muted">
                  Den gewichteten Elementen bleibt ein Mindestanteil der Woche
                  erhalten; die Projekte werden entsprechend gekürzt.
                </span>
              </span>
            </label>

            {priority === 'operations' && (
              <div className="pl-8">
                <label className="label block mb-1">
                  Mindestanteil für Operations
                </label>
                <div className="flex items-center gap-2">
                  <input type="number" step="1" min="0" max="99"
                         className="field-key w-24 text-right" value={opsMinPct}
                         onChange={e => { setOpsMinPct(Number(e.target.value)); touch() }} />
                  <span>%</span>
                  <span className="text-cats-muted">
                    entspricht {fmtHours(weekHours * opsMinPct / 100)} h in einer
                    Woche mit {weekHours} h
                  </span>
                </div>
                {opsMinPct <= 0 && (
                  <p className="text-brand-red mt-1">
                    Ohne Mindestanteil wirkt der Vorrang nicht.
                  </p>
                )}
              </div>
            )}

            {overbooked && (
              <p className="border border-brand-yellow bg-cats-fieldkey p-2">
                Die Projekt-Obergrenzen ergeben {fmtHours(projectHours)} h, verfügbar
                sind {fmtHours(projectCap)} h. Die Projekte werden anteilig auf diesen
                Wert gekürzt.
              </p>
            )}
          </div>
        </div>
      )}

      {Object.keys(rules).length > 0 && (
        <div className="panel">
          <div className="panel-head">Entscheidungen zu Abwesenheitsgründen</div>
          <div className="panel-body">
            <table className="table-cats">
              <thead>
                <tr><th>Grundtext im Zeitnachweis</th><th className="w-48">Behandlung</th><th className="w-10"></th></tr>
              </thead>
              <tbody>
                {Object.entries(rules).map(([reason, decision]) => (
                  <tr key={reason}>
                    <td className="font-mono">{reason}</td>
                    <td>
                      <select className="field w-full" value={decision}
                              onChange={e => {
                                setRules({ ...rules, [reason]: e.target.value as 'book' | 'exclude' })
                                touch()
                              }}>
                        <option value="book">als Arbeitszeit buchen</option>
                        <option value="exclude">nicht buchen</option>
                      </select>
                    </td>
                    <td>
                      <button className="btn w-full" onClick={() => {
                        const next = { ...rules }; delete next[reason]; setRules(next); touch()
                      }}>×</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {error && <p className="text-brand-red">{error}</p>}
      {warnings.length > 0 && (
        <div className="panel border-brand-yellow">
          <div className="panel-head bg-brand-yellow text-cats-text">Hinweise</div>
          <ul className="panel-body list-disc pl-5 space-y-1">
            {warnings.map((w, i) => <li key={i}>{w}</li>)}
          </ul>
        </div>
      )}

      <div className="flex items-center gap-3">
        <button className="btn-primary" onClick={save}
                disabled={busy || !totalOk || !personnel.trim()
                          || (elements.length === 0 && projects.length === 0)}>
          Speichern
        </button>
        {saved && <span className="text-cats-muted">Gespeichert als Version {cfg.version}.</span>}
      </div>
    </div>
  )
}
