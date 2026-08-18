/** Schmaler API-Client. Das JWT liegt in localStorage und wird als Bearer gesendet. */

const TOKEN_KEY = 'fckcats.token'

export const token = {
  get: () => localStorage.getItem(TOKEN_KEY),
  set: (t: string) => localStorage.setItem(TOKEN_KEY, t),
  clear: () => localStorage.removeItem(TOKEN_KEY),
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
  post: <T>(p: string, body?: unknown) =>
    request<T>(p, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) }),
  put:  <T>(p: string, body: unknown) =>
    request<T>(p, { method: 'PUT', body: JSON.stringify(body) }),
  patch: <T>(p: string, body: unknown) =>
    request<T>(p, { method: 'PATCH', body: JSON.stringify(body) }),
  del:  <T>(p: string) => request<T>(p, { method: 'DELETE' }),
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
    const res = await fetch(p, { headers: { Authorization: `Bearer ${token.get()}` } })
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
