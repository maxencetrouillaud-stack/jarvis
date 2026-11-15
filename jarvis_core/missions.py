"""Missions multi-outils : le cerveau central choisit les outils, les agents et l'ordre des actions, puis exécute
le plan en arrière-plan en transmettant les résultats d'une étape à l'autre.

Exemple : « Crée-moi une application sur les dernières technologies automobiles »
    1. chercheur  : web.research   — recherche, lecture des pages (onglets visibles), citations vérifiées, comparaison
    2. concepteur : image.generate — illustrations
    3. développeur: artifact.create — application interactive alimentée par les étapes 1 et 2
    4. vérificateur: artifact.verify — contrôle statique et à l'exécution, correction automatique bornée
    5. cerveau central : synthèse avec les sources, enregistrée dans la conversation et le projet

Garde-fous : nombre d'étapes et durée bornés, nouvel essai limité par outil, actions sensibles soumises à votre
accord (la mission attend), reprise après erreur à partir de l'étape qui a échoué, journal de chaque action.
Les résultats temporaires restent dans la mission : rien n'entre dans la mémoire permanente sans votre accord.
"""
import json
import re
import threading
import time
import unicodedata

from .artifacts import ArtifactError, guess_kind, is_engineering
from .browser import BrowserError
from .images import FORMATS, ImageError, STYLES
from .providers import ProviderError
from .tools import AGENTS, Context, NeedsApproval, Registry, Tool, ToolError

MISSION_STATES = ("en cours", "en attente", "terminée", "échec", "arrêtée", "interrompue")

# ----------------------------------------------------------------------------- détection d'intention
_V_CREATE = r"(?:g[ée]n[èe]re|cr[ée]e|cr[ée]er|dessine|fais|faire|produis|r[ée]alise|imagine|construis|d[ée]veloppe|" \
            r"programme|con[çc]ois|pr[ée]pare|monte|code|propose|mets en place|invente)"
_IMAGE_NOUN = r"(?:image|illustration|logo|dessin|rendu|visuel|affiche|ic[ôo]ne|portrait|photo(?!copie)|" \
              r"fond d['’][ée]cran|wallpaper|avatar|banni[èe]re|mascotte|croquis|peinture|poster|miniature|" \
              r"vignette|sticker|autocollant)s?"
_ART_NOUN = r"(?:application|appli|app\b|outil|calculateur|calculatrice|simulat(?:eur|ion)|visualisation|" \
            r"tableau de bord|dashboard|jeu|mini-jeu|graphique|courbe|page web|site(?: web| internet)?|prototype|" \
            r"interface|pr[ée]sentation|diaporama|diagramme|organigramme|sch[ée]ma|carte interactive|quiz|" \
            r"formulaire|artefact|animation|chronologie|frise|convertisseur|minuteur|chronom[èe]tre|" \
            r"comparateur|g[ée]n[ée]rateur|[ée]diteur|planificateur|tracker|suivi interactif|mod[èe]le 3d|vue 3d|" \
            r"visualiseur|fiche (?:interactive|de r[ée]vision interactive)|document structur[ée])s?"
_IMAGE_STRONG = re.compile(
    rf"\b{_V_CREATE}(?:-moi| moi|-nous| nous)?\b[^.?!\n]{{0,50}}\b{_IMAGE_NOUN}\b|"
    rf"\b(?:transforme|convertis|change)\b[^.?!\n]{{0,40}}\ben (?:un )?(?:rendu|image|illustration|dessin|photo)\b|"
    rf"\b(?:modifie|retouche|am[ée]liore|agrandis|recadre)\b[^.?!\n]{{0,25}}\b(?:cette|l['’]|la|mon|ma|ton|ta) ?(?:image|photo|illustration|logo|rendu|dessin)\b|"
    rf"\billustration technique\b|\brendu (?:r[ée]aliste|3d|photor[ée]aliste)\b", re.I)
_IMAGE_WEAK = re.compile(rf"^\W*(?:jarvis\W+)?(?:peux-tu |pourrais-tu )?(?:g[ée]n[èe]re|dessine|imagine)(?:-moi| moi)?\b|"
                         r"\ben 3d\b|\bphotor[ée]aliste\b", re.I)
_ART_STRONG = re.compile(rf"\b{_V_CREATE}(?:-moi| moi|-nous| nous)?\b[^.?!\n]{{0,60}}\b{_ART_NOUN}\b|\bartefact\b", re.I)
_EDIT_START = re.compile(
    r"^\W*(?:jarvis\W+)?(?:peux-tu |pourrais-tu |tu peux )?(?:change|changer|modifie|modifier|ajoute|ajouter|"
    r"enl[èe]ve|supprime|retire|remplace|mets|met|rends|agrandis|r[ée]duis|d[ée]place|corrige|am[ée]liore|traduis|"
    r"colore|passe|rajoute|augmente|diminue|centre|aligne|renomme|transforme|fais en sorte|inverse|affiche|cache|"
    r"masque|ralentis|acc[ée]l[èe]re|simplifie|optimise|utilise)\b", re.I)
_WEB = re.compile(
    r"(?:sur|dans) (?:internet|le web|google|la toile)|\ben ligne\b|recherche (?:sur|en ligne|web)|"
    r"\bcherche(?:-moi| moi)? (?:sur|en ligne|des infos|les derni)|"
    r"\bva sur\b|\bouvre le site\b|consulte (?:le site|la page)|compare (?:les )?prix|\bmeilleurs? prix\b|"
    r"\bactualit[ée]s?\b|derni[èe]res? (?:nouvelles|infos|technologies|versions|sorties|avanc[ée]es|tendances|innovations)|"
    r"\b(?:avanc[ée]es|innovations|nouveaut[ée]s|technologies|sorties|tendances) r[ée]centes|"
    r"trouve(?:-moi| moi)? (?:des|un|une|les) (?:pdf|documents?|articles?|sources?|liens?|publications?|fiches techniques)|"
    r"https?://\S+|\bv[ée]rifie (?:sur|avec) (?:plusieurs|des) sources", re.I)
_URL = re.compile(r"https?://[^\s<>\"')\]]+")
_SUMMARY_PAGE = re.compile(r"r[ée]sume|synth[ée]tise|que dit|explique|analyse|extrait|lis\b", re.I)

INTENT_PROMPT = """Classe la demande de l'utilisateur d'un assistant. Réponds uniquement par un objet JSON :
{"image": false, "modifier_image": false, "artefact": false, "modifier_artefact": false, "web": false}
- image : il veut qu'on GÉNÈRE une image (illustration, logo, rendu, dessin, photo réaliste, objet ou scène en 3D en image).
- modifier_image : il veut modifier l'image affichée ou fournie.
- artefact : il veut une création interactive affichée à part (application, outil, calculateur, simulation, visualisation,
  schéma, diagramme, tableau de bord, jeu, présentation, document structuré).
- modifier_artefact : il demande un changement dans l'artefact ouvert.
- web : il faut chercher sur Internet des informations récentes, des prix, ou consulter un site précis.
Toutes les valeurs à false si une simple réponse écrite suffit.
Contexte : {ctx}"""

PLAN_PROMPT = """Tu es le cerveau central de Jarvis. Tu planifies une mission en choisissant les OUTILS de tes agents.
Outils disponibles :
{tools}

Règles :
- 2 à {max_steps} étapes, dans l'ordre d'exécution ; chaque étape reçoit automatiquement les résultats des étapes
  précédentes (textes, sources, images, artefacts) ;
- n'utilise que les outils de la liste qui ne sont pas INDISPONIBLES ; recherche web avant la création quand des
  informations récentes ou précises sont nécessaires ; les images avant l'artefact qui les intègre ;
- après artifact.create ou artifact.engineer, ajoute artifact.verify ;
- « consigne » : ce que l'agent doit faire, précis et autonome (pour image.generate, la description de l'image).
Réponds uniquement par un objet JSON :
{{"objectif": "...", "etapes": [{{"outil": "web.research", "consigne": "..."}}]}}"""

QUERY_PROMPT = """Tu prépares une recherche web pour répondre à la demande ci-dessous. Donne 1 ou 2 requêtes de moteur de
recherche efficaces (mots-clés, langue la plus utile : français, ou anglais pour un sujet technique international).
Réponds uniquement par un objet JSON : {"requetes": ["...", "..."], "scientifique": false}"""

NOTES_PROMPT = """Tu es le chercheur de Jarvis. Voici une page web et la demande de l'utilisateur. Relève au plus 8
informations de la page UTILES pour la demande, chacune avec une citation EXACTE copiée mot pour mot de la page
(10 à 40 mots). N'ajoute rien qui ne figure pas dans la page. Si la page n'apporte rien, réponds [].
Réponds uniquement par un tableau JSON : [{"info": "...", "citation": "..."}]"""

COMPARE_PROMPT = """Tu es le vérificateur de Jarvis. Voici des informations extraites de plusieurs sources numérotées.
Regroupe celles qui disent la même chose, et repère les contradictions. Réponds uniquement par un objet JSON :
{"points": [{"info": "...", "sources": [1, 3], "statut": "confirmée|source unique|contradictoire", "note": ""}]}
« confirmée » seulement si au moins deux sources DIFFÉRENTES disent la même chose."""

SYNTH_PROMPT = """Tu es {name}, l'assistant personnel de l'utilisateur. Tes agents viennent d'exécuter une mission pour
répondre à sa demande. Rédige la réponse finale, en français :
- appuie-toi UNIQUEMENT sur les résultats ci-dessous, n'invente rien ;
- cite les sources web avec [W1], [W2]… (numéros de la liste SOURCES) ; précise si une information est confirmée par
  plusieurs sources ou ne vient que d'une seule, et signale les contradictions ;
- si des images ou des artefacts ont été créés, présente-les en une phrase (ils s'affichent sous ta réponse) ;
- si une étape a échoué ou a été refusée, dis-le simplement ;
- réponse claire et structurée, sans répéter tout le contenu des artefacts."""


def _norm(s):
    s = unicodedata.normalize("NFKC", s or "").lower()
    s = s.replace("’", "'").replace("«", '"').replace("»", '"')
    return re.sub(r"\s+", " ", s).strip()


def parse_obj(raw, opener="{", closer="}"):
    a, b = (raw or "").find(opener), (raw or "").rfind(closer)
    if a < 0 or b <= a:
        return None
    try:
        return json.loads(raw[a:b + 1])
    except ValueError:
        return None


def detect(text, open_artifact=None, last_image=None):
    """Règles rapides (sans appel au modèle). -> (besoins, sûr, raisons)."""
    t = text.strip()
    needs, reasons = [], []
    edit = bool(_EDIT_START.search(t))
    mentions_image = re.search(r"\b(?:cette|l['’]|la|mon|ma) ?(?:image|photo|illustration|logo|dessin|rendu)\b", t, re.I)
    if edit and open_artifact and not mentions_image and not _ART_STRONG.search(t):
        return ["artifact_edit"], True, ["modification demandée alors qu'un artefact est ouvert"]
    if last_image and (mentions_image or (edit and not open_artifact)) and not _ART_STRONG.search(t):
        if re.search(r"\b(?:modifie|retouche|ajoute|enl[èe]ve|supprime|retire|remplace|change|mets|transforme|rends|"
                     r"am[ée]liore|colore|agrandis)\b", t, re.I):
            return ["image_edit"], True, ["modification de la dernière image"]
    if _IMAGE_STRONG.search(t):
        needs.append("image")
        reasons.append("demande de création ou de modification d'image")
    art = _ART_STRONG.search(t)
    if art:
        # « fais-moi une image… » ne doit pas devenir un artefact ; « une application avec des illustrations » : les deux
        if not (needs and not re.search(_ART_NOUN, art.group(0), re.I)):
            needs.append("artifact")
            reasons.append("création interactive demandée")
            if "image" in needs and not re.search(r"illustr|images?\b|visuels?|photos?|logo", t, re.I):
                needs.remove("image")
    if _WEB.search(t):
        needs.append("web")
        reasons.append("informations à chercher sur Internet")
    sure = bool(needs)
    if not needs and (_IMAGE_WEAK.search(t) or (edit and (open_artifact or last_image))):
        sure = False
        reasons.append("intention ambiguë : classement par le modèle")
    return needs, sure, reasons


class Missions:
    def __init__(self, app):
        self.app = app
        self.ws, self.brain, self.settings = app.ws, app.brain, app.settings
        self.registry = Registry()
        self._threads, self._cancel, self._decisions = {}, {}, {}
        self._lock = threading.Lock()
        self._slots = threading.Semaphore(3)
        self._register_tools()
        # missions laissées « en cours » par un arrêt de Jarvis : elles pourront être reprises
        self.ws._x("UPDATE missions SET status='interrompue', updated=? WHERE status IN ('en cours','en attente')",
                   (time.time(),))

    # ------------------------------------------------------------------ modèles
    def _ref(self, requested="auto", text=""):
        try:
            return self.brain.pick_model(requested or "auto", text)[0][0]
        except ProviderError:
            return None

    def _need_ref(self, ctx, purpose="code"):
        ref = (ctx.data.get("ref_code") if purpose == "code" else ctx.data.get("ref")) if hasattr(ctx, "data") else None
        ref = ref or self._ref("auto", "code html javascript" if purpose == "code" else ctx.goal)
        if not ref:
            raise ToolError("Aucun modèle de texte disponible : ajoutez une clé ou démarrez Ollama (onglet Modèles).",
                            retry=False)
        return ref

    # ------------------------------------------------------------------ outils
    def _register_tools(self):
        R = self.registry
        app = self.app
        R.register(Tool("kb.search", "Base de connaissances", "chercheur",
                        "Cherche dans la bibliothèque technique, les connaissances et la mémoire de l'utilisateur.",
                        self._t_kb, {"requete": "texte à chercher"}, timeout=60))
        R.register(Tool("web.research", "Recherche web vérifiée", "chercheur",
                        "Cherche sur Internet, ouvre les pages pertinentes (visibles dans le Navigateur), relève des "
                        "citations exactes, compare et vérifie les sources.",
                        self._t_research, {"consigne": "ce qu'il faut trouver", "pages": "nombre de pages (1-6)"},
                        timeout=420, retries=0))
        R.register(Tool("web.read", "Lecture d'une page", "chercheur",
                        "Ouvre une adresse précise dans le Navigateur et en extrait le contenu.",
                        self._t_read, {"url": "adresse http(s)"}, timeout=90))
        R.register(Tool("web.page", "Page ouverte", "chercheur",
                        "Lit la page actuellement ouverte par l'utilisateur dans un onglet du Navigateur.",
                        self._t_page, {"tab_id": "onglet"}, timeout=60, retries=0))
        R.register(Tool("web.download", "Téléchargement", "chercheur",
                        "Télécharge un fichier depuis une adresse (demande toujours votre accord).",
                        self._t_download, {"url": "adresse du fichier"}, sensitive=True, timeout=300, retries=0))
        R.register(Tool("image.generate", "Génération d'image", "concepteur",
                        "Génère une ou plusieurs images à partir d'une description (illustrations, logos, rendus, "
                        "images techniques).",
                        self._t_image, {"consigne": "description de l'image", "format": "carré|portrait|paysage",
                                        "style": "|".join(STYLES), "variantes": "1 à 4"},
                        timeout=300, retries=1, available=app.images.available))
        R.register(Tool("image.edit", "Modification d'image", "concepteur",
                        "Modifie une image existante selon une consigne (ajouter, retirer, remplacer, changer de style).",
                        self._t_image_edit, {"consigne": "modification", "image_id": "image (par défaut la dernière)"},
                        timeout=300, retries=1, available=app.images.available))
        R.register(Tool("artifact.create", "Création d'artefact", "developpeur",
                        "Crée une application web, un tableau de bord, un jeu, une présentation, un document ou un "
                        "composant React interactif, en intégrant les données, sources et images des étapes précédentes.",
                        self._t_artifact, {"consigne": "ce qu'il faut créer", "type": "html|react|svg|mermaid|markdown|slides"},
                        timeout=420, retries=1))
        R.register(Tool("artifact.engineer", "Création technique", "ingenieur",
                        "Crée un schéma technique, une simulation physique ou mécanique, un calculateur scientifique ou "
                        "une visualisation 2D/3D, avec unités, formules et hypothèses.",
                        self._t_artifact, {"consigne": "ce qu'il faut créer", "type": "html|react|svg|mermaid"},
                        timeout=420, retries=1))
        R.register(Tool("artifact.edit", "Modification d'artefact", "developpeur",
                        "Modifie un artefact existant selon une consigne (élément sélectionné éventuel).",
                        self._t_artifact_edit, {"consigne": "modification", "artifact_id": "artefact (par défaut le dernier)"},
                        timeout=420, retries=1))
        R.register(Tool("artifact.verify", "Contrôle de l'artefact", "verificateur",
                        "Vérifie l'artefact (code, images, exécution dans un navigateur isolé) et corrige les erreurs.",
                        self._t_verify, {"artifact_id": "artefact (par défaut le dernier)"}, timeout=480, retries=0))

    # outils : implémentations ------------------------------------------------
    def _t_kb(self, ctx, requete="", consigne=""):
        q = requete or consigne or ctx.goal
        hits = self.app.hub.search(q, k=8)
        rows = hits.get("results", hits) if isinstance(hits, dict) else hits
        lines, srcs = [], []
        for h in rows[:8]:
            title = h.get("title") or h.get("label") or "Connaissance"
            txt = (h.get("content") or h.get("text") or h.get("excerpt") or "")[:900]
            lines.append(f"- {title} : {txt}")
        ctx.log(f"Base de connaissances : {len(lines)} résultat(s)", q[:200])
        return {"summary": f"{len(lines)} passage(s) trouvé(s) dans la base de connaissances",
                "text": ("Base de connaissances :\n" + "\n".join(lines)) if lines else "", "sources": srcs}

    def _t_read(self, ctx, url="", consigne=""):
        url = url or next(iter(_URL.findall(consigne or ctx.goal)), "")
        if not url:
            raise ToolError("Aucune adresse à lire", retry=False)
        ctx.log("Ouverture de la page", url)
        try:
            c = self.app.browser.agent_read(url, ctx.mission_id)
        except BrowserError as e:
            raise ToolError(str(e), retry=False) from None
        src = self.ws.add_source(c["url"], c["title"], c["text"][:600], ctx.project_id, ctx.conv_id, ctx.mission_id,
                                 {"via": "web.read"})
        return {"summary": f"Page lue : {c['title'][:80]}", "text": f"Contenu de {c['title']} ({c['url']}) :\n{c['text'][:24000]}",
                "sources": [{"url": c["url"], "title": c["title"], "excerpt": c["text"][:600], "resource_id": src["id"]}],
                "data": {"tab_id": c["tab_id"], "tables": c.get("tables", [])[:5]}}

    def _t_page(self, ctx, tab_id="", consigne=""):
        try:
            c = self.app.browser.page_content(tab_id)
        except (KeyError, BrowserError) as e:
            raise ToolError(f"Onglet illisible : {e}", retry=False) from None
        if not c["text"].strip():
            raise ToolError("La page ouverte ne contient pas de texte lisible.", retry=False)
        ctx.log("Lecture de la page ouverte", c["url"])
        src = self.ws.add_source(c["url"], c["title"] or c["url"], c["text"][:600], ctx.project_id, ctx.conv_id,
                                 ctx.mission_id, {"via": "page ouverte"})
        tables = "\n\n".join("\n".join(" | ".join(r) for r in t[:30]) for t in (c.get("tables") or [])[:4])
        return {"summary": f"Page lue : {(c['title'] or c['url'])[:80]}",
                "text": f"Page ouverte par l'utilisateur — {c['title']} ({c['url']}) :\n{c['text'][:30000]}"
                        + (f"\n\nTableaux de la page :\n{tables}" if tables else ""),
                "sources": [{"url": c["url"], "title": c["title"] or c["url"], "excerpt": c["text"][:600],
                             "resource_id": src["id"]}]}

    def _t_download(self, ctx, url="", consigne=""):
        url = url or next(iter(_URL.findall(consigne or "")), "")
        try:
            r = self.app.browser.download(url, ctx.project_id, ctx.mission_id, by_agent=True, approved=True)
        except (BrowserError, Exception) as e:
            raise ToolError(f"Téléchargement impossible : {e}", retry=False) from None
        return {"summary": f"Fichier téléchargé : {r['title']}", "text": "", "files": [r["id"]]}

    def _t_research(self, ctx, consigne="", pages=4):
        b = self.app.browser
        question = consigne or ctx.goal
        pages = max(1, min(6, int(pages or self.settings.get("mission_pages", 4))))
        ref = self._need_ref(ctx, "text")
        util = self.brain.utility_ref(ref)
        queries, science = [question[:200]], False
        try:
            j = parse_obj(self.brain.complete(util, QUERY_PROMPT, [{"role": "user", "content": question}], 300))
            if j and j.get("requetes"):
                queries = [str(q)[:200] for q in j["requetes"][:2] if str(q).strip()] or queries
                science = bool(j.get("scientifique"))
        except ProviderError:
            pass
        results, notes = [], []
        for q in queries:
            if ctx.cancelled():
                raise ToolError("Mission arrêtée", retry=False)
            ctx.log("Recherche", q)
            _, res, note = b.agent_search(q, ctx.mission_id, 8, science)
            if note:
                notes.append(note)
            for r in res:
                if r["url"] not in {x["url"] for x in results}:
                    results.append(r)
        if not results:
            raise ToolError("Aucun résultat de recherche." + (f" {notes[0]}" if notes else ""), retry=False)
        sources, extracted, skipped = [], [], []
        for r in results:
            if len(sources) >= pages or ctx.cancelled():
                break
            if r.get("text") and r.get("provider") in ("OpenAlex", "HAL", "arXiv"):
                page = {"url": r["url"], "title": r["title"], "text": r["text"]}   # résumé scientifique déjà obtenu
            else:
                ctx.log("Lecture", r["url"])
                try:
                    page = b.agent_read(r["url"], ctx.mission_id)
                except BrowserError as e:
                    skipped.append(f"{r['url']} : {e}")
                    continue
            if len(page["text"]) < 200:
                skipped.append(f"{r['url']} : page sans texte exploitable")
                continue
            try:
                raw = self.brain.complete(util, NOTES_PROMPT, [{"role": "user", "content":
                                          f"DEMANDE : {question}\n\nPAGE : {page['title']} ({page['url']})\n{page['text'][:18000]}"}],
                                          1400)
            except ProviderError as e:
                skipped.append(f"{r['url']} : extraction impossible ({e})")
                continue
            items = parse_obj(raw, "[", "]") or []
            body = _norm(page["text"])
            kept = []
            for it in items if isinstance(items, list) else []:
                if not isinstance(it, dict):
                    continue
                cit = _norm(str(it.get("citation", "")))
                if len(cit) >= 12 and cit in body:      # la citation doit exister mot pour mot dans la page
                    kept.append({"info": str(it.get("info", ""))[:500], "citation": str(it.get("citation", ""))[:400]})
            n = len(sources) + 1
            src = self.ws.add_source(page["url"], page["title"] or page["url"],
                                     (kept[0]["citation"] if kept else page["text"][:400]), ctx.project_id, ctx.conv_id,
                                     ctx.mission_id, {"via": "web.research", "verified_quotes": len(kept),
                                                      "provider": r.get("provider")})
            sources.append({"n": n, "url": page["url"], "title": page["title"] or page["url"],
                            "excerpt": (kept[0]["citation"] if kept else page["text"][:400]), "resource_id": src["id"],
                            "quotes": len(kept), "provider": r.get("provider", "")})
            for k in kept:
                extracted.append({**k, "source": n})
            ctx.emit("progress", text=f"{len(kept)} citation(s) vérifiée(s) sur {page['title'][:60]}")
        if not sources:
            raise ToolError("Aucune page n'a pu être lue. " + "; ".join(skipped[:3]), retry=False)
        points = []
        if len({e["source"] for e in extracted}) >= 2:
            try:
                listing = "\n".join(f"[{e['source']}] {e['info']} (« {e['citation']} »)" for e in extracted[:60])
                j = parse_obj(self.brain.complete(util, COMPARE_PROMPT, [{"role": "user", "content": listing}], 2000))
                valid = {s["n"] for s in sources}
                for p in (j or {}).get("points", [])[:30]:
                    srcs = sorted({int(x) for x in p.get("sources", []) if str(x).isdigit() and int(x) in valid})
                    if not srcs:
                        continue
                    statut = p.get("statut", "source unique")
                    if statut == "confirmée" and len(srcs) < 2:
                        statut = "source unique"    # le modèle ne peut pas déclarer confirmé ce qu'une seule source dit
                    points.append({"info": str(p.get("info", ""))[:500], "sources": srcs, "statut": statut,
                                   "note": str(p.get("note", ""))[:200]})
            except ProviderError:
                pass
        if not points:
            points = [{"info": e["info"], "sources": [e["source"]], "statut": "source unique", "note": ""} for e in extracted]
        confirmed = sum(1 for p in points if p["statut"] == "confirmée")
        contra = sum(1 for p in points if p["statut"] == "contradictoire")
        text = "Résultats de la recherche web (citations vérifiées dans les pages) :\n" + "\n".join(
            f"- {p['info']} [{', '.join('W' + str(s) for s in p['sources'])}] — {p['statut']}"
            + (f" ({p['note']})" if p['note'] else "") for p in points)
        if skipped:
            text += "\nPages non lues : " + "; ".join(skipped[:4])
        ctx.log(f"Recherche terminée : {len(sources)} source(s), {len(extracted)} citation(s) vérifiée(s)",
                f"{confirmed} confirmée(s), {contra} contradiction(s)")
        return {"summary": f"{len(sources)} source(s) lue(s), {len(extracted)} citation(s) vérifiée(s), "
                           f"{confirmed} information(s) confirmée(s) par plusieurs sources"
                           + (f", {contra} contradiction(s)" if contra else ""),
                "text": text, "sources": sources, "data": {"points": points, "notes": notes, "skipped": skipped}}

    def _t_image(self, ctx, consigne="", format="carré", style="libre", variantes=1, refs=None):
        prompt = consigne or ctx.goal
        fmt = format if format in FORMATS or ":" in str(format) else "carré"
        if style not in STYLES:
            style = "technique" if re.search(r"techni|m[ée]canism|sch[ée]ma", prompt, re.I) else \
                "logo" if re.search(r"\blogo\b", prompt, re.I) else \
                "3d" if re.search(r"\b3d\b", prompt, re.I) else "libre"
        refs = [int(r) for r in (refs or []) if str(r).isdigit()]
        ctx.log("Génération d'image", prompt[:300])
        try:
            out = self.app.images.generate(prompt, style, fmt, "", variantes, refs, ctx.project_id, ctx.conv_id,
                                           ctx.mission_id)
        except ImageError as e:
            raise ToolError(str(e), retry="HTTP 5" in str(e) or "aucune image" in str(e).lower()) from None
        ids = [r["id"] for r in out["images"]]
        return {"summary": f"{len(ids)} image(s) générée(s) avec {out['model']}",
                "text": "Images générées : " + ", ".join(f"jarvis-image:{i} ({prompt[:120]})" for i in ids),
                "images": ids, "data": {"errors": out["errors"]}}

    def _t_image_edit(self, ctx, consigne="", image_id=None):
        rid = int(image_id) if str(image_id or "").isdigit() else None
        if not rid:
            g = ctx.gathered()["images"]
            rid = g[-1] if g else ctx.data.get("last_image")
        if not rid:
            raise ToolError("Aucune image à modifier", retry=False)
        ctx.log(f"Modification de l'image {rid}", consigne[:300])
        try:
            out = self.app.images.edit(rid, consigne or ctx.goal, n=1, project_id=ctx.project_id, conv_id=ctx.conv_id,
                                       mission_id=ctx.mission_id)
        except ImageError as e:
            raise ToolError(str(e), retry=False) from None
        ids = [r["id"] for r in out["images"]]
        return {"summary": f"Image modifiée (nouvelle version à partir de l'image {rid})", "images": ids,
                "text": "Image modifiée : " + ", ".join(f"jarvis-image:{i}" for i in ids)}

    def _t_artifact(self, ctx, consigne="", type=None):
        ref = self._need_ref(ctx, "code")
        g = ctx.gathered()
        brief = consigne or ctx.goal
        if consigne and consigne != ctx.goal:
            brief = f"{consigne}\n\n(Demande initiale de l'utilisateur : {ctx.goal})"
        ctx.log("Création de l'artefact", brief[:300])
        try:
            a = self.app.artifacts.generate(ref, brief, type if type else guess_kind(ctx.goal + " " + (consigne or "")), g,
                                            ctx.project_id, ctx.conv_id, ctx.mission_id)
        except (ArtifactError, ProviderError) as e:
            raise ToolError(f"Création impossible : {e}") from None
        ctx.emit("artifact", artifact_id=a["id"], version=a["version"], title=a["title"])
        return {"summary": f"Artefact créé : « {a['title']} » ({a['kind_label']})", "artifacts": [a["id"]],
                "text": f"Artefact « {a['title']} » (n° {a['id']}, {a['kind_label']}) créé."}

    def _t_artifact_edit(self, ctx, consigne="", artifact_id=None, selection=None):
        aid = int(artifact_id) if str(artifact_id or "").isdigit() else None
        aid = aid or (ctx.gathered()["artifacts"] or [None])[-1] or ctx.data.get("artifact_id")
        if not aid:
            raise ToolError("Aucun artefact à modifier", retry=False)
        ref = self._need_ref(ctx, "code")
        ctx.log(f"Modification de l'artefact {aid}", consigne[:300])
        try:
            a = self.app.artifacts.edit(ref, aid, consigne or ctx.goal, selection or ctx.data.get("selection"),
                                        ctx.gathered())
        except (ArtifactError, ProviderError, KeyError) as e:
            raise ToolError(f"Modification impossible : {e}") from None
        ctx.emit("artifact", artifact_id=a["id"], version=a["version"], title=a["title"])
        return {"summary": f"« {a['title']} » modifié — version {a['version']} ({a.get('edit_mode', '')})",
                "artifacts": [a["id"]], "text": f"Artefact « {a['title']} » modifié (version {a['version']})."}

    def _t_verify(self, ctx, artifact_id=None, consigne=""):
        aid = int(artifact_id) if str(artifact_id or "").isdigit() else None
        aid = aid or (ctx.gathered()["artifacts"] or [None])[-1] or ctx.data.get("artifact_id")
        if not aid:
            raise ToolError("Aucun artefact à contrôler", retry=False)
        runtime = bool(self.settings.get("mission_runtime_check", True))
        res = self.app.artifacts.check(aid, runtime)
        fixes = 0
        while not res["ok"] and fixes < int(self.settings.get("mission_fix_attempts", 1)) and not ctx.cancelled():
            fixes += 1
            errs = [i["text"] for i in res["issues"] if i["level"] == "erreur"]
            ctx.log(f"Erreurs détectées, correction automatique ({fixes})", "; ".join(errs)[:500], ok=False)
            ctx.emit("progress", text=f"Correction automatique : {errs[0][:120]}")
            try:
                ref = self._need_ref(ctx, "code")
                a = self.app.artifacts.edit(ref, aid, "Corrige ces erreurs détectées par le contrôle, sans rien changer "
                                                      "d'autre :\n- " + "\n- ".join(errs[:8]))
                ctx.emit("artifact", artifact_id=a["id"], version=a["version"], title=a["title"])
            except (ArtifactError, ProviderError) as e:
                ctx.log("Correction impossible", str(e), ok=False)
                break
            res = self.app.artifacts.check(aid, runtime)
        warn = [i["text"] for i in res["issues"] if i["level"] == "attention"]
        errs = [i["text"] for i in res["issues"] if i["level"] == "erreur"]
        rt = res["runtime"]
        how = "exécuté dans un navigateur isolé" if rt.get("checked") else f"contrôle du code seulement ({rt.get('note', '')})"
        summary = ("Contrôle réussi — " + how) if res["ok"] else f"{len(errs)} erreur(s) restante(s) — " + how
        if fixes:
            summary += f", {fixes} correction(s) automatique(s)"
        ctx.log(summary, "; ".join(errs + warn)[:600], ok=res["ok"])
        return {"summary": summary, "artifacts": [aid],
                "text": f"Contrôle de l'artefact : {summary}." + (" Erreurs : " + "; ".join(errs) if errs else ""),
                "data": {"ok": res["ok"], "issues": res["issues"], "runtime": rt, "fixes": fixes}}

    # ------------------------------------------------------------------ planification
    def classify(self, text, ref, open_artifact=None, last_image=None):
        needs, sure, reasons = detect(text, open_artifact, last_image)
        if sure or not reasons or not ref:
            return needs, reasons
        ctxt = []
        if open_artifact:
            ctxt.append(f"un artefact est ouvert : « {open_artifact} »")
        if last_image:
            ctxt.append("une image a été générée juste avant")
        try:
            j = parse_obj(self.brain.complete(self.brain.utility_ref(ref), INTENT_PROMPT.replace(
                "{ctx}", "; ".join(ctxt) or "aucun"), [{"role": "user", "content": text}], 200))
        except ProviderError:
            return [], reasons
        if not isinstance(j, dict):
            return [], reasons
        out = []
        if j.get("modifier_artefact") and open_artifact:
            return ["artifact_edit"], reasons + ["le modèle y voit une modification de l'artefact"]
        if j.get("modifier_image") and last_image:
            return ["image_edit"], reasons + ["le modèle y voit une modification de l'image"]
        if j.get("web"):
            out.append("web")
        if j.get("image"):
            out.append("image")
        if j.get("artefact"):
            out.append("artifact")
        return out, reasons + (["le modèle a reconnu : " + ", ".join(out)] if out else [])

    def template(self, goal, needs, tab_id=None):
        eng = is_engineering(goal)
        art = "artifact.engineer" if eng else "artifact.create"
        steps = []
        urls = _URL.findall(goal)
        if "artifact_edit" in needs:
            return [{"tool": "artifact.edit", "consigne": goal}, {"tool": "artifact.verify"}]
        if "image_edit" in needs:
            return [{"tool": "image.edit", "consigne": goal}]
        if "page" in needs and tab_id:
            steps.append({"tool": "web.page", "args": {"tab_id": tab_id}})
        elif "web" in needs:
            if urls and (_SUMMARY_PAGE.search(goal) or len(goal) < 160):
                steps += [{"tool": "web.read", "args": {"url": u}} for u in urls[:3]]
            else:
                steps.append({"tool": "web.research", "consigne": goal})
        if "image" in needs:
            steps.append({"tool": "image.generate", "consigne": goal if "artifact" not in needs else
                          f"Illustration pour : {goal}", "args": {"variantes": 1}})
        if "artifact" in needs:
            steps += [{"tool": art, "consigne": goal}, {"tool": "artifact.verify"}]
        return steps

    def plan(self, goal, needs, ref, tab_id=None):
        """Plan d'outils : modèle pour les missions qui combinent plusieurs outils, modèle-type sinon."""
        base = self.template(goal, needs, tab_id)
        multi = len([n for n in needs if n in ("web", "image", "artifact")]) >= 2
        if not multi or not ref:
            return base, "plan type"
        max_steps = int(self.settings.get("mission_max_steps", 8))
        try:
            raw = self.brain.complete(self.brain.utility_ref(ref), PLAN_PROMPT.format(
                tools=self.registry.for_planner(), max_steps=max_steps), [{"role": "user", "content": goal}], 1200)
        except ProviderError:
            return base, "plan type (planificateur indisponible)"
        j = parse_obj(raw)
        if not isinstance(j, dict) or not isinstance(j.get("etapes"), list):
            return base, "plan type (plan du modèle illisible)"
        names = set(self.registry.names())
        steps = []
        for st in j["etapes"][:max_steps]:
            tool = str(st.get("outil", ""))
            if tool not in names or tool in ("web.page",):
                continue
            t = self.registry.get(tool)
            if t.available and not t.available()[0]:
                continue
            steps.append({"tool": tool, "consigne": str(st.get("consigne") or goal)[:1500]})
        tools = [s["tool"] for s in steps]
        if any(t in ("artifact.create", "artifact.engineer") for t in tools) and "artifact.verify" not in tools:
            steps.append({"tool": "artifact.verify"})
        # le plan du modèle doit couvrir les besoins détectés, sinon on garde le plan type
        need_tools = {"web": ("web.research", "web.read"), "image": ("image.generate",),
                      "artifact": ("artifact.create", "artifact.engineer")}
        for n in needs:
            if n in need_tools and not any(t in tools for t in need_tools[n]):
                return base, "plan type (le plan du modèle oubliait un outil nécessaire)"
        if len(steps) < 2:
            return base, "plan type"
        return steps, "plan établi par le cerveau central"

    # ------------------------------------------------------------------ cycle de vie
    def _save(self, mid, m):
        self.ws._x("UPDATE missions SET status=?, plan=?, updated=?, finished=?, error=?, message_id=?, answer=? WHERE id=?",
                   (m["status"], json.dumps(m["plan"], ensure_ascii=False), time.time(), m.get("finished"),
                    m.get("error"), m.get("message_id"), m.get("answer"), mid))

    def get(self, mid):
        r = self.ws._q("SELECT * FROM missions WHERE id=?", (mid,), one=True)
        if not r:
            return None
        r["plan"] = json.loads(r["plan"] or "{}")
        for st in r["plan"].get("steps", []):
            st["agent_label"] = AGENTS.get(st.get("agent"), st.get("agent"))
        return r

    def view(self, mid):
        m = self.get(mid)
        if not m:
            return None
        m["log"] = self.ws.agent_log(mid, 200)
        return m

    def list(self, project_id=None, status=None, limit=50):
        where, args = [], []
        if project_id is not None:
            where.append("project_id=?")
            args.append(project_id)
        if status:
            where.append("status=?")
            args.append(status)
        rows = self.ws._q("SELECT * FROM missions " + (f"WHERE {' AND '.join(where)} " if where else "")
                          + "ORDER BY id DESC LIMIT ?", (*args, limit))
        out = []
        for r in rows:
            p = json.loads(r["plan"] or "{}")
            steps = p.get("steps", [])
            r["plan"] = {"objective": p.get("objective"), "planner": p.get("planner"),
                         "steps": [{k: s.get(k) for k in ("n", "tool", "label", "agent", "state", "summary", "error")}
                                   for s in steps]}
            done = sum(1 for s in steps if s.get("state") in ("terminée", "ignorée", "refusée"))
            r["progress"] = round(100 * done / len(steps)) if steps else 0
            out.append(r)
        return out

    def _emit(self, mid, kind, **data):
        self.ws.bus.publish("mission", mission_id=mid, kind=kind, **data)

    def create(self, goal, conv_id=None, project_id=None, requested="auto", needs=None, steps=None, selection=None,
               artifact_id=None, last_image=None, tab_id=None, planner=""):
        goal = re.sub(r"\s+", " ", str(goal or "")).strip()
        if len(goal) < 3:
            raise ValueError("Décrivez la mission.")
        if conv_id and project_id is None:
            project_id = self.ws.conversation_project(conv_id)
        ref = self._ref(requested, goal)
        if needs is None:
            needs, reasons = self.classify(goal, ref, None, None)
            planner = planner or ("; ".join(reasons))
        if steps is None:
            if not needs:
                needs = ["web"]   # mission lancée à la main sans outil reconnu : on commence par chercher
            steps, how = self.plan(goal, needs, ref, tab_id)
            planner = (planner + " — " if planner else "") + how
        if not steps:
            raise ValueError("Aucun outil ne correspond à cette mission.")
        max_steps = int(self.settings.get("mission_max_steps", 8))
        out = []
        for i, st in enumerate(steps[:max_steps]):
            t = self.registry.get(st["tool"])
            out.append({"n": i + 1, "tool": t.name, "label": t.label, "agent": t.agent, "consigne": st.get("consigne", ""),
                        "args": st.get("args") or {}, "state": "en attente", "summary": "", "error": "", "attempts": 0})
        cited = [int(x) for x in dict.fromkeys(re.findall(r"jarvis-image:(\d+)", goal)) if self.ws.resource(int(x))]
        plan = {"objective": goal, "steps": out, "needs": needs, "planner": planner, "approved": [],
                "requested": requested, "data": {"selection": selection, "artifact_id": artifact_id,
                                                 "last_image": last_image, "images": cited[:8]}}
        now = time.time()
        mid = self.ws._x("INSERT INTO missions(goal,status,project_id,conv_id,plan,created,updated) VALUES(?,?,?,?,?,?,?)",
                         (goal[:2000], "en cours", project_id, conv_id, json.dumps(plan, ensure_ascii=False), now, now))
        self.ws.touch_project(project_id)
        self.ws.log("jarvis", f"Mission planifiée : {len(out)} étape(s)", " → ".join(s["label"] for s in out), True,
                    None, mid)
        self._emit(mid, "plan", objective=goal, planner=planner,
                   steps=[{k: s[k] for k in ("n", "tool", "label", "agent")} | {"agent_label": AGENTS[s["agent"]]}
                          for s in out])
        return mid

    def start(self, mid):
        with self._lock:
            if mid in self._threads and self._threads[mid].is_alive():
                return
            self._cancel[mid] = threading.Event()
            th = threading.Thread(target=self._run, args=(mid,), daemon=True, name=f"mission-{mid}")
            self._threads[mid] = th
        th.start()

    def launch(self, goal, **kw):
        mid = self.create(goal, **kw)
        self.start(mid)
        return mid

    def stop(self, mid):
        m = self.get(mid)
        if not m:
            raise KeyError("Mission introuvable")
        ev = self._cancel.get(mid)
        if ev:
            ev.set()
        d = self._decisions.get(mid)
        if d:
            d["decision"] = "refuser"
            d["event"].set()
        if m["status"] in ("en cours", "en attente"):
            m["status"], m["finished"] = "arrêtée", time.time()
            self._save(mid, m)
            self._emit(mid, "done", status="arrêtée")
        return self.view(mid)

    def decide(self, mid, decision):
        d = self._decisions.get(mid)
        if not d:
            raise KeyError("Aucune autorisation en attente pour cette mission")
        d["decision"] = "autoriser" if decision in ("autoriser", "approve", True) else "refuser"
        d["event"].set()
        return {"ok": True}

    def resume(self, mid):
        m = self.get(mid)
        if not m:
            raise KeyError("Mission introuvable")
        if m["status"] not in ("échec", "interrompue", "arrêtée"):
            raise ValueError("Seule une mission en échec, interrompue ou arrêtée peut être reprise.")
        for st in m["plan"]["steps"]:
            if st["state"] in ("échec", "en cours", "autorisation", "annulée"):
                st["state"], st["error"] = "en attente", ""
        m["status"], m["error"], m["finished"] = "en cours", None, None
        self._save(mid, m)
        self.ws.log("jarvis", "Mission reprise", "", True, None, mid)
        self._emit(mid, "status", status="en cours", text="Reprise de la mission")
        self.start(mid)
        return self.view(mid)

    # ------------------------------------------------------------------ exécution
    def _run(self, mid):
        with self._slots:
            try:
                self._execute(mid)
            except Exception as e:   # filet de sécurité : la mission ne doit jamais rester « en cours »
                m = self.get(mid)
                if m and m["status"] in ("en cours", "en attente"):
                    m["status"], m["error"], m["finished"] = "échec", f"Erreur interne : {type(e).__name__}: {e}", time.time()
                    self._save(mid, m)
                    self._emit(mid, "done", status="échec", error=m["error"])

    def _execute(self, mid):
        m = self.get(mid)
        plan = m["plan"]
        cancel = self._cancel.setdefault(mid, threading.Event())
        results = [st.get("result") for st in plan["steps"] if st["state"] == "terminée"]
        if (plan.get("data") or {}).get("images"):   # images désignées par l'utilisateur (jarvis-image:N)
            results.insert(0, {"images": plan["data"]["images"], "text": ""})
        ctx = Context(self.ws, mid, m["project_id"], m["conv_id"], m["goal"], results,
                      lambda kind, **d: self._emit(mid, kind, **d), set(plan.get("approved", [])), cancel)
        ctx.data = dict(plan.get("data") or {})
        ctx.data["ref"] = self._ref(plan.get("requested", "auto"), m["goal"])
        ctx.data["ref_code"] = self._ref(plan.get("requested", "auto"), "code html javascript " + m["goal"])
        deadline = time.time() + float(self.settings.get("mission_max_seconds", 900))
        if self.settings.get("mission_confirm_plan") and "plan" not in ctx.approved:
            if not self._wait_approval(mid, m, None, "Exécuter ce plan",
                                       " → ".join(s["label"] for s in plan["steps"]), "plan", ctx):
                m["status"], m["finished"], m["error"] = "arrêtée", time.time(), "plan refusé"
                self._save(mid, m)
                self._emit(mid, "done", status="arrêtée", error="plan refusé")
                return
        for st in plan["steps"]:
            if st["state"] in ("terminée", "ignorée", "refusée"):
                continue
            if cancel.is_set():
                return
            if time.time() > deadline:
                st["state"], st["error"] = "échec", "durée maximale de la mission dépassée"
                return self._finish(mid, m, ctx, failed=st)
            args = dict(st.get("args") or {})
            if st.get("consigne") and "consigne" in self.registry.get(st["tool"]).params:
                args.setdefault("consigne", st["consigne"])
            st["state"], st["started"] = "en cours", time.time()
            st["attempts"] = st.get("attempts", 0)
            self._save(mid, m)
            self._emit(mid, "step", n=st["n"], state="en cours", label=st["label"], agent=st["agent"],
                       agent_label=AGENTS[st["agent"]])
            while True:
                try:
                    res, attempts = self.registry.run(st["tool"], args, ctx)
                    st["attempts"] += attempts
                    break
                except NeedsApproval as na:
                    key = f"tool:{st['tool']}"
                    if self._wait_approval(mid, m, st, na.action, na.detail, key, ctx):
                        continue
                    st["state"], st["summary"], st["ended"] = "refusée", "Action refusée par l'utilisateur", time.time()
                    res = None
                    break
                except ToolError as e:
                    if cancel.is_set():
                        return
                    st["state"], st["error"], st["ended"] = "échec", str(e), time.time()
                    self._save(mid, m)
                    self._emit(mid, "step", n=st["n"], state="échec", label=st["label"], error=str(e),
                               agent=st["agent"], agent_label=AGENTS[st["agent"]])
                    res = False
                    break
            if res is False:
                if st["tool"] in ("artifact.verify", "kb.search"):
                    continue          # une étape de contrôle ou de complément qui échoue n'arrête pas la mission
                return self._finish(mid, m, ctx, failed=st)
            if res is None:
                self._save(mid, m)
                self._emit(mid, "step", n=st["n"], state="refusée", label=st["label"], agent=st["agent"],
                           agent_label=AGENTS[st["agent"]])
                continue
            st["state"], st["ended"] = "terminée", time.time()
            st["summary"] = res.get("summary", "")
            st["result"] = {"summary": st["summary"], "text": (res.get("text") or "")[:24000],
                            "sources": res.get("sources") or [], "images": res.get("images") or [],
                            "artifacts": res.get("artifacts") or [], "files": res.get("files") or [],
                            "data": _small(res.get("data"))}
            ctx.results.append(st["result"])
            self._save(mid, m)
            self._emit(mid, "step", n=st["n"], state="terminée", label=st["label"], summary=st["summary"],
                       agent=st["agent"], agent_label=AGENTS[st["agent"]], images=st["result"]["images"],
                       artifacts=st["result"]["artifacts"], sources=len(st["result"]["sources"]))
        self._finish(mid, m, ctx)

    def _wait_approval(self, mid, m, st, action, detail, key, ctx):
        ev = threading.Event()
        self._decisions[mid] = {"event": ev, "decision": None}
        if st:
            st["state"] = "autorisation"
            st["approval"] = {"action": action, "detail": detail[:500]}
        m["status"] = "en attente"
        self._save(mid, m)
        self.ws.log("jarvis", f"Autorisation demandée : {action}", detail[:500], True, None, mid)
        self._emit(mid, "approval", n=st["n"] if st else 0, action=action, detail=detail[:500])
        deadline = time.time() + 3600
        while not ev.wait(1.0):
            if ctx.cancelled() or time.time() > deadline:
                break
        dec = self._decisions.pop(mid, {}).get("decision")
        ok = dec == "autoriser"
        if ok:
            ctx.approved.add(key)
            m["plan"].setdefault("approved", []).append(key)
        if st:
            st.pop("approval", None)
            st["state"] = "en cours" if ok else "refusée"
        if not ctx.cancelled():
            m["status"] = "en cours"
        self._save(mid, m)
        self.ws.log("jarvis", "Action autorisée" if ok else "Action refusée", action, ok, None, mid)
        self._emit(mid, "decision", n=st["n"] if st else 0, approved=ok)
        return ok

    def _finish(self, mid, m, ctx, failed=None):
        g = ctx.gathered()
        given = set((m["plan"].get("data") or {}).get("images") or [])
        g["images"] = [i for i in g["images"] if i not in given]   # images fournies par l'utilisateur : pas « créées »
        steps = m["plan"]["steps"]
        if failed:
            for st in steps:
                if st["state"] == "en attente":
                    st["state"] = "annulée"
        answer, model = self._synthesis(mid, m, g, failed)
        meta = {"mission_id": mid, "artifacts": g["artifacts"], "images": g["images"],
                "web_sources": [{k: s.get(k) for k in ("n", "url", "title", "excerpt", "resource_id", "quotes", "provider")}
                                for s in g["sources"]],
                "mission": {"objective": m["plan"].get("objective"), "planner": m["plan"].get("planner"),
                            "steps": [{k: s.get(k) for k in ("n", "label", "agent", "state", "summary", "error")}
                                      | {"agent_label": AGENTS.get(s.get("agent"), "")} for s in steps]}}
        msg_id = None
        conv = m["conv_id"]
        if not conv:   # mission lancée depuis un projet : sa conversation est créée et rattachée au projet
            c = self.brain.store.create_conversation(m["goal"][:60])
            conv = c["id"]
            self.ws.set_conversation_project(conv, m["project_id"])
            self.brain.store.add_message(conv, "user", m["goal"])
            self.ws._x("UPDATE missions SET conv_id=? WHERE id=?", (conv, mid))
            m["conv_id"] = conv
        if answer.strip():
            msg_id = self.brain.store.add_message(conv, "assistant", answer, model, meta)
        status = "échec" if failed else "terminée"
        m.update(status=status, finished=time.time(), message_id=msg_id, answer=answer[:20000],
                 error=(f"Étape {failed['n']} ({failed['label']}) : {failed['error']}" if failed else None))
        self._save(mid, m)
        self.ws.log("jarvis", f"Mission {status}", m.get("error") or "", not failed, None, mid)
        self._emit(mid, "done", status=status, message_id=msg_id, conv_id=conv, error=m.get("error"),
                   artifacts=g["artifacts"], images=g["images"])

    def _synthesis(self, mid, m, g, failed):
        steps = m["plan"]["steps"]
        needs = m["plan"].get("needs") or []
        web = bool(g["sources"])
        if not web and not (len([n for n in needs if n in ("image", "artifact", "web")]) >= 2):
            return self._template_answer(m, g, failed), None
        ref = self._ref(m["plan"].get("requested", "auto"), m["goal"])
        if not ref:
            return self._template_answer(m, g, failed), None
        sources = "\n".join(f"[W{i + 1}] {s.get('title', '')} — {s.get('url', '')}" for i, s in enumerate(g["sources"]))
        # les numéros [Wn] des agents sont relatifs à leur étape : on les renumérote sur la liste globale
        text = "\n\n".join(g["text"])
        created = []
        for aid in g["artifacts"]:
            a = self.app.artifacts.get(aid)
            if a:
                created.append(f"Artefact « {a['title']} » ({a['kind_label']}, version {a['version']})")
        for rid in g["images"]:
            r = self.ws.resource(rid)
            if r:
                created.append(f"Image « {r['title']} »")
        status = "\n".join(f"Étape {s['n']} — {s['label']} : {s['state']}" + (f" ({s['error']})" if s.get('error') else "")
                           + (f" — {s['summary']}" if s.get('summary') else "") for s in steps)
        user = (f"DEMANDE : {m['goal']}\n\nÉTAPES :\n{status}\n\nRÉSULTATS :\n{text[:30000]}\n\n"
                f"CRÉATIONS :\n{chr(10).join(created) or '(aucune)'}\n\nSOURCES :\n{sources or '(aucune)'}")
        name = self.settings.get("assistant_name") or "Jarvis"
        answer = ""
        try:
            for chunk in self.brain.stream(ref, SYNTH_PROMPT.format(name=name), [{"role": "user", "content": user}],
                                           max_tokens=min(4096, int(self.settings.get("max_tokens", 4096)))):
                answer += chunk
                self._emit(mid, "delta", text=chunk)
        except ProviderError as e:
            if not answer:
                return self._template_answer(m, g, failed) + f"\n\n_(Synthèse impossible : {e})_", None
        n = len(g["sources"])
        bad = sorted({int(x) for x in re.findall(r"\[W(\d+)\]", answer) if int(x) > n or int(x) < 1})
        if bad:
            note = "\n\n> Contrôle : citation(s) " + ", ".join(f"[W{b}]" for b in bad) + " sans source correspondante."
            answer += note
            self._emit(mid, "delta", text=note)
        return answer, ref

    def _template_answer(self, m, g, failed):
        parts = []
        for aid in g["artifacts"]:
            a = self.app.artifacts.get(aid)
            if a:
                parts.append(f"Voici l'artefact **{a['title']}** ({a['kind_label'].lower()}, version {a['version']}). "
                             "Il s'affiche dans le panneau Artefact : vous pouvez le modifier en me le demandant ici.")
        imgs = [self.ws.resource(r) for r in g["images"]]
        imgs = [r for r in imgs if r]
        if imgs:
            model = (imgs[0].get("meta") or {}).get("model", "")
            parts.append(f"Voici {'l’image générée' if len(imgs) == 1 else f'les {len(imgs)} images générées'}"
                         + (f" (modèle {model})" if model else "") + ". Elle n'est conservée dans la galerie que si vous "
                         "cliquez sur **Enregistrer**." if len(imgs) == 1 else
                         f"Voici les {len(imgs)} images générées" + (f" (modèle {model})" if model else "")
                         + ". Seules celles que vous **enregistrez** restent dans la galerie.")
        for st in m["plan"]["steps"]:
            if st["tool"] == "artifact.verify" and st.get("summary"):
                parts.append(f"Contrôle : {st['summary']}.")
            if st["state"] == "refusée":
                parts.append(f"Étape « {st['label']} » non exécutée : vous l'avez refusée.")
        if failed:
            parts.append(f"La mission s'est arrêtée à l'étape {failed['n']} ({failed['label']}) : {failed['error']}\n\n"
                         "Vous pouvez la reprendre depuis l'onglet Projets ou le suivi de mission.")
        return "\n\n".join(parts) or "Mission terminée."

    # ------------------------------------------------------------------ conversation
    def from_chat(self, cid, text, requested="auto", artifact_id=None, selection=None, tool=None, tab_id=None):
        """Décide si un message de conversation doit devenir une mission. -> id de mission ou None."""
        if not self.settings.get("mission_auto_tools", True) and not tool and not selection and not tab_id:
            return None
        last_image = None
        rows = self.ws.resources(kind="image", conv_id=cid, limit=1)
        if rows and time.time() - rows[0]["created"] < 3 * 3600:
            last_image = rows[0]["id"]
        cited = [int(x) for x in re.findall(r"jarvis-image:(\d+)", text) if self.ws.resource(int(x))]
        if cited:            # image désignée explicitement (bouton « Modifier » d'une image)
            last_image = cited[0]
        open_title = None
        if artifact_id:
            a = self.app.artifacts.get(artifact_id)
            open_title = a["title"] if a else None
            if not a:
                artifact_id = None
        forced = {"image": ["image"], "artifact": ["artifact"], "web": ["web"], "page": ["page"]}.get(tool or "")
        if selection and artifact_id:
            forced = ["artifact_edit"]
        if tab_id and not forced:
            forced = ["page"]
        ref = self._ref(requested, text)
        if forced:
            needs, reasons = forced, [f"outil choisi : {forced[0]}"]
        else:
            needs, reasons = self.classify(text, ref, open_title, last_image)
        if not needs:
            return None
        if needs == ["web"] and not self.settings.get("mission_web_auto", True):
            return None
        mid = self.create(text, cid, None, requested, needs, None, selection, artifact_id, last_image, tab_id,
                          "; ".join(reasons))
        self.start(mid)
        return mid

    def follow(self, mid, since=0, timeout=None):
        """Événements de la mission (pour le flux de la conversation) jusqu'à sa fin."""
        last = since
        end = time.time() + (timeout or float(self.settings.get("mission_max_seconds", 900)) + 3700)
        while time.time() < end:
            for ev in self.ws.bus.since(last, 10, "mission"):
                last = ev["id"]
                if ev.get("mission_id") != mid:
                    continue
                yield ev
                if ev["kind"] == "done":
                    return
            m = self.get(mid)
            if m and m["status"] not in ("en cours", "en attente"):
                yield {"kind": "done", "mission_id": mid, "status": m["status"], "message_id": m.get("message_id"),
                       "conv_id": m.get("conv_id"), "error": m.get("error")}
                return


def _small(d):
    try:
        s = json.dumps(d or {}, ensure_ascii=False)
    except (TypeError, ValueError):
        return {}
    return d if len(s) < 40000 else {"note": "données trop volumineuses pour être conservées"}
