/** Schmaler API-Client. Das JWT liegt in localStorage und wird als Bearer gesendet. */

const TOKEN_KEY = 'fckcats.token'
const DATA_KEY = 'fckcats.datakey'

export const token = {
  get: () => localStorage.getItem(TOKEN_KEY),
  set: (t: string) => localStorage.setItem(TOKEN_KEY, t),
  clear: () => {
    localStorage.removeItem(TOKEN_KEY)
    dataKey.clear()
  },
}

/**
 * Datenschlüssel bei gesetzter Passphrase. Bewusst in sessionStorage: er
 * verschwindet mit dem Tab, statt wie das Token dauerhaft liegenzubleiben.
 */
export const dataKey = {
  get: () => sessionStorage.getItem(DATA_KEY),
  set: (k: string) => sessionStorage.setItem(DATA_KEY, k),
  clear: () => sessionStorage.removeItem(DATA_KEY),
}

export class ApiError extends Error {
  constructor(public status: number, message: string, public detail?: unknown) {
    super(message)
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  const t = token.get()
  if (t) headers.set('Authorization', `Bearer ${t}`)
  const k = dataKey.get()
  if (k) headers.set('X-Data-Key', k)
  if (init.body && !(init.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json')
  }

  const res = await fetch(path, { ...init, headers })

  if (res.status === 401) {
    token.clear()
    window.location.reload()
    throw new ApiError(401, 'Sitzung abgelaufen')
  }

  if (!res.ok) {
    let detail: unknown = await res.text()
    try {
      const parsed = JSON.parse(detail as string)
      detail = parsed.detail ?? parsed
    } catch {
      /* Klartext belassen */
    }
    const message = typeof detail === 'string'
      ? detail
      : (detail as { message?: string })?.message ?? `Fehler ${res.status}`
    throw new ApiError(res.status, message, detail)
  }

  if (res.status === 204) return undefined as T
  const type = res.headers.get('content-type') ?? ''
  return (type.includes('application/json') ? res.json() : res.text()) as Promise<T>
}

export const api = {
  get:  <T>(p: string) => request<T>(p),
  /** POST, dessen Antwort eine Datei ist — für den Export ohne Speicherung. */
  postDownload: async (p: string, body: unknown, fallbackName: string) => {
    const headers: Record<string, string> = {
      Authorization: `Bearer ${token.get()}`,
      'Content-Type': 'application/json',
    }
    const k = dataKey.get()
    if (k) headers['X-Data-Key'] = k
    const res = await fetch(p, { method: 'POST', headers, body: JSON.stringify(body) })
    if (!res.ok) {
      let detail: string = await res.text()
      try { detail = JSON.parse(detail).detail ?? detail } catch { /* Klartext */ }
      throw new ApiError(res.status, detail)
    }
    const disposition = res.headers.get('content-disposition') ?? ''
    const match = disposition.match(/filename="([^"]+)"/)
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = match?.[1] ?? fallbackName
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
    return {
      rows: Number(res.headers.get('x-row-count') ?? 0),
      hours: Number(res.headers.get('x-total-hours') ?? 0),
    }
  },
  post: <T>(p: string, body?: unknown) =>
    request<T>(p, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) }),
  put:  <T>(p: string, body: unknown) =>
    request<T>(p, { method: 'PUT', body: JSON.stringify(body) }),
  patch: <T>(p: string, body: unknown) =>
    request<T>(p, { method: 'PATCH', body: JSON.stringify(body) }),
  del:  <T>(p: string) => request<T>(p, { method: 'DELETE' }),
  /** DELETE mit Rumpf — die Passphrase gehört nicht in die URL. */
  delWithBody: <T>(p: string, body: unknown) =>
    request<T>(p, { method: 'DELETE', body: JSON.stringify(body) }),
  upload: <T>(p: string, file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return request<T>(p, { method: 'POST', body: fd })
  },
  uploadFields: <T>(p: string, fields: Record<string, File | string>) => {
    const fd = new FormData()
    for (const [k, v] of Object.entries(fields)) fd.append(k, v)
    return request<T>(p, { method: 'POST', body: fd })
  },
  /** Laedt eine Datei mit Bearer-Token und stoesst den Browser-Download an. */
  download: async (p: string, filename: string) => {
    const headers: Record<string, string> = { Authorization: `Bearer ${token.get()}` }
    const k = dataKey.get()
    if (k) headers['X-Data-Key'] = k
    const res = await fetch(p, { headers })
    if (!res.ok) throw new ApiError(res.status, 'Download fehlgeschlagen')
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
  },
}

/** 7.83 -> "7,83" — im Zeitnachweis und in CATS ist das Komma das Trennzeichen. */
export const fmtHours = (h: number | null | undefined) =>
  h === null || h === undefined ? '' : h.toFixed(2).replace('.', ',')

/** "2026-08-17" -> "17.08.2026" */
export const fmtDate = (iso: string) => {
  const [y, m, d] = iso.split('-')
  return `${d}.${m}.${y}`
}

export const WEEKDAY_SHORT = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So']

export const weekdayOf = (iso: string) => {
  const d = new Date(iso + 'T00:00:00')
  return WEEKDAY_SHORT[(d.getDay() + 6) % 7]
}
