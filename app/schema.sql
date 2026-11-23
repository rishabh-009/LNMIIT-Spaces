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
    type TEXT NOT NULL CHECK (type IN ('LT', 'LH', 'LAB', 'ROOM', 'MEETING')),
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
    status TEXT NOT NULL CHECK (status IN ('CONFIRMED', 'CANCELLED')),
    booking_type TEXT NOT NULL CHECK (booking_type IN ('USER', 'ACADEMIC'))
);

CREATE INDEX IF NOT EXISTS booking_room_time
ON bookings (room_id, status, start_time, end_time);
