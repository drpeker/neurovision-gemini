"""Robot hafizasi (SQLite, Pi'de yerel).

- yakin : oturum devam belirteci (kv) -> yeniden baslayinca ayni sohbete donus
- orta  : konusma dokumu (turns) + sohbet ozetleri (episodes)
- uzun  : kalici bilgiler (facts, FTS5 ile aranir)
"""

import re
import sqlite3
import time
from pathlib import Path

CATEGORIES = ["kişi", "tercih", "ev", "eşya", "alışkanlık", "diğer"]
# Eski kayitlardaki Turkce karaktersiz kategori adlari
OLD_CATEGORIES = {"kisi": "kişi", "esya": "eşya", "rutin": "alışkanlık", "diger": "diğer"}

SCHEMA = """
create table if not exists kv(key text primary key, value text, ts real);
create table if not exists turns(id integer primary key, ts real, role text, text text);
create table if not exists episodes(
    id integer primary key, ts_start real, ts_end real, last_turn integer, summary text);
create table if not exists facts(
    id integer primary key, ts real, category text, text text, importance integer, source text);
-- Dizin katlanmis metni tutar (_fold); asil metin facts tablosunda
create virtual table if not exists facts_fts using fts5(
    text, content='', contentless_delete=1, tokenize='unicode61 remove_diacritics 2');
"""

STOPWORDS = {"bir", "bu", "su", "ne", "nerede", "neydi", "nasil", "mi", "mu", "ile", "ve",
             "icin", "benim", "senin", "hakkinda", "hatirla", "hatirliyor", "musun", "misin"}


_ASCII = str.maketrans("çğıöşüâîû", "cgiosuaiu")


def _fold(text: str) -> str:
    # Arama icin Turkce harfleri katla: "Anahtarlarım Şurada" -> "anahtarlarim surada"
    return text.replace("İ", "i").replace("I", "i").lower().replace("i̇", "i").translate(_ASCII)


def _stem(word: str) -> str:
    # Turkce ekler icin kaba kok: uzun kelimede ilk 5 harf ("anahtarlarim" -> "anaht"),
    # kisada son harfi at ("adim" -> "adi", "kizim" -> "kizi")
    return word[:5] if len(word) > 5 else word[:max(3, len(word) - 1)]


def _fts_query(text: str) -> str:
    words = [w for w in re.findall(r"\w{3,}", _fold(text)) if w not in STOPWORDS]
    return " OR ".join(f'"{_stem(w)}"*' for w in words)


def _date(ts: float) -> str:
    return time.strftime("%d.%m.%Y %H:%M", time.localtime(ts))


class Memory:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.executescript(SCHEMA)
        for old, new in OLD_CATEGORIES.items():
            self._db.execute("update facts set category=? where category=?", (new, old))
        self._db.commit()

    # --- yakin: oturum belirteci ---
    def get(self, key: str, max_age: float | None = None) -> str | None:
        row = self._db.execute("select value, ts from kv where key=?", (key,)).fetchone()
        if not row or (max_age is not None and time.time() - row[1] > max_age):
            return None
        return row[0]

    def set(self, key: str, value: str) -> None:
        self._db.execute("insert or replace into kv values (?, ?, ?)", (key, value, time.time()))
        self._db.commit()

    # --- orta: dokum ve ozetler ---
    def add_turn(self, role: str, text: str) -> None:
        text = text.strip()
        if text:
            self._db.execute("insert into turns(ts, role, text) values (?, ?, ?)",
                             (time.time(), role, text))
            self._db.commit()

    def pending_turns(self) -> list[tuple[int, float, str, str]]:
        last = self._db.execute("select coalesce(max(last_turn), 0) from episodes").fetchone()[0]
        return self._db.execute(
            "select id, ts, role, text from turns where id > ? order by id", (last,)).fetchall()

    def add_episode(self, summary: str, turns: list) -> None:
        self._db.execute(
            "insert into episodes(ts_start, ts_end, last_turn, summary) values (?, ?, ?, ?)",
            (turns[0][1], turns[-1][1], turns[-1][0], summary.strip()))
        self._db.commit()

    def recent_episodes(self, n: int) -> list[tuple[float, str]]:
        rows = self._db.execute(
            "select ts_start, summary from episodes order by id desc limit ?", (n,)).fetchall()
        return rows[::-1]

    # --- uzun: kalici bilgiler ---
    def remember(self, text: str, category: str = "diğer", importance: int = 3,
                 source: str = "tool") -> int | None:
        text = text.strip()
        if not text:
            return None
        folded = _fold(text).rstrip(". ")
        if any(_fold(t).rstrip(". ") == folded for (t,) in self._db.execute("select text from facts")):
            return None
        category = OLD_CATEGORIES.get(category, category)
        category = category if category in CATEGORIES else "diğer"
        importance = max(1, min(5, int(importance)))
        cur = self._db.execute(
            "insert into facts(ts, category, text, importance, source) values (?, ?, ?, ?, ?)",
            (time.time(), category, text, importance, source))
        self._db.execute("insert into facts_fts(rowid, text) values (?, ?)",
                         (cur.lastrowid, f"{folded} {category}"))
        self._db.commit()
        return cur.lastrowid

    def recall(self, query: str, limit: int = 8) -> list[tuple[int, str, str]]:
        q = _fts_query(query)
        if not q:
            return []
        return self._db.execute(
            "select f.id, f.category, f.text from facts_fts join facts f on f.id = facts_fts.rowid "
            "where facts_fts match ? order by rank limit ?", (q, limit)).fetchall()

    def search_turns(self, query: str, limit: int = 6) -> list[tuple[float, str, str]]:
        """Ozetlerde olmayan ayrintilar icin ham dokumde basit arama."""
        stems = [_stem(w) for w in re.findall(r"\w{3,}", _fold(query)) if w not in STOPWORDS][:4]
        if not stems:
            return []
        hits = []
        for row in self._db.execute(
                "select ts, role, text from turns order by id desc limit 2000"):
            if any(s in _fold(row[2]) for s in stems):
                hits.append(row)
                if len(hits) >= limit:
                    break
        return hits

    def forget(self, query: str) -> list[str]:
        q = _fts_query(query)
        if not q:
            return []
        rows = self._db.execute(
            "select f.id, f.text from facts_fts join facts f on f.id = facts_fts.rowid "
            "where facts_fts match ? order by rank limit 1", (q,)).fetchall()
        for fid, _ in rows:
            self._db.execute("delete from facts where id=?", (fid,))
            self._db.execute("delete from facts_fts where rowid=?", (fid,))
        self._db.commit()
        return [t for _, t in rows]

    def top_facts(self, n: int) -> list[tuple[int, str, str]]:
        return self._db.execute(
            "select id, category, text from facts order by importance desc, ts desc limit ?",
            (n,)).fetchall()

    def count(self) -> dict[str, int]:
        return {t: self._db.execute(f"select count(*) from {t}").fetchone()[0]
                for t in ("turns", "episodes", "facts")}

    # --- yeni oturum icin baglam ---
    def context(self, n_facts: int, n_episodes: int, n_recent: int = 12) -> str:
        parts = []
        facts = self.top_facts(n_facts)
        if facts:
            parts.append("Kullanıcı ve ev hakkında bildiklerin:\n" +
                         "\n".join(f"- {t}" for _, _, t in facts))
        eps = self.recent_episodes(n_episodes)
        if eps:
            parts.append("Önceki sohbetlerinizin özeti:\n" +
                         "\n".join(f"- {_date(ts)}: {s}" for ts, s in eps))
        # Henuz ozetlenmemis son konusma: temiz oturum acilsa da az once konusulan kopmasin
        recent = self.pending_turns()[-n_recent:]
        if recent:
            parts.append("Az önceki konuşmanız:\n" + "\n".join(
                f"- {'Kullanıcı' if r == 'user' else 'Sen'}: {t}" for _, _, r, t in recent))
        return "\n\n".join(parts)

    def close(self) -> None:
        self._db.close()
