// AI Operator — shared TypeScript types (mirrors Pydantic schemas)

export interface ApiResponse<T> {
  success: boolean
  data: T
  message?: string
}

// Commands
export type CommandStatus = 'queued' | 'running' | 'completed' | 'failed'

export interface Command {
  id: string
  text: string
  status: CommandStatus
  created_at: string
  updated_at?: string
  result?: string
  error?: string
  agent?: string
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
