from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


# --- Auth ---

class LoginRequest(BaseModel):
    login_id: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)


class UserOut(BaseModel):
    id: int
    login_id: str
    full_name: str
    role: str
    is_demo: bool


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8, max_length=200)


class UserCreate(BaseModel):
    login_id: str = Field(min_length=3, max_length=80)
    full_name: str = Field(min_length=1, max_length=160)
    role: Literal["admin", "associate", "clerk"] = "associate"
    password: str = Field(min_length=8, max_length=200)


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=160)
    role: Literal["admin", "associate", "clerk"] | None = None
    is_active: bool | None = None


class UserAdminOut(BaseModel):
    id: int
    login_id: str
    full_name: str
    role: str
    is_active: bool
    is_demo: bool
    created_at: datetime


class StartFreshRequest(BaseModel):
    confirmation_text: str = Field(min_length=1)
    keep_holidays_and_letterheads: bool = True
    new_admin_login_id: str | None = Field(default=None, min_length=3, max_length=80)
    new_admin_full_name: str | None = Field(default=None, min_length=1, max_length=160)
    new_admin_password: str | None = Field(default=None, min_length=8, max_length=200)


class AuditLogOut(BaseModel):
    id: int
    actor: str
    entity_type: str
    entity_id: str
    action: str
    before_json: str | None = None
    after_json: str | None = None
    created_at: datetime


# --- Clients & Advocates ---

class ClientCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=200)
    contact_info: str | None = None
    notes: str | None = None


class ClientUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    contact_info: str | None = None
    notes: str | None = None
    is_active: bool | None = None


class ClientOut(BaseModel):
    id: int
    display_name: str
    contact_info: str | None = None
    notes: str | None = None
    is_active: bool
    is_demo: bool
    matter_count: int = 0


class AdvocateCreate(BaseModel):
    full_name: str = Field(min_length=1, max_length=160)


class AdvocateOut(BaseModel):
    id: int
    full_name: str
    is_active: bool


# --- Matters ---

class ClientLinkIn(BaseModel):
    client_id: int
    role: str = "Client"
    is_billing_client: bool = True


class MatterCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    court_forum: str = Field(min_length=1, max_length=200)
    case_type: str = ""
    case_number: str = ""
    connected_cases: str | None = None
    current_stage: str = "Fresh / Notice"
    status: Literal["Active", "Reserved", "Disposed", "Stay", "Archived"] = "Active"
    current_next_date: date | None = None
    current_next_purpose: str | None = None
    notes: str | None = None
    cnr: str | None = None
    clients: list[ClientLinkIn] = Field(min_length=1)
    advocate_ids: list[int] = Field(default_factory=list)
    allow_duplicate: bool = False


class MatterUpdate(BaseModel):
    title: str | None = None
    court_forum: str | None = None
    case_type: str | None = None
    case_number: str | None = None
    connected_cases: str | None = None
    current_stage: str | None = None
    status: Literal["Active", "Reserved", "Disposed", "Stay", "Archived"] | None = None
    current_next_date: date | None = None
    current_next_purpose: str | None = None
    notes: str | None = None
    cnr: str | None = None
    clients: list[ClientLinkIn] | None = None
    advocate_ids: list[int] | None = None


class MatterClientSummary(BaseModel):
    client_id: int
    display_name: str
    role: str
    is_billing_client: bool


class MatterSummaryOut(BaseModel):
    id: int
    title: str
    court_forum: str
    case_type: str
    case_number: str
    connected_cases: str | None = None
    current_stage: str
    status: str
    current_next_date: date | None = None
    current_next_purpose: str | None = None
    notes: str | None = None
    cnr: str | None = None
    ecourts_synced_at: datetime | None = None
    ecourts_data_json: str | None = None
    clients: list[MatterClientSummary]
    advocates: list[AdvocateOut]
    is_demo: bool


class DuplicateMatchOut(BaseModel):
    matter_id: int
    title: str
    case_number: str
    court_forum: str
    reason: str


# --- Hearings ---

class HearingSaveRequest(BaseModel):
    hearing_id: int | None = None
    hearing_date: date
    session_time: str | None = None
    status: Literal["Completed", "Draft", "Not Taken Up", "Cancelled"] = "Completed"
    business_conducted: str = ""
    exception_reason: str | None = None
    stage_at_hearing: str | None = None
    advocate_ids: list[int] = Field(default_factory=list)
    next_date: date | None = None
    next_date_status: Literal[
        "Fixed", "Not Fixed", "Reserved for Orders", "Disposed", "Awaiting Notification"
    ] = "Fixed"
    next_purpose: str | None = None
    is_billable_candidate: bool = False
    correction_reason: str | None = None
    confirm_holiday_date: bool = False


class NotReachedRequest(BaseModel):
    hearing_id: int | None = None
    hearing_date: date
    next_date: date | None = None
    next_purpose: str | None = None
    advocate_ids: list[int] = Field(default_factory=list)
    is_billable_candidate: bool = False


class BatchEodItem(BaseModel):
    matter_id: int
    action: Literal["complete", "not_reached", "draft"] = "complete"
    payload: HearingSaveRequest


class BatchEodRequest(BaseModel):
    items: list[BatchEodItem]


class HearingOut(BaseModel):
    id: int
    matter_id: int
    matter_title: str = ""
    court_forum: str = ""
    hearing_date: date
    session_time: str | None = None
    status: str
    business_conducted: str
    exception_reason: str | None = None
    stage_at_hearing: str | None = None
    next_date: date | None = None
    next_date_status: str
    next_purpose: str | None = None
    is_billable_candidate: bool
    correction_reason: str | None = None
    revision_no: int
    advocates: list[AdvocateOut]
    holiday_warning: str | None = None


# --- Calendar & Holidays ---

class CalendarEventCreate(BaseModel):
    event_type: Literal["Court", "Deadline", "MeetingPersonal", "Task"]
    title: str = Field(min_length=1, max_length=300)
    event_date: date
    start_time: str | None = None
    end_time: str | None = None
    is_all_day_or_session: bool = True
    location: str | None = None
    notes: str | None = None
    matter_id: int | None = None
    advocate_ids: list[int] = Field(default_factory=list)


class CalendarEventUpdate(BaseModel):
    event_type: Literal["Court", "Deadline", "MeetingPersonal", "Task"] | None = None
    title: str | None = None
    event_date: date | None = None
    start_time: str | None = None
    end_time: str | None = None
    is_all_day_or_session: bool | None = None
    location: str | None = None
    notes: str | None = None
    matter_id: int | None = None
    advocate_ids: list[int] | None = None
    status: Literal["Active", "Cancelled"] | None = None


class ClashWarningOut(BaseModel):
    severity: Literal["Definite Overlap", "Potential Conflict"]
    conflicting_event_id: int
    conflicting_title: str
    message: str


class CalendarEventOut(BaseModel):
    id: int
    event_type: str
    title: str
    event_date: date
    start_time: str | None = None
    end_time: str | None = None
    is_all_day_or_session: bool
    location: str | None = None
    notes: str | None = None
    matter_id: int | None = None
    source_hearing_id: int | None = None
    status: str
    advocates: list[AdvocateOut] = Field(default_factory=list)
    clash_warnings: list[ClashWarningOut] = Field(default_factory=list)


class CourtHolidayOut(BaseModel):
    id: int
    year: int
    calendar_date: date
    title: str
    category: str
    bench: str
    is_closed: bool


# --- Tasks ---

class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=250)
    task_type: Literal["Deadline", "Office Task"] = "Office Task"
    due_date: date | None = None
    due_time: str | None = None
    priority: Literal["Low", "Normal", "High", "Urgent"] = "Normal"
    notes: str | None = None
    matter_id: int | None = None
    client_id: int | None = None
    source_hearing_id: int | None = None


class TaskUpdate(BaseModel):
    title: str | None = None
    task_type: Literal["Deadline", "Office Task"] | None = None
    due_date: date | None = None
    due_time: str | None = None
    priority: Literal["Low", "Normal", "High", "Urgent"] | None = None
    status: Literal["Open", "Completed", "Deferred"] | None = None
    notes: str | None = None
    matter_id: int | None = None


class TaskOut(BaseModel):
    id: int
    title: str
    task_type: str
    due_date: date | None = None
    due_time: str | None = None
    priority: str
    status: str
    notes: str | None = None
    matter_id: int | None = None
    matter_title: str | None = None
    client_id: int | None = None


# --- Fee Memos & Payments ---

class FeeLineIn(BaseModel):
    linked_hearing_id: int | None = None
    category: str = "Appearances"
    description: str = Field(min_length=1)
    quantity: Decimal = Field(default=Decimal("1"), gt=0)
    unit_rate: Decimal = Field(default=Decimal("0"), ge=0)


class FeeAdjustmentIn(BaseModel):
    reason: str = Field(min_length=1, max_length=250)
    signed_amount: Decimal


class FeeMemoCreate(BaseModel):
    bill_number: str | None = None
    bill_date: date
    due_date: date | None = None
    addressee_client_id: int | None = None
    addressee_name: str = Field(min_length=1, max_length=250)
    addressee_address: str | None = None
    subject_line: str | None = None
    matter_ids: list[int] = Field(default_factory=list)
    letterhead_id: int | None = None
    tax_percent: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    notes: str | None = None
    footer_bank_details: str | None = None
    lines: list[FeeLineIn] = Field(default_factory=list)
    adjustments: list[FeeAdjustmentIn] = Field(default_factory=list)


class FeeMemoUpdate(BaseModel):
    bill_number: str | None = None
    bill_date: date | None = None
    due_date: date | None = None
    addressee_client_id: int | None = None
    addressee_name: str | None = None
    addressee_address: str | None = None
    subject_line: str | None = None
    matter_ids: list[int] | None = None
    letterhead_id: int | None = None
    tax_percent: Decimal | None = Field(default=None, ge=0, le=100)
    notes: str | None = None
    footer_bank_details: str | None = None
    lines: list[FeeLineIn] | None = None
    adjustments: list[FeeAdjustmentIn] | None = None


class PaymentCreate(BaseModel):
    payment_date: date
    amount: Decimal = Field(gt=0)
    method: Literal["NEFT/RTGS", "IMPS", "UPI", "Cheque", "Cash", "Other"] = "NEFT/RTGS"
    reference_no: str | None = None
    notes: str | None = None
    allow_overpayment: bool = False


class VoidMemoRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class MemoQuickStatusRequest(BaseModel):
    status: Literal["Paid", "Partially Paid", "Pending"]
    amount: Decimal | None = Field(default=None, gt=0)
    payment_date: date | None = None
    method: Literal["NEFT/RTGS", "IMPS", "UPI", "Cheque", "Cash", "Other"] = "NEFT/RTGS"
    reference_no: str | None = None


class FeeLineOut(BaseModel):
    id: int
    sort_order: int
    linked_hearing_id: int | None = None
    category: str
    description: str
    quantity: float
    unit_rate: float
    line_amount: float


class FeeAdjustmentOut(BaseModel):
    id: int
    reason: str
    signed_amount: float


class PaymentOut(BaseModel):
    id: int
    payment_date: date
    amount: float
    method: str
    reference_no: str | None = None
    notes: str | None = None


class FeeMemoOut(BaseModel):
    id: int
    bill_number: str
    bill_date: date
    due_date: date | None = None
    addressee_client_id: int | None = None
    addressee_name: str
    addressee_address: str | None = None
    subject_line: str | None = None
    status: str
    is_overdue: bool
    matter_ids: list[int]
    matter_titles: list[str]
    gross_total: float
    adjustment_total: float
    tax_percent: float
    tax_amount: float
    net_total: float
    net_total_formatted: str
    amount_in_words: str
    paid_total: float
    balance_due: float
    balance_due_formatted: str
    notes: str | None = None
    footer_bank_details: str | None = None
    lines: list[FeeLineOut]
    adjustments: list[FeeAdjustmentOut]
    payments: list[PaymentOut]
    is_demo: bool


# --- Law / Settings / Search ---

class OfficeSettingsOut(BaseModel):
    office_name: str
    advocate_signatory: str
    default_bhc_bench: str
    bill_number_prefix: str
    next_bill_seq: int
    default_bank_details: str | None = None


class OfficeSettingsUpdate(BaseModel):
    office_name: str | None = None
    advocate_signatory: str | None = None
    default_bhc_bench: str | None = None
    bill_number_prefix: str | None = None
    default_bank_details: str | None = None


class BareActCreate(BaseModel):
    title: str = Field(min_length=2, max_length=250)
    short_title: str | None = Field(default=None, max_length=100)
    year: int | None = None
    jurisdiction: str = "India"
    version_date: str | None = Field(default="Verified Local Upload", max_length=60)
    source_note: str | None = Field(default=None, max_length=250)
    tags: str | None = Field(default=None, max_length=250)
    summary_text: str | None = None
    full_text: str | None = None


class BareActSectionOut(BaseModel):
    id: int
    act_id: int
    section_no: str
    heading: str
    page_number: int
    body_text: str


class BareActOut(BaseModel):
    id: int
    title: str
    short_title: str | None = None
    year: int | None = None
    jurisdiction: str
    version_date: str | None = None
    source_note: str | None = None
    tags: str | None = None
    summary_text: str | None = None
    is_demo: bool
    has_file: bool = False
    page_count: int = 0
    section_count: int = 0


class BareActDetailOut(BareActOut):
    sections: list[BareActSectionOut] = Field(default_factory=list)


class ResearchConfigUpdate(BaseModel):
    ai_enabled: bool | None = None
    provider: Literal["local_bare_acts", "openai_compatible", "gemini"] | None = None
    model_name: str | None = Field(default=None, max_length=120)
    endpoint_url: str | None = Field(default=None, max_length=300)
    api_key: str | None = Field(default=None, max_length=300)
    max_daily_queries: int | None = Field(default=None, ge=1, le=500)
    disclaimer_accepted: bool | None = None


class ResearchAskRequest(BaseModel):
    question: str = Field(min_length=2, max_length=2000)
    act_ids: list[int] = Field(default_factory=list)
    use_external_ai: bool = False


# --- Four-Part Statutory Analysis & AI Usage Ledger ---

class JudgmentOut(BaseModel):
    case_name: str
    court: str
    decision_date: str | None = None
    citation: str | None = None
    legal_proposition: str
    relevant_paragraphs: str | None = None
    source_url: str | None = None
    doc_id: int | None = None
    authority_type: str = "binding"  # binding | persuasive | distinguishing | adverse | benchmark
    relevance_note: str | None = None


class FourPartAnalysisOut(BaseModel):
    act_id: int
    act_title: str
    short_title: str | None = None
    year: int | None = None
    section_id: int
    section_no: str
    heading: str
    page_number: int
    page_count: int = 0
    has_file: bool = False
    statutory_text: str
    interpretation: str
    characteristics: list[str] = Field(default_factory=list)
    judgments: list[JudgmentOut] = Field(default_factory=list)
    is_cached: bool = False
    cached_at: str | None = None
    model_used: str = ""
    ai_status: str = "ready"  # ready | cached | generated | unconfigured | error | fallback
    kanoon_status: str = "verified"  # verified | unconfigured | empty | error
    cost_inr: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    error_message: str | None = None


# Backward-compatibility alias
ThreePointAnalysisOut = FourPartAnalysisOut


class AIUsageSummaryOut(BaseModel):
    total_queries: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_cost_inr: float = 0.0
    total_cost_usd: float = 0.0
    month_to_date_cost_inr: float = 0.0
    month_to_date_queries: int = 0
    recent_logs: list[dict] = Field(default_factory=list)


