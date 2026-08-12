export interface Health {
  status: string
  phase: number
  workflow: string
  serper_configured: boolean
  openrouter_configured?: boolean
  deepseek_configured?: boolean
  verification_provider?: string
  phase2_enabled?: boolean
}

export interface EvidenceItem {
  evidence_type: string
  source_url: string
  source_title: string | null
  extracted_text: string | null
  source_type: string
  confidence: number
  published_date: string | null
}

export interface EnrichmentResult {
  name: string
  organization: string | null
  linkedin_url: string | null
  role: string | null
  background_summary: string | null
  city: string | null
  state: string | null
  department_raw: string | null
  department_bucket: string | null
  sub_function: string | null
  local_context: string | null
  organization_website: string | null
  evidence: EvidenceItem[]
  sources: string[]
  identity_confidence: number
  role_confidence: number
  organization_confidence: number
  location_confidence: number
  department_confidence: number
  sub_function_confidence: number
  local_context_confidence: number
  overall_confidence: number
  profile_complete: boolean
  manual_review: boolean
  review_reason: string | null
  status: 'completed' | 'manual_review' | 'failed'
  verification_method: string | null
}

export interface StoredEnrichmentResult {
  row_id: number
  status: 'pending' | 'processing' | 'completed' | 'manual_review' | 'failed'
  result: EnrichmentResult | null
  error: string | null
  started_at: string | null
  completed_at: string | null
  updated_at: string
}

export interface EnrichmentReviewUpdate {
  role: string | null
  background_summary: string | null
  city: string | null
  state: string | null
  department_bucket: string | null
  sub_function: string | null
  local_context: string | null
  linkedin_url: string | null
  manual_review: boolean
  review_reason: string | null
}

export interface EnrichmentStatistics {
  total: number
  pending: number
  processing: number
  processed: number
  completed: number
  manual_review: number
  failed: number
  progress_percent: number
}

export interface EnrichmentStatus {
  status: 'idle' | 'running' | 'complete' | 'stopped' | 'failed'
  batch_id: string | null
  total: number
  processed: number
  completed: number
  manual_review: number
  failed: number
  current_row: number | null
  current_name: string | null
  stage: string | null
  searches_completed: number
  searches_total: number
  source_count: number
  verification_provider: string | null
  pause_reason: string | null
  started_at: string | null
  completed_at: string | null
  last_error: string | null
  statistics?: EnrichmentStatistics
}

export interface Statistics {
  total: number
  processed: number
  pending: number
  found: number
  not_found: number
  errors: number
  flagged: number
  progress_percent: number
}

export interface Contact {
  row_id: number
  source_row: number
  name: string | null
  organization: string | null
  title: string | null
  email: string | null
  location: string | null
  linkedin_url: string | null
  lookup_status: 'pending' | 'found' | 'not_found' | 'error'
  validation_note: string | null
  match_confidence: number
  flagged: boolean
  flag_reason: string | null
  updated_at: string
  original_data: Record<string, unknown>
}

export interface UploadBatch {
  batch_id: string
  created_at: string
  source_file: string
  size_bytes: number
  contact_count: number
  headers: string[]
  name_column: string
  organization_column: string
  title_column: string | null
  email_column: string | null
  location_column: string | null
  linkedin_column: string | null
  file_found_count?: number
  cached_found_count?: number
  resumed_found_count: number
  phase1_complete?: boolean
  phase1_reused_file_count?: number
  phase1_reused_batch_id?: string | null
  statistics: Statistics
}

export interface LiveResult {
  row_id: number
  name: string | null
  organization: string | null
  status: 'found' | 'not_found' | 'error'
  linkedin_url: string | null
  confidence: number
  note: string | null
  flagged: boolean
  flag_reason: string | null
}

export interface LookupStatus {
  status: 'idle' | 'running' | 'complete' | 'stopped' | 'failed'
  batch_id: string | null
  started_at: string | null
  completed_at: string | null
  total: number
  processed: number
  found: number
  not_found: number
  errors: number
  current_row: number | null
  current_name: string | null
  current_organization: string | null
  stage: 'queued' | 'searching_linkedin' | 'waiting_to_retry' | 'searching_linkedin_retry' | 'verifying_candidates_with_llm' | null
  recent_results: LiveResult[]
  last_error: string | null
}
