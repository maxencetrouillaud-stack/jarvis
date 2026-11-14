"""Collecte et indexation en arrière-plan : fichiers, pages web, Wikipédia, actualisation, graphe.

Rien n'est inventé : chaque document vient d'un fichier de l'utilisateur ou d'une adresse précise,
enregistrée comme source avec sa licence et sa date.
"""
import hashlib
import os
import ipaddress
import json
import queue
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import extract, library
from .brain import parse_json
from .knowledge import Duplicate
from .providers import ProviderError

MAX_FETCH = 8 * 1024 * 1024
UA = "JarvisPersonnel/0.4 (assistant personnel local ; usage individuel)"

OFFICIAL = ("docs.python.org", "developer.mozilla.org", "learn.microsoft.com", "iso.org", "afnor.org",
            "legifrance.gouv.fr", "service-public.fr", "europa.eu", "nist.gov", "ietf.org", "w3.org",
            "rust-lang.org", "typescriptlang.org", "freecad.org", "postgresql.org", "sqlite.org", "who.int")

STARTER = {  # titres d'articles de Wikipédia en français, proposés comme premier corpus (non importés d'office)
    "Génie mécanique": ["Génie mécanique", "Liaison (mécanique)", "Roulement mécanique", "Engrenage",
                        "Arbre (mécanique)", "Ajustement (mécanique)", "Cotation fonctionnelle", "Visserie"],
    "Résistance des matériaux": ["Résistance des matériaux", "Contrainte (mécanique)", "Module de Young",
                                 "Essai de traction", "Limite d'élasticité", "Flambage", "Fatigue (matériau)",
                                 "Loi de Hooke", "Coefficient de Poisson", "Moment quadratique"],
    "Matériaux et métallurgie": ["Acier", "Acier inoxydable", "Aluminium", "Fonte (métallurgie)",
                                 "Matériau composite", "Trempe (métallurgie)", "Dureté (matériau)"],
    "Usinage": ["Usinage", "Fraisage", "Tournage mécanique", "Perçage", "Rectification (mécanique)"],
    "CNC et programmation ISO": ["Machine-outil à commande numérique", "Programmation de commande numérique"],
    "Soudage, tôlerie, chaudronnerie": ["Soudage", "Soudage TIG", "Soudage MIG-MAG", "Chaudronnerie", "Tôlerie"],
    "Impression 3D": ["Impression 3D", "Dépôt de fil fondu"],
    "CAO, FAO, DAO": ["Conception assistée par ordinateur", "Fabrication assistée par ordinateur", "FreeCAD",
                      "SolidWorks", "CATIA"],
    "Simulation (FEM, CFD, fatigue, vibrations)": ["Méthode des éléments finis", "Mécanique des fluides numérique",
                                                   "Vibration"],
    "Métrologie": ["Métrologie", "Pied à coulisse", "Rugosité"],
    "Automatisme et robotique": ["Automate programmable industriel", "Robot industriel", "Grafcet",
                                 "Asservissement (automatique)"],
    "Mécanique": ["Mécanique newtonienne", "Moment d'une force", "Frottement", "Énergie cinétique"],
    "Thermodynamique": ["Thermodynamique", "Premier principe de la thermodynamique"],
    "Mathématiques": ["Dérivée", "Intégration (mathématiques)", "Matrice (mathématiques)", "Trigonométrie"],
    "Langages de programmation": ["Python (langage)", "JavaScript", "TypeScript", "C++", "Rust (langage)"],
    "Bases de données et API": ["Base de données", "Interface de programmation", "SQL"],
    "Intelligence artificielle": ["Intelligence artificielle", "Apprentissage automatique", "Apprentissage profond",
                                  "Réseau de neurones artificiels"],
    "LLM, RAG, embeddings": ["Grand modèle de langage", "Génération augmentée de récupération", "Plongement lexical"],
}


class CollectError(Exception):
    pass


def _check_host(url, allow_private):
    u = urllib.parse.urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise CollectError("Adresse refusée : seules les adresses http(s) sont acceptées")
    if allow_private:
        return
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80))
    except OSError:
        raise CollectError(f"Site introuvable : {u.hostname}") from None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise CollectError("Adresse refusée : elle pointe vers le réseau local")


class _Redirects(urllib.request.HTTPRedirectHandler):
    def __init__(self, allow_private):
        self.allow_private = allow_private

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_host(newurl, self.allow_private)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url, allow_private=False, timeout=30, headers=None):
    """Télécharge une page ou un fichier public. -> (octets, type de contenu, adresse finale)"""
    _check_host(url, allow_private)
    handlers = [_Redirects(allow_private)]
    if allow_private:
        handlers.append(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/pdf,text/plain,*/*",
                                               **(headers or {})})
    try:
        with urllib.request.build_opener(*handlers).open(req, timeout=timeout) as r:
            data = r.read(MAX_FETCH + 1)
            if len(data) > MAX_FETCH:
                raise CollectError("Page trop volumineuse (8 Mo au maximum)")
            return data, (r.headers.get("Content-Type") or "").split(";")[0].strip().lower(), r.geturl()
    except urllib.error.HTTPError as e:
        raise CollectError(f"Le site a répondu {e.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise CollectError(f"Téléchargement impossible ({getattr(e, 'reason', e)})") from None


def reliability_for(url):
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if host.endswith((".gouv.fr", ".gov", ".edu", ".ac.uk")) or any(host == d or host.endswith("." + d) for d in OFFICIAL):
        return 4
    if host.endswith("wikipedia.org"):
        return 3
    return 2


class Wikipedia:
    LICENSE = "CC BY-SA 4.0 — Wikipédia, contributeurs de l'article"

    def __init__(self, api="https://{lang}.wikipedia.org/w/api.php", allow_private=False):
        self.api, self.allow_private = api, allow_private

    def _get(self, lang, params):
        if not re.fullmatch(r"[a-z]{2,3}", lang):
            raise CollectError("Langue invalide")
        url = self.api.format(lang=lang) + "?" + urllib.parse.urlencode({**params, "format": "json", "formatversion": "2"})
        data, _, _ = fetch(url, self.allow_private, 25)
        try:
            return json.loads(data.decode("utf-8"))
        except ValueError:
            raise CollectError("Réponse illisible de Wikipédia") from None

    def search(self, query, lang="fr", limit=8):
        j = self._get(lang, {"action": "query", "list": "search", "srsearch": query, "srlimit": limit})
        out = []
        for s in j.get("query", {}).get("search", []):
            out.append({"title": s["title"], "lang": lang,
                        "snippet": re.sub(r"<[^>]+>", "", s.get("snippet", "")).replace("&quot;", '"'),
                        "url": f"https://{lang}.wikipedia.org/wiki/" + urllib.parse.quote(s["title"].replace(" ", "_"))})
        return out

    def article(self, title, lang="fr"):
        j = self._get(lang, {"action": "query", "prop": "extracts|info|revisions", "explaintext": "1",
                             "exsectionformat": "wiki", "redirects": "1", "inprop": "url", "rvprop": "timestamp",
                             "titles": title})
        pages = j.get("query", {}).get("pages", [])
        if not pages or pages[0].get("missing") or not pages[0].get("extract", "").strip():
            raise CollectError(f"Article introuvable sur Wikipédia ({lang}) : {title}")
        p = pages[0]
        text = re.sub(r"^(=+) *(.+?) *=+ *$", lambda m: "#" * max(1, len(m.group(1)) - 1) + " " + m.group(2),
                      p["extract"], flags=re.M)
        return {"title": p["title"], "text": extract.clean(text),
                "url": p.get("fullurl") or f"https://{lang}.wikipedia.org/wiki/" + urllib.parse.quote(p["title"].replace(" ", "_")),
                "revised": (p.get("revisions") or [{}])[0].get("timestamp", "")[:10]}


CATEGORIZE_PROMPT = """Classe ce document dans UNE des catégories ci-dessous. Réponds uniquement par le nom exact
de la catégorie (la partie après « › »), ou « Non classé » si aucune ne convient.

Catégories :
{cats}

Titre : {title}
Début du document :
{text}"""

GRAPH_PROMPT = """Relève les relations entre concepts EXPLICITEMENT énoncées dans le texte ci-dessous
(concepts, formules, matériaux, machines, procédés, normes, technologies, organisations).
N'ajoute rien qui ne soit pas écrit dans le texte. 25 relations au maximum, les plus structurantes.
Noms courts (1 à 4 mots), au singulier. Relation = verbe ou expression courte (« a pour propriété », « est mesuré par »,
« est défini par la norme », « utilise », « est un type de »…).
types possibles : concept, formule, matériau, machine, procédé, norme, technologie, organisation.
Réponds uniquement par un tableau JSON :
[{{"source": "acier", "type_source": "matériau", "relation": "a pour propriété", "cible": "résistance à la traction", "type_cible": "concept"}}]

Titre : {title}
Texte :
{text}"""


class KnowledgeService:
    def __init__(self, kb, brain, settings, home: Path, wikipedia=None, allow_private=False):
        self.kb, self.brain, self.settings, self.home = kb, brain, settings, home
        self.wiki = wikipedia or Wikipedia()
        self.allow_private = allow_private
        self.dir = home / "knowledge"
        (self.dir / "originals").mkdir(parents=True, exist_ok=True)
        (self.dir / "incoming").mkdir(parents=True, exist_ok=True)
        for f in (self.dir / "incoming").iterdir():   # restes d'imports en échec, vieux de plus d'une semaine
            if f.is_file() and time.time() - f.stat().st_mtime > 7 * 86400:
                f.unlink(missing_ok=True)
        self.hub = None  # branché par l'application : crée les connexions de chaque nouvelle connaissance
        self.q = queue.Queue()
        self.idle = threading.Event()
        self.idle.set()
        self._scan_at = 0.0
        threading.Thread(target=self._loop, daemon=True).start()
        threading.Thread(target=self._watch_loop, daemon=True).start()
        with self.kb.lock:
            old = self.kb.db.execute("SELECT 1 FROM chunks WHERE sig_l IS NULL LIMIT 1").fetchone()
        if old:  # base créée avant la version 0.4 : l'index de voisinage est calculé en arrière-plan
            self._submit("index", "Mise à niveau de l'index vectoriel", {})

    # ----- dépôt des tâches -----
    def _submit(self, kind, label, payload):
        jid = self.kb.add_job(kind, label, payload)
        self.idle.clear()
        self.q.put(jid)
        return jid

    def submit_file(self, name, data: bytes, opts=None):
        name = re.sub(r"[^\w.\- ()\[\]àâäéèêëîïôöùûüçÀÂÉÈÊÎÔÙÛÇ']", "_", Path(name).name)[:120] or "document"
        tmp = self.dir / "incoming" / f"{int(time.time() * 1000)}_{hashlib.sha1(data).hexdigest()[:8]}_{name}"
        tmp.write_bytes(data)
        return self._submit("fichier", name, {"path": str(tmp), "name": name, **(opts or {})})

    def submit_url(self, url, opts=None):
        _check_host(url, True)  # forme de l'adresse ; la vérification réseau a lieu au téléchargement
        return self._submit("page web", url[:200], {"url": url, **(opts or {})})

    def submit_wikipedia(self, title, lang="fr", opts=None):
        return self._submit("wikipédia", title, {"title": title, "lang": lang, **(opts or {})})

    def submit_refresh(self, doc_id):
        d = self.kb.get(doc_id)
        if not d:
            raise ValueError("Document introuvable")
        if d["source_type"] not in ("page web", "wikipédia"):
            raise ValueError("Seuls les documents venus du web peuvent être actualisés automatiquement")
        return self._submit("actualisation", d["title"], {"doc_id": doc_id})

    def submit_graph(self, doc_id):
        d = self.kb.get(doc_id)
        if not d:
            raise ValueError("Document introuvable")
        return self._submit("graphe", d["title"], {"doc_id": doc_id})

    def submit_reindex(self):
        if not self.brain.embedding_ref():
            raise ProviderError("Aucun modèle d'embeddings n'est réglé (onglet Modèles).")
        return self._submit("embeddings", "Indexation sémantique des passages", {})

    def submit_links(self):
        return self._submit("connexions", "Recalcul des connexions entre connaissances", {})

    def retry(self, jid):
        """Relance une tâche en erreur (même fichier, même adresse)."""
        j = self.kb.retry_info(jid)
        if not j or j["status"] != "erreur":
            raise ValueError("Cette tâche ne peut pas être relancée")
        p = json.loads(j["payload"] or "{}")
        if j["kind"] == "fichier" and not Path(p.get("path", "")).exists():
            raise ValueError("Le fichier n'est plus disponible : importez-le à nouveau")
        return self._submit(j["kind"], j["label"], p)

    def wait(self, timeout=60):
        return self.idle.wait(timeout)

    # ----- dossier surveillé -----
    def folder_state(self):
        folder = (self.settings.get("library_folder") or "").strip()
        with self.kb.lock:
            g = lambda q: self.kb.db.execute(q).fetchone()[0]
            return {"folder": folder, "exists": bool(folder) and Path(folder).is_dir(),
                    "watch": bool(self.settings.get("library_watch")), "last_scan": self._scan_at or None,
                    "files": g("SELECT COUNT(*) FROM watched_files"),
                    "indexed": g("SELECT COUNT(*) FROM watched_files WHERE doc_id IS NOT NULL"),
                    "errors": g("SELECT COUNT(*) FROM watched_files WHERE status LIKE 'erreur%'"),
                    "missing": g("SELECT COUNT(*) FROM watched_files WHERE status='fichier absent du dossier'")}

    def scan_folder(self, limit=20000):
        """Parcourt le dossier de la bibliothèque : indexe les nouveaux fichiers, réindexe ceux qui ont changé.

        Rien n'est supprimé de la base quand un fichier disparaît du dossier : il est seulement signalé.
        """
        folder = (self.settings.get("library_folder") or "").strip()
        if not folder:
            raise ValueError("Aucun dossier de bibliothèque n'est réglé")
        root = Path(folder)
        if not root.is_absolute() or not root.is_dir():
            raise ValueError(f"Dossier introuvable : {folder}")
        with self.kb.lock:
            known = {r["path"]: dict(r) for r in self.kb.db.execute("SELECT * FROM watched_files")}
            queued = {json.loads(r[0] or "{}").get("path") for r in self.kb.db.execute(
                "SELECT payload FROM jobs WHERE kind='dossier' AND status IN ('en file','en cours')")}
        seen, new, changed, skipped = set(), 0, 0, 0
        now = time.time()
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith((".", "$", "~"))]
            for name in filenames:
                if len(seen) >= limit:
                    break
                path = Path(dirpath) / name
                if name.startswith(("~$", ".")) or path.suffix.lower() not in extract.SUPPORTED:
                    continue
                try:
                    st = path.stat()
                except OSError:
                    continue
                key = str(path)
                seen.add(key)
                if st.st_size > 30 * 1024 * 1024 or st.st_size == 0:
                    skipped += 1
                    self._watched(key, st.st_size, st.st_mtime, None, None, "erreur : fichier vide ou de plus de 30 Mo")
                    continue
                k = known.get(key)
                same = k and k["size"] == st.st_size and abs((k["mtime"] or 0) - st.st_mtime) < 1
                if key in queued or (same and k["status"] != "à indexer"):
                    continue
                self._watched(key, st.st_size, st.st_mtime, k["sha256"] if k else None, k["doc_id"] if k else None,
                              "à indexer")
                self._submit("dossier", name, {"path": key, "name": name})
                if k and k["doc_id"]:
                    changed += 1
                else:
                    new += 1
        missing = 0
        with self.kb.lock:
            for key in known:
                if key not in seen and key.startswith(str(root)):
                    self.kb.db.execute("UPDATE watched_files SET status='fichier absent du dossier' WHERE path=?", (key,))
                    missing += 1
            self.kb.db.commit()
        self._scan_at = now
        return {"files": len(seen), "new": new, "changed": changed, "missing": missing, "skipped": skipped}

    def _watched(self, path, size, mtime, sha, doc_id, status):
        with self.kb.lock:
            self.kb.db.execute(
                "INSERT INTO watched_files(path,size,mtime,sha256,doc_id,status,seen) VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(path) DO UPDATE SET size=excluded.size, mtime=excluded.mtime, sha256=excluded.sha256, "
                "doc_id=excluded.doc_id, status=excluded.status, seen=excluded.seen",
                (path, size, mtime, sha, doc_id, status, time.time()))
            self.kb.db.commit()

    def _watch_loop(self):
        while True:
            time.sleep(30)
            try:
                every = max(1, int(self.settings.get("library_scan_minutes", 10))) * 60
                if self.settings.get("library_watch") and self.settings.get("library_folder") \
                        and time.time() - self._scan_at >= every and self.idle.is_set():
                    self.scan_folder()
            except Exception:
                self._scan_at = time.time()  # dossier indisponible : nouvel essai au prochain cycle

    def _do_folder(self, p):
        path = Path(p["path"])
        try:
            data = path.read_bytes()
            st = path.stat()
        except OSError as e:
            self._watched(str(path), 0, 0, None, None, f"erreur : {e}")
            raise CollectError(f"Fichier illisible : {e}") from None
        sha = hashlib.sha256(data).hexdigest()
        with self.kb.lock:
            k = self.kb.db.execute("SELECT * FROM watched_files WHERE path=?", (str(path),)).fetchone()
        doc_id = k["doc_id"] if k else None
        try:
            title, segs = extract.segments(p["name"], data)
            if doc_id and self.kb.get(doc_id):
                res = self.kb.reingest(doc_id, segs, self._embedder())
                msg = (f"fichier modifié : version {res['version']} indexée, l'ancienne est archivée"
                       if res["changed"] else "contenu inchangé")
                if res["changed"]:
                    msg += " ; " + self._after_import(doc_id)
            else:
                original = self.dir / "originals" / f"{sha[:16]}{path.suffix.lower()}"
                doc_id = self.kb.ingest(title or path.stem, segs, "fichier", str(path), None, 3, "",
                                        original=original.name, embed=self._embedder())
                original.write_bytes(data)
                msg = self._after_import(doc_id, len(segs) > 1 and segs[0][0].startswith("p."))
        except Duplicate as e:
            self._watched(str(path), st.st_size, st.st_mtime, sha, e.doc_id, "déjà dans la base (même contenu)")
            raise
        except (extract.ExtractError, ValueError) as e:
            self._watched(str(path), st.st_size, st.st_mtime, sha, doc_id, f"erreur : {e}")
            raise
        self._watched(str(path), st.st_size, st.st_mtime, sha, doc_id, "indexé")
        return msg

    # ----- exécution -----
    def _loop(self):
        while True:
            jid = self.q.get()
            try:
                self._run(jid)
            except Exception as e:  # une tâche en échec ne doit jamais arrêter la file
                self.kb.job_update(jid, "erreur", f"{type(e).__name__} : {e}")
            finally:
                if self.q.empty():
                    self.idle.set()

    def _run(self, jid):
        with self.kb.lock:
            row = self.kb.db.execute("SELECT kind, payload FROM jobs WHERE id=?", (jid,)).fetchone()
        if not row:
            return
        kind, p = row["kind"], json.loads(row["payload"] or "{}")
        self.kb.job_update(jid, "en cours")
        try:
            if kind == "fichier":
                msg = self._do_file(p)
            elif kind == "page web":
                msg = self._do_url(p)
            elif kind == "wikipédia":
                msg = self._do_wiki(p)
            elif kind == "actualisation":
                msg = self._do_refresh(p)
            elif kind == "graphe":
                msg = self._graph(p["doc_id"], force=True)
            elif kind == "embeddings":
                msg = self._do_embed()
            elif kind == "dossier":
                msg = self._do_folder(p)
            elif kind == "index":
                msg = (f"{self.kb.backfill_signatures()} signature(s) calculée(s) ; formules, tableaux ou données "
                       f"relevés dans {self.kb.backfill_extracts()} document(s) déjà importé(s)")
            elif kind == "connexions":
                msg = f"{self.hub.rebuild_links() if self.hub else 0} connexion(s) entre connaissances"
            else:
                raise CollectError(f"Tâche inconnue : {kind}")
            self.kb.job_update(jid, "terminé", msg)
        except Duplicate as e:
            if p.get("proposal_id"):
                self.kb.set_proposal(p["proposal_id"], "importé")
            self.kb.job_update(jid, "doublon", str(e))
        except (extract.ExtractError, CollectError, ProviderError, ValueError) as e:
            if p.get("proposal_id"):
                self.kb.set_proposal(p["proposal_id"], "en attente")
            self.kb.job_update(jid, "erreur", str(e))

    def _embedder(self):
        return self.brain.embed if self.brain.embedding_ref() else None

    def _do_file(self, p):
        path = Path(p["path"])
        try:
            data = path.read_bytes()
            title, segs = extract.segments(p["name"], data)
            ext = Path(p["name"]).suffix.lower()
            sha = hashlib.sha256(data).hexdigest()
            original = self.dir / "originals" / f"{sha[:16]}{ext}"
            doc_id = self.kb.ingest(
                p.get("title") or title or Path(p["name"]).stem, segs, "fichier", p["name"],
                p.get("category_id"), p.get("reliability", 3), p.get("license", ""), original=original.name,
                embed=self._embedder())
            original.write_bytes(data)
        except (Duplicate, extract.ExtractError, ValueError, OSError):
            path.unlink(missing_ok=True)   # échec définitif : le fichier temporaire ne servirait à rien
            raise
        # (pour toute autre erreur, passagère, le fichier est gardé : la tâche peut être relancée)
        path.unlink(missing_ok=True)
        return self._after_import(doc_id, len(segs) > 1 and segs[0][0].startswith("p."))

    def _segments_from_url(self, url):
        data, ctype, final = fetch(url, self.allow_private)
        name = urllib.parse.urlparse(final).path.rsplit("/", 1)[-1] or "page"
        if ctype == "application/pdf" or data[:5] == b"%PDF-":
            name = name if name.lower().endswith(".pdf") else name + ".pdf"
        elif "html" in ctype or b"<html" in data[:2000].lower():
            name = "page.html"
        elif ctype.startswith("text/") or not ctype:
            name = name if "." in name else "page.txt"
        elif not any(name.lower().endswith(e) for e in extract.SUPPORTED):
            raise CollectError(f"Type de contenu non pris en charge : {ctype}")
        title, segs = extract.segments(name, data)
        return title, segs, final

    def _do_url(self, p):
        title, segs, final = self._segments_from_url(p["url"])
        doc_id = self.kb.ingest(p.get("title") or title or final, segs, "page web", final, p.get("category_id"),
                                p.get("reliability") or reliability_for(final), p.get("license", ""),
                                embed=self._embedder())
        return self._after_import(doc_id)

    def _do_wiki(self, p):
        a = self.wiki.article(p["title"], p.get("lang", "fr"))
        doc_id = self.kb.ingest(a["title"], [("", a["text"])], "wikipédia", a["url"], p.get("category_id"), 3,
                                Wikipedia.LICENSE, published=a["revised"], embed=self._embedder())
        if p.get("proposal_id"):
            self.kb.set_proposal(p["proposal_id"], "importé")
        return self._after_import(doc_id)

    def _do_refresh(self, p):
        d = self.kb.get(p["doc_id"])
        if not d:
            raise ValueError("Document introuvable")
        if d["source_type"] == "wikipédia":
            m = re.match(r"https://([a-z]+)\.wikipedia\.org/wiki/(.+)", d["source"])
            lang, title = (m.group(1), urllib.parse.unquote(m.group(2)).replace("_", " ")) if m else ("fr", d["title"])
            segs = [("", self.wiki.article(title, lang)["text"])]
        else:
            _, segs, _ = self._segments_from_url(d["source"])
        res = self.kb.reingest(p["doc_id"], segs, self._embedder())
        if not res["changed"]:
            return "Source inchangée — date de vérification mise à jour"
        extra = self._graph(p["doc_id"]) if self.settings.get("kb_graph_auto") else ""
        return f"Contenu modifié à la source : version {res['version']} indexée, l'ancienne est archivée. {extra}".strip()

    def _do_embed(self):
        ref = self.brain.embedding_ref()
        done = 0
        for c in self.kb.chunks_missing_ext(ref):
            ext = self.brain.embed(c["content"])
            if not ext:
                raise ProviderError(f"Le modèle d'embeddings {ref} ne répond pas ({done} passages indexés avant l'arrêt)")
            self.kb.set_chunk_ext(c["id"], ext[0], ref)
            done += 1
        docs = 0
        for d in self.kb.docs_missing_ext(ref):
            ext = self.brain.embed(f"{d['title']}\n{(d['first'] or '')[:1500]}")
            if ext:
                self.kb.set_doc_ext(d["id"], ext[0], ref)
                docs += 1
        return f"{done} passage(s) et {docs} document(s) indexé(s) avec {ref}"

    # ----- après l'import : classement et graphe (si un modèle est disponible) -----
    def _llm(self):
        try:
            avail = self.brain.available()
        except Exception:
            return None
        if not avail:
            return None
        default = self.settings.get("default_model")
        return self.brain.utility_ref(default if default in avail else avail[0])

    def _after_import(self, doc_id, paged=False):
        d = self.kb.get(doc_id)
        notes = [f"{d['n_chunks']} passage(s) indexé(s)" + (" avec repères de page" if paged else "")]
        if d["dup_of"]:
            notes.append(f"ressemble fortement au document n° {d['dup_of']} (voir Doublons)")
        found = {}
        for e in self.kb.extracts_of(doc_id):
            found[e["kind"]] = found.get(e["kind"], 0) + 1
        if found:
            notes.append("relevé : " + ", ".join(f"{found[k]} {k}(s)" for k in ("formule", "tableau", "donnée") if k in found))
        if d.get("superseded_by"):
            notes.append(f"une version plus récente existe déjà (document n° {d['superseded_by']})")
        with self.kb.lock:
            older = self.kb.db.execute("SELECT id FROM documents WHERE superseded_by=?", (doc_id,)).fetchone()
        if older:
            notes.append(f"semble remplacer le document n° {older[0]} (voir À traiter)")
        ref = self._llm()
        if not d["category_id"] and self.settings.get("kb_auto_categorize"):
            cat = self._categorize(ref, d) if ref else None
            how = "modèle"
            if not cat:  # sans modèle (ou sans réponse) : classement par le vocabulaire du document
                text = "\n".join(c["content"] for c in self.kb.chunks_of(doc_id)[:4])
                name, _, words = library.classify_text(d["title"], text)
                cat = self.kb.find_category(name) if name else None
                how = "vocabulaire (" + ", ".join(words[:4]) + ")"
                if cat:
                    self.kb.update(doc_id, {"category_id": cat})
            if cat:
                self.kb.update(doc_id, {"class_how": how.split(" ")[0]})
                notes.append(f"classé dans « {self.kb.category_path(cat)} »" + ("" if how == "modèle" else f" d'après son {how}"))
            else:
                notes.append("catégorie non déterminée")
        if self.settings.get("kb_graph_auto"):
            notes.append(self._graph(doc_id, ref=ref))
        if self.hub:
            n = self.hub.integrate(f"d:{doc_id}")
            notes.append(f"{n} connexion(s) avec des connaissances existantes" if n else "aucune connaissance proche à relier")
        return " ; ".join(n for n in notes if n)

    def _categorize(self, ref, d):
        cats = [c for c in self.kb.categories() if c["parent_id"]]
        text = "\n".join(c["content"] for c in self.kb.chunks_of(d["id"])[:2])[:1800]
        try:
            ans = self.brain.complete(ref, "", [{"role": "user", "content": CATEGORIZE_PROMPT.format(
                cats="\n".join("- " + self.kb.category_path(c["id"]) for c in cats), title=d["title"], text=text)}],
                60, 0.0)
        except ProviderError:
            return None
        cid = self.kb.find_category(ans.strip().splitlines()[0] if ans.strip() else "")
        if cid:
            self.kb.update(d["id"], {"category_id": cid})
        return cid

    def _graph(self, doc_id, ref=None, force=False):
        ref = ref or self._llm()
        if not ref:
            if force:
                raise ProviderError("Aucun modèle disponible pour extraire les relations.")
            return "graphe non extrait (aucun modèle disponible)"
        d = self.kb.get(doc_id)
        text = "\n\n".join(c["content"] for c in self.kb.chunks_of(doc_id))[:9000]
        try:
            raw = self.brain.complete(ref, "", [{"role": "user", "content": GRAPH_PROMPT.format(
                title=d["title"], text=text)}], 2500, 0.0)
        except ProviderError as e:
            if force:
                raise
            return f"graphe non extrait ({e})"
        items = parse_json(raw)
        triples = []
        low = extract.clean(text).lower()
        for it in items if isinstance(items, list) else []:
            if not isinstance(it, dict):
                continue
            s, r, c = str(it.get("source", "")).strip(), str(it.get("relation", "")).strip(), str(it.get("cible", "")).strip()
            # garde-fou : les deux concepts doivent réellement figurer dans le texte
            if s and r and c and self._present(s, low) and self._present(c, low):
                triples.append((s, r, c, str(it.get("type_source") or "concept"), str(it.get("type_cible") or "concept")))
        n = self.kb.add_relations(triples[:25], doc_id)
        return f"{n} relation(s) ajoutée(s) au graphe"

    @staticmethod
    def _present(name, low_text):
        words = [w for w in re.findall(r"\w+", name.lower()) if len(w) > 3]
        return all(w[:-1] in low_text for w in words) if words else name.lower() in low_text

    # ----- enrichissement : propositions à valider -----
    def suggest(self, max_searches=10):
        """Cherche sur Wikipédia de quoi combler les questions sans source et les catégories vides."""
        lang = self.settings.get("kb_wikipedia_lang", "fr")
        made, searches, errors = 0, 0, []
        targets = [(g["question"], f"question restée sans source : « {g['question'][:80]} »", None)
                   for g in self.kb.gaps()[:max_searches // 2 + 1]]
        empty = [c for c in self.kb.categories() if c["parent_id"] and c["n"] == 0]
        for c in empty:
            targets.append((c["name"].split(" (")[0].split(",")[0], f"catégorie vide : {c['name']}", c["id"]))
        for query, reason, cat in targets[:max_searches]:
            searches += 1
            try:
                kw = " ".join(list(dict.fromkeys(re.findall(r"\w{4,}", query)))[:6]) or query
                for res in self.wiki.search(kw, lang, 2):
                    if self.kb.add_proposal("wikipédia", res["title"], res["url"], res["snippet"], reason, lang, cat):
                        made += 1
            except CollectError as e:
                errors.append(str(e))
                break
        auto = 0
        if not self.settings.get("kb_require_validation"):
            for p in self.kb.proposals():
                self.accept(p["id"])
                auto += 1
        return {"proposals": made, "searches": searches, "auto_imported": auto, "error": errors[0] if errors else None}

    def accept(self, pid):
        p = self.kb.get_proposal(pid)
        if not p or p["status"] != "en attente":
            raise ValueError("Proposition introuvable")
        self.kb.set_proposal(pid, "en cours")
        return self.submit_wikipedia(p["title"], p["lang"], {"category_id": p["category_id"], "proposal_id": pid})

    def starter(self):
        """Liste du premier corpus proposé, avec ce qui est déjà importé."""
        have = {d["title"].lower() for d in self.kb.list(limit=100000) if d["source_type"] == "wikipédia"}
        return [{"category": cat, "category_id": self.kb.find_category(cat),
                 "titles": [{"title": t, "imported": t.lower() in have} for t in titles]}
                for cat, titles in STARTER.items()]

    def refresh_stale(self, limit=30):
        before = time.time() - self.settings.get("kb_stale_days", 180) * 86400
        n = 0
        for d in self.kb.list(stale_before=before, limit=limit * 4):
            if d["source_type"] in ("page web", "wikipédia") and n < limit:
                self.submit_refresh(d["id"])
                n += 1
        return n
