"""Cerveau central : analyse chaque demande, choisit la stratégie, exécute les étapes et contrôle le résultat.

Pour chaque message, l'orchestrateur :
  1. analyse la demande (objectif, type de tâche, complexité, ressources utiles, outils envisagés, question déjà posée) ;
  2. choisit une stratégie :
       - directe       : simple échange, aucun contexte nécessaire ;
       - contextuelle  : une réponse appuyée sur la mémoire et les connaissances retrouvées ;
       - approfondie   : plan de résolution, réponse, vérification par un second appel, correction si besoin ;
       - décomposée    : la tâche est découpée en étapes, exécutées dans l'ordre (chaque étape reçoit les
                         résultats de celles dont elle dépend), puis une synthèse finale est rédigée et vérifiée ;
  3. contrôle le résultat avant de le présenter (sources citées qui existent, action annoncée mais non exécutée,
     réponse identique à la précédente) ;
  4. garde une trace de chaque exécution (stratégie, étapes, appels aux modèles, durée, contrôles).

Garde-fous contre les boucles : nombre d'étapes et d'appels plafonné, durée maximale, étape dont le résultat
répète une étape précédente écartée, abandon de la décomposition après deux échecs (on revient à une réponse directe).
L'analyse elle-même est faite par des règles (aucun appel au modèle) : elle est rapide, gratuite et explicable.
"""
import json
import re
import time

from . import launcher
from .memory import tokens
from .providers import ProviderError

STRATEGIES = {
    "directe": "Réponse directe",
    "contextuelle": "Réponse avec contexte",
    "approfondie": "Réflexion approfondie",
    "décomposée": "Tâche décomposée en étapes",
}

_MULTI = re.compile(r"\b(?:puis|ensuite|apr[eè]s (?:ça|cela)|enfin|ainsi que|et aussi|en plus|d['’]abord|"
                    r"premi[eè]rement|deuxi[eè]mement|pour finir)\b", re.I)
_BIG = re.compile(r"[ée]tape par [ée]tape|de a à z|plan complet|projet complet|cahier des charges|rapport complet|"
                  r"dossier complet|programme complet|plan d['’]action|feuille de route|planning complet|"
                  r"analyse compl[eè]te|[ée]tude compl[eè]te|guide complet", re.I)
_LIST = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+\S", re.M)
_LAUNCH = re.compile(r"^\s*(?:peux-tu |tu peux |pourrais-tu )?(?:ouvre|ouvrir|lance|lancer|d[ée]marre|d[ée]marrer)\b", re.I)
_CALC = re.compile(r"\d\s*[-+×x*/^]\s*\d|\bcalcul|\br[ée]sou[ds]|\bdimensionn|\bcombien\b", re.I)
_CLAIMED = re.compile(r"\bj['’]ai (?:bien )?(?:lanc[ée]|ouvert l|envoy[ée]|supprim[ée]|t[ée]l[ée]charg[ée]|install[ée]|"
                      r"r[ée]serv[ée]|achet[ée]|pay[ée]|programm[ée] (?:un|une|le|la) (?:rappel|alarme|minuteur)|"
                      r"ajout[ée] (?:à|a|dans) (?:ton|votre) (?:agenda|calendrier))", re.I)

PLAN_STEPS_PROMPT = """Tu es le planificateur d'un assistant personnel. La demande ci-dessous est complexe.
Découpe-la en étapes de travail concrètes, dans l'ordre où elles doivent être faites (2 à {max_steps} étapes).
Chaque étape produit un résultat écrit (analyse, calcul, liste, texte, code…) qui servira à la réponse finale.
N'invente pas de besoin que la demande n'exprime pas, et ne fais pas d'étape « relire » ou « vérifier » :
la vérification est faite ensuite par un contrôleur.
"depend_de" liste les numéros des étapes PRÉCÉDENTES dont le résultat est nécessaire.
Réponds seulement par un objet JSON :
{{"objectif": "reformulation de l'objectif en une phrase", "etapes": [{{"titre": "…", "consigne": "…", "depend_de": []}}]}}"""

STEP_INSTRUCTION = """Tu exécutes UNE étape d'un travail en plusieurs étapes.
Demande initiale de l'utilisateur :
<demande>
{request}
</demande>
{deps}
Étape {n} sur {total} — {title}
Consigne : {instruction}

Produis uniquement le résultat de cette étape, complet et précis, sans introduction ni conclusion générale.
Si une information manque, dis-le explicitement au lieu de l'inventer."""

EXPAND_PROMPT = """Une mémoire personnelle doit être interrogée pour répondre à la demande ci-dessous, mais une première
recherche n'a rien trouvé. Donne jusqu'à 8 mots-clés, synonymes ou formulations courtes qui pourraient apparaître dans
un souvenir utile (par exemple : « boulot » pour « travail », « appartement » pour « où j'habite »).
Réponds seulement par un tableau JSON de chaînes.

Demande : {text}"""


def _jaccard(a, b):
    a, b = set(tokens(a)), set(tokens(b))
    return len(a & b) / len(a | b) if a and b else 0.0


def parse_json(text, opener="[", closer="]"):
    a, b = text.find(opener), text.rfind(closer)
    if a < 0 or b <= a:
        return None
    try:
        return json.loads(text[a:b + 1])
    except ValueError:
        return None


def objective(text):
    """Objectif de la demande, réduit à sa première phrase utile (sans formule de politesse)."""
    t = re.sub(r"\s+", " ", text).strip()
    t = re.sub(r"^(?:salut|bonjour|bonsoir|hello|coucou|dis|alors|ok|bon)\b[\s,!.]*", "", t, flags=re.I)
    m = re.match(r"(.{12,}?[.?!])(?:\s|$)", t)
    first = (m.group(1) if m else t)[:180]
    return first + ("…" if len(first) < len(t) and not m else "")


def assess(text, route, small_talk=False):
    """Complexité de la demande, avec les raisons (règles explicables, sans appel au modèle)."""
    t = text.strip()
    if small_talk:
        return "simple", ["simple échange"]
    reasons, score = [], 0
    n = len(t)
    if n > 600:
        score += 2
        reasons.append(f"demande longue ({n} caractères)")
    elif n > 250:
        score += 1
    items = len(_LIST.findall(t))
    if items >= 3:
        score += 2
        reasons.append(f"{items} points listés")
    chain = len(_MULTI.findall(t))
    if chain >= 2:
        score += 1
        reasons.append("plusieurs actions enchaînées")
    q = t.count("?")
    if q >= 3:
        score += 1
        reasons.append(f"{q} questions dans le même message")
    big = _BIG.search(t)
    if big:
        score += 2
        reasons.append(f"travail d'ensemble demandé (« {big.group(0)} »)")
    if route in ("reasoning", "code") and n > 120:
        score += 1
        reasons.append("problème technique ou de raisonnement")
    if score >= 3:
        return "complexe", reasons
    if score >= 1 or route in ("reasoning", "code") or _CALC.search(t):
        return "moyenne", reasons or (["calcul ou raisonnement"] if route != "code" else ["programmation"])
    return "simple", reasons or ["demande courte et directe"]


class Orchestrator:
    def __init__(self, brain):
        self.brain = brain
        self.settings = brain.settings

    # ------------------------------------------------------------ analyse
    def analyze(self, text, route, plan, deep, prior_user_texts=()):
        s = self.settings
        complexity, reasons = assess(text, route, plan.get("small_talk", False))
        decompose = s.get("orchestrator_decompose", "auto") != "jamais"
        if complexity == "complexe" and decompose:
            strategy = "décomposée"
        elif deep:
            strategy = "approfondie"
        elif plan.get("small_talk"):
            strategy = "directe"
        else:
            strategy = "contextuelle"
        mode = s.get("orchestrator_verify", "auto")
        if deep:
            verify, why = True, "réflexion approfondie demandée"
        elif mode == "toujours":
            verify, why = True, "vérification systématique (réglage)"
        elif mode == "jamais":
            verify, why = False, "vérification désactivée (réglage)"
        elif strategy == "décomposée":
            verify, why = True, "tâche complexe : la synthèse est contrôlée"
        else:
            verify, why = False, "demande simple ou moyenne : pas de second appel"
        tools = []
        if _LAUNCH.match(text) and len(text) < 80:
            found = launcher.find(s.get("apps") or [], text)
            if found:
                tools.append({"tool": "application", "name": found["name"], "query": text,
                              "label": f"Lancer {found['name']}", "needs_confirmation": True})
        repeated = None
        for i, prev in enumerate(prior_user_texts):
            if len(set(tokens(text))) >= 3 and _jaccard(text, prev) >= 0.8:
                repeated = {"index": i, "text": prev[:160]}
        resources = []
        if plan.get("memory"):
            resources.append({"essentiel": "mémoire : souvenirs essentiels", "ciblée": "mémoire : recherche ciblée",
                              "complète": "mémoire : recherche complète"}[plan["memory"]])
        if plan.get("kb"):
            resources.append("base de connaissances" + (f" (domaine : {plan['domain']})" if plan.get("domain") else ""))
        if plan.get("recall"):
            resources.append("anciennes conversations")
        return {"objective": objective(text), "route": route, "complexity": complexity, "reasons": reasons,
                "strategy": strategy, "strategy_label": STRATEGIES[strategy], "verify": verify, "verify_reason": why,
                "tools": tools, "repeated": repeated, "resources": resources}

    # ------------------------------------------------------------ mémoire : recherche reformulée
    def expand_memories(self, text, memories, ref, top_k):
        """Relance la recherche en mémoire avec des mots-clés proposés par le modèle utilitaire,
        quand une demande personnelle n'a rien trouvé alors que la mémoire contient des souvenirs."""
        store = self.brain.store
        if not self.settings.get("memory_query_expansion", True):
            return []
        found = {m["id"] for m in memories}
        with store.lock:
            total = store.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        if total <= len(found):
            return []
        try:
            raw = self.brain.complete(ref, "", [{"role": "user", "content": EXPAND_PROMPT.format(text=text[:1500])}], 200, 0.0)
        except ProviderError:
            return []
        words = parse_json(raw)
        if not isinstance(words, list):
            return []
        words = [str(w).strip()[:40] for w in words if str(w).strip()][:8]
        if not words:
            return []
        extra = []
        for m in store.search_memories(" ".join(words), top_k, None, mark_used=True):
            if m["id"] not in found and not m.get("core"):
                m["found_by"] = "reformulation"
                extra.append(m)
        return extra[:top_k]

    # ------------------------------------------------------------ décomposition
    def decompose(self, ref, text):
        """Plan d'exécution {objectif, etapes[{n, titre, consigne, depend_de}]} ou None si le modèle n'en donne pas."""
        max_steps = max(2, min(8, int(self.settings.get("orchestrator_max_steps", 6))))
        try:
            raw = self.brain.complete(ref, PLAN_STEPS_PROMPT.format(max_steps=max_steps),
                                      [{"role": "user", "content": text[:8000]}], 900, 0.0)
        except ProviderError:
            return None
        j = parse_json(raw, "{", "}")
        if not isinstance(j, dict) or not isinstance(j.get("etapes"), list):
            return None
        steps, seen = [], set()
        for it in j["etapes"]:
            if not isinstance(it, dict):
                continue
            title = str(it.get("titre") or "").strip()[:120]
            instr = str(it.get("consigne") or title).strip()[:1200]
            key = " ".join(tokens(instr))
            if not title or not key or key in seen:  # étape vide ou en double : ignorée
                continue
            seen.add(key)
            n = len(steps) + 1
            deps = []
            for d in it.get("depend_de") or []:
                try:
                    d = int(d)
                except (TypeError, ValueError):
                    continue
                if 1 <= d < n and d not in deps:  # seulement des étapes précédentes : pas de cycle possible
                    deps.append(d)
            steps.append({"n": n, "titre": title, "consigne": instr, "depend_de": deps})
            if len(steps) >= max_steps:
                break
        if len(steps) < 2:
            return None
        return {"objectif": str(j.get("objectif") or "").strip()[:300], "etapes": steps}

    def run_steps(self, ref, system, request, plan, budget):
        """Exécute les étapes dans l'ordre. Générateur d'événements ; le résultat est dans budget["steps"]."""
        results = []
        failures = 0
        for st in plan["etapes"]:
            if self.brain.turn_calls() >= budget["max_calls"] - 2 or time.time() > budget["deadline"]:
                results.append({**st, "etat": "non exécutée", "note": "limite d'appels ou de durée atteinte"})
                yield {"type": "step", "n": st["n"], "title": st["titre"], "state": "non exécutée",
                       "note": "limite d'appels ou de durée atteinte"}
                continue
            yield {"type": "step", "n": st["n"], "title": st["titre"], "state": "en cours"}
            deps = [r for r in results if r["n"] in st["depend_de"] and r.get("sortie")]
            dep_txt = ""
            if deps:
                dep_txt = "Résultats des étapes dont celle-ci dépend :\n" + "\n\n".join(
                    f"<etape n=\"{r['n']}\" titre=\"{r['titre']}\">\n{r['sortie'][:3000]}\n</etape>" for r in deps)
            prompt = STEP_INSTRUCTION.format(request=request[:6000], deps=dep_txt, n=st["n"],
                                             total=len(plan["etapes"]), title=st["titre"], instruction=st["consigne"])
            out, note, state = "", "", "terminée"
            try:
                out = self.brain.complete(ref, system, [{"role": "user", "content": prompt}],
                                          min(2000, self.settings.get("max_tokens", 4096)), 0.3).strip()
            except ProviderError as e:
                state, note = "échec", str(e)[:200]
            if state == "terminée" and not out:
                state, note = "échec", "le modèle n'a rien renvoyé"
            if state == "terminée":
                twin = next((r for r in results if r.get("sortie") and _jaccard(out, r["sortie"]) >= 0.85), None)
                if twin:  # l'étape répète une étape précédente : on ne la transmet pas deux fois
                    state, note, out = "redondante", f"résultat presque identique à l'étape {twin['n']}", ""
            if state == "échec":
                failures += 1
            results.append({**st, "etat": state, "note": note, "sortie": out})
            yield {"type": "step", "n": st["n"], "title": st["titre"], "state": state, "note": note,
                   "summary": out[:280]}
            if failures >= 2:
                budget["aborted"] = "deux étapes ont échoué : abandon de la décomposition, réponse directe"
                break
        budget["steps"] = results

    @staticmethod
    def synthesis_block(results):
        done = [r for r in results if r.get("sortie")]
        if not done:
            return ""
        body = "\n\n".join(f"<etape n=\"{r['n']}\" titre=\"{r['titre']}\">\n{r['sortie'][:4000]}\n</etape>" for r in done)
        missing = [r for r in results if not r.get("sortie") and r["etat"] != "redondante"]
        miss = ""
        if missing:
            miss = ("\nCes étapes n'ont pas pu être faites ; dis-le dans la réponse au lieu d'en inventer le contenu : "
                    + ", ".join(f"{r['n']}. {r['titre']}" for r in missing))
        return ("Tu as traité cette demande en plusieurs étapes. Voici leurs résultats (données de travail, à vérifier et "
                "à assembler) :\n<etapes>\n" + body + "\n</etapes>" + miss + "\nRédige maintenant la réponse finale, "
                "complète et cohérente, à la demande initiale. Ne recopie pas les étapes telles quelles et corrige une "
                "étape si elle contient une erreur.")

    # ------------------------------------------------------------ contrôles
    @staticmethod
    def controls(answer, sources, actions_done=(), previous_answer=""):
        """Contrôles automatiques avant présentation (sans appel au modèle)."""
        out = []
        cited = {int(n) for n in re.findall(r"\[S(\d+)\]", answer)}
        bad = sorted(c for c in cited if c < 1 or c > len(sources))
        if bad:
            out.append({"level": "erreur", "text": "cite une source qui n'existe pas : "
                        + ", ".join(f"[S{n}]" for n in bad)})
        m = _CLAIMED.search(answer)
        if m and not actions_done:
            out.append({"level": "attention", "text": f"annonce une action (« {m.group(0)} ») alors qu'aucune action "
                                                      "n'a été exécutée pendant cette réponse"})
        if previous_answer and len(answer) > 80 and _jaccard(answer, previous_answer) >= 0.9:
            out.append({"level": "attention", "text": "réponse presque identique à la précédente"})
        return out
