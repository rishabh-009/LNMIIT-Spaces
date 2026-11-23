import argparse
import getpass
import sqlite3

from app.auth import hash_password
from app.connection import get_connection, setup_database


def create_user(name, email, password, role="user"):
    name = name.strip()
    email = email.strip().lower()
    if not name or len(name) > 120 or len(email) > 254 or "@" not in email or any(c.isspace() for c in email):
        raise ValueError("Enter a name and valid email address.")
    if role not in ("user", "admin"):
        raise ValueError("Role must be user or admin.")
    if len(password) < 12:
        raise ValueError("Use at least 12 characters for the password.")
    saved = hash_password(password)
    connection = get_connection()
    try:
        cursor = connection.execute(
            "INSERT INTO users (name, email, password_hash, role) VALUES (?, ?, ?, ?)",
            (name, email, saved, role),
        )
        connection.commit()
        return cursor.lastrowid
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description="Create a local LNMIIT Spaces account.")
    parser.add_argument("--name", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--role", choices=("user", "admin"), default="user")
    args = parser.parse_args()
    password = getpass.getpass("Password (at least 12 characters): ")
    if password != getpass.getpass("Confirm password: "):
        parser.error("Passwords did not match.")
    setup_database()
    try:
        user_id = create_user(args.name, args.email, password, args.role)
    except (ValueError, sqlite3.IntegrityError) as error:
        parser.error("That email already exists." if isinstance(error, sqlite3.IntegrityError) else str(error))
    print(f"Account created (ID {user_id}, role {args.role}).")


if __name__ == "__main__":
    main()
