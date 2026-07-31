/*
 * App shell.
 *
 * A tool, not a product: no hero, no marketing surface, no animation. Five
 * tabs, a sign-out link, and dense readable tables.
 *
 * Routing is component state rather than a router. The dashboard has five
 * screens and one drill-down; a router would add a dependency and a build
 * concern to solve a problem this app does not have.
 */

import { useEffect, useState } from 'react'
import {
  Activity,
  BarChart3,
  ListChecks,
  PlusCircle,
  Play,
} from 'lucide-react'
import {
  MetricsScreen,
  OverviewScreen,
  QuestionDetailScreen,
  QuestionsScreen,
  RunsScreen,
  ManualAddScreen,
} from './components/screens'
import { getCurrentUser, initAuth, isConfigured, signIn, signOutNow, useDevAuth } from './lib/auth'
import type { FirebaseUser } from './lib/auth'
import { Card } from './components/primitives'

type Tab = 'overview' | 'questions' | 'metrics' | 'runs' | 'add'

const TABS: Array<{ id: Tab; label: string; Icon: typeof Activity }> = [
  { id: 'overview', label: 'Overview', Icon: Activity },
  { id: 'questions', label: 'Questions', Icon: ListChecks },
  { id: 'metrics', label: 'Metrics', Icon: BarChart3 },
  { id: 'runs', label: 'Runs', Icon: Play },
  { id: 'add', label: 'Add question', Icon: PlusCircle },
]

const DEV_EMAIL = import.meta.env.VITE_DEV_OPERATOR_EMAIL as string | undefined

export function App() {
  const [tab, setTab] = useState<Tab>('overview')
  const [openQuestion, setOpenQuestion] = useState<string | null>(null)
  const [user, setUser] = useState<FirebaseUser | null>(getCurrentUser())
  const [ready, setReady] = useState(false)

  useEffect(() => {
    if (DEV_EMAIL) {
      useDevAuth(DEV_EMAIL)
      setUser({ email: DEV_EMAIL, uid: 'dev' })
      setReady(true)
      return
    }
    initAuth((next) => {
      setUser(next)
      setReady(true)
    })
  }, [])

  if (!ready) {
    return (
      <div className="shell">
        <div className="empty">Starting…</div>
      </div>
    )
  }

  if (!user) {
    return (
      <div className="shell login">
        <h1 style={{ fontSize: 18, marginBottom: 20 }}>Anizai Calibration</h1>
        <Card>
          {isConfigured() ? (
            <>
              <p style={{ marginTop: 0, fontSize: 13 }}>
                Operator access only. Signing in with an account that is not on
                the allowlist will authenticate but not authorise.
              </p>
              <button className="action" onClick={() => signIn()}>
                Sign in with Google
              </button>
            </>
          ) : (
            <p style={{ marginTop: 0, fontSize: 13 }}>
              Firebase is not configured. Set <code>VITE_FIREBASE_API_KEY</code>,{' '}
              <code>VITE_FIREBASE_AUTH_DOMAIN</code> and{' '}
              <code>VITE_FIREBASE_PROJECT_ID</code>, or set{' '}
              <code>VITE_DEV_OPERATOR_EMAIL</code> to use the local development
              bypass.
            </p>
          )}
        </Card>
      </div>
    )
  }

  return (
    <div className="shell">
      <header className="topbar">
        <h1>Anizai Calibration</h1>
        <div className="who">
          <span>{user.email}</span>
          {!DEV_EMAIL && (
            <button className="action" onClick={() => signOutNow()}>
              Sign out
            </button>
          )}
        </div>
      </header>

      <nav className="tabs" aria-label="Sections">
        {TABS.map(({ id, label, Icon }) => (
          <button
            key={id}
            aria-current={tab === id && !openQuestion ? 'page' : undefined}
            onClick={() => {
              setTab(id)
              setOpenQuestion(null)
            }}
          >
            <Icon
              size={14}
              style={{ verticalAlign: -2, marginRight: 6 }}
              aria-hidden="true"
            />
            {label}
          </button>
        ))}
      </nav>

      <main>
        {openQuestion ? (
          <QuestionDetailScreen
            questionId={openQuestion}
            onBack={() => setOpenQuestion(null)}
          />
        ) : (
          <>
            {tab === 'overview' && <OverviewScreen />}
            {tab === 'questions' && (
              <QuestionsScreen onOpen={(id) => setOpenQuestion(id)} />
            )}
            {tab === 'metrics' && <MetricsScreen />}
            {tab === 'runs' && <RunsScreen />}
            {tab === 'add' && <ManualAddScreen />}
          </>
        )}
      </main>
    </div>
  )
}
