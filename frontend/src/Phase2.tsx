import { useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  ClipboardCheck,
  Download,
  ExternalLink,
  FileSpreadsheet,
  Globe2,
  LoaderCircle,
  Search,
  ShieldCheck,
  Square,
} from 'lucide-react'

import { api } from './api'
import { ReviewPage } from './ReviewPage'
import type {
  Contact,
  EnrichmentStatistics,
  EnrichmentStatus,
  StoredEnrichmentResult,
  UploadBatch,
} from './types'


const EMPTY_STATS: EnrichmentStatistics = {
  total: 0,
  pending: 0,
  processing: 0,
  processed: 0,
  completed: 0,
  manual_review: 0,
  failed: 0,
  progress_percent: 0,
}

const STAGE_LABELS: Record<string, string> = {
  queued: 'Preparing the next contact',
  resolving_identity: 'Preparing contact identity',
  searching_web: 'Searching public web results',
  searching_linkedin: 'Searching for the LinkedIn profile',
  reusing_linkedin: 'Using the saved LinkedIn profile',
  searching_local_news: 'Searching relevant local news',
  verifying_with_llm: 'The AI provider is verifying facts and classification',
  validating_llm_response: 'Validating the exact department taxonomy',
  saved: 'Result saved to the Excel report',
  daily_limit_reached: 'Paused at the configured daily LLM request limit',
  provider_unavailable: 'Paused because the verification provider is unavailable',
  stopping: 'Stopping safely after the current request',
}


interface Props {
  batch: UploadBatch
  contacts: Contact[]
  onBack: () => void
  onNewFile: () => void
}


export function Phase2({ batch, contacts, onBack, onNewFile }: Props) {
  const [status, setStatus] = useState<EnrichmentStatus | null>(null)
  const [results, setResults] = useState<StoredEnrichmentResult[]>([])
  const [statistics, setStatistics] = useState<EnrichmentStatistics>(EMPTY_STATS)
  const [limit, setLimit] = useState('')
  const [query, setQuery] = useState('')
  const [error, setError] = useState('')
  const [downloading, setDownloading] = useState(false)
  const [reviewing, setReviewing] = useState(false)
  const linkedinReady = contacts.filter((contact) => Boolean(contact.linkedin_url)).length

  async function refresh() {
    const [nextStatus, page] = await Promise.all([
      api.enrichmentStatus(batch.batch_id),
      api.enrichmentResults(batch.batch_id),
    ])
    setStatus(nextStatus)
    setResults(page.items)
    setStatistics(page.statistics)
  }

  useEffect(() => {
    void refresh().catch((caught) => setError(caught instanceof Error ? caught.message : 'Phase 2 status failed.'))
  }, [batch.batch_id])

  useEffect(() => {
    if (status?.status !== 'running') return
    const timer = window.setInterval(() => {
      void refresh().catch((caught) => setError(caught instanceof Error ? caught.message : 'Live Phase 2 status failed.'))
    }, 1000)
    return () => window.clearInterval(timer)
  }, [status?.status, status?.processed, batch.batch_id])

  const rows = useMemo(() => {
    const resultMap = new Map(results.map((item) => [item.row_id, item]))
    const normalized = query.trim().toLocaleLowerCase()
    return contacts
      .map((contact) => ({ contact, stored: resultMap.get(contact.row_id) }))
      .filter(({ contact, stored }) => {
        if (!normalized) return true
        const result = stored?.result
        return [contact.name, contact.organization, result?.role, result?.department_bucket, result?.sub_function]
          .filter(Boolean)
          .some((value) => String(value).toLocaleLowerCase().includes(normalized))
      })
  }, [contacts, results, query])

  async function start() {
    setError('')
    const parsed = limit.trim() ? Number.parseInt(limit, 10) : undefined
    if (parsed !== undefined && (!Number.isFinite(parsed) || parsed < 1)) {
      setError('Enter a contact limit of 1 or more, or leave it blank for all contacts.')
      return
    }
    try {
      setStatus(await api.startEnrichment(batch.batch_id, parsed))
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Phase 2 could not start.')
    }
  }

  async function stop() {
    try {
      setStatus(await api.stopEnrichment(batch.batch_id))
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Phase 2 could not be stopped.')
    }
  }

  async function download() {
    setDownloading(true)
    setError('')
    try {
      await api.downloadEnrichment(batch.batch_id)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Excel download failed.')
    } finally {
      setDownloading(false)
    }
  }

  function newFile() {
    if (running && !window.confirm('Stop Phase 2 and choose a new file?')) return
    if (running) void api.stopEnrichment(batch.batch_id).catch(() => undefined)
    onNewFile()
  }

  const running = status?.status === 'running'
  const remaining = statistics.pending + statistics.processing
  const batchFinished = statistics.total > 0 && statistics.processed === statistics.total && remaining === 0

  if (reviewing) {
    return <ReviewPage
      batch={batch}
      contacts={contacts}
      results={results}
      statistics={statistics}
      onBack={() => setReviewing(false)}
      onNewFile={onNewFile}
      onSaved={(item, nextStatistics) => {
        setResults((current) => current.map((stored) => stored.row_id === item.row_id ? item : stored))
        setStatistics(nextStatistics)
      }}
    />
  }

  return (
    <div className="app-shell phase-two">
      <header className="topbar">
        <div className="brand"><div className="brand-icon brand-icon--small"><Globe2 size={21} /></div><div><span>ProfileMatch AI · Phase 2</span><strong>Search + AI contact verification</strong></div></div>
        <div className="topbar-actions">
          <button className="button button--ghost" onClick={onBack}><ArrowLeft size={16} />Phase 1</button>
          <button className="button button--ghost" onClick={newFile}><FileSpreadsheet size={16} />New file</button>
          <button className="button button--ghost" disabled={running} onClick={() => setReviewing(true)}><ClipboardCheck size={16} />Review & export</button>
          <button className="button button--dark" disabled={downloading} onClick={() => void download()}><Download size={16} />{downloading ? 'Preparing...' : 'Download Excel'}</button>
        </div>
      </header>

      <main className="main-content">
        <section className="phase2-intro">
          <div><ShieldCheck size={25} /><div><strong>Clear evidence, verified by resilient AI routing</strong><span>Serper gathers focused web, LinkedIn, and local-news results. Groq verifies the role, city, local context, and classification, with OpenRouter retained as a final fallback.</span></div></div>
          <div className="cache-pill">Search once · cache evidence · verify with LLM</div>
        </section>

        {linkedinReady < batch.contact_count ? <div className="phase2-warning"><AlertTriangle size={17} /><div><strong>{batch.contact_count - linkedinReady} contacts do not have a saved LinkedIn profile.</strong><span>Phase 2 can continue using the available identity fields, but uploading the Phase 1 results CSV gives stronger verification and fewer manual-review flags.</span></div></div> : null}

        <section className="file-summary">
          <div><FileSpreadsheet size={22} /><div><strong>{batch.source_file}</strong><span>{batch.contact_count} contacts · {linkedinReady} LinkedIn profiles ready · original columns preserved</span></div></div>
          <div className="phase2-controls">
            <label><span>Contacts to process</span><input type="number" min="1" max={batch.contact_count} value={limit} onChange={(event) => setLimit(event.target.value)} placeholder="All" disabled={running} /></label>
            {running ? <button className="button button--stop" onClick={() => void stop()}><Square size={14} />Stop</button> : <button className="button button--primary" onClick={() => void start()}><Search size={15} />{statistics.processed ? 'Continue Phase 2' : 'Start Phase 2'}</button>}
          </div>
        </section>

        <section className="stats-grid phase2-stats">
          <div className="progress-card"><div><span>Enrichment progress</span><strong>{statistics.progress_percent}%</strong></div><div className="progress-track"><span style={{ width: `${statistics.progress_percent}%` }} /></div><small>{statistics.processed} of {statistics.total} rows processed</small></div>
          <div className="stat"><span>Total</span><strong>{statistics.total}</strong></div>
          <div className="stat stat--found"><span>Completed</span><strong>{statistics.completed}</strong></div>
          <div className="stat stat--missing"><span>Review</span><strong>{statistics.manual_review}</strong></div>
          <div className="stat stat--error"><span>Failed</span><strong>{statistics.failed}</strong></div>
        </section>

        <section className={`live-card live-card--${status?.status ?? 'idle'}`}>
          <div className="live-icon">{running ? <LoaderCircle className="spin" size={22} /> : batchFinished ? <CheckCircle2 size={22} /> : <Globe2 size={22} />}</div>
          <div className="live-copy">
            {running ? <><strong>Searching and verifying live</strong><span>Row {status?.current_row ?? '—'}: {status?.current_name ?? 'Preparing contact'}</span><small>{STAGE_LABELS[status?.stage ?? 'queued'] ?? 'Working on this contact'} · {status?.searches_completed ?? 0}/{status?.searches_total ?? 3} focused evidence searches complete · {status?.source_count ?? 0} public results collected · {status?.verification_provider ?? 'OpenRouter'} verification. Each finished row is saved immediately.</small></> : status?.pause_reason === 'daily_limit_reached' ? <><strong>Verification paused safely</strong><span>{status.last_error ?? "OpenRouter's shared free-model daily request limit was reached."}</span><small>Completed rows are saved. Continue the remaining {remaining} contacts after the provider limit resets.</small></> : status?.pause_reason === 'provider_unavailable' ? <><strong>Verification provider needs attention</strong><span>{status.last_error ?? 'The AI provider cannot accept verification requests.'}</span><small>The active row remains pending and every completed result is saved. Correct the provider issue, then continue the remaining {remaining} contacts.</small></> : batchFinished ? <><strong>Phase 2 verification finished</strong><span>{statistics.completed} completed · {statistics.manual_review} require review · {statistics.failed} failed</span><small>Rows are flagged when LinkedIn, the department category, or its closest sub-function is missing or invalid.</small></> : remaining > 0 ? <><strong>Phase 2 is ready to continue</strong><span>{statistics.processed} saved · {remaining} contacts remaining</span><small>Choose a contact limit or leave it blank to process every remaining contact, then select Continue Phase 2.</small></> : <><strong>Phase 2 is ready</strong><span>Choose a custom limit or process all contacts.</span><small>The LLM assigns the closest approved department and sub-function from supported work evidence. Invalid or missing classification and LinkedIn information is flagged.</small></>}
          </div>
        </section>

        {error ? <div className="alert alert--error page-alert"><AlertTriangle size={17} />{error}</div> : null}

        <section className="results-card">
          <div className="results-heading"><div><h2>Enrichment results</h2><span>Role, location, classification, local context, evidence, and manual-review status</span></div><label><Search size={16} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search enriched contacts" /></label></div>
          <div className="table-wrap phase2-table">
            <table>
              <thead><tr><th>Row</th><th>Contact</th><th>LLM-verified role and background</th><th>Classification</th><th>Local context / evidence</th><th>Flag</th><th>Status</th></tr></thead>
              <tbody>
                {rows.map(({ contact, stored }) => {
                  const result = stored?.result
                  return <tr key={contact.row_id} className={contact.row_id === status?.current_row ? 'active-row' : ''}>
                    <td>{contact.row_id}</td>
                    <td><strong>{contact.name}</strong><small className="contact-meta">{contact.organization}</small>{result?.city ? <small className="contact-meta">{result.city}{result.state ? `, ${result.state}` : ''}</small> : null}</td>
                    <td><strong>{result?.role ?? 'Role not verified'}</strong>{result?.background_summary ? <small className="result-note">{result.background_summary}</small> : <small className="result-note">No supported role summary yet.</small>}</td>
                    <td><strong>{result?.department_bucket ?? 'Needs review — no bucket assigned'}</strong><small className="contact-meta">{result?.sub_function ?? 'No supported sub-function'}</small></td>
                    <td>{result?.local_context ?? 'No supported local-news context; left blank.'}{result?.sources?.[0] ? <a className="profile-link evidence-link" href={result.sources[0]} target="_blank" rel="noreferrer"><ExternalLink size={12} />Open evidence</a> : null}</td>
                    <td>{result?.manual_review || stored?.status === 'failed' ? <><span className="result-pill result-pill--manual_review"><AlertTriangle size={13} />YES</span><small className="result-note">{result?.review_reason ?? stored?.error ?? 'Manual review required'}</small></> : stored?.status === 'completed' ? <span className="result-pill result-pill--completed"><CheckCircle2 size={13} />NO</span> : <span className="contact-meta">—</span>}</td>
                    <td><span className={`result-pill result-pill--${stored?.status ?? 'pending'}`}>{stored?.status === 'completed' ? <CheckCircle2 size={13} /> : stored?.status === 'failed' ? <AlertTriangle size={13} /> : <LoaderCircle className={stored?.status === 'processing' ? 'spin' : ''} size={13} />}{stored?.status === 'manual_review' ? 'Review required' : stored?.status ?? 'pending'}</span>{result ? <small className="result-note">{result.verification_method === 'human_reviewed' ? 'Human reviewed' : result.verification_method?.startsWith('serper_groq') ? 'Groq verified' : result.verification_method?.startsWith('serper_deepseek') ? 'DeepSeek verified' : result.verification_method?.startsWith('serper_openrouter') ? 'OpenRouter verified' : 'AI verified'}</small> : stored?.error ? <small className="result-note">{stored.error}</small> : null}</td>
                  </tr>
                })}
              </tbody>
            </table>
          </div>
        </section>
      </main>
    </div>
  )
}
