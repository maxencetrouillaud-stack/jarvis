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
# nature d'un souvenir : à quoi il sert (indépendant de la catégorie, qui dit de quoi il parle)
NATURES = ["personnelle", "projet", "technique", "temporaire"]


def derive_nature(category, kind):
    """Nature déduite d'un souvenir enregistré avant la version 0.6 (ou quand le modèle ne la donne pas)."""
    if kind == "temporaire":
        return "temporaire"
    if category in ("projets", "tâches"):
        return "projet"
    if category == "connaissances":
        return "technique"
    return "personnelle"


# Mots de même sens, pour retrouver un souvenir formulé autrement (sans modèle d'embeddings).
# Chaque ligne est un groupe ; un mot peut appartenir à plusieurs groupes.
SYNONYMS = """travail boulot job emploi taf poste bosse bosser travaille travailler entreprise
voiture auto automobile vehicule bagnole
logement appartement appart maison domicile habite habiter vit vivre loge loger chez
etudes etudiant etudie etudier fac universite ecole iut cours formation but licence master
sport entrainement musculation muscu course courir running footing seance salle
repas manger mange nourriture cuisine cuisiner plat recette diner dejeuner aliment
aime adore prefere preference apprecie kiffe gout
ordinateur ordi pc laptop portable machine
telephone smartphone mobile portable
argent budget finances depenses depense salaire
stage stagiaire alternance alternant
nom appelle prenom surnom
age ans anniversaire naissance ne
copine copain partenaire compagne compagnon conjoint
famille parents mere pere frere soeur
ami amis pote potes copains
musique chanson chansons ecoute groupe
vacances voyage voyager conges sejour
examen examens partiel partiels controle oral soutenance epreuve
sante medecin maladie allergie allergique
langue langues anglais espagnol allemand
code programmation programme programmer developpe developper logiciel script
reponse reponses explication explications style ton format""".splitlines()

_SUFFIXES = ("issements", "issement", "ements", "ement", "ations", "ation", "atrices", "atrice", "ateurs", "ateur",
             "euses", "euse", "eurs", "eur", "ables", "able", "iques", "ique", "ismes", "isme", "istes", "iste",
             "ites", "ite", "ances", "ance", "ences", "ence", "antes", "ante", "ants", "ant", "aient", "ees", "ee",
             "es", "er", "ez", "ais", "ait", "s", "x", "e")


def stem(word: str) -> str:
    """Racine approximative d'un mot français (sans accents) : « mécaniques » et « mécanique » donnent la même."""
    for suf in _SUFFIXES:
        if word.endswith(suf) and len(word) - len(suf) >= 4:
            return word[:-len(suf)]
    return word


_GROUPS = {}
for _i, _line in enumerate(SYNONYMS):
    for _w in _line.split():
        _GROUPS.setdefault(stem(_w), set()).add(_i)


def concepts(word: str):
    """Racine du mot et groupes de synonymes auxquels il appartient."""
    st = stem(word)
    return {st} | {f"g{g}" for g in _GROUPS.get(st, ())}

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
CREATE TABLE IF NOT EXISTS memory_versions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, memory_id INTEGER NOT NULL, content TEXT NOT NULL, category TEXT,
  importance INTEGER, kind TEXT, nature TEXT, project TEXT, at REAL NOT NULL, reason TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_memver ON memory_versions(memory_id, id);
CREATE TABLE IF NOT EXISTS memory_proposals(
  id INTEGER PRIMARY KEY AUTOINCREMENT, created REAL NOT NULL, target_id INTEGER, content TEXT NOT NULL,
  category TEXT, importance INTEGER, kind TEXT, days REAL, nature TEXT, project TEXT, reason TEXT, conv_id TEXT,
  old_content TEXT, status TEXT NOT NULL DEFAULT 'en attente', decided REAL);
CREATE TABLE IF NOT EXISTS runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, conv_id TEXT, message_id INTEGER, strategy TEXT,
  complexity TEXT, route TEXT, model TEXT, steps INTEGER, calls INTEGER, verified INTEGER, ok INTEGER,
  duration REAL, trace TEXT);
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
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(memories)")}
        for col in ("nature", "project"):  # base créée avant la version 0.6
            if col not in cols:
                self.db.execute(f"ALTER TABLE memories ADD COLUMN {col} TEXT")
        for r in self.db.execute("SELECT id, category, kind FROM memories WHERE nature IS NULL").fetchall():
            self.db.execute("UPDATE memories SET nature=? WHERE id=?", (derive_nature(r["category"], r["kind"]), r["id"]))
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
                self._delete_memory(i, "expiré")
            self.db.commit()
        return len(ids)

    def _version(self, row, reason):
        """Garde une copie de l'état d'un souvenir avant sa modification ou sa suppression."""
        self.db.execute(
            "INSERT INTO memory_versions(memory_id,content,category,importance,kind,nature,project,at,reason) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (row["id"], row["content"], row["category"], row["importance"], row["kind"], row["nature"], row["project"],
             time.time(), reason))

    def _delete_memory(self, mid, reason=None):
        if reason:
            r = self.db.execute("SELECT * FROM memories WHERE id=?", (mid,)).fetchone()
            if r:
                self._version(r, reason)
        self.db.execute("DELETE FROM memories_fts WHERE mem_id=?", (mid,))
        self.db.execute("DELETE FROM memories WHERE id=?", (mid,))

    def delete_memory(self, mid):
        with self.lock:
            self._delete_memory(mid, "supprimé")
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

    def list_memories(self, category=None, kind=None, nature=None, project=None):
        sql, args = "SELECT * FROM memories WHERE 1=1", []
        for col, val in (("category", category), ("kind", kind), ("nature", nature), ("project", project)):
            if val:
                sql += f" AND {col}=?"
                args.append(val)
        with self.lock:
            rows = self.db.execute(sql + " ORDER BY updated DESC", args).fetchall()
        return [self._mem(r) for r in rows]

    def projects(self):
        """Projets connus de la mémoire, avec le nombre de souvenirs rattachés."""
        with self.lock:
            rows = self.db.execute("SELECT project, COUNT(*) AS n, MAX(updated) AS updated FROM memories "
                                   "WHERE project IS NOT NULL AND project!='' GROUP BY project ORDER BY updated DESC")
            return [dict(r) for r in rows]

    def find_duplicate(self, content, vec_local, ext=None):
        """Souvenir existant quasi identique (évite les répétitions)."""
        nt = set(tokens(content))
        with self.lock:
            rows = self.db.execute("SELECT id, content, vec_local, vec_ext, vec_model FROM memories "
                                   "WHERE kind!='connaissance'").fetchall()
        for r in rows:
            ot = set(tokens(r["content"]))
            jac = len(nt & ot) / len(nt | ot) if nt | ot else 1.0
            if jac >= 0.75 or (r["vec_local"] and cosine(vec_local, unpack(r["vec_local"])) >= 0.92):
                return r["id"]
            if ext and r["vec_ext"] and r["vec_model"] == ext[1] and cosine(ext[0], unpack(r["vec_ext"])) >= 0.95:
                return r["id"]
        return None

    def add_memory(self, content, category="divers", importance=3, kind="durable", source="manuel",
                   days=None, title=None, ext=None, dedupe=False, nature=None, project=None):
        content = content.strip()
        if not content:
            raise ValueError("Souvenir vide")
        category = category if category in CATEGORIES else "divers"
        kind = kind if kind in KINDS else "durable"
        importance = max(1, min(5, int(importance or 3)))
        nature = nature if nature in NATURES else derive_nature(category, kind)
        if kind == "temporaire":
            nature = "temporaire"
        project = (str(project).strip()[:80] or None) if project else None
        vl = local_vector(content)
        now = time.time()
        if dedupe:
            dup = self.find_duplicate(content, vl, ext)
            if dup:
                with self.lock:
                    self.db.execute("UPDATE memories SET updated=?, importance=MAX(importance,?), "
                                    "project=COALESCE(project,?) WHERE id=?", (now, importance, project, dup))
                    self.db.commit()
                return {"id": dup, "status": "déjà connu"}
        expires = now + float(days) * 86400 if (kind == "temporaire" and days) else None
        if kind == "temporaire" and not expires:
            expires = now + 14 * 86400
        ev, em = (pack(ext[0]), ext[1]) if ext else (None, None)
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO memories(content,category,importance,kind,source,title,created,updated,expires_at,"
                "vec_local,vec_ext,vec_model,nature,project) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (content, category, importance, kind, source, title, now, now, expires, pack(vl), ev, em,
                 nature, project))
            self.db.execute("INSERT INTO memories_fts(content,mem_id) VALUES(?,?)", (content, cur.lastrowid))
            self.db.commit()
            return {"id": cur.lastrowid, "status": "créé"}

    def update_memory(self, mid, patch, ext=None, reason="modifié à la main"):
        """Modifie un souvenir. L'état précédent est conservé dans son historique (restaurable)."""
        with self.lock:
            row = self.db.execute("SELECT * FROM memories WHERE id=?", (mid,)).fetchone()
        if not row:
            return None
        cur = self._mem(row)
        content = (patch.get("content") or cur["content"]).strip()
        category = patch.get("category", cur["category"])
        category = category if category in CATEGORIES else cur["category"]
        kind = patch.get("kind", cur["kind"])
        kind = kind if kind in KINDS else cur["kind"]
        importance = max(1, min(5, int(patch.get("importance", cur["importance"]))))
        nature = patch.get("nature") or cur["nature"]
        nature = nature if nature in NATURES else derive_nature(category, kind)
        if kind == "temporaire":
            nature = "temporaire"
        elif nature == "temporaire":
            nature = derive_nature(category, kind)
        project = patch["project"] if "project" in patch else cur["project"]
        project = (str(project).strip()[:80] or None) if project else None
        now = time.time()
        expires = cur["expires_at"]
        if kind != "temporaire":
            expires = None
        elif patch.get("days"):
            expires = now + float(patch["days"]) * 86400
        elif not expires:
            expires = now + 14 * 86400
        changed = content != cur["content"]
        meaningful = changed or (category, kind, importance, nature, project) != (
            cur["category"], cur["kind"], cur["importance"], cur["nature"], cur["project"])
        with self.lock:
            if meaningful:
                self._version(row, reason)
            self.db.execute(
                "UPDATE memories SET content=?,category=?,importance=?,kind=?,expires_at=?,updated=?,nature=?,project=? "
                "WHERE id=?", (content, category, importance, kind, expires, now, nature, project, mid))
            if changed:
                self.db.execute("UPDATE memories SET vec_local=?, vec_ext=?, vec_model=? WHERE id=?",
                                (pack(local_vector(content)), pack(ext[0]) if ext else None,
                                 ext[1] if ext else None, mid))
                self.db.execute("DELETE FROM memories_fts WHERE mem_id=?", (mid,))
                self.db.execute("INSERT INTO memories_fts(content,mem_id) VALUES(?,?)", (content, mid))
            self.db.commit()
        return self.get_memory(mid)

    # ---------- historique et propositions ----------
    def versions(self, mid):
        with self.lock:
            return [dict(r) for r in self.db.execute(
                "SELECT * FROM memory_versions WHERE memory_id=? ORDER BY id DESC", (mid,))]

    def deleted_memories(self, limit=50):
        """Souvenirs supprimés (ou expirés) qui peuvent encore être récupérés depuis leur historique."""
        with self.lock:
            return [dict(r) for r in self.db.execute(
                "SELECT v.* FROM memory_versions v WHERE v.id = (SELECT MAX(id) FROM memory_versions w "
                "WHERE w.memory_id=v.memory_id) AND v.reason IN ('supprimé','expiré') "
                "AND v.memory_id NOT IN (SELECT id FROM memories) ORDER BY v.id DESC LIMIT ?", (limit,))]

    def restore_version(self, mid, vid, ext=None):
        """Remet un souvenir dans un état antérieur (l'état actuel rejoint l'historique)."""
        with self.lock:
            v = self.db.execute("SELECT * FROM memory_versions WHERE id=? AND memory_id=?", (vid, mid)).fetchone()
        if not v:
            raise ValueError("Version introuvable")
        if not self.get_memory(mid):  # souvenir supprimé depuis : on le recrée sous le même numéro
            with self.lock:
                now = time.time()
                self.db.execute(
                    "INSERT INTO memories(id,content,category,importance,kind,source,created,updated,vec_local,nature,project) "
                    "VALUES(?,?,?,?,?,'restauré',?,?,?,?,?)",
                    (mid, v["content"], v["category"] or "divers", v["importance"] or 3,
                     "durable" if v["kind"] == "temporaire" else (v["kind"] or "durable"), now, now,
                     pack(local_vector(v["content"])), v["nature"], v["project"]))
                self.db.execute("INSERT INTO memories_fts(content,mem_id) VALUES(?,?)", (v["content"], mid))
                self.db.commit()
            return self.get_memory(mid)
        return self.update_memory(mid, {"content": v["content"], "category": v["category"], "importance": v["importance"],
                                        "kind": v["kind"], "nature": v["nature"], "project": v["project"]},
                                  ext, reason="avant restauration")

    def add_proposal(self, target_id, content, category, importance, kind, days, nature, project, reason, conv_id=None):
        old = self.get_memory(target_id) if target_id else None
        with self.lock:
            dup = self.db.execute("SELECT id FROM memory_proposals WHERE status='en attente' AND content=? "
                                  "AND COALESCE(target_id,0)=?", (content, target_id or 0)).fetchone()
            if dup:
                return dup["id"]
            cur = self.db.execute(
                "INSERT INTO memory_proposals(created,target_id,content,category,importance,kind,days,nature,project,"
                "reason,conv_id,old_content) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (time.time(), target_id, content, category, importance, kind, days, nature, project, reason[:300],
                 conv_id, old["content"] if old else None))
            self.db.commit()
            return cur.lastrowid

    def proposals(self, status="en attente", limit=100):
        with self.lock:
            return [dict(r) for r in self.db.execute(
                "SELECT * FROM memory_proposals WHERE status=? ORDER BY id DESC LIMIT ?", (status, limit))]

    def get_proposal(self, pid):
        with self.lock:
            r = self.db.execute("SELECT * FROM memory_proposals WHERE id=?", (pid,)).fetchone()
        return dict(r) if r else None

    def decide_proposal(self, pid, accept, ext=None):
        p = self.get_proposal(pid)
        if not p or p["status"] != "en attente":
            raise ValueError("Proposition introuvable ou déjà traitée")
        result = None
        if accept:
            patch = {"content": p["content"], "category": p["category"], "importance": p["importance"],
                     "kind": p["kind"], "days": p["days"], "nature": p["nature"], "project": p["project"]}
            if p["target_id"] and self.get_memory(p["target_id"]):
                result = self.update_memory(p["target_id"], patch, ext, reason="mise à jour validée")
            else:
                r = self.add_memory(p["content"], p["category"] or "divers", p["importance"] or 3, p["kind"] or "durable",
                                    "auto", p["days"], ext=ext, nature=p["nature"], project=p["project"])
                result = self.get_memory(r["id"])
        with self.lock:
            self.db.execute("UPDATE memory_proposals SET status=?, decided=? WHERE id=?",
                            ("acceptée" if accept else "refusée", time.time(), pid))
            self.db.commit()
        return result

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
        """Recherche hybride : similarité vectorielle + mots (racines et synonymes) + importance.

        ext = (vecteur, nom_du_modèle) si un modèle d'embeddings est actif.
        Les souvenirs essentiels (importance 5) et les préférences importantes (4 ou plus) sont « de base » :
        avec always_core, ils accompagnent chaque demande.
        """
        self.purge_expired()
        qt = set(tokens(query))
        qc = {w: concepts(w) for w in qt}
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
            mc = set()
            for w in tokens(r["content"]):
                mc |= concepts(w)
            hits = sum(1 for w in qt if qc[w] & mc)
            kw = hits / len(qt) if qt else 0.0
            score = 0.65 * max(cos, 0.0) + 0.25 * kw + 0.02 * r["importance"]
            relevant = kw > 0 or cos >= (0.45 if semantic else 0.22)
            core = always_core and r["kind"] != "connaissance" and (
                r["importance"] >= 5 or (r["category"] == "préférences" and r["importance"] >= 4))
            if relevant or core:
                d = self._mem(r)
                d.update(score=round(score, 4), similarity=round(cos, 4), core=core and not relevant)
                scored.append(d)
        scored.sort(key=lambda d: d["score"], reverse=True)
        best = scored[0]["score"] if scored else 0
        ranked = [d for d in scored if not d["core"] and d["score"] >= 0.5 * best][:top_k]
        cores = [d for d in scored if d["core"]][:6]
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

    # ---------- exécutions du cerveau central ----------
    def log_run(self, conv_id, message_id, strategy, complexity, route, model, steps, calls, verified, ok, duration, trace):
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO runs(at,conv_id,message_id,strategy,complexity,route,model,steps,calls,verified,ok,duration,trace) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (time.time(), conv_id, message_id, strategy, complexity, route, model, steps, calls,
                 None if verified is None else int(verified), int(ok), round(duration, 2),
                 json.dumps(trace, ensure_ascii=False)))
            self.db.commit()
            return cur.lastrowid

    def runs(self, limit=30):
        with self.lock:
            rows = self.db.execute("SELECT r.*, c.title FROM runs r LEFT JOIN conversations c ON c.id=r.conv_id "
                                   "ORDER BY r.id DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["trace"] = json.loads(d["trace"]) if d.get("trace") else {}
            out.append(d)
        return out

    def run_stats(self):
        """Chiffres réels des exécutions du cerveau central (rien n'est estimé)."""
        with self.lock:
            g = lambda q, a=(): self.db.execute(q, a).fetchone()
            total = g("SELECT COUNT(*), COALESCE(AVG(duration),0), COALESCE(SUM(calls),0), COALESCE(SUM(steps),0) FROM runs")
            by = {r[0]: {"n": r[1], "avg_s": round(r[2] or 0, 1)} for r in self.db.execute(
                "SELECT strategy, COUNT(*), AVG(duration) FROM runs GROUP BY strategy")}
            ver = g("SELECT COUNT(*), COALESCE(SUM(ok),0) FROM runs WHERE verified=1")
            failed = g("SELECT COUNT(*) FROM runs WHERE ok=0")[0]
            cx = {r[0]: r[1] for r in self.db.execute("SELECT complexity, COUNT(*) FROM runs GROUP BY complexity")}
        return {"runs": total[0], "avg_s": round(total[1], 1), "calls": total[2], "steps": total[3],
                "by_strategy": by, "by_complexity": cx, "verified": ver[0], "verified_ok": ver[1], "failed": failed}

    def observed_habits(self, min_messages=20):
        """Habitudes observées dans les échanges réels (heures, types de demandes, longueur…).

        Ce sont des statistiques calculées sur l'historique, pas des souvenirs : rien n'est deviné.
        Le résumé n'est produit qu'à partir de min_messages messages, pour ne pas généraliser trop tôt."""
        with self.lock:
            users = self.db.execute("SELECT created, length(content) FROM messages WHERE role='user' "
                                    "ORDER BY id DESC LIMIT 3000").fetchall()
            metas = self.db.execute("SELECT meta FROM messages WHERE role='assistant' AND meta IS NOT NULL "
                                    "ORDER BY id DESC LIMIT 3000").fetchall()
        n = len(users)
        out = {"messages": n, "enough": n >= min_messages, "min_messages": min_messages, "periods": {}, "days": {},
               "routes": {}, "deep_share": 0.0, "avg_chars": 0, "since": None, "summary": ""}
        if not n:
            return out
        periods = {"la nuit (0 h – 6 h)": 0, "le matin (6 h – 12 h)": 0, "l'après-midi (12 h – 18 h)": 0, "le soir (18 h – 24 h)": 0}
        names = list(periods)
        days = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
        dcount = {d: 0 for d in days}
        for created, _ in users:
            t = time.localtime(created)
            periods[names[t.tm_hour // 6]] += 1
            dcount[days[t.tm_wday]] += 1
        routes, deep = {}, 0
        for (raw,) in metas:
            try:
                m = json.loads(raw)
            except ValueError:
                continue
            if m.get("route"):
                routes[m["route"]] = routes.get(m["route"], 0) + 1
            deep += 1 if m.get("deep") else 0
        out.update(periods=periods, days=dcount, routes=routes, deep_share=round(deep / max(1, len(metas)), 3),
                   avg_chars=round(sum(c for _, c in users) / n), since=min(c for c, _ in users))
        if n >= min_messages:
            labels = {"code": "programmation", "reasoning": "raisonnement et calculs", "creative": "rédaction",
                      "quick": "questions rapides", "general": "questions générales"}
            parts = []
            top_p = max(periods, key=periods.get)
            if periods[top_p] / n >= 0.4:
                parts.append(f"écrit surtout {top_p} ({round(100 * periods[top_p] / n)} % des messages)")
            tot = sum(routes.values())
            if tot:
                top = sorted(routes.items(), key=lambda kv: -kv[1])[:2]
                parts.append("demandes les plus fréquentes : " + ", ".join(
                    f"{labels.get(k, k)} ({round(100 * v / tot)} %)" for k, v in top))
            parts.append(f"messages de {out['avg_chars']} caractères en moyenne")
            if out["deep_share"] >= 0.3:
                parts.append(f"active la réflexion approfondie dans {round(100 * out['deep_share'])} % des cas")
            out["summary"] = f"D'après ses {n} derniers messages, l'utilisateur " + " ; ".join(parts) + "."
        return out

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
                "by_nature": {r[0]: r[1] for r in self.db.execute("SELECT nature, COUNT(*) FROM memories GROUP BY nature")},
                "projects": g("SELECT COUNT(DISTINCT project) FROM memories WHERE project IS NOT NULL AND project!=''"),
                "proposals": g("SELECT COUNT(*) FROM memory_proposals WHERE status='en attente'"),
                "versions": g("SELECT COUNT(*) FROM memory_versions"),
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
