# AdvocateOS — Master Development Prompt for an AI Coding Agent

**Project:** AdvocateOS — Private Legal Office Management System  
**Prepared for:** An independent advocate practising in Mumbai, India  
**Document purpose:** A complete, execution-oriented instruction to an AI software-development agent.  
**Version:** 1.0 — 12 November 2026  
**User role:** Legal professional and product owner, **not a developer**.

---

## 0. Instructions to the AI development agent

You are the **lead product designer, frontend engineer, backend engineer, QA engineer and technical implementation guide** for AdvocateOS. **Build and run a usable application; do not stop at writing a proposal, screenshots or pseudocode.**

The product owner is an advocate, not a programmer. Communicate in plain English, offer short, actionable steps, handle routine technical decisions yourself, and do not burden the owner with decisions about languages, frameworks, file structure, databases, or architecture.

### Decisions you own

- **You decide** the technology stack, architecture, project layout, programming languages, tooling, database, deployment pattern and testing framework. Choose sensible, maintainable tools appropriate to a small independent legal practice.
- **Keep frontend and backend clearly separated** in both the implemented code and your explanations. This requirement does not mandate a particular repository structure or framework; establish and document explicit interfaces and boundaries.
- Prefer low ongoing costs, simple installation, offline/local-first operation where practicable, reliable persistence, responsive Mac/iPad/iPhone use, secure backups and easy future maintenance.
- Reuse any supplied prototype when appropriate, but feel free to refactor or replace its implementation if justified. Preserve user data and behaviour; never silently overwrite it.
- Start with **fictional demonstration data**. Do not migrate or use actual client data until security controls are implemented, reviewed and approved.
- There is **no need for court APIs, Apple Calendar APIs, Google Calendar APIs, online case-status scraping or automatic external synchronisation**. The advocate will enter hearing and office information manually every day. **Internal automation** is required when that information is saved.
- The **Law → Research** feature is a future/optional AI integration. Do not make the core office management dependent on any AI API. If an embedded ChatGPT-powered research experience is eventually implemented, it may require a supported integration or separately provisioned API credentials. Never hardcode secrets or claim that ChatGPT subscriptions automatically include API credits.
- Do **not** recommend, reference, integrate, link to or cite the legal research service expressly excluded by the product owner in earlier conversation.
- Do not use live private client information to develop or demonstrate the application.

### Execution approach

1. Inventory the supplied files and any prior prototype. Report what was actually found (never invent results).
2. Produce a brief functional scope and the implementation sequence, then **implement Phase 1 immediately**.
3. Build both frontend and backend, connect them to real persistent data, seed fictional records, and run the app locally.
4. Run automated checks plus manual scenario tests. Fix failures.
5. Deliver user-friendly launch instructions, screenshots if available, a short administrator guide, backup/restore directions and a documented change list.
6. Work in incremental, demonstrable milestones. Do not ask the owner to manually write code. Explain technical blockers with an exact next action.

**Definition of done:** The owner can open AdvocateOS, add a fictional matter, enter a hearing outcome, select appearing advocates, set a next date, view the resulting calendar and month/client records, prepare a draft fee memo, record part-payment and see the correct balance — all without editing code or a database directly.

---

# 1. Product vision and scope

AdvocateOS is **primarily a legal office administration and management application**, not a general legal research bot. Its immediate purpose is to replace duplicated spreadsheet entry and fragmented office reminders with a central, reliable record.

**Core principle: Enter information once; automatically update every relevant view.**

The advocate currently maintains two Google Sheets/Excel-style records:

1. **Monthly matters list**: monthly worksheets, used as a chronological appearance/business-conducted register.
2. **Client-wise list**: matters grouped under clients, used to see the present position of a client's matters.

The new application should preserve the logic and familiar output of those records while using a single central matter database and immutable historical hearing entries.

### Success outcomes

- Every listed matter is visible on the correct day's board.
- Every matter heard today is clearly marked **Updated** or **Awaiting hearing update**.
- Future dates entered in hearing updates appear automatically in the app's calendar and upcoming boards.
- Important deadlines and office tasks receive visible reminders.
- Court, meeting and personal events are visible together for conflict detection.
- Work done can be selected for a professionally formatted fee memo.
- Every memo has auditable draft/issued/partial/paid status and payment history.
- Client-wise and month-wise reports are generated from the same underlying data; historical entries are never overwritten when a case is adjourned.
- The system works without external case-status integrations.

---

# 2. Navigation — EXACTLY five primary tabs

The order of primary navigation is non-negotiable:

| Position | Tab | Purpose |
|---|---|---|
| 1 | **Calendar** | iOS-inspired month view and integrated office/personal diary |
| 2 | **Matters** | Add matters; monthly list; client-wise list; matter record and hearing updates |
| 3 | **Home** | **Default landing screen**; office pinboard |
| 4 | **Memo of Fees** | Draft/issue fee memos, letterhead and fees settings, payment tracking |
| 5 | **Law** | Bare Acts library and a separate general-law research space |

- On **every fresh app launch**, show **Home** by default, even though it is the third tab.
- Navigation must be obvious on desktop and mobile. On phones, use a five-item bottom navigation if suitable; on desktop, a clear sidebar or top navigation is acceptable. Maintain exact tab ordering and Home-default behaviour.
- All tab contents must load meaningful data from application storage, not hardcoded examples after prototype mode.

---

# 3. FRONTEND — screen-by-screen design and interactions

The following wireframes are **functional references for the interactive prototypes previously demonstrated**. They are not pixel-perfect constraints. Aim for a polished, restrained, professional legal-office interface with excellent readability and accessibility. Use the provided iOS Calendar screenshot as inspiration, not a literal proprietary clone.

## 3.1 Shared visual style

- Clean, modern, professional, low-clutter aesthetic; neutral background, clear contrast, comfortable spacing.
- Responsive for Mac browser, iPad and iPhone.
- Large dates and readable case titles; badges for urgent, overdue, unverified, partially paid, completed.
- Colour is supplementary: all statuses must also have clear text, icons or labels.
- Date format appropriate for India (e.g. `12 Nov 2026` / `12.11.2026`), currency `₹`, Indian-number formatting, time zone `Asia/Kolkata`.
- Support quick filtering and search, keyboard navigation, mobile touch targets, confirmation for destructive actions, and accessible labels.
- Indicate **Demo / Fictional Data** conspicuously in the initial seeded environment.

## 3.2 Prototype A — HOME (default pinboard)

```text
┌────────────────────────────────────────────────────────────────────┐
│ AdvocateOS                                      Search  Settings │
│ [Calendar] [Matters] [HOME •] [Memo of Fees] [Law]               │
├────────────────────────────────────────────────────────────────────┤
│ OFFICE PIN BOARD                                                   │
│ Thursday, 12 November 2026                LIVE CLOCK  02:32 PM IST│
│                                                                    │
│ [Matters listed today: 4] [Deadlines: 2] [Tasks: 3] [Updates: 1] │
│                                                                    │
│ TODAY'S BOARD                       [View tomorrow] [Add event]    │
│ 11:00  Bombay High Court | ABC Ltd v XYZ Ltd | Arguments          │
│ 14:30  Arbitral Tribunal| Sample Arbitration | Evidence           │
│                                                                    │
│ IMPORTANT DEADLINES                                                │
│ ! Affidavit due 16 Nov | linked matter | [Open]                   │
│                                                                    │
│ TO-DO LIST                                                         │
│ [ ] Review affidavit [ ] Prepare notes [+ Add task]               │
│                                                                    │
│ END-OF-DAY UPDATES                                                 │
│ 3 matters updated / 4 listed; 1 still pending [Update hearing]    │
│                                                                    │
│ UNBILLED WORK / PENDING MEMOS                                      │
│ 2 appearance items await memo [Review]                            │
└────────────────────────────────────────────────────────────────────┘
```

### Frontend behaviour

- Display **current local date** and a **running clock**; update the displayed time while the app is open.
- Show today's listed hearings in time/session order with court, matter, purpose, advocate assignment, hearing-update status.
- Separate **important deadlines** from hearings; show due today, overdue, upcoming.
- Show editable **to-do list** (add, check, edit, defer, complete) with linked matters optional.
- Show **tomorrow's board** and a quick switch for upcoming days.
- Show alerts for calendar clashes, overdue work, hearings without end-of-day updates, unbilled appearance/drafting records and pending fee memos.
- Provide one-click opening of the relevant matter, task, calendar event or fee record.
- Do not create an assumption of court data completeness; all data reflects entries provided by the office.

## 3.3 Prototype B — CALENDAR (iOS-inspired)

Reference: user-provided screenshot `November 2026.png`. Recreate its **layout pattern**: prominent month heading, Sun–Sat grid, compact colour markers under dates, selected date emphasis and list of events beneath the calendar. Include prev/next month navigation, Today shortcut and Add event.

```text
┌────────────────────────────────────────────────────────────────────┐
│ <  November 2026  >                             [Today] [ + ]     │
│ Sun      Mon      Tue      Wed      Thu      Fri      Sat          │
│  1        2 •      3••      4        5 •      6        7           │
│  8 •      9 •     10       11 •     (12)•    13       14 •         │
│ 15 •     16 !     17       18 •     19       20       21           │
│ 22       23       24 ••    25 •     26       27 •     28           │
│ 29       30                                                       │
│                                                                    │
│ SELECTED: THURSDAY 12 NOVEMBER                                     │
│ | 11:00 AM   Court hearing — Sample Commercial Suit               │
│ | 04:00 PM   Conference with Counsel                              │
└────────────────────────────────────────────────────────────────────┘
Legend: Blue = Court | Orange = Deadline | Green = Meeting/personal
        Purple = Office task
```

### Calendar features

- Month view as primary; optional day/week list.
- Click/tap a day to display **all events on that day** below, in chronological order; clearly separate all-day or untimed events.
- Manually add/edit: court hearing, arbitration, filing deadline, office task, meeting, personal commitment, travel or custom event.
- Each event should support start/end time or **session/untimed**; optional location, notes, client/matter link, assigned advocate and reminder preferences.
- Display colour-coded event markers. When several event types occur on a day, show multiple markers or a clear overflow indicator.
- **Detect potential clashes** among time-bounded events. Warn rather than block saving; distinguish definite overlap from ambiguous all-day / court session listings.
- Show only locally entered events; do not claim live court listing sync.
- Future hearing date created through the end-of-day hearing form must appear here automatically; do not duplicate the same event upon edits.
- Internal reminders can be shown in-app. Background alerts outside an open application require an explicitly designed local notification/scheduler mechanism; **do not claim that notifications will fire when a local server and app are closed unless implemented and tested**.

## 3.4 Prototype C — MATTERS

```text
MATTERS
[+ Add New Matter]  [Monthly List]  [Client-Wise List]
[Search matter/client/court/case number]

MATTER DETAIL — Example Commercial Suit
Client: Fictional Client A           Court: Bombay High Court
Case #: Sample/2026                  Status: Active
Next date: 25 Nov 2026              Stage: Further Arguments

[Overview] [Hearing History] [Tasks & Deadlines] [Documents (later)]
          [Appearances & Billable Work] [Linked Fee Memos]

HEARING HISTORY
12 Nov 2026 | Arguments heard | Advocates: A, B | Next: 25 Nov
04 Nov 2026 | Reply filed      | Advocates: A    | Next: 12 Nov

[+ Enter Hearing Update]
```

### Three mandatory sub-sections

**A. Add Matter**

Minimum fields: client, party/title, court/forum, case type, case number, optional connected cases, stage, status, next hearing date, next purpose, assigned advocates, notes. Support client creation without leaving the workflow. Validate possible duplicates without incorrectly merging distinct proceedings.

**B. Monthly Matters List** — preserve the current spreadsheet's columns and order:

`Sr. No. | Matter | Court | Present Date | Next Date | Business Conducted | Stage`

- Allow month/year selection, filter/search, print/export to Excel-compatible file.
- Preserve appearance history, including earlier next dates, even if the matter is later adjourned.
- Multiple hearings of the same matter within one month are separate entries.
- Where there was no appearance/business that month, do not fabricate a row merely because a matter exists.

**C. Client-Wise List** — preserve current client grouping and columns:

`Sr. No. | Matter | Court | Next Date | Stage`

- Group matters under client names, allow expand/collapse, search, filter, print/export.
- Show current next date/stage based on the latest **confirmed saved entry**, not by overwriting historical rows.
- Support one client with multiple matters and, when relevant, more than one client attached to a matter with clear billing responsibility.

**D. Hearing history / matter detail**

- Each matter must have chronological hearing records, optional notes and tasks, linked future calendar events and billable work items.
- If a case is disposed of/reserved/no date fixed, allow the next-date field to be absent without incorrectly scheduling a hearing.
- Show which hearing updates are complete, pending or amended.

## 3.5 Prototype D — END-OF-DAY HEARING UPDATE (mandatory)

The application must prompt the office at day-end or when a matter is marked heard/over. The prompt can be a prominently visible workflow in the app; do not imply the program can interrupt the user when closed unless that capability actually exists.

```text
END-OF-DAY HEARING UPDATE                  Matter: [Select ▾]

1. What happened in the matter today?
   [ Multiline text — business conducted / court directions           ]

2. Who appeared? (MULTI-SELECT CHECKBOXES)
   [x] Janam Sonpal          [ ] Another saved advocate
   [ ] Additional advocate   [+ Add Advocate Name]

3. What is the next date?
   [ Date picker ]  [ ] Next date not fixed / reserved / disposed

4. What business is to be conducted on the next date?
   [ Multiline text — filing, arguments, evidence, cross, etc.        ]

[Save Hearing Update]    [Save Draft]    [Cancel]
```

### Specific rules

1. Show today's matters still awaiting updates, including matter title/court/time.
2. **What happened?** Required to mark a hearing update complete; save a draft if incomplete.
3. **Who appeared?** Show saved advocate names as **checkboxes**, allow multiple advocates and `+ Add Advocate`; new names persist for future use. Require at least one selection for a completed appearance unless marked as *not taken up / no appearance / other explicitly stated outcome*.
4. **Next date:** Date picker; support `not fixed`, `reserved for orders`, `disposed`, `awaiting notification`. Ask for next-date business where a next hearing is fixed. If an earlier date is intentionally entered (e.g. corrective historic record), warn and allow authorised correction with reason.
5. **Next date business:** Free text, optionally suggested standard labels such as arguments, filing reply, evidence, cross-examination, directions.
6. Save atomically so the **hearing history, client's current matter status, monthly list, next calendar event, next daily board and billing work candidate** stay in sync.
7. Prevent duplicated hearing records and duplicated future calendar events from repeated form submission. Edits must be revisioned/audited.
8. After save, offer `Create task/deadline?` and `Add as billable appearance?`; allow manual override, because not every appearance is billable.
9. The Home page shows `X updated / Y listed today`, with a direct link to any missing updates.

## 3.6 Prototype E — MEMO OF FEES

Use the uploaded sample `JS_24_Suraj Babulal Shah.docx` as the **format reference**, including the formal memo structure, not as an invitation to copy private banking details into a demo.

```text
MEMO OF FEES
[+ Prepare Memo] [Fee Schedule] [Letterhead Settings] [Payments]

NEW MEMO
Bill no.: [Auto + optional override]       Date: [Date]
To: [Choose client / edit addressee]
References: [Select one or several linked matters]
Letterhead: [Choose uploaded image]

PARTICULARS                                       AMOUNT (₹)
Appearances: 14 × ₹5,000                            70,000
Drafting: 4 × ₹25,000                              1,00,000
                                 Gross total        1,70,000
Adjustment / credit                               (50,000)
                                 Net total          1,20,000
(Rupees One Lakh Twenty Thousand Only)

[Save Draft] [Preview] [Export Word] [Export PDF] [Issue Memo]

PAYMENTS
[Record ₹ payment]  Received ₹____   Outstanding ₹____
```

### User-provided real-world sample structure, **figures are for illustrating calculations only**

- Bill number and date.
- Addressee/client and references to **multiple connected proceedings**.
- Itemised services: appearances, consultancy/research/due diligence, drafting.
- Example: 14 appearances × ₹5,000 = ₹70,000.
- Example: 4 drafts × ₹25,000 = ₹1,00,000.
- Gross: ₹1,70,000, adjustment: −₹50,000, net: ₹1,20,000.
- Amount in Indian Rupees **in words**, closing/signature block and optional firm/bank/payment particulars configured by the owner.

### Fee memo capabilities

- Upload an image of letterhead (PNG/JPEG, possibly WebP), preview placement, page margins and scale; choose an image suitable for printed Word/PDF output. Do not assume the letterhead occupies the whole page or duplicate a logo/banner if template already contains it.
- Maintain **editable default fee categories and rates**: appearance, drafting, research, consultancy, conference, due diligence and custom categories. Apply matter/client-specific overrides; do not hardcode rates.
- Select work items from the matter's appearance/drafting history, or add a manual line item.
- Combine multiple dates, documents and matters into one memo with line-item explanations and quantity × rate math.
- Show gross, discounts/adjustments/credits, tax if applicable and explicitly configured, final memo amount, payments received and balance due. **Payments and adjustments are not the same**.
- Prevent double-billing of the same completed work item or warn and require deliberate approval of exceptions.
- Generate Word and PDF outputs preserving professional layout; render test pages and check pagination, numeric accuracy, amount-in-words, and letterhead placement.
- Record unique bill numbers, optionally editable, without collision; do not reuse numbers after issuance.
- Workflow status: **Draft → Issued → Partially Paid / Paid**; separately flag **Overdue** when due date passes. Support cancelled/voided memos with history, not silent deletion.
- Record any number of partial payments with date, amount, payment mode, reference and notes. Do not allow a total greater than the memo amount without an explicit credit/overpayment policy.
- Client-wise and month-wise billing and collections views, outstanding and ageing dashboard, unbilled work queue.
- Never send memos automatically; require user approval and manual issuance/export.

## 3.7 Prototype F — LAW

```text
LAW
[ Bare Acts ]  [ Research ]

BARE ACTS
[Search titles/sections]  [+ Add Act / PDF / text]
Title | Year | Version/source date | Sections | Tags

RESEARCH
[Ask a general question about a provision or legal interpretation]
[Sources to consult] [Answer] [Unverified citations warning]
```

- **Bare Acts:** Maintain a searchable, locally curated set of legislation uploaded or added by the owner with title, year, jurisdiction, amendment/version date, source and sections when parsing is feasible. Never imply the collection is complete or current merely because it is saved.
- **Research:** A separate general-law Q&A interface intended for concepts, provisions and interpretation, not autonomous court filings. **Do not activate a cloud AI connection without explicit opt-in and credentials**. While offline, provide a clearly labelled local search/notes mode or a disabled AI state with instructions.
- Research responses must distinguish source-based law from model analysis, avoid fabricated citations and present clear verification warnings.
- Never upload private case files to external AI without specific authorisation and approved data handling.

---

# 4. BACKEND — domain model and required business logic

The backend must be the **single source of truth**. The frontend may request changes, but persistence, validation, calculations, relationships, duplicate prevention and permissions are enforced by the backend.

## 4.1 Essential data entities

Design a normalized schema or equally robust equivalent for:

1. **Client**: unique ID, display name, contact information (optional), notes, active/inactive state, timestamps.
2. **Matter**: ID, title/parties, court/forum, case type, case number, stage, status, current date/purpose (derived when possible), notes, created/updated.
3. **ClientMatter association**: many-to-many if needed; role and billing client designation.
4. **Advocate**: ID, full name, active status; may be assigned to multiple matters/hearings.
5. **Hearing / Appearance record**: ID, matter ID, hearing date, timing/session, outcome/business conducted, next date, next-purpose, next-date status, entered-by, created/updated; revision history.
6. **HearingAdvocate association**: one hearing can have multiple advocates.
7. **CalendarEvent**: ID, type, start/end date/time or all-day/session flag, title, status, source hearing ID if generated, linked matter, location, notes, reminder preferences.
8. **Task / Deadline**: ID, title, type, due date/time, priority, status, optional matter/client link, owner, source hearing/event, completion metadata.
9. **BillableWorkItem**: ID, matter, related hearing or drafting task if relevant, date, category, description, quantity, unit rate, proposed amount, billing status.
10. **FeeRate / FeeAgreement**: categories/default rate plus effective per-matter/client override, as needed.
11. **FeeMemo**: ID, bill number, date, addressee, one or more referenced matters, memo status, due date, notes, totals, letterhead template reference, issuance metadata.
12. **FeeMemoLine**: ID, memo ID, work-item link if any, description, quantity, unit rate, amount, optional line adjustment.
13. **FeeAdjustment**: type/reason and signed value, distinct from payment.
14. **Payment**: ID, memo ID, amount, date, payment method, transaction/reference, notes, creator and timestamps.
15. **LetterheadTemplate / OfficeSettings**: image storage reference, positioning, signature, optional billing/payment information and numbering preferences.
16. **AuditEvent**: actor, timestamp, entity, action, before/after details where appropriate; protect sensitive information in logs.
17. **User / Session / Role**: for secure use when real data is introduced, even if the MVP begins single-user and local.

Use explicit foreign keys and integrity constraints. Document deletion/archive/restore rules and how changes affect historical records.

## 4.2 Core business invariants

- A **matter** is one record; a **hearing** is a separate dated historical record; **monthly list** is a view/report of hearings, not a second source of truth.
- The client-wise list is a computed projection of clients/matters and their latest saved current status.
- Recording a hearing's next date creates or updates the appropriate future calendar event with a stable association; repeated save does not duplicate it.
- If next-date status becomes `not fixed`, `reserved` or `disposed`, remove/cancel the generated future event as appropriate while retaining audit history.
- Completing a hearing may create a **draft billable work item**, never an automatically issued fee memo.
- Issuing a memo locks financial line details or makes later amendment explicit through versioning/credit workflow.
- Net memo amount = sum of applicable line amounts plus/minus explicit adjustments and optional separately configured taxes. Balance = amount due minus valid payments and valid credits. Payments must not reduce gross charges retrospectively.
- The system must reject invalid amounts, invalid dates, duplicate bill numbers, references to nonexistent matters, unauthorised modifications, and overpayment not covered by a documented policy.
- All stored dates are timezone-aware where appropriate and consistently rendered in IST. Avoid ambiguous parsing such as `06.01.26` without an explicit date-format policy.
- The daily board, monthly report, client-wise list, calendar, billing and dashboards must agree on underlying data.
- Preserve original imported values and changes for review, instead of silently changing suspicious historic entries.

## 4.3 Data import and export

The owner will supply spreadsheet examples:

- `Monthly matters list.xlsx` — 12 month tabs; columns are `Sr. No., Matter, Court, Present Date, Next Date, Business Conducted, Stage`.
- `Client wise list.xlsx` — main party-wise grouping; columns are `Sr. No., Matter, Court, Next Date, Stage`.

Implement **preview-first import**:

1. Let the owner upload an Excel workbook (no Google API necessary).
2. Detect worksheets, client section headers, month/year, row types, empty separator rows and inconsistent formatting.
3. Show proposed mapping and **flag uncertain names, case numbers, connected proceedings, date parsing anomalies, ambiguous clients and possible duplicates**.
4. Do not infer missing data as established fact; make unresolved rows reviewable.
5. Obtain explicit confirmation before committing imported records.
6. Produce an import report: records added, matched, skipped and flagged, with traceability to original sheet/row.
7. Export monthly and client-wise reports in familiar Excel layouts; support print-ready views.

**Important:** Existing workbook formats are references, not necessarily perfect databases. The application should retain their visual familiarity without copying their structural problems.

## 4.4 Backend functions and interfaces

Provide clearly defined service/API contracts for:

- Clients: create, list, search, edit, archive.
- Matters: create, search, list, detail, edit, stage/status changes, related clients and advocates.
- Hearings: today's board, enter/update outcome, assign advocates, schedule next date, retrieve history, count missing updates.
- Calendar: list events by date/range, add/edit manual event, generated hearing events, conflict warnings.
- Tasks: create, list by due date and priority, edit, complete, link to matters.
- Billing: fee schedules, billable work, draft memo, preview/export, issue/void, payment posting, balances and ageing.
- Reporting: monthly matters, client-wise matters, tomorrow's board, fee performance, unbilled work.
- Settings: letterhead, numbering, regional options, office details, backups.
- Law module: upload/search approved Bare Acts; research connector disabled unless configured.

Backend functions should be accessible via documented interfaces; automated tests should call those interfaces directly. Choose the actual protocol and implementation yourself.

## 4.5 Reminders and clash logic

- **Court hearing**: remind in the app the previous evening and on the date; configuration can be changed.
- **Filing deadline**: default alerts at 7 days, 3 days, 1 day and on due date, configurable per deadline.
- **Unupdated hearing**: show as pending after the listed date/office closing point until updated or explicitly marked cancelled/not taken up.
- **Fee follow-up**: surface unpaid/part-paid amounts and due dates in Finance; never automatically contact clients.
- **Clashes**: warn when timed commitments overlap for the same assigned advocate; treat date-only court listings as potential, not definite, conflicts.
- When the application is closed, the system must **not promise** external notifications unless a separately implemented notification delivery method is verified. A local in-app reminder dashboard is mandatory; external push/email/SMS is optional future work.

---

# 5. SECURITY, PRIVACY AND RELIABILITY (MANDATORY)

Legal records are confidential. Even when starting with demo data, build toward production-grade safeguards:

- Start with **synthetic demo data only**; provide easy reset of fictional records.
- Do not store secrets in client-side bundles or the code repository.
- Before real data: implement proper authentication, session expiry, secure password storage or equivalent, permissions, secure access to uploaded documents, encrypted connections where networked, and appropriate encryption at rest based on threat model.
- Server-side access checks must protect every matter and financial action. Never rely on hiding UI buttons as access control.
- Validate uploads (type, size, storage path) and sanitise user input/exports; protect against injection, XSS, CSRF where applicable, and unsafe file serving.
- Maintain audit trails for changes to hearing dates, deadlines, memo totals, bill status, bank/payment settings and payments. Correct entries through edits with history, not silent deletion.
- Make **backups and restore** accessible to a nontechnical owner; test actual restoration. Explain where data is stored and how it can be exported if the application is discontinued.
- Prevent duplicate submissions, show actionable errors and preserve partially entered forms where appropriate.
- Choose storage/hosting with suitable legal professional confidentiality, data retention, access and applicable Indian law considerations. Document trade-offs rather than claiming legal compliance automatically.
- No background transfer of private documents to AI services, analytics vendors or third parties without explicit authorisation.
- Exports containing sensitive information must only be initiated by authorised users.
- The MVP is **not approved for real client data** until the production security checklist is complete.

---

# 6. IMPLEMENTATION MILESTONES (frontend/backend separated)

## Phase 1 — Working office-management MVP (build now)

**Frontend deliverables**
- Five primary tabs with exact ordering; Home default.
- Home pinboard with live clock, today/tomorrow's board, to-do list and pending updates.
- Month-view calendar with day details, colour indicators, manual event entry, basic clash warning.
- Matters: add matter, searchable list, matter detail, monthly report, client-wise report.
- End-of-day hearing update form with multiple advocate checkboxes, add advocate, next date and next business.
- Memo of Fees: draft screen, fee lines, basic memo preview and partial-payment interface.
- Law: Bare Acts library placeholder and a clearly marked research integration placeholder (no pretend AI functionality).

**Backend deliverables**
- Persistent client, matter, advocate, hearing, calendar event, task, billable-work, memo, line item and payment records.
- Create/read/update workflows and data validation.
- Event generation from hearing updates with no duplicates.
- Basic financial calculations and payments ledger.
- Fictional seeded dataset and sample letterhead (not actual client's letterhead).
- Backup/export and basic audit entries, with explicit MVP security limitations.

**Acceptance scenario**
1. Open app → Home is visible with IST date and running clock.
2. Create a fictional client and matter → appears in Matters/client-wise view.
3. Record a fictional hearing with advocates A and B and a future date → historical monthly row and next calendar event appear.
4. On the next date, the event is visible in daily board.
5. Add a conflicting meeting → warning is displayed.
6. Mark an appearance billable; create a fee memo with an additional drafting line and adjustment → total correct.
7. Post a partial payment → remaining due and memo status correct.
8. Restart app → records remain.

## Phase 2 — Spreadsheet compatibility and polished billing

**Frontend:** import-review UI, mapping confirmation, client/matter duplicate review, rich fee memo preview, letterhead upload, print/export, finance filters and ageing.  
**Backend:** robust Excel import/export, source-row lineage, merge-review logic, document generation, bank-detail settings, numbering rules, audit and complete reconciliation tests.

## Phase 3 — Production readiness

**Frontend:** clean setup wizard, user management if needed, settings, backup/restore UI, accessible mobile refinements.  
**Backend:** verified access security, storage protection, audit-log hardening, recovery tests, deployment documentation, data migration and secure administration.

## Phase 4 — Optional legal library and AI research

**Frontend:** searchable Bare Acts interface, optional research chat with cited sources and clear verification state.  
**Backend:** curated content management, version metadata, optional user-authorised AI provider integration, token/cost limits and confidentiality controls. Keep fully isolated from the office-management transaction engine.

**Stop after each milestone for a clear demo, but do not stop at architecture diagrams. Demonstrate functioning app workflows.**

---

# 7. Automated tests and practical QA

Write automated tests for:

- Create client → create matter → appear in both matter and client-wise views.
- Multiple hearing records for same matter preserved independently.
- Completed hearing update requires outcome and appearing advocates (or a selected exception reason).
- New advocate saved and available for later hearings.
- Future hearing generated exactly once even after repeated save.
- Future hearing corrected → linked calendar date updated, historic record preserved.
- `Next date not fixed / reserved / disposed` behaviour.
- Monthly report retains original hearing record and correctly filters across year boundaries.
- Three categories of events display under same selected date.
- Timed event overlaps generate warnings without blocking justified saving.
- Deadlines and tasks visible on Home and Calendar.
- Fee memo line arithmetic; Indian currency and amount-in-words, including adjustment and tax handling when configured.
- Partial payment and multiple payment reconciliation.
- Overpayment rejected or explicitly handled.
- Duplicate billing attempt flagged.
- Unbilled work list updated after memo issuance.
- Excel import flags suspicious dates and ambiguous matches rather than silently changing them.
- Data survives app restart; backup restored successfully.
- Unauthorised read/edit requests rejected once access controls are enabled.
- Responsive checks on narrow mobile and wide desktop layouts.

Manual UI checklist: Can a nondeveloper find today's board in 3 seconds, enter a hearing in about a minute, identify unupdated matters, locate overdue amounts, and export a professional memo without touching code?

---

# 8. Inputs and sample/reference assets

Use reference inputs when made available to the development workspace:

| File | Intended use |
|---|---|
| `November 2026.png` | Visual reference for iOS-inspired monthly calendar layout |
| `Monthly matters list.xlsx` | Existing month-wise worksheet layout and reporting semantics |
| `Client wise list.xlsx` | Existing client-grouped matter layout and reporting semantics |
| `JS_24_Suraj Babulal Shah.docx` | Existing professional memo of fees layout, headings and calculation patterns |
| `AdvocateOS-MVP.zip` / `AdvocateOS-MVP/` | Optional prior local prototype; inspect before reusing, do not presume its security or completeness |
| Letterhead image (to be supplied later) | Owner-configured printable fee memo layout |

**Reference files are sensitive.** Treat them as design material and avoid incorporating real names, contact details, payment accounts or case facts into fictional demo records. The Markdown specification is stand-alone, but the original screenshot/workbooks/document should be supplied separately to the agent if exact format matching is desired.

---

# 9. Requirements the agent must not misunderstand

1. **Manual data entry is deliberate.** It does not mean records stay disconnected: saving a hearing should internally update related views.
2. **Home is third in order but the default screen.**
3. **Calendar is first in order**, inspired by the owner's iPhone Calendar screenshot, with month grid and events below the selected date.
4. Monthly list is **historical appearances**, not merely a list of current matters.
5. Client-wise list reflects the **current stage and next date**, but historic hearings must remain intact.
6. Advocate names are selectable via **checkboxes**, including a persistent `Add Advocate` option.
7. The fee memo sample includes a **gross-to-net adjustment**, which is **not** the same thing as a payment received.
8. **One fee memo can cover multiple related matters**, and one client may have many matters.
9. Do not silently issue memos, email clients, calculate and certify limitation periods, or create external calendar entries.
10. The Law research tab is **secondary and optional**; case administration, court diary and billing are the top priorities.
11. Do not require the advocate to install a complex software toolchain if a simpler, reliable experience is possible. Provide novice-friendly installation and operation steps.
12. Never present a nonfunctional screen as a completed feature; label placeholders plainly.

---

# 10. Final deliverables to the product owner

Provide, at minimum:

- A **runnable application**, not just a specification.
- Source code with **clearly separated frontend and backend** and documented contracts.
- Readable README with **Mac-first, step-by-step installation, launch and shutdown** instructions for a nondeveloper.
- Demo login/access details if applicable, clearly identified as synthetic.
- Fictional data and an easy demo reset.
- Screenshot(s) of Home, Calendar, Matters, Hearing Update and Memo of Fees.
- Data model / entity relationship explanation in nontechnical language.
- A table of completed, partially completed and future features.
- Automated test report including failures corrected and remaining limitations.
- Instructions to back up, restore and export data.
- A security-readiness checklist indicating what must be done before entering confidential client information.
- The next recommended development milestone with an exact task prompt for a coding agent.

### What to say to the advocate at handoff

Explain simply:

1. **What can I do with AdvocateOS today?**
2. **How do I open it?**
3. **How do I add my first matter?**
4. **How do I update a hearing after court?**
5. **How do I prepare and record a fee memo?**
6. **Where is my data saved, and how do I back it up?**
7. **Which security features remain before using real client data?**

**Begin execution now. Make reasonable technical decisions independently. Ask the product owner only if a decision materially affects legal confidentiality, commercial expenditure or essential product behaviour.**
