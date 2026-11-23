# Low-Level Design (LLD)

**Project:** LNMIIT Spaces — Room Discovery & Booking System
**Document status:** v1.0
**Prerequisite reading:** [High-Level Design](HLD.md)

This document specifies the internals of every module: exact validation rules, algorithms, transaction boundaries, data contracts, and error behaviour. It mirrors the implementation in `app/` and is intended to let a maintainer verify or rebuild any component without reading the whole codebase.

---

## 1. Conventions and global constants

| Constant | Value | Defined in |
|---|---|---|
| `room_types` | `("LT", "LH", "LAB", "ROOM", "MEETING")` | `connection.py` |
| Campus timezone | `timezone(timedelta(hours=5, minutes=30))` — fixed, no DST | `bookings.py`, `main.py` |
| Cookie name | `sessionnamespaces` | `auth.py` |
| Authenticated session lifetime | 36,000 s (10 h) | `auth.py` |
| Anonymous session lifetime | 3,600 s (1 h) | `auth.py` |
| Session map cap | 4,096 entries (evict oldest expiry) | `auth.py` |
| PBKDF2 rounds | 600,000 (accept 100,000–2,000,000 on verify) | `auth.py` |
| SQLite busy timeout | 5 s per connection | `connection.py` |
| Env `SPACES_DB` | SQLite file path (default `app/data/spaces.db`) | `connection.py` |
| Env `SPACES_SECURE_COOKIE` | `1` forces `Secure` cookies | `auth.py` |

All datetimes are **campus-local, second-precision ISO strings** (`YYYY-MM-DDTHH:MM:SS`) with no offset. `campus_now()` in `main.py` produces "now" in this representation.

---

## 2. `connection.py` — data access foundation

### 2.1 `get_connection()`

1. Resolve DB path from `SPACES_DB` (default `app/data/spaces.db`); `mkdir(parents=True)` the parent.
2. `sqlite3.connect(path, timeout=5)`; set `row_factory = sqlite3.Row` (rows behave like dicts).
3. Execute `PRAGMA foreign_keys = ON` — FKs are enforced per connection in SQLite.

Connections are short-lived: every public function opens one and closes it in `finally`. No pooling (appropriate at this scale).

### 2.2 `list_rooms(room_type="", min_capacity=0)`

```sql
SELECT id, name, type, capacity, location FROM rooms WHERE is_active = 1
  [AND type = ?] [AND capacity >= ?] ORDER BY name, id
```

Both filters are appended only when truthy, with bound parameters. Returns a list of dicts.

### 2.3 `room_schedule(room_id, day)`

- Window: `start = day T00:00:00`, `end = (day+1) T00:00:00`.
- Room row fetched with `is_active = 1`; `None` means "missing or inactive" (routed to 404).
- Bookings = rows whose interval intersects the window:

```sql
SELECT start_time, end_time, booking_type FROM bookings
WHERE room_id = ? AND status = 'CONFIRMED'
  AND start_time < ? /* end-of-day  */ AND end_time > ? /* start-of-day */
ORDER BY start_time, id
```

Returns `(room_dict | None, [booking_dict, ...])`. This is the same overlap predicate used for conflict detection (§5.2) applied to a day window.

### 2.4 `setup_database()` (idempotent bootstrap)

1. Probe `sqlite_master` for an existing `rooms` table.
2. `executescript(schema.sql)` — every statement is `IF NOT EXISTS`, safe on re-runs.
3. **Only when the probe was empty** (first ever run), insert the five demo rooms:

| Name | Type | Capacity | Location |
|---|---|---|---|
| Lecture Theatre 1 | LT | 180 | Academic block |
| Lecture Hall 2 | LH | 80 | Academic block |
| Computer Lab | LAB | 40 | Lab block |
| Study Room | ROOM | 12 | Library block |
| Meeting Room | MEETING | 20 | Academic block |

4. Commit; rollback and re-raise on any error. Called from the FastAPI lifespan hook at startup.

---

## 3. Schema (`schema.sql`)

```sql
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL CHECK (length(trim(name)) > 0),
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'admin'))
);

CREATE TABLE IF NOT EXISTS rooms (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL CHECK (length(trim(name)) > 0),
    type TEXT NOT NULL CHECK (type IN ('LT','LH','LAB','ROOM','MEETING')),
    capacity INTEGER NOT NULL CHECK (capacity > 0),
    location TEXT NOT NULL CHECK (length(trim(location)) > 0),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
);

CREATE TABLE IF NOT EXISTS bookings (
    id INTEGER PRIMARY KEY,
    room_id INTEGER NOT NULL REFERENCES rooms(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL CHECK (end_time > start_time),
    subject TEXT NOT NULL DEFAULT '',
    batch TEXT NOT NULL DEFAULT '',
    purpose TEXT NOT NULL CHECK (length(trim(purpose)) > 0),
    status TEXT NOT NULL CHECK (status IN ('CONFIRMED','CANCELLED')),
    booking_type TEXT NOT NULL CHECK (booking_type IN ('USER','ACADEMIC'))
);

CREATE INDEX IF NOT EXISTS booking_room_time
ON bookings (room_id, status, start_time, end_time);
```

Notes:

- `COLLATE NOCASE` on `email` makes uniqueness case-insensitive; the service layer also `strip().lower()`s emails before every lookup/insert so Python and SQLite agree.
- `booking_room_time` exactly covers the overlap query's `WHERE room_id = ? AND status = 'CONFIRMED' AND start_time < ? AND end_time > ?` — an index-shaped query by design.
- Timestamps compare correctly as strings because the canonical form is fixed-width, zero-padded, second-precision ISO.

---

## 4. `auth.py` — authentication, sessions, CSRF

### 4.1 Password hashing

**`hash_password(password)`**

- Rejects non-str, empty, or `> 1024` bytes (`ValueError`).
- `salt = secrets.token_hex(16)` (32 hex chars, 128 bits).
- `hashed = pbkdf2_hmac("sha256", password, salt, 600_000)`.
- Stored as `"600000$<salt-hex>$<hash-hex>"`.

**`verify_password(password, saved)`**

- Split `saved` on `$` into `rounds, salt, hashed`; reject unless `100000 <= rounds <= 2000000`.
- Bound password length (≤ 1024 bytes) *before* deriving.
- Recompute and compare with `secrets.compare_digest` (constant time).
- Any malformed input (`ValueError`/`AttributeError`/`TypeError`) returns `False`, never raises.

### 4.2 Session store

In-memory `dict` guarded by a `threading.Lock`. A session is:

```python
{
  "id":         secrets.token_urlsafe(32),   # 256-bit opaque ID = cookie value
  "user_id":    int | None,                  # None => anonymous CSRF-only session
  "csrf_token": secrets.token_urlsafe(32),   # 256-bit per-session token
  "expires":    epoch_float,
}
```

| Function | Behaviour |
|---|---|
| `new_session(user_id=None)` | Sweeps expired entries; if `len(sessions) >= 4096`, evicts the entry with the smallest `expires`; inserts the new session; returns a **copy** |
| `get_session(request)` | Reads the cookie; returns `None` for unknown/expired (expired entries are popped); returns a **copy** |
| `remove_session(session_id)` | Locks and pops the ID |

Anonymous sessions exist so that public pages (login, booking form) can carry a CSRF token without an account.

### 4.3 Cookie handling — `set_session_cookie(response, request, session)`

`set_cookie(cookie_name, session["id"], httponly=True, samesite="lax", secure=..., max_age=remaining, path="/")`, where `secure = (request scheme is https) or SPACES_SECURE_COOKIE == "1"` and `max_age = max(0, expires - now)`.

### 4.4 Identity resolution

- `get_user(request)` — session → `user_id` → `SELECT id, name, email, role FROM users WHERE id = ?`; enriches the dict with `student_identity(email)` fields (§7). Returns `None` for anonymous/expired/deleted users.
- `current_user(request)` — `get_user` or raise **401** "Please sign in to continue."
- `admin_user(request)` — `current_user` + `role == "admin"`, else **403** "This action is only available to admins."
- `find_user(email)` — `SELECT * FROM users WHERE email = ?` with `email.strip().lower()`; DB errors become **500**.

### 4.5 CSRF — `check_csrf(request, token)`

1. Fetch the server-side session; **403** ("Your form expired…") if no session, token is not a `str`, or `compare_digest(session.csrf_token, token)` fails.
2. If an `Origin` header is present, it must equal `scheme://netloc` of the request URL, else **403** ("This request came from another site.").

Web forms submit the token as field `csrf_token`; API clients send header `X-CSRF-Token`. Both land in this single function.

---

## 5. `bookings.py` — business rules and transactions

### 5.1 Validation helpers (shared by every entry point)

| Helper | Rule | Failure |
|---|---|---|
| `_positive_id(v, f)` | `type(v) is int` and `0 < v <= 2^63-1` (bool excluded) | 400 |
| `_text(v, f, max, required=False)` | must be `str`; strip; reject blank-if-required, `len > max`, or containing `\x00` | 400 |
| `_datetime(v, f)` | regex `[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}(:[0-9]{2})?` then `datetime.fromisoformat` (catches `2026-02-31`) | 400 |
| `_current_user(conn, user, admin=False)` | re-`SELECT`s `id, role` **inside the transaction**; user must exist with role `user`/`admin`; `admin=True` demands `admin` | 403 |

> [!IMPORTANT]
> `_current_user` re-reads the role from the database on **every** privileged call. A demoted or deleted account loses authority immediately, even mid-session; the in-cookie session never carries authority of its own.

`_database_error(e)` maps `SQLITE_BUSY`/`SQLITE_LOCKED` (masked with 255) to **503** "Database is busy; please retry" and re-raises anything else. `_connect()` applies this at connection time.

### 5.2 `create_booking(user, room_id, start_time, end_time, subject='', batch='', purpose='', booking_type='USER')`

Algorithm (all DB work inside one transaction):

```text
validate room_id, datetimes, end > start
subject <= 200, batch <= 100, purpose <= 1000 (required), booking_type in (USER, ACADEMIC)
OPEN connection; BEGIN IMMEDIATE
  current := _current_user(conn, user, admin = booking_type == 'ACADEMIC')   # 403
  if start < campus_now(): 400 "Start time cannot be in the past"
  room := SELECT id FROM rooms WHERE id = ? AND is_active = 1                 # 404
  canonicalise times to second-precision ISO
  conflict := SELECT id FROM bookings
              WHERE room_id = ? AND status = 'CONFIRMED'
                AND start_time < :end AND end_time > :start LIMIT 1           # 409
  INSERT bookings(..., status='CONFIRMED', booking_type)
  result := SELECT * FROM bookings WHERE id = lastrowid
COMMIT; return result
on OperationalError: ROLLBACK; _database_error
on other Exception: ROLLBACK; raise
finally: close
```

The overlap predicate `start_time < new_end AND end_time > new_start` implements half-open interval intersection `[start, end)`; back-to-back bookings (`09:00–10:00` then `10:00–11:00`) are therefore allowed. Running the check under `BEGIN IMMEDIATE` serialises concurrent writers, so two racing requests cannot both pass the guard.

### 5.3 `cancel_booking(user, booking_id)`

`BEGIN IMMEDIATE` → re-auth → fetch booking (**404** if missing) → authorise:

- admin ⇒ may cancel anything;
- otherwise `booking.user_id == current.id` **and** `booking.booking_type == 'USER'`, else **403** "You may cancel only your own user bookings".

Then `UPDATE bookings SET status = 'CANCELLED'`, re-select, commit, return. Users can never cancel an `ACADEMIC` block — only admins.

### 5.4 `list_bookings(user, all_bookings=False)`

- `all_bookings` must be `bool` (400 otherwise); `admin=True` is demanded of the DB role when `all_bookings`.
- One joined query returning booking rows enriched with `room_name`, `room_type`, `user_name`, `user_email`:

```sql
SELECT bookings.*, rooms.name AS room_name, rooms.type AS room_type,
       users.name AS user_name, users.email AS user_email
FROM bookings JOIN rooms ON rooms.id = bookings.room_id
              JOIN users ON users.id = bookings.user_id
[WHERE bookings.user_id = ?]
ORDER BY bookings.start_time DESC, bookings.id DESC
```

### 5.5 `save_room(user, name, room_type, capacity, location, is_active=True, room_id=None)`

Validation: `name` ≤ 120 required; `location` ≤ 200 required; `room_type ∈ room_types`; `capacity` is `int` in `[1, 10000]`; `is_active` is `bool`; optional `room_id` positive. Under `BEGIN IMMEDIATE` with admin re-auth: `room_id is None` ⇒ `INSERT`, else verify existence (**404**) and `UPDATE`. Returns the resulting room row.

### 5.6 `all_rooms(user)`

Admin-only; `SELECT * FROM rooms ORDER BY name, id` — includes inactive rooms (unlike `list_rooms`, which is the public view).

---

## 6. `main.py` — routes, pages, API

### 6.1 Application wiring

- `lifespan` hook calls `setup_database()` at startup.
- `/static` is mounted from `app/static`; Jinja2 templates from `app/templates`.
- Pydantic API model:

```python
class Booking(BaseModel):
    model_config = ConfigDict(extra="forbid")   # unknown keys => 422
    room_id: StrictInt                          # "1" (str) rejected, bools rejected
    start_time: str
    end_time: str
    purpose: str                                # required
    subject: str = ""
    batch: str = ""
```

### 6.2 Page rendering — `show_page(request, name, context, status_code)`

1. `get_session`; if none, `new_session()` (anonymous, CSRF-capable).
2. Base context: `user` (via `get_user`), `csrf_token`, `error=None`, `message` mapped from `?message=` (`created` / `cancelled` / `room_saved` / `blocked`), `values={}`; caller context overrides.
3. `TemplateResponse` with security headers: `Cache-Control: no-store`, `Vary: Cookie`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`.
4. If the session was just created, set its cookie on the response.

`redirect(path)` = `303 See Other` + `Cache-Control: no-store` (post/redirect/get).

### 6.3 Route catalogue

| Route | Handler logic (after CSRF where applicable) |
|---|---|
| `GET /login` | Redirect to `/rooms` if already signed in; else render login |
| `POST /login` | Cap email ≤ 254 / password ≤ 1024 (**401** otherwise, generic message); `find_user`; **always** run `verify_password` against a dummy `600000$missing-account$000…0` hash when the user is unknown (timing-safe); on success drop the old session, mint a login session, set cookie, 303 → `/rooms` |
| `POST /logout` | CSRF; remove session; delete cookie; 303 → `/login` |
| `GET /` , `GET /rooms` | `check_filters`; `list_rooms`; render `rooms.html` |
| `GET /rooms/{id}/bookings` | Validate `room_id` range (404); `selected_day(day)`; `room_schedule`; 404 if room `None`; render `schedule.html` |
| `GET /bookings` | Redirect anonymous → `/login`; split `list_bookings(user)` into `upcoming` (`CONFIRMED` and `end_time > now`) vs `past` (everything else) using `campus_now()` |
| `POST /bookings` | `current_user`; CSRF; `form_booking` → on **400/409** re-render the schedule page with the submitted `values` and error; on success 303 → `/bookings?message=created` |
| `POST /bookings/{id}/cancel` | `current_user`; CSRF; `cancel_booking`; 303 → `/bookings?message=cancelled` (or `/admin?...` when `redirect_to=/admin` **and** role is admin) |
| `GET /admin` | Redirect anonymous → `/login`; `admin_user` (403 for non-admins); render with `all_rooms`, `list_bookings(all=True)`, `today` |
| `POST /admin/rooms` | `admin_user`; CSRF; `is_active` must be `""` or `"1"` (400); `save_room`; 303 → `/admin?message=room_saved` |
| `POST /admin/blocks` | `admin_user`; CSRF; `form_booking(..., "ACADEMIC")`; 303 → `/admin?message=blocked` |

Shared request validators:

- `number(v, label)` — strict int in `[0, 2^63-1]`, bools rejected, else 400.
- `selected_day(v)` — empty ⇒ today (campus tz); else `date.fromisoformat` with round-trip check (`chosen.isoformat() == value`, rejecting e.g. `2026-1-1`) and `date.max` rejected; 400 on failure.
- `check_filters(type, cap)` — type ∈ `room_types` (400); `0 <= cap <= 2^63-1` (400).
- `form_booking(user, form, kind)` — `day` required (400); composes `day + "T" + start/end` and calls `create_booking`.

### 6.4 API routes

| Route | Behaviour |
|---|---|
| `GET /api/rooms` | `check_filters` + `list_rooms`; JSON list |
| `GET /api/rooms/{id}/bookings` | Same guards as the page; `{"room", "day", "bookings"}` |
| `GET /api/bookings` | `current_user`; own bookings (all for admins); `no-store` |
| `POST /api/bookings` | `current_user`; CSRF via `X-CSRF-Token` header; `create_booking(**model_dump())`; **201** + `no-store` |
| `DELETE /api/bookings/{id}` | `current_user`; CSRF header; `cancel_booking`; **200** |

### 6.5 Error handling

```mermaid
flowchart TB
    E[Exception raised anywhere] --> H1{sqlite3.OperationalError?}
    H1 -- yes --> DB503[/api/: JSON 503<br>pages: error.html 503/]
    H1 -- no --> H2{HTTPException?}
    H2 -- status 503 --> DB503
    H2 -- /api/ path --> J[JSON {"detail": msg} + status]
    H2 -- page path --> P[error.html rendered with detail + status]
```

Status-code contract:

| Code | Raised when |
|---|---|
| 400 | Malformed ids, dates, datetimes, text limits, bad filters, missing day, past start, `end <= start` |
| 401 | No session / anonymous on protected routes; failed login (page) |
| 403 | CSRF failure, cross-origin, non-admin on admin routes, cancelling others' bookings, `ACADEMIC` type as user |
| 404 | Unknown/inactive room (page + API), unknown booking, bad room-id range |
| 409 | Overlap with an existing `CONFIRMED` booking |
| 422 | Pydantic body violations (FastAPI default) |
| 500 | Unexpected DB error in `find_user` |
| 503 | `SQLITE_BUSY`/`SQLITE_LOCKED`, DB unavailable |

---

## 7. Student identity — `student_identity.py` + `coursecatalog.py`

`student_identity(email)`:

1. Must contain exactly one `@`; domain (lowercased) ∈ `{lnmit.ac.in, lnmiit.ac.in}`; otherwise `None`.
2. Local part upper-cased and matched with `([0-9]{2})([A-Z]{3,5})([0-9]{3})` → `year, code, serial`; otherwise `None`.
3. Base identity dict: `roll_number`, `email_local_part`, `enrollment_year`, programme letter decode (`U`/`D`/`M`/`P` for 3-char codes), branch code/name from `branch_names`, `serial_number`, and `course`/`school`/`mapping_source` placeholders.
4. If `code` matches a catalog `programmes` prefix, overlay `degree`, `branch`, `school`, `prefix_source`.
5. Special case `IMAI` ⇒ Integrated Masters in AI (LICAI school).

The catalog (`coursecatalog.py`) is a curated list where each entry carries its web evidence `source`/`prefix_source` (admissions pages, convocation roll listings, or explicit user rules) — the decoder is data-driven and only claims mappings that are evidenced. Recognised prefixes: `UCC UCS UEC UME` (UG), `DCS DEC` (dual), `IMAI` (integrated), `MCS MEC MMT MPH` (PG), `PCC PCS PEC PMM PMT PPH PHS` (PhD).

The result is display-only (header badge in `base.html`); it never gates access.

---

## 8. `create_user.py` — CLI provisioning

`python -m app.create_user --name "…" --email "…" --role user|admin`

1. `getpass` twice; mismatch ⇒ error.
2. Validation: name 1–120 chars; email ≤ 254, contains `@`, no whitespace; role ∈ {user, admin}; password ≥ 12 chars.
3. `setup_database()` (safe on existing DBs) then `INSERT` with `hash_password`.
4. `sqlite3.IntegrityError` (duplicate email, case-insensitive) is reported as "That email already exists."

---

## 9. Presentation layer

| Template | Content |
|---|---|
| `base.html` | Skip link, header nav (`Rooms`, `My bookings`, `Admin` for admins, account + roll number, logout form), CSRF `<meta name="csrf-token">`, error/success notices with `role=alert`/`role=status`, `#main` landmark, footer with timezone note |
| `rooms.html` | Filter form (type select, capacity) + room cards linking to schedules |
| `schedule.html` | Day picker, confirmed-booking table (scrollable), booking form with subject/batch/purpose |
| `bookings.html` | Upcoming vs past split of the user's bookings, cancel buttons |
| `admin.html` | Inventory table (edit + activate/deactivate), room form, all-bookings view with cancel, academic block form |
| `login.html` | Email/password + CSRF |
| `error.html` | Status + detail message |

`static/style.css` is a single hand-rolled, responsive design system (tokens, focus outlines, notice styles) with no CSS framework dependency. Forms always embed `{{ csrf_token }}`; the API reads the token from the meta tag.

---

## 10. Verification checklist

Manual/automatable checks that pin the invariants in this document:

- [x] Overlap: two identical-interval bookings ⇒ second gets 409 (web shows inline error, API returns `detail`)
- [x] Back-to-back (`10:00–11:00`, `11:00–12:00`) ⇒ both succeed
- [x] Past `start_time` ⇒ 400
- [x] Cancel of another user's booking as non-admin ⇒ 403; of an `ACADEMIC` booking as non-admin ⇒ 403
- [x] Non-admin `POST /admin/blocks` and `POST /api/bookings` with `ACADEMIC`-style type ⇒ 403 path (`booking_type` validated in service)
- [x] Missing/garbled CSRF token on any mutating route ⇒ 403
- [x] Cross-origin `Origin` header ⇒ 403
- [x] Login with unknown email takes ≈ same time as known-email wrong password (dummy derivation)
- [x] Restart server ⇒ all sessions invalid
- [x] Duplicate email (any case) via CLI ⇒ rejected
- [x] `GET /rooms?room_type=XYZ` ⇒ 400; `min_capacity=-1` ⇒ 400
- [x] Room deactivated ⇒ disappears from `/rooms`, schedule 404s, new bookings 404

---

## 11. Related documents

- [High-Level Design](HLD.md)
- [Project Selection Report](Project%20Selection%20Report.md)
- [`../README.md`](../README.md)
