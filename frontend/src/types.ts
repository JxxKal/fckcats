export interface Me {
  id: number
  username: string
  email: string | null
  display_name: string | null
  role: 'admin' | 'user'
  source: 'local' | 'saml'
  must_change_password: boolean
}

export interface WbsElement {
  wbs: string
  weight: number
}

export interface CatsConfig {
  configured: boolean
  personnel_number: string
  wbs_elements: WbsElement[]
  reason_rules: Record<string, 'book' | 'exclude'>
  version: number
  typical_week_hours: number
  min_weight_pct: number
}

export interface ParsedDay {
  day: number
  weekday: string
  work_date: string | null
  reason: string
  time_from: string | null
  time_to: string | null
  hours_gross: number | null
  hours_target: number | null
  hours_net: number | null
  excluded: boolean
  unknown_reason: boolean
  incomplete: boolean
  bookable: boolean
}

export interface UploadResult {
  upload_id: number
  personnel_number: string | null
  employee_name: string | null
  month: number | null
  year: number | null
  days: ParsedDay[]
  bookable_count: number
  bookable_hours: number
  clarifications: ParsedDay[]
  warnings: string[]
  already_exported_dates: string[]
}

export interface EntryRow {
  work_date: string
  wbs_element: string
  hours: number
  exported: boolean
  export_id: number | null
  day_hours: number | null
  day_source: string | null
}

export interface WeekGroup {
  iso_year: number
  iso_week: number
  hours: number
  days: number
  per_wbs: Record<string, number>
  rows: EntryRow[]
  open_rows: number
  exported_rows: number
}

export interface EntriesResponse {
  weeks: WeekGroup[]
  total_hours: number
  open_hours: number
}

export interface ExportRecord {
  id: number
  filename: string
  date_from: string
  date_to: string
  row_count: number
  total_hours: number
  created_at: string
  download_url: string
}

export interface SslStatus {
  mode: string
  active: boolean
  subject: string | null
  issuer: string | null
  not_after: string | null
  domains: string[] | null
  hostname: string | null
}

export interface AdminUser {
  id: number
  username: string
  email: string | null
  display_name: string | null
  role: 'admin' | 'user'
  source: 'local' | 'saml'
  active: boolean
  must_change_password: boolean
  created_at: string
  last_login: string | null
  entry_count: number
  export_count: number
}

export interface EntryHistoryRecord {
  work_date: string
  payload: { wbs_element: string; hours: number; exported_at: string | null }[]
  was_exported: boolean
  replaced_at: string
}
