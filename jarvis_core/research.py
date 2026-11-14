"""Chercheur autonome : cherche, lit, recoupe et propose des connaissances — sans jamais en inventer.

Principes appliqués par le code (pas seulement demandés au modèle) :
  - une affirmation n'est gardée que si elle s'appuie sur une citation retrouvée mot pour mot dans la source ;
  - « vérifié » exige au moins deux sources indépendantes (deux sites différents) ; avec une seule, c'est
    « à confirmer » ; si une source dit le contraire, c'est « contradictoire » ;
  - rien n'entre dans la base sans validation, sauf si l'utilisateur a désactivé la validation — et même alors,
    seules les affirmations « vérifiées » et sans conflit avec la base existante sont intégrées ;
  - une connaissance existante n'est jamais modifiée ni remplacée par le chercheur ;
  - les références d'origine (adresse, auteurs, date, licence) sont conservées avec chaque source.

Sources : Wikipédia, arXiv, OpenAlex, HAL (API publiques), et le web général via l'API Brave Search si une clé
est fournie. Les pages web sous droits d'auteur ne sont pas copiées : seuls de courts extraits cités sont gardés.
"""
import json
import queue
import re
import threading
import time
import urllib.parse
from xml.etree import ElementTree as ET

from . import extract, library
from .brain import parse_json
from .collect import CollectError, Wikipedia, fetch, reliability_for
from .knowledge import Duplicate, stems
from .memory import chunk_text, normalize
from .providers import ProviderError

MODES = {"rapide": {"queries": 2, "per_query": 3, "sources": 6, "passages": 2, "claims": 8},
         "approfondi": {"queries": 4, "per_query": 5, "sources": 14, "passages": 3, "claims": 14}}
PROVIDERS = [
    ("wikipedia", "Wikipédia", "Encyclopédie, articles sous licence CC BY-SA"),
    ("hal", "HAL", "Archive ouverte de la recherche française (articles, thèses)"),
    ("openalex", "OpenAlex", "Index mondial des publications scientifiques"),
    ("arxiv", "arXiv", "Prépublications scientifiques (non relues par les pairs)"),
    ("web", "Web (Brave Search)", "Sites officiels et documentations ; demande une clé d'API Brave"),
]

QUERY_PROMPT = """Tu prépares une recherche documentaire pour répondre à la question ci-dessous.
Donne {n} requêtes de recherche en français et {n} en anglais : 2 à 5 mots-clés chacune, pas de phrase,
pas de guillemets. Varie les angles (définition, valeurs, méthode, norme…).
Réponds uniquement par un objet JSON : {{"fr": ["..."], "en": ["..."]}}

Question : {question}"""

CLAIMS_PROMPT = """Tu es un documentaliste rigoureux. Voici une question et des extraits de sources numérotées.
Relève les affirmations utiles pour répondre à la question, en respectant strictement ces règles :
- n'écris QUE ce qui est explicitement dit dans les extraits ; n'ajoute aucune connaissance extérieure ;
- chaque affirmation est une phrase autonome, précise, en français ;
- pour chaque affirmation, cite la ou les sources qui la disent, avec la citation EXACTE (copiée mot pour mot
  depuis l'extrait, dans sa langue d'origine, 25 mots au maximum) ;
- si une autre source dit le contraire ou donne une valeur différente, indique-le dans "contredit_par" avec sa citation exacte ;
- une hypothèse ou une opinion d'auteur doit être formulée comme telle (« Selon … ») ;
- {max} affirmations au maximum ; si les extraits ne répondent pas à la question, réponds [].
Réponds uniquement par un tableau JSON :
[{{"affirmation": "...", "sources": [{{"n": 1, "citation": "..."}}], "contredit_par": [{{"n": 2, "citation": "..."}}]}}]

Question : {question}

{sources}"""


def norm_text(t):
    return " ".join(re.findall(r"[a-z0-9]+", normalize(t)))


def quote_found(quote, text_norm):
    """La citation figure-t-elle dans le texte ? (casse, accents, ponctuation et espaces ignorés)"""
    q = norm_text(quote)
    if len(q) < 12:
        return False
    if q in text_norm:
        return True
    words = q.split()
    if len(words) < 6:
        return False
    grams = [" ".join(words[i:i + 5]) for i in range(len(words) - 4)]
    return sum(g in text_norm for g in grams) / len(grams) >= 0.9   # tolère une coupure « … » au milieu


def site_of(url):
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    parts = host.split(".")
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in ("gouv", "co", "ac", "com", "org", "edu"):
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def keywords(text, limit=6):
    out = []
    for w in re.findall(r"[A-Za-zÀ-ÿ0-9][A-Za-zÀ-ÿ0-9\-]+", text):
        if stems(w) and w.lower() not in (x.lower() for x in out):
            out.append(w)
    return " ".join(out[:limit])


# ============================================================== sources
class Source:
    key, label, open_text = "", "", True

    def __init__(self, r):
        self.r = r

    def ready(self):
        return True, ""

    def search(self, query, n, lang="fr", recent=False):
        raise NotImplementedError

    def _json(self, url, headers=None):
        data, _, _ = fetch(url, self.r.allow_private, 25, headers)
        try:
            return json.loads(data.decode("utf-8"))
        except ValueError:
            raise CollectError(f"Réponse illisible de {self.label}") from None


class WikipediaSource(Source):
    key, label = "wikipedia", "Wikipédia"

    def search(self, query, n, lang="fr", recent=False):
        out = []
        for hit in self.r.wiki.search(query, lang, n):
            try:
                a = self.r.wiki.article(hit["title"], lang)
            except CollectError:
                continue
            out.append({"provider": self.key, "url": a["url"], "title": a["title"], "authors": "Contributeurs de Wikipédia",
                        "published": a["revised"], "license": Wikipedia.LICENSE, "reliability": 3,
                        "kind": "article d'encyclopédie", "text": a["text"], "lang": lang})
        return out


class ArxivSource(Source):
    key, label = "arxiv", "arXiv"
    NS = {"a": "http://www.w3.org/2005/Atom"}

    def search(self, query, n, lang="en", recent=False):
        words = [w for w in re.findall(r"[A-Za-z0-9\-]+", normalize(query)) if len(w) > 2][:6]
        if not words:
            return []
        params = {"search_query": " AND ".join(f"all:{w}" for w in words), "start": 0, "max_results": n}
        if recent:
            params.update(sortBy="submittedDate", sortOrder="descending")
        self.r.pace("arxiv")  # conditions d'utilisation d'arXiv : une requête toutes les 3 secondes au plus
        data, _, _ = fetch(self.r.endpoint("arxiv") + "?" + urllib.parse.urlencode(params), self.r.allow_private, 30)
        try:
            root = ET.fromstring(data)
        except ET.ParseError:
            raise CollectError("Réponse illisible d'arXiv") from None
        out = []
        for e in root.findall("a:entry", self.NS):
            get = lambda tag: re.sub(r"\s+", " ", (e.findtext(f"a:{tag}", "", self.NS) or "")).strip()
            url = get("id")
            if not url or not get("summary"):
                continue
            out.append({"provider": self.key, "url": url.replace("http://", "https://"), "title": get("title"),
                        "authors": ", ".join((a.findtext("a:name", "", self.NS) or "").strip()
                                             for a in e.findall("a:author", self.NS))[:300],
                        "published": get("published")[:10],
                        "license": "Résumé : métadonnées arXiv (CC0 1.0). Texte intégral : licence de l'auteur.",
                        "reliability": 3, "kind": "prépublication (non relue par les pairs)",
                        "text": f"{get('title')}.\n\n{get('summary')}", "lang": "en"})
        return out


class OpenAlexSource(Source):
    key, label = "openalex", "OpenAlex"

    def search(self, query, n, lang="en", recent=False):
        params = {"search": query, "per_page": n}
        if recent:
            params["sort"] = "publication_date:desc"
            params["filter"] = "from_publication_date:" + time.strftime("%Y-%m-%d", time.gmtime(time.time() - 400 * 86400))
        key = self.r.keys.get("openalex")
        if key:
            params["api_key"] = key
        j = self._json(self.r.endpoint("openalex") + "/works?" + urllib.parse.urlencode(params))
        out = []
        for w in j.get("results") or []:
            if w.get("is_retracted"):
                continue  # un article rétracté n'est jamais proposé
            inv = w.get("abstract_inverted_index") or {}
            pos = sorted((p, word) for word, ps in inv.items() for p in ps)
            abstract = " ".join(word for _, word in pos)
            title = w.get("display_name") or w.get("title") or ""
            loc = w.get("primary_location") or {}
            src = loc.get("source") or {}
            url = w.get("doi") or loc.get("landing_page_url") or w.get("id") or ""
            if not title or not url or len(abstract) < 80:
                continue
            journal = w.get("type") == "article" and src.get("type") == "journal"
            out.append({"provider": self.key, "url": url, "title": title,
                        "authors": ", ".join((a.get("author") or {}).get("display_name", "")
                                             for a in (w.get("authorships") or [])[:8]),
                        "published": w.get("publication_date") or str(w.get("publication_year") or ""),
                        "license": "Résumé : métadonnées OpenAlex (CC0). Texte intégral : licence de l'éditeur.",
                        "reliability": 4 if journal else 3,
                        "kind": f"article de revue ({src.get('display_name')})" if journal else (w.get("type") or "publication"),
                        "text": f"{title}.\n\n{abstract}", "lang": "en"})
        return out


class HalSource(Source):
    key, label = "hal", "HAL"
    TYPES = {"ART": "article de revue", "THESE": "thèse", "HDR": "habilitation", "COMM": "communication de congrès",
             "OUV": "ouvrage", "COUV": "chapitre d'ouvrage", "REPORT": "rapport"}

    def search(self, query, n, lang="fr", recent=False):
        params = {"q": query, "wt": "json", "rows": n,
                  "fl": "halId_s,title_s,abstract_s,uri_s,authFullName_s,publicationDateY_i,docType_s"}
        if recent:
            params["sort"] = "publicationDateY_i desc"
        j = self._json(self.r.endpoint("hal") + "?" + urllib.parse.urlencode(params))
        out = []
        for d in (j.get("response") or {}).get("docs") or []:
            title = " ".join(d.get("title_s") or [])[:300] if isinstance(d.get("title_s"), list) else str(d.get("title_s") or "")
            abstract = d.get("abstract_s") or []
            abstract = " ".join(abstract) if isinstance(abstract, list) else str(abstract)
            if not title or not d.get("uri_s") or len(abstract) < 80:
                continue
            kind = d.get("docType_s") or ""
            out.append({"provider": self.key, "url": d["uri_s"], "title": title,
                        "authors": ", ".join((d.get("authFullName_s") or [])[:8]),
                        "published": str(d.get("publicationDateY_i") or ""),
                        "license": "Résumé : métadonnées HAL (CC0). Texte intégral : licence choisie par l'auteur.",
                        "reliability": 4 if kind in ("ART", "THESE", "HDR") else 3,
                        "kind": self.TYPES.get(kind, "document scientifique"),
                        "text": f"{title}.\n\n{abstract}", "lang": "fr"})
        return out


class WebSource(Source):
    key, label, open_text = "web", "Web (Brave Search)", False

    def ready(self):
        return (True, "") if self.r.keys.has("brave") else (False, "clé d'API Brave Search manquante")

    def search(self, query, n, lang="fr", recent=False):
        params = {"q": query, "count": n, "search_lang": lang}
        if recent:
            params["freshness"] = "py"
        j = self._json(self.r.endpoint("brave") + "?" + urllib.parse.urlencode(params),
                       {"X-Subscription-Token": self.r.keys.get("brave"), "Accept": "application/json"})
        out = []
        for res in ((j.get("web") or {}).get("results") or [])[:n]:
            url = res.get("url") or ""
            if not url.startswith("http") or "wikipedia.org" in url:
                continue
            try:
                data, ctype, final = fetch(url, self.r.allow_private, 20)
                if "html" in ctype or b"<html" in data[:2000].lower():
                    title, text = extract.html_to_text(extract.decode(data))
                elif ctype.startswith("text/"):
                    title, text = "", extract.clean(extract.decode(data))
                else:
                    continue
            except (CollectError, extract.ExtractError):
                continue
            if len(text) < 200:
                continue
            out.append({"provider": self.key, "url": final, "title": (title or res.get("title") or final)[:200],
                        "authors": "", "published": str(res.get("age") or "")[:40],
                        "license": "Page web : droits de l'éditeur. Seuls de courts extraits cités sont conservés.",
                        "reliability": reliability_for(final), "kind": "page web", "text": text[:60000], "lang": lang})
        return out


ENDPOINTS = {"arxiv": "https://export.arxiv.org/api/query", "openalex": "https://api.openalex.org",
             "hal": "https://api.archives-ouvertes.fr/search/", "brave": "https://api.search.brave.com/res/v1/web/search"}


# ============================================================== chercheur
class Cancelled(Exception):
    pass


class Researcher:
    def __init__(self, kb, brain, settings, keys, hub, kbs, wikipedia=None, allow_private=False, endpoints=None,
                 pace_seconds=3.1):
        self.kb, self.brain, self.settings, self.keys, self.hub, self.kbs = kb, brain, settings, keys, hub, kbs
        self.wiki = wikipedia or Wikipedia()
        self.allow_private = allow_private
        self._endpoints = {**ENDPOINTS, **(endpoints or {})}
        self._pace, self._last = pace_seconds, {}
        self.sources = {c.key: c(self) for c in (WikipediaSource, HalSource, OpenAlexSource, ArxivSource, WebSource)}
        self.q = queue.Queue()
        self.idle = threading.Event()
        self.idle.set()
        self._cancel = set()
        threading.Thread(target=self._loop, daemon=True).start()
        threading.Thread(target=self._watch_loop, daemon=True).start()

    def endpoint(self, name):
        return self._endpoints[name]

    def pace(self, name):
        wait = self._last.get(name, 0) + self._pace - time.time()
        if wait > 0:
            time.sleep(wait)
        self._last[name] = time.time()

    # ---------- état des sources ----------
    def providers_state(self):
        on = self.settings.get("research_sources") or {}
        out = []
        for key, label, desc in PROVIDERS:
            ready, why = self.sources[key].ready()
            out.append({"key": key, "label": label, "description": desc, "enabled": bool(on.get(key, True)),
                        "ready": ready, "note": why,
                        "needs_key": key == "web", "has_key": self.keys.has("brave") if key == "web"
                        else self.keys.has("openalex") if key == "openalex" else None,
                        "key_optional": key == "openalex"})
        return out

    def _active(self):
        on = self.settings.get("research_sources") or {}
        return [self.sources[k] for k, _, _ in PROVIDERS if on.get(k, True) and self.sources[k].ready()[0]]

    # ---------- file ----------
    def create(self, question, mode="rapide", origin="manuel", gap_id=None, watch_id=None):
        question = re.sub(r"\s+", " ", str(question)).strip()[:400]
        if len(question) < 4:
            raise ValueError("Question trop courte")
        mode = mode if mode in MODES else "rapide"
        with self.kb.lock:
            dup = self.kb.db.execute("SELECT id FROM research WHERE question=? AND status IN ('en file','en cours')",
                                     (question,)).fetchone()
            if dup:
                return dup[0]
            rid = self.kb.db.execute(
                "INSERT INTO research(question,mode,origin,created,gap_id,watch_id) VALUES(?,?,?,?,?,?)",
                (question, mode, origin, time.time(), gap_id, watch_id)).lastrowid
            self.kb.db.commit()
        self.idle.clear()
        self.q.put(rid)
        return rid

    def cancel(self, rid):
        self._cancel.add(rid)
        with self.kb.lock:
            self.kb.db.execute("UPDATE research SET status='annulé', finished=? WHERE id=? AND status='en file'",
                               (time.time(), rid))
            self.kb.db.commit()

    def wait(self, timeout=60):
        return self.idle.wait(timeout)

    def _loop(self):
        while True:
            rid = self.q.get()
            try:
                self._run(rid)
            except Cancelled:
                self._set(rid, status="annulé", finished=time.time())
            except Exception as e:  # une recherche en échec ne doit jamais arrêter la file
                self._set(rid, status="erreur", error=f"{type(e).__name__} : {e}"[:500], finished=time.time())
            finally:
                self._cancel.discard(rid)
                if self.q.empty():
                    self.idle.set()

    def _set(self, rid, **fields):
        with self.kb.lock:
            self.kb.db.execute(f"UPDATE research SET {', '.join(k + '=?' for k in fields)} WHERE id=?",
                               list(fields.values()) + [rid])
            self.kb.db.commit()

    def _log(self, rid, step, detail=""):
        if rid in self._cancel:
            raise Cancelled()
        with self.kb.lock:
            r = self.kb.db.execute("SELECT log FROM research WHERE id=?", (rid,)).fetchone()
            log = json.loads(r[0] or "[]") if r else []
            log.append({"at": time.time(), "step": step, "detail": detail[:400]})
            self.kb.db.execute("UPDATE research SET log=? WHERE id=?", (json.dumps(log, ensure_ascii=False), rid))
            self.kb.db.commit()

    # ---------- déroulement d'une recherche ----------
    def _run(self, rid):
        with self.kb.lock:
            row = self.kb.db.execute("SELECT * FROM research WHERE id=?", (rid,)).fetchone()
        if not row or row["status"] != "en file":
            return
        question, cfg = row["question"], MODES[row["mode"]]
        recent = row["origin"] == "veille"
        self._set(rid, status="en cours", started=time.time())
        llm = self.kbs._llm()

        # 1. domaine et connaissances déjà présentes
        cat, _, words = library.classify_text("", question, min_score=1.0, question=True)
        cat_id = self.kb.find_category(cat) if cat else None
        self._set(rid, category_id=cat_id)
        known = self.kb.search(question, 4, self.brain.embed(question))
        self._log(rid, "Analyse de la question",
                  (f"domaine : {cat}" if cat else "domaine non identifié")
                  + f" ; {len(known)} passage(s) déjà dans la base sur ce sujet")

        # 2. requêtes
        fr, en = [keywords(question)], []
        if llm:
            try:
                j = parse_json(self.brain.complete(llm, "", [{"role": "user", "content": QUERY_PROMPT.format(
                    n=cfg["queries"], question=question)}], 400, 0.0), "{", "}")
                if isinstance(j, dict):
                    fr = [str(x)[:80] for x in (j.get("fr") or []) if str(x).strip()][:cfg["queries"]] or fr
                    en = [str(x)[:80] for x in (j.get("en") or []) if str(x).strip()][:cfg["queries"]]
            except ProviderError as e:
                self._log(rid, "Requêtes", f"le modèle n'a pas répondu ({e}) : mots-clés de la question utilisés")
        fr = [q for q in fr if q.strip()]
        if not fr and not en:
            raise CollectError("La question ne contient aucun mot-clé exploitable")
        self._set(rid, queries=json.dumps({"fr": fr, "en": en}, ensure_ascii=False))
        self._log(rid, "Requêtes", " · ".join(fr + en))

        # 3. collecte
        rules = self.kb.source_rules()
        active = self._active()
        if not active:
            raise CollectError("Aucune source de recherche n'est activée")
        docs, seen = [], set()
        for src in active:
            if src.key == "wikipedia":
                plan = [(q, self.settings.get("kb_wikipedia_lang", "fr")) for q in fr[:2]]
                if row["mode"] == "approfondi" and en:
                    plan.append((en[0], "en"))
            elif src.key in ("arxiv", "openalex"):
                plan = [(q, "en") for q in (en or fr)[:cfg["queries"]]]
            else:
                plan = [(q, "fr") for q in fr[:cfg["queries"]]] + ([(en[0], "en")] if en and row["mode"] == "approfondi" else [])
            got, err = 0, ""
            for q, lang in plan:
                try:
                    for d in src.search(q, cfg["per_query"], lang, recent):
                        key = d["url"].split("#")[0].rstrip("/").lower()
                        site = site_of(d["url"])
                        rule = rules.get(site) or rules.get((urllib.parse.urlparse(d["url"]).hostname or "").lower())
                        if key in seen or (rule and rule["blocked"]):
                            continue
                        if rule and rule["reliability"]:
                            d["reliability"] = rule["reliability"]
                        seen.add(key)
                        d["site"], d["open"] = site, src.open_text
                        docs.append(d)
                        got += 1
                except CollectError as e:
                    err = str(e)
                    break
            self._log(rid, f"Source : {src.label}", f"{got} document(s)" + (f" — {err}" if err else ""))
        off = [f"{l} ({self.sources[k].ready()[1]})" for k, l, _ in PROVIDERS
               if (self.settings.get("research_sources") or {}).get(k, True) and not self.sources[k].ready()[0]]
        if off:
            self._log(rid, "Sources non interrogées", " ; ".join(off))

        # 4. lecture : passages en rapport avec la question, tri par pertinence et fiabilité
        qs = stems(" ".join([question] + fr + en))
        for d in docs:
            chunks = chunk_text(d["text"], 700)
            scored = sorted(((len(qs & stems(c)), -i, c) for i, c in enumerate(chunks)), reverse=True)
            best = [(s, c) for s, _, c in scored[:cfg["passages"]] if s >= (2 if len(qs) > 2 else 1)]
            d["passages"] = [c for _, c in best]
            d["relevance"] = round(sum(s for s, _ in best) / max(1, len(qs)), 3)
        docs = [d for d in docs if d["passages"]]
        docs.sort(key=lambda d: d["relevance"] * (0.6 + 0.1 * d["reliability"]), reverse=True)
        docs = docs[:cfg["sources"]]
        now = time.time()
        with self.kb.lock:
            for i, d in enumerate(docs):
                d["n"] = i + 1
                self.kb.db.execute(
                    "INSERT INTO research_sources(research_id,n,provider,url,title,domain,authors,published,license,"
                    "reliability,kind,excerpt,relevance,created) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (rid, d["n"], d["provider"], d["url"], d["title"][:300], d["site"], d["authors"][:300], d["published"],
                     d["license"], d["reliability"], d["kind"][:120], "\n\n".join(d["passages"])[:2400], d["relevance"], now))
            self.kb.db.commit()
        self._log(rid, "Lecture", f"{len(docs)} source(s) retenue(s) sur {len(seen)} trouvée(s)")
        if not docs:
            self._finish(rid, row, [], "Aucune source en rapport avec la question n'a été trouvée.")
            return

        # 5. affirmations : extraites par le modèle, puis contrôlées citation par citation
        findings = []
        if llm:
            findings += self._claims(rid, llm, question, docs, cfg["claims"])
        else:
            self._log(rid, "Extraction", "aucun modèle disponible : seules les données chiffrées sont relevées, "
                                         "les sources restent consultables")
        findings += self._data_findings(question, docs, findings)

        # 6. contrôle, statut, confiance
        for f in findings:
            f["status"], f["confidence"] = self._judge(f)
        n_ok = sum(f["status"] == "vérifié" for f in findings)
        self._log(rid, "Vérification", f"{len(findings)} affirmation(s) appuyée(s) par une citation retrouvée dans la source : "
                                       f"{n_ok} vérifiée(s) par deux sources indépendantes, "
                                       f"{sum(f['status'] == 'à confirmer' for f in findings)} à confirmer, "
                                       f"{sum(f['status'] == 'contradictoire' for f in findings)} contradictoire(s)")
        self._finish(rid, row, findings, None, docs)

    def _claims(self, rid, llm, question, docs, max_claims):
        blocks = [f"[{d['n']}] {d['title']} — {d['provider']}, {d['kind']}, fiabilité {d['reliability']}/5\n"
                  + "\n…\n".join(d["passages"]) for d in docs]
        text, total = [], 0
        for b in blocks:
            if total + len(b) > 14000:
                break
            text.append(b)
            total += len(b)
        try:
            raw = self.brain.complete(llm, "", [{"role": "user", "content": CLAIMS_PROMPT.format(
                max=max_claims, question=question, sources="\n\n".join(text))}], 3000, 0.0)
        except ProviderError as e:
            self._log(rid, "Extraction", f"le modèle n'a pas répondu ({e})")
            return []
        items = parse_json(raw)
        by_n = {d["n"]: d for d in docs}
        norm = {d["n"]: norm_text(d["text"]) for d in docs}
        out, dropped = [], 0

        def cites(lst):
            nonlocal dropped
            ok = []
            for c in lst if isinstance(lst, list) else []:
                if not isinstance(c, dict):
                    continue
                try:
                    n = int(c.get("n"))
                except (TypeError, ValueError):
                    continue
                quote = str(c.get("citation") or "").strip()
                if n in by_n and quote_found(quote, norm[n]):
                    ok.append({"n": n, "quote": quote[:400]})
                else:
                    dropped += 1
            return ok

        for it in items if isinstance(items, list) else []:
            if not isinstance(it, dict) or not str(it.get("affirmation") or "").strip():
                continue
            sup = cites(it.get("sources"))
            if not sup:
                continue  # aucune citation retrouvée : l'affirmation est écartée
            out.append({"claim": str(it["affirmation"]).strip()[:600], "support": sup,
                        "contra": cites(it.get("contredit_par")), "origin": "modèle", "docs": by_n})
        self._log(rid, "Extraction", f"{len(out)} affirmation(s) retenue(s)"
                  + (f" ; {dropped} citation(s) écartée(s) car introuvable(s) dans la source" if dropped else ""))
        return out[:max_claims]

    def _data_findings(self, question, docs, existing):
        """Données chiffrées relevées sans modèle : une grandeur, une valeur, une unité, la phrase d'origine."""
        qs = stems(question)
        items = []
        for d in docs:
            for p in d["passages"]:
                for e in library.find_extracts([("", p)]):
                    if e["kind"] != "donnée" or not (set(library.label_key(e["label"]).split()) & qs):
                        continue
                    val = e["content"].rsplit(" : ", 1)[-1]
                    m = re.search(re.escape(val.rsplit(" ", 1)[0]) + r"\s*" + re.escape(e["unit"]), p)
                    quote = library.sentence_around(p, m.group(0)) if m else e["content"]
                    items.append({**e, "n": d["n"], "quote": quote, "valstr": val})
        by_n = {d["n"]: d for d in docs}
        have = " ".join(norm_text(f["claim"]) for f in existing)
        out = []
        for lst in library.group_quantities(items):
            ref = min(lst, key=lambda x: len(x["label"]))   # le libellé le plus court est le plus propre
            if norm_text(ref["valstr"]) in have:
                continue  # déjà couvert par une affirmation du modèle
            agree = [x for x in lst if abs(x["value"] - ref["value"]) <= 0.1 * max(abs(ref["value"]), abs(x["value"]), 1e-9)]
            differ = [x for x in lst if x not in agree]
            out.append({"claim": f"{ref['label'][0].upper()}{ref['label'][1:]} : {ref['valstr']}",
                        "support": [{"n": x["n"], "quote": x["quote"]} for x in agree],
                        "contra": [{"n": x["n"], "quote": x["quote"]} for x in differ],
                        "origin": "relevé automatique", "docs": by_n})
        return out[:6]

    @staticmethod
    def _judge(f):
        docs = f["docs"]
        sites = {docs[c["n"]]["site"] for c in f["support"]}
        rel = max(docs[c["n"]]["reliability"] for c in f["support"])
        contra_sites = {docs[c["n"]]["site"] for c in f["contra"]} - sites
        conf = 0.12 * rel + 0.2 * min(2, len(sites) - 1)
        if contra_sites:
            return "contradictoire", round(max(0.05, min(0.5, conf - 0.3)), 2)
        if len(sites) >= 2:
            return "vérifié", round(min(0.99, conf + 0.15), 2)
        return "à confirmer", round(min(0.6, conf), 2)

    def _finish(self, rid, row, findings, message, docs=()):
        now = time.time()
        auto = not self.settings.get("research_require_validation", True)
        used = set()
        ids = []
        with self.kb.lock:
            for f in findings:
                for c in f["support"] + f["contra"]:
                    used.add(c["n"])
                enrich = lambda lst: [{**c, "title": f["docs"][c["n"]]["title"], "url": f["docs"][c["n"]]["url"],
                                       "provider": f["docs"][c["n"]]["provider"], "site": f["docs"][c["n"]]["site"],
                                       "reliability": f["docs"][c["n"]]["reliability"],
                                       "published": f["docs"][c["n"]]["published"],
                                       "license": f["docs"][c["n"]]["license"]} for c in lst]
                cur = self.kb.db.execute(
                    "INSERT INTO findings(research_id,claim,status,confidence,support,contra,created,origin) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (rid, f["claim"], f["status"], f["confidence"], json.dumps(enrich(f["support"]), ensure_ascii=False),
                     json.dumps(enrich(f["contra"]), ensure_ascii=False), now, f["origin"]))
                ids.append((cur.lastrowid, f["status"]))
            if used:
                self.kb.db.execute(f"UPDATE research_sources SET used=1 WHERE research_id=? AND n IN "
                                   f"({','.join('?' * len(used))})", [rid] + sorted(used))
            self.kb.db.commit()
        integrated = 0
        if auto:
            for fid, status in ids:
                if status == "vérifié":
                    res = self.decide(fid, "valider", automatic=True)
                    integrated += res.get("decision") == "intégré"
        counts = {s: sum(1 for _, st in ids if st == s) for s in ("vérifié", "à confirmer", "contradictoire")}
        summary = message or (
            f"{len(ids)} affirmation(s) à partir de {len(docs)} source(s) : {counts['vérifié']} vérifiée(s), "
            f"{counts['à confirmer']} à confirmer, {counts['contradictoire']} contradictoire(s). "
            + (f"{integrated} intégrée(s) automatiquement (validation désactivée)." if auto
               else "Rien n'est intégré à la base sans votre validation."))
        if not ids and not message:
            summary = (f"{len(docs)} source(s) lue(s), mais aucune affirmation n'a pu être appuyée par une citation exacte. "
                       "Les sources restent consultables ci-dessous.")
        self._set(rid, status="terminé", finished=now, summary=summary)
        if row["watch_id"]:
            with self.kb.lock:
                self.kb.db.execute("UPDATE watch SET last_run=? WHERE id=?", (now, row["watch_id"]))
                self.kb.db.commit()
        self._log(rid, "Terminé", summary)

    # ---------- validation et intégration ----------
    def decide(self, fid, action, claim=None, automatic=False):
        with self.kb.lock:
            f = self.kb.db.execute("SELECT f.*, r.question, r.category_id FROM findings f JOIN research r "
                                   "ON r.id=f.research_id WHERE f.id=?", (fid,)).fetchone()
        if not f:
            raise ValueError("Affirmation introuvable")
        if f["decision"] == "intégré":
            raise ValueError("Cette affirmation est déjà dans la base")
        now = time.time()
        if action == "refuser":
            self._decision(fid, "refusé", now)
            return {"decision": "refusé"}
        if action != "valider":
            raise ValueError("Action invalide")
        text = (claim or f["claim"]).strip()[:600]
        if not text:
            raise ValueError("Affirmation vide")
        support, contra = json.loads(f["support"]), json.loads(f["contra"])
        clash = self._clash(text)
        if automatic and clash:
            self._decision(fid, "en attente", None, f"non intégrée automatiquement : {clash}")
            return {"decision": "en attente", "note": clash}
        lines = [text, "", f"Statut : {f['status']} — confiance {int(f['confidence'] * 100)} %."
                 + (" Affirmation appuyée par une seule source : à confirmer." if f["status"] == "à confirmer" else "")
                 + (" Des sources se contredisent sur ce point (voir ci-dessous)." if contra else ""), "", "Sources :"]
        for c in support:
            lines.append(f"[{c['n']}] « {c['quote']} » — {c['title']} ({c['provider']}, {c['published'] or 'date inconnue'}) {c['url']}")
        if contra:
            lines.append("")
            lines.append("Sources en désaccord :")
            for c in contra:
                lines.append(f"[{c['n']}] « {c['quote']} » — {c['title']} ({c['provider']}) {c['url']}")
        rel = max(c["reliability"] for c in support)
        rel = rel if f["status"] == "vérifié" else min(rel, 2)
        try:
            doc_id = self.kb.ingest(
                text[:140], [("", "\n".join(lines))], "recherche", support[0]["url"], f["category_id"], rel,
                "Synthèse du chercheur de Jarvis ; citations courtes des sources indiquées",
                description=f"Chercheur autonome — {f['status']} — question : {f['question']}"[:400],
                published=time.strftime("%Y-%m-%d"), embed=self.brain.embed if self.brain.embedding_ref() else None)
        except Duplicate as e:
            self._decision(fid, "déjà connu", now, str(e))
            return {"decision": "déjà connu", "note": str(e)}
        self.kb.update(doc_id, {"refs": " ; ".join(c["url"] for c in support + contra)[:1900],
                                "class_how": "chercheur"})
        self.hub.integrate(f"d:{doc_id}")
        self.kb.add_links([(f"r:{f['research_id']}", f"d:{doc_id}", f["confidence"])], "recherche")
        with self.kb.lock:
            self.kb.db.execute("UPDATE findings SET decision='intégré', decided=?, doc_id=?, claim=?, note=? WHERE id=?",
                               (now, doc_id, text, clash or "", fid))
            gap = self.kb.db.execute("SELECT gap_id FROM research WHERE id=?", (f["research_id"],)).fetchone()[0]
            if gap:
                self.kb.db.execute("UPDATE gaps SET status='traité' WHERE id=?", (gap,))
            self.kb._log("recherche", doc_id, text[:140],
                         ("intégrée automatiquement" if automatic else "validée") + f" — {f['status']}, {len(support)} source(s)")
            self.kb.db.commit()
        return {"decision": "intégré", "doc_id": doc_id, "note": clash or ""}

    def _decision(self, fid, decision, when, note=None):
        with self.kb.lock:
            if note is None:
                self.kb.db.execute("UPDATE findings SET decision=?, decided=? WHERE id=?", (decision, when, fid))
            else:
                self.kb.db.execute("UPDATE findings SET decision=?, decided=?, note=? WHERE id=?",
                                   (decision, when, note[:300], fid))
            self.kb.db.commit()

    def _clash(self, claim):
        """Une donnée de l'affirmation contredit-elle un document déjà dans la base ?"""
        mine = [e for e in library.find_extracts([("", claim)]) if e["kind"] == "donnée"]
        if not mine:
            return ""
        with self.kb.lock:
            rows = [dict(r) for r in self.kb.db.execute(
                "SELECT e.label, e.value, e.unit, e.content, d.id AS doc_id, d.title FROM extracts e JOIN documents d "
                "ON d.id=e.doc_id WHERE e.kind='donnée' AND d.archived=0 AND e.unit IN "
                f"({','.join('?' * len(mine))})", [e["unit"] for e in mine])]
        for a, b in library.conflicts([{**e, "doc_id": 0, "title": ""} for e in mine] + rows):
            if (a["doc_id"] == 0) != (b["doc_id"] == 0):
                other = b if a["doc_id"] == 0 else a
                return f"contredit « {other['title']} » ({other['content']})"
        return ""

    # ---------- consultation ----------
    @staticmethod
    def _run_row(r):
        d = dict(r)
        d["log"] = json.loads(d.get("log") or "[]")
        d["queries"] = json.loads(d["queries"]) if d.get("queries") else {}
        return d

    def runs(self, limit=60):
        with self.kb.lock:
            rows = self.kb.db.execute(
                "SELECT r.*, (SELECT COUNT(*) FROM research_sources s WHERE s.research_id=r.id) AS n_sources, "
                "(SELECT COUNT(*) FROM findings f WHERE f.research_id=r.id) AS n_findings, "
                "(SELECT COUNT(*) FROM findings f WHERE f.research_id=r.id AND f.decision='en attente') AS n_pending, "
                "(SELECT COUNT(*) FROM findings f WHERE f.research_id=r.id AND f.decision='intégré') AS n_integrated "
                "FROM research r ORDER BY r.id DESC LIMIT ?", (limit,)).fetchall()
        return [self._run_row(r) for r in rows]

    def get(self, rid):
        with self.kb.lock:
            r = self.kb.db.execute("SELECT * FROM research WHERE id=?", (rid,)).fetchone()
            if not r:
                return None
            out = self._run_row(r)
            out["sources"] = [dict(x) for x in self.kb.db.execute(
                "SELECT * FROM research_sources WHERE research_id=? ORDER BY n", (rid,))]
            out["findings"] = [self._finding(x) for x in self.kb.db.execute(
                "SELECT * FROM findings WHERE research_id=? ORDER BY id", (rid,))]
        out["category"] = self.kb.category_path(out["category_id"]) if out["category_id"] else ""
        return out

    @staticmethod
    def _finding(r):
        d = dict(r)
        d["support"], d["contra"] = json.loads(d["support"]), json.loads(d["contra"])
        return d

    def pending(self, limit=100):
        with self.kb.lock:
            return [self._finding(r) | {"question": r["question"]} for r in self.kb.db.execute(
                "SELECT f.*, r.question FROM findings f JOIN research r ON r.id=f.research_id "
                "WHERE f.decision='en attente' ORDER BY f.confidence DESC, f.id DESC LIMIT ?", (limit,))]

    def delete(self, rid):
        """Retire une recherche de l'historique. Les connaissances déjà intégrées restent dans la base."""
        self._cancel.add(rid)
        with self.kb.lock:
            self.kb.db.execute("DELETE FROM findings WHERE research_id=? AND decision!='intégré'", (rid,))
            keep = self.kb.db.execute("SELECT COUNT(*) FROM findings WHERE research_id=?", (rid,)).fetchone()[0]
            if keep:
                self.kb.db.execute("UPDATE research SET status='archivé' WHERE id=?", (rid,))
            else:
                self.kb.db.execute("DELETE FROM research_sources WHERE research_id=?", (rid,))
                self.kb.db.execute("DELETE FROM research WHERE id=?", (rid,))
                self.kb.db.execute("DELETE FROM links WHERE a=? OR b=?", (f"r:{rid}", f"r:{rid}"))
            self.kb.db.commit()
        return {"kept": bool(keep)}

    def summary(self):
        runs = self.runs(8)
        with self.kb.lock:
            g = lambda q: self.kb.db.execute(q).fetchone()[0]
            return {"runs": [{k: r[k] for k in ("id", "question", "status", "mode", "origin", "created", "finished",
                                                "n_sources", "n_findings", "n_pending", "n_integrated", "summary")}
                             for r in runs],
                    "pending": g("SELECT COUNT(*) FROM findings WHERE decision='en attente'"),
                    "active": g("SELECT COUNT(*) FROM research WHERE status IN ('en file','en cours')"),
                    "total": g("SELECT COUNT(*) FROM research"),
                    "integrated": g("SELECT COUNT(*) FROM findings WHERE decision='intégré'")}

    def tracked_sources(self, limit=300):
        """Suivi des sources : par site, combien de documents lus et d'affirmations appuyées."""
        rules = self.kb.source_rules()
        sites = {}
        with self.kb.lock:
            for r in self.kb.db.execute("SELECT domain, provider, reliability, used, created FROM research_sources"):
                s = sites.setdefault(r["domain"], {"domain": r["domain"], "providers": set(), "read": 0, "used": 0,
                                                   "documents": 0, "reliability": r["reliability"], "last": 0})
                s["providers"].add(r["provider"])
                s["read"] += 1
                s["used"] += r["used"]
                s["last"] = max(s["last"], r["created"])
            for r in self.kb.db.execute("SELECT source, reliability, imported FROM documents WHERE source LIKE 'http%' "
                                        "AND source_type!='recherche'"):
                dom = site_of(r["source"])
                s = sites.setdefault(dom, {"domain": dom, "providers": set(), "read": 0, "used": 0, "documents": 0,
                                           "reliability": r["reliability"], "last": 0})
                s["documents"] += 1
                s["last"] = max(s["last"], r["imported"])
        for dom, rule in rules.items():
            sites.setdefault(dom, {"domain": dom, "providers": set(), "read": 0, "used": 0, "documents": 0,
                                   "reliability": rule["reliability"] or reliability_for("https://" + dom), "last": 0})
        out = []
        for s in sites.values():
            rule = rules.get(s["domain"])
            s["providers"] = sorted(s["providers"])
            s["auto"] = reliability_for("https://" + s["domain"]) if not s["read"] else s["reliability"]
            s["rule"] = {"reliability": rule["reliability"], "blocked": bool(rule["blocked"]), "note": rule["note"]} if rule else None
            if rule and rule["reliability"]:
                s["reliability"] = rule["reliability"]
            out.append(s)
        out.sort(key=lambda s: (-(s["used"] + s["documents"]), s["domain"]))
        return out[:limit]

    def thin_topics(self):
        """Sujets insuffisamment documentés, avec la recherche à lancer pour chacun."""
        out = []
        for g in self.kb.gaps()[:10]:
            out.append({"kind": "question sans source", "label": g["question"], "question": g["question"],
                        "detail": f"posée {g['count']} fois", "gap_id": g["id"]})
        with self.kb.lock:
            for r in self.kb.db.execute(
                    "SELECT e.name, COUNT(DISTINCT r.doc_id) AS docs, COUNT(*) AS deg FROM entities e JOIN relations r "
                    "ON r.src=e.id OR r.dst=e.id GROUP BY e.id HAVING deg>=3 AND docs<=1 ORDER BY deg DESC LIMIT 8"):
                out.append({"kind": "concept appuyé sur un seul document", "label": r["name"],
                            "question": f"{r['name']} : définition, propriétés et valeurs de référence",
                            "detail": f"{r['deg']} relations, 1 seul document source"})
            for r in self.kb.db.execute("SELECT f.claim, r.question FROM findings f JOIN research r ON r.id=f.research_id "
                                        "WHERE f.status='à confirmer' AND f.decision IN ('en attente','intégré') "
                                        "ORDER BY f.id DESC LIMIT 6"):
                out.append({"kind": "affirmation appuyée sur une seule source", "label": r["claim"][:160],
                            "question": r["claim"][:300], "detail": f"issue de la recherche « {r['question'][:60]} »"})
        used = [c for c in self.kb.categories() if c["parent_id"]]
        if any(c["n"] for c in used):
            for c in [c for c in used if c["n"] == 1][:6]:
                out.append({"kind": "catégorie avec un seul document", "label": c["name"],
                            "question": c["name"].split(" (")[0], "detail": "1 document"})
        return out

    def export(self):
        return {"recherches": [self.get(r["id"]) for r in self.runs(10 ** 6)], "veille": self.watches()}

    # ---------- veille ----------
    def watches(self):
        with self.kb.lock:
            return [dict(r) for r in self.kb.db.execute("SELECT * FROM watch ORDER BY id")]

    def add_watch(self, topic, every_days=7):
        topic = re.sub(r"\s+", " ", str(topic)).strip()[:200]
        if len(topic) < 3:
            raise ValueError("Sujet trop court")
        with self.kb.lock:
            self.kb.db.execute("INSERT OR IGNORE INTO watch(topic,every_days,created) VALUES(?,?,?)",
                               (topic, max(1.0, min(90.0, float(every_days))), time.time()))
            self.kb.db.commit()

    def update_watch(self, wid, enabled=None, every_days=None):
        with self.kb.lock:
            if enabled is not None:
                self.kb.db.execute("UPDATE watch SET enabled=? WHERE id=?", (1 if enabled else 0, wid))
            if every_days is not None:
                self.kb.db.execute("UPDATE watch SET every_days=? WHERE id=?", (max(1.0, min(90.0, float(every_days))), wid))
            self.kb.db.commit()

    def delete_watch(self, wid):
        with self.kb.lock:
            self.kb.db.execute("DELETE FROM watch WHERE id=?", (wid,))
            self.kb.db.commit()

    def run_due_watches(self, force_id=None):
        """Lance les veilles arrivées à échéance. -> identifiants des recherches créées"""
        now, out = time.time(), []
        for w in self.watches():
            due = w["enabled"] and (w["last_run"] or 0) + w["every_days"] * 86400 <= now
            if (force_id == w["id"]) or (force_id is None and due and self.settings.get("research_watch", True)):
                out.append(self.create(w["topic"], "rapide", "veille", watch_id=w["id"]))
                with self.kb.lock:
                    self.kb.db.execute("UPDATE watch SET last_run=? WHERE id=?", (now, w["id"]))
                    self.kb.db.commit()
        return out

    def _watch_loop(self):
        time.sleep(90)
        while True:
            try:
                self.run_due_watches()
            except Exception:
                pass
            time.sleep(900)
