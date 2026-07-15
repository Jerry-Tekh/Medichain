#!/usr/bin/env python3
"""Production Uvicorn launcher with bounded concurrency."""

import os


def integer_environment(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise SystemExit(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise SystemExit(f"{name} must be between {minimum} and {maximum}")
    return value


def main() -> None:
    port = integer_environment("PORT", 8000, 1, 65535)
    concurrency = integer_environment("UVICORN_LIMIT_CONCURRENCY", 32, 1, 256)
    os.execvp(
        "uvicorn",
        [
            "uvicorn",
            "main:app",
            "--host",
            "0.0.0.0",
            "--port",
            str(port),
            "--workers",
            "1",
            "--limit-concurrency",
            str(concurrency),
            "--timeout-keep-alive",
            "5",
            "--no-server-header",
        ],
    )


if __name__ == "__main__":
    main()
