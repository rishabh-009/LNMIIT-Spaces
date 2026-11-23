# Project Selection Report — Why This Project

**Project:** LNMIIT Spaces — Room Discovery & Booking System
**Institution:** The LNM Institute of Information Technology (LNMIIT), Jaipur
**Document status:** v1.0

This report records why the project was chosen, what alternatives were considered and rejected, the objectives and scope agreed upon, and the feasibility assessment that justified the selection. It complements the [High-Level Design](HLD.md) and [Low-Level Design](LLD.md).

---

## 1. Problem statement

A residential institute like LNMIIT runs a dense daily schedule across a fixed set of physical spaces — lecture theatres, lecture halls, labs, study rooms, and meeting rooms. Three groups constantly compete for these spaces:

- **Students and clubs** needing rooms for practice sessions, workshops, and meetings;
- **Faculty and teaching assistants** needing labs or theatres for extra classes and exams;
- **Administration** needing to publish timetable blocks that ordinary bookings must not collide with.

Without a system, this coordination happens over calls, spreadsheets, and physical registers. The failure modes are familiar to anyone on campus:

| Failure | Consequence |
|---|---|
| No single source of truth for "is this room free right now?" | Walk to the room and find it occupied |
| Double bookings resolved manually | Wasted slots, friction between groups |
| Invisible academic schedule | Event bookings clash with timetabled classes |
| No utilization record | Administration cannot see which spaces are actually used |
| Ad-hoc approval trails (messages, verbal nods) | No accountability for who booked what |

**The problem, in one line:** the institute lacks a lightweight, authoritative, always-current view of campus space availability and a fair, conflict-free way to reserve it.

## 2. Proposed solution

A web application, owned by the institute, that:

1. Publishes the **active room inventory** with type and capacity filters;
2. Shows **daily confirmed schedules** per room — public, no login needed to look;
3. Accepts **bookings** from signed-in users with subject/batch/purpose metadata and **guarantees no double-booking** through transactional overlap detection;
4. Lets users **cancel their own** bookings, and gives **admins** the power to cancel anything, manage inventory, and place **academic blocks**;
5. Exposes the same operations as a **JSON API** so future campus tools can integrate.

## 3. Why this project (selection rationale)

### 3.1 Real, observable need

The problem is not hypothetical — it occurs on campus every week during event seasons and exam periods. A project that its own developers (and friends) would actually use provides continuous validation and honest feedback, which toy projects never receive.

### 3.2 Correct level of ambition

The problem is *bounded but not trivial*. It has:

- A genuinely **concurrent core** — two users racing to book the same slot is a real distributed-systems microcosm (transaction serialisation, an overlap invariant, meaningful conflict semantics);
- Real **security surface** — authentication, authorisation (user vs admin), CSRF, session management, password storage — each individually small, together a faithful model of any production web system;
- Real **data modelling** — users, rooms, bookings, lifecycle states, and constraints that must agree at the schema, service, and UI layers;
- A **finished-product shape** — inventory management, schedules, bookings, cancellations, and an admin console form a complete vertical slice, so "done" is definable.

### 3.3 Learning value per unit of complexity

Every feature maps to a transferable engineering lesson rather than glue code:

| Feature | Transferable lesson |
|---|---|
| Overlap detection under `BEGIN IMMEDIATE` | Transactions, isolation, why check-then-insert needs serialisation |
| Session store with expiry, eviction, fixation defence | Authenticated web security end to end |
| CSRF token + Origin verification | Same-origin policy and request forgery class of attacks |
| PBKDF2 with dummy-hash timing defence | Password storage and side-channel thinking |
| Pydantic models beside hand-rolled validators | Input validation at trust boundaries |
| Server-rendered pages **and** a REST API in one app | Two interface paradigms over one service layer |
| SQLite `BUSY` → HTTP 503 mapping | Turning infrastructure failure into honest API semantics |

### 3.4 Deployable and demonstrable

The whole system runs from one command with zero external services (no message broker, no separate DB server). That makes it demonstrable in a lab review, deployable on the cheapest available institute hardware, and maintainable by a student team after the original authors graduate — often the deciding factor for whether campus software survives.

### 3.5 Institutional fit

Room booking is naturally institute-specific (room types, academic blocks, roll-number identity from `@lnmiit.ac.in` emails). A generic clone of a commercial product would not fit; this project produces something that belongs to LNMIIT specifically, increasing both its usefulness and the team's sense of ownership.

## 4. Alternatives considered and rejected

| Candidate project | Why rejected |
|---|---|
| **E-commerce storefront clone** | Heaviest common tutorial domain; nothing new to discover, no real users, payments add legal/external dependencies without teaching system design |
| **Chat / messaging app** | Value concentrates in real-time infrastructure (WebSockets at scale) that a campus cannot exercise meaningfully; hard to demo "done" |
| **Library management system** | Close cousin, but the interesting part is cataloguing/borrowing rules that a small institute library already solves with off-the-shelf software |
| **Attendance / time-table generator** | Timetabling is NP-hard constraint solving — fascinating, but the solution would be opaque to users and the feedback loop too slow for iterative development |
| **Campus news / events portal** | Content problem, not a systems problem; almost no concurrency, integrity, or authorisation depth |
| **Reusing a commercial booking SaaS** | Wrong trust model: booking data and account emails stay inside the institute; also no learning outcome — the selection is a *learning and ownership* exercise |

The room-booking system uniquely combined a live user base, a hard integrity invariant, a real authorisation hierarchy, and single-command deployability.

## 5. Technology selection and justification

| Decision | Alternatives | Why the choice wins here |
|---|---|---|
| **FastAPI** | Django, Flask, Express/Nest | One framework cleanly hosts SSR pages *and* a typed JSON API; Pydantic gives declarative request models; async ASGI leaves headroom; smaller surface than Django for a bounded app |
| **Server-rendered Jinja2 + progressive CSS** | React/Vue SPA | An SPA duplicates state on the client and complicates CSRF/auth flows for marginal benefit in a form-centric app; SSR keeps security logic in one place and the page works before JS |
| **SQLite via `sqlite3`** | PostgreSQL, MySQL, an ORM | Single-file, zero-admin, institute-portable; `BEGIN IMMEDIATE` provides exactly the write serialisation the overlap invariant needs at campus scale; no ORM keeps SQL inspectable and auditable — the point of the learning exercise |
| **Self-hosted PBKDF2 + server-side sessions** | OAuth with an external IdP, JWTs | No external dependency on the institute network; opaque server-side sessions are the simplest construct that is also revocable and fixation-safe; PBKDF2-HMAC-SHA256 at 600k rounds follows current OWASP guidance |
| **In-memory session map** | Redis, DB-backed sessions | Honest scope decision: single-process deployment, bounded users; restart-logs-out is acceptable (documented in HLD §9) and the encapsulation in `auth.py` makes a later swap mechanical |
| **Hand-rolled CSS design system** | Tailwind, Bootstrap, component kits | Full control of accessibility details (focus outlines, skip links, responsive tables) and no build pipeline; the UI is small enough that a framework would dominate the dependency footprint |

> [!NOTE]
> Each rejected alternative remains a documented evolution path rather than a mistake — see the trade-off table in the [HLD §9](HLD.md#9-constraints-trade-offs-and-future-scope).

## 6. Objectives

### 6.1 Primary objectives

1. Single authoritative view of **active room inventory** with type and capacity filters.
2. **Conflict-free booking**: it must be impossible, by construction, for two confirmed bookings to overlap in the same room.
3. Role model: **users** book/cancel their own; **admins** manage inventory, cancel anything, and place academic blocks.
4. Security baseline: hashed passwords, server-side sessions, CSRF protection, secure cookies/headers, fully parameterised SQL.
5. Dual interface: accessible server-rendered pages **and** a documented JSON API.
6. Single-command startup with automatic schema bootstrap and seed inventory.

### 6.2 Success criteria

- [x] Overlap invariant holds under concurrent booking attempts (transaction + index-backed check)
- [x] All mutating routes require a valid CSRF token and same-origin requests
- [x] Login does not leak account existence through timing
- [x] Restart invalidates all sessions; cookies carry no authority
- [x] Rooms deactivated by admins vanish from discovery and reject new bookings
- [x] Public schedule browsing works without an account
- [x] App runs from `uvicorn app.main:app` with no manual DB setup

## 7. Scope

### 7.1 In scope (v1)

- Room discovery (type/capacity filters), daily schedules, booking creation with validation and 409 conflicts, cancellations with ownership rules, admin console (inventory CRUD-lite, activation toggles, all-bookings view, academic blocking), CLI account provisioning, JSON API, campus-timezone semantics, accessibility-conscious responsive UI.

### 7.2 Explicitly out of scope (v1)

Recurring/series bookings, waitlists and booking queues, email or push notifications, file attachments, payments, institute SSO integration, multi-campus support, attendance tracking, analytics dashboards, and recurring-batch timetable import. Each is a scoped extension that does not require re-architecting (HLD §9).

## 8. Feasibility study

### 8.1 Technical

Every required capability — transactional overlap rejection, session auth, CSRF, SSR + REST — is achievable with the chosen stack and well within the team's competence ramp. The only hard invariant (no double-booking) reduces to a half-open interval intersection query inside a write transaction, which SQLite's locking model guarantees. **Verdict: feasible.**

### 8.2 Operational

Zero external services beyond the app process; the database is one file on local disk, trivially backed up by copying. Administration is limited to running `create_user` for account provisioning. The failure mode (process down) is recoverable by restart with no state loss beyond sessions. **Verdict: feasible for a student-run deployment.**

### 8.3 Economic

Software cost is zero (open-source stack, institute hardware). Development effort is the only real cost; the module breakdown (HLD §3.2) supports parallel work — auth, booking logic, and UI are separable with stable interfaces. **Verdict: feasible.**

### 8.4 Schedule

The vertical-slice architecture allows the system to reach *usable* early (browse → book → cancel) and then harden (security, admin console, API). Risky items (overlap correctness, CSRF) are core-path, not bolt-ons, so they are exercised from the first integration test onward rather than discovered late. **Verdict: feasible within an academic timeline.**

### 8.5 Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Concurrent-booking correctness bug | Medium | High (core promise) | Overlap check inside `BEGIN IMMEDIATE`; index-shaped query; verification checklist in LLD §10 |
| SQLite write contention under event rushes | Medium | Medium | 5 s busy timeout; honest 503 with retry semantics; documented PostgreSQL migration path |
| Session loss on restart annoys users | Certain | Low | Re-login is cheap; lifetime is 10 h; behaviour documented |
| Weak/leaked admin password | Medium | High | 12-char minimum, PBKDF2-600k, timing-safe login; account creation restricted to CLI operators |
| Scope creep (notifications, SSO, recurring) | High | Medium | Explicit out-of-scope list (§7.2) approved as part of selection |
| Demo inventory mistaken for real data | Low | Low | Seeded rooms labelled as demo in documentation; inventory editable from admin console |

## 9. Expected outcomes

### 9.1 For the institute

- A live, trustworthy answer to "which rooms are free today?";
- Elimination of double-booking disputes and manual approval trails;
- An auditable record of who reserved what, and why (purpose is mandatory);
- A foundation that future campus tooling can build on via the JSON API.

### 9.2 For the team

- End-to-end ownership of a deployed system: requirements → security design → implementation → documentation (this set);
- Working knowledge of transactional integrity, web auth security, and API design that transfers directly to production engineering;
- A portfolio artifact with real users rather than a scaffold clone.

## 10. Conclusion

LNMIIT Spaces was selected because it sits at the intersection of a **real campus need**, a **technically honest core problem** (concurrent, security-sensitive, integrity-critical), and **sustainable operability** (single process, zero external dependencies). The rejected alternatives each failed at least one of these: they were either artificial, infrastructure-dominated without users, already solved by off-the-shelf software, or opaque systems with slow feedback loops. The chosen scope delivers a complete, defensible, and genuinely useful system — and every deliberate constraint has a documented evolution path rather than a dead end.

---

**Related:** [High-Level Design](HLD.md) · [Low-Level Design](LLD.md) · [`../README.md`](../README.md)
