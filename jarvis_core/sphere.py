"""Sphère 3D de la mémoire : nœuds, liens et positions calculés à partir des données réelles.

Chaque nœud correspond à une ligne de la base (souvenir, document, concept du graphe, catégorie).
La position n'est pas aléatoire :
  - chaque famille occupe une région fixe de la sphère ; chaque sous-catégorie, une zone de cette région ;
  - dans sa zone, un élément est placé d'après une projection de son vecteur de contenu : deux textes
    proches se retrouvent proches ;
  - un concept du graphe est placé à l'intérieur de la sphère, au barycentre de ce à quoi il est relié.
"""
import math
import re
import zlib

from . import vectors
from .hub import MODULES
from .knowledge import stems
from .memory import local_vector, unpack

MAX_NODES = 6000
FAMILIES = [
    ("sciences", "Sciences", "#3b82f6"),
    ("maths", "Mathématiques", "#34d399"),
    ("ingenierie", "Ingénierie", "#ff9a3c"),
    ("informatique", "Informatique", "#a78bfa"),
    ("ia", "Intelligence artificielle", "#22d3ee"),
    ("general", "Histoire et culture générale", "#facc15"),
    ("quotidien", "Vie quotidienne", "#a3e635"),
    ("documents", "Mes documents", "#94a3b8"),
    ("perso", "Mémoire personnelle", "#fb7185"),
    ("projets", "Projets", "#f8fafc"),
    ("nonclasse", "Non classé", "#64748b"),
]
TOP = {"Sciences fondamentales": "sciences", "Ingénierie et industrie": "ingenierie", "Informatique et IA": "informatique",
       "Connaissances générales": "general", "Vie quotidienne": "quotidien", "Mes documents": "documents"}
SUB = {"Mathématiques": "maths", "Intelligence artificielle": "ia", "LLM, RAG, embeddings": "ia", "Agents autonomes": "ia"}


def _norm(v):
    n = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) or 1.0
    return (v[0] / n, v[1] / n, v[2] / n)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _fib(i, n):
    y = 1 - 2 * (i + 0.5) / n
    r = math.sqrt(max(0.0, 1 - y * y))
    t = i * math.pi * (3 - math.sqrt(5))
    return (math.cos(t) * r, y, math.sin(t) * r)


def _basis(a):
    up = (0.0, 1.0, 0.0) if abs(a[1]) < 0.9 else (1.0, 0.0, 0.0)
    u = _norm(_cross(a, up))
    return u, _cross(a, u)


def _offset(a, dx, dy, radius=1.0):
    u, v = _basis(a)
    p = _norm((a[0] + dx * u[0] + dy * v[0], a[1] + dx * u[1] + dy * v[1], a[2] + dx * u[2] + dy * v[2]))
    return (p[0] * radius, p[1] * radius, p[2] * radius)


def _signs(seed, dim=512):
    out, x = [], zlib.crc32(seed.encode()) or 1
    for _ in range(dim):
        x = (x * 1103515245 + 12345) & 0x7FFFFFFF
        out.append(1.0 if x & 0x10000 else -1.0)
    return out


R1, R2, R3 = _signs("axe-1"), _signs("axe-2"), _signs("axe-3")


def _project(vec):
    """Projection fixe du vecteur de contenu sur 3 axes : (x, y) sur la surface, z pour la profondeur."""
    if not vec:
        return 0.0, 0.0, 0.0
    c = lambda t: max(-2.2, min(2.2, t))
    return (c(sum(a * b for a, b in zip(vec, R1))), c(sum(a * b for a, b in zip(vec, R2))),
            c(sum(a * b for a, b in zip(vec, R3))))


def _r(x):
    return round(x, 4)


class Sphere:
    def __init__(self, store, kb, research=None):
        self.store, self.kb, self.research = store, kb, research
        self._cache = (None, None)

    def version(self):
        with self.kb.lock:
            k = self.kb.db.execute(
                "SELECT (SELECT COUNT(*) FROM documents), (SELECT COALESCE(MAX(updated),0) FROM documents), "
                "(SELECT COUNT(*) FROM relations), (SELECT COALESCE(MAX(id),0) FROM relations), "
                "(SELECT COUNT(*) FROM categories), (SELECT COUNT(*) FROM links), (SELECT COALESCE(MAX(id),0) FROM links), "
                "(SELECT COUNT(*) FROM documents WHERE archived=1)").fetchone()
        with self.store.lock:
            m = self.store.db.execute("SELECT COUNT(*), COALESCE(MAX(updated),0) FROM memories").fetchone()
        return "-".join(str(x) for x in (*k, *m))

    def family_of_category(self, cid, cats):
        c = cats.get(cid)
        if not c:
            return "nonclasse"
        if c["name"] in SUB:
            return SUB[c["name"]]
        top = cats.get(c["parent_id"]) if c["parent_id"] else c
        return TOP.get(top["name"], "documents") if top else "nonclasse"

    def build(self):
        ver = self.version()
        if self._cache[0] == ver:
            return self._cache[1]
        cats = {c["id"]: c for c in self.kb.categories()}
        anchors = {f[0]: _fib(i, len(FAMILIES)) for i, f in enumerate(FAMILIES)}
        with self.kb.lock:
            docs = self.kb.db.execute(
                "SELECT id, title, description, keywords, category_id, reliability, n_chunks, vec_local, updated, "
                "source_type FROM documents WHERE archived=0 ORDER BY updated DESC").fetchall()
            links = self.kb.db.execute("SELECT a, b, kind FROM links").fetchall()
            runs = {r["id"]: r["question"] for r in self.kb.db.execute("SELECT id, question FROM research")}
            rels = self.kb.db.execute("SELECT src, dst, doc_id FROM relations").fetchall()
            ents = self.kb.db.execute("SELECT id, name, type FROM entities").fetchall()
            firsts = {r[0]: r[1] for r in self.kb.db.execute(
                "SELECT doc_id, content FROM chunks WHERE ord=0")}
        with self.store.lock:
            mems = self.store.db.execute(
                "SELECT id, content, category, importance, vec_local, updated FROM memories ORDER BY updated DESC").fetchall()

        total = len(docs) + len(mems) + len(ents)
        truncated = None
        if total > MAX_NODES:
            share = MAX_NODES / total
            deg = {}
            for r in rels:
                deg[r["src"]] = deg.get(r["src"], 0) + 1
                deg[r["dst"]] = deg.get(r["dst"], 0) + 1
            docs = docs[:max(1, int(len(docs) * share))]
            mems = mems[:max(1, int(len(mems) * share))]
            ents = sorted(ents, key=lambda e: -deg.get(e["id"], 0))[:max(1, int(len(ents) * share))]
            truncated = {"shown": len(docs) + len(mems) + len(ents), "total": total}

        nodes, index, edges, pos = [], {}, [], {}

        general = self.kb.general_category_ids()
        vec_of = {}

        def add(nid, kind, label, family, p, size, module=""):
            index[nid] = len(nodes)
            pos[nid] = p
            nodes.append({"id": nid, "k": kind, "l": label[:70], "f": family, "p": [_r(p[0]), _r(p[1]), _r(p[2])],
                          "s": size, "m": module})

        def link(a, b, t):
            if a in index and b in index and a != b:
                edges.append([index[a], index[b], t])

        # --- familles et sous-catégories
        doc_family, doc_group = {}, {}
        for d in docs:
            fam = self.family_of_category(d["category_id"], cats)
            doc_family[d["id"]] = fam
            c = cats.get(d["category_id"])
            doc_group[d["id"]] = f"c:{c['id']}" if c and c["parent_id"] else f"f:{fam}"
        mem_family = {m["id"]: "projets" if m["category"] == "projets" else "perso" for m in mems}
        counts = {}
        for fam in list(doc_family.values()) + list(mem_family.values()):
            counts[fam] = counts.get(fam, 0) + 1
        for key, label, _ in FAMILIES:
            add(f"f:{key}", "family", label, key, anchors[key], 3.2 if counts.get(key) else 1.6)

        sub_by_family = {}
        for g in sorted({g for g in doc_group.values() if g.startswith("c:")}, key=lambda s: int(s[2:])):
            fam = self.family_of_category(int(g[2:]), cats)
            sub_by_family.setdefault(fam, []).append(g)
        group_anchor = {f"f:{k}": anchors[k] for k, _, _ in FAMILIES}
        for fam, groups in sub_by_family.items():
            for i, g in enumerate(groups):
                ang = 2 * math.pi * i / len(groups) + 0.6
                p = _offset(anchors[fam], 0.27 * math.cos(ang), 0.27 * math.sin(ang))
                group_anchor[g] = p
                add(g, "category", cats[int(g[2:])]["name"], fam, p, 2.2)
                link(f"f:{fam}", g, 0)
        # groupes de souvenirs : une zone par catégorie de mémoire, autour de l'ancre de la famille
        mem_cats = sorted({m["category"] for m in mems if m["category"] != "projets"})
        for i, c in enumerate(mem_cats):
            ang = 2 * math.pi * i / max(1, len(mem_cats))
            group_anchor[f"mc:{c}"] = _offset(anchors["perso"], 0.2 * math.cos(ang), 0.2 * math.sin(ang))

        sizes = {}
        for g in list(doc_group.values()) + [f"mc:{m['category']}" for m in mems]:
            sizes[g] = sizes.get(g, 0) + 1

        def place(group, vec, depth_bias=0.0):
            x, y, z = _project(vec)
            spread = min(0.2, 0.045 + 0.011 * math.sqrt(sizes.get(group, 1)))
            return _offset(group_anchor[group], spread * x / 1.4, spread * y / 1.4, 1.0 + 0.035 * z + depth_bias)

        text_of = {}
        for d in docs:
            vec = unpack(d["vec_local"]) if d["vec_local"] else local_vector(d["title"])
            nid = f"d:{d['id']}"
            module = ("recherche" if d["source_type"] == "recherche"
                      else "generale" if d["category_id"] in general else "bibliotheque")
            add(nid, "doc", d["title"], doc_family[d["id"]], place(doc_group[d["id"]], vec),
                round(1.0 + min(1.4, math.log10(1 + d["n_chunks"])) + 0.1 * d["reliability"], 2), module)
            vec_of[nid] = vec
            link(doc_group[d["id"]], nid, 0)
            text_of[nid] = f"{d['title']} {d['keywords']} {d['description']} {(firsts.get(d['id']) or '')[:500]}"
        for m in mems:
            vec = unpack(m["vec_local"]) if m["vec_local"] else local_vector(m["content"])
            fam = mem_family[m["id"]]
            nid = f"m:{m['id']}"
            group = "f:projets" if fam == "projets" else f"mc:{m['category']}"
            if group == "f:projets":
                sizes.setdefault(group, counts.get("projets", 1))
            add(nid, "memory", m["content"], fam, place(group, vec, -0.04), round(0.9 + 0.22 * m["importance"], 2),
                "projets" if fam == "projets" else "perso")
            vec_of[nid] = vec
            link(f"f:{fam}", nid, 0)
            text_of[nid] = m["content"]

        # --- concepts du graphe : au barycentre de leurs voisins, à l'intérieur de la sphère
        ent_ids = {e["id"] for e in ents}
        neigh, ent_doc = {}, {}
        for r in rels:
            if r["src"] in ent_ids and r["dst"] in ent_ids:
                neigh.setdefault(r["src"], set()).add(f"e:{r['dst']}")
                neigh.setdefault(r["dst"], set()).add(f"e:{r['src']}")
            if r["doc_id"] and f"d:{r['doc_id']}" in index:
                for e in (r["src"], r["dst"]):
                    if e in ent_ids:
                        neigh.setdefault(e, set()).add(f"d:{r['doc_id']}")
                        ent_doc.setdefault(e, r["doc_id"])
        epos = {}
        for e in ents:
            h = zlib.crc32(e["name"].encode())
            epos[e["id"]] = tuple(0.45 * c for c in _fib(h % 997, 997))
        for _ in range(3):
            for e in ents:
                pts = [pos.get(n) or epos.get(int(n[2:])) for n in neigh.get(e["id"], ())]
                pts = [p for p in pts if p]
                if pts:
                    mx, my, mz = (sum(p[i] for p in pts) / len(pts) for i in range(3))
                    j = _fib(zlib.crc32(e["name"].encode()) % 997, 997)
                    d = _norm((mx + 0.12 * j[0], my + 0.12 * j[1], mz + 0.12 * j[2]))
                    depth = 0.58 + 0.1 * (zlib.crc32(e["name"].encode()[::-1]) % 100) / 100
                    epos[e["id"]] = (d[0] * depth, d[1] * depth, d[2] * depth)
        for e in ents:
            fam = doc_family.get(ent_doc.get(e["id"]), "nonclasse")
            src_doc = index.get(f"d:{ent_doc.get(e['id'])}")
            add(f"e:{e['id']}", "concept", e["name"], fam, epos[e["id"]],
                round(1.0 + min(1.2, 0.2 * len(neigh.get(e["id"], ()))), 2),
                nodes[src_doc]["m"] if src_doc is not None else "bibliotheque")
        seen = set()
        for r in rels:
            a, b = f"e:{r['src']}", f"e:{r['dst']}"
            link(a, b, 1)
            if r["doc_id"]:
                for e in (a, b):
                    if (e, r["doc_id"]) not in seen:
                        seen.add((e, r["doc_id"]))
                        link(e, f"d:{r['doc_id']}", 2)

        # --- proximité de contenu : éléments qui partagent des mots rares
        inv = {}
        for nid, text in text_of.items():
            for w in stems(text):
                if len(w) > 3:
                    inv.setdefault(w, []).append(nid)
        shared = {}
        for w, ids in inv.items():
            if 2 <= len(ids) <= 12:
                for i in range(len(ids)):
                    for j in range(i + 1, len(ids)):
                        key = (ids[i], ids[j])
                        shared[key] = shared.get(key, 0) + 1
        best = {}
        for (a, b), n in shared.items():
            need = 2 if a[0] == "m" or b[0] == "m" else 3
            if n >= need:
                best.setdefault(a, []).append((n, b))
                best.setdefault(b, []).append((n, a))
        done = set()
        for a, lst in best.items():
            for n, b in sorted(lst, reverse=True)[:2]:
                key = (a, b) if a < b else (b, a)
                if key not in done:
                    done.add(key)
                    link(a, b, 3)

        # --- connexions créées à l'intégration (voisinage de sens) et recherches du chercheur
        by_run = {}
        for l in links:
            if l["kind"] == "recherche":
                run, doc = (l["b"], l["a"]) if l["b"].startswith("r:") else (l["a"], l["b"])
                if doc in index:
                    by_run.setdefault(run, []).append(doc)
        for run, members in by_run.items():
            rid = int(run[2:])
            if rid not in runs:
                continue
            c = _norm(tuple(sum(pos[m][i] for m in members) / len(members) for i in range(3)))
            add(run, "research", runs[rid], nodes[index[members[0]]]["f"], (c[0] * 1.07, c[1] * 1.07, c[2] * 1.07),
                2.4, "recherche")
            for m in members:
                link(run, m, 6)
        for l in links:
            if l["kind"] == "sémantique":
                link(l["a"], l["b"], 4)

        # --- mémoire vectorielle : zones sémantiques (k-moyennes sur les vecteurs de contenu)
        zones = self._zones(vec_of, text_of, pos, nodes, index)
        for z in zones:
            add(z["id"], "cluster", z["label"], z["family"], z["pos"], 2.6, "vectorielle")
            for m in z["members"][:40]:
                link(z["id"], m, 5)

        fam_counts, mod_counts = {}, {}
        for n in nodes:
            if n["k"] in ("doc", "memory", "concept"):
                fam_counts[n["f"]] = fam_counts.get(n["f"], 0) + 1
            if n["m"]:
                mod_counts[n["m"]] = mod_counts.get(n["m"], 0) + 1
        out = {
            "version": ver, "nodes": nodes, "edges": edges, "truncated": truncated,
            "families": [{"key": k, "label": l, "color": c, "count": fam_counts.get(k, 0)} for k, l, c in FAMILIES],
            "modules": [{"key": k, "label": l, "color": c, "count": mod_counts.get(k, 0)} for k, l, c in MODULES],
            "stats": {"documents": len(docs), "memories": len(mems), "concepts": len(ents), "relations": len(rels),
                      "links": len(edges), "connections": sum(1 for e in edges if e[2] == 4), "zones": len(zones),
                      "research": len(by_run)},
        }
        self._cache = (ver, out)
        return out

    @staticmethod
    def _zones(vec_of, text_of, pos, nodes, index):
        """Regroupe documents et souvenirs par proximité de leurs vecteurs. Les zones ne sont pas des données
        ajoutées : chacune désigne des éléments existants, et son nom est fait de leurs mots les plus distinctifs."""
        ids = sorted(vec_of)
        if len(ids) < 12:
            return []
        proj = {i: vectors.project(vec_of[i], 24) for i in ids}
        k = max(2, min(14, round(math.sqrt(len(ids) / 8))))
        sample = ids if len(ids) <= 1500 else ids[::len(ids) // 1500 + 1]
        _, centers = vectors.kmeans([proj[i] for i in sample], k)
        groups = {}
        for i in ids:
            c = max(range(len(centers)), key=lambda j: vectors.dot(proj[i], centers[j]))
            groups.setdefault(c, []).append((vectors.dot(proj[i], centers[c]), i))
        df, words, forms = {}, {}, {}
        for i in ids:
            ws = set()
            for word in re.findall(r"[A-Za-zÀ-ÿ]{4,}", text_of.get(i, "")):
                for st in stems(word):
                    ws.add(st)
                    f = forms.setdefault(st, {})
                    f[word.lower()] = f.get(word.lower(), 0) + 1
            words[i] = ws
            for w in ws:
                df[w] = df.get(w, 0) + 1
        shown = lambda st: max(sorted(forms[st]), key=forms[st].get)   # le mot tel qu'il est écrit, accents compris
        out = []
        for c, members in sorted(groups.items()):
            if len(members) < 3:
                continue
            members.sort(reverse=True)
            tf = {}
            for _, i in members:
                for w in words[i]:
                    tf[w] = tf.get(w, 0) + 1
            top = sorted((w for w in tf if tf[w] >= 2),
                         key=lambda w: (-(tf[w] / len(members)) * math.log(1 + len(ids) / df[w]), w))[:3]
            if not top:
                continue
            ctr = tuple(sum(pos[i][a] for _, i in members) / len(members) for a in range(3))
            n = _norm(ctr)
            fams = {}
            for _, i in members:
                f = nodes[index[i]]["f"]
                fams[f] = fams.get(f, 0) + 1
            out.append({"id": f"v:{len(out) + 1}", "label": "Zone : " + " · ".join(shown(w) for w in top), "family": max(fams, key=fams.get),
                        "pos": (n[0] * 0.82, n[1] * 0.82, n[2] * 0.82), "members": [i for _, i in members],
                        "size": len(members)})
        return out

    def _connections(self, nid):
        data = self.build()
        label = {n["id"]: n for n in data["nodes"]}
        out = []
        for l in self.kb.links_of(nid)[:20]:
            other = l["b"] if l["a"] == nid else l["a"]
            if other in label:
                out.append({"id": other, "label": label[other]["l"], "kind": l["kind"], "score": l["score"]})
        return out

    # ---------- données exactes derrière un nœud ----------
    def detail(self, nid):
        kind, _, raw = nid.partition(":")
        if kind == "r" and raw.isdigit() and self.research:
            r = self.research.get(int(raw))
            if not r:
                return None
            done = [f for f in r["findings"] if f["decision"] == "intégré"]
            return {"id": nid, "kind": "research", "name": r["question"], "description": r["summary"],
                    "category": r["category"] or "Recherche du chercheur autonome", "keywords": "",
                    "source": f"{len(r['sources'])} source(s) consultée(s)", "added": r["created"], "verified": r["finished"],
                    "relations": [], "research_id": r["id"], "module": "recherche",
                    "children": [{"id": f"d:{f['doc_id']}", "label": f["claim"][:90], "kind": "doc"} for f in done],
                    "children_total": len(done)}
        if kind == "v":
            data = self.build()
            node = next((n for n in data["nodes"] if n["id"] == nid), None)
            if not node:
                return None
            idx = data["nodes"].index(node)
            kids = [data["nodes"][b if a == idx else a] for a, b, t in data["edges"] if t == 5 and idx in (a, b)]
            return {"id": nid, "kind": "cluster", "name": node["l"], "module": "vectorielle",
                    "description": "Zone de la mémoire vectorielle : ces éléments ont des contenus proches (regroupement "
                                   "calculé sur leurs vecteurs). Le nom reprend leurs mots les plus distinctifs.",
                    "category": "Mémoire vectorielle", "keywords": "", "source": "", "added": None, "verified": None,
                    "relations": [], "children": [{"id": k["id"], "label": k["l"], "kind": k["k"]} for k in kids],
                    "children_total": len(kids)}
        if kind == "m" and raw.isdigit():
            m = self.store.get_memory(int(raw))
            if not m:
                return None
            return {"id": nid, "kind": "memory", "name": m["content"][:90], "description": m["content"],
                    "category": "Projets" if m["category"] == "projets" else f"Mémoire personnelle › {m['category']}",
                    "keywords": ", ".join(sorted(stems(m["content"]))[:8]),
                    "source": "appris en conversation" if m["source"] == "auto" else "ajouté à la main",
                    "added": m["created"], "verified": m["updated"], "importance": m["importance"],
                    "expires_at": m["expires_at"], "use_count": m["use_count"], "relations": [],
                    "module": "projets" if m["category"] == "projets" else "perso", "connections": self._connections(nid)}
        if kind == "d" and raw.isdigit():
            d = self.kb.get(int(raw))
            if not d:
                return None
            chunks = self.kb.chunks_of(d["id"])
            with self.kb.lock:
                rel = [dict(r) for r in self.kb.db.execute(
                    "SELECT a.name AS src, r.label, b.name AS dst, r.src AS src_id, r.dst AS dst_id FROM relations r "
                    "JOIN entities a ON a.id=r.src JOIN entities b ON b.id=r.dst WHERE r.doc_id=? LIMIT 40", (d["id"],))]
            return {"id": nid, "kind": "doc", "name": d["title"],
                    "description": d["description"] or (chunks[0]["content"][:420] + "…" if chunks else ""),
                    "category": d["category"], "keywords": d["keywords"], "source": d["source"] or d["source_type"],
                    "source_type": d["source_type"], "license": d["license"], "added": d["imported"],
                    "verified": d["verified"], "reliability": d["reliability"], "passages": d["n_chunks"],
                    "doc_id": d["id"], "relations": rel, "module": d["module"], "connections": self._connections(nid),
                    "extracts": self.kb.stats()["extracts"] and {
                        k: sum(1 for e in self.kb.extracts_of(d["id"]) if e["kind"] == k)
                        for k in ("formule", "tableau", "donnée")}}
        if kind == "e" and raw.isdigit():
            with self.kb.lock:
                e = self.kb.db.execute("SELECT * FROM entities WHERE id=?", (int(raw),)).fetchone()
                if not e:
                    return None
                rel = [dict(r) for r in self.kb.db.execute(
                    "SELECT r.id, a.name AS src, r.label, b.name AS dst, r.src AS src_id, r.dst AS dst_id, r.doc_id, "
                    "d.title, d.imported, d.verified FROM relations r JOIN entities a ON a.id=r.src "
                    "JOIN entities b ON b.id=r.dst LEFT JOIN documents d ON d.id=r.doc_id "
                    "WHERE r.src=? OR r.dst=? LIMIT 60", (e["id"], e["id"]))]
            docs = {r["doc_id"]: r for r in rel if r["doc_id"]}
            return {"id": nid, "kind": "concept", "name": e["name"],
                    "description": f"Concept du graphe de connaissances, relié à {len(rel)} autre(s) élément(s).",
                    "category": e["type"], "keywords": "",
                    "source": ", ".join(r["title"] for r in docs.values()) or "relation ajoutée à la main",
                    "docs": [{"doc_id": k, "title": v["title"]} for k, v in docs.items()],
                    "added": min((r["imported"] for r in docs.values()), default=None),
                    "verified": max((r["verified"] for r in docs.values()), default=None), "relations": rel}
        if kind in ("f", "c"):
            data = self.build()
            node = next((n for n in data["nodes"] if n["id"] == nid), None)
            if not node:
                return None
            idx = data["nodes"].index(node)
            kids = [data["nodes"][b if a == idx else a] for a, b, t in data["edges"] if t == 0 and idx in (a, b)]
            kids = [k for k in kids if k["id"] != nid and not (kind == "c" and k["k"] == "family")]
            return {"id": nid, "kind": "family" if kind == "f" else "category", "name": node["l"],
                    "description": f"{len(kids)} élément(s) rattaché(s) directement.", "category": "Famille de connaissances"
                    if kind == "f" else next(f["label"] for f in data["families"] if f["key"] == node["f"]),
                    "keywords": "", "source": "", "added": None, "verified": None, "relations": [],
                    "children": [{"id": k["id"], "label": k["l"], "kind": k["k"]} for k in kids[:80]],
                    "children_total": len(kids)}
        return None
