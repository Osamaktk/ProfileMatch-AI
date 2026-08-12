import type {
  Contact,
  EnrichmentStatistics,
  EnrichmentReviewUpdate,
  EnrichmentStatus,
  Health,
  LookupStatus,
  Statistics,
  StoredEnrichmentResult,
  UploadBatch,
} from './types'


async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(path, options)
  if (!response.ok) {
    let message = `Request failed (${response.status})`
    try {
      const payload = (await response.json()) as { detail?: string | Array<{ msg?: string }> }
      if (typeof payload.detail === 'string') message = payload.detail
      else if (Array.isArray(payload.detail)) message = payload.detail[0]?.msg ?? message
    } catch {
      // Preserve the HTTP status message for non-JSON errors.
    }
    throw new Error(message)
  }
  return (await response.json()) as T
}


export const api = {
  health: () => request<Health>('/api/health'),
  upload: (file: File) => {
    const body = new FormData()
    body.append('file', file)
    return request<UploadBatch>('/api/uploads', { method: 'POST', body })
  },
  contacts: (batchId: string) =>
    request<{ items: Contact[]; total: number }>(
      `/api/contacts?batch_id=${encodeURIComponent(batchId)}`,
    ),
  statistics: (batchId: string) =>
    request<Statistics>(`/api/statistics?batch_id=${encodeURIComponent(batchId)}`),
  start: (batchId: string) =>
    request<LookupStatus>('/api/linkedin/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ batch_id: batchId, retry_errors: true }),
    }),
  status: (batchId: string) =>
    request<LookupStatus>(`/api/linkedin/status?batch_id=${encodeURIComponent(batchId)}`),
  stop: (batchId: string) =>
    request<LookupStatus>(`/api/linkedin/stop?batch_id=${encodeURIComponent(batchId)}`, {
      method: 'POST',
    }),
  exportCsv: (batchId: string) =>
    request<{ filename: string }>(`/api/export?batch_id=${encodeURIComponent(batchId)}`, {
      method: 'POST',
    }),
  downloadCsv: async (batchId: string) => {
    const response = await fetch(`/api/reports/csv?batch_id=${encodeURIComponent(batchId)}`)
    if (!response.ok) throw new Error('The CSV could not be downloaded.')
    const blob = await response.blob()
    const disposition = response.headers.get('content-disposition') ?? ''
    const filename = disposition.match(/filename="?([^";]+)"?/i)?.[1] ?? 'linkedin_results.csv'
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    link.click()
    URL.revokeObjectURL(url)
  },
  startEnrichment: (batchId: string, limit?: number) =>
    request<EnrichmentStatus>('/api/v1/enrichment/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ batch_id: batchId, limit: limit ?? null, retry_failed: true }),
    }),
  enrichmentStatus: (batchId: string) =>
    request<EnrichmentStatus>(`/api/v1/enrichment/status/${encodeURIComponent(batchId)}`),
  enrichmentResults: (batchId: string) =>
    request<{ items: StoredEnrichmentResult[]; total: number; statistics: EnrichmentStatistics }>(
      `/api/v1/enrichment/results/${encodeURIComponent(batchId)}`,
    ),
  stopEnrichment: (batchId: string) =>
    request<EnrichmentStatus>(`/api/v1/enrichment/stop/${encodeURIComponent(batchId)}`, {
      method: 'POST',
    }),
  updateEnrichment: (batchId: string, rowId: number, update: EnrichmentReviewUpdate) =>
    request<{ item: StoredEnrichmentResult; statistics: EnrichmentStatistics }>(
      `/api/v1/enrichment/results/${encodeURIComponent(batchId)}/${rowId}`,
      {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(update),
      },
    ),
  enrichmentFile: async (batchId: string) => {
    const response = await fetch(`/api/v1/enrichment/report/${encodeURIComponent(batchId)}`)
    if (!response.ok) throw new Error('The enriched Excel workbook could not be downloaded.')
    const blob = await response.blob()
    const disposition = response.headers.get('content-disposition') ?? ''
    const filename = disposition.match(/filename="?([^";]+)"?/i)?.[1] ?? 'contacts_enriched.xlsx'
    return { blob, filename }
  },
  downloadEnrichment: async (batchId: string) => {
    const { blob, filename } = await api.enrichmentFile(batchId)
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    link.click()
    URL.revokeObjectURL(url)
  },
}
