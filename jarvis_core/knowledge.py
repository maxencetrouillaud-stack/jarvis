"""Base de connaissances (Knowledge Core) — fichier knowledge.db, séparé de la mémoire personnelle.

Documents découpés en passages, recherche plein texte (FTS5) + vecteurs, graphe de concepts,
file d'indexation, journal des modifications, versions, doublons, questions sans source.
Toute la couche d'accès aux données est dans cette classe : la remplacer par PostgreSQL + pgvector
ne demande de réécrire que ce fichier.
"""
import hashlib
import json
import operator
import re
import sqlite3
import threading
import time
import zlib
from pathlib import Path

from . import library, vectors
from .memory import chunk_text, local_vector, normalize, pack, tokens, unpack, cosine

FULL_SCAN_LIMIT = 6000      # au-delà, la comparaison vectorielle ne porte que sur les candidats plein texte
RELIABILITY = {1: "non vérifié", 2: "faible", 3: "correct", 4: "fiable", 5: "référence officielle"}

TAXONOMY = {
    "Sciences fondamentales": ["Mathématiques", "Physique", "Mécanique", "Thermodynamique", "Chimie",
                               "Matériaux et métallurgie", "Électricité et électronique", "Astronomie", "Biologie"],
    "Ingénierie et industrie": ["Génie mécanique", "CAO, FAO, DAO", "Logiciels de CAO (FreeCAD, CATIA, SolidWorks)",
                                "CNC et programmation ISO", "Impression 3D",
                                "Usinage", "Soudage, tôlerie, chaudronnerie", "Résistance des matériaux",
                                "Simulation (FEM, CFD, fatigue, vibrations)", "Normes (ISO, DIN, EN)",
                                "Maintenance industrielle", "Automatisme et robotique", "Métrologie"],
    "Informatique et IA": ["Langages de programmation", "Développement web et logiciel", "Architecture informatique",
                           "Réseaux et systèmes", "Cybersécurité défensive", "Intelligence artificielle",
                           "LLM, RAG, embeddings", "Agents autonomes", "Bases de données et API", "Cloud et DevOps"],
    "Connaissances générales": ["Histoire et géographie", "Économie et gestion", "Langues et traduction",
                                "Littérature et philosophie", "Culture générale", "Droit et réglementation",
                                "Environnement et énergie"],
    "Vie quotidienne": ["Cuisine et recettes", "Sport et entraînement", "Automobile", "Bricolage",
                        "Informatique personnelle", "Organisation et productivité"],
    "Mes documents": [],
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS categories(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, parent_id INTEGER, UNIQUE(name, parent_id));
CREATE TABLE IF NOT EXISTS documents(
  id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, description TEXT DEFAULT '', category_id INTEGER,
  keywords TEXT DEFAULT '', source_type TEXT NOT NULL, source TEXT DEFAULT '', license TEXT DEFAULT '',
  refs TEXT DEFAULT '', published TEXT DEFAULT '', imported REAL NOT NULL, verified REAL NOT NULL,
  updated REAL NOT NULL, reliability INTEGER NOT NULL DEFAULT 3, sha256 TEXT NOT NULL, original TEXT DEFAULT '',
  chars INTEGER DEFAULT 0, n_chunks INTEGER DEFAULT 0, version INTEGER DEFAULT 1, dup_of INTEGER,
  graph_done INTEGER DEFAULT 0, vec_local BLOB);
CREATE INDEX IF NOT EXISTS idx_doc_sha ON documents(sha256);
CREATE INDEX IF NOT EXISTS idx_doc_cat ON documents(category_id);
CREATE TABLE IF NOT EXISTS chunks(
  id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id INTEGER NOT NULL, ord INTEGER NOT NULL, locator TEXT DEFAULT '',
  content TEXT NOT NULL, vec_local BLOB, vec_ext BLOB, vec_model TEXT);
CREATE INDEX IF NOT EXISTS idx_chunk_doc ON chunks(doc_id, ord);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  content, title, chunk_id UNINDEXED, doc_id UNINDEXED, tokenize='unicode61 remove_diacritics 2');
CREATE TABLE IF NOT EXISTS doc_versions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id INTEGER NOT NULL, version INTEGER NOT NULL, sha256 TEXT,
  saved REAL NOT NULL, content BLOB);
CREATE TABLE IF NOT EXISTS entities(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, norm TEXT NOT NULL UNIQUE, type TEXT DEFAULT 'concept');
CREATE TABLE IF NOT EXISTS relations(
  id INTEGER PRIMARY KEY AUTOINCREMENT, src INTEGER NOT NULL, dst INTEGER NOT NULL, label TEXT NOT NULL,
  doc_id INTEGER, UNIQUE(src, dst, label));
CREATE INDEX IF NOT EXISTS idx_rel_src ON relations(src);
CREATE INDEX IF NOT EXISTS idx_rel_dst ON relations(dst);
CREATE TABLE IF NOT EXISTS jobs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, label TEXT NOT NULL, payload TEXT,
  status TEXT NOT NULL DEFAULT 'en file', detail TEXT DEFAULT '', created REAL NOT NULL, finished REAL);
CREATE TABLE IF NOT EXISTS journal(
  id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, action TEXT NOT NULL, doc_id INTEGER,
  title TEXT DEFAULT '', detail TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS gaps(
  id INTEGER PRIMARY KEY AUTOINCREMENT, question TEXT NOT NULL, norm TEXT NOT NULL UNIQUE, count INTEGER DEFAULT 1,
  created REAL NOT NULL, last REAL NOT NULL, status TEXT NOT NULL DEFAULT 'ouvert');
CREATE TABLE IF NOT EXISTS proposals(
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, title TEXT NOT NULL, lang TEXT DEFAULT 'fr',
  snippet TEXT DEFAULT '', url TEXT NOT NULL UNIQUE, reason TEXT DEFAULT '', category_id INTEGER,
  created REAL NOT NULL, status TEXT NOT NULL DEFAULT 'en attente');
CREATE TABLE IF NOT EXISTS extracts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id INTEGER NOT NULL, kind TEXT NOT NULL, label TEXT DEFAULT '',
  content TEXT NOT NULL, locator TEXT DEFAULT '', value REAL, unit TEXT);
CREATE INDEX IF NOT EXISTS idx_extract_doc ON extracts(doc_id);
CREATE INDEX IF NOT EXISTS idx_extract_kind ON extracts(kind);
CREATE TABLE IF NOT EXISTS links(
  id INTEGER PRIMARY KEY AUTOINCREMENT, a TEXT NOT NULL, b TEXT NOT NULL, kind TEXT NOT NULL, score REAL DEFAULT 0,
  created REAL NOT NULL, UNIQUE(a, b, kind));
CREATE INDEX IF NOT EXISTS idx_links_a ON links(a);
CREATE INDEX IF NOT EXISTS idx_links_b ON links(b);
CREATE TABLE IF NOT EXISTS watched_files(
  path TEXT PRIMARY KEY, size INTEGER, mtime REAL, sha256 TEXT, doc_id INTEGER, status TEXT DEFAULT '', seen REAL);
CREATE TABLE IF NOT EXISTS source_rules(
  domain TEXT PRIMARY KEY, reliability INTEGER, blocked INTEGER NOT NULL DEFAULT 0, note TEXT DEFAULT '', updated REAL);
CREATE TABLE IF NOT EXISTS research(
  id INTEGER PRIMARY KEY AUTOINCREMENT, question TEXT NOT NULL, mode TEXT NOT NULL DEFAULT 'rapide',
  origin TEXT NOT NULL DEFAULT 'manuel', status TEXT NOT NULL DEFAULT 'en file', created REAL NOT NULL, started REAL,
  finished REAL, summary TEXT DEFAULT '', error TEXT DEFAULT '', log TEXT DEFAULT '[]', category_id INTEGER,
  gap_id INTEGER, watch_id INTEGER, queries TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS research_sources(
  id INTEGER PRIMARY KEY AUTOINCREMENT, research_id INTEGER NOT NULL, n INTEGER NOT NULL, provider TEXT NOT NULL,
  url TEXT NOT NULL, title TEXT NOT NULL, domain TEXT DEFAULT '', authors TEXT DEFAULT '', published TEXT DEFAULT '',
  license TEXT DEFAULT '', reliability INTEGER DEFAULT 2, kind TEXT DEFAULT '', excerpt TEXT DEFAULT '',
  relevance REAL DEFAULT 0, used INTEGER DEFAULT 0, created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_rsrc ON research_sources(research_id);
CREATE TABLE IF NOT EXISTS findings(
  id INTEGER PRIMARY KEY AUTOINCREMENT, research_id INTEGER NOT NULL, claim TEXT NOT NULL, status TEXT NOT NULL,
  confidence REAL NOT NULL, support TEXT NOT NULL DEFAULT '[]', contra TEXT NOT NULL DEFAULT '[]',
  decision TEXT NOT NULL DEFAULT 'en attente', doc_id INTEGER, created REAL NOT NULL, decided REAL,
  note TEXT DEFAULT '', origin TEXT DEFAULT 'modèle');
CREATE INDEX IF NOT EXISTS idx_find_r ON findings(research_id);
CREATE TABLE IF NOT EXISTS watch(
  id INTEGER PRIMARY KEY AUTOINCREMENT, topic TEXT NOT NULL UNIQUE, every_days REAL NOT NULL DEFAULT 7,
  last_run REAL, enabled INTEGER NOT NULL DEFAULT 1, created REAL NOT NULL);
"""

# colonnes ajoutées après la version 0.3 : les bases existantes sont mises à niveau sans rien perdre
MIGRATIONS = [
    ("documents", "archived", "INTEGER NOT NULL DEFAULT 0"),
    ("documents", "superseded_by", "INTEGER"),
    ("documents", "class_how", "TEXT DEFAULT ''"),
    ("documents", "vec_ext", "BLOB"),
    ("documents", "vec_model", "TEXT"),
    ("documents", "sig_l", "INTEGER"),
    ("documents", "title_key", "TEXT"),
    ("chunks", "sig_l", "INTEGER"),
    ("chunks", "sig_e", "INTEGER"),
]
GENERAL_TOPS = ("Connaissances générales", "Vie quotidienne")

EDITABLE = ("title", "description", "keywords", "license", "refs", "published", "source")


class Duplicate(Exception):
    def __init__(self, doc_id, title):
        super().__init__(f"Déjà présent dans la base : « {title} »")
        self.doc_id = doc_id


def stems(text):
    """Mots significatifs, pluriels simples retirés (aciers -> acier)."""
    return {w[:-1] if len(w) > 4 and w[-1] in "sx" else w for w in tokens(text)}


def fts_prefix_query(text, limit=14):
    words = list(dict.fromkeys(stems(text)))[:limit]
    return " OR ".join(f'"{w}"*' if len(w) >= 4 else f'"{w}"' for w in words)


def text_hash(segs):
    h = hashlib.sha256()
    for _, t in segs:
        h.update(re.sub(r"\s+", " ", t).strip().encode("utf-8"))
    return h.hexdigest()


class Knowledge:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.ann_local, self.ann_ext, self.ann_docs = vectors.SigIndex(), vectors.SigIndex(), vectors.SigIndex()
        self._open()

    def _open(self):
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        for table, col, decl in MIGRATIONS:
            have = {r[1] for r in self.db.execute(f"PRAGMA table_info({table})")}
            if col not in have:
                self.db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_doc_title_key ON documents(title_key)")
        rows = self.db.execute("SELECT id, title FROM documents WHERE title_key IS NULL").fetchall()
        self.db.executemany("UPDATE documents SET title_key=? WHERE id=?",
                            [(library.title_stem(r["title"]), r["id"]) for r in rows])
        for top, subs in TAXONOMY.items():  # catégories d'origine, complétées si une version en ajoute
            r = self.db.execute("SELECT id FROM categories WHERE name=? AND parent_id IS NULL", (top,)).fetchone()
            tid = r[0] if r else self.db.execute("INSERT INTO categories(name,parent_id) VALUES(?,NULL)", (top,)).lastrowid
            for sub in subs:
                if not self.db.execute("SELECT 1 FROM categories WHERE name=? AND parent_id=?", (sub, tid)).fetchone():
                    self.db.execute("INSERT INTO categories(name,parent_id) VALUES(?,?)", (sub, tid))
        # une indexation interrompue par un arrêt est signalée, pas relancée en silence
        self.db.execute("UPDATE jobs SET status='erreur', detail='interrompue par l''arrêt de Jarvis', finished=? "
                        "WHERE status IN ('en cours','en file')", (time.time(),))
        self.db.execute("UPDATE research SET status='erreur', error='interrompue par l''arrêt de Jarvis', finished=? "
                        "WHERE status IN ('en cours','en file')", (time.time(),))
        self.db.commit()

    def close(self):
        with self.lock:
            self.db.close()

    def reopen(self):
        with self.lock:
            self._open()
            for idx in (self.ann_local, self.ann_ext, self.ann_docs):
                idx.invalidate()

    def snapshot(self, dest):
        with self.lock:
            out = sqlite3.connect(str(dest))
            try:
                self.db.backup(out)
            finally:
                out.close()

    def _log(self, action, doc_id=None, title="", detail=""):
        self.db.execute("INSERT INTO journal(at,action,doc_id,title,detail) VALUES(?,?,?,?,?)",
                        (time.time(), action, doc_id, title, detail))

    # ---------- catégories ----------
    def categories(self):
        with self.lock:
            rows = self.db.execute(
                "SELECT c.id, c.name, c.parent_id, (SELECT COUNT(*) FROM documents d WHERE d.category_id=c.id) AS n "
                "FROM categories c ORDER BY c.parent_id IS NOT NULL, c.id").fetchall()
        return [dict(r) for r in rows]

    def category_path(self, cid):
        if not cid:
            return "Non classé"
        with self.lock:
            r = self.db.execute("SELECT c.name, p.name FROM categories c LEFT JOIN categories p ON p.id=c.parent_id "
                                "WHERE c.id=?", (cid,)).fetchone()
        return "Non classé" if not r else (f"{r[1]} › {r[0]}" if r[1] else r[0])

    def category_ids(self, cid):
        """La catégorie et ses sous-catégories."""
        with self.lock:
            return [cid] + [r[0] for r in self.db.execute("SELECT id FROM categories WHERE parent_id=?", (cid,))]

    def add_category(self, name, parent_id=None):
        name = name.strip()[:80]
        if not name:
            raise ValueError("Nom de catégorie vide")
        with self.lock:
            r = self.db.execute("SELECT id FROM categories WHERE name=? AND parent_id IS ?", (name, parent_id)).fetchone()
            if r:
                return r[0]
            cur = self.db.execute("INSERT INTO categories(name,parent_id) VALUES(?,?)", (name, parent_id))
            self._log("catégorie créée", detail=name)
            self.db.commit()
            return cur.lastrowid

    def find_category(self, label):
        """Retrouve une catégorie par son nom ou son chemin « Domaine › Sous-domaine »."""
        want = normalize(label.split("›")[-1].split(">")[-1].strip())
        for c in self.categories():
            if normalize(c["name"]) == want:
                return c["id"]
        return None

    # ---------- documents ----------
    def _doc(self, r):
        d = dict(r)
        for k in ("vec_local", "vec_ext", "sig_l"):
            d.pop(k, None)
        d["module"] = self.module_of(d.get("source_type"), d.get("category_id"))
        d["category"] = self.category_path(d.get("category_id"))
        return d

    def get(self, doc_id):
        with self.lock:
            r = self.db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        return self._doc(r) if r else None

    def chunks_of(self, doc_id):
        with self.lock:
            rows = self.db.execute("SELECT id, ord, locator, content FROM chunks WHERE doc_id=? ORDER BY ord",
                                   (doc_id,)).fetchall()
        return [dict(r) for r in rows]

    def module_of(self, source_type, category_id):
        """Module auquel appartient un document : recherche, generale ou bibliotheque."""
        if source_type == "recherche":
            return "recherche"
        if category_id:
            with self.lock:
                r = self.db.execute("SELECT c.name, p.name FROM categories c LEFT JOIN categories p ON p.id=c.parent_id "
                                    "WHERE c.id=?", (category_id,)).fetchone()
            if r and (r[1] or r[0]) in GENERAL_TOPS:
                return "generale"
        return "bibliotheque"

    def general_category_ids(self):
        with self.lock:
            return {r[0] for r in self.db.execute(
                "SELECT c.id FROM categories c LEFT JOIN categories p ON p.id=c.parent_id "
                f"WHERE COALESCE(p.name, c.name) IN ({','.join('?' * len(GENERAL_TOPS))})", GENERAL_TOPS)}

    def list(self, category_id=None, stale_before=None, dups=False, limit=500, module=None, state=None, q=None):
        sql, args = "SELECT * FROM documents WHERE 1=1", []
        if module == "recherche":
            sql += " AND source_type='recherche'"
        elif module in ("generale", "bibliotheque"):
            gen = sorted(self.general_category_ids())
            marks = ",".join("?" * len(gen)) or "NULL"
            sql += (f" AND source_type!='recherche' AND category_id IN ({marks})" if module == "generale"
                    else f" AND source_type!='recherche' AND (category_id IS NULL OR category_id NOT IN ({marks}))")
            args += gen
        if state == "archived":
            sql += " AND archived=1"
        elif state == "obsolete":
            sql += " AND superseded_by IS NOT NULL AND archived=0"
        elif state == "active":
            sql += " AND archived=0"
        if q:
            sql += " AND title LIKE ?"
            args.append(f"%{q}%")
        if category_id == 0:
            sql += " AND category_id IS NULL"
        elif category_id:
            ids = self.category_ids(category_id)
            sql += f" AND category_id IN ({','.join('?' * len(ids))})"
            args += ids
        if stale_before:
            sql += " AND verified < ?"
            args.append(stale_before)
        if dups:
            sql += " AND dup_of IS NOT NULL"
        with self.lock:
            rows = self.db.execute(sql + " ORDER BY updated DESC LIMIT ?", args + [limit]).fetchall()
        return [self._doc(r) for r in rows]

    def _insert_chunks(self, doc_id, title, segs, embed=None):
        n, chars, ord_ = 0, 0, 0
        for loc, text in segs:
            for ch in chunk_text(text, 1100):
                ext = embed(ch) if embed else None
                lv = local_vector(ch)
                cur = self.db.execute(
                    "INSERT INTO chunks(doc_id,ord,locator,content,vec_local,vec_ext,vec_model,sig_l,sig_e) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (doc_id, ord_, loc, ch, pack(lv), pack(ext[0]) if ext else None, ext[1] if ext else None,
                     vectors.signature(lv), vectors.signature(ext[0]) if ext else None))
                self.db.execute("INSERT INTO chunks_fts(content,title,chunk_id,doc_id) VALUES(?,?,?,?)",
                                (ch, title, cur.lastrowid, doc_id))
                ord_ += 1
                n += 1
                chars += len(ch)
        return n, chars

    def _delete_chunks(self, doc_id):
        self.db.execute("DELETE FROM chunks_fts WHERE doc_id=?", (doc_id,))
        self.db.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
        self.db.execute("DELETE FROM extracts WHERE doc_id=?", (doc_id,))

    def _insert_extracts(self, doc_id, segs):
        items = library.find_extracts(segs)[:400]
        self.db.executemany(
            "INSERT INTO extracts(doc_id,kind,label,content,locator,value,unit) VALUES(?,?,?,?,?,?,?)",
            [(doc_id, e["kind"], e["label"], e["content"], e["locator"], e["value"], e["unit"]) for e in items])
        counts = {}
        for e in items:
            counts[e["kind"]] = counts.get(e["kind"], 0) + 1
        return counts

    def _older_version(self, doc_id, title, published, dv):
        """Document existant qui semble être une autre version du même document -> (ancien, récent) ou None."""
        stem = library.title_stem(title)
        if len(stem) < 4:
            return None
        for r in self.db.execute("SELECT id, title, published, imported, vec_local FROM documents "
                                 "WHERE title_key=? AND id!=? AND archived=0 AND source_type!='recherche' LIMIT 50",
                                 (stem, doc_id)):
            if not r["vec_local"]:
                continue
            cos = cosine(dv, unpack(r["vec_local"]))
            if not 0.35 <= cos < 0.95:
                continue
            mine, other = library.version_rank(title, published), library.version_rank(r["title"], r["published"])
            return (doc_id, r["id"]) if mine < other else (r["id"], doc_id)
        return None

    def ingest(self, title, segs, source_type, source="", category_id=None, reliability=3, license="",
               description="", keywords="", published="", original="", embed=None):
        """Ajoute un document. Lève Duplicate si le même texte est déjà dans la base."""
        sha = text_hash(segs)
        now = time.time()
        full = "\n\n".join(t for _, t in segs)
        with self.lock:
            dup = self.db.execute("SELECT id, title FROM documents WHERE sha256=?", (sha,)).fetchone()
            if dup:
                raise Duplicate(dup["id"], dup["title"])
            dv = local_vector(full[:8000])
            dsig = vectors.signature(dv)
            near = None
            # quasi-doublon : on ne compare exactement qu'aux documents dont la signature est la plus proche
            self.ann_docs.ensure("chargé", self._doc_sigs)
            cand = self.ann_docs.nearest(dsig, 40)
            if cand:
                for r in self.db.execute(f"SELECT id, vec_local FROM documents WHERE id IN ({','.join('?' * len(cand))})", cand):
                    if r["vec_local"] and cosine(dv, unpack(r["vec_local"])) >= 0.95:
                        near = r["id"]
                        break
            cur = self.db.execute(
                "INSERT INTO documents(title,description,category_id,keywords,source_type,source,license,published,"
                "imported,verified,updated,reliability,sha256,original,dup_of,vec_local) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (title[:200], description, category_id, keywords, source_type, source, license, published,
                 now, now, now, max(1, min(5, int(reliability))), sha, original, near, pack(dv)))
            doc_id = cur.lastrowid
            n, chars = self._insert_chunks(doc_id, title, segs, embed)
            found = self._insert_extracts(doc_id, segs)
            dext = embed(f"{title}\n{full[:1500]}") if embed else None
            self.db.execute("UPDATE documents SET n_chunks=?, chars=?, sig_l=?, vec_ext=?, vec_model=?, title_key=? "
                            "WHERE id=?", (n, chars, dsig, pack(dext[0]) if dext else None,
                                           dext[1] if dext else None, library.title_stem(title), doc_id))
            self.ann_docs.add(doc_id, dsig)
            pair = None if near else self._older_version(doc_id, title, published, dv)
            if pair:
                self.db.execute("UPDATE documents SET superseded_by=? WHERE id=?", (pair[1], pair[0]))
            extra = ", ".join(f"{v} {k}{'x' if k == 'tableau' and v > 1 else 's' if v > 1 and k != 'tableau' else ''}"
                              for k, v in found.items())
            self._log("import", doc_id, title, f"{n} passages, source : {source or source_type}"
                      + (f" ; relevé : {extra}" if extra else "")
                      + (f" — ressemble fortement au document n° {near}" if near else "")
                      + (f" — autre version probable du document n° {pair[0] if pair[0] != doc_id else pair[1]}"
                         if pair else ""))
            self.db.commit()
        return doc_id

    def reingest(self, doc_id, segs, embed=None):
        """Actualisation : si le texte a changé, l'ancienne version est archivée et le document réindexé."""
        sha = text_hash(segs)
        now = time.time()
        with self.lock:
            d = self.db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
            if not d:
                raise ValueError("Document introuvable")
            if d["sha256"] == sha:
                self.db.execute("UPDATE documents SET verified=? WHERE id=?", (now, doc_id))
                self._log("vérifié", doc_id, d["title"], "contenu inchangé à la source")
                self.db.commit()
                return {"changed": False}
            old = "\n\n".join(r[0] for r in self.db.execute(
                "SELECT content FROM chunks WHERE doc_id=? ORDER BY ord", (doc_id,)))
            self.db.execute("INSERT INTO doc_versions(doc_id,version,sha256,saved,content) VALUES(?,?,?,?,?)",
                            (doc_id, d["version"], d["sha256"], now, zlib.compress(old.encode("utf-8"))))
            self._delete_chunks(doc_id)
            self.db.execute("DELETE FROM relations WHERE doc_id=?", (doc_id,))
            n, chars = self._insert_chunks(doc_id, d["title"], segs, embed)
            self._insert_extracts(doc_id, segs)
            full = "\n\n".join(t for _, t in segs)
            self.db.execute(
                "UPDATE documents SET sha256=?, n_chunks=?, chars=?, version=version+1, verified=?, updated=?, "
                "graph_done=0, vec_local=?, sig_l=? WHERE id=?",
                (sha, n, chars, now, now, pack(local_vector(full[:8000])),
                 vectors.signature(local_vector(full[:8000])), doc_id))
            self.ann_docs.invalidate()
            self._log("mis à jour", doc_id, d["title"], f"version {d['version'] + 1}, {n} passages")
            self.db.commit()
        return {"changed": True, "version": d["version"] + 1}

    def versions(self, doc_id):
        with self.lock:
            rows = self.db.execute("SELECT id, version, saved, LENGTH(content) AS bytes FROM doc_versions "
                                   "WHERE doc_id=? ORDER BY version DESC", (doc_id,)).fetchall()
        return [dict(r) for r in rows]

    def version_text(self, vid):
        with self.lock:
            r = self.db.execute("SELECT content FROM doc_versions WHERE id=?", (vid,)).fetchone()
        return zlib.decompress(r[0]).decode("utf-8") if r else None

    def update(self, doc_id, patch):
        d = self.get(doc_id)
        if not d:
            return None
        sets, args, changed = [], [], []
        for k in EDITABLE:
            if k in patch and str(patch[k]) != str(d[k]):
                sets.append(f"{k}=?")
                args.append(str(patch[k])[:2000])
                changed.append(k)
        if "reliability" in patch and int(patch["reliability"]) != d["reliability"]:
            sets.append("reliability=?")
            args.append(max(1, min(5, int(patch["reliability"]))))
            changed.append("fiabilité")
        if "category_id" in patch and patch["category_id"] != d["category_id"]:
            sets.append("category_id=?")
            args.append(patch["category_id"] or None)
            changed.append("catégorie")
        if patch.get("mark_verified"):
            sets.append("verified=?")
            args.append(time.time())
            changed.append("vérifié")
        if patch.get("not_duplicate"):
            sets.append("dup_of=NULL")
            changed.append("doublon écarté")
        if patch.get("not_obsolete"):
            sets.append("superseded_by=NULL")
            changed.append("considéré comme un document distinct")
        if "archived" in patch and bool(patch["archived"]) != bool(d["archived"]):
            sets.append("archived=?")
            args.append(1 if patch["archived"] else 0)
            changed.append("archivé (écarté des réponses)" if patch["archived"] else "remis en service")
        if "class_how" in patch:
            sets.append("class_how=?")
            args.append(str(patch["class_how"])[:40])
        if not sets:
            return d
        with self.lock:
            self.db.execute(f"UPDATE documents SET {', '.join(sets)}, updated=? WHERE id=?",
                            args + [time.time(), doc_id])
            if "title" in changed:
                self.db.execute("UPDATE chunks_fts SET title=? WHERE doc_id=?", (patch["title"], doc_id))
                self.db.execute("UPDATE documents SET title_key=? WHERE id=?", (library.title_stem(patch["title"]), doc_id))
            self._log("modifié", doc_id, patch.get("title", d["title"]), ", ".join(changed))
            self.db.commit()
        return self.get(doc_id)

    def delete(self, doc_id):
        d = self.get(doc_id)
        if not d:
            return None
        with self.lock:
            self._delete_chunks(doc_id)
            self.db.execute("DELETE FROM relations WHERE doc_id=?", (doc_id,))
            self.db.execute("DELETE FROM entities WHERE id NOT IN (SELECT src FROM relations UNION SELECT dst FROM relations)")
            self.db.execute("DELETE FROM doc_versions WHERE doc_id=?", (doc_id,))
            self.db.execute("UPDATE documents SET dup_of=NULL WHERE dup_of=?", (doc_id,))
            self.db.execute("UPDATE documents SET superseded_by=NULL WHERE superseded_by=?", (doc_id,))
            self.db.execute("DELETE FROM links WHERE a=? OR b=?", (f"d:{doc_id}", f"d:{doc_id}"))
            self.ann_docs.invalidate()
            self.db.execute("UPDATE watched_files SET doc_id=NULL, status='supprimé de la base' WHERE doc_id=?", (doc_id,))
            self.db.execute("DELETE FROM documents WHERE id=?", (doc_id,))
            self._log("supprimé", doc_id, d["title"])
            self.db.commit()
        return d

    def chunks_missing_ext(self, model, limit=100000):
        with self.lock:
            rows = self.db.execute("SELECT id, content FROM chunks WHERE vec_model IS NOT ? OR vec_ext IS NULL LIMIT ?",
                                   (model, limit)).fetchall()
        return [dict(r) for r in rows]

    def set_chunk_ext(self, cid, vec, model):
        with self.lock:
            self.db.execute("UPDATE chunks SET vec_ext=?, vec_model=?, sig_e=? WHERE id=?",
                            (pack(vec), model, vectors.signature(vec), cid))
            self.db.commit()

    def docs_missing_ext(self, model, limit=100000):
        with self.lock:
            rows = self.db.execute(
                "SELECT d.id, d.title, (SELECT content FROM chunks c WHERE c.doc_id=d.id AND c.ord=0) AS first "
                "FROM documents d WHERE d.vec_model IS NOT ? OR d.vec_ext IS NULL LIMIT ?", (model, limit)).fetchall()
        return [dict(r) for r in rows]

    def set_doc_ext(self, doc_id, vec, model):
        with self.lock:
            self.db.execute("UPDATE documents SET vec_ext=?, vec_model=? WHERE id=?", (pack(vec), model, doc_id))
            self.db.commit()

    def backfill_signatures(self, batch=2000):
        """Calcule les signatures manquantes (bases créées avant la version 0.4). -> nombre traité"""
        done = 0
        while True:
            with self.lock:
                rows = self.db.execute("SELECT id, vec_local, vec_ext FROM chunks WHERE sig_l IS NULL LIMIT ?",
                                       (batch,)).fetchall()
                if not rows:
                    break
                self.db.executemany("UPDATE chunks SET sig_l=?, sig_e=? WHERE id=?", [
                    (vectors.signature(unpack(r["vec_local"])) if r["vec_local"] else 0,
                     vectors.signature(unpack(r["vec_ext"])) if r["vec_ext"] else None, r["id"]) for r in rows])
                self.db.commit()
            done += len(rows)
        with self.lock:
            rows = self.db.execute("SELECT id, vec_local FROM documents WHERE sig_l IS NULL AND vec_local IS NOT NULL").fetchall()
            self.db.executemany("UPDATE documents SET sig_l=? WHERE id=?",
                                [(vectors.signature(unpack(r["vec_local"])), r["id"]) for r in rows])
            self.db.commit()
        self.ann_docs.invalidate()
        return done + len(rows)

    def backfill_extracts(self):
        """Relève formules, tableaux et données des documents importés avant la version 0.4. -> nombre de documents"""
        with self.lock:
            ids = [r[0] for r in self.db.execute(
                "SELECT id FROM documents WHERE id NOT IN (SELECT DISTINCT doc_id FROM extracts)")]
        n = 0
        for doc_id in ids:
            with self.lock:
                segs = [(r["locator"], r["content"]) for r in self.db.execute(
                    "SELECT locator, content FROM chunks WHERE doc_id=? ORDER BY ord", (doc_id,))]
                if self._insert_extracts(doc_id, segs):
                    n += 1
                self.db.commit()
        return n

    def _ann_candidates(self, ql, ext, n=300):
        """Passages candidats par voisinage approché (signatures), pour les grandes bases."""
        with self.lock:
            key = self.db.execute("SELECT COUNT(*), COALESCE(MAX(id),0), COUNT(sig_e) FROM chunks").fetchone()
            key = tuple(key)
        self.ann_local.ensure(key, lambda: self._sig_rows("sig_l"))
        out = self.ann_local.nearest(vectors.signature(ql), n)
        if ext:
            self.ann_ext.ensure(key + (ext[1],), lambda: self._sig_rows("sig_e", ext[1]))
            out += self.ann_ext.nearest(vectors.signature(ext[0]), n)
        return out

    def _sig_rows(self, col, model=None):
        with self.lock:
            if model:
                return self.db.execute(f"SELECT id, {col} FROM chunks WHERE {col} IS NOT NULL AND vec_model=?",
                                       (model,)).fetchall()
            return self.db.execute(f"SELECT id, {col} FROM chunks WHERE {col} IS NOT NULL").fetchall()

    # ---------- recherche hybride ----------
    def search(self, query, k=6, ext=None, category_id=None, per_doc=3, strict=True):
        """Plein texte + vecteurs, fusion des classements, puis reclassement.

        strict=True (utilisé pour répondre aux questions) écarte les passages sans rapport réel.
        """
        qs = stems(query)
        if not qs:
            return []
        cat_ids = set(self.category_ids(category_id)) if category_id else None
        with self.lock:
            total = self.db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            if not total:
                return []
            fts = [r[0] for r in self.db.execute(
                "SELECT chunk_id FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts, 1.0, 0.6) LIMIT 80",
                (fts_prefix_query(query),))]
        ql = local_vector(query)
        with self.lock:
            if total <= FULL_SCAN_LIMIT:
                rows = self.db.execute("SELECT id, vec_local, vec_ext, vec_model FROM chunks").fetchall()
            else:  # grande base : candidats plein texte + voisins approchés, cosinus exact sur ces seuls candidats
                cand = list(dict.fromkeys(fts + self._ann_candidates(ql, ext)))
                rows = [r for i in range(0, len(cand), 900) for r in self.db.execute(
                    f"SELECT id, vec_local, vec_ext, vec_model FROM chunks WHERE id IN "
                    f"({','.join('?' * len(cand[i:i + 900]))})", cand[i:i + 900])]
        sims = {}
        for r in rows:
            if ext and r["vec_ext"] and r["vec_model"] == ext[1]:
                sims[r["id"]] = (cosine(ext[0], unpack(r["vec_ext"])), True)
            elif r["vec_local"]:
                sims[r["id"]] = (sum(map(operator.mul, ql, unpack(r["vec_local"]))), False)
        vec_rank = [i for i, _ in sorted(sims.items(), key=lambda kv: kv[1][0], reverse=True)[:80]]
        fused = {}
        for rank, cid in enumerate(fts):
            fused[cid] = fused.get(cid, 0) + 1 / (60 + rank)
        for rank, cid in enumerate(vec_rank):
            fused[cid] = fused.get(cid, 0) + 1 / (60 + rank)
        if not fused:
            return []
        ids = sorted(fused, key=fused.get, reverse=True)[:60]
        with self.lock:
            data = self.db.execute(
                f"SELECT c.id, c.doc_id, c.ord, c.locator, c.content, d.title, d.source, d.source_type, d.reliability, "
                f"d.verified, d.category_id, d.license, d.published, d.superseded_by, d.description, c.vec_local "
                f"FROM chunks c JOIN documents d ON d.id=c.doc_id "
                f"WHERE d.archived=0 AND c.id IN ({','.join('?' * len(ids))})", ids).fetchall()
        out = []
        in_fts, in_vec = set(fts), set(vec_rank)
        general = self.general_category_ids()
        lvec = {}
        for r in data:
            if cat_ids and r["category_id"] not in cat_ids:
                continue
            cs = stems(r["content"] + " " + r["title"])
            hits = len(qs & cs)
            cover = hits / len(qs)
            cos, semantic = sims.get(r["id"], (0.0, False))
            relevant = (hits >= 2 and cover >= 0.34) or cover >= 0.6 or (semantic and cos >= 0.45)
            if strict and not relevant:
                continue
            if not strict and hits == 0 and cos < 0.2:
                continue
            d = dict(r)
            lvec[r["id"]] = unpack(d.pop("vec_local")) if d.get("vec_local") else None
            d.pop("vec_local", None)
            d["score"] = round(fused[r["id"]] * 30 + 0.5 * cover + 0.25 * max(cos, 0) + 0.03 * r["reliability"]
                               - (0.15 if r["superseded_by"] else 0), 4)
            d["category"] = self.category_path(r["category_id"])
            d["module"] = ("recherche" if r["source_type"] == "recherche"
                           else "generale" if r["category_id"] in general else "bibliotheque")
            # par quel chemin le passage a été trouvé : mots de la question, sens (vecteurs), ou les deux
            # « sens » n'est annoncé que si un modèle d'embeddings a servi ; sinon c'est une proximité de vocabulaire
            near = "sens" if semantic else "proximité"
            d["found_by"] = (f"mots et {near}" if r["id"] in in_fts and r["id"] in in_vec
                             else "mots" if r["id"] in in_fts else near)
            d["semantic"] = bool(semantic)
            out.append(d)
        out.sort(key=lambda d: d["score"], reverse=True)
        # reclassement : à pertinence proche, on préfère des passages qui n'en répètent pas un autre
        out = vectors.mmr(out[:max(k * 4, 24)], lambda d: lvec.get(d["id"]), lambda d: d["score"], max(k * 4, 24), 0.8)
        picked, per = [], {}
        for d in out:
            if per.get(d["doc_id"], 0) < per_doc:
                per[d["doc_id"]] = per.get(d["doc_id"], 0) + 1
                picked.append(d)
            if len(picked) >= k:
                break
        return picked

    # ---------- graphe ----------
    def _entity(self, name, type_="concept"):
        name = re.sub(r"\s+", " ", str(name)).strip(" .,;:«»\"'")[:80]
        norm = normalize(name)
        if len(norm) < 2:
            return None
        r = self.db.execute("SELECT id FROM entities WHERE norm=?", (norm,)).fetchone()
        if r:
            return r[0]
        return self.db.execute("INSERT INTO entities(name,norm,type) VALUES(?,?,?)", (name, norm, type_)).lastrowid

    def add_relations(self, triples, doc_id=None):
        """triples : [(source, relation, cible, type_source?, type_cible?)]"""
        n = 0
        with self.lock:
            for t in triples:
                a, b = self._entity(t[0], t[3] if len(t) > 3 else "concept"), \
                    self._entity(t[2], t[4] if len(t) > 4 else "concept")
                label = re.sub(r"\s+", " ", str(t[1])).strip()[:60]
                if not a or not b or a == b or not label:
                    continue
                cur = self.db.execute("INSERT OR IGNORE INTO relations(src,dst,label,doc_id) VALUES(?,?,?,?)",
                                      (a, b, label, doc_id))
                n += cur.rowcount
            if doc_id:
                self.db.execute("UPDATE documents SET graph_done=1 WHERE id=?", (doc_id,))
                if n:
                    self._log("graphe", doc_id, detail=f"{n} relations ajoutées")
            self.db.commit()
        return n

    def delete_relation(self, rid):
        with self.lock:
            self.db.execute("DELETE FROM relations WHERE id=?", (rid,))
            self.db.execute("DELETE FROM entities WHERE id NOT IN (SELECT src FROM relations UNION SELECT dst FROM relations)")
            self.db.commit()

    def related(self, query, limit=12):
        """Relations du graphe dont un concept est nommé dans la question."""
        nq = " " + " ".join(tokens(query, keep_stop=True)) + " "
        with self.lock:
            ents = self.db.execute("SELECT id, name, norm FROM entities").fetchall()
            found = [e["id"] for e in ents
                     if len(e["norm"]) >= 4 and (" " + " ".join(tokens(e["norm"], True)) + " ") in nq]
            if not found:
                return []
            marks = ",".join("?" * len(found))
            rows = self.db.execute(
                f"SELECT r.id, a.name AS src, r.label, b.name AS dst, r.doc_id, d.title FROM relations r "
                f"JOIN entities a ON a.id=r.src JOIN entities b ON b.id=r.dst LEFT JOIN documents d ON d.id=r.doc_id "
                f"WHERE r.src IN ({marks}) OR r.dst IN ({marks}) LIMIT ?", found + found + [limit]).fetchall()
        return [dict(r) for r in rows]

    def graph(self, center="", limit=140):
        with self.lock:
            if center.strip():
                like = f"%{normalize(center.strip())}%"
                seed = [r[0] for r in self.db.execute("SELECT id FROM entities WHERE norm LIKE ? LIMIT 8", (like,))]
                if not seed:
                    return {"nodes": [], "edges": []}
                marks = ",".join("?" * len(seed))
                hop = {r[0] for r in self.db.execute(
                    f"SELECT dst FROM relations WHERE src IN ({marks}) UNION SELECT src FROM relations WHERE dst IN ({marks})",
                    seed + seed)}
                keep = list(set(seed) | hop)[:limit]
            else:
                keep = [r[0] for r in self.db.execute(
                    "SELECT e.id FROM entities e ORDER BY (SELECT COUNT(*) FROM relations r WHERE r.src=e.id OR r.dst=e.id) "
                    "DESC LIMIT ?", (limit,))]
            if not keep:
                return {"nodes": [], "edges": []}
            marks = ",".join("?" * len(keep))
            nodes = [dict(r) for r in self.db.execute(f"SELECT id, name, type FROM entities WHERE id IN ({marks})", keep)]
            edges = [dict(r) for r in self.db.execute(
                f"SELECT r.id, r.src, r.dst, r.label, r.doc_id, d.title FROM relations r LEFT JOIN documents d "
                f"ON d.id=r.doc_id WHERE r.src IN ({marks}) AND r.dst IN ({marks})", keep + keep)]
        return {"nodes": nodes, "edges": edges}

    # ---------- file d'indexation, journal ----------
    def add_job(self, kind, label, payload=None):
        with self.lock:
            cur = self.db.execute("INSERT INTO jobs(kind,label,payload,created) VALUES(?,?,?,?)",
                                  (kind, label[:200], json.dumps(payload or {}, ensure_ascii=False), time.time()))
            self.db.commit()
            return cur.lastrowid

    def job_update(self, jid, status, detail=""):
        with self.lock:
            fin = time.time() if status in ("terminé", "erreur", "doublon") else None
            self.db.execute("UPDATE jobs SET status=?, detail=?, finished=? WHERE id=?", (status, detail[:600], fin, jid))
            self.db.commit()

    def jobs(self, limit=40):
        with self.lock:
            rows = self.db.execute("SELECT id, kind, label, status, detail, created, finished FROM jobs "
                                   "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def clear_jobs(self):
        with self.lock:
            self.db.execute("DELETE FROM jobs WHERE status NOT IN ('en file','en cours')")
            self.db.commit()

    def journal(self, limit=80):
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM journal ORDER BY id DESC LIMIT ?", (limit,))]

    # ---------- questions sans source, propositions ----------
    def note_gap(self, question):
        q = re.sub(r"\s+", " ", question).strip()[:300]
        norm = " ".join(sorted(stems(q)))
        if len(norm) < 4:
            return
        now = time.time()
        with self.lock:
            self.db.execute(
                "INSERT INTO gaps(question,norm,created,last) VALUES(?,?,?,?) "
                "ON CONFLICT(norm) DO UPDATE SET count=count+1, last=excluded.last, "
                "status=CASE WHEN status='ignoré' THEN 'ignoré' ELSE 'ouvert' END", (q, norm, now, now))
            self.db.commit()

    def gaps(self, status="ouvert"):
        with self.lock:
            return [dict(r) for r in self.db.execute(
                "SELECT * FROM gaps WHERE status=? ORDER BY count DESC, last DESC LIMIT 100", (status,))]

    def set_gap(self, gid, status):
        with self.lock:
            self.db.execute("UPDATE gaps SET status=? WHERE id=?", (status, gid))
            self.db.commit()

    def add_proposal(self, kind, title, url, snippet="", reason="", lang="fr", category_id=None):
        with self.lock:
            if self.db.execute("SELECT 1 FROM documents WHERE source=?", (url,)).fetchone():
                return None
            cur = self.db.execute(
                "INSERT OR IGNORE INTO proposals(kind,title,lang,snippet,url,reason,category_id,created) "
                "VALUES(?,?,?,?,?,?,?,?)", (kind, title, lang, snippet[:400], url, reason[:200], category_id, time.time()))
            self.db.commit()
            return cur.lastrowid if cur.rowcount else None

    def proposals(self, status="en attente"):
        with self.lock:
            return [dict(r) for r in self.db.execute(
                "SELECT * FROM proposals WHERE status=? ORDER BY id DESC LIMIT 200", (status,))]

    def get_proposal(self, pid):
        with self.lock:
            r = self.db.execute("SELECT * FROM proposals WHERE id=?", (pid,)).fetchone()
        return dict(r) if r else None

    def set_proposal(self, pid, status):
        with self.lock:
            self.db.execute("UPDATE proposals SET status=? WHERE id=?", (status, pid))
            self.db.commit()

    # ---------- statistiques ----------
    def stats(self, stale_days=180):
        with self.lock:
            g = lambda q, a=(): self.db.execute(q, a).fetchone()[0]
            return {
                "documents": g("SELECT COUNT(*) FROM documents"),
                "chunks": g("SELECT COUNT(*) FROM chunks"),
                "chunks_semantic": g("SELECT COUNT(*) FROM chunks WHERE vec_ext IS NOT NULL"),
                "chars": g("SELECT COALESCE(SUM(chars),0) FROM documents"),
                "entities": g("SELECT COUNT(*) FROM entities"),
                "relations": g("SELECT COUNT(*) FROM relations"),
                "duplicates": g("SELECT COUNT(*) FROM documents WHERE dup_of IS NOT NULL"),
                "obsolete": g("SELECT COUNT(*) FROM documents WHERE superseded_by IS NOT NULL AND archived=0"),
                "archived": g("SELECT COUNT(*) FROM documents WHERE archived=1"),
                "links": g("SELECT COUNT(*) FROM links"),
                "extracts": {r[0]: r[1] for r in self.db.execute("SELECT kind, COUNT(*) FROM extracts GROUP BY kind")},
                "categories_used": g("SELECT COUNT(DISTINCT category_id) FROM documents WHERE category_id IS NOT NULL"),
                "categories": g("SELECT COUNT(*) FROM categories"),
                "job_errors": g("SELECT COUNT(*) FROM jobs WHERE status='erreur'"),
                "ann_active": g("SELECT COUNT(*) FROM chunks") > FULL_SCAN_LIMIT,
                "stale": g("SELECT COUNT(*) FROM documents WHERE verified < ?", (time.time() - stale_days * 86400,)),
                "unclassified": g("SELECT COUNT(*) FROM documents WHERE category_id IS NULL"),
                "gaps": g("SELECT COUNT(*) FROM gaps WHERE status='ouvert'"),
                "proposals": g("SELECT COUNT(*) FROM proposals WHERE status='en attente'"),
                "jobs_active": g("SELECT COUNT(*) FROM jobs WHERE status IN ('en file','en cours')"),
                "by_source": {r[0]: r[1] for r in self.db.execute(
                    "SELECT source_type, COUNT(*) FROM documents GROUP BY source_type")},
                "db_bytes": self.path.stat().st_size if self.path.exists() else 0,
            }

    # ---------- formules, tableaux, données ----------
    def extracts_of(self, doc_id):
        with self.lock:
            return [dict(r) for r in self.db.execute(
                "SELECT id, kind, label, content, locator, value, unit FROM extracts WHERE doc_id=? ORDER BY id", (doc_id,))]

    def search_extracts(self, query="", kind=None, limit=80):
        sql = ("SELECT e.id, e.doc_id, e.kind, e.label, e.content, e.locator, d.title FROM extracts e "
               "JOIN documents d ON d.id=e.doc_id WHERE d.archived=0")
        args = []
        if kind:
            sql += " AND e.kind=?"
            args.append(kind)
        for w in list(dict.fromkeys(re.findall(r"\w{2,}", query)))[:6]:
            sql += " AND (e.content LIKE ? OR e.label LIKE ? OR d.title LIKE ?)"
            args += [f"%{w}%"] * 3
        with self.lock:
            return [dict(r) for r in self.db.execute(sql + " ORDER BY e.id DESC LIMIT ?", args + [limit])]

    def data_conflicts(self, limit=40):
        """Données chiffrées en désaccord entre deux documents (même grandeur, même unité, écart > 10 %)."""
        with self.lock:
            rows = [dict(r) for r in self.db.execute(
                "SELECT e.id, e.doc_id, e.label, e.content, e.value, e.unit, e.locator, d.title, d.reliability "
                "FROM extracts e JOIN documents d ON d.id=e.doc_id WHERE e.kind='donnée' AND d.archived=0 LIMIT 20000")]
        out = []
        for a, b in library.conflicts(rows):
            if a["doc_id"] != b["doc_id"]:
                out.append({"a": a, "b": b})
            if len(out) >= limit:
                break
        return out

    # ---------- connexions entre éléments (documents d:, souvenirs m:, recherches r:) ----------
    def add_links(self, pairs, kind="sémantique"):
        """pairs : [(a, b, score)] -> nombre de connexions nouvelles."""
        n, now = 0, time.time()
        with self.lock:
            for a, b, score in pairs:
                if a == b:
                    continue
                a, b = (a, b) if a < b else (b, a)
                cur = self.db.execute("INSERT OR IGNORE INTO links(a,b,kind,score,created) VALUES(?,?,?,?,?)",
                                      (a, b, kind, round(float(score), 4), now))
                n += cur.rowcount
            self.db.commit()
        return n

    def links_of(self, node):
        with self.lock:
            return [dict(r) for r in self.db.execute(
                "SELECT id, a, b, kind, score FROM links WHERE a=? OR b=? ORDER BY score DESC", (node, node))]

    def all_links(self):
        with self.lock:
            return self.db.execute("SELECT a, b, kind, score FROM links").fetchall()

    def drop_links(self, node=None, kind=None):
        with self.lock:
            if node:
                self.db.execute("DELETE FROM links WHERE a=? OR b=?", (node, node))
            elif kind:
                self.db.execute("DELETE FROM links WHERE kind=?", (kind,))
            self.db.commit()

    def doc_vectors(self, exclude=None, near=None, limit=2000):
        """(id, titre, vecteur local, vecteur sémantique, modèle) des documents actifs.

        near : vecteur local de référence. Si la base dépasse `limit` documents, seuls les plus proches
        (voisinage approché par signatures) sont renvoyés, pour ne pas tout comparer à chaque import.
        """
        with self.lock:
            total = self.db.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            if near is None or total <= limit:
                return self.db.execute("SELECT id, title, vec_local, vec_ext, vec_model FROM documents "
                                       "WHERE archived=0 AND id IS NOT ?", (exclude,)).fetchall()
        with self.lock:   # même ordre de verrouillage que l'import : base, puis index
            self.ann_docs.ensure("chargé", self._doc_sigs)
        ids = self.ann_docs.nearest(vectors.signature(near), 300)
        with self.lock:
            return self.db.execute(
                f"SELECT id, title, vec_local, vec_ext, vec_model FROM documents WHERE archived=0 AND id IS NOT ? "
                f"AND id IN ({','.join('?' * len(ids))})", [exclude] + ids).fetchall()

    def _doc_sigs(self):
        with self.lock:
            return self.db.execute("SELECT id, sig_l FROM documents WHERE sig_l IS NOT NULL").fetchall()

    # ---------- sources ----------
    def source_rules(self):
        with self.lock:
            return {r["domain"]: dict(r) for r in self.db.execute("SELECT * FROM source_rules")}

    def set_source_rule(self, domain, reliability=None, blocked=None, note=None):
        domain = domain.strip().lower().lstrip(".")[:120]
        if not re.fullmatch(r"[a-z0-9.\-]+\.[a-z]{2,}", domain):
            raise ValueError("Nom de domaine invalide (exemple : freecad.org)")
        with self.lock:
            cur = self.db.execute("SELECT * FROM source_rules WHERE domain=?", (domain,)).fetchone()
            rel = cur["reliability"] if cur else None
            blk = cur["blocked"] if cur else 0
            nt = cur["note"] if cur else ""
            if reliability is not None:
                rel = max(1, min(5, int(reliability))) if reliability else None
            if blocked is not None:
                blk = 1 if blocked else 0
            if note is not None:
                nt = str(note)[:200]
            self.db.execute("INSERT INTO source_rules(domain,reliability,blocked,note,updated) VALUES(?,?,?,?,?) "
                            "ON CONFLICT(domain) DO UPDATE SET reliability=excluded.reliability, "
                            "blocked=excluded.blocked, note=excluded.note, updated=excluded.updated",
                            (domain, rel, blk, nt, time.time()))
            self._log("source", detail=f"{domain} : fiabilité {rel or 'automatique'}{', bloquée' if blk else ''}")
            self.db.commit()

    def delete_source_rule(self, domain):
        with self.lock:
            self.db.execute("DELETE FROM source_rules WHERE domain=?", (domain,))
            self.db.commit()

    def retry_info(self, jid):
        with self.lock:
            r = self.db.execute("SELECT kind, label, payload, status FROM jobs WHERE id=?", (jid,)).fetchone()
        return dict(r) if r else None
