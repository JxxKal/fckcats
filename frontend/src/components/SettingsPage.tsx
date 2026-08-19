import { useEffect, useState } from 'react'
import { api, ApiError } from '../api'
import type { SslStatus } from '../types'
import UserSection from './UserSection'

interface SamlSettings {
  enabled: boolean
  idp_entity_id: string
  idp_sso_url: string
  idp_slo_url: string
  idp_x509_cert: string
  sp_entity_id: string
  acs_url: string
  slo_url: string
  attribute_username: string
  attribute_email: string
  attribute_display_name: string
  default_role: string
  want_messages_signed: boolean
}

export default function SettingsPage() {
  const [ssl, setSsl] = useState<SslStatus | null>(null)
  const [hostname, setHostname] = useState('')
  const [saml, setSaml] = useState<SamlSettings | null>(null)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  useEffect(() => { void load() }, [])

  async function load() {
    const [s, h, sm] = await Promise.all([
      api.get<SslStatus>('/api/ssl/status'),
      api.get<{ hostname: string }>('/api/ssl/hostname'),
      api.get<SamlSettings>('/api/admin/saml'),
    ])
    setSsl(s); setHostname(h.hostname); setSaml(sm)
  }

  async function run(fn: () => Promise<unknown>, ok: string) {
    setError(''); setMessage('')
    try { await fn(); setMessage(ok); await load() }
    catch (err) { setError(err instanceof ApiError ? err.message : 'Fehlgeschlagen') }
  }

  return (
    <div className="space-y-4 max-w-5xl">
      <UserSection />

      <div className="panel max-w-3xl">
        <div className="panel-head">Hostname</div>
        <div className="panel-body space-y-2">
          <input className="field w-full" value={hostname} placeholder="fckcats.example.org"
                 onChange={e => setHostname(e.target.value)} />
          <p className="text-cats-muted">
            Setzt <span className="font-mono">server_name</span> in nginx. Wirkt nach einem
            Neustart des nginx-Containers.
          </p>
          <button className="btn-primary"
                  onClick={() => run(() => api.post('/api/ssl/hostname', { hostname }), 'Hostname gespeichert.')}>
            Speichern
          </button>
        </div>
      </div>

      <div className="panel max-w-3xl">
        <div className="panel-head">TLS-Zertifikat</div>
        <div className="panel-body space-y-3">
          {ssl?.active ? (
            <table className="table-cats">
              <tbody>
                <tr><td className="w-40 label">Modus</td><td className="font-mono">{ssl.mode}</td></tr>
                <tr><td className="label">Subject</td><td className="font-mono break-all">{ssl.subject}</td></tr>
                <tr><td className="label">Aussteller</td><td className="font-mono break-all">{ssl.issuer}</td></tr>
                <tr><td className="label">gültig bis</td><td className="font-mono">{ssl.not_after}</td></tr>
                <tr><td className="label">Domains</td><td className="font-mono">{ssl.domains?.join(', ') ?? '—'}</td></tr>
              </tbody>
            </table>
          ) : (
            <p className="text-cats-muted">
              Kein Zertifikat hinterlegt — nginx läuft auf Port 80 ohne TLS.
            </p>
          )}

          <details>
            <summary className="cursor-pointer font-bold text-brand-darker">
              Zertifikat hochladen (PEM)
            </summary>
            <form className="mt-2 space-y-2" onSubmit={e => {
              e.preventDefault()
              const f = new FormData(e.currentTarget)
              const fields: Record<string, File | string> = {
                cert: f.get('cert') as File,
                key: f.get('key') as File,
              }
              const ca = f.get('ca') as File
              if (ca && ca.size) fields.ca = ca
              void run(() => api.uploadFields('/api/ssl/upload', fields), 'Zertifikat gespeichert.')
            }}>
              <label className="label block">Zertifikat (cert.pem)</label>
              <input type="file" name="cert" required className="field w-full" />
              <label className="label block">Privater Schlüssel (key.pem)</label>
              <input type="file" name="key" required className="field w-full" />
              <label className="label block">CA-Kette (optional)</label>
              <input type="file" name="ca" className="field w-full" />
              <button className="btn-primary">Hochladen</button>
            </form>
          </details>

          <details>
            <summary className="cursor-pointer font-bold text-brand-darker">
              Zertifikat hochladen (PFX / PKCS#12)
            </summary>
            <form className="mt-2 space-y-2" onSubmit={e => {
              e.preventDefault()
              const f = new FormData(e.currentTarget)
              void run(() => api.uploadFields('/api/ssl/upload-pfx', {
                pfx: f.get('pfx') as File,
                password: (f.get('password') as string) || '',
              }), 'PFX importiert.')
            }}>
              <label className="label block">PFX-Datei</label>
              <input type="file" name="pfx" required className="field w-full" />
              <label className="label block">Passwort</label>
              <input type="password" name="password" className="field w-full" />
              <button className="btn-primary">Importieren</button>
            </form>
          </details>

          <details>
            <summary className="cursor-pointer font-bold text-brand-darker">
              Selbstsigniertes Zertifikat erzeugen
            </summary>
            <form className="mt-2 space-y-2" onSubmit={e => {
              e.preventDefault()
              const f = new FormData(e.currentTarget)
              void run(() => api.post('/api/ssl/self-signed', {
                common_name: f.get('cn'),
                days: Number(f.get('days')),
              }), 'Zertifikat erzeugt. nginx neu starten, damit es greift.')
            }}>
              <label className="label block">Common Name</label>
              <input name="cn" required className="field w-full"
                     defaultValue={hostname || 'fckcats.local'} />
              <label className="label block">Gültigkeit in Tagen</label>
              <input name="days" type="number" className="field w-full" defaultValue={825} />
              <button className="btn-primary">Erzeugen</button>
            </form>
          </details>
        </div>
      </div>

      {saml && (
        <div className="panel max-w-3xl">
          <div className="panel-head">SAML Single Sign-on</div>
          <form className="panel-body space-y-2" onSubmit={e => {
            e.preventDefault()
            void run(() => api.put('/api/admin/saml', saml), 'SAML-Einstellungen gespeichert.')
          }}>
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={saml.enabled}
                     onChange={e => setSaml({ ...saml, enabled: e.target.checked })} />
              <span className="font-bold">SAML aktivieren</span>
            </label>

            {([
              ['idp_entity_id', 'IdP Entity ID'],
              ['idp_sso_url', 'IdP SSO-URL'],
              ['idp_slo_url', 'IdP SLO-URL (optional)'],
              ['sp_entity_id', 'SP Entity ID'],
              ['acs_url', 'ACS-URL (https://host/api/auth/saml/acs)'],
              ['slo_url', 'SP SLO-URL (optional)'],
              ['attribute_username', 'Attribut Benutzername'],
              ['attribute_email', 'Attribut E-Mail'],
              ['attribute_display_name', 'Attribut Anzeigename'],
            ] as const).map(([field, label]) => (
              <div key={field}>
                <label className="label block mb-1">{label}</label>
                <input className="field w-full" value={saml[field]}
                       onChange={e => setSaml({ ...saml, [field]: e.target.value })} />
              </div>
            ))}

            <div>
              <label className="label block mb-1">IdP-Zertifikat (X.509, Base64)</label>
              <textarea className="field w-full h-28" value={saml.idp_x509_cert}
                        onChange={e => setSaml({ ...saml, idp_x509_cert: e.target.value })} />
              <p className="text-cats-muted">
                <span className="font-mono">__gespeichert__</span> bedeutet: das hinterlegte
                Zertifikat bleibt unverändert.
              </p>
            </div>

            <div className="flex gap-2">
              <button className="btn-primary">Speichern</button>
              <a className="btn" href="/api/auth/saml/metadata">SP-Metadata herunterladen</a>
            </div>
          </form>
        </div>
      )}

      {message && <p className="text-brand-dark font-bold">{message}</p>}
      {error && <p className="text-brand-red">{error}</p>}
    </div>
  )
}
