"""Pydantic request and response models."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


LEAD_STATUSES = {"new", "contacted", "qualified", "quotation", "negotiation", "won", "lost", "archived"}
LEAD_PRIORITIES = {"unset", "hot", "warm", "cold"}


class ErrorBody(BaseModel):
    code: str
    message: str


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=200)


class LoginResponse(BaseModel):
    token: str
    expires_at: datetime


class LeadBase(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=50)
    company: str | None = Field(default=None, max_length=200)
    location: str | None = Field(default=None, max_length=200)
    requirements: str | None = None
    budget_min: float | None = Field(default=None, ge=0)
    budget_max: float | None = Field(default=None, ge=0)
    currency: str = Field(default="AED", min_length=3, max_length=8)
    source: str | None = Field(default=None, max_length=100)
    status: str = "new"
    priority: str = "unset"
    next_action: str | None = None
    next_follow_up_at: datetime | None = None
    notes: str | None = None

    @field_validator("status")
    @classmethod
    def _status(cls, value: str) -> str:
        if value not in LEAD_STATUSES:
            raise ValueError(f"status must be one of {sorted(LEAD_STATUSES)}")
        return value

    @field_validator("priority")
    @classmethod
    def _priority(cls, value: str) -> str:
        if value not in LEAD_PRIORITIES:
            raise ValueError(f"priority must be one of {sorted(LEAD_PRIORITIES)}")
        return value

    @field_validator("email")
    @classmethod
    def _email(cls, value: str | None) -> str | None:
        if value is None or value.strip() == "":
            return None
        cleaned = value.strip()
        if "@" not in cleaned or "." not in cleaned.split("@")[-1]:
            raise ValueError("email is not valid")
        return cleaned


class LeadCreate(LeadBase):
    is_synthetic: bool = False


class LeadUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=50)
    company: str | None = Field(default=None, max_length=200)
    location: str | None = Field(default=None, max_length=200)
    requirements: str | None = None
    budget_min: float | None = Field(default=None, ge=0)
    budget_max: float | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=8)
    source: str | None = Field(default=None, max_length=100)
    status: str | None = None
    priority: str | None = None
    next_action: str | None = None
    next_follow_up_at: datetime | None = None
    notes: str | None = None
    qualification_score: int | None = Field(default=None, ge=0, le=100)
    qualification_summary: str | None = None
    missing_information: str | None = None

    @field_validator("status")
    @classmethod
    def _status(cls, value: str | None) -> str | None:
        if value is not None and value not in LEAD_STATUSES:
            raise ValueError(f"status must be one of {sorted(LEAD_STATUSES)}")
        return value

    @field_validator("priority")
    @classmethod
    def _priority(cls, value: str | None) -> str | None:
        if value is not None and value not in LEAD_PRIORITIES:
            raise ValueError(f"priority must be one of {sorted(LEAD_PRIORITIES)}")
        return value


class LeadOut(BaseModel):
    id: int
    name: str
    email: str | None
    phone: str | None
    company: str | None
    location: str | None
    requirements: str | None
    budget_min: float | None
    budget_max: float | None
    currency: str
    source: str | None
    status: str
    priority: str
    qualification_score: int | None
    qualification_summary: str | None
    qualification_confidence: float | None
    qualification_method: str | None
    missing_information: str | None
    next_action: str | None
    next_follow_up_at: datetime | None
    notes: str | None
    is_synthetic: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class LeadList(BaseModel):
    items: list[LeadOut]
    total: int


class NoteCreate(BaseModel):
    body: str = Field(min_length=1, max_length=5000)


class NoteOut(BaseModel):
    id: int
    lead_id: int
    body: str
    author_type: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ActivityOut(BaseModel):
    id: int
    lead_id: int
    activity_type: str
    summary: str
    details_json: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class BulkLeadRequest(BaseModel):
    action: Literal["delete", "set_status", "set_follow_up"]
    ids: list[int] = Field(min_length=1, max_length=100)
    confirm: bool = False
    status: str | None = None
    next_follow_up_at: datetime | None = None


class EmailOut(BaseModel):
    id: int
    provider: str
    provider_message_id: str
    thread_id: str | None
    lead_id: int | None
    direction: str
    sender: str | None
    recipients: str | None
    subject: str | None
    snippet: str | None
    body_text: str | None
    classification: str | None
    extraction_json: str | None
    received_at: datetime | None
    processed_at: datetime | None
    processing_status: str
    is_synthetic: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class EmailList(BaseModel):
    items: list[EmailOut]
    total: int


class DraftCreate(BaseModel):
    lead_id: int | None = None
    recipient: str = Field(min_length=3, max_length=320)
    subject: str = Field(default="", max_length=500)
    body: str = ""
    template_name: str | None = Field(default=None, max_length=100)
    source_email_id: int | None = None


class DraftUpdate(BaseModel):
    lead_id: int | None = None
    recipient: str | None = Field(default=None, max_length=320)
    subject: str | None = Field(default=None, max_length=500)
    body: str | None = None
    template_name: str | None = None


class DraftOut(BaseModel):
    id: int
    lead_id: int | None
    source_email_id: int | None
    recipient: str
    subject: str
    body: str
    template_name: str | None
    status: str
    approval_state: str
    approved_at: datetime | None
    provider_draft_id: str | None
    provider_message_id: str | None
    send_error: str | None
    created_at: datetime
    updated_at: datetime
    sent_at: datetime | None

    model_config = {"from_attributes": True}


class SendRequest(BaseModel):
    confirm: bool
    recipient: str


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class ConversationOut(BaseModel):
    id: int
    title: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=8000)


class MessageOut(BaseModel):
    id: int
    conversation_id: int
    role: str
    content: str
    created_at: datetime

    model_config = {"from_attributes": True}


class StepOut(BaseModel):
    id: int
    agent_run_id: int
    sequence_number: int
    tool_name: str | None
    input_summary: str | None
    result_summary: str | None
    status: str
    started_at: datetime
    finished_at: datetime | None

    model_config = {"from_attributes": True}


class RunOut(BaseModel):
    id: int
    conversation_id: int | None
    goal: str
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    result_summary: str | None
    error_message: str | None
    cancel_requested: bool
    created_at: datetime
    steps: list[StepOut] = []

    model_config = {"from_attributes": True}


class ChatAccepted(BaseModel):
    conversation: ConversationOut
    user_message: MessageOut
    run: RunOut


class TaskOut(BaseModel):
    id: int
    task_type: str
    payload_json: str
    status: str
    priority: int
    idempotency_key: str | None
    attempts: int
    max_attempts: int
    scheduled_at: datetime
    locked_at: datetime | None
    last_error: str | None
    result_json: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None

    model_config = {"from_attributes": True}


class TaskList(BaseModel):
    items: list[TaskOut]
    total: int


class TaskLogOut(BaseModel):
    id: int
    task_id: int
    level: str
    message: str
    created_at: datetime

    model_config = {"from_attributes": True}


class AuditOut(BaseModel):
    id: int
    actor_type: str
    action: str
    entity_type: str
    entity_id: str | None
    result: str
    details_json: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class RuleOut(BaseModel):
    id: int
    name: str
    enabled: bool
    trigger_type: str
    conditions_json: str
    actions_json: str
    requires_approval: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SettingsIn(BaseModel):
    inbox_poll_enabled: bool
    inbox_poll_interval_seconds: int = Field(ge=5, le=86400)
    gmail_search_query: str = Field(min_length=1, max_length=500)
    lead_creation_enabled: bool
    qualification_confidence_threshold: float = Field(ge=0, le=1)
    draft_generation_enabled: bool
    send_requires_approval: bool
    auto_send_enabled: bool
    allowed_recipient_scope: str = Field(default="", max_length=2000)
    max_emails_per_hour: int = Field(ge=0, le=100)
    max_emails_per_day: int = Field(ge=0, le=500)
    follow_up_cooldown_hours: int = Field(ge=1, le=720)
    max_task_attempts: int = Field(ge=1, le=10)
    llm_request_timeout_seconds: int = Field(ge=10, le=300)
    business_hours_start: int = Field(ge=0, le=23)
    business_hours_end: int = Field(ge=1, le=24)
    business_days: str = Field(min_length=3, max_length=64)
    follow_up_scan_enabled: bool

    @field_validator("business_hours_end")
    @classmethod
    def _hours(cls, value: int, info) -> int:
        start = info.data.get("business_hours_start")
        if start is not None and value <= start:
            raise ValueError("business_hours_end must be after business_hours_start")
        return value


class SettingsOut(SettingsIn):
    id: int
    updated_at: datetime

    model_config = {"from_attributes": True}


class LinkLeadRequest(BaseModel):
    lead_id: int | None = None
    create: bool = False


class QualificationOut(BaseModel):
    score: int
    priority: Literal["hot", "warm", "cold"]
    explanation: str
    missing_fields: list[str]
    recommended_action: str
    confidence: float
    intent: str | None = None
    requirements: str | None = None
    location: str | None = None
    budget_min: float | None = None
    budget_max: float | None = None
    currency: str | None = None
    deadline: str | None = None
    urgency: str = "unknown"
    uncertainty_notes: list[str] = []
    method: Literal["rules", "llm", "hybrid"]
    source_checked: bool = True


class OverviewOut(BaseModel):
    leads_by_status: dict[str, int]
    hot: int
    warm: int
    cold: int
    follow_ups_due_today: int
    overdue_follow_ups: int
    email_stats: dict[str, int]
    recent_runs: list[RunOut]
    pending_approvals: int
    failed_tasks: int
    total_leads: int
