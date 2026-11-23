import hashlib
import os
import secrets
import threading
import time


from fastapi import HTTPException, requests, status

from app.connection import get_connection
from app.student_identity import student_identity


sessions = {}
session_lock = threading.Lock()
cookie_name = "sessionnamespaces"
session_seconds = 36000


def hash_password(password):
    if not isinstance(password, str) or not password or len(password.encode()) > 1024:
        raise ValueError("Password must contain 1 to 1024 bytes.")

    
    salt = secrets.token_hex(16)
    
    rounds = 600000
    hashed = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), rounds)
    return f"{rounds}${salt}${hashed.hex()}"

def verify_password(password, saved):
    try:
        rounds, salt, hashed = saved.split("$")
        rounds = int(rounds)
        if not isinstance(password, str) or len(password.encode()) > 1024 or not 100000 <= rounds <= 2000000:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), rounds)
        return secrets.compare_digest(actual.hex(), hashed)
    except (ValueError, AttributeError, TypeError):
        return False



def find_user(email):
    connection = get_connection()

    try:
        row = connection.execute("SELECT * FROM users WHERE email = ?", (email.strip().lower(),)).fetchone() # LEARNING!!! used ',', because the second argument must be a tuple otherwise python will treat it as a string only. 
        return dict(row) if row else None
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
    finally:
        connection.close()



def new_session(user_id=None):
    now = time.time()

    session = {
        "id": secrets.token_urlsafe(32), "user_id": user_id,
        "csrf_token": secrets.token_urlsafe(32),
        "expires": now + (session_seconds if user_id is not None else 3600),
    }
    with session_lock:
        expired = [key for key, value in sessions.items() if value["expires"] <= now]
        for key in expired:
            sessions.pop(key, None)
        if len(sessions) >= 4096:
            oldest = min(sessions, key=lambda key: sessions[key]["expires"])
            sessions.pop(oldest, None)
        sessions[session["id"]] = session
    return session.copy()


def get_session(request):
    token = request.cookies.get(cookie_name)
    if not token:
        return None
    with session_lock:
        session = sessions.get(token)
        if session is None:
            return None
        if session["expires"] <= time.time():
            sessions.pop(token, None)
            return None
        return session.copy()

def remove_session(session_id):
    with session_lock:
        sessions.pop(session_id, None)

def set_session_cookie(response, request, session):
    response.set_cookie(
        cookie_name, session["id"], httponly=True, samesite="lax",
        secure=request.url.scheme == "https" or os.getenv("SPACES_SECURE_COOKIE") == "1",
        max_age=max(0, int(session["expires"] - time.time())), path="/",
    )


def get_user(request):
    session = get_session(request)
    if session is None or session["user_id"] is None:
        return None
    connection = get_connection()
    try:
        row = connection.execute(
            "SELECT id, name, email, role FROM users WHERE id = ?", (session["user_id"],),
        ).fetchone()
        if row is None:
            return None
        user = dict(row)
        user.update(student_identity(user["email"]) or {})
        return user
    finally:
        connection.close()


def current_user(request):
    user = get_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Please sign in to continue.")
    return user


def admin_user(request):
    user = current_user(request)
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="This action is only available to admins.")
    return user


def check_csrf(request, token):
    session = get_session(request)
    if session is None or not isinstance(token, str) or not secrets.compare_digest(session["csrf_token"], token):
        raise HTTPException(status_code=403, detail="Your form expired. Reload the page and try again.")
    origin = request.headers.get("origin")
    expected = f"{request.url.scheme}://{request.url.netloc}"
    if origin and origin != expected:
        raise HTTPException(status_code=403, detail="This request came from another site.")
