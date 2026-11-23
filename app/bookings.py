import re
import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from app.connection import get_connection, room_types

campus_timezone = timezone(timedelta(hours=5, minutes=30))



def _positive_id(value, field):
    if type(value) is not int or not 0 < value <= 9223372036854775807:
        raise HTTPException(status_code=400, detail=field + " must be a positive integer")
    return value


def _text(value, field, maximum, required=False):
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail=field + " must be text")
    value = value.strip()
    if (required and not value) or len(value) > maximum or "\x00" in value:
        raise HTTPException(status_code=400, detail=field + " is blank or too long, or contains a null character")
    return value


def _datetime(value, field):
    if not isinstance(value, str) or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}(:[0-9]{2})?", value
    ):
        raise HTTPException(status_code=400, detail=field + " must be a local ISO datetime without timezone or fractions")
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=400, detail=field + " is not a valid datetime") from None


def _database_error(error):

    code = getattr(error, "sqlite_errorcode", 0)
    if code & 255 in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):

        raise HTTPException(status_code=503, detail="Database is busy; please retry") from error
    
    raise error


def _connect():
    try:
        return get_connection()
    
    except sqlite3.OperationalError as error:
        _database_error(error)



def _current_user(connection, user, admin=False):
    if not isinstance(user, dict) or type(user.get("id")) is not int or not 0 < user["id"] <= 9223372036854775807:

        raise HTTPException(status_code=403, detail="A current account is required")

    
    current = connection.execute("SELECT id, role FROM users WHERE id = ?", (user["id"],)).fetchone()

    if current is None or current["role"] not in ("user", "admin"):
        raise HTTPException(status_code=403, detail="A current account is required")
    
    if admin and current["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return current

# AI Generated code for management and handling conflicts
def create_booking(user, room_id, start_time, end_time, subject='', batch='', purpose='', booking_type='USER'):

    room_id = _positive_id(room_id, "Room ID")
    start = _datetime(start_time, "Start time")
    end = _datetime(end_time, "End time")

    if end <= start:
        raise HTTPException(status_code=400, detail="End time must be after start time")
    subject = _text(subject, "Subject", 200)
    batch = _text(batch, "Batch", 100)
    purpose = _text(purpose, "Purpose", 1000, required=True)

    if not isinstance(booking_type, str) or booking_type not in ("USER", "ACADEMIC"):
        raise HTTPException(status_code=400, detail="Invalid booking type")

    connection = _connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        current = _current_user(connection, user, admin=booking_type == "ACADEMIC")

        if start < datetime.now(campus_timezone).replace(tzinfo=None):
            raise HTTPException(status_code=400, detail="Start time cannot be in the past")
        room = connection.execute("SELECT id FROM rooms WHERE id = ? AND is_active = 1", (room_id,)).fetchone()
        if room is None:
            raise HTTPException(status_code=404, detail="Room is missing or inactive")

        start_time = start.isoformat(timespec="seconds")
        end_time = end.isoformat(timespec="seconds")
        conflict = connection.execute(
            """SELECT id FROM bookings WHERE room_id = ? AND status = 'CONFIRMED'
            AND start_time < ? AND end_time > ? LIMIT 1""",
            (room_id, end_time, start_time),
        ).fetchone()
        if conflict:
            raise HTTPException(status_code=409, detail="Room is already booked for this interval")
        cursor = connection.execute(
            """INSERT INTO bookings
            (room_id, user_id, start_time, end_time, subject, batch, purpose, status, booking_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'CONFIRMED', ?)""",
            (room_id, current["id"], start_time, end_time, subject, batch, purpose, booking_type),
        )
        booking = dict(connection.execute("SELECT * FROM bookings WHERE id = ?", (cursor.lastrowid,)).fetchone())
        connection.commit()
        return booking
    except sqlite3.OperationalError as error:
        connection.rollback()
        _database_error(error)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def list_bookings(user, all_bookings=False):
    if type(all_bookings) is not bool:
        raise HTTPException(status_code=400, detail="all_bookings must be a boolean")
    connection = _connect()
    try:
        current = _current_user(connection, user, admin=all_bookings)
        query = """SELECT bookings.*, rooms.name AS room_name, rooms.type AS room_type,
        users.name AS user_name, users.email AS user_email
        FROM bookings JOIN rooms ON rooms.id = bookings.room_id
        JOIN users ON users.id = bookings.user_id"""
        values = []
        if not all_bookings:
            query += " WHERE bookings.user_id = ?"
            values.append(current["id"])
        query += " ORDER BY bookings.start_time DESC, bookings.id DESC"
        return [dict(row) for row in connection.execute(query, values).fetchall()]
    except sqlite3.OperationalError as error:
        _database_error(error)
    finally:
        connection.close()


def cancel_booking(user, booking_id):
    booking_id = _positive_id(booking_id, "Booking ID")
    connection = _connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        current = _current_user(connection, user)
        booking = connection.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
        if booking is None:
            raise HTTPException(status_code=404, detail="Booking not found")
        if current["role"] != "admin" and (booking["user_id"] != current["id"] or booking["booking_type"] != "USER"):
            raise HTTPException(status_code=403, detail="You may cancel only your own user bookings")
        connection.execute("UPDATE bookings SET status = 'CANCELLED' WHERE id = ?", (booking_id,))
        result = dict(connection.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone())
        connection.commit()
        return result
    except sqlite3.OperationalError as error:
        connection.rollback()
        _database_error(error)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def save_room(user, name, room_type, capacity, location, is_active=True, room_id=None):
    name = _text(name, "Room name", 120, required=True)
    location = _text(location, "Location", 200, required=True)
    if not isinstance(room_type, str) or room_type not in room_types:
        raise HTTPException(status_code=400, detail="Invalid room type")
    
    if type(capacity) is not int or not 1 <= capacity <= 10000:
        raise HTTPException(status_code=400, detail="Capacity must be an integer between 1 and 10000")
    
    if type(is_active) is not bool:
        raise HTTPException(status_code=400, detail="is_active must be a boolean")
    if room_id is not None:
        room_id = _positive_id(room_id, "Room ID")

    connection = _connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        _current_user(connection, user, admin=True)
        if room_id is None:
            cursor = connection.execute(
                "INSERT INTO rooms (name, type, capacity, location, is_active) VALUES (?, ?, ?, ?, ?)",
                (name, room_type, capacity, location, int(is_active)),
            )
            room_id = cursor.lastrowid
        else:
            exists = connection.execute("SELECT id FROM rooms WHERE id = ?", (room_id,)).fetchone()
            if exists is None:
                raise HTTPException(status_code=404, detail="Room not found")
            connection.execute(
                "UPDATE rooms SET name = ?, type = ?, capacity = ?, location = ?, is_active = ? WHERE id = ?",
                (name, room_type, capacity, location, int(is_active), room_id),
            )
        result = dict(connection.execute("SELECT * FROM rooms WHERE id = ?", (room_id,)).fetchone())
        connection.commit()
        return result
    except sqlite3.OperationalError as error:
        connection.rollback()
        _database_error(error)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def all_rooms(user):
    connection = _connect()
    try:
        _current_user(connection, user, admin=True)
        return [dict(row) for row in connection.execute("SELECT * FROM rooms ORDER BY name, id").fetchall()]
    except sqlite3.OperationalError as error:
        _database_error(error)
    finally:
        connection.close()
