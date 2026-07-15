"""Durable wallet authentication state for challenges, users, and sessions."""

from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import sqlite3
from typing import Iterator, Optional


@dataclass(frozen=True)
class StoredChallenge:
    challenge_id: str
    address: str
    message: str
    chain_id: int
    expires_at: int
    used_at: Optional[int]


@dataclass(frozen=True)
class StoredUser:
    address: str
    role: str
    active: bool
    created_at: int
    last_login_at: int


@dataclass(frozen=True)
class StoredSession:
    session_id: str
    address: str
    role: str
    active: bool
    expires_at: int
    revoked_at: Optional[int]


class AuthStore:
    """Small synchronous store compatible with SQLite and PostgreSQL."""

    def __init__(self, database_url: str):
        self.database_url = database_url
        if database_url.startswith("sqlite:///"):
            self.kind = "sqlite"
            self.sqlite_path = database_url[len("sqlite:///"):]
        elif database_url.startswith(("postgres://", "postgresql://")):
            self.kind = "postgres"
            self.sqlite_path = ""
        else:
            raise RuntimeError("DATABASE_URL must use sqlite:/// or PostgreSQL")

    @contextmanager
    def _connection(self):
        if self.kind == "sqlite":
            path = Path(self.sqlite_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(path, timeout=10)
            try:
                connection.execute("PRAGMA foreign_keys = ON")
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()
            try:
                os.chmod(path, 0o600)
            except FileNotFoundError:
                pass
            return

        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError("psycopg is required for PostgreSQL wallet auth") from exc
        with psycopg.connect(self.database_url) as connection:
            yield connection

    def _sql(self, statement: str) -> str:
        return statement if self.kind == "sqlite" else statement.replace("?", "%s")

    def _execute(self, connection, statement: str, parameters=()):
        return connection.execute(self._sql(statement), parameters)

    def initialize(self) -> None:
        statements = (
            """
            CREATE TABLE IF NOT EXISTS auth_challenges (
                challenge_id VARCHAR(96) PRIMARY KEY,
                address VARCHAR(42) NOT NULL,
                message TEXT NOT NULL,
                chain_id BIGINT NOT NULL,
                expires_at BIGINT NOT NULL,
                used_at BIGINT,
                created_at BIGINT NOT NULL
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS auth_challenges_address_idx
            ON auth_challenges (address, created_at)
            """,
            """
            CREATE TABLE IF NOT EXISTS wallet_users (
                address VARCHAR(42) PRIMARY KEY,
                role VARCHAR(16) NOT NULL,
                active INTEGER NOT NULL,
                created_at BIGINT NOT NULL,
                last_login_at BIGINT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS auth_sessions (
                session_id VARCHAR(96) PRIMARY KEY,
                address VARCHAR(42) NOT NULL,
                expires_at BIGINT NOT NULL,
                revoked_at BIGINT,
                created_at BIGINT NOT NULL,
                FOREIGN KEY (address) REFERENCES wallet_users(address)
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS auth_sessions_address_idx
            ON auth_sessions (address, created_at)
            """,
        )
        with self._connection() as connection:
            for statement in statements:
                self._execute(connection, statement)

    def create_challenge(
        self,
        challenge_id: str,
        address: str,
        message: str,
        chain_id: int,
        expires_at: int,
        created_at: int,
    ) -> None:
        with self._connection() as connection:
            self._execute(
                connection,
                """
                UPDATE auth_challenges
                SET used_at = ?
                WHERE address = ? AND used_at IS NULL
                """,
                (created_at, address),
            )
            self._execute(
                connection,
                """
                INSERT INTO auth_challenges (
                    challenge_id, address, message, chain_id,
                    expires_at, used_at, created_at
                ) VALUES (?, ?, ?, ?, ?, NULL, ?)
                """,
                (challenge_id, address, message, chain_id, expires_at, created_at),
            )

    def get_challenge(self, challenge_id: str) -> Optional[StoredChallenge]:
        with self._connection() as connection:
            row = self._execute(
                connection,
                """
                SELECT challenge_id, address, message, chain_id, expires_at, used_at
                FROM auth_challenges
                WHERE challenge_id = ?
                """,
                (challenge_id,),
            ).fetchone()
        if not row:
            return None
        return StoredChallenge(
            challenge_id=row[0],
            address=row[1],
            message=row[2],
            chain_id=int(row[3]),
            expires_at=int(row[4]),
            used_at=int(row[5]) if row[5] is not None else None,
        )

    def consume_challenge(self, challenge_id: str, address: str, now: int) -> bool:
        with self._connection() as connection:
            cursor = self._execute(
                connection,
                """
                UPDATE auth_challenges
                SET used_at = ?
                WHERE challenge_id = ?
                  AND address = ?
                  AND used_at IS NULL
                  AND expires_at >= ?
                """,
                (now, challenge_id, address, now),
            )
            return cursor.rowcount == 1

    def upsert_user(self, address: str, default_role: str, now: int) -> StoredUser:
        with self._connection() as connection:
            self._execute(
                connection,
                """
                INSERT INTO wallet_users (
                    address, role, active, created_at, last_login_at
                ) VALUES (?, ?, 1, ?, ?)
                ON CONFLICT(address) DO UPDATE
                SET last_login_at = excluded.last_login_at
                """,
                (address, default_role, now, now),
            )
            row = self._execute(
                connection,
                """
                SELECT address, role, active, created_at, last_login_at
                FROM wallet_users
                WHERE address = ?
                """,
                (address,),
            ).fetchone()
        return StoredUser(
            address=row[0],
            role=row[1],
            active=bool(row[2]),
            created_at=int(row[3]),
            last_login_at=int(row[4]),
        )

    def get_user(self, address: str) -> Optional[StoredUser]:
        with self._connection() as connection:
            row = self._execute(
                connection,
                """
                SELECT address, role, active, created_at, last_login_at
                FROM wallet_users
                WHERE address = ?
                """,
                (address,),
            ).fetchone()
        if not row:
            return None
        return StoredUser(
            address=row[0],
            role=row[1],
            active=bool(row[2]),
            created_at=int(row[3]),
            last_login_at=int(row[4]),
        )

    def set_user_role(self, address: str, role: str) -> bool:
        with self._connection() as connection:
            cursor = self._execute(
                connection,
                "UPDATE wallet_users SET role = ? WHERE address = ?",
                (role, address),
            )
            return cursor.rowcount == 1

    def create_session(
        self,
        session_id: str,
        address: str,
        expires_at: int,
        created_at: int,
    ) -> None:
        with self._connection() as connection:
            self._execute(
                connection,
                """
                DELETE FROM auth_sessions
                WHERE expires_at < ? OR (revoked_at IS NOT NULL AND revoked_at < ?)
                """,
                (created_at, created_at - 86_400),
            )
            self._execute(
                connection,
                """
                INSERT INTO auth_sessions (
                    session_id, address, expires_at, revoked_at, created_at
                ) VALUES (?, ?, ?, NULL, ?)
                """,
                (session_id, address, expires_at, created_at),
            )

    def get_session(self, session_id: str) -> Optional[StoredSession]:
        with self._connection() as connection:
            row = self._execute(
                connection,
                """
                SELECT
                    sessions.session_id,
                    sessions.address,
                    users.role,
                    users.active,
                    sessions.expires_at,
                    sessions.revoked_at
                FROM auth_sessions AS sessions
                JOIN wallet_users AS users ON users.address = sessions.address
                WHERE sessions.session_id = ?
                """,
                (session_id,),
            ).fetchone()
        if not row:
            return None
        return StoredSession(
            session_id=row[0],
            address=row[1],
            role=row[2],
            active=bool(row[3]),
            expires_at=int(row[4]),
            revoked_at=int(row[5]) if row[5] is not None else None,
        )

    def revoke_session(self, session_id: str, address: str, now: int) -> bool:
        with self._connection() as connection:
            cursor = self._execute(
                connection,
                """
                UPDATE auth_sessions
                SET revoked_at = ?
                WHERE session_id = ? AND address = ? AND revoked_at IS NULL
                """,
                (now, session_id, address),
            )
            return cursor.rowcount == 1
