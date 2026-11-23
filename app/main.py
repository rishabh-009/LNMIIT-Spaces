import sqlite3
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, StrictInt
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.auth import (
    admin_user, check_csrf, cookie_name, current_user, find_user, get_session,
    get_user, new_session, remove_session, set_session_cookie, verify_password,
)
from app.bookings import all_rooms, cancel_booking, create_booking, list_bookings, save_room
from app.connection import list_rooms, room_schedule, room_types, setup_database

folder = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app):
    setup_database()
    yield


app = FastAPI(title="LNMIIT Spaces", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(folder / "static")), name="static")
templates = Jinja2Templates(directory=str(folder / "templates"))


class Booking(BaseModel):
    model_config = ConfigDict(extra="forbid")
    room_id: StrictInt
    start_time: str
    end_time: str
    subject: str = ""
    batch: str = ""
    purpose: str


def campus_now():
    return datetime.now(timezone(timedelta(hours=5, minutes=30))).replace(tzinfo=None, microsecond=0)


def show_page(request, name, context=None, status_code=200):
    session = get_session(request)
    created = session is None
    if created:
        session = new_session()
    messages = {
        "created": "Your booking is confirmed.", "cancelled": "Booking cancelled.",
        "room_saved": "Room saved.", "blocked": "Academic slot blocked.",
    }
    values = {
        "user": get_user(request), "csrf_token": session["csrf_token"],
        "error": None, "message": messages.get(request.query_params.get("message")), "values": {},
    }
    values.update(context or {})
    response = templates.TemplateResponse(request=request, name=name, context=values, status_code=status_code)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Vary"] = "Cookie"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    if created:
        set_session_cookie(response, request, session)
    return response


def redirect(path):
    return RedirectResponse(path, status_code=303, headers={"Cache-Control": "no-store"})


def number(value, label):
    try:
        result = int(value)
        if isinstance(value, bool) or not 0 <= result <= 9223372036854775807:
            raise ValueError
        return result
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"Enter a valid {label}.")


def form_booking(user, form, kind="USER"):
    day = form.get("day", "")
    if not day:
        raise HTTPException(status_code=400, detail="Choose a booking date.")
    selected_day(day)
    return create_booking(
        user, number(form.get("room_id"), "room"),
        day + "T" + form.get("start_time", ""), day + "T" + form.get("end_time", ""),
        subject=form.get("subject", ""), batch=form.get("batch", ""),
        purpose=form.get("purpose", ""), booking_type=kind,
    )


def check_filters(room_type, min_capacity):
    if room_type and room_type not in room_types:
        raise HTTPException(status_code=400, detail="Choose a valid room type.")
    if min_capacity < 0:
        raise HTTPException(status_code=400, detail="Minimum capacity cannot be negative.")
    if min_capacity > 9223372036854775807:
        raise HTTPException(status_code=400, detail="Minimum capacity is too large.")


def selected_day(value):
    if not value:
        return datetime.now(timezone(timedelta(hours=5, minutes=30))).date()
    try:
        chosen = date.fromisoformat(value)
        if chosen.isoformat() != value or chosen == date.max:
            raise ValueError
        return chosen
    except ValueError:
        raise HTTPException(status_code=400, detail="Choose a valid date (YYYY-MM-DD).")


@app.exception_handler(sqlite3.OperationalError)
async def database_error(request, error):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Database unavailable. Please try again shortly."}, status_code=503)
    return templates.TemplateResponse(request=request, name="error.html", context={
        "error": "Database unavailable. Please try again shortly.",
        "user": None, "csrf_token": "", "message": None,
    }, status_code=503, headers={"Cache-Control": "no-store"})


@app.exception_handler(StarletteHTTPException)
async def http_error(request, error):
    if error.status_code == 503:
        return await database_error(request, error)
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": error.detail}, status_code=error.status_code, headers=error.headers)
    return show_page(request, "error.html", {"error": error.detail}, error.status_code)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if get_user(request):
        return redirect("/rooms")
    return show_page(request, "login.html")


@app.post("/login")
async def login(request: Request):
    form = dict(await request.form(max_files=0, max_fields=20))
    check_csrf(request, form.get("csrf_token"))
    email = form.get("email", "")
    password = form.get("password", "")
    if len(email) > 254 or len(password) > 1024:
        return show_page(request, "login.html", {"error": "Incorrect email or password."}, 401)
    user = find_user(email)
    # A missing account still performs a password derivation.
    saved = user["password_hash"] if user else "600000$missing-account$" + "0" * 64
    valid = verify_password(password, saved)
    if not valid or user is None:
        return show_page(request, "login.html", {
            "error": "Incorrect email or password.", "values": {"email": email},
        }, 401)
    remove_session(request)
    session = new_session(user["id"])
    response = redirect("/rooms")
    set_session_cookie(response, request, session)
    return response


@app.post("/logout")
async def logout(request: Request):
    form = dict(await request.form(max_files=0, max_fields=10))
    check_csrf(request, form.get("csrf_token"))
    remove_session(request)
    response = redirect("/login")
    response.delete_cookie(cookie_name, path="/", httponly=True, samesite="lax")
    return response


@app.get("/", response_class=HTMLResponse)
@app.get("/rooms", response_class=HTMLResponse)
def rooms_page(request: Request, room_type: str = "", min_capacity: int = 0):
    check_filters(room_type, min_capacity)
    rooms = list_rooms(room_type, min_capacity)
    return show_page(request, "rooms.html", {
        "rooms": rooms, "room_types": room_types,
        "room_type": room_type, "min_capacity": min_capacity,
    })


@app.get("/rooms/{room_id}/bookings", response_class=HTMLResponse)
def schedule_page(request: Request, room_id: int, day: str = ""):
    if room_id < 1 or room_id > 9223372036854775807:
        raise HTTPException(status_code=404, detail="Room not found or inactive.")
    chosen = selected_day(day)
    room, bookings = room_schedule(room_id, chosen)
    if room is None:
        raise HTTPException(status_code=404, detail="Room not found or inactive.")
    return show_page(request, "schedule.html", {
        "room": room, "bookings": bookings, "day": chosen.isoformat(),
    })


@app.get("/api/rooms")
def get_rooms(room_type: str = "", min_capacity: int = 0):
    check_filters(room_type, min_capacity)
    return list_rooms(room_type, min_capacity)


@app.get("/api/rooms/{room_id}/bookings")
def get_schedule(room_id: int, day: str = ""):
    if room_id < 1 or room_id > 9223372036854775807:
        raise HTTPException(status_code=404, detail="Room not found or inactive.")
    chosen = selected_day(day)
    room, bookings = room_schedule(room_id, chosen)
    if room is None:
        raise HTTPException(status_code=404, detail="Room not found or inactive.")
    return {"room": room, "day": chosen.isoformat(), "bookings": bookings}


@app.get("/bookings", response_class=HTMLResponse)
def bookings_page(request: Request):
    user = get_user(request)
    if user is None:
        return redirect("/login")
    bookings = list_bookings(user)
    now = campus_now().isoformat(timespec="seconds")
    upcoming = [b for b in bookings if b["status"] == "CONFIRMED" and b["end_time"] > now]
    past = [b for b in bookings if b["status"] != "CONFIRMED" or b["end_time"] <= now]
    return show_page(request, "bookings.html", {"upcoming": upcoming, "past": past})


@app.post("/bookings")
async def book_room(request: Request):
    user = current_user(request)
    form = dict(await request.form(max_files=0, max_fields=20))
    check_csrf(request, form.get("csrf_token"))
    try:
        form_booking(user, form)
    except HTTPException as error:
        if error.status_code in (400, 409):
            room_id = number(form.get("room_id"), "room")
            chosen = selected_day(form.get("day", ""))
            room, bookings = room_schedule(room_id, chosen)
            if room is not None:
                return show_page(request, "schedule.html", {
                    "room": room, "bookings": bookings, "day": chosen.isoformat(),
                    "values": form, "error": error.detail,
                }, error.status_code)
        raise
    return redirect("/bookings?message=created")


@app.post("/bookings/{booking_id}/cancel")
async def cancel(request: Request, booking_id: int):
    user = current_user(request)
    form = dict(await request.form(max_files=0, max_fields=10))
    check_csrf(request, form.get("csrf_token"))
    cancel_booking(user, booking_id)
    target = "/admin" if form.get("redirect_to") == "/admin" and user["role"] == "admin" else "/bookings"
    return redirect(target + "?message=cancelled")


@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request):
    if get_user(request) is None:
        return redirect("/login")
    user = admin_user(request)
    return show_page(request, "admin.html", {
        "rooms": all_rooms(user), "room_types": room_types,
        "bookings": list_bookings(user, all_bookings=True), "today": campus_now().date().isoformat(),
    })


@app.post("/admin/rooms")
async def edit_room(request: Request):
    user = admin_user(request)
    form = dict(await request.form(max_files=0, max_fields=20))
    check_csrf(request, form.get("csrf_token"))
    if form.get("is_active", "") not in ("", "1"):
        raise HTTPException(status_code=400, detail="Choose a valid room status.")
    save_room(
        user, name=form.get("name", ""), room_type=form.get("room_type", ""),
        capacity=number(form.get("capacity"), "capacity"), location=form.get("location", ""),
        is_active=form.get("is_active") == "1", room_id=number(form.get("room_id", "0"), "room") or None,
    )
    return redirect("/admin?message=room_saved")


@app.post("/admin/blocks")
async def academic_block(request: Request):
    user = admin_user(request)
    form = dict(await request.form(max_files=0, max_fields=20))
    check_csrf(request, form.get("csrf_token"))
    form_booking(user, form, "ACADEMIC")
    return redirect("/admin?message=blocked")


@app.get("/api/bookings")
def get_bookings(request: Request):
    response = JSONResponse(list_bookings(current_user(request)))
    response.headers["Cache-Control"] = "no-store"
    return response


@app.post("/api/bookings", status_code=201)
def api_book_room(request: Request, booking: Booking):
    user = current_user(request)
    check_csrf(request, request.headers.get("x-csrf-token"))
    result = create_booking(user, **booking.model_dump())
    return JSONResponse(result, status_code=201, headers={"Cache-Control": "no-store"})


@app.delete("/api/bookings/{booking_id}")
def api_cancel(request: Request, booking_id: int):
    user = current_user(request)
    check_csrf(request, request.headers.get("x-csrf-token"))
    result = cancel_booking(user, booking_id)
    return JSONResponse(result, headers={"Cache-Control": "no-store"})
