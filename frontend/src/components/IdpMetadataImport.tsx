import { useState } from 'react'

export interface IdpMetadata {
  idp_entity_id: string
  idp_sso_url: string
  idp_slo_url: string
  idp_x509_cert: string
}

const MD_NS = 'urn:oasis:names:tc:SAML:2.0:metadata'
const DS_NS = 'http://www.w3.org/2000/09/xmldsig#'
const REDIRECT = 'urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect'
const POST = 'urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST'

function pickLocation(elements: Element[], preferred: string): string {
  const match = elements.find(e => e.getAttribute('Binding') === preferred)
  return (match ?? elements[0])?.getAttribute('Location') ?? ''
}

/**
 * Liest die IdP-Metadata eines Identity Providers.
 *
 * Gesucht wird gezielt der IDPSSODescriptor, nicht das Wurzelelement: manche
 * IdPs liefern eine Sammlung mehrerer Einträge (EntitiesDescriptor), andere
 * beschreiben in derselben Datei zusätzlich einen SP. Die entityID gehört zum
 * umschließenden EntityDescriptor, nicht zum Dokument.
 */
export function parseIdpMetadata(xml: string): IdpMetadata {
  const doc = new DOMParser().parseFromString(xml, 'text/xml')
  if (doc.querySelector('parsererror')) {
    throw new Error('Die Datei ist kein gültiges XML.')
  }

  const descriptors = Array.from(doc.getElementsByTagNameNS(MD_NS, 'IDPSSODescriptor'))
  if (!descriptors.length) {
    throw new Error(
      'Kein IDPSSODescriptor gefunden. Das sieht nicht nach IdP-Metadata aus — ' +
      'stammt die Datei vielleicht von einem Service Provider?')
  }
  const idp = descriptors[0]

  // entityID hängt am umschließenden EntityDescriptor.
  let entity: Element | null = idp.parentElement
  while (entity && entity.localName !== 'EntityDescriptor') entity = entity.parentElement
  const entityId = entity?.getAttribute('entityID')
    ?? doc.documentElement.getAttribute('entityID')
    ?? ''

  const sso = pickLocation(
    Array.from(idp.getElementsByTagNameNS(MD_NS, 'SingleSignOnService')), REDIRECT)
  const slo = pickLocation(
    Array.from(idp.getElementsByTagNameNS(MD_NS, 'SingleLogoutService')), POST)

  // Signaturzertifikat: KeyDescriptor ohne use gilt für alles.
  const keys = Array.from(idp.getElementsByTagNameNS(MD_NS, 'KeyDescriptor'))
  const signing = keys.find(k => (k.getAttribute('use') ?? 'signing') === 'signing') ?? keys[0]
  const cert = signing
    ?.getElementsByTagNameNS(DS_NS, 'X509Certificate')[0]
    ?.textContent?.replace(/\s/g, '') ?? ''

  if (!entityId && !sso) {
    throw new Error('Weder entityID noch SSO-URL gefunden.')
  }

  return {
    idp_entity_id: entityId,
    idp_sso_url: sso,
    idp_slo_url: slo,
    idp_x509_cert: cert,
  }
}

interface Props {
  onImport: (values: IdpMetadata) => void
  /** Anzahl weiterer IdP-Einträge, falls die Datei mehrere enthält. */
  disabled?: boolean
}

export default function IdpMetadataImport({ onImport, disabled }: Props) {
  const [xml, setXml] = useState('')
  const [parsed, setParsed] = useState<IdpMetadata | null>(null)
  const [error, setError] = useState('')
  const [note, setNote] = useState('')

  function analyse(text: string) {
    setError('')
    setParsed(null)
    setNote('')
    if (!text.trim()) return
    try {
      const values = parseIdpMetadata(text)
      setParsed(values)
      const count = (text.match(/IDPSSODescriptor/g) ?? []).length / 2
      if (count > 1) {
        setNote(`Die Datei enthält ${Math.round(count)} IdP-Einträge — übernommen ` +
                `wird der erste.`)
      }
      if (!values.idp_x509_cert) {
        setNote(n => (n ? n + ' ' : '') +
          'Kein Signaturzertifikat gefunden; es muss von Hand eingetragen werden.')
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Die Datei konnte nicht gelesen werden.')
    }
  }

  async function readFile(file: File) {
    const text = await file.text()
    setXml(text)
    analyse(text)
  }

  return (
    <div className="border border-cats-border bg-cats-rowalt p-3 space-y-2">
      <div className="font-bold text-brand-darker">IdP-Metadata einlesen</div>
      <p className="text-cats-muted">
        Die Metadata-XML des Identity Providers hochladen oder einfügen — Entity ID,
        SSO-URL, SLO-URL und Signaturzertifikat werden daraus übernommen. Die
        übrigen Felder bleiben unberührt.
      </p>

      <input type="file" accept=".xml,text/xml,application/xml" className="field w-full"
             onChange={e => { const f = e.target.files?.[0]; if (f) void readFile(f) }} />

      <textarea className="field w-full h-24 font-mono" value={xml}
                placeholder={'<?xml version="1.0"?>\n<md:EntityDescriptor entityID="…">'}
                onChange={e => { setXml(e.target.value); analyse(e.target.value) }} />

      {error && <p className="text-brand-red">{error}</p>}

      {parsed && (
        <>
          <table className="table-cats">
            <tbody>
              <tr>
                <td className="w-40">Entity ID</td>
                <td className="font-mono break-all">{parsed.idp_entity_id || '—'}</td>
              </tr>
              <tr>
                <td>SSO-URL</td>
                <td className="font-mono break-all">{parsed.idp_sso_url || '—'}</td>
              </tr>
              <tr>
                <td>SLO-URL</td>
                <td className="font-mono break-all">{parsed.idp_slo_url || '—'}</td>
              </tr>
              <tr>
                <td>Zertifikat</td>
                <td className="font-mono">
                  {parsed.idp_x509_cert
                    ? `${parsed.idp_x509_cert.length} Zeichen, beginnt mit ${parsed.idp_x509_cert.slice(0, 24)}…`
                    : '—'}
                </td>
              </tr>
            </tbody>
          </table>

          {note && <p className="text-brand-red">{note}</p>}

          <button className="btn-primary" disabled={disabled}
                  onClick={() => { onImport(parsed); setXml(''); setParsed(null); setNote('') }}>
            Werte übernehmen
          </button>
        </>
      )}
    </div>
  )
}
