import { useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  ExternalLink,
  FileDown,
  FileSpreadsheet,
  Link2,
  LoaderCircle,
  Save,
  Search,
  Share2,
} from 'lucide-react'

import { api } from './api'
import type {
  Contact,
  EnrichmentReviewUpdate,
  EnrichmentStatistics,
  StoredEnrichmentResult,
  UploadBatch,
} from './types'


const TAXONOMY: Record<string, string[]> = {
  "Clerk's Office": ['Meeting packets', 'Ordinance approval', 'Minutes archive', 'Public records requests'],
  'Zoning and Planning': ['Permit applications', 'Site plans', 'Review routing', 'GIS lookup', 'Planning Commission approval'],
  Accounting: ['AP Invoice Processing', 'Purchasing', 'Contract Routing'],
  'Public Works': ['Work Orders', 'Capital Projects', 'Asset Documentation', 'Fleet Maintenance', 'Road Projects'],
  'Human Resources': ['Personnel Files', 'Hiring', 'Onboarding', 'Performance Reviews'],
  'Public Records': ['FOIA/Open Records', 'Automated Routing', 'Deadline Tracking', 'Redaction Workflow'],
  IT: ['Help Desk Requests', 'Change Management', 'Asset Documentation', 'SOP Library'],
  Forms: ['Permit Request', 'Vacation Request', 'Public Records Request', 'Citizen Complaint', 'Work Order', 'Purchase Request'],
}

type Filter = 'all' | 'review' | 'complete'

interface Props {
  batch: UploadBatch
  contacts: Contact[]
  results: StoredEnrichmentResult[]
  statistics: EnrichmentStatistics
  onBack: () => void
  onNewFile: () => void
  onSaved: (item: StoredEnrichmentResult, statistics: EnrichmentStatistics) => void
}

function draftFrom(stored: StoredEnrichmentResult | undefined): EnrichmentReviewUpdate | null {
  const result = stored?.result
  if (!result) return null
  return {
    role: result.role,
    background_summary: result.background_summary,
    city: result.city,
    state: result.state,
    department_bucket: result.department_bucket,
    sub_function: result.sub_function,
    local_context: result.local_context,
    linkedin_url: result.linkedin_url,
    manual_review: result.manual_review,
    review_reason: result.review_reason,
  }
}

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}

function suggestedOutputName(sourceFile: string): string {
  const stem = sourceFile.replace(/\.[^.]+$/, '').replace(/_(linkedin_results|enriched)$/i, '')
  return `${stem || 'contacts'}_enriched.xlsx`
}

export function ReviewPage({ batch, contacts, results, statistics, onBack, onNewFile, onSaved }: Props) {
  const resultMap = useMemo(() => new Map(results.map((item) => [item.row_id, item])), [results])
  const firstResult = results.find((item) => item.status === 'manual_review' && item.result)?.row_id
    ?? results.find((item) => item.result)?.row_id
    ?? contacts[0]?.row_id
  const [selectedRow, setSelectedRow] = useState<number | undefined>(firstResult)
  const [draft, setDraft] = useState<EnrichmentReviewUpdate | null>(() => draftFrom(resultMap.get(firstResult ?? -1)))
  const [savedDraft, setSavedDraft] = useState<EnrichmentReviewUpdate | null>(() => draftFrom(resultMap.get(firstResult ?? -1)))
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState<Filter>('all')
  const [saving, setSaving] = useState(false)
  const [fileAction, setFileAction] = useState<'save' | 'share' | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const dirty = JSON.stringify(draft) !== JSON.stringify(savedDraft)
  const selectedContact = contacts.find((contact) => contact.row_id === selectedRow)
  const selectedStored = selectedRow === undefined ? undefined : resultMap.get(selectedRow)

  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (!dirty) return
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  const visibleRows = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase()
    return contacts.filter((contact) => {
      const stored = resultMap.get(contact.row_id)
      if (filter === 'review' && stored?.status !== 'manual_review' && stored?.status !== 'failed') return false
      if (filter === 'complete' && stored?.status !== 'completed') return false
      if (!normalized) return true
      const result = stored?.result
      return [contact.name, contact.organization, result?.role, result?.department_bucket, result?.sub_function]
        .filter(Boolean)
        .some((value) => String(value).toLocaleLowerCase().includes(normalized))
    })
  }, [contacts, filter, query, resultMap])

  function chooseRow(rowId: number) {
    if (dirty && !window.confirm('Discard the unsaved changes for this contact?')) return
    const next = draftFrom(resultMap.get(rowId))
    setSelectedRow(rowId)
    setDraft(next)
    setSavedDraft(next)
    setError('')
    setNotice('')
  }

  function leaveReview() {
    if (dirty && !window.confirm('Leave without saving your changes?')) return
    onBack()
  }

  function newFile() {
    if (dirty && !window.confirm('Start a new file without saving these changes?')) return
    onNewFile()
  }

  function update<K extends keyof EnrichmentReviewUpdate>(key: K, value: EnrichmentReviewUpdate[K]) {
    setDraft((current) => current ? { ...current, [key]: value } : current)
    setNotice('')
  }

  async function saveReview() {
    if (!draft || selectedRow === undefined) return
    setSaving(true)
    setError('')
    setNotice('')
    try {
      const response = await api.updateEnrichment(batch.batch_id, selectedRow, draft)
      onSaved(response.item, response.statistics)
      const next = draftFrom(response.item)
      setDraft(next)
      setSavedDraft(next)
      setNotice('Review saved. The Excel workbook has been rebuilt with your changes.')
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'The review could not be saved.')
    } finally {
      setSaving(false)
    }
  }

  async function saveAs() {
    setFileAction('save')
    setError('')
    setNotice('')
    try {
      type WritableFile = { write: (value: Blob) => Promise<void>; close: () => Promise<void> }
      type FileHandle = { createWritable: () => Promise<WritableFile> }
      type PickerWindow = Window & {
        showSaveFilePicker?: (options: Record<string, unknown>) => Promise<FileHandle>
      }
      const picker = (window as PickerWindow).showSaveFilePicker
      if (picker) {
        // Ask for the destination while the button click still carries browser
        // user activation, then generate and write the current workbook.
        const handle = await picker.call(window, {
          suggestedName: suggestedOutputName(batch.source_file),
          types: [{
            description: 'Excel workbook',
            accept: { 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': ['.xlsx'] },
          }],
        })
        const { blob, filename } = await api.enrichmentFile(batch.batch_id)
        const writable = await handle.createWritable()
        await writable.write(blob)
        await writable.close()
        setNotice(`Saved ${filename} to the selected location.`)
      } else {
        const { blob, filename } = await api.enrichmentFile(batch.batch_id)
        downloadBlob(blob, filename)
        setNotice('Your browser used its download location. Enable “Ask where to save” in browser settings to choose every time.')
      }
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === 'AbortError') return
      setError(caught instanceof Error ? caught.message : 'The workbook could not be saved.')
    } finally {
      setFileAction(null)
    }
  }

  async function share() {
    setFileAction('share')
    setError('')
    setNotice('')
    try {
      const { blob, filename } = await api.enrichmentFile(batch.batch_id)
      const file = new File([blob], filename, { type: blob.type })
      const shareData = { title: 'ProfileMatch AI contact enrichment', text: 'Reviewed contact enrichment workbook', files: [file] }
      if (navigator.share && (!navigator.canShare || navigator.canShare(shareData))) {
        await navigator.share(shareData)
        setNotice('Workbook shared successfully.')
      } else {
        downloadBlob(blob, filename)
        setNotice('File sharing is not supported by this browser, so the workbook was downloaded for you to attach or send.')
      }
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === 'AbortError') return
      setError(caught instanceof Error ? caught.message : 'The workbook could not be shared.')
    } finally {
      setFileAction(null)
    }
  }

  const selectedIndex = visibleRows.findIndex((contact) => contact.row_id === selectedRow)
  const subFunctions = draft?.department_bucket ? TAXONOMY[draft.department_bucket] ?? [] : []

  return (
    <div className="app-shell review-page">
      <header className="topbar review-topbar">
        <div className="brand"><div className="brand-icon brand-icon--small"><FileSpreadsheet size={21} /></div><div><span>ProfileMatch AI · Review workspace</span><strong>Preview, edit and export results</strong></div></div>
        <div className="topbar-actions">
          <button className="button button--ghost" onClick={leaveReview}><ArrowLeft size={16} />Processing</button>
          <button className="button button--ghost" disabled={fileAction !== null} onClick={() => void share()}>{fileAction === 'share' ? <LoaderCircle className="spin" size={16} /> : <Share2 size={16} />}Share</button>
          <button className="button button--dark" disabled={fileAction !== null} onClick={() => void saveAs()}>{fileAction === 'save' ? <LoaderCircle className="spin" size={16} /> : <FileDown size={16} />}Save as…</button>
        </div>
      </header>

      <main className="review-shell">
        <section className="review-summary">
          <div><span>Reviewed file</span><strong>{batch.source_file}</strong><small>Edits are persisted and included in the next Excel export.</small></div>
          <div className="review-metrics">
            <div><strong>{statistics.processed}</strong><span>Available</span></div>
            <div className="metric-complete"><strong>{statistics.completed}</strong><span>Complete</span></div>
            <div className="metric-review"><strong>{statistics.manual_review}</strong><span>Need review</span></div>
            <div><strong>{statistics.pending + statistics.processing}</strong><span>Waiting</span></div>
          </div>
        </section>

        {error ? <div className="alert alert--error review-message"><AlertTriangle size={17} />{error}</div> : null}
        {notice ? <div className="alert alert--success review-message"><CheckCircle2 size={17} />{notice}</div> : null}

        <section className="review-workspace">
          <aside className="review-list">
            <div className="review-list-head">
              <div><strong>Contact results</strong><span>{visibleRows.length} shown</span></div>
              <label><Search size={15} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search contacts" /></label>
              <div className="review-filters">
                <button className={filter === 'all' ? 'active' : ''} onClick={() => setFilter('all')}>All</button>
                <button className={filter === 'review' ? 'active' : ''} onClick={() => setFilter('review')}>Review</button>
                <button className={filter === 'complete' ? 'active' : ''} onClick={() => setFilter('complete')}>Complete</button>
              </div>
            </div>
            <div className="review-contact-list">
              {visibleRows.map((contact) => {
                const stored = resultMap.get(contact.row_id)
                const result = stored?.result
                return <button key={contact.row_id} className={`review-contact ${selectedRow === contact.row_id ? 'selected' : ''}`} onClick={() => chooseRow(contact.row_id)}>
                  <span className="review-row-number">{contact.row_id}</span>
                  <span className="review-contact-copy"><strong>{contact.name || 'Name missing'}</strong><small>{result?.role || contact.title || contact.organization || 'Awaiting result'}</small></span>
                  {stored?.status === 'completed' ? <CheckCircle2 className="review-state complete" size={16} /> : stored?.status === 'manual_review' || stored?.status === 'failed' ? <AlertTriangle className="review-state flagged" size={16} /> : <span className="review-state pending" />}
                </button>
              })}
            </div>
          </aside>

          <div className="review-editor">
            {draft && selectedContact && selectedStored?.result ? <>
              <div className="editor-header">
                <div><span className="eyebrow">Row {selectedContact.row_id} · {selectedStored.status === 'completed' ? 'Ready to export' : 'Human review required'}</span><h1>{selectedContact.name}</h1><p>{selectedContact.organization || 'Organization not supplied'}{selectedContact.email ? ` · ${selectedContact.email}` : ''}</p></div>
                <div className="editor-nav"><button disabled={selectedIndex <= 0} onClick={() => chooseRow(visibleRows[selectedIndex - 1].row_id)}><ChevronLeft size={17} /></button><span>{selectedIndex + 1} / {visibleRows.length}</span><button disabled={selectedIndex < 0 || selectedIndex >= visibleRows.length - 1} onClick={() => chooseRow(visibleRows[selectedIndex + 1].row_id)}><ChevronRight size={17} /></button></div>
              </div>

              <div className="editor-body">
                <section className="editor-section">
                  <div className="section-title"><div><strong>Verified profile</strong><span>Edit the final role and location shown in the workbook.</span></div>{draft.linkedin_url ? <a href={draft.linkedin_url} target="_blank" rel="noreferrer"><Link2 size={14} />LinkedIn<ExternalLink size={12} /></a> : null}</div>
                  <div className="field-grid">
                    <label className="field field--wide"><span>Role / title</span><input value={draft.role ?? ''} onChange={(event) => update('role', event.target.value || null)} /></label>
                    <label className="field"><span>City</span><input value={draft.city ?? ''} onChange={(event) => update('city', event.target.value || null)} /></label>
                    <label className="field"><span>State</span><input value={draft.state ?? ''} onChange={(event) => update('state', event.target.value || null)} /></label>
                    <label className="field field--full"><span>LinkedIn profile URL</span><input value={draft.linkedin_url ?? ''} onChange={(event) => update('linkedin_url', event.target.value || null)} placeholder="https://www.linkedin.com/in/..." /></label>
                    <label className="field field--full"><span>Role and background · 2–3 lines</span><textarea rows={4} value={draft.background_summary ?? ''} onChange={(event) => update('background_summary', event.target.value || null)} /></label>
                  </div>
                </section>

                <section className="editor-section">
                  <div className="section-title"><div><strong>Classification</strong><span>Only approved department buckets and sub-functions are available.</span></div></div>
                  <div className="field-grid field-grid--two">
                    <label className="field"><span>Department bucket</span><select value={draft.department_bucket ?? ''} onChange={(event) => { update('department_bucket', event.target.value || null); update('sub_function', null) }}><option value="">Select department</option>{Object.keys(TAXONOMY).map((bucket) => <option key={bucket}>{bucket}</option>)}</select></label>
                    <label className="field"><span>Closest sub-function</span><select value={draft.sub_function ?? ''} disabled={!draft.department_bucket} onChange={(event) => update('sub_function', event.target.value || null)}><option value="">Select sub-function</option>{subFunctions.map((item) => <option key={item}>{item}</option>)}</select></label>
                    <label className="field field--full"><span>Relevant local context</span><textarea rows={3} value={draft.local_context ?? ''} onChange={(event) => update('local_context', event.target.value || null)} /></label>
                  </div>
                </section>

                <section className="editor-section evidence-section">
                  <div className="section-title"><div><strong>Evidence preview</strong><span>Open the public sources used for this result.</span></div><span className="source-count">{selectedStored.result.evidence.length} sources</span></div>
                  <div className="evidence-grid">
                    {selectedStored.result.evidence.length ? selectedStored.result.evidence.map((item, index) => <a key={`${item.source_url}-${index}`} href={item.source_url} target="_blank" rel="noreferrer"><span>{item.source_type.replaceAll('_', ' ')}</span><strong>{item.source_title || item.source_url}</strong><small>{item.extracted_text || 'Open this source to review the evidence.'}</small><ExternalLink size={14} /></a>) : <div className="evidence-empty">No saved evidence URLs are available for this result.</div>}
                  </div>
                </section>

                <section className={`review-decision ${draft.manual_review ? 'is-flagged' : 'is-approved'}`}>
                  <div><span className="decision-icon">{draft.manual_review ? <AlertTriangle size={18} /> : <CheckCircle2 size={18} />}</span><div><strong>{draft.manual_review ? 'Keep flagged for manual review' : 'Approve as complete'}</strong><small>{draft.manual_review ? 'The row remains visible in the review queue.' : 'The row will be marked complete in the workbook.'}</small></div></div>
                  <label className="switch"><input type="checkbox" checked={draft.manual_review} onChange={(event) => update('manual_review', event.target.checked)} /><span /></label>
                  {draft.manual_review ? <label className="field decision-reason"><span>Flag reason</span><textarea rows={2} value={draft.review_reason ?? ''} onChange={(event) => update('review_reason', event.target.value || null)} /></label> : null}
                </section>
              </div>

              <div className="editor-footer">
                <span>{dirty ? 'Unsaved changes' : selectedStored.result.verification_method === 'human_reviewed' ? 'Human-reviewed and saved' : 'No unsaved changes'}</span>
                <div><button className="button button--ghost" disabled={!dirty || saving} onClick={() => { const reset = draftFrom(selectedStored); setDraft(reset); setSavedDraft(reset) }}>Reset</button><button className="button button--primary" disabled={!dirty || saving} onClick={() => void saveReview()}>{saving ? <LoaderCircle className="spin" size={16} /> : <Save size={16} />}{saving ? 'Saving…' : 'Save review'}</button></div>
              </div>
            </> : <div className="review-empty"><FileSpreadsheet size={38} /><h2>Result not available yet</h2><p>This contact has not completed Phase 2. Return to processing to verify it, then come back here to edit and approve the result.</p><button className="button button--primary" onClick={leaveReview}>Return to processing</button></div>}
          </div>
        </section>
        <button className="review-new-file" onClick={newFile}>Start with a different file</button>
      </main>
    </div>
  )
}
