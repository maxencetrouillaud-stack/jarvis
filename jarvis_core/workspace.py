"""Espace de travail commun : projets, ressources (images, fichiers, sources, téléchargements), journal des agents
et bus d'événements partagés par le navigateur, les artefacts, le générateur d'images et les missions.

Tout est rangé dans la base existante (jarvis.db) : un seul système de projets, une seule sauvegarde.
Les fichiers vont dans <données>/files/<type>/ ; ils sont dédupliqués par empreinte SHA-256.
"""
import hashlib
import hmac
import json
import mimetypes
import re
import threading
import time
from collections import deque
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE COLLATE NOCASE, objective TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'actif', notes TEXT NOT NULL DEFAULT '', created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS resources(
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, title TEXT NOT NULL, project_id INTEGER, conv_id TEXT,
  mission_id INTEGER, path TEXT, url TEXT, mime TEXT, sha TEXT, bytes INTEGER NOT NULL DEFAULT 0, meta TEXT,
  saved INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL, deleted REAL);
CREATE INDEX IF NOT EXISTS idx_res_kind ON resources(kind, deleted, id);
CREATE INDEX IF NOT EXISTS idx_res_project ON resources(project_id, kind);
CREATE TABLE IF NOT EXISTS artifacts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, kind TEXT NOT NULL, project_id INTEGER, conv_id TEXT,
  mission_id INTEGER, version INTEGER NOT NULL DEFAULT 1, created REAL NOT NULL, updated REAL NOT NULL, deleted REAL);
CREATE TABLE IF NOT EXISTS artifact_versions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, artifact_id INTEGER NOT NULL, n INTEGER NOT NULL, content TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '', author TEXT NOT NULL DEFAULT 'jarvis', created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_artver ON artifact_versions(artifact_id, n);
CREATE TABLE IF NOT EXISTS missions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, goal TEXT NOT NULL, status TEXT NOT NULL, project_id INTEGER, conv_id TEXT,
  plan TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL, finished REAL, error TEXT, message_id INTEGER,
  answer TEXT);
CREATE TABLE IF NOT EXISTS agent_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, mission_id INTEGER, agent TEXT NOT NULL, tool TEXT,
  action TEXT NOT NULL, detail TEXT, ok INTEGER NOT NULL DEFAULT 1);
CREATE INDEX IF NOT EXISTS idx_agentlog ON agent_log(mission_id, id);
CREATE TABLE IF NOT EXISTS browser_history(
  id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, url TEXT NOT NULL, title TEXT, agent INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS browser_favorites(
  id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT NOT NULL UNIQUE, title TEXT, created REAL NOT NULL);
"""

STATUSES = ("actif", "en pause", "terminé", "archivé")
KINDS = ("image", "fichier", "source", "telechargement")
EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif", "application/pdf": ".pdf",
       "text/html": ".html", "text/plain": ".txt", "application/zip": ".zip"}


def _now():
    return time.time()


class EventBus:
    """Événements partagés entre modules (missions, navigateur, images, artefacts, agents).

    Les abonnés lisent la suite d'événements depuis un numéro donné : aucun abonné lent ne bloque un module."""

    def __init__(self, keep=600):
        self._cond = threading.Condition()
        self._events = deque(maxlen=keep)
        self._seq = 0
        self._hooks = []

    def publish(self, topic, **data):
        with self._cond:
            self._seq += 1
            ev = {"id": self._seq, "at": _now(), "topic": topic, **data}
            self._events.append(ev)
            self._cond.notify_all()
            hooks = list(self._hooks)
        for h in hooks:
            try:
                h(ev)
            except Exception:
                pass
        return ev

    def subscribe(self, fn):
        """Rappel synchrone appelé pour chaque événement (doit rester très court)."""
        with self._cond:
            self._hooks.append(fn)

    def unsubscribe(self, fn):
        with self._cond:
            if fn in self._hooks:
                self._hooks.remove(fn)

    @property
    def last_id(self):
        with self._cond:
            return self._seq

    def since(self, last_id, timeout=15.0, topic_prefix=None):
        """Événements de numéro > last_id ; attend au plus `timeout` secondes s'il n'y en a pas."""
        end = _now() + timeout
        with self._cond:
            while True:
                out = [e for e in self._events if e["id"] > last_id
                       and (not topic_prefix or e["topic"].startswith(topic_prefix))]
                if out:
                    return out
                left = end - _now()
                if left <= 0:
                    return []
                self._cond.wait(left)


class Workspace:
    def __init__(self, store, home: Path, bus: EventBus = None, secret: str = ""):
        self.store = store
        self.home = Path(home)
        self.files = self.home / "files"
        self.files.mkdir(parents=True, exist_ok=True)
        self.bus = bus or EventBus()
        self.secret = (secret or hashlib.sha256(str(self.home).encode()).hexdigest()).encode()
        self._conn = None

    # ------------------------------------------------------------------ base
    @property
    def db(self):
        db = self.store.db
        if db is not self._conn:   # première utilisation, ou base rouverte après une restauration
            db.executescript(SCHEMA)
            cols = {r[1] for r in db.execute("PRAGMA table_info(conversations)")}
            if "project_id" not in cols:
                db.execute("ALTER TABLE conversations ADD COLUMN project_id INTEGER")
            db.commit()
            self._conn = db
        return db

    def _q(self, sql, args=(), one=False):
        with self.store.lock:
            cur = self.db.execute(sql, args)
            rows = cur.fetchall()
        rows = [dict(r) for r in rows]
        return (rows[0] if rows else None) if one else rows

    def _x(self, sql, args=()):
        with self.store.lock:
            cur = self.db.execute(sql, args)
            self.db.commit()
            return cur.lastrowid

    # ------------------------------------------------------------------ média signé
    def sign(self, what: str) -> str:
        return hmac.new(self.secret, what.encode("utf-8"), hashlib.sha256).hexdigest()[:32]

    def check(self, what: str, sig: str) -> bool:
        return bool(sig) and hmac.compare_digest(self.sign(what), sig)

    def media_url(self, rid: int) -> str:
        return f"/media/r/{rid}?s={self.sign(f'r{rid}')}"

    # ------------------------------------------------------------------ journal des agents
    def log(self, agent, action, detail="", ok=True, tool=None, mission_id=None):
        lid = self._x("INSERT INTO agent_log(at,mission_id,agent,tool,action,detail,ok) VALUES(?,?,?,?,?,?,?)",
                      (_now(), mission_id, agent, tool, action[:300], (detail or "")[:2000], int(bool(ok))))
        self.bus.publish("agent", log_id=lid, mission_id=mission_id, agent=agent, tool=tool, action=action[:300],
                         detail=(detail or "")[:400], ok=bool(ok))
        return lid

    def agent_log(self, mission_id=None, limit=100):
        if mission_id:
            return self._q("SELECT * FROM agent_log WHERE mission_id=? ORDER BY id DESC LIMIT ?", (mission_id, limit))
        return self._q("SELECT * FROM agent_log ORDER BY id DESC LIMIT ?", (limit,))

    # ------------------------------------------------------------------ projets
    def _project_row(self, r):
        if not r:
            return None
        pid = r["id"]
        with self.store.lock:
            c = self.db.execute
            r["counts"] = {
                "conversations": c("SELECT COUNT(*) FROM conversations WHERE project_id=?", (pid,)).fetchone()[0],
                "artifacts": c("SELECT COUNT(*) FROM artifacts WHERE project_id=? AND deleted IS NULL", (pid,)).fetchone()[0],
                "images": c("SELECT COUNT(*) FROM resources WHERE project_id=? AND kind='image' AND deleted IS NULL",
                            (pid,)).fetchone()[0],
                "sources": c("SELECT COUNT(*) FROM resources WHERE project_id=? AND kind='source' AND deleted IS NULL",
                             (pid,)).fetchone()[0],
                "files": c("SELECT COUNT(*) FROM resources WHERE project_id=? AND kind IN ('fichier','telechargement') "
                           "AND deleted IS NULL", (pid,)).fetchone()[0],
                "missions": c("SELECT COUNT(*) FROM missions WHERE project_id=?", (pid,)).fetchone()[0],
                "memories": c("SELECT COUNT(*) FROM memories WHERE project=? COLLATE NOCASE", (r["name"],)).fetchone()[0],
            }
            done = c("SELECT COUNT(*) FROM missions WHERE project_id=? AND status='terminée'", (pid,)).fetchone()[0]
        total = r["counts"]["missions"]
        r["progress"] = round(100 * done / total) if total else None
        return r

    def sync_projects_from_memory(self):
        """Les projets nommés dans la mémoire personnelle (champ « projet » des souvenirs) deviennent des projets."""
        names = [r["project"] for r in self._q(
            "SELECT DISTINCT project FROM memories WHERE project IS NOT NULL AND TRIM(project)<>''")]
        for n in names:
            self.ensure_project(n)

    def ensure_project(self, name, objective=""):
        name = re.sub(r"\s+", " ", str(name or "")).strip()[:80]
        if not name:
            return None
        r = self._q("SELECT id FROM projects WHERE name=? COLLATE NOCASE", (name,), one=True)
        if r:
            return r["id"]
        return self._x("INSERT INTO projects(name,objective,created,updated) VALUES(?,?,?,?)",
                       (name, objective[:2000], _now(), _now()))

    def create_project(self, name, objective=""):
        name = re.sub(r"\s+", " ", str(name or "")).strip()[:80]
        if not name:
            raise ValueError("Donnez un nom au projet.")
        if self._q("SELECT id FROM projects WHERE name=? COLLATE NOCASE", (name,), one=True):
            raise ValueError("Un projet porte déjà ce nom.")
        pid = self.ensure_project(name, objective)
        self.bus.publish("project", project_id=pid, action="créé")
        return self.project(pid)

    def projects(self, include_archived=False):
        self.sync_projects_from_memory()
        rows = self._q("SELECT * FROM projects " + ("" if include_archived else "WHERE status<>'archivé' ")
                       + "ORDER BY updated DESC")
        return [self._project_row(r) for r in rows]

    def project(self, pid):
        return self._project_row(self._q("SELECT * FROM projects WHERE id=?", (pid,), one=True))

    def update_project(self, pid, patch):
        p = self.project(pid)
        if not p:
            raise KeyError("Projet introuvable")
        fields = {}
        if "name" in patch:
            name = re.sub(r"\s+", " ", str(patch["name"] or "")).strip()[:80]
            if not name:
                raise ValueError("Le nom du projet ne peut pas être vide.")
            other = self._q("SELECT id FROM projects WHERE name=? COLLATE NOCASE AND id<>?", (name, pid), one=True)
            if other:
                raise ValueError("Un projet porte déjà ce nom.")
            fields["name"] = name
        if "objective" in patch:
            fields["objective"] = str(patch["objective"] or "")[:4000]
        if "notes" in patch:
            fields["notes"] = str(patch["notes"] or "")[:20000]
        if "status" in patch:
            if patch["status"] not in STATUSES:
                raise ValueError("État de projet inconnu")
            fields["status"] = patch["status"]
        if not fields:
            return p
        sets = ", ".join(f"{k}=?" for k in fields)
        self._x(f"UPDATE projects SET {sets}, updated=? WHERE id=?", (*fields.values(), _now(), pid))
        if "name" in fields and fields["name"] != p["name"]:   # les souvenirs suivent le nouveau nom
            self._x("UPDATE memories SET project=? WHERE project=? COLLATE NOCASE", (fields["name"], p["name"]))
        self.bus.publish("project", project_id=pid, action="modifié")
        return self.project(pid)

    def touch_project(self, pid):
        if pid:
            self._x("UPDATE projects SET updated=? WHERE id=?", (_now(), pid))

    def set_conversation_project(self, cid, pid):
        if pid is not None and not self.project(pid):
            raise KeyError("Projet introuvable")
        self._x("UPDATE conversations SET project_id=? WHERE id=?", (pid, cid))
        self.touch_project(pid)

    def conversation_project(self, cid):
        r = self._q("SELECT project_id FROM conversations WHERE id=?", (cid,), one=True)
        return r["project_id"] if r else None

    def project_detail(self, pid):
        p = self.project(pid)
        if not p:
            return None
        p["conversations"] = self._q(
            "SELECT id, title, updated FROM conversations WHERE project_id=? ORDER BY updated DESC", (pid,))
        p["artifacts"] = self._q("SELECT id, title, kind, version, updated FROM artifacts WHERE project_id=? AND "
                                 "deleted IS NULL ORDER BY updated DESC", (pid,))
        p["images"] = [self.public(r) for r in self._q(
            "SELECT * FROM resources WHERE project_id=? AND kind='image' AND deleted IS NULL ORDER BY id DESC LIMIT 60",
            (pid,))]
        p["sources"] = [self.public(r) for r in self._q(
            "SELECT * FROM resources WHERE project_id=? AND kind='source' AND deleted IS NULL ORDER BY id DESC LIMIT 100",
            (pid,))]
        p["files"] = [self.public(r) for r in self._q(
            "SELECT * FROM resources WHERE project_id=? AND kind IN ('fichier','telechargement') AND deleted IS NULL "
            "ORDER BY id DESC LIMIT 100", (pid,))]
        p["memories"] = self._q("SELECT id, content, category, nature FROM memories WHERE project=? COLLATE NOCASE "
                                "ORDER BY importance DESC, id DESC LIMIT 50", (p["name"],))
        return p

    # ------------------------------------------------------------------ ressources
    def public(self, r):
        if not r:
            return None
        r = dict(r)
        try:
            r["meta"] = json.loads(r.get("meta") or "{}")
        except ValueError:
            r["meta"] = {}
        if r.get("path"):
            r["src"] = self.media_url(r["id"])
        r.pop("path", None)
        return r

    def _store_file(self, kind, data: bytes, mime, name_hint=""):
        sha = hashlib.sha256(data).hexdigest()
        ext = EXT.get(mime) or Path(name_hint).suffix.lower()[:10] or mimetypes.guess_extension(mime or "") or ".bin"
        if not re.fullmatch(r"\.[a-z0-9]{1,9}", ext):
            ext = ".bin"
        rel = Path(kind) / f"{sha[:24]}{ext}"
        dest = self.files / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            tmp = dest.with_suffix(dest.suffix + ".part")
            tmp.write_bytes(data)
            tmp.replace(dest)
        return rel.as_posix(), sha

    def add_file(self, kind, data: bytes, mime, title, project_id=None, conv_id=None, mission_id=None, meta=None,
                 saved=False, name_hint="", dedupe=True):
        if kind not in KINDS:
            raise ValueError("Type de ressource inconnu")
        rel, sha = self._store_file(kind if kind != "telechargement" else "telechargements", data, mime, name_hint)
        if dedupe:
            ex = self._q("SELECT * FROM resources WHERE kind=? AND sha=? AND deleted IS NULL AND "
                         "COALESCE(project_id,0)=COALESCE(?,0) ORDER BY id LIMIT 1", (kind, sha, project_id), one=True)
            if ex:
                if saved and not ex["saved"]:
                    self._x("UPDATE resources SET saved=1 WHERE id=?", (ex["id"],))
                return self.resource(ex["id"])
        rid = self._x(
            "INSERT INTO resources(kind,title,project_id,conv_id,mission_id,path,mime,sha,bytes,meta,saved,created) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (kind, (title or "sans titre")[:300], project_id, conv_id, mission_id, rel, mime, sha, len(data),
             json.dumps(meta or {}, ensure_ascii=False), int(bool(saved)), _now()))
        self.touch_project(project_id)
        self.bus.publish("resource", resource_id=rid, kind=kind, project_id=project_id, mission_id=mission_id)
        return self.resource(rid)

    def add_source(self, url, title, excerpt="", project_id=None, conv_id=None, mission_id=None, meta=None):
        """Source consultée (page web, document). Une même adresse n'est enregistrée qu'une fois par projet."""
        ex = self._q("SELECT * FROM resources WHERE kind='source' AND url=? AND deleted IS NULL AND "
                     "COALESCE(project_id,0)=COALESCE(?,0) LIMIT 1", (url, project_id), one=True)
        m = dict(meta or {})
        if excerpt:
            m["excerpt"] = excerpt[:1500]
        if ex:
            old = json.loads(ex.get("meta") or "{}")
            old.update(m)
            self._x("UPDATE resources SET title=?, meta=?, mission_id=COALESCE(?, mission_id) WHERE id=?",
                    (title[:300] or ex["title"], json.dumps(old, ensure_ascii=False), mission_id, ex["id"]))
            return self.resource(ex["id"])
        rid = self._x("INSERT INTO resources(kind,title,project_id,conv_id,mission_id,url,meta,saved,created) "
                      "VALUES('source',?,?,?,?,?,?,1,?)",
                      ((title or url)[:300], project_id, conv_id, mission_id, url, json.dumps(m, ensure_ascii=False),
                       _now()))
        self.touch_project(project_id)
        self.bus.publish("resource", resource_id=rid, kind="source", project_id=project_id, mission_id=mission_id)
        return self.resource(rid)

    def resource(self, rid):
        return self.public(self._q("SELECT * FROM resources WHERE id=? AND deleted IS NULL", (rid,), one=True))

    def resource_path(self, rid):
        r = self._q("SELECT path, mime FROM resources WHERE id=? AND deleted IS NULL", (rid,), one=True)
        if not r or not r["path"]:
            return None, None
        p = (self.files / r["path"]).resolve()
        if self.files.resolve() not in p.parents or not p.exists():
            return None, None
        return p, r["mime"]

    def resource_bytes(self, rid):
        p, mime = self.resource_path(rid)
        return (p.read_bytes(), mime) if p else (None, None)

    def resources(self, kind=None, project_id=None, saved=None, mission_id=None, conv_id=None, limit=200, q=None):
        where, args = ["deleted IS NULL"], []
        if kind:
            kinds = kind if isinstance(kind, (list, tuple)) else [kind]
            where.append(f"kind IN ({','.join('?' * len(kinds))})")
            args += kinds
        for col, val in (("project_id", project_id), ("mission_id", mission_id), ("conv_id", conv_id)):
            if val is not None:
                where.append(f"{col}=?")
                args.append(val)
        if saved is not None:
            where.append("saved=?")
            args.append(int(bool(saved)))
        if q:
            where.append("(title LIKE ? OR meta LIKE ? OR url LIKE ?)")
            args += [f"%{q}%"] * 3
        rows = self._q(f"SELECT * FROM resources WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT ?", (*args, limit))
        return [self.public(r) for r in rows]

    def update_resource(self, rid, patch):
        r = self.resource(rid)
        if not r:
            raise KeyError("Ressource introuvable")
        if "saved" in patch:
            self._x("UPDATE resources SET saved=? WHERE id=?", (int(bool(patch["saved"])), rid))
        if "title" in patch and str(patch["title"]).strip():
            self._x("UPDATE resources SET title=? WHERE id=?", (str(patch["title"]).strip()[:300], rid))
        if "project_id" in patch:
            pid = patch["project_id"]
            if pid is not None and not self.project(pid):
                raise KeyError("Projet introuvable")
            self._x("UPDATE resources SET project_id=? WHERE id=?", (pid, rid))
            self.touch_project(pid)
        self.bus.publish("resource", resource_id=rid, kind=r["kind"], action="modifiée")
        return self.resource(rid)

    def referenced_images(self):
        """Images utilisées par au moins un artefact (référence jarvis-image:N dans une version)."""
        ids = set()
        for row in self._q("SELECT content FROM artifact_versions"):
            ids.update(int(x) for x in re.findall(r"jarvis-image:(\d+)", row["content"]))
        return ids

    def delete_resource(self, rid):
        """Suppression récupérable : la ligne est marquée, le fichier reste tant qu'une autre ligne l'utilise."""
        r = self._q("SELECT * FROM resources WHERE id=? AND deleted IS NULL", (rid,), one=True)
        if not r:
            raise KeyError("Ressource introuvable")
        self._x("UPDATE resources SET deleted=? WHERE id=?", (_now(), rid))
        self.bus.publish("resource", resource_id=rid, kind=r["kind"], action="supprimée")

    def purge_temporary(self, days=7):
        """Les images générées mais jamais enregistrées disparaissent au bout de `days` jours (sauf si un
        artefact les utilise). Rien d'enregistré n'est touché."""
        keep = self.referenced_images()
        old = self._q("SELECT id FROM resources WHERE kind='image' AND saved=0 AND deleted IS NULL AND created<?",
                      (_now() - days * 86400,))
        n = 0
        for r in old:
            if r["id"] not in keep:
                self._x("UPDATE resources SET deleted=? WHERE id=?", (_now(), r["id"]))
                n += 1
        # fichiers orphelins : plus aucune ligne active ne les utilise
        live = {r["path"] for r in self._q("SELECT path FROM resources WHERE deleted IS NULL AND path IS NOT NULL")}
        dead = {r["path"] for r in self._q("SELECT path FROM resources WHERE deleted IS NOT NULL AND path IS NOT NULL "
                                           "AND deleted<?", (_now() - days * 86400,))}
        for rel in dead - live:
            p = (self.files / rel)
            if p.exists() and self.files.resolve() in p.resolve().parents:
                p.unlink()
        return n

    # ------------------------------------------------------------------ navigateur : historique et favoris
    def add_history(self, url, title, agent=False):
        last = self._q("SELECT url FROM browser_history ORDER BY id DESC LIMIT 1", one=True)
        if last and last["url"] == url:
            self._x("UPDATE browser_history SET title=?, at=? WHERE id=(SELECT MAX(id) FROM browser_history)",
                    (title[:300], _now()))
            return
        self._x("INSERT INTO browser_history(at,url,title,agent) VALUES(?,?,?,?)", (_now(), url, title[:300], int(agent)))

    def history(self, limit=200, q=None):
        if q:
            return self._q("SELECT * FROM browser_history WHERE url LIKE ? OR title LIKE ? ORDER BY id DESC LIMIT ?",
                           (f"%{q}%", f"%{q}%", limit))
        return self._q("SELECT * FROM browser_history ORDER BY id DESC LIMIT ?", (limit,))

    def clear_history(self):
        self._x("DELETE FROM browser_history")

    def favorites(self):
        return self._q("SELECT * FROM browser_favorites ORDER BY id DESC")

    def add_favorite(self, url, title):
        self._x("INSERT OR IGNORE INTO browser_favorites(url,title,created) VALUES(?,?,?)", (url, title[:300], _now()))
        return self.favorites()

    def remove_favorite(self, fid):
        self._x("DELETE FROM browser_favorites WHERE id=?", (fid,))
        return self.favorites()

    # ------------------------------------------------------------------ chiffres
    def stats(self):
        with self.store.lock:
            c = self.db.execute
            return {
                "projects": c("SELECT COUNT(*) FROM projects WHERE status<>'archivé'").fetchone()[0],
                "artifacts": c("SELECT COUNT(*) FROM artifacts WHERE deleted IS NULL").fetchone()[0],
                "images": c("SELECT COUNT(*) FROM resources WHERE kind='image' AND deleted IS NULL AND saved=1").fetchone()[0],
                "sources": c("SELECT COUNT(*) FROM resources WHERE kind='source' AND deleted IS NULL").fetchone()[0],
                "missions": c("SELECT COUNT(*) FROM missions").fetchone()[0],
                "missions_running": c("SELECT COUNT(*) FROM missions WHERE status IN ('en cours','en attente')").fetchone()[0],
            }
