"""
database/db.py — SQLite слой v6.

Новое в v6:
  - ADMIN_ID: только владелец бота имеет полный доступ
  - access_tokens: временные токены для клиентов (генерирует только админ)
  - allowed_users: разрешённые пользователи с датой окончания доступа
  - access_log: аудит всех входов
  - Полная изоляция данных: history владельца ≠ история клиентов
"""

import os
import sqlite3
import csv
import json
import shutil
import secrets
import string
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

DB_PATH  = os.getenv("DB_PATH", "games.db")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))   # ваш Telegram user_id

STRATEGIES = ["statistical", "ema", "serial", "markov", "zigzag", "temporal", "momentum"]


def _conn() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA cache_size=-8000")
    return con


def init_db():
    con = _conn()
    cur = con.cursor()
    cur.executescript("""
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

        CREATE TABLE IF NOT EXISTS strategy_weights (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            strategy   TEXT    NOT NULL,
            correct    INTEGER DEFAULT 0,
            total      INTEGER DEFAULT 0,
            updated_at TEXT    NOT NULL,
            UNIQUE(user_id, strategy)
        );

        CREATE TABLE IF NOT EXISTS predictions (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            strategy   TEXT    NOT NULL,
            predicted  INTEGER NOT NULL,
            actual     INTEGER,
            correct    INTEGER DEFAULT 0,
            created_at TEXT    NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_preds_user ON predictions(user_id, actual, strategy);

        CREATE TABLE IF NOT EXISTS user_settings (
            user_id              INTEGER PRIMARY KEY,
            pause_threshold_min  INTEGER DEFAULT 60,
            use_statistical      INTEGER DEFAULT 1,
            use_ema              INTEGER DEFAULT 1,
            use_serial           INTEGER DEFAULT 1,
            use_markov           INTEGER DEFAULT 1,
            use_zigzag           INTEGER DEFAULT 1,
            use_temporal         INTEGER DEFAULT 1,
            use_recency          INTEGER DEFAULT 1,
            top_n                INTEGER DEFAULT 3,
            updated_at           TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS notification_log (
            user_id   INTEGER PRIMARY KEY,
            last_sent TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS access_tokens (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            token       TEXT    NOT NULL UNIQUE,
            created_at  TEXT    NOT NULL,
            expires_at  TEXT    NOT NULL,
            created_by  INTEGER NOT NULL,
            note        TEXT    DEFAULT '',
            used_by     INTEGER,
            used_at     TEXT
        );

        CREATE TABLE IF NOT EXISTS allowed_users (
            user_id     INTEGER PRIMARY KEY,
            token_used  TEXT    NOT NULL,
            granted_at  TEXT    NOT NULL,
            expires_at  TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS access_log (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            action  TEXT    NOT NULL,
            detail  TEXT    DEFAULT '',
            at      TEXT    NOT NULL
        );
    """)
    con.commit()
    con.close()


# ─────────────────────────── Контроль доступа ─────────────────────────────────

def is_admin(user_id: int) -> bool:
    return ADMIN_ID != 0 and user_id == ADMIN_ID


def is_allowed(user_id: int) -> bool:
    """True если: администратор ИЛИ у пользователя есть действующий токен."""
    if is_admin(user_id):
        return True
    if ADMIN_ID == 0:
        # Режим разработки — ADMIN_ID не задан, все разрешены
        return True
    con = _conn()
    cur = con.cursor()
    cur.execute("SELECT expires_at FROM allowed_users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    con.close()
    if not row:
        return False
    try:
        return datetime.now() < datetime.fromisoformat(row["expires_at"])
    except Exception:
        return False


def activate_token(user_id: int, token_str: str) -> dict:
    """
    Активирует токен для пользователя.
    Возвращает {"ok": bool, "reason": str, "expires_at": str|None}
    """
    token_str = token_str.strip().upper()
    con = _conn()
    cur = con.cursor()
    now = datetime.now()

    cur.execute("SELECT * FROM access_tokens WHERE token=?", (token_str,))
    row = cur.fetchone()
    con.close()

    if not row:
        _log_access(user_id, "bad_token", token_str[:20])
        return {"ok": False, "reason": "Токен не найден. Проверьте правильность."}

    row = dict(row)
    if datetime.fromisoformat(row["expires_at"]) < now:
        _log_access(user_id, "expired_token", token_str[:20])
        return {"ok": False, "reason": "Токен истёк. Запросите новый у администратора."}

    if row["used_by"] and row["used_by"] != user_id:
        _log_access(user_id, "used_token", token_str[:20])
        return {"ok": False, "reason": "Токен уже использован другим пользователем."}

    con = _conn()
    cur = con.cursor()
    cur.execute(
        "UPDATE access_tokens SET used_by=?, used_at=? WHERE token=?",
        (user_id, now.isoformat(timespec="seconds"), token_str)
    )
    cur.execute(
        """INSERT INTO allowed_users (user_id, token_used, granted_at, expires_at)
           VALUES (?,?,?,?)
           ON CONFLICT(user_id) DO UPDATE SET
               token_used=excluded.token_used,
               granted_at=excluded.granted_at,
               expires_at=excluded.expires_at""",
        (user_id, token_str,
         now.isoformat(timespec="seconds"),
         row["expires_at"])
    )
    con.commit()
    con.close()
    _log_access(user_id, "token_ok", f"expires={row['expires_at'][:10]}")
    return {"ok": True, "reason": "Доступ открыт!", "expires_at": row["expires_at"]}


def _log_access(user_id: int, action: str, detail: str = ""):
    try:
        con = _conn()
        cur = con.cursor()
        cur.execute(
            "INSERT INTO access_log (user_id, action, detail, at) VALUES (?,?,?,?)",
            (user_id, action, detail, datetime.now().isoformat(timespec="seconds"))
        )
        con.commit()
        con.close()
    except Exception:
        pass


# ─────────────────────────── Управление токенами ──────────────────────────────

def _gen_token(length: int = 8) -> str:
    # Исключаем похожие символы: O/0, I/1, S/5
    alphabet = "ABCDEFGHJKLMNPQRTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def create_token(created_by: int, days: int = 1, note: str = "") -> dict:
    """Создаёт токен доступа на указанное количество дней."""
    now = datetime.now()
    expires = now + timedelta(days=days)
    con = _conn()
    cur = con.cursor()
    for _ in range(10):
        token = _gen_token(8)
        try:
            cur.execute(
                """INSERT INTO access_tokens
                   (token, created_at, expires_at, created_by, note)
                   VALUES (?,?,?,?,?)""",
                (token,
                 now.isoformat(timespec="seconds"),
                 expires.isoformat(timespec="seconds"),
                 created_by, note)
            )
            con.commit()
            con.close()
            return {
                "token":      token,
                "expires_at": expires.isoformat(timespec="seconds"),
                "days":       days,
                "note":       note,
            }
        except sqlite3.IntegrityError:
            continue
    con.close()
    raise RuntimeError("Не удалось сгенерировать уникальный токен")


def get_active_tokens(admin_id: int) -> list[dict]:
    con = _conn()
    cur = con.cursor()
    cur.execute(
        """SELECT * FROM access_tokens
           WHERE created_by=? AND expires_at > ?
           ORDER BY id DESC""",
        (admin_id, datetime.now().isoformat())
    )
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return rows


def get_all_tokens(admin_id: int, limit: int = 20) -> list[dict]:
    con = _conn()
    cur = con.cursor()
    cur.execute(
        "SELECT * FROM access_tokens WHERE created_by=? ORDER BY id DESC LIMIT ?",
        (admin_id, limit)
    )
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return rows


def revoke_token(token_str: str) -> bool:
    now = datetime.now().isoformat(timespec="seconds")
    con = _conn()
    cur = con.cursor()
    cur.execute(
        "UPDATE access_tokens SET expires_at=? WHERE token=?",
        (now, token_str)
    )
    affected = cur.rowcount
    # Немедленно блокируем пользователя, активировавшего этот токен
    cur.execute(
        "DELETE FROM allowed_users WHERE token_used=?",
        (token_str.strip().upper(),)
    )
    con.commit()
    con.close()
    return affected > 0


def get_allowed_users_list() -> list[dict]:
    con = _conn()
    cur = con.cursor()
    cur.execute(
        "SELECT * FROM allowed_users WHERE expires_at > ? ORDER BY granted_at DESC",
        (datetime.now().isoformat(),)
    )
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return rows


# ─────────────────────────────── Games ────────────────────────────────────────

def add_game(user_id: int, result: int, game_number: str = "") -> int:
    now = datetime.now()
    con = _conn()
    cur = con.cursor()
    cur.execute(
        """INSERT INTO games (user_id, game_number, result, added_at, hour_of_day, day_of_week)
           VALUES (?,?,?,?,?,?)""",
        (user_id, game_number, result,
         now.isoformat(timespec="seconds"), now.hour, now.weekday()),
    )
    con.commit()
    row_id = cur.lastrowid
    con.close()
    return row_id


def add_games_bulk(user_id: int, results: list[int]) -> list[int]:
    now = datetime.now()
    con = _conn()
    cur = con.cursor()
    ids = []
    for r in results:
        cur.execute(
            """INSERT INTO games (user_id, game_number, result, added_at, hour_of_day, day_of_week)
               VALUES (?,?,?,?,?,?)""",
            (user_id, "", r, now.isoformat(timespec="seconds"), now.hour, now.weekday()),
        )
        ids.append(cur.lastrowid)
    con.commit()
    con.close()
    return ids


def get_games(user_id: int, limit: Optional[int] = None) -> list[dict]:
    con = _conn()
    cur = con.cursor()
    if limit:
        cur.execute(
            "SELECT * FROM games WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        )
    else:
        cur.execute(
            "SELECT * FROM games WHERE user_id=? ORDER BY id DESC",
            (user_id,),
        )
    rows = [dict(r) for r in cur.fetchall()]
    con.close()
    return rows


def get_total(user_id: int) -> int:
    con = _conn()
    cur = con.cursor()
    cur.execute("SELECT COUNT(*) FROM games WHERE user_id=?", (user_id,))
    n = cur.fetchone()[0]
    con.close()
    return n


def delete_last(user_id: int) -> bool:
    con = _conn()
    cur = con.cursor()
    cur.execute("SELECT id FROM games WHERE user_id=? ORDER BY id DESC LIMIT 1", (user_id,))
    row = cur.fetchone()
    if not row:
        con.close()
        return False
    cur.execute("DELETE FROM games WHERE id=?", (row[0],))
    con.commit()
    con.close()
    return True


def delete_game_by_id(user_id: int, game_id: int) -> bool:
    con = _conn()
    cur = con.cursor()
    cur.execute("DELETE FROM games WHERE id=? AND user_id=?", (game_id, user_id))
    affected = cur.rowcount
    con.commit()
    con.close()
    return affected > 0


def clear_games(user_id: int):
    con = _conn()
    cur = con.cursor()
    cur.execute("DELETE FROM games WHERE user_id=?", (user_id,))
    cur.execute("DELETE FROM predictions WHERE user_id=?", (user_id,))
    cur.execute("DELETE FROM strategy_weights WHERE user_id=?", (user_id,))
    con.commit()
    con.close()


def restore_games_from_file(path: str, truncate: bool = True) -> dict:
    """
    Восстанавливает историю игр из JSON/CSV экспорта.
    Используется для быстрого поднятия базы после деплоя.
    """
    source = Path(path)
    if not source.exists():
        raise FileNotFoundError(f"Файл не найден: {source}")

    suffix = source.suffix.lower()
    if suffix == ".json":
        rows = json.loads(source.read_text(encoding="utf-8"))
    elif suffix == ".csv":
        with source.open("r", encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh))
    else:
        raise ValueError("Поддерживаются только .json и .csv файлы")

    normalized = []
    for row in rows:
        normalized.append({
            "id": int(row["id"]),
            "user_id": int(row["user_id"]),
            "game_number": str(row.get("game_number") or ""),
            "result": int(row["result"]),
            "added_at": str(row["added_at"]),
            "hour_of_day": int(row["hour_of_day"]) if str(row.get("hour_of_day", "")) != "" else None,
            "day_of_week": int(row["day_of_week"]) if str(row.get("day_of_week", "")) != "" else None,
        })

    con = _conn()
    cur = con.cursor()

    if truncate:
        cur.execute("DELETE FROM games")
        cur.execute("DELETE FROM predictions")
        cur.execute("DELETE FROM strategy_weights")
        cur.execute("DELETE FROM notification_log")

    cur.execute("SELECT user_id, game_number, result, added_at FROM games")
    existing = {
        (int(r["user_id"]), str(r["game_number"] or ""), int(r["result"]), str(r["added_at"]))
        for r in cur.fetchall()
    }

    inserted = 0
    skipped = 0
    for row in sorted(normalized, key=lambda item: (item["added_at"], item["id"])):
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
    return {
        "source": str(source),
        "rows": len(normalized),
        "inserted": inserted,
        "skipped": skipped,
        "truncate": truncate,
    }


# ──────────────────────────── Settings ────────────────────────────────────────

def get_settings(user_id: int) -> dict:
    con = _conn()
    cur = con.cursor()
    cur.execute("SELECT * FROM user_settings WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    con.close()
    if row:
        return dict(row)
    return {
        "user_id": user_id,
        "pause_threshold_min": int(os.getenv("PAUSE_THRESHOLD_MINUTES", "60")),
        "use_statistical": 1, "use_ema": 1, "use_serial": 1,
        "use_markov": 1, "use_zigzag": 1, "use_temporal": 1, "use_recency": 1,
        "top_n": 3,
    }


def save_settings(user_id: int, **kwargs):
    settings = get_settings(user_id)
    settings.update(kwargs)
    settings["updated_at"] = datetime.now().isoformat(timespec="seconds")
    con = _conn()
    cur = con.cursor()
    cur.execute("""
        INSERT INTO user_settings
            (user_id, pause_threshold_min,
             use_statistical, use_ema, use_serial, use_markov, use_zigzag, use_temporal, use_recency,
             top_n, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET
            pause_threshold_min=excluded.pause_threshold_min,
            use_statistical=excluded.use_statistical,
            use_ema=excluded.use_ema,
            use_serial=excluded.use_serial,
            use_markov=excluded.use_markov,
            use_zigzag=excluded.use_zigzag,
            use_temporal=excluded.use_temporal,
            use_recency=excluded.use_recency,
            top_n=excluded.top_n,
            updated_at=excluded.updated_at
    """, (
        user_id,
        settings["pause_threshold_min"],
        settings.get("use_statistical", 1),
        settings.get("use_ema", 1),
        settings.get("use_serial", 1),
        settings.get("use_markov", 1),
        settings.get("use_zigzag", 1),
        settings.get("use_temporal", 1),
        settings.get("use_recency", 1),
        settings.get("top_n", 3),
        settings["updated_at"],
    ))
    con.commit()
    con.close()


# ──────────────────────── Strategy weights ────────────────────────────────────

def get_weights(user_id: int) -> dict[str, float]:
    con = _conn()
    cur = con.cursor()
    cur.execute(
        "SELECT strategy, correct, total FROM strategy_weights WHERE user_id=?",
        (user_id,),
    )
    rows = {r["strategy"]: dict(r) for r in cur.fetchall()}
    con.close()

    settings = get_settings(user_id)
    enabled = {
        "statistical": settings.get("use_statistical", 1),
        "ema":         settings.get("use_ema", 1),
        "serial":      settings.get("use_serial", 1),
        "markov":      settings.get("use_markov", 1),
        "zigzag":      settings.get("use_zigzag", 1),
        "temporal":    settings.get("use_temporal", 1),
        "momentum":    settings.get("use_recency", 1),
    }

    weights = {}
    for s in STRATEGIES:
        if not enabled.get(s, 1):
            weights[s] = 0.0
            continue
        if s in rows and rows[s]["total"] >= 10:
            correct = rows[s]["correct"]
            total   = rows[s]["total"]
            weights[s] = (correct + 1) / (total + 11)
        else:
            weights[s] = 1 / 11

    total_w = sum(weights.values()) or 1
    return {k: v / total_w for k, v in weights.items()}


def record_prediction(user_id: int, strategy: str, predicted: int):
    con = _conn()
    cur = con.cursor()
    cur.execute(
        "INSERT INTO predictions (user_id, strategy, predicted, created_at) VALUES (?,?,?,?)",
        (user_id, strategy, predicted, datetime.now().isoformat(timespec="seconds")),
    )
    con.commit()
    con.close()


def update_strategy_stats(user_id: int, actual: int):
    con = _conn()
    cur = con.cursor()
    cur.execute(
        """SELECT id, strategy, predicted FROM predictions
           WHERE user_id=? AND actual IS NULL ORDER BY id DESC""",
        (user_id,),
    )
    open_preds = [dict(r) for r in cur.fetchall()]
    seen = set()
    now = datetime.now().isoformat(timespec="seconds")
    for pred in open_preds:
        s = pred["strategy"]
        if s in seen:
            continue
        seen.add(s)
        correct = 1 if pred["predicted"] == actual else 0
        cur.execute(
            "UPDATE predictions SET actual=?, correct=? WHERE id=?",
            (actual, correct, pred["id"]),
        )
        cur.execute("""
            INSERT INTO strategy_weights (user_id, strategy, correct, total, updated_at)
            VALUES (?,?,?,1,?)
            ON CONFLICT(user_id, strategy) DO UPDATE SET
                correct=correct+?, total=total+1, updated_at=?
        """, (user_id, s, correct, now, correct, now))
    con.commit()
    con.close()


def get_strategy_accuracy(user_id: int) -> dict[str, dict]:
    con = _conn()
    cur = con.cursor()
    cur.execute(
        "SELECT strategy, correct, total FROM strategy_weights WHERE user_id=?",
        (user_id,),
    )
    result = {r["strategy"]: dict(r) for r in cur.fetchall()}
    con.close()
    return result


# ──────────────────────────── Export ──────────────────────────────────────────

def export_csv(user_id: int, path: str) -> int:
    games = get_games(user_id)
    if not games:
        return 0
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=games[0].keys())
        writer.writeheader()
        writer.writerows(games)
    return len(games)


def export_json_file(user_id: int, path: str) -> int:
    games = get_games(user_id)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(games, f, ensure_ascii=False, indent=2)
    return len(games)


# ──────────────────────────── Backup ──────────────────────────────────────────

def backup_db():
    if os.path.exists(DB_PATH):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = f"{DB_PATH}.bak_{ts}"
        shutil.copy2(DB_PATH, backup_path)
        backups = sorted(
            f for f in os.listdir(".")
            if f.startswith(os.path.basename(DB_PATH) + ".bak_")
        )
        for old in backups[:-5]:
            try:
                os.remove(old)
            except Exception:
                pass


# ──────────────────────── Notification log ────────────────────────────────────

def get_notification_last(user_id: int) -> Optional[datetime]:
    con = _conn()
    cur = con.cursor()
    try:
        cur.execute("SELECT last_sent FROM notification_log WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        con.close()
        return datetime.fromisoformat(row["last_sent"]) if row else None
    except Exception:
        con.close()
        return None


def set_notification_sent(user_id: int):
    con = _conn()
    cur = con.cursor()
    now = datetime.now().isoformat(timespec="seconds")
    cur.execute(
        """INSERT INTO notification_log (user_id, last_sent) VALUES (?,?)
           ON CONFLICT(user_id) DO UPDATE SET last_sent=excluded.last_sent""",
        (user_id, now)
    )
    con.commit()
    con.close()


def get_active_users(hours: int = 24) -> list[int]:
    con = _conn()
    cur = con.cursor()
    cutoff = (datetime.now() - timedelta(hours=hours)).isoformat(timespec="seconds")
    cur.execute(
        """SELECT DISTINCT user_id FROM games
           WHERE added_at >= ?""",
        (cutoff,)
    )
    users = [row["user_id"] for row in cur.fetchall()]
    con.close()
    return users
