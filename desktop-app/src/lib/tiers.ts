// Centralized tier definitions — ALL feature gating flows through this file.
// Never scatter if (tier === 'pro') checks across components.

export type Tier = 'free' | 'pro' | 'enterprise'

export type FeatureKey =
  // Free tier
  | 'voiceAssistant'
  | 'wakeWord'
  | 'systemTools'
  | 'openApps'
  | 'fileBrowser'
  | 'settingsControl'
  | 'volumeControl'
  | 'workflows'
  | 'offlineAI'
  | 'basicMemory'
  | 'workMode'
  // Pro tier
  | 'advancedAI'
  | 'persistentMemory'
  | 'autonomousAgents'
  | 'browserControl'
  | 'emailIntegration'
  | 'calendarIntegration'
  | 'smartWorkflows'
  | 'cloudSync'
  | 'premiumVoice'
  | 'desktopAutomation'
  | 'priorityModels'
  // Enterprise tier
  | 'teamAgents'
  | 'sharedMemory'
  | 'orgDashboard'
  | 'adminControls'
  | 'apiAccess'
  | 'selfHosted'
  | 'workflowOrchestration'
  | 'companyCopilots'

type FeatureMap = Record<FeatureKey, boolean>

const FREE_FEATURES: FeatureMap = {
  voiceAssistant: true,
  wakeWord: true,
  systemTools: true,
  openApps: true,
  fileBrowser: true,
  settingsControl: true,
  volumeControl: true,
  workflows: true,
  offlineAI: true,
  basicMemory: true,
  workMode: true,
  advancedAI: false,
  persistentMemory: false,
  autonomousAgents: false,
  browserControl: false,
  emailIntegration: false,
  calendarIntegration: false,
  smartWorkflows: false,
  cloudSync: false,
  premiumVoice: false,
  desktopAutomation: false,
  priorityModels: false,
  teamAgents: false,
  sharedMemory: false,
  orgDashboard: false,
  adminControls: false,
  apiAccess: false,
  selfHosted: false,
  workflowOrchestration: false,
  companyCopilots: false,
}

const PRO_FEATURES: FeatureMap = {
  ...FREE_FEATURES,
  advancedAI: true,
  persistentMemory: true,
  autonomousAgents: true,
  browserControl: true,
  emailIntegration: true,
  calendarIntegration: true,
  smartWorkflows: true,
  cloudSync: true,
  premiumVoice: true,
  desktopAutomation: true,
  priorityModels: true,
}

const ENTERPRISE_FEATURES: FeatureMap = {
  ...PRO_FEATURES,
  teamAgents: true,
  sharedMemory: true,
  orgDashboard: true,
  adminControls: true,
  apiAccess: true,
  selfHosted: true,
  workflowOrchestration: true,
  companyCopilots: true,
}

export const TIER_FEATURES: Record<Tier, FeatureMap> = {
  free: FREE_FEATURES,
  pro: PRO_FEATURES,
  enterprise: ENTERPRISE_FEATURES,
}

export function canAccess(tier: Tier, feature: FeatureKey): boolean {
  return TIER_FEATURES[tier]?.[feature] ?? false
}

export interface TierMeta {
  id: Tier
  name: string
  tagline: string
  status: 'available' | 'coming_soon'
  price: string | null
  ctaLabel: string
  ctaDisabled: boolean
  features: string[]
  highlighted: boolean
}

export const TIER_META: TierMeta[] = [
  {
    id: 'free',
    name: 'Free',
    tagline: 'Everything you need to get started',
    status: 'available',
    price: '$0',
    ctaLabel: 'Start Free',
    ctaDisabled: false,
    highlighted: false,
    features: [
      'Voice assistant',
      'Wake word detection',
      'Windows system tools',
      'Open apps & folders',
      'Settings & volume control',
      'Workflow automation',
      'Offline local AI',
      'Basic memory',
      'Work mode',
    ],
  },
  {
    id: 'pro',
    name: 'Pro',
    tagline: 'Advanced AI for power users',
    status: 'coming_soon',
    price: null,
    ctaLabel: 'Coming Soon',
    ctaDisabled: true,
    highlighted: true,
    features: [
      'Advanced AI reasoning',
      'Persistent long-term memory',
      'Multi-step autonomous agents',
      'Browser control',
      'Email & Calendar integration',
      'Smart workflows',
      'Cloud sync',
      'Faster premium voice',
      'AI desktop automation',
      'Priority models',
    ],
  },
  {
    id: 'enterprise',
    name: 'Enterprise',
    tagline: 'For teams and organizations',
    status: 'coming_soon',
    price: null,
    ctaLabel: 'Contact Us',
    ctaDisabled: true,
    highlighted: false,
    features: [
      'Team agents',
      'Shared memory',
      'Organization dashboards',
      'Admin controls',
      'API access',
      'Self-hosted deployments',
      'Workflow orchestration',
      'Internal company copilots',
    ],
  },
]
