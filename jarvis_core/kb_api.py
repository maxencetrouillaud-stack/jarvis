"""Routes HTTP de la base de connaissances (/api/kb/…)."""
import base64
import binascii
import re
import time

from . import extract
from .collect import CollectError, reliability_for
from .knowledge import RELIABILITY, Duplicate

MAX_FILE = 30 * 1024 * 1024


class Bad(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


def _int(v, default=None):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _links(app, node):
    """Connexions d'un élément, avec le nom de ce à quoi il est relié."""
    out = []
    for l in app.kb.links_of(node)[:20]:
        other = l["b"] if l["a"] == node else l["a"]
        kind, _, raw = other.partition(":")
        title = None
        if kind == "d":
            d = app.kb.get(int(raw))
            title = d["title"] if d else None
        elif kind == "m":
            mm = app.store.get_memory(int(raw))
            title = mm["content"][:100] if mm else None
        elif kind == "r":
            r = app.research.get(int(raw))
            title = f"Recherche : {r['question']}" if r else None
        if title:
            out.append({"node": other, "title": title, "kind": l["kind"], "score": l["score"]})
    return out


def kb_state(app):
    try:
        import pypdf  # noqa: F401
        pdf = True
    except ImportError:
        pdf = False
    s = app.settings
    return {
        "stats": app.kb.stats(s.get("kb_stale_days", 180)),
        "categories": app.kb.categories(),
        "jobs": app.kb.jobs(25),
        "reliability": RELIABILITY,
        "formats": extract.SUPPORTED,
        "pdf_ready": pdf,
        "embedding_active": app.brain.embedding_ref(),
        "llm_ready": bool(app.kbs._llm()),
        "originals_dir": str(app.kbs.dir / "originals"),
        "folder": app.kbs.folder_state(),
        "pending_findings": app.research.summary()["pending"],
    }


def handle(app, method, path, q, body):
    """Renvoie l'objet JSON de réponse, ou None si la route n'existe pas. `body` est une fonction."""
    kb, svc = app.kb, app.kbs
    arg = lambda k, d="": (q.get(k) or [d])[0]

    if path == "/api/kb/state" and method == "GET":
        return kb_state(app)

    if path == "/api/kb/documents" and method == "GET":
        f = arg("filter")
        cat = _int(arg("category"))
        if f == "stale":
            return kb.list(stale_before=time.time() - app.settings.get("kb_stale_days", 180) * 86400)
        if f == "dups":
            docs = kb.list(dups=True)
            for d in docs:
                other = kb.get(d["dup_of"])
                d["dup_title"] = other["title"] if other else "(supprimé)"
            return docs
        if f == "unclassified":
            return kb.list(category_id=0)
        if f == "obsolete":
            docs = kb.list(state="obsolete")
            for d in docs:
                other = kb.get(d["superseded_by"])
                d["newer_title"] = other["title"] if other else "(supprimé)"
            return docs
        return kb.list(category_id=cat, module=arg("module") or None, state=arg("state") or None,
                       q=arg("q").strip() or None)

    m = re.match(r"^/api/kb/documents/(\d+)(/refresh|/graph)?$", path)
    if m:
        doc_id, action = int(m.group(1)), m.group(2)
        if action == "/refresh" and method == "POST":
            return {"job": svc.submit_refresh(doc_id)}
        if action == "/graph" and method == "POST":
            return {"job": svc.submit_graph(doc_id)}
        if not action and method == "GET":
            d = kb.get(doc_id)
            if not d:
                raise Bad(404, "Document introuvable")
            chunks = kb.chunks_of(doc_id)
            d["chunks"] = chunks[:60]
            d["chunks_total"] = len(chunks)
            d["versions"] = kb.versions(doc_id)
            d["extracts"] = kb.extracts_of(doc_id)
            d["links"] = _links(app, f"d:{doc_id}")
            if d.get("superseded_by"):
                other = kb.get(d["superseded_by"])
                d["newer_title"] = other["title"] if other else ""
            return d
        if not action and method == "PATCH":
            d = kb.update(doc_id, body())
            if not d:
                raise Bad(404, "Document introuvable")
            return d
        if not action and method == "DELETE":
            d = kb.delete(doc_id)
            if not d:
                raise Bad(404, "Document introuvable")
            if d.get("original"):
                (svc.dir / "originals" / d["original"]).unlink(missing_ok=True)
            return {"ok": True}

    if path == "/api/kb/extracts" and method == "GET":
        kind = arg("kind")
        return kb.search_extracts(arg("q"), kind if kind in ("formule", "tableau", "donnée") else None)
    if path == "/api/kb/conflicts" and method == "GET":
        return kb.data_conflicts()

    if path == "/api/kb/sources" and method == "GET":
        return app.research.tracked_sources()
    if path == "/api/kb/sources" and method == "POST":
        b = body()
        try:
            kb.set_source_rule(str(b.get("domain") or ""), b.get("reliability"), b.get("blocked"), b.get("note"))
        except ValueError as e:
            raise Bad(400, str(e)) from None
        return app.research.tracked_sources()
    m = re.match(r"^/api/kb/sources/([\w.\-]+)$", path)
    if m and method == "DELETE":
        kb.delete_source_rule(m.group(1))
        return app.research.tracked_sources()

    if path == "/api/kb/folder/scan" and method == "POST":
        try:
            return svc.scan_folder()
        except ValueError as e:
            raise Bad(400, str(e)) from None
    m = re.match(r"^/api/kb/jobs/(\d+)/retry$", path)
    if m and method == "POST":
        try:
            return {"job": svc.retry(int(m.group(1)))}
        except ValueError as e:
            raise Bad(400, str(e)) from None
    if path == "/api/kb/rebuild-links" and method == "POST":
        return {"job": svc.submit_links()}

    # ----- hub : tableau de bord, recherche croisée, interface des agents, export -----
    if path == "/api/hub/dashboard" and method == "GET":
        return app.hub.dashboard()
    if path == "/api/hub/search" and method == "GET":
        query = arg("q").strip()
        if len(query) < 2:
            return []
        mods = [x for x in arg("modules").split(",") if x] or None
        return app.hub.search(query, mods, 12)
    if path == "/api/hub/query" and method == "POST":
        b = body()
        question = str(b.get("question") or "").strip()
        if not question:
            raise Bad(400, "Question vide")
        g = app.hub.gather(question, "general", None, app.brain.embed(question))
        return {"plan": g["plan"], "modules": g["modules"], "sources": g["sources"], "relations": g["relations"],
                "memories": [{"id": x["id"], "content": x["content"], "category": x["category"]} for x in g["memories"]],
                "conflicts": g["conflicts"], "kb_miss": g["kb_miss"]}
    if path == "/api/hub/export" and method == "POST":
        return app.hub.export()
    if path == "/api/hub/exports" and method == "GET":
        return app.hub.exports()

    # ----- chercheur autonome -----
    rs = app.research
    if path == "/api/research" and method == "GET":
        return {"runs": rs.runs(), "providers": rs.providers_state(), "pending": rs.pending(), "watches": rs.watches(),
                "llm_ready": bool(svc._llm()), "summary": rs.summary()}
    if path == "/api/research" and method == "POST":
        b = body()
        try:
            return {"id": rs.create(b.get("question") or "", str(b.get("mode") or "rapide"),
                                    str(b.get("origin") or "manuel") if b.get("origin") in ("manuel", "lacune", "conversation") else "manuel",
                                    _int(b.get("gap_id")))}
        except ValueError as e:
            raise Bad(400, str(e)) from None
    if path == "/api/research/thin" and method == "GET":
        return rs.thin_topics()
    if path == "/api/research/keys" and method == "POST":
        b = body()
        if b.get("name") not in ("brave", "openalex"):
            raise Bad(400, "Service inconnu")
        app.keys.set(b["name"], str(b.get("key") or "").strip())
        return rs.providers_state()
    m = re.match(r"^/api/research/findings/(\d+)$", path)
    if m and method == "POST":
        b = body()
        try:
            return rs.decide(int(m.group(1)), str(b.get("action") or ""), b.get("claim"))
        except ValueError as e:
            raise Bad(400, str(e)) from None
    if path == "/api/research/watch" and method == "POST":
        b = body()
        try:
            rs.add_watch(b.get("topic") or "", b.get("every_days") or 7)
        except ValueError as e:
            raise Bad(400, str(e)) from None
        return rs.watches()
    m = re.match(r"^/api/research/watch/(\d+)(/run)?$", path)
    if m:
        wid = int(m.group(1))
        if m.group(2) and method == "POST":
            return {"runs": rs.run_due_watches(force_id=wid)}
        if method == "PATCH":
            b = body()
            rs.update_watch(wid, b.get("enabled"), b.get("every_days"))
            return rs.watches()
        if method == "DELETE":
            rs.delete_watch(wid)
            return rs.watches()
    m = re.match(r"^/api/research/(\d+)(/cancel)?$", path)
    if m:
        rid = int(m.group(1))
        if m.group(2) and method == "POST":
            rs.cancel(rid)
            return {"ok": True}
        if method == "GET":
            r = rs.get(rid)
            if not r:
                raise Bad(404, "Recherche introuvable")
            return r
        if method == "DELETE":
            return rs.delete(rid)

    m = re.match(r"^/api/kb/versions/(\d+)$", path)
    if m and method == "GET":
        text = kb.version_text(int(m.group(1)))
        if text is None:
            raise Bad(404, "Version introuvable")
        return {"text": text[:200000]}

    if path == "/api/kb/import/file" and method == "POST":
        b = body()
        try:
            data = base64.b64decode(str(b.get("data") or ""), validate=True)
        except (binascii.Error, ValueError):
            raise Bad(400, "Fichier illisible") from None
        if not data:
            raise Bad(400, "Fichier vide")
        if len(data) > MAX_FILE:
            raise Bad(413, "Fichier trop volumineux (30 Mo au maximum)")
        name = str(b.get("name") or "document")
        ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
        if ext and ext not in extract.SUPPORTED:
            raise Bad(400, f"Format {ext} non pris en charge. Formats lus : {', '.join(extract.SUPPORTED)}")
        return {"job": svc.submit_file(name, data, {
            "category_id": _int(b.get("category_id")), "reliability": _int(b.get("reliability"), 3),
            "license": str(b.get("license") or "")[:200]})}

    if path == "/api/kb/import/url" and method == "POST":
        b = body()
        url = str(b.get("url") or "").strip()
        try:
            return {"job": svc.submit_url(url, {"category_id": _int(b.get("category_id")),
                                                "reliability": _int(b.get("reliability")) or reliability_for(url)})}
        except CollectError as e:
            raise Bad(400, str(e)) from None

    if path == "/api/kb/import/wikipedia" and method == "POST":
        b = body()
        titles = [str(t).strip() for t in (b.get("titles") or []) if str(t).strip()][:200]
        if not titles:
            raise Bad(400, "Aucun article indiqué")
        lang = str(b.get("lang") or app.settings.get("kb_wikipedia_lang", "fr"))
        items = b.get("items")  # [{title, category_id}] pour le premier corpus
        if isinstance(items, list) and items:
            jobs = [svc.submit_wikipedia(str(i["title"]), lang, {"category_id": _int(i.get("category_id"))})
                    for i in items[:300] if isinstance(i, dict) and i.get("title")]
        else:
            jobs = [svc.submit_wikipedia(t, lang, {"category_id": _int(b.get("category_id"))}) for t in titles]
        return {"jobs": jobs}

    if path == "/api/kb/import/text" and method == "POST":
        b = body()
        text = extract.clean(str(b.get("text") or ""))
        if not text:
            raise Bad(400, "Texte vide")
        try:
            doc_id = kb.ingest(str(b.get("title") or "Note").strip()[:200], [("", text)], "texte", "saisie directe",
                               _int(b.get("category_id")), _int(b.get("reliability"), 3),
                               embed=app.brain.embed if app.brain.embedding_ref() else None)
        except Duplicate as e:
            raise Bad(409, str(e)) from None
        return kb.get(doc_id)

    if path == "/api/kb/wikipedia/search" and method == "GET":
        try:
            return svc.wiki.search(arg("q"), arg("lang", app.settings.get("kb_wikipedia_lang", "fr")), 10)
        except CollectError as e:
            raise Bad(502, str(e)) from None

    if path == "/api/kb/search" and method == "GET":
        query = arg("q")
        return kb.search(query, 20, app.brain.embed(query), _int(arg("category")), per_doc=4, strict=False)

    if path == "/api/kb/graph" and method == "GET":
        return kb.graph(arg("q"))

    if path == "/api/kb/relations" and method == "POST":
        b = body()
        n = kb.add_relations([(b.get("source", ""), b.get("relation", ""), b.get("cible", ""))])
        if not n:
            raise Bad(400, "Relation vide ou déjà présente")
        return {"ok": True}
    m = re.match(r"^/api/kb/relations/(\d+)$", path)
    if m and method == "DELETE":
        kb.delete_relation(int(m.group(1)))
        return {"ok": True}

    if path == "/api/kb/jobs" and method == "GET":
        return kb.jobs(60)
    if path == "/api/kb/jobs/clear" and method == "POST":
        kb.clear_jobs()
        return {"ok": True}
    if path == "/api/kb/journal" and method == "GET":
        return kb.journal(120)

    if path == "/api/kb/gaps" and method == "GET":
        return kb.gaps()
    m = re.match(r"^/api/kb/gaps/(\d+)$", path)
    if m and method == "POST":
        status = body().get("status")
        if status not in ("ouvert", "traité", "ignoré"):
            raise Bad(400, "Statut invalide")
        kb.set_gap(int(m.group(1)), status)
        return {"ok": True}

    if path == "/api/kb/proposals" and method == "GET":
        return kb.proposals()
    if path == "/api/kb/suggest" and method == "POST":
        return svc.suggest()
    m = re.match(r"^/api/kb/proposals/(\d+)$", path)
    if m and method == "POST":
        action = body().get("action")
        if action == "accept":
            return {"job": svc.accept(int(m.group(1)))}
        if action == "reject":
            kb.set_proposal(int(m.group(1)), "refusé")
            return {"ok": True}
        raise Bad(400, "Action invalide")

    if path == "/api/kb/starter" and method == "GET":
        return svc.starter()
    if path == "/api/kb/reindex" and method == "POST":
        return {"job": svc.submit_reindex()}
    if path == "/api/kb/refresh-stale" and method == "POST":
        return {"queued": svc.refresh_stale()}
    if path == "/api/kb/categories" and method == "POST":
        b = body()
        return {"id": kb.add_category(str(b.get("name") or ""), _int(b.get("parent_id")))}

    if path == "/api/global-search" and method == "GET":
        query = arg("q").strip()
        if len(query) < 2:
            return {"knowledge": [], "memories": [], "messages": []}
        ext = app.brain.embed(query)
        return {"knowledge": kb.search(query, 8, ext, strict=False),
                "memories": app.store.search_memories(query, 6, ext),
                "messages": app.store.search_messages(query, None, 6)}

    return None
