import { useCallback, useEffect, useState } from 'react'
import { api, ApiError, dataKey, token } from './api'
import type { Me } from './types'
import LoginPage from './components/LoginPage'
import PasswordChange from './components/PasswordChange'
import ConfigPage from './components/ConfigPage'
import ImportPage from './components/ImportPage'
import EntriesPage from './components/EntriesPage'
import HistoryPage from './components/HistoryPage'
import SettingsPage from './components/SettingsPage'
import PrivacyPage from './components/PrivacyPage'
import UnlockPage from './components/UnlockPage'

type Tab = 'import' | 'entries' | 'config' | 'history' | 'privacy' | 'settings'

const TABS: { id: Tab; label: string; adminOnly?: boolean }[] = [
  { id: 'import',   label: 'Import' },
  { id: 'entries',  label: 'Zieltabelle' },
  { id: 'config',   label: 'CATS-Config' },
  { id: 'history',  label: 'Historie' },
  { id: 'privacy',  label: 'Datenschutz' },
  { id: 'settings', label: 'Einstellungen', adminOnly: true },
]

export default function App() {
  const [me, setMe] = useState<Me | null>(null)
  const [locked, setLocked] = useState(false)
  const [loading, setLoading] = useState(true)
  const [tab, setTab] = useState<Tab>('import')
  // Zwingt die Zieltabelle nach einem Import zum Neuladen.
  const [reloadKey, setReloadKey] = useState(0)

  const loadMe = useCallback(async () => {
    if (!token.get()) { setMe(null); setLoading(false); return }
    try {
      const profile = await api.get<Me>('/api/auth/me')
      setMe(profile)
      // 412 heisst: der Workspace ist mit einer Passphrase gesperrt.
      try {
        await api.get('/api/cats-config')
        setLocked(false)
      } catch (err) {
        setLocked(err instanceof ApiError && err.status === 412)
      }
    } catch {
      token.clear()
      setMe(null)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void loadMe() }, [loadMe])

  function logout() {
    token.clear()
    dataKey.clear()
    setMe(null)
    setLocked(false)
  }

  if (loading) {
    return <div className="min-h-screen grid place-items-center text-cats-muted">Wird geladen …</div>
  }

  if (!me) return <LoginPage onLoggedIn={() => { setLoading(true); void loadMe() }} />

  if (me.must_change_password) {
    return <PasswordChange onDone={() => { setLoading(true); void loadMe() }} />
  }

  if (locked) {
    return <UnlockPage onUnlocked={() => { setLoading(true); void loadMe() }} />
  }

  const visibleTabs = TABS.filter(t => !t.adminOnly || me.role === 'admin')

  return (
    <div className="min-h-screen flex flex-col">
      <header className="bg-gradient-to-b from-cats-header to-cats-bg border-b border-cats-border">
        <div className="px-4 py-2 flex items-center gap-3">
          <img src="/logo.svg" alt="" className="h-11" />
          <h1 className="font-bold italic text-lg text-brand-darker">
            Zeitnachweis → CATS Mass Upload
          </h1>
          <div className="ml-auto flex items-center gap-3">
            <span className="text-cats-muted">{me.display_name || me.username}</span>
            {me.role === 'admin' && (
              <span className="px-2 py-0.5 border border-brand-dark bg-brand text-white">
                Administrator
              </span>
            )}
            {me.source === 'saml' && (
              <span className="px-2 py-0.5 border border-cats-border bg-cats-rowalt
                               text-cats-muted">
                SSO
              </span>
            )}
            <button className="btn" onClick={logout}>Abmelden</button>
          </div>
        </div>

        <nav className="px-4 flex gap-1" role="tablist">
          {visibleTabs.map(t => (
            <button key={t.id} role="tab" aria-selected={tab === t.id}
                    onClick={() => setTab(t.id)}
                    className={`px-4 py-1.5 border border-b-0 border-cats-border -mb-px
                      ${tab === t.id
                        ? 'bg-cats-panel font-bold text-brand-darker'
                        : 'bg-cats-rowalt text-cats-muted hover:bg-cats-rowhover'}`}>
              {t.label}
            </button>
          ))}
        </nav>
      </header>

      <main className="flex-1 p-4">
        {tab === 'import'   && <ImportPage onImported={() => { setReloadKey(k => k + 1); setTab('entries') }} />}
        {tab === 'entries'  && <EntriesPage key={reloadKey} />}
        {tab === 'config'   && <ConfigPage />}
        {tab === 'history'  && <HistoryPage />}
        {tab === 'privacy'  && <PrivacyPage />}
        {tab === 'settings' && me.role === 'admin' && <SettingsPage />}
      </main>

      <footer className="px-4 py-2 border-t border-cats-border text-cats-muted">
        Gebucht wird die Spalte <span className="font-mono">Prod.</span> des Zeitnachweises.
        Die Tagessumme entspricht immer exakt der erfassten Arbeitszeit.
      </footer>
    </div>
  )
}
