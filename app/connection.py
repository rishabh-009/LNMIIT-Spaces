import os
import sqlite3
from datetime import timedelta
from pathlib import Path

folder = Path(__file__).resolve().parent
room_types = ("LT", "LH", "LAB", "ROOM", "MEETING")


def get_connection():
    path = Path(os.getenv("SPACES_DB", str(folder / "data" / "spaces.db")))
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection

def list_rooms(room_type="", min_capacity=0):
    query = "SELECT id, name, type, capacity, location FROM rooms WHERE is_active = 1"
    values = []
    if room_type:
        query += " AND type = ?"
        values.append(room_type)
    if min_capacity:
        query += " AND capacity >= ?"
        values.append(min_capacity)
    query += " ORDER BY name, id"

    connection = get_connection()
    try:
        return [dict(row) for row in connection.execute(query, values).fetchall()]
    finally:
        connection.close()




def room_schedule(room_id, day):
    start = day.isoformat() + "T00:00:00"
    end = (day + timedelta(days=1)).isoformat() + "T00:00:00"
    connection = get_connection()
    try:
        room = connection.execute(
            "SELECT id, name, type, capacity, location FROM rooms WHERE id = ? AND is_active = 1",
            (room_id,),
        ).fetchone()
        bookings = connection.execute(
            """SELECT start_time, end_time, booking_type FROM bookings
            WHERE room_id = ? AND status = 'CONFIRMED'
            AND start_time < ? AND end_time > ? ORDER BY start_time, id""",
            (room_id, end, start),
        ).fetchall()
        return dict(room) if room else None, [dict(row) for row in bookings]
    finally:
        connection.close()



def setup_database():
    connection = get_connection()
    try:
        existing = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'rooms'"
        ).fetchone()
        connection.executescript((folder / "schema.sql").read_text(encoding="utf-8"))

        # Demo rooms hain, actual campus inventory nahi.
        if existing is None:
            connection.executemany(
                "INSERT INTO rooms (name, type, capacity, location) VALUES (?, ?, ?, ?)",
                [
                    ("Lecture Theatre 1", "LT", 180, "Academic block"),
                    ("Lecture Hall 2", "LH", 80, "Academic block"),
                    ("Computer Lab", "LAB", 40, "Lab block"),
                    ("Study Room", "ROOM", 12, "Library block"),
                    ("Meeting Room", "MEETING", 20, "Academic block"),
                ],
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()