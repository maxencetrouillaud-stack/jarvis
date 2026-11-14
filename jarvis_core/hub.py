"""Hub du cerveau : point d'entrée unique vers les modules de connaissance.

Bibliothèque technique, connaissances générales, recherches du chercheur, mémoire vectorielle, mémoire
personnelle et projets partagent les mêmes index. Le hub :
  - choisit, pour chaque question, les modules qui valent la peine d'être consultés (pas tous à chaque fois) ;
  - lance les recherches, croise les résultats et repère les données contradictoires ;
  - relie chaque nouvelle connaissance à ce qui existe déjà (connexions affichées dans la sphère) ;
  - fournit les chiffres du centre de connaissances et l'export de la base.
C'est aussi l'interface que les futurs agents utiliseront (route /api/hub/query).
"""
import json
import re
import time
import zipfile
from pathlib import Path

from . import library
from .memory import tokens, unpack
from .vectors import cosine, dot

MODULES = [
    ("bibliotheque", "Bibliothèque technique", "#ff9a3c"),
    ("generale", "Connaissances générales", "#facc15"),
    ("recherche", "Recherches du chercheur", "#f472b6"),
    ("vectorielle", "Mémoire vectorielle", "#22d3ee"),
    ("perso", "Mémoire personnelle", "#fb7185"),
    ("projets", "Projets", "#f8fafc"),
]
MODULE_LABEL = {k: l for k, l, _ in MODULES}

_PERSONAL = re.compile(r"(?<![\wÀ-ÿ])(?:je|j['’]|moi|mon|ma|mes|m['’]|me|nous|notre|nos|mienne?s?)(?![\wÀ-ÿ])", re.I)
_RECENT = re.compile(r"derni[eè]res?\s+(?:version|nouveaut|avanc|actualit|recherche|publication)|r[ée]cent|nouveaut|"
                     r"actualit[ée]|[ée]tat de l['’]art|en ce moment|vient de sortir|cette ann[ée]e|\b202[5-9]\b", re.I)
_SMALL = re.compile(r"^\W*(?:salut|bonjour|bonsoir|coucou|hello|merci|ok|d['’]accord|super|parfait|top|génial|"
                    r"au revoir|bonne nuit|bonne journée|à plus|ça va|ca va|oui|non)\b", re.I)
_QUESTION = re.compile(r"\?|^\s*(?:qu|comment|pourquoi|combien|quel|quand|où|est-ce|explique|donne|calcule|d[ée]finis|"
                       r"d[ée]cris|compare|liste|r[ée]sume|pr[ée]sente|montre|cherche|trouve)", re.I)
_PAST = re.compile(r"derni[eè]re fois|l['’]autre (?:jour|fois)|on (?:avait|a) (?:dit|parl|vu)|comme je (?:t['’]ai|te l['’]ai)|"
                   r"tu te (?:souviens|rappelles)|rappelle[- ]moi|d[ée]j[aà] (?:dit|parl)", re.I)


class Hub:
    def __init__(self, store, kb, brain, settings, home: Path):
        self.store, self.kb, self.brain, self.settings, self.home = store, kb, brain, settings, Path(home)
        self.research = None  # branché par l'application une fois le chercheur créé

    # ------------------------------------------------------------ choix des modules
    def plan(self, text, route="general"):
        """Quels modules consulter pour cette demande, et pourquoi."""
        t = text.strip()
        small = bool(_SMALL.match(t)) and len(t) < 60 and "?" not in t
        cat, score, words = library.classify_text("", t, min_score=1.0, question=True)
        personal = bool(_PERSONAL.search(t))
        asks = bool(_QUESTION.search(t)) or len(t) > 80 or bool(cat)
        return {
            "small_talk": small,
            "domain": cat, "domain_id": self.kb.find_category(cat) if cat else None, "domain_words": words,
            "personal": personal,
            "kb": not small and asks and self.settings.get("kb_enabled", True),
            "memory": "essentiel" if small else ("complète" if personal else "ciblée"),
            "recall": not small and self.settings.get("search_past_conversations") and (personal or bool(_PAST.search(t))),
            "recent": bool(_RECENT.search(t)),
        }

    # ------------------------------------------------------------ contexte d'une conversation
    def gather(self, text, route, conv_id, ext):
        """Tout le contexte utile à une réponse, module par module."""
        s = self.settings
        plan = self.plan(text, route)
        top_k = s.get("memory_top_k", 8)
        if plan["memory"] == "essentiel":
            memories = [m for m in self.store.search_memories(text, 3, ext, mark_used=True, always_core=True)
                        if m.get("core")]
        else:
            memories = self.store.search_memories(text, top_k if plan["memory"] == "complète" else max(3, top_k // 2),
                                                  ext, mark_used=True, always_core=True)
        recalls = self.store.recall_messages(text, conv_id) if plan["recall"] else []

        sources, relations, kb_miss, conflicts = [], [], False, []
        hits = []
        if plan["kb"] and self.kb:
            hits = self.kb.search(text, s.get("kb_top_k", 6) + 2, ext)
            if plan["domain_id"]:  # à pertinence proche, le domaine identifié passe devant
                ids = set(self.kb.category_ids(plan["domain_id"]))
                for h in hits:
                    if h["category_id"] in ids:
                        h["score"] += 0.12
                hits.sort(key=lambda h: h["score"], reverse=True)
            hits = hits[:s.get("kb_top_k", 6)]
            for i, h in enumerate(hits):
                sources.append({
                    "n": i + 1, "doc_id": h["doc_id"], "chunk_id": h["id"], "title": h["title"], "locator": h["locator"],
                    "source": h["source"] or h["source_type"], "source_type": h["source_type"],
                    "reliability": h["reliability"], "content": h["content"], "module": h["module"],
                    "found_by": h["found_by"],
                    "status": ("à confirmer" if h["source_type"] == "recherche" and "à confirmer" in (h.get("description") or "")
                               else "vérifié" if h["source_type"] == "recherche" else "")})
            st = self.kb.stats()
            relations = self.kb.related(text) if st["relations"] else []
            personal_hits = [m for m in memories if not m.get("core")] or recalls
            if not sources and route != "quick" and not personal_hits and st["documents"]:
                kb_miss = True
                self.kb.note_gap(text)
            conflicts = self.cross_check(sources)

        mode = s.get("research_on_gap", "proposer")
        offer = bool(self.research) and mode != "jamais" and plan["kb"] and (kb_miss or plan["recent"]
                                                                               or (not sources and not memories))
        modules = self.report(plan, memories, recalls, sources, relations)
        return {"plan": plan, "memories": memories, "recalls": recalls, "sources": sources, "relations": relations,
                "kb_miss": kb_miss, "conflicts": conflicts, "modules": modules,
                "research_offer": ("nouveautés" if plan["recent"] and sources else "manque") if offer else ""}

    def report(self, plan, memories, recalls, sources, relations):
        """Ce qui a été consulté, module par module (affiché sous la réponse)."""
        per = {}
        for src in sources:
            per[src["module"]] = per.get(src["module"], 0) + 1
        by_sense = sum(1 for src in sources if "sens" in src["found_by"])
        by_near = sum(1 for src in sources if "proximité" in src["found_by"])
        perso = [m for m in memories if m["category"] != "projets"]
        proj = [m for m in memories if m["category"] == "projets"]
        skipped = "non consultée : simple échange" if plan["small_talk"] else "non consultée pour cette demande"
        out = []
        for key in ("bibliotheque", "generale", "recherche"):
            out.append({"key": key, "label": MODULE_LABEL[key], "used": plan["kb"], "hits": per.get(key, 0),
                        "note": "" if plan["kb"] else skipped})
        out.append({"key": "vectorielle", "label": MODULE_LABEL["vectorielle"], "used": plan["kb"],
                    "hits": by_sense or by_near,
                    "note": (f"retrouvés par le sens (modèle {self.brain.embedding_ref()})" if self.brain.embedding_ref()
                             else "retrouvés par proximité de vocabulaire ; sans modèle d'embeddings, pas de recherche par le sens")
                    if plan["kb"] else skipped})
        out.append({"key": "perso", "label": MODULE_LABEL["perso"], "used": True, "hits": len(perso),
                    "note": {"essentiel": "souvenirs essentiels seulement", "ciblée": "recherche ciblée",
                             "complète": "recherche complète (demande personnelle)"}[plan["memory"]]})
        out.append({"key": "projets", "label": MODULE_LABEL["projets"], "used": True, "hits": len(proj), "note": ""})
        out.append({"key": "conversations", "label": "Anciennes conversations", "used": bool(plan["recall"]),
                    "hits": len(recalls), "note": ""})
        if relations:
            out.append({"key": "graphe", "label": "Graphe de concepts", "used": True, "hits": len(relations), "note": ""})
        return out

    def cross_check(self, sources):
        """Données chiffrées en désaccord entre les passages retenus."""
        items = []
        for src in sources:
            for e in library.find_extracts([("", src["content"])]):
                if e["kind"] == "donnée":
                    items.append({**e, "n": src["n"], "title": src["title"]})
        out = []
        for a, b in library.conflicts(items):
            if a["n"] != b["n"]:
                out.append({"label": a["label"], "a": {"n": a["n"], "text": a["content"], "title": a["title"]},
                            "b": {"n": b["n"], "text": b["content"], "title": b["title"]}})
        return out[:5]

    # ------------------------------------------------------------ recherche croisée (interface, agents)
    def search(self, query, modules=None, k=12, ext=None):
        """Recherche dans tous les modules, résultats fusionnés et typés."""
        want = set(modules or [m[0] for m in MODULES] + ["conversations"])
        if ext is None:
            ext = self.brain.embed(query)
        out = []
        if want & {"bibliotheque", "generale", "recherche", "vectorielle"}:
            for rank, h in enumerate(self.kb.search(query, k * 2, ext, per_doc=3, strict=False)):
                mod = h["module"]
                if mod not in want and not ("vectorielle" in want and h["found_by"] in ("sens", "proximité")):
                    continue
                out.append({"module": mod, "kind": "passage", "node": f"d:{h['doc_id']}", "doc_id": h["doc_id"],
                            "title": h["title"] + (f" — {h['locator']}" if h["locator"] else ""),
                            "excerpt": h["content"][:420], "source": h["source"] or h["source_type"],
                            "reliability": h["reliability"], "category": h["category"], "found_by": h["found_by"],
                            "rank": rank, "score": h["score"]})
        if want & {"perso", "projets"}:
            qt = set(tokens(query))
            for rank, m in enumerate(self.store.search_memories(query, k, ext)):
                mod = "projets" if m["category"] == "projets" else "perso"
                if mod in want:
                    out.append({"module": mod, "kind": "souvenir", "node": f"m:{m['id']}", "memory_id": m["id"],
                                "title": m["category"], "excerpt": m["content"][:420], "source": "mémoire personnelle",
                                "found_by": ("mots" if qt & set(tokens(m["content"])) else
                                             "sens" if ext and m.get("vec_model") == ext[1] else "proximité"),
                                "rank": rank,
                                "score": m["score"]})
        if "conversations" in want:
            for rank, r in enumerate(self.store.search_messages(query, None, 6)):
                out.append({"module": "conversations", "kind": "message", "conv_id": r["conv_id"], "title": r["title"],
                            "excerpt": r["content"][:300], "source": "conversation", "found_by": "mots", "rank": rank,
                            "score": 0})
        # fusion des classements de chaque module (rang réciproque) : aucun module n'écrase les autres
        out.sort(key=lambda r: (-1 / (20 + r["rank"]), r["module"]))
        return out[:k * 2]

    # ------------------------------------------------------------ intégration : connexions d'une connaissance
    def _vectors(self, node):
        kind, _, raw = node.partition(":")
        if not raw.isdigit():
            return None
        if kind == "d":
            with self.kb.lock:
                r = self.kb.db.execute("SELECT title, vec_local, vec_ext, vec_model FROM documents WHERE id=?",
                                       (int(raw),)).fetchone()
        elif kind == "m":
            with self.store.lock:
                r = self.store.db.execute("SELECT content AS title, vec_local, vec_ext, vec_model FROM memories WHERE id=?",
                                          (int(raw),)).fetchone()
        else:
            return None
        if not r or not r["vec_local"]:
            return None
        return {"text": r["title"], "local": unpack(r["vec_local"]),
                "ext": unpack(r["vec_ext"]) if r["vec_ext"] else None, "model": r["vec_model"]}

    def integrate(self, node, k=3):
        """Cherche à quoi une connaissance se rattache et crée les connexions. -> nombre de liens créés"""
        if not self.settings.get("kb_auto_links", True):
            return 0
        me = self._vectors(node)
        if not me:
            return 0
        me_stems = set(library.label_key(me["text"]).split())
        cands = []
        rows = [(f"d:{r['id']}", r) for r in self.kb.doc_vectors(near=me["local"])]
        with self.store.lock:
            rows += [(f"m:{r['id']}", r) for r in self.store.db.execute(
                "SELECT id, content AS title, vec_local, vec_ext, vec_model FROM memories ORDER BY id DESC LIMIT 5000")]
        for nid, r in rows:
            if nid == node or not r["vec_local"]:
                continue
            if me["ext"] and r["vec_ext"] and r["vec_model"] == me["model"]:
                sim, ok = cosine(me["ext"], unpack(r["vec_ext"])), 0.62
            else:
                sim, ok = dot(me["local"], unpack(r["vec_local"])), 0.28
                # sans modèle d'embeddings, on exige aussi un mot significatif commun dans les titres
                if sim >= ok and len(me_stems) > 1 and not me_stems & set(library.label_key(r["title"]).split()) and sim < 0.45:
                    continue
            if sim >= ok:
                cands.append((sim, nid))
        cands.sort(reverse=True)
        return self.kb.add_links([(node, nid, sim) for sim, nid in cands[:k]])

    def forget(self, node):
        self.kb.drop_links(node=node)

    def rebuild_links(self):
        """Recalcule toutes les connexions sémantiques (après un changement de modèle d'embeddings, par exemple)."""
        self.kb.drop_links(kind="sémantique")
        n = 0
        for r in self.kb.doc_vectors():
            n += self.integrate(f"d:{r['id']}")
        for m in self.store.list_memories():
            n += self.integrate(f"m:{m['id']}")
        return n

    # ------------------------------------------------------------ centre de connaissances
    def _dir_bytes(self, path):
        p = Path(path)
        if not p.exists():
            return 0
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())

    def dashboard(self):
        ks = self.kb.stats(self.settings.get("kb_stale_days", 180))
        ms = self.store.stats()
        gen = self.kb.general_category_ids()
        with self.kb.lock:
            db = self.kb.db
            docs = db.execute("SELECT id, title, imported, source_type, category_id, n_chunks FROM documents "
                              "WHERE archived=0 ORDER BY imported DESC LIMIT 12").fetchall()
            per_mod = {"recherche": db.execute("SELECT COUNT(*) FROM documents WHERE source_type='recherche' AND archived=0").fetchone()[0]}
            marks = ",".join("?" * len(gen)) or "NULL"
            per_mod["generale"] = db.execute(
                f"SELECT COUNT(*) FROM documents WHERE archived=0 AND source_type!='recherche' AND category_id IN ({marks})",
                sorted(gen)).fetchone()[0]
            per_mod["bibliotheque"] = ks["documents"] - ks["archived"] - per_mod["recherche"] - per_mod["generale"]
            errors = [dict(r) for r in db.execute(
                "SELECT id, kind, label, detail, created FROM jobs WHERE status='erreur' ORDER BY id DESC LIMIT 20")]
            stale_before = time.time() - self.settings.get("kb_stale_days", 180) * 86400
            stale = [dict(r) | {"why": "non vérifié depuis longtemps"} for r in db.execute(
                "SELECT id, title, verified, source_type FROM documents WHERE archived=0 AND verified<? "
                "ORDER BY verified LIMIT 12", (stale_before,))]
            stale += [dict(r) | {"why": "une version plus récente existe"} for r in db.execute(
                "SELECT id, title, verified, source_type FROM documents WHERE archived=0 AND superseded_by IS NOT NULL LIMIT 12")]
            file_errors = [dict(r) for r in db.execute(
                "SELECT path, status FROM watched_files WHERE status LIKE 'erreur%' LIMIT 20")]
            domains = db.execute("SELECT COUNT(DISTINCT source) FROM documents WHERE source LIKE 'http%'").fetchone()[0]
        with self.store.lock:
            mems = self.store.db.execute("SELECT id, content, category, created FROM memories ORDER BY created DESC LIMIT 8").fetchall()
            n_proj = self.store.db.execute("SELECT COUNT(*) FROM memories WHERE category='projets'").fetchone()[0]
        latest = [{"kind": "document", "id": d["id"], "node": f"d:{d['id']}", "title": d["title"], "at": d["imported"],
                   "module": "recherche" if d["source_type"] == "recherche" else "generale" if d["category_id"] in gen else "bibliotheque",
                   "detail": f"{d['n_chunks']} passage(s) · {d['source_type']}"} for d in docs]
        latest += [{"kind": "souvenir", "id": m["id"], "node": f"m:{m['id']}", "title": m["content"][:110], "at": m["created"],
                    "module": "projets" if m["category"] == "projets" else "perso", "detail": m["category"]} for m in mems]
        latest.sort(key=lambda x: x["at"], reverse=True)
        storage = {
            "knowledge_db": ks["db_bytes"], "memory_db": ms["db_bytes"],
            "originals": self._dir_bytes(self.home / "knowledge" / "originals"),
            "exports": self._dir_bytes(self.home / "exports"), "backups": self._dir_bytes(self.home / "backups")}
        storage["total"] = sum(storage.values())
        research = self.research.summary() if self.research else {"runs": [], "pending": 0, "active": 0, "total": 0}
        modules = [
            {"key": "bibliotheque", "label": MODULE_LABEL["bibliotheque"], "count": per_mod["bibliotheque"], "unit": "documents"},
            {"key": "generale", "label": MODULE_LABEL["generale"], "count": per_mod["generale"], "unit": "documents"},
            {"key": "recherche", "label": MODULE_LABEL["recherche"], "count": per_mod["recherche"], "unit": "connaissances validées"},
            {"key": "vectorielle", "label": MODULE_LABEL["vectorielle"], "count": ks["chunks"] + ms["memories"], "unit": "vecteurs"},
            {"key": "perso", "label": MODULE_LABEL["perso"], "count": ms["memories"] - n_proj, "unit": "souvenirs"},
            {"key": "projets", "label": MODULE_LABEL["projets"], "count": n_proj, "unit": "souvenirs"},
        ]
        return {
            "totals": {
                "knowledge": ks["documents"] - ks["archived"] + ms["memories"] + ks["entities"],
                "documents": ks["documents"], "passages": ks["chunks"], "memories": ms["memories"],
                "relations": ks["relations"] + ks["links"], "graph_relations": ks["relations"], "links": ks["links"],
                "concepts": ks["entities"], "categories": ks["categories"], "categories_used": ks["categories_used"],
                "extracts": ks["extracts"], "sources": domains,
            },
            "modules": modules, "latest": latest[:14], "research": research, "to_update": stale[:16],
            "errors": errors, "file_errors": file_errors, "storage": storage,
            "vector": {"model": self.brain.embedding_ref(), "chunks": ks["chunks"], "chunks_semantic": ks["chunks_semantic"],
                       "memories": ms["memories"], "memories_semantic": ms["memories_semantic"],
                       "ann": ks["ann_active"], "numpy": False},
            "todo": {"duplicates": ks["duplicates"], "obsolete": ks["obsolete"], "stale": ks["stale"],
                     "unclassified": ks["unclassified"], "gaps": ks["gaps"], "proposals": ks["proposals"],
                     "findings": research["pending"]},
        }

    # ------------------------------------------------------------ export
    def export(self):
        """Archive lisible de toute la base : métadonnées et textes en JSON, base SQLite, fichiers d'origine."""
        out_dir = self.home / "exports"
        out_dir.mkdir(exist_ok=True)
        name = f"jarvis-connaissances-{time.strftime('%Y%m%d-%H%M%S')}.zip"
        tmp_db = out_dir / "_knowledge.tmp.db"
        self.kb.snapshot(tmp_db)
        try:
            with zipfile.ZipFile(out_dir / name, "w", zipfile.ZIP_DEFLATED) as z:
                z.write(tmp_db, "knowledge.db")
                docs = self.kb.list(limit=10 ** 9)
                lines = []
                for d in docs:
                    d = dict(d)
                    d["text"] = "\n\n".join(c["content"] for c in self.kb.chunks_of(d["id"]))
                    d["extracts"] = self.kb.extracts_of(d["id"])
                    lines.append(json.dumps(d, ensure_ascii=False))
                z.writestr("documents.jsonl", "\n".join(lines))
                z.writestr("souvenirs.jsonl", "\n".join(json.dumps(m, ensure_ascii=False) for m in self.store.list_memories()))
                g = self.kb.graph(limit=10 ** 6)
                z.writestr("graphe.json", json.dumps(g, ensure_ascii=False))
                z.writestr("connexions.json", json.dumps([list(r) for r in self.kb.all_links()], ensure_ascii=False))
                if self.research:
                    z.writestr("recherches.json", json.dumps(self.research.export(), ensure_ascii=False))
                orig = self.home / "knowledge" / "originals"
                n_files = 0
                if orig.exists():
                    for f in sorted(orig.iterdir()):
                        if f.is_file():
                            z.write(f, f"fichiers-origine/{f.name}")
                            n_files += 1
                z.writestr("LISEZ-MOI.txt", (
                    "Export de la base de connaissances de Jarvis\n"
                    f"Date : {time.strftime('%d/%m/%Y %H:%M')}\n\n"
                    f"documents.jsonl   : {len(docs)} documents (métadonnées, source, licence, texte, formules/tableaux/données)\n"
                    "souvenirs.jsonl   : mémoire personnelle\n"
                    "graphe.json       : concepts et relations\n"
                    "connexions.json   : liens entre connaissances\n"
                    "recherches.json   : historique du chercheur, sources et affirmations\n"
                    f"fichiers-origine/ : {n_files} fichier(s) importé(s), tels quels\n"
                    "knowledge.db      : la base SQLite complète (pour une restauration)\n"))
        finally:
            tmp_db.unlink(missing_ok=True)
        return {"name": name, "bytes": (out_dir / name).stat().st_size, "documents": len(docs)}

    def exports(self):
        d = self.home / "exports"
        if not d.exists():
            return []
        return [{"name": f.name, "bytes": f.stat().st_size, "created": f.stat().st_mtime}
                for f in sorted(d.glob("jarvis-connaissances-*.zip"), reverse=True)]
