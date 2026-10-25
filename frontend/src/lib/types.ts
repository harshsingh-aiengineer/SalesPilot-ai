export type Lead = {
  id: number;
  name: string;
  email: string | null;
  phone: string | null;
  company: string | null;
  location: string | null;
  requirements: string | null;
  budget_min: number | null;
  budget_max: number | null;
  currency: string;
  source: string | null;
  status: string;
  priority: string;
  qualification_score: number | null;
  qualification_summary: string | null;
  qualification_confidence: number | null;
  qualification_method: string | null;
  missing_information: string | null;
  next_action: string | null;
  next_follow_up_at: string | null;
  notes: string | null;
  is_synthetic: boolean;
  created_at: string;
  updated_at: string;
};

export type EmailItem = {
  id: number;
  provider: string;
  provider_message_id: string;
  thread_id: string | null;
  lead_id: number | null;
  direction: string;
  sender: string | null;
  recipients: string | null;
  subject: string | null;
  snippet: string | null;
  body_text: string | null;
  classification: string | null;
  extraction_json: string | null;
  received_at: string | null;
  processed_at: string | null;
  processing_status: string;
  is_synthetic: boolean;
  created_at: string;
};

export type Draft = {
  id: number;
  lead_id: number | null;
  recipient: string;
  subject: string;
  body: string;
  template_name: string | null;
  status: string;
  approval_state: string;
  provider_draft_id: string | null;
  provider_message_id: string | null;
  send_error: string | null;
  created_at: string;
  updated_at: string;
  sent_at: string | null;
};

export type AgentRun = {
  id: number;
  conversation_id: number | null;
  goal: string;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  result_summary: string | null;
  error_message: string | null;
  cancel_requested: boolean;
  created_at: string;
  steps: AgentStep[];
};

export type AgentStep = {
  id: number;
  sequence_number: number;
  tool_name: string | null;
  input_summary: string | null;
  result_summary: string | null;
  status: string;
  started_at: string;
  finished_at: string | null;
};

export type Conversation = { id: number; title: string; created_at: string; updated_at: string };
export type ChatMessage = { id: number; conversation_id: number; role: string; content: string; created_at: string };

export type Task = {
  id: number;
  task_type: string;
  status: string;
  attempts: number;
  max_attempts: number;
  last_error: string | null;
  scheduled_at: string;
  result_json: string | null;
  idempotency_key: string | null;
  created_at: string;
  completed_at: string | null;
};

export type AutomationSettings = {
  id: number;
  inbox_poll_enabled: boolean;
  inbox_poll_interval_seconds: number;
  gmail_search_query: string;
  lead_creation_enabled: boolean;
  qualification_confidence_threshold: number;
  draft_generation_enabled: boolean;
  send_requires_approval: boolean;
  auto_send_enabled: boolean;
  allowed_recipient_scope: string;
  max_emails_per_hour: number;
  max_emails_per_day: number;
  follow_up_cooldown_hours: number;
  max_task_attempts: number;
  llm_request_timeout_seconds: number;
  business_hours_start: number;
  business_hours_end: number;
  business_days: string;
  follow_up_scan_enabled: boolean;
  updated_at: string;
};
