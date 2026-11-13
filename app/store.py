"""SQLite: әңгіме тарихы және қайталанған хабарламаларды сүзу.

Meta бір хабарламаны кейде бірнеше рет жібереді (қайта жіберу). Бот екі рет
жауап бермеуі үшін әр message_id есте сақталады.

Жеке деректер: клиенттің нөмірі мен хабарламалары сақталады. prune() ескі
жазбаларды өшіреді — оны күн сайын іске қосып отыр.
"""

import json
import sqlite3
import threading
import time
from pathlib import Path


class Store:
    def __init__(self, path: Path | str = ":memory:"):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    business_id TEXT, user TEXT, role TEXT, content TEXT, ts REAL
                );
                CREATE INDEX IF NOT EXISTS idx_messages ON messages(business_id, user, ts);
                CREATE TABLE IF NOT EXISTS seen (message_id TEXT PRIMARY KEY, ts REAL);
                CREATE TABLE IF NOT EXISTS sent (message_id TEXT PRIMARY KEY, ts REAL);
                CREATE TABLE IF NOT EXISTS state (
                    business_id TEXT, user TEXT, data TEXT, ts REAL, PRIMARY KEY (business_id, user)
                );
                CREATE TABLE IF NOT EXISTS paused (
                    business_id TEXT, user TEXT, until REAL, PRIMARY KEY (business_id, user)
                );
                """
            )
            self._db.commit()

    def first_time(self, message_id: str) -> bool:
        """Хабарлама бірінші рет келсе True, қайталанса False."""
        with self._lock:
            cur = self._db.execute(
                "INSERT OR IGNORE INTO seen(message_id, ts) VALUES (?, ?)", (message_id, time.time())
            )
            self._db.commit()
            return cur.rowcount == 1

    def mark_sent(self, message_ids: list[str]) -> None:
        """Бот өзі жіберген хабарлардың id-лері: echo келгенде оларды иесінің жауабы деп санамаймыз."""
        with self._lock:
            self._db.executemany(
                "INSERT OR IGNORE INTO sent(message_id, ts) VALUES (?, ?)",
                [(i, time.time()) for i in message_ids if i],
            )
            self._db.commit()

    def was_sent_by_bot(self, message_ids) -> bool:
        ids = [i for i in message_ids if i]
        if not ids:
            return False
        with self._lock:
            q = "SELECT 1 FROM sent WHERE message_id IN (%s) LIMIT 1" % ",".join("?" * len(ids))
            return self._db.execute(q, ids).fetchone() is not None

    def pause(self, business_id: str, user: str, hours: float) -> None:
        """Иесі клиентке өзі жазды — бот бұл чатта hours сағат үндемейді."""
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO paused VALUES (?, ?, ?)", (business_id, user, time.time() + hours * 3600)
            )
            self._db.commit()

    def is_paused(self, business_id: str, user: str) -> bool:
        with self._lock:
            row = self._db.execute(
                "SELECT until FROM paused WHERE business_id=? AND user=?", (business_id, user)
            ).fetchone()
        return bool(row and row[0] > time.time())

    def get_state(self, business_id: str, user: str, max_age_hours: float = 24) -> dict | None:
        """Базалық боттың клиент үшін күйі (қай мәзірде тұр). 24 сағаттан ескі болса — жаңа әңгіме."""
        with self._lock:
            row = self._db.execute(
                "SELECT data, ts FROM state WHERE business_id=? AND user=?", (business_id, user)
            ).fetchone()
        if not row or time.time() - row[1] > max_age_hours * 3600:
            return None
        return json.loads(row[0])

    def set_state(self, business_id: str, user: str, data: dict) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO state VALUES (?, ?, ?, ?)",
                (business_id, user, json.dumps(data, ensure_ascii=False), time.time()),
            )
            self._db.commit()

    def add(self, business_id: str, user: str, role: str, content: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO messages VALUES (?, ?, ?, ?, ?)",
                (business_id, user, role, content, time.time()),
            )
            self._db.commit()

    def history(self, business_id: str, user: str, limit: int = 6, max_age_hours: float = 24) -> list[dict]:
        """Соңғы limit хабарлама (тек соңғы max_age_hours ішіндегі), ескісі бірінші."""
        since = time.time() - max_age_hours * 3600
        with self._lock:
            rows = self._db.execute(
                "SELECT role, content FROM messages WHERE business_id=? AND user=? AND ts>=? "
                "ORDER BY ts DESC LIMIT ?",
                (business_id, user, since, limit),
            ).fetchall()
        return [{"role": r, "content": c} for r, c in reversed(rows)]

    def prune(self, days: int = 30) -> None:
        cutoff = time.time() - days * 86400
        with self._lock:
            self._db.execute("DELETE FROM messages WHERE ts < ?", (cutoff,))
            self._db.execute("DELETE FROM seen WHERE ts < ?", (cutoff,))
            self._db.execute("DELETE FROM sent WHERE ts < ?", (cutoff,))
            self._db.execute("DELETE FROM paused WHERE until < ?", (time.time(),))
            self._db.execute("DELETE FROM state WHERE ts < ?", (cutoff,))
            self._db.commit()
