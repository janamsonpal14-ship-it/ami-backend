from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Advocate,
    BareAct,
    CalendarEvent,
    Client,
    ClientMatter,
    CourtHoliday,
    EventAdvocate,
    FeeAdjustment,
    FeeMemo,
    FeeMemoLine,
    FeeMemoMatter,
    Hearing,
    HearingAdvocate,
    Matter,
    MatterAdvocate,
    OfficeSettings,
    Payment,
    Task,
    User,
)
from app.services.audit_service import create_snapshot, record_audit
from app.services.billing_service import _recalculate_memo_totals
from app.utils.indian_format import ist_now, ist_today
from app.utils.security import hash_password, normalize_case_number, normalize_name

# Public Bombay High Court 2026 Principal Seat Holidays & Working Saturdays (from BHC Cal 2026.pdf)
_BHC_2026_PUBLIC_HOLIDAYS: list[tuple[date, str, str, bool]] = [
    (date(2026, 1, 17), "Court Working Saturday", "Working Saturday", False),
    (date(2026, 1, 26), "Republic Day", "Public Holiday", True),
    (date(2026, 2, 19), "Chhatrapati Shivaji Maharaj Jayanti", "Public Holiday", True),
    (date(2026, 3, 2), "BHC Holiday", "Public Holiday", True),
    (date(2026, 3, 3), "Holi (2nd Day)", "Public Holiday", True),
    (date(2026, 3, 7), "Court Working Saturday", "Working Saturday", False),
    (date(2026, 3, 19), "Gudi Padwa", "Public Holiday", True),
    (date(2026, 3, 20), "BHC Holiday", "Public Holiday", True),
    (date(2026, 3, 21), "Ramzan Id", "Public Holiday", True),
    (date(2026, 3, 31), "Mahavir Jayanti", "Public Holiday", True),
    (date(2026, 4, 3), "Good Friday", "Public Holiday", True),
    (date(2026, 4, 13), "BHC Holiday", "Public Holiday", True),
    (date(2026, 4, 14), "Dr. Babasaheb Ambedkar Jayanti", "Public Holiday", True),
    (date(2026, 4, 18), "Court Working Saturday", "Working Saturday", False),
    (date(2026, 5, 1), "Maharashtra Day & Buddha Pournima", "Public Holiday", True),
    (date(2026, 5, 27), "Bakri-Id", "Public Holiday", True),
    (date(2026, 6, 26), "Moharrum", "Public Holiday", True),
    (date(2026, 8, 15), "Independence Day & Parsi New Year", "Public Holiday", True),
    (date(2026, 8, 26), "Id-E-Milad", "Public Holiday", True),
    (date(2026, 9, 14), "Ganesh Chaturthi", "Public Holiday", True),
    (date(2026, 9, 25), "Anant Chaturdashi", "Public Holiday", True),
    (date(2026, 10, 2), "Mahatma Gandhi Jayanti", "Public Holiday", True),
    (date(2026, 10, 20), "Dasara", "Public Holiday", True),
    (date(2026, 11, 23), "BHC Holiday", "Public Holiday", True),
    (date(2026, 11, 24), "Guru Nanak Jayanti", "Public Holiday", True),
    (date(2026, 12, 5), "Court Working Saturday", "Working Saturday", False),
    (date(2026, 12, 25), "Christmas", "Public Holiday", True),
]


def _seed_bhc_2026_holidays(db: Session) -> None:
    if db.scalar(select(CourtHoliday)) is not None:
        return
    bench = "Principal Seat Bombay"
    for d, title, cat, is_closed in _BHC_2026_PUBLIC_HOLIDAYS:
        db.add(
            CourtHoliday(
                year=2026,
                calendar_date=d,
                title=title,
                category=cat,
                bench=bench,
                is_closed=is_closed,
                source_file="BHC Cal 2026.pdf (Public Reference)",
            )
        )
    # Add Diwali Vacation (6 Nov - 18 Nov 2026), Summer Vacation (11 May - 7 Jun 2026), Christmas Vacation (25 Dec - 31 Dec 2026)
    vacations = [
        (date(2026, 5, 11), date(2026, 6, 7), "Summer Vacation", "Summer Vacation"),
        (date(2026, 11, 6), date(2026, 11, 18), "Diwali Vacation", "Diwali Vacation"),
        (date(2026, 12, 25), date(2026, 12, 31), "Christmas Vacation", "Christmas Vacation"),
    ]
    for start_d, end_d, title, cat in vacations:
        cur = start_d
        while cur <= end_d:
            existing = db.scalar(
                select(CourtHoliday).where(
                    CourtHoliday.calendar_date == cur,
                    CourtHoliday.bench == bench,
                    CourtHoliday.title == title,
                )
            )
            if existing is None:
                db.add(
                    CourtHoliday(
                        year=cur.year,
                        calendar_date=cur,
                        title=title,
                        category=cat,
                        bench=bench,
                        is_closed=True,
                        source_file="BHC Cal 2026.pdf (Public Reference)",
                    )
                )
            cur += timedelta(days=1)


def ensure_seed_data(db: Session) -> None:
    """Seed initial synthetic demo data and default user if the database is empty."""
    if db.scalar(select(User)) is None:
        db.add(
            User(
                login_id="demo_advocate",
                full_name="Adv. Demo Practitioner",
                role="admin",
                password_hash=hash_password("DemoPassword@2026"),
                is_active=True,
                is_demo=True,
            )
        )

    if db.scalar(select(OfficeSettings)) is None:
        db.add(
            OfficeSettings(
                office_name="Chambers of Adv. Demo Practitioner (Synthetic Demo)",
                advocate_signatory="Adv. Demo Practitioner",
                default_bhc_bench="Principal Seat Bombay",
                bill_number_prefix="AO/2026-27/",
                next_bill_seq=3,
                default_bank_details="Demo Bank Details Only — Replace before live billing",
            )
        )

    _seed_bhc_2026_holidays(db)

    if db.scalar(select(Client)) is not None:
        db.commit()
        return

    today = ist_today()
    yesterday = today - timedelta(days=1)
    last_week = today - timedelta(days=7)
    next_week = today + timedelta(days=7)
    two_weeks = today + timedelta(days=14)
    tomorrow = today + timedelta(days=1)

    # Fictional Advocates
    adv_main = Advocate(full_name="Adv. Demo Practitioner", is_demo=True)
    adv_a = Advocate(full_name="Adv. A. Kulkarni (Demo)", is_demo=True)
    adv_b = Advocate(full_name="Adv. M. Parekh (Demo)", is_demo=True)
    db.add_all([adv_main, adv_a, adv_b])
    db.flush()

    # Fictional Clients
    c1 = Client(
        display_name="Vardhman Textiles Pvt. Ltd. (Demo)",
        normalized_name=normalize_name("Vardhman Textiles Pvt. Ltd. (Demo)"),
        contact_info="Fort, Mumbai • demo.client1@example.invalid",
        notes="Fictional commercial litigation client.",
        is_demo=True,
    )
    c2 = Client(
        display_name="Kaveri Infrastructure LLP (Demo)",
        normalized_name=normalize_name("Kaveri Infrastructure LLP (Demo)"),
        contact_info="BKC, Mumbai • demo.client2@example.invalid",
        notes="Fictional arbitration client.",
        is_demo=True,
    )
    c3 = Client(
        display_name="Rohan Deshmukh (Demo)",
        normalized_name=normalize_name("Rohan Deshmukh (Demo)"),
        contact_info="Dadar, Mumbai • demo.client3@example.invalid",
        notes="Fictional individual client.",
        is_demo=True,
    )
    db.add_all([c1, c2, c3])
    db.flush()

    # Fictional Matters (2 under Client 1 to showcase multi-matter client grouping)
    m1 = Matter(
        title="Vardhman Textiles Pvt. Ltd. vs. Meridian Logistics Ltd. (Demo)",
        court_forum="Bombay High Court",
        case_type="Commercial Suit",
        case_number="COMS/142/2026",
        normalized_case_number=normalize_case_number("COMS/142/2026"),
        current_stage="Further Arguments",
        status="Active",
        current_next_date=today,
        current_next_purpose="Final arguments on Interim Application",
        notes="Fictional demo suit.",
        is_demo=True,
    )
    m2 = Matter(
        title="Vardhman Textiles Pvt. Ltd. vs. Apex Warehousing Co. (Demo)",
        court_forum="City Civil Court, Mumbai",
        case_type="Summary Suit",
        case_number="SS/88/2026",
        normalized_case_number=normalize_case_number("SS/88/2026"),
        current_stage="Summons for Judgment",
        status="Active",
        current_next_date=next_week,
        current_next_purpose="Reply to Leave to Defend",
        is_demo=True,
    )
    m3 = Matter(
        title="Kaveri Infrastructure LLP vs. Western Metro Corp. (Demo)",
        court_forum="Arbitral Tribunal",
        case_type="Arbitration",
        case_number="ARB/19/2026",
        normalized_case_number=normalize_case_number("ARB/19/2026"),
        current_stage="Cross-Examination",
        status="Active",
        current_next_date=today,
        current_next_purpose="Cross-examination of CW-1",
        is_demo=True,
    )
    m4 = Matter(
        title="Rohan Deshmukh vs. Horizon Realty Developers (Demo)",
        court_forum="NCLT Mumbai",
        case_type="Section 7 IBC",
        case_number="CP(IB)/310/2026",
        normalized_case_number=normalize_case_number("CP(IB)/310/2026"),
        current_stage="Admission Hearing",
        status="Active",
        current_next_date=tomorrow,
        current_next_purpose="Rejoinder & admission arguments",
        is_demo=True,
    )
    db.add_all([m1, m2, m3, m4])
    db.flush()

    db.add_all([
        ClientMatter(client_id=c1.id, matter_id=m1.id, role="Plaintiff", is_billing_client=True),
        ClientMatter(client_id=c1.id, matter_id=m2.id, role="Plaintiff", is_billing_client=True),
        ClientMatter(client_id=c2.id, matter_id=m3.id, role="Claimant", is_billing_client=True),
        ClientMatter(client_id=c3.id, matter_id=m4.id, role="Financial Creditor", is_billing_client=True),
        MatterAdvocate(matter_id=m1.id, advocate_id=adv_main.id),
        MatterAdvocate(matter_id=m1.id, advocate_id=adv_a.id),
        MatterAdvocate(matter_id=m2.id, advocate_id=adv_main.id),
        MatterAdvocate(matter_id=m3.id, advocate_id=adv_main.id),
        MatterAdvocate(matter_id=m3.id, advocate_id=adv_b.id),
        MatterAdvocate(matter_id=m4.id, advocate_id=adv_a.id),
    ])

    # Historic hearings + today's listings (m1 listed today & still pending EOD; m3 already updated yesterday or today)
    h1 = Hearing(
        matter_id=m1.id,
        hearing_date=last_week,
        status="Completed",
        business_conducted="Part arguments heard on ad-interim relief; matter adjourned for further arguments.",
        stage_at_hearing="Further Arguments",
        next_date=today,
        next_date_status="Fixed",
        next_purpose="Final arguments on Interim Application",
        is_billable_candidate=True,
        is_demo=True,
    )
    h2 = Hearing(
        matter_id=m2.id,
        hearing_date=yesterday,
        status="Completed",
        business_conducted="Defendant filed affidavit seeking leave to defend; time granted to file rejoinder.",
        stage_at_hearing="Summons for Judgment",
        next_date=next_week,
        next_date_status="Fixed",
        next_purpose="Reply to Leave to Defend",
        is_billable_candidate=True,
        is_demo=True,
    )
    h3 = Hearing(
        matter_id=m3.id,
        hearing_date=last_week,
        status="Completed",
        business_conducted="Affidavit of evidence of CW-1 taken on record; cross-examination fixed.",
        stage_at_hearing="Cross-Examination",
        next_date=today,
        next_date_status="Fixed",
        next_purpose="Cross-examination of CW-1",
        is_billable_candidate=True,
        is_demo=True,
    )
    h4 = Hearing(
        matter_id=m4.id,
        hearing_date=last_week,
        status="Completed",
        business_conducted="Reply filed by Corporate Debtor; rejoinder directed before admission hearing.",
        stage_at_hearing="Admission Hearing",
        next_date=tomorrow,
        next_date_status="Fixed",
        next_purpose="Rejoinder & admission arguments",
        is_billable_candidate=False,
        is_demo=True,
    )
    db.add_all([h1, h2, h3, h4])
    db.flush()

    db.add_all([
        HearingAdvocate(hearing_id=h1.id, advocate_id=adv_main.id),
        HearingAdvocate(hearing_id=h1.id, advocate_id=adv_a.id),
        HearingAdvocate(hearing_id=h2.id, advocate_id=adv_main.id),
        HearingAdvocate(hearing_id=h3.id, advocate_id=adv_main.id),
        HearingAdvocate(hearing_id=h3.id, advocate_id=adv_b.id),
        HearingAdvocate(hearing_id=h4.id, advocate_id=adv_a.id),
    ])

    # Calendar Events linked to hearings + meeting/personal/deadline events (including a clash demo today)
    ev1 = CalendarEvent(
        event_type="Court",
        title=f"{m1.title} ({m1.court_forum})",
        event_date=today,
        start_time="11:00",
        end_time="12:30",
        is_all_day_or_session=False,
        location="Court Room 18, Bombay High Court",
        notes="Final arguments on Interim Application",
        matter_id=m1.id,
        source_hearing_id=h1.id,
        status="Active",
        is_demo=True,
    )
    ev_clash = CalendarEvent(
        event_type="MeetingPersonal",
        title="Client Conference — Kaveri Arbitration Strategy (Demo)",
        event_date=today,
        start_time="11:45",
        end_time="12:45",
        is_all_day_or_session=False,
        location="Chambers",
        notes="Intentional demo clash with 11:00 High Court slot",
        matter_id=m3.id,
        status="Active",
        is_demo=True,
    )
    ev3 = CalendarEvent(
        event_type="Court",
        title=f"{m3.title} ({m3.court_forum})",
        event_date=today,
        start_time="14:30",
        end_time="16:30",
        is_all_day_or_session=False,
        location="Arbitration Centre, Nariman Point",
        notes="Cross-examination of CW-1",
        matter_id=m3.id,
        source_hearing_id=h3.id,
        status="Active",
        is_demo=True,
    )
    ev2 = CalendarEvent(
        event_type="Court",
        title=f"{m2.title} ({m2.court_forum})",
        event_date=next_week,
        is_all_day_or_session=True,
        location="City Civil Court, Mumbai",
        notes="Reply to Leave to Defend",
        matter_id=m2.id,
        source_hearing_id=h2.id,
        status="Active",
        is_demo=True,
    )
    ev4 = CalendarEvent(
        event_type="Court",
        title=f"{m4.title} ({m4.court_forum})",
        event_date=tomorrow,
        is_all_day_or_session=True,
        location="NCLT Mumbai Bench II",
        notes="Rejoinder & admission arguments",
        matter_id=m4.id,
        source_hearing_id=h4.id,
        status="Active",
        is_demo=True,
    )
    ev_deadline = CalendarEvent(
        event_type="Deadline",
        title="Filing Deadline: Rejoinder Affidavit in CP(IB)/310/2026 (Demo)",
        event_date=today + timedelta(days=3),
        is_all_day_or_session=True,
        location="NCLT Registry",
        matter_id=m4.id,
        status="Active",
        is_demo=True,
    )
    db.add_all([ev1, ev_clash, ev3, ev2, ev4, ev_deadline])
    db.flush()

    db.add_all([
        EventAdvocate(event_id=ev1.id, advocate_id=adv_main.id),
        EventAdvocate(event_id=ev_clash.id, advocate_id=adv_main.id),
        EventAdvocate(event_id=ev3.id, advocate_id=adv_main.id),
    ])

    # Tasks & Deadlines
    db.add_all([
        Task(
            title="File Affidavit in Rejoinder (Demo)",
            task_type="Deadline",
            due_date=today + timedelta(days=3),
            priority="Urgent",
            status="Open",
            matter_id=m4.id,
            client_id=c3.id,
            is_demo=True,
        ),
        Task(
            title="Finalize Cross-Examination Questions for CW-1 (Demo)",
            task_type="Office Task",
            due_date=today,
            priority="High",
            status="Open",
            matter_id=m3.id,
            client_id=c2.id,
            is_demo=True,
        ),
        Task(
            title="Check indexed compilation pagination (Demo)",
            task_type="Office Task",
            due_date=tomorrow,
            priority="Normal",
            status="Open",
            matter_id=m1.id,
            client_id=c1.id,
            is_demo=True,
        ),
    ])

    # Sample Fee Memos (matching Proforma pattern)
    memo1 = FeeMemo(
        bill_number="AO/2026-27/001",
        bill_date=last_week,
        due_date=two_weeks,
        addressee_client_id=c1.id,
        addressee_name="Vardhman Textiles Pvt. Ltd. (Demo)",
        addressee_address="Fort, Mumbai (Synthetic Demo Address)",
        subject_line="Memo of Professional Fees — Commercial Suit & Summary Suit (Demo)",
        status="Issued",
        issued_at=ist_now(),
        notes="Fictional demo fee memo illustrating gross-to-net adjustment and part payment.",
        footer_bank_details="Demo Bank Details Only",
        is_demo=True,
    )
    memo1.lines = [
        FeeMemoLine(sort_order=0, category="Appearances", description="Appearances before Hon'ble Bombay High Court & City Civil Court", quantity=Decimal("14"), unit_rate=Decimal("5000"), line_amount=Decimal("70000")),
        FeeMemoLine(sort_order=1, category="Drafting", description="Drafting Plaint, Interim Application & Summons for Judgment", quantity=Decimal("4"), unit_rate=Decimal("25000"), line_amount=Decimal("100000")),
    ]
    memo1.adjustments = [
        FeeAdjustment(reason="Agreed lumpsum adjustment / credit (Demo)", signed_amount=Decimal("-50000")),
    ]
    _recalculate_memo_totals(memo1)
    db.add(memo1)
    db.flush()
    db.add_all([
        FeeMemoMatter(memo_id=memo1.id, matter_id=m1.id),
        FeeMemoMatter(memo_id=memo1.id, matter_id=m2.id),
        Payment(
            memo_id=memo1.id,
            payment_date=yesterday,
            amount=Decimal("45000"),
            method="NEFT/RTGS",
            reference_no="DEMO-UTR-001",
            notes="Part payment received (Demo)",
            recorded_by="demo_advocate",
        ),
    ])
    memo1.status = "Partially Paid"

    memo2 = FeeMemo(
        bill_number="AO/2026-27/002",
        bill_date=today,
        due_date=two_weeks,
        addressee_client_id=c2.id,
        addressee_name="Kaveri Infrastructure LLP (Demo)",
        subject_line="Draft Memo — Arbitration Proceedings ARB/19/2026 (Demo)",
        status="Draft",
        notes="Editable draft memo.",
        is_demo=True,
    )
    memo2.lines = [
        FeeMemoLine(sort_order=0, category="Appearances", description="Arbitration Hearings (Procedural & Evidence)", quantity=Decimal("3"), unit_rate=Decimal("15000"), line_amount=Decimal("45000")),
    ]
    _recalculate_memo_totals(memo2)
    db.add(memo2)
    db.flush()
    db.add(FeeMemoMatter(memo_id=memo2.id, matter_id=m3.id))

    # Bare Act placeholders
    db.add_all([
        BareAct(
            title="Code of Civil Procedure, 1908",
            short_title="CPC",
            year=1908,
            jurisdiction="India",
            version_date="Placeholder Entry",
            source_note="Local placeholder only — upload verified legislation PDF/text before relying.",
            tags="civil, jurisdiction, commercial",
            summary_text="Sample placeholder entry for local Bare Acts library.",
            is_demo=True,
        ),
        BareAct(
            title="Arbitration and Conciliation Act, 1996",
            short_title="Arbitration Act",
            year=1996,
            jurisdiction="India",
            version_date="Placeholder Entry",
            source_note="Local placeholder only — upload verified legislation PDF/text before relying.",
            tags="arbitration, section 9, section 11, section 34",
            summary_text="Sample placeholder entry for local Bare Acts library.",
            is_demo=True,
        ),
    ])

    db.commit()


def reset_demo_data(db: Session, actor: str) -> None:
    """Create a snapshot, delete demo records, and re-seed fresh synthetic demo data."""
    create_snapshot("reset_demo_data")
    for model in (
        Payment,
        FeeAdjustment,
        FeeMemoLine,
        FeeMemoMatter,
        FeeMemo,
        Task,
        EventAdvocate,
        CalendarEvent,
        HearingAdvocate,
        Hearing,
        MatterAdvocate,
        ClientMatter,
        Matter,
        Client,
        Advocate,
        BareAct,
    ):
        db.query(model).delete()
    db.commit()
    ensure_seed_data(db)
    record_audit(db, actor=actor, entity_type="system", entity_id="demo", action="reset_demo_data")
    db.commit()

