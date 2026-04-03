"""Импорт истории игр из JSON/CSV в SQLite БД бота."""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from pathlib import Path


EXPECTED_FIELDS = (
    "id",
    "user_id",
    "game_number",
    "result",
    "added_at",
    "hour_of_day",
    "day_of_week",
)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS games (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    game_number TEXT    DEFAULT '',
    result      INTEGER NOT NULL,
    added_at    TEXT    NOT NULL,
    hour_of_day INTEGER,
    day_of_week INTEGER
);
CREATE INDEX IF NOT EXISTS idx_games_user ON games(user_id, id DESC);

CREATE TABLE IF NOT EXISTS predictions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    strategy   TEXT    NOT NULL,
    predicted  INTEGER NOT NULL,
    actual     INTEGER,
    correct    INTEGER DEFAULT 0,
    created_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS strategy_weights (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    strategy   TEXT    NOT NULL,
    correct    INTEGER DEFAULT 0,
    total      INTEGER DEFAULT 0,
    updated_at TEXT    NOT NULL,
    UNIQUE(user_id, strategy)
);

CREATE TABLE IF NOT EXISTS notification_log (
    user_id   INTEGER PRIMARY KEY,
    last_sent TEXT    NOT NULL
);
"""


def _load_rows(path: Path) -> list[dict]:
    if path.suffix.lower() == ".json":
        rows = json.loads(path.read_text(encoding="utf-8"))
    elif path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh))
    else:
        raise ValueError("Поддерживаются только файлы .json и .csv")

    if not rows:
        return []

    missing = [field for field in EXPECTED_FIELDS if field not in rows[0]]
    if missing:
        raise ValueError(f"В файле не хватает полей: {', '.join(missing)}")

    normalized = []
    for row in rows:
        normalized.append(
            {
                "id": int(row["id"]),
                "user_id": int(row["user_id"]),
                "game_number": str(row["game_number"] or ""),
                "result": int(row["result"]),
                "added_at": str(row["added_at"]),
                "hour_of_day": int(row["hour_of_day"]) if str(row["hour_of_day"]) != "" else None,
                "day_of_week": int(row["day_of_week"]) if str(row["day_of_week"]) != "" else None,
            }
        )
    return normalized


def _connect(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path, timeout=10)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA_SQL)
    con.commit()
    return con


def _existing_keys(con: sqlite3.Connection) -> set[tuple[int, str, int, str]]:
    cur = con.cursor()
    cur.execute("SELECT user_id, game_number, result, added_at FROM games")
    return {
        (int(row["user_id"]), str(row["game_number"] or ""), int(row["result"]), str(row["added_at"]))
        for row in cur.fetchall()
    }


def _truncate(con: sqlite3.Connection) -> None:
    cur = con.cursor()
    cur.execute("DELETE FROM games")
    cur.execute("DELETE FROM predictions")
    cur.execute("DELETE FROM strategy_weights")
    cur.execute("DELETE FROM notification_log")
    con.commit()


def import_rows(db_path: str, rows: list[dict], truncate: bool) -> tuple[int, int]:
    con = _connect(db_path)
    if truncate:
        _truncate(con)

    cur = con.cursor()
    existing = _existing_keys(con)
    inserted = 0
    skipped = 0

    for row in sorted(rows, key=lambda r: (r["added_at"], r["id"])):
        key = (row["user_id"], row["game_number"], row["result"], row["added_at"])
        if key in existing:
            skipped += 1
            continue
        cur.execute(
            """
            INSERT INTO games (id, user_id, game_number, result, added_at, hour_of_day, day_of_week)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"],
                row["user_id"],
                row["game_number"],
                row["result"],
                row["added_at"],
                row["hour_of_day"],
                row["day_of_week"],
            ),
        )
        existing.add(key)
        inserted += 1

    con.commit()
    con.close()
    return inserted, skipped


def main() -> None:
    parser = argparse.ArgumentParser(description="Импорт истории игр в SQLite БД")
    parser.add_argument("source", help="Путь к desyatka_export.json или desyatka_export.csv")
    parser.add_argument("--db-path", default="games.db", help="Путь к БД, по умолчанию games.db")
    parser.add_argument("--truncate", action="store_true", help="Очистить games/predictions/weights перед импортом")
    args = parser.parse_args()

    source = Path(args.source)
    if not source.exists():
        raise FileNotFoundError(f"Файл не найден: {source}")

    rows = _load_rows(source)
    inserted, skipped = import_rows(args.db_path, rows, args.truncate)

    print(f"Источник: {source}")
    print(f"Всего строк: {len(rows)}")
    print(f"Добавлено: {inserted}")
    print(f"Пропущено как дубли: {skipped}")
    print(f"БД: {Path(args.db_path).resolve()}")


if __name__ == "__main__":
    main()
