import { useEffect, useState } from 'react'
import { api, ApiError } from '../api'
import type { CatsConfig, WbsElement } from '../types'

export default function ConfigPage() {
  const [cfg, setCfg] = useState<CatsConfig | null>(null)
  const [personnel, setPersonnel] = useState('')
  const [elements, setElements] = useState<WbsElement[]>([])
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
    setElements(c.wbs_elements.length ? c.wbs_elements : [{ wbs: '', weight: 100 }])
    setRules(c.reason_rules)
  }

  const total = elements.reduce((s, e) => s + (Number(e.weight) || 0), 0)
  const totalOk = Math.abs(total - 100) < 0.01

  function update(i: number, patch: Partial<WbsElement>) {
    setElements(prev => prev.map((e, idx) => (idx === i ? { ...e, ...patch } : e)))
    setSaved(false)
  }

  /** Verteilt 100 % gleichmäßig; der Rundungsrest geht auf die erste Zeile. */
  function distributeEvenly() {
    const n = elements.length
    if (!n) return
    const each = Math.floor((100 / n) * 10) / 10
    setElements(elements.map((e, i) =>
      ({ ...e, weight: i === 0 ? Math.round((100 - each * (n - 1)) * 10) / 10 : each })))
    setSaved(false)
  }

  async function save() {
    setError('')
    setBusy(true)
    try {
      const res = await api.put<{ version: number; warnings: string[] }>('/api/cats-config', {
        personnel_number: personnel,
        wbs_elements: elements.filter(e => e.wbs.trim()),
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
                 onChange={e => { setPersonnel(e.target.value); setSaved(false) }} />
          <p className="text-cats-muted mt-1">
            Wird beim Import gegen den Kopf des Zeitnachweises geprüft.
          </p>
        </div>
      </div>

      <div className="panel">
        <div className="panel-head">WBS-Arbeitsvorrat</div>
        <div className="panel-body space-y-3">
          <table className="table-cats">
            <thead>
              <tr>
                <th>WBS-Element</th>
                <th className="w-28">Gewichtung</th>
                <th className="w-28">bei {cfg.typical_week_hours} h/Woche</th>
                <th className="w-10"></th>
              </tr>
            </thead>
            <tbody>
              {elements.map((e, i) => {
                const weekHours = (cfg.typical_week_hours * (Number(e.weight) || 0)) / 100
                const tooSmall = e.wbs.trim() !== '' && Number(e.weight) < cfg.min_weight_pct
                return (
                  <tr key={i}>
                    <td>
                      <input className="field w-full" value={e.wbs}
                             placeholder="DEO1111-NP/PJ00-O51.0000"
                             onChange={ev => update(i, { wbs: ev.target.value })} />
                    </td>
                    <td>
                      <input type="number" step="0.1" min="0" max="100"
                             className="field w-full text-right" value={e.weight}
                             onChange={ev => update(i, { weight: Number(ev.target.value) })} />
                    </td>
                    <td className={`num ${tooSmall ? 'text-brand-red' : 'text-cats-muted'}`}>
                      {weekHours.toFixed(2).replace('.', ',')} h
                    </td>
                    <td>
                      <button className="btn w-full" title="Zeile entfernen"
                              onClick={() => { setElements(elements.filter((_, x) => x !== i)); setSaved(false) }}>
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
                <td className="border border-cats-border" colSpan={2}></td>
              </tr>
            </tfoot>
          </table>

          <div className="flex flex-wrap gap-2">
            <button className="btn"
                    onClick={() => { setElements([...elements, { wbs: '', weight: 0 }]); setSaved(false) }}>
              Zeile hinzufügen
            </button>
            <button className="btn" onClick={distributeEvenly}>Gleichmäßig verteilen</button>
          </div>

          {!totalOk && (
            <p className="text-brand-red">
              Die Summe muss exakt 100 % ergeben. Aktuell fehlen{' '}
              {(100 - total).toFixed(1).replace('.', ',')} Prozentpunkte.
            </p>
          )}
          <p className="text-cats-muted">
            Unter {cfg.min_weight_pct.toFixed(1).replace('.', ',')} % ergibt ein Element bei{' '}
            {cfg.typical_week_hours} h Wochenarbeitszeit weniger als eine Stunde und fällt in
            vielen Wochen aus der Verteilung.
          </p>
        </div>
      </div>

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
                              onChange={e => { setRules({ ...rules, [reason]: e.target.value as 'book' | 'exclude' }); setSaved(false) }}>
                        <option value="book">als Arbeitszeit buchen</option>
                        <option value="exclude">nicht buchen</option>
                      </select>
                    </td>
                    <td>
                      <button className="btn w-full" onClick={() => {
                        const next = { ...rules }; delete next[reason]; setRules(next); setSaved(false)
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
                disabled={busy || !totalOk || !personnel.trim()}>
          Speichern
        </button>
        {saved && <span className="text-cats-muted">Gespeichert als Version {cfg.version}.</span>}
      </div>
    </div>
  )
}
