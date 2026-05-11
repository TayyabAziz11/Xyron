// Xyron — shared TypeScript types (mirrors Pydantic schemas)

export interface SystemMetrics {
  cpu_pct:        number
  cpu_cores:      number
  cpu_freq_mhz:   number | null
  ram_pct:        number
  ram_used_gb:    number
  ram_total_gb:   number
  swap_pct:       number
  disk_pct:       number
  disk_used_gb:   number
  disk_total_gb:  number
  net_bytes_sent: number
  net_bytes_recv: number
  temp_c:         number | null
  uptime_s:       number
  uptime_str:     string
}

export interface ApiResponse<T> {
  success: boolean
  data: T
  message?: string
}

// Commands
export type CommandStatus = 'queued' | 'running' | 'completed' | 'failed'

export interface CommandIntent {
  agent: string
  skill: string
  confidence: string
}

export interface Command {
  id: string
  text: string
  status: CommandStatus
  created_at: string
  updated_at?: string
  result?: string
  error?: string
  agent?: string
  intent?: CommandIntent
  assistant_response?: string
  draft_id?: string   // present when command produced a reviewable draft
  action_url?: string // URL for frontend to open (search/navigation results)
  action_app?: string // desktop app name for frontend to launch
}

// Drafts
export type DraftStatus = 'draft' | 'confirmed' | 'executing' | 'executed' | 'rejected' | 'failed'

export interface Draft {
  id: string
  command_id: string
  draft_type: 'linkedin_post' | 'email' | 'instagram' | 'whatsapp' | string
  title: string
  content: string
  metadata: Record<string, string>
  status: DraftStatus
  created_at: string
  updated_at?: string
  executed_at?: string
  error?: string
  execution_url?: string
}

// Approvals
export type ApprovalStatus = 'pending' | 'approved' | 'rejected'
export type RiskLevel = 'low' | 'medium' | 'high'

export interface Approval {
  id: string
  title: string
  description: string
  action_type: string
  risk_level: RiskLevel
  status: ApprovalStatus
  created_at: string
  file_path?: string
  related_plan?: string
  details?: Record<string, unknown>
}

// Activity
export type ActivityStatus = 'success' | 'error' | 'warning' | 'info'

export interface ActivityEvent {
  id: string
  event_type: string
  description: string
  status: ActivityStatus
  timestamp: string
  source?: string
  actor?: string
  target?: string
  metadata?: Record<string, unknown>
}

// Integrations
export type IntegrationStatus = 'connected' | 'disconnected' | 'not_configured' | 'error'

export interface Integration {
  id: string
  name: string
  description: string
  status: IntegrationStatus
  last_sync?: string
  icon?: string
  credential_file?: string
  mcp_server?: string
}

// Workflows
export type WorkflowStatus = 'active' | 'completed' | 'failed' | 'queued' | 'paused'

export interface Workflow {
  id: string
  name: string
  type: string
  status: WorkflowStatus
  started_at: string
  completed_at?: string
  current_step?: string
  agent?: string
  command_id?: string
  error?: string
}

// Voice
export interface VoiceTranscriptionResult {
  text: string
  language: string
  note?: string
}

// System Actions (from tool registry)
export interface SystemActionEvent {
  id:         string
  tool:       string
  params:     Record<string, unknown>
  spoken:     string
  result:     string
  success:    boolean
  risk:       'low' | 'medium' | 'high'
  timestamp:  string
  action_url?: string
  action_app?: string
  action_path?: string
}

// Multi-step Tasks
export type TaskStepStatus = 'pending' | 'running' | 'completed' | 'failed' | 'skipped'
export type TaskStatus     = 'planning' | 'running' | 'completed' | 'failed' | 'cancelled'
export type RiskLevel2     = 'low' | 'medium' | 'high'

export interface TaskStep {
  id:           string
  index:        number
  description:  string
  tool:         string
  params:       Record<string, unknown>
  status:       TaskStepStatus
  result?:      string
  error?:       string
  started_at?:  string
  completed_at?: string
}

export interface Task {
  id:                    string
  goal:                  string
  steps:                 TaskStep[]
  status:                TaskStatus
  risk_level:            RiskLevel2
  confirmation_required: boolean
  created_at:            string
  completed_at?:         string
  result?:               string
  error?:                string
}

// Health
export interface HealthStatus {
  status: string
  service: string
}

export interface SystemStatus {
  python_version: string
  python_executable: string
  repo_root: string
  repo_root_exists: boolean
  logs_dir_exists: boolean
  pending_approval_dir_exists: boolean
  secrets_dir_exists: boolean
  mcp_servers_dir_exists: boolean
  mcp_servers: string[]
  skills_dir_exists: boolean
  healthy: boolean
}
