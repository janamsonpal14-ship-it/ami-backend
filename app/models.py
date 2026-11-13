from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.utils.indian_format import ist_now


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    login_id: Mapped[str] = mapped_column(String(80), unique=True, index=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    role: Mapped[str] = mapped_column(String(40), default="admin", nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ist_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ist_now, onupdate=ist_now)


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    normalized_name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    contact_info: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ist_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ist_now, onupdate=ist_now)

    matter_links: Mapped[list[ClientMatter]] = relationship(back_populates="client", cascade="all, delete-orphan")


class Advocate(Base):
    __tablename__ = "advocates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ist_now)


class Matter(Base):
    __tablename__ = "matters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    court_forum: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    case_type: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    case_number: Mapped[str] = mapped_column(String(120), default="", nullable=False, index=True)
    normalized_case_number: Mapped[str] = mapped_column(String(120), default="", nullable=False, index=True)
    connected_cases: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_stage: Mapped[str] = mapped_column(String(160), default="Fresh / Notice", nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="Active", nullable=False, index=True)
    current_next_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    current_next_purpose: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ist_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ist_now, onupdate=ist_now)

    client_links: Mapped[list[ClientMatter]] = relationship(back_populates="matter", cascade="all, delete-orphan")
    advocate_links: Mapped[list[MatterAdvocate]] = relationship(back_populates="matter", cascade="all, delete-orphan")
    hearings: Mapped[list[Hearing]] = relationship(
        back_populates="matter",
        cascade="all, delete-orphan",
        order_by="desc(Hearing.hearing_date), desc(Hearing.id)",
    )
    tasks: Mapped[list[Task]] = relationship(back_populates="matter")


class ClientMatter(Base):
    __tablename__ = "client_matters"
    __table_args__ = (UniqueConstraint("client_id", "matter_id", name="uq_client_matter"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    matter_id: Mapped[int] = mapped_column(ForeignKey("matters.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(80), default="Client", nullable=False)
    is_billing_client: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    client: Mapped[Client] = relationship(back_populates="matter_links")
    matter: Mapped[Matter] = relationship(back_populates="client_links")


class MatterAdvocate(Base):
    __tablename__ = "matter_advocates"
    __table_args__ = (UniqueConstraint("matter_id", "advocate_id", name="uq_matter_advocate"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    matter_id: Mapped[int] = mapped_column(ForeignKey("matters.id", ondelete="CASCADE"), nullable=False)
    advocate_id: Mapped[int] = mapped_column(ForeignKey("advocates.id", ondelete="CASCADE"), nullable=False)

    matter: Mapped[Matter] = relationship(back_populates="advocate_links")
    advocate: Mapped[Advocate] = relationship()


class Hearing(Base):
    __tablename__ = "hearings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    matter_id: Mapped[int] = mapped_column(ForeignKey("matters.id", ondelete="CASCADE"), nullable=False, index=True)
    hearing_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    session_time: Mapped[str | None] = mapped_column(String(20), nullable=True)  # HH:MM or None (session)
    status: Mapped[str] = mapped_column(String(40), default="Completed", nullable=False)
    # Completed | Draft | Not Taken Up | Cancelled | Pending Update
    business_conducted: Mapped[str] = mapped_column(Text, default="", nullable=False)
    exception_reason: Mapped[str | None] = mapped_column(String(160), nullable=True)
    stage_at_hearing: Mapped[str | None] = mapped_column(String(160), nullable=True)
    next_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    next_date_status: Mapped[str] = mapped_column(String(40), default="Fixed", nullable=False)
    # Fixed | Not Fixed | Reserved for Orders | Disposed | Awaiting Notification
    next_purpose: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_billable_candidate: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    correction_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    revision_no: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    entered_by: Mapped[str] = mapped_column(String(100), default="demo_advocate", nullable=False)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ist_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ist_now, onupdate=ist_now)

    matter: Mapped[Matter] = relationship(back_populates="hearings")
    advocate_links: Mapped[list[HearingAdvocate]] = relationship(back_populates="hearing", cascade="all, delete-orphan")
    revisions: Mapped[list[HearingRevision]] = relationship(back_populates="hearing", cascade="all, delete-orphan")


class HearingAdvocate(Base):
    __tablename__ = "hearing_advocates"
    __table_args__ = (UniqueConstraint("hearing_id", "advocate_id", name="uq_hearing_advocate"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hearing_id: Mapped[int] = mapped_column(ForeignKey("hearings.id", ondelete="CASCADE"), nullable=False)
    advocate_id: Mapped[int] = mapped_column(ForeignKey("advocates.id", ondelete="CASCADE"), nullable=False)

    hearing: Mapped[Hearing] = relationship(back_populates="advocate_links")
    advocate: Mapped[Advocate] = relationship()


class HearingRevision(Base):
    __tablename__ = "hearing_revisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hearing_id: Mapped[int] = mapped_column(ForeignKey("hearings.id", ondelete="CASCADE"), nullable=False)
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    changed_by: Mapped[str] = mapped_column(String(100), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ist_now)

    hearing: Mapped[Hearing] = relationship(back_populates="revisions")


class CalendarEvent(Base):
    __tablename__ = "calendar_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    # Court | Deadline | MeetingPersonal | Task
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    event_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    start_time: Mapped[str | None] = mapped_column(String(10), nullable=True)  # "11:00"
    end_time: Mapped[str | None] = mapped_column(String(10), nullable=True)    # "12:00"
    is_all_day_or_session: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    location: Mapped[str | None] = mapped_column(String(200), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    matter_id: Mapped[int | None] = mapped_column(ForeignKey("matters.id", ondelete="SET NULL"), nullable=True, index=True)
    source_hearing_id: Mapped[int | None] = mapped_column(
        ForeignKey("hearings.id", ondelete="SET NULL"), unique=True, nullable=True
    )
    status: Mapped[str] = mapped_column(String(30), default="Active", nullable=False)  # Active | Cancelled
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ist_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ist_now, onupdate=ist_now)

    advocate_links: Mapped[list[EventAdvocate]] = relationship(back_populates="event", cascade="all, delete-orphan")


class EventAdvocate(Base):
    __tablename__ = "event_advocates"
    __table_args__ = (UniqueConstraint("event_id", "advocate_id", name="uq_event_advocate"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("calendar_events.id", ondelete="CASCADE"), nullable=False)
    advocate_id: Mapped[int] = mapped_column(ForeignKey("advocates.id", ondelete="CASCADE"), nullable=False)

    event: Mapped[CalendarEvent] = relationship(back_populates="advocate_links")
    advocate: Mapped[Advocate] = relationship()


class CourtHoliday(Base):
    __tablename__ = "court_holidays"
    __table_args__ = (UniqueConstraint("calendar_date", "bench", "title", name="uq_holiday_date_bench_title"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    calendar_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(60), nullable=False)
    # Public Holiday | Closed Saturday | Sunday | Summer Vacation | Diwali Vacation | Christmas Vacation | Working Saturday
    bench: Mapped[str] = mapped_column(String(80), default="Principal Seat Bombay", nullable=False)
    is_closed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    source_file: Mapped[str | None] = mapped_column(String(255), nullable=True)


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(250), nullable=False)
    task_type: Mapped[str] = mapped_column(String(40), default="Office Task", nullable=False)  # Deadline | Office Task
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    due_time: Mapped[str | None] = mapped_column(String(10), nullable=True)
    priority: Mapped[str] = mapped_column(String(20), default="Normal", nullable=False)  # Low | Normal | High | Urgent
    status: Mapped[str] = mapped_column(String(30), default="Open", nullable=False)  # Open | Completed | Deferred
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    matter_id: Mapped[int | None] = mapped_column(ForeignKey("matters.id", ondelete="SET NULL"), nullable=True)
    client_id: Mapped[int | None] = mapped_column(ForeignKey("clients.id", ondelete="SET NULL"), nullable=True)
    source_hearing_id: Mapped[int | None] = mapped_column(ForeignKey("hearings.id", ondelete="SET NULL"), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ist_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ist_now, onupdate=ist_now)

    matter: Mapped[Matter | None] = relationship(back_populates="tasks")


class LetterheadTemplate(Base):
    __tablename__ = "letterhead_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    top_margin_mm: Mapped[int] = mapped_column(Integer, default=35, nullable=False)
    bottom_margin_mm: Mapped[int] = mapped_column(Integer, default=20, nullable=False)
    left_margin_mm: Mapped[int] = mapped_column(Integer, default=20, nullable=False)
    right_margin_mm: Mapped[int] = mapped_column(Integer, default=20, nullable=False)
    image_width_pct: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ist_now)


class FeeMemo(Base):
    __tablename__ = "fee_memos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bill_number: Mapped[str] = mapped_column(String(80), unique=True, nullable=False, index=True)
    bill_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    addressee_client_id: Mapped[int | None] = mapped_column(ForeignKey("clients.id", ondelete="SET NULL"), nullable=True)
    addressee_name: Mapped[str] = mapped_column(String(250), nullable=False)
    addressee_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    subject_line: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="Draft", nullable=False, index=True)
    # Draft | Issued | Partially Paid | Paid | Voided
    letterhead_id: Mapped[int | None] = mapped_column(
        ForeignKey("letterhead_templates.id", ondelete="SET NULL"), nullable=True
    )
    gross_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"), nullable=False)
    adjustment_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"), nullable=False)
    tax_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("0.00"), nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"), nullable=False)
    net_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"), nullable=False)
    amount_in_words: Mapped[str] = mapped_column(Text, default="Rupees Zero Only", nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    footer_bank_details: Mapped[str | None] = mapped_column(Text, nullable=True)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    void_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ist_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ist_now, onupdate=ist_now)

    matter_links: Mapped[list[FeeMemoMatter]] = relationship(back_populates="memo", cascade="all, delete-orphan")
    lines: Mapped[list[FeeMemoLine]] = relationship(
        back_populates="memo", cascade="all, delete-orphan", order_by="FeeMemoLine.sort_order, FeeMemoLine.id"
    )
    adjustments: Mapped[list[FeeAdjustment]] = relationship(back_populates="memo", cascade="all, delete-orphan")
    payments: Mapped[list[Payment]] = relationship(
        back_populates="memo", cascade="all, delete-orphan", order_by="Payment.payment_date, Payment.id"
    )


class FeeMemoMatter(Base):
    __tablename__ = "fee_memo_matters"
    __table_args__ = (UniqueConstraint("memo_id", "matter_id", name="uq_memo_matter"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    memo_id: Mapped[int] = mapped_column(ForeignKey("fee_memos.id", ondelete="CASCADE"), nullable=False)
    matter_id: Mapped[int] = mapped_column(ForeignKey("matters.id", ondelete="CASCADE"), nullable=False)

    memo: Mapped[FeeMemo] = relationship(back_populates="matter_links")
    matter: Mapped[Matter] = relationship()


class FeeMemoLine(Base):
    __tablename__ = "fee_memo_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    memo_id: Mapped[int] = mapped_column(ForeignKey("fee_memos.id", ondelete="CASCADE"), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    linked_hearing_id: Mapped[int | None] = mapped_column(ForeignKey("hearings.id", ondelete="SET NULL"), nullable=True)
    category: Mapped[str] = mapped_column(String(80), default="Appearances", nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("1.00"), nullable=False)
    unit_rate: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"), nullable=False)
    line_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"), nullable=False)

    memo: Mapped[FeeMemo] = relationship(back_populates="lines")


class FeeAdjustment(Base):
    __tablename__ = "fee_adjustments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    memo_id: Mapped[int] = mapped_column(ForeignKey("fee_memos.id", ondelete="CASCADE"), nullable=False)
    reason: Mapped[str] = mapped_column(String(250), nullable=False)
    signed_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    memo: Mapped[FeeMemo] = relationship(back_populates="adjustments")


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    memo_id: Mapped[int] = mapped_column(ForeignKey("fee_memos.id", ondelete="CASCADE"), nullable=False, index=True)
    payment_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    method: Mapped[str] = mapped_column(String(60), default="NEFT/RTGS", nullable=False)
    reference_no: Mapped[str | None] = mapped_column(String(120), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_by: Mapped[str] = mapped_column(String(100), default="demo_advocate", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ist_now)

    memo: Mapped[FeeMemo] = relationship(back_populates="payments")


class OfficeSettings(Base):
    __tablename__ = "office_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    office_name: Mapped[str] = mapped_column(String(200), default="Chambers (Demo Office)", nullable=False)
    advocate_signatory: Mapped[str] = mapped_column(String(160), default="Adv. Demo Practitioner", nullable=False)
    default_bhc_bench: Mapped[str] = mapped_column(String(80), default="Principal Seat Bombay", nullable=False)
    bill_number_prefix: Mapped[str] = mapped_column(String(40), default="AO/2026-27/", nullable=False)
    next_bill_seq: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    default_bank_details: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ist_now, onupdate=ist_now)


class BareAct(Base):
    __tablename__ = "bare_acts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(250), nullable=False)
    short_title: Mapped[str | None] = mapped_column(String(100), nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    jurisdiction: Mapped[str] = mapped_column(String(80), default="India", nullable=False)
    version_date: Mapped[str | None] = mapped_column(String(60), nullable=True)
    source_note: Mapped[str | None] = mapped_column(String(250), nullable=True)
    tags: Mapped[str | None] = mapped_column(String(250), nullable=True)
    summary_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    page_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    section_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ist_now)

    sections: Mapped[list[BareActSection]] = relationship(
        back_populates="act",
        cascade="all, delete-orphan",
        order_by="BareActSection.id",
    )


class BareActSection(Base):
    """Extracted section or page chunk from an uploaded Bare Act PDF/text (isolated from case data)."""

    __tablename__ = "bare_act_sections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    act_id: Mapped[int] = mapped_column(ForeignKey("bare_acts.id", ondelete="CASCADE"), nullable=False, index=True)
    section_no: Mapped[str] = mapped_column(String(80), default="", nullable=False, index=True)
    heading: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    body_text: Mapped[str] = mapped_column(Text, default="", nullable=False)

    act: Mapped[BareAct] = relationship(back_populates="sections")


class ResearchConfig(Base):
    """Isolated opt-in settings & daily budget limits for Phase 4 legal research."""

    __tablename__ = "research_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ai_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    provider: Mapped[str] = mapped_column(String(60), default="local_bare_acts", nullable=False)
    model_name: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    endpoint_url: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    api_key_encrypted_or_plain: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    max_daily_queries: Mapped[int] = mapped_column(Integer, default=20, nullable=False)
    queries_today: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_query_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    disclaimer_accepted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ist_now, onupdate=ist_now)


class ResearchQueryLog(Base):
    """Audit/history log of legal research queries (never references clients or matters)."""

    __tablename__ = "research_query_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor: Mapped[str] = mapped_column(String(100), nullable=False)
    mode: Mapped[str] = mapped_column(String(60), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer_text: Mapped[str] = mapped_column(Text, nullable=False)
    citations_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ist_now)


class BareActAIAnalysis(Base):
    """Cached AI statutory analysis (interpretation and characteristics) per provision."""

    __tablename__ = "bare_act_ai_analysis"
    __table_args__ = (
        UniqueConstraint("act_id", "section_id", name="uq_bare_act_section_analysis"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    act_id: Mapped[int] = mapped_column(ForeignKey("bare_acts.id", ondelete="CASCADE"), nullable=False, index=True)
    section_id: Mapped[int] = mapped_column(ForeignKey("bare_act_sections.id", ondelete="CASCADE"), nullable=False, index=True)
    model_id: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    doc_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    interpretation: Mapped[str] = mapped_column(Text, nullable=False)
    characteristics_json: Mapped[str] = mapped_column(Text, nullable=False)  # JSON array of strings
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_inr: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ist_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ist_now, onupdate=ist_now)

    act: Mapped[BareAct] = relationship()
    section: Mapped[BareActSection] = relationship()


class AIUsageLedger(Base):
    """Accurate Pay-As-You-Go usage accounting for AI requests (tokens, costs, and status)."""

    __tablename__ = "ai_usage_ledger"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ist_now, index=True)
    module: Mapped[str] = mapped_column(String(60), default="law", nullable=False)
    model_id: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_inr: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="success", nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    entity_id: Mapped[str] = mapped_column(String(60), nullable=False)
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    before_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    after_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ist_now)


class ImportBatch(Base):
    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    import_type: Mapped[str] = mapped_column(String(60), nullable=False)  # bhc_pdf | excel_monthly | excel_client_wise
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    summary_json: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    imported_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=ist_now)

