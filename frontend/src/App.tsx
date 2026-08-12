import { useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  CheckCircle2,
  Download,
  ExternalLink,
  FileSpreadsheet,
  Link,
  LoaderCircle,
  RefreshCcw,
  Search,
  Square,
  Upload,
  XCircle,
} from 'lucide-react'

import { api } from './api'
import { Phase2 } from './Phase2'
import type { Contact, Health, LookupStatus, Statistics, UploadBatch } from './types'


const EMPTY_STATS: Statistics = {
  total: 0,
  processed: 0,
  pending: 0,
  found: 0,
  not_found: 0,
  errors: 0,
  flagged: 0,
  progress_percent: 0,
}


function statusLabel(contact: Contact): string {
  if (contact.lookup_status === 'found') return 'Profile found'
  if (contact.lookup_status === 'not_found') return 'User LinkedIn not found'
  if (contact.lookup_status === 'error') return 'Lookup error'
  return 'Waiting'
}


export default function App() {
  const [health, setHealth] = useState<Health | null>(null)
  const [file, setFile] = useState<File | null>(null)
  const [batch, setBatch] = useState<UploadBatch | null>(null)
  const [contacts, setContacts] = useState<Contact[]>([])
  const [statistics, setStatistics] = useState<Statistics>(EMPTY_STATS)
  const [lookup, setLookup] = useState<LookupStatus | null>(null)
  const [uploading, setUploading] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [query, setQuery] = useState('')
  const [error, setError] = useState('')
  const [phaseTwo, setPhaseTwo] = useState(false)

  useEffect(() => {
    api.health()
      .then(setHealth)
      .catch(() => setError('The local service is not responding.'))
  }, [])

  useEffect(() => {
    if (!batch || lookup?.status !== 'running') return
    const timer = window.setInterval(() => {
      void api.status(batch.batch_id)
        .then(async (next) => {
          const changed = next.processed !== lookup.processed || next.status !== lookup.status
          setLookup(next)
          if (changed) {
            const [page, stats] = await Promise.all([
              api.contacts(batch.batch_id),
              api.statistics(batch.batch_id),
            ])
            setContacts(page.items)
            setStatistics(stats)
          }
        })
        .catch((caught) => setError(caught instanceof Error ? caught.message : 'Live status failed.'))
    }, 800)
    return () => window.clearInterval(timer)
  }, [batch, lookup?.processed, lookup?.status])

  const visibleContacts = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase()
    if (!normalized) return contacts
    return contacts.filter((contact) =>
      [contact.name, contact.organization, contact.title, contact.email, contact.location, contact.linkedin_url]
        .filter(Boolean)
        .some((value) => String(value).toLocaleLowerCase().includes(normalized)),
    )
  }, [contacts, query])

  async function uploadAndStart() {
    if (!file) return
    if (!['.csv', '.xlsx'].some((suffix) => file.name.toLocaleLowerCase().endsWith(suffix))) {
      setError('Choose a CSV or XLSX file.')
      return
    }
    setUploading(true)
    setError('')
    setPhaseTwo(false)
    try {
      const uploaded = await api.upload(file)
      const page = await api.contacts(uploaded.batch_id)
      setBatch(uploaded)
      setContacts(page.items)
      setStatistics(uploaded.statistics)
      if (uploaded.phase1_complete) {
        setLookup({
          status: 'complete',
          batch_id: uploaded.batch_id,
          started_at: null,
          completed_at: null,
          total: uploaded.statistics.total,
          processed: uploaded.statistics.processed,
          found: uploaded.statistics.found,
          not_found: uploaded.statistics.not_found,
          errors: uploaded.statistics.errors,
          current_row: null,
          current_name: null,
          current_organization: null,
          stage: null,
          recent_results: [],
          last_error: null,
        })
        setPhaseTwo(true)
        try {
          await api.startEnrichment(uploaded.batch_id)
        } catch (caught) {
          setError(
            caught instanceof Error
              ? `Phase 1 was restored, but Phase 2 could not start: ${caught.message}`
              : 'Phase 1 was restored, but Phase 2 could not start.',
          )
        }
      } else {
        setLookup(await api.start(uploaded.batch_id))
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'The upload failed.')
    } finally {
      setUploading(false)
    }
  }

  async function startLookup() {
    if (!batch) return
    setError('')
    try {
      setLookup(await api.start(batch.batch_id))
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'The lookup could not start.')
    }
  }

  async function stopLookup() {
    if (!batch) return
    try {
      setLookup(await api.stop(batch.batch_id))
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'The lookup could not be stopped.')
    }
  }

  async function downloadCsv() {
    if (!batch) return
    setExporting(true)
    setError('')
    try {
      await api.exportCsv(batch.batch_id)
      await api.downloadCsv(batch.batch_id)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'The CSV export failed.')
    } finally {
      setExporting(false)
    }
  }

  function newFile() {
    if (lookup?.status === 'running' && !window.confirm('Stop the active lookup and choose a new file?')) return
    if (batch && lookup?.status === 'running') void api.stop(batch.batch_id).catch(() => undefined)
    setBatch(null)
    setContacts([])
    setStatistics(EMPTY_STATS)
    setLookup(null)
    setFile(null)
    setQuery('')
    setError('')
    setPhaseTwo(false)
  }

  if (!batch) {
    return (
      <main className="upload-page">
        <section className="upload-card">
          <div className="phase-label">Phase 1</div>
          <div className="brand-icon"><Link size={30} /></div>
          <h1>ProfileMatch AI</h1>
          <p>Discover LinkedIn profiles, enrich public contact information, and review every result before export.</p>

          <label className="file-drop">
            <Upload size={24} />
            <strong>{file ? file.name : 'Choose a CSV or XLSX file'}</strong>
            <span>Required: Name and Organization. Helpful: Title, Email, City or Location.</span>
            <input
              type="file"
              accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            />
          </label>

          {file ? <div className="selected-file"><FileSpreadsheet size={17} /><span>{file.name}</span><small>{(file.size / 1024).toFixed(1)} KB</small></div> : null}
          {error ? <div className="alert alert--error"><AlertTriangle size={17} />{error}</div> : null}

          <button className="button button--primary button--wide" disabled={!file || uploading || !health?.serper_configured} onClick={() => void uploadAndStart()}>
            {uploading ? <><LoaderCircle className="spin" size={18} />Reading contacts…</> : <><Search size={18} />Upload and start</>}
          </button>

          <div className="privacy-note">
            <CheckCircle2 size={16} />
            <span>Phase 1 searches name and organization, then name and LinkedIn, followed by exact email. Retries also check Last/First name order.</span>
          </div>
          <div className="service-line"><span className={health?.serper_configured ? 'dot dot--ready' : 'dot'} />{health?.serper_configured ? 'Serper is configured and ready' : 'Serper key is not configured'}</div>
        </section>
      </main>
    )
  }

  const isRunning = lookup?.status === 'running'
  const currentRow = lookup?.current_row

  if (phaseTwo) {
    return <Phase2 batch={batch} contacts={contacts} onBack={() => setPhaseTwo(false)} onNewFile={newFile} />
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand"><div className="brand-icon brand-icon--small"><Link size={21} /></div><div><span>Phase 1</span><strong>LinkedIn profile finder</strong></div></div>
        <div className="topbar-actions">
          <div className="service-ready"><span className="dot dot--ready" />Serper ready</div>
          {!isRunning ? <button className="button button--primary" onClick={() => setPhaseTwo(true)}>Phase 2</button> : null}
          <button className="button button--ghost" onClick={newFile}><RefreshCcw size={16} />New file</button>
          <button className="button button--dark" disabled={exporting} onClick={() => void downloadCsv()}><Download size={16} />{exporting ? 'Preparing…' : 'Download CSV'}</button>
        </div>
      </header>

      <main className="main-content">
        <section className="file-summary">
          <div><FileSpreadsheet size={22} /><div><strong>{batch.source_file}</strong><span>{batch.contact_count} contacts</span></div></div>
          <div className="detected-columns">
            <span>Name: <b>{batch.name_column}</b></span>
            <span>Organization: <b>{batch.organization_column}</b></span>
            {batch.title_column ? <span>Title: <b>{batch.title_column}</b></span> : null}
            {batch.email_column ? <span>Email: <b>{batch.email_column}</b></span> : null}
            {batch.location_column ? <span>Location: <b>{batch.location_column}</b></span> : null}
          </div>
        </section>

        <div className="resume-note"><RefreshCcw size={16} /><span><strong>LinkedIn history checked.</strong> {batch.resumed_found_count > 0 ? `${batch.resumed_found_count} previously validated profile${batch.resumed_found_count === 1 ? ' was' : 's were'} restored and skipped. ` : 'No previously validated profiles were restored. '}Previous misses remain pending and are searched again.</span></div>

        <section className="stats-grid">
          <div className="progress-card"><div><span>Lookup progress</span><strong>{statistics.progress_percent}%</strong></div><div className="progress-track"><span style={{ width: `${statistics.progress_percent}%` }} /></div><small>{statistics.processed} of {statistics.total} rows processed</small></div>
          <div className="stat"><span>Total</span><strong>{statistics.total}</strong></div>
          <div className="stat stat--found"><span>Found</span><strong>{statistics.found}</strong></div>
          <div className="stat stat--missing"><span>Not found</span><strong>{statistics.not_found}</strong></div>
          <div className="stat stat--error"><span>Errors</span><strong>{statistics.errors}</strong></div>
          <div className="stat stat--missing"><span>Flagged</span><strong>{statistics.flagged}</strong></div>
        </section>

        <section className={`live-card live-card--${lookup?.status ?? 'idle'}`}>
          <div className="live-icon">{isRunning ? <LoaderCircle className="spin" size={22} /> : lookup?.status === 'complete' ? <CheckCircle2 size={22} /> : <Search size={22} />}</div>
          <div className="live-copy">
            {isRunning ? <><strong>Finding LinkedIn profiles live</strong><span>Row {currentRow ?? '—'}: {lookup.current_name || 'Name missing'} · {lookup.current_organization || 'Organization missing'}</span><small>{lookup.stage === 'waiting_to_retry' ? 'No profile yet; waiting 2–3 seconds before retrying.' : lookup.stage === 'searching_linkedin_retry' ? 'Retrying with alternative name order.' : lookup.stage === 'verifying_candidates_with_llm' ? 'Verifying the returned profile.' : 'Searching by name, organization, and email.'} Every completed row is saved immediately.</small></> : lookup?.status === 'complete' ? <><strong>LinkedIn lookup finished</strong><span>{statistics.found} profiles available · {statistics.not_found} not found · {statistics.errors} errors · {statistics.flagged} flagged</span><small>Incomplete rows and unresolved LinkedIn profiles are identified in the Flag and Flag Reason columns.</small></> : lookup?.status === 'failed' ? <><strong>Lookup stopped unexpectedly</strong><span>{lookup.last_error}</span></> : <><strong>Lookup is stopped</strong><span>Saved results remain available for download and can be resumed later.</span></>}
          </div>
          <div className="live-action">{isRunning ? <button className="button button--stop" onClick={() => void stopLookup()}><Square size={14} />Stop</button> : statistics.pending > 0 || statistics.errors > 0 ? <button className="button button--primary" onClick={() => void startLookup()}><Search size={15} />Continue</button> : <button className="button button--primary" onClick={() => setPhaseTwo(true)}>Open Phase 2</button>}</div>
        </section>

        {error ? <div className="alert alert--error page-alert"><AlertTriangle size={17} />{error}</div> : null}

        <section className="results-card">
          <div className="results-heading"><div><h2>Live results</h2><span>Every row is written to the downloadable CSV immediately</span></div><label><Search size={16} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search any contact field" /></label></div>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Row</th><th>Person</th><th>Organization</th><th>Result</th><th>Flag</th><th>Confidence</th></tr></thead>
              <tbody>
                {visibleContacts.map((contact) => (
                  <tr key={contact.row_id} className={contact.row_id === currentRow ? 'active-row' : ''}>
                    <td>{contact.row_id}</td>
                    <td><strong>{contact.name || 'Name missing'}</strong>{contact.title ? <small className="contact-meta">{contact.title}</small> : null}</td>
                    <td>{contact.organization || 'Organization missing'}{contact.location ? <small className="contact-meta">{contact.location}</small> : null}</td>
                    <td>
                      {contact.lookup_status === 'found' && contact.linkedin_url ? <a className="profile-link" href={contact.linkedin_url} target="_blank" rel="noreferrer"><Link size={15} />Open profile<ExternalLink size={13} /></a> : <span className={`result-pill result-pill--${contact.lookup_status}`}>{contact.lookup_status === 'error' ? <XCircle size={13} /> : contact.lookup_status === 'not_found' ? <AlertTriangle size={13} /> : <LoaderCircle className={contact.row_id === currentRow ? 'spin' : ''} size={13} />}{statusLabel(contact)}</span>}
                      {contact.validation_note ? <small className="result-note">{contact.validation_note}</small> : null}
                    </td>
                    <td>{contact.flagged ? <><span className="result-pill result-pill--not_found"><AlertTriangle size={13} />Flagged</span>{contact.flag_reason ? <small className="result-note">{contact.flag_reason}</small> : null}</> : '—'}</td>
                    <td>{contact.lookup_status === 'found' ? `${contact.match_confidence}%` : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </main>
    </div>
  )
}
