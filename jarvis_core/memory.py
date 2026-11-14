"""Mémoire permanente : conversations, souvenirs, recherche plein texte et vectorielle.

Tout est stocké dans un fichier SQLite local (jarvis.db), indépendant du modèle utilisé.
- Recherche plein texte : SQLite FTS5.
- Recherche vectorielle : un vecteur local (hachage de mots et de trigrammes, hors ligne,
  toujours disponible) + un vecteur sémantique optionnel fourni par un modèle d'embeddings
  (Ollama, OpenAI ou Gemini). Les vecteurs sont stockés dans la base.
"""
import array
import json
import math
import re
import sqlite3
import threading
import time
import unicodedata
import uuid
import zlib
from pathlib import Path

LOCAL_DIM = 512
LOCAL_MODEL = "local-hash-512"
CATEGORIES = ["profil", "préférences", "projets", "connaissances", "personnes", "tâches", "divers"]
KINDS = ["durable", "temporaire"]

STOP = set("""a à au aux avec ce ces cet cette dans de des du elle en est et eux il ils je la le les leur lui
ma mais me mes moi mon ne nos notre nous on ou où par pas pour qu que qui sa se ses son sur ta te tes toi ton tu
un une vos votre vous y c d j l m n s t the and for are but not you all can her was one our out has have this that
with from what when how why est-ce quoi quel quelle quels quelles comment pourquoi quand suis es sont ai as avons
avez ont fait faire peux peut veux veut être avoir plus très bien aussi comme si mon ça cela dois doit""".split())

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations(
  id TEXT PRIMARY KEY, title TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT, conv_id TEXT NOT NULL, role TEXT NOT NULL,
  content TEXT NOT NULL, model TEXT, meta TEXT, created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conv_id, id);
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
  content, msg_id UNINDEXED, conv_id UNINDEXED, tokenize='unicode61 remove_diacritics 2');
CREATE TABLE IF NOT EXISTS memories(
  id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT NOT NULL, category TEXT NOT NULL,
  importance INTEGER NOT NULL DEFAULT 3, kind TEXT NOT NULL DEFAULT 'durable',
  source TEXT NOT NULL DEFAULT 'manuel', title TEXT, created REAL NOT NULL, updated REAL NOT NULL,
  expires_at REAL, last_used REAL, use_count INTEGER NOT NULL DEFAULT 0,
  vec_local BLOB, vec_ext BLOB, vec_model TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
  content, mem_id UNINDEXED, tokenize='unicode61 remove_diacritics 2');
CREATE TABLE IF NOT EXISTS actions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL, ok INTEGER NOT NULL);
"""


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFKD", text.lower().replace("œ", "oe").replace("æ", "ae"))
    return "".join(c for c in t if not unicodedata.combining(c))


def tokens(text: str, keep_stop=False):
    out = re.findall(r"[a-z0-9]+", normalize(text))
    return out if keep_stop else [w for w in out if len(w) > 2 and w not in STOP]


def local_vector(text: str):
    v = [0.0] * LOCAL_DIM
    words = tokens(text)
    feats = [("w:" + w, 1.0) for w in words]
    for w in words:
        p = f"^{w}$"
        feats += [("t:" + p[i:i + 3], 0.4) for i in range(len(p) - 2)]
    for f, wgt in feats:
        h = zlib.crc32(f.encode("utf-8"))
        v[h % LOCAL_DIM] += wgt if (h >> 16) & 1 else -wgt
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def pack(v) -> bytes:
    return array.array("f", v).tobytes()


def unpack(b: bytes):
    a = array.array("f")
    a.frombytes(b)
    return a


def cosine(a, b) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def fts_query(text: str, limit=12):
    words = list(dict.fromkeys(tokens(text)))[:limit]
    return " OR ".join(f'"{w}"' for w in words)


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self._open()

    def _open(self):
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self):
        with self.lock:
            self.db.close()

    def reopen(self):
        with self.lock:
            self._open()

    def snapshot(self, dest: Path):
        """Copie cohérente de la base (API de sauvegarde SQLite)."""
        with self.lock:
            out = sqlite3.connect(str(dest))
            try:
                self.db.backup(out)
            finally:
                out.close()

    # ---------- conversations ----------
    def create_conversation(self, title="Nouvelle conversation"):
        cid, now = uuid.uuid4().hex[:16], time.time()
        with self.lock:
            self.db.execute("INSERT INTO conversations VALUES(?,?,?,?)", (cid, title, now, now))
            self.db.commit()
        return {"id": cid, "title": title, "created": now, "updated": now}

    def list_conversations(self):
        with self.lock:
            rows = self.db.execute(
                "SELECT c.*, (SELECT COUNT(*) FROM messages m WHERE m.conv_id=c.id) AS n "
                "FROM conversations c ORDER BY updated DESC").fetchall()
        return [dict(r) for r in rows]

    def get_conversation(self, cid):
        with self.lock:
            c = self.db.execute("SELECT * FROM conversations WHERE id=?", (cid,)).fetchone()
            if not c:
                return None
            msgs = self.db.execute("SELECT * FROM messages WHERE conv_id=? ORDER BY id", (cid,)).fetchall()
        out = dict(c)
        out["messages"] = [self._msg(m) for m in msgs]
        return out

    @staticmethod
    def _msg(m):
        d = dict(m)
        d["meta"] = json.loads(d["meta"]) if d.get("meta") else {}
        return d

    def rename_conversation(self, cid, title):
        with self.lock:
            self.db.execute("UPDATE conversations SET title=? WHERE id=?", (title[:120], cid))
            self.db.commit()

    def delete_conversation(self, cid):
        with self.lock:
            self.db.execute("DELETE FROM messages_fts WHERE conv_id=?", (cid,))
            self.db.execute("DELETE FROM messages WHERE conv_id=?", (cid,))
            self.db.execute("DELETE FROM conversations WHERE id=?", (cid,))
            self.db.commit()

    def add_message(self, cid, role, content, model=None, meta=None):
        now = time.time()
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO messages(conv_id,role,content,model,meta,created) VALUES(?,?,?,?,?,?)",
                (cid, role, content, model, json.dumps(meta, ensure_ascii=False) if meta else None, now))
            self.db.execute("INSERT INTO messages_fts(content,msg_id,conv_id) VALUES(?,?,?)",
                            (content, cur.lastrowid, cid))
            self.db.execute("UPDATE conversations SET updated=? WHERE id=?", (now, cid))
            self.db.commit()
            return cur.lastrowid

    def search_messages(self, query, exclude_conv=None, limit=20):
        q = fts_query(query)
        if not q:
            return []
        with self.lock:
            rows = self.db.execute(
                "SELECT m.id, m.conv_id, m.role, m.content, m.created, c.title, bm25(messages_fts) AS rank "
                "FROM messages_fts f JOIN messages m ON m.id=f.msg_id JOIN conversations c ON c.id=m.conv_id "
                "WHERE messages_fts MATCH ? AND m.conv_id IS NOT ? ORDER BY rank LIMIT ?",
                (q, exclude_conv, limit)).fetchall()
        return [dict(r) for r in rows]

    def recall_messages(self, query, exclude_conv, limit=3):
        """Extraits d'anciennes conversations réellement en rapport avec la question."""
        qt = set(tokens(query))
        if len(qt) < 2:
            return []
        out = []
        for r in self.search_messages(query, exclude_conv, 12):
            mt = set(tokens(r["content"]))
            hit = len(qt & mt)
            if hit >= 2 and hit / len(qt) >= 0.4:
                r["content"] = r["content"][:500]
                out.append(r)
            if len(out) >= limit:
                break
        return out

    # ---------- souvenirs ----------
    def purge_expired(self):
        with self.lock:
            ids = [r[0] for r in self.db.execute(
                "SELECT id FROM memories WHERE expires_at IS NOT NULL AND expires_at < ?", (time.time(),))]
            for i in ids:
                self._delete_memory(i)
            self.db.commit()
        return len(ids)

    def _delete_memory(self, mid):
        self.db.execute("DELETE FROM memories_fts WHERE mem_id=?", (mid,))
        self.db.execute("DELETE FROM memories WHERE id=?", (mid,))

    def delete_memory(self, mid):
        with self.lock:
            self._delete_memory(mid)
            self.db.commit()

    @staticmethod
    def _mem(r):
        d = dict(r)
        for k in ("vec_local", "vec_ext"):
            d.pop(k, None)
        return d

    def get_memory(self, mid):
        with self.lock:
            r = self.db.execute("SELECT * FROM memories WHERE id=?", (mid,)).fetchone()
        return self._mem(r) if r else None

    def list_memories(self, category=None, kind=None):
        sql, args = "SELECT * FROM memories WHERE 1=1", []
        if category:
            sql += " AND category=?"
            args.append(category)
        if kind:
            sql += " AND kind=?"
            args.append(kind)
        with self.lock:
            rows = self.db.execute(sql + " ORDER BY updated DESC", args).fetchall()
        return [self._mem(r) for r in rows]

    def find_duplicate(self, content, vec_local):
        """Souvenir existant quasi identique (évite les répétitions)."""
        nt = set(tokens(content))
        with self.lock:
            rows = self.db.execute("SELECT id, content, vec_local FROM memories WHERE kind!='connaissance'").fetchall()
        for r in rows:
            ot = set(tokens(r["content"]))
            jac = len(nt & ot) / len(nt | ot) if nt | ot else 1.0
            if jac >= 0.75 or (r["vec_local"] and cosine(vec_local, unpack(r["vec_local"])) >= 0.92):
                return r["id"]
        return None

    def add_memory(self, content, category="divers", importance=3, kind="durable", source="manuel",
                   days=None, title=None, ext=None, dedupe=False):
        content = content.strip()
        if not content:
            raise ValueError("Souvenir vide")
        category = category if category in CATEGORIES else "divers"
        kind = kind if kind in KINDS else "durable"
        importance = max(1, min(5, int(importance or 3)))
        vl = local_vector(content)
        now = time.time()
        if dedupe:
            dup = self.find_duplicate(content, vl)
            if dup:
                with self.lock:
                    self.db.execute("UPDATE memories SET updated=?, importance=MAX(importance,?) WHERE id=?",
                                    (now, importance, dup))
                    self.db.commit()
                return {"id": dup, "status": "déjà connu"}
        expires = now + float(days) * 86400 if (kind == "temporaire" and days) else None
        if kind == "temporaire" and not expires:
            expires = now + 14 * 86400
        ev, em = (pack(ext[0]), ext[1]) if ext else (None, None)
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO memories(content,category,importance,kind,source,title,created,updated,expires_at,"
                "vec_local,vec_ext,vec_model) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (content, category, importance, kind, source, title, now, now, expires, pack(vl), ev, em))
            self.db.execute("INSERT INTO memories_fts(content,mem_id) VALUES(?,?)", (content, cur.lastrowid))
            self.db.commit()
            return {"id": cur.lastrowid, "status": "créé"}

    def update_memory(self, mid, patch, ext=None):
        cur = self.get_memory(mid)
        if not cur:
            return None
        content = (patch.get("content") or cur["content"]).strip()
        category = patch.get("category", cur["category"])
        category = category if category in CATEGORIES else cur["category"]
        kind = patch.get("kind", cur["kind"])
        kind = kind if kind in KINDS else cur["kind"]
        importance = max(1, min(5, int(patch.get("importance", cur["importance"]))))
        now = time.time()
        expires = cur["expires_at"]
        if kind != "temporaire":
            expires = None
        elif patch.get("days"):
            expires = now + float(patch["days"]) * 86400
        elif not expires:
            expires = now + 14 * 86400
        changed = content != cur["content"]
        with self.lock:
            self.db.execute(
                "UPDATE memories SET content=?,category=?,importance=?,kind=?,expires_at=?,updated=? WHERE id=?",
                (content, category, importance, kind, expires, now, mid))
            if changed:
                self.db.execute("UPDATE memories SET vec_local=?, vec_ext=?, vec_model=? WHERE id=?",
                                (pack(local_vector(content)), pack(ext[0]) if ext else None,
                                 ext[1] if ext else None, mid))
                self.db.execute("DELETE FROM memories_fts WHERE mem_id=?", (mid,))
                self.db.execute("INSERT INTO memories_fts(content,mem_id) VALUES(?,?)", (content, mid))
            self.db.commit()
        return self.get_memory(mid)

    def set_ext_vector(self, mid, vec, model):
        with self.lock:
            self.db.execute("UPDATE memories SET vec_ext=?, vec_model=? WHERE id=?", (pack(vec), model, mid))
            self.db.commit()

    def memories_missing_ext(self, model):
        with self.lock:
            rows = self.db.execute("SELECT id, content FROM memories WHERE vec_model IS NOT ? OR vec_ext IS NULL",
                                   (model,)).fetchall()
        return [dict(r) for r in rows]

    def search_memories(self, query, top_k=8, ext=None, mark_used=False, always_core=False):
        """Recherche hybride : similarité vectorielle + mots-clés + importance.

        ext = (vecteur, nom_du_modèle) si un modèle d'embeddings est actif.
        """
        self.purge_expired()
        qt = set(tokens(query))
        ql = local_vector(query) if qt else None
        with self.lock:
            rows = self.db.execute("SELECT * FROM memories").fetchall()
        scored = []
        for r in rows:
            semantic = bool(ext and r["vec_ext"] and r["vec_model"] == ext[1])
            if semantic:
                cos = cosine(ext[0], unpack(r["vec_ext"]))
            else:
                cos = cosine(ql, unpack(r["vec_local"])) if (ql and r["vec_local"]) else 0.0
            mt = set(tokens(r["content"]))
            kw = len(qt & mt) / len(qt) if qt else 0.0
            score = 0.65 * max(cos, 0.0) + 0.25 * kw + 0.02 * r["importance"]
            relevant = kw > 0 or cos >= (0.45 if semantic else 0.22)
            core = always_core and r["importance"] >= 5 and r["kind"] != "connaissance"
            if relevant or core:
                d = self._mem(r)
                d.update(score=round(score, 4), similarity=round(cos, 4), core=core and not relevant)
                scored.append(d)
        scored.sort(key=lambda d: d["score"], reverse=True)
        best = scored[0]["score"] if scored else 0
        ranked = [d for d in scored if not d["core"] and d["score"] >= 0.5 * best][:top_k]
        cores = [d for d in scored if d["core"]][:5]
        out = ranked + [c for c in cores if c["id"] not in {d["id"] for d in ranked}]
        if mark_used and out:
            now = time.time()
            with self.lock:
                self.db.executemany("UPDATE memories SET last_used=?, use_count=use_count+1 WHERE id=?",
                                    [(now, d["id"]) for d in out])
                self.db.commit()
        return out

    def log_action(self, kind, detail, ok=True):
        with self.lock:
            self.db.execute("INSERT INTO actions(at,kind,detail,ok) VALUES(?,?,?,?)", (time.time(), kind, detail[:300], int(ok)))
            self.db.commit()

    def actions(self, limit=30):
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM actions ORDER BY id DESC LIMIT ?", (limit,))]

    def stats(self):
        with self.lock:
            g = lambda q: self.db.execute(q).fetchone()[0]
            cats = {r[0]: r[1] for r in self.db.execute("SELECT category, COUNT(*) FROM memories GROUP BY category")}
            return {
                "conversations": g("SELECT COUNT(*) FROM conversations"),
                "messages": g("SELECT COUNT(*) FROM messages"),
                "memories": g("SELECT COUNT(*) FROM memories"),
                "memories_semantic": g("SELECT COUNT(*) FROM memories WHERE vec_ext IS NOT NULL"),
                "by_category": cats,
                "models_used": {r[0]: r[1] for r in self.db.execute(
                    "SELECT model, COUNT(*) FROM messages WHERE role='assistant' AND model IS NOT NULL GROUP BY model")},
                "db_bytes": self.path.stat().st_size if self.path.exists() else 0,
            }


def chunk_text(text: str, size=900):
    """Découpe un document importé en passages (par paragraphes)."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks, cur = [], ""
    for p in paras:
        while len(p) > size:
            cut = p.rfind(" ", 0, size)
            cut = cut if cut > size // 2 else size
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(p[:cut])
            p = p[cut:].strip()
        if len(cur) + len(p) + 2 > size and cur:
            chunks.append(cur)
            cur = p
        else:
            cur = f"{cur}\n\n{p}" if cur else p
    if cur:
        chunks.append(cur)
    return chunks
