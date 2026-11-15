"""Registre des outils : une interface unique pour le navigateur, le générateur d'images, les artefacts et la
base de connaissances. Le cerveau central (missions) n'appelle jamais un module directement : il passe par ici,
ce qui donne à chaque appel les mêmes garde-fous (durée maximale, nouvel essai borné, autorisation pour les
actions sensibles, journalisation).

Un outil reçoit un contexte (mission, projet, résultats des étapes précédentes) et des arguments, et renvoie un
dictionnaire standard :
    {"summary": str,            # une phrase lisible par l'utilisateur
     "text": str,               # contenu transmis aux étapes suivantes (notes, extraits…)
     "sources": [{url,title,excerpt,resource_id}],
     "images": [resource_id], "artifacts": [artifact_id], "data": {...}}
"""
import threading
import time
from dataclasses import dataclass, field

AGENTS = {
    "jarvis": "Cerveau central",
    "chercheur": "Chercheur",
    "concepteur": "Concepteur",
    "developpeur": "Développeur",
    "ingenieur": "Ingénieur",
    "verificateur": "Vérificateur",
}


class ToolError(Exception):
    """Échec d'un outil (message destiné à l'utilisateur)."""

    def __init__(self, msg, retry=True):
        super().__init__(msg)
        self.retry = retry


class NeedsApproval(Exception):
    """L'outil veut faire une action sensible : la mission attend l'accord de l'utilisateur."""

    def __init__(self, action, detail=""):
        super().__init__(action)
        self.action, self.detail = action, detail


@dataclass
class Tool:
    name: str
    label: str
    agent: str
    description: str
    run: callable
    params: dict = field(default_factory=dict)
    sensitive: bool = False          # demande toujours l'accord avant de s'exécuter
    timeout: float = 240.0
    retries: int = 1                 # nouvel essai après un échec (0 = aucun)
    available: callable = None       # () -> (bool, raison) ; None = toujours disponible

    def state(self):
        ok, why = (True, "") if not self.available else self.available()
        return {"name": self.name, "label": self.label, "agent": self.agent, "agent_label": AGENTS.get(self.agent, self.agent),
                "description": self.description, "params": self.params, "sensitive": self.sensitive,
                "available": ok, "unavailable_reason": "" if ok else why}


class Context:
    """Ce qu'un outil sait de la mission en cours, et ce qu'il peut signaler."""

    def __init__(self, ws, mission_id=None, project_id=None, conv_id=None, goal="", results=None, emit=None,
                 approved=None, cancel=None):
        self.ws, self.mission_id, self.project_id, self.conv_id, self.goal = ws, mission_id, project_id, conv_id, goal
        self.results = results if results is not None else []
        self._emit = emit
        self.approved = approved if approved is not None else set()
        self.cancel = cancel or threading.Event()
        self.agent = "jarvis"
        self.tool = None

    def emit(self, kind, **data):
        if self._emit:
            self._emit(kind, **data)

    def log(self, action, detail="", ok=True):
        if self.ws:
            self.ws.log(self.agent, action, detail, ok, self.tool, self.mission_id)

    def cancelled(self):
        return self.cancel.is_set()

    def require(self, key, action, detail=""):
        """Bloque une action sensible tant que l'utilisateur ne l'a pas autorisée (clé unique par action)."""
        if key not in self.approved:
            raise NeedsApproval(action, detail)

    # résultats des étapes précédentes, regroupés pour l'étape suivante
    def gathered(self):
        out = {"text": [], "sources": [], "images": [], "artifacts": []}
        for r in self.results:
            if not r:
                continue
            if r.get("text"):
                out["text"].append(r["text"])
            for s in r.get("sources") or []:
                if s.get("url") not in {x.get("url") for x in out["sources"]}:
                    out["sources"].append(s)
            out["images"] += [i for i in r.get("images") or [] if i not in out["images"]]
            out["artifacts"] += [a for a in r.get("artifacts") or [] if a not in out["artifacts"]]
        return out


class Registry:
    def __init__(self):
        self._tools = {}

    def register(self, tool: Tool):
        if tool.agent not in AGENTS:
            raise ValueError(f"Agent inconnu : {tool.agent}")
        self._tools[tool.name] = tool
        return tool

    def get(self, name) -> Tool:
        t = self._tools.get(name)
        if not t:
            raise ToolError(f"Outil inconnu : {name}", retry=False)
        return t

    def names(self):
        return list(self._tools)

    def describe(self):
        return [t.state() for t in self._tools.values()]

    def for_planner(self):
        lines = []
        for t in self._tools.values():
            ok, why = (True, "") if not t.available else t.available()
            if t.name.startswith("_"):
                continue
            params = ", ".join(f"{k} : {v}" for k, v in t.params.items())
            lines.append(f"- {t.name} ({AGENTS[t.agent]}) — {t.description}"
                         + (f" Paramètres : {params}." if params else "")
                         + ("" if ok else f" [INDISPONIBLE : {why}]"))
        return "\n".join(lines)

    def run(self, name, args, ctx: Context):
        """Exécute un outil avec ses garde-fous. Renvoie (résultat, essais). Lève ToolError ou NeedsApproval."""
        tool = self.get(name)
        ok, why = (True, "") if not tool.available else tool.available()
        if not ok:
            raise ToolError(f"{tool.label} indisponible : {why}", retry=False)
        if tool.sensitive:
            ctx.require(f"tool:{name}", f"{tool.label}", str(args)[:300])
        ctx.agent, ctx.tool = tool.agent, name
        attempts, last = 0, None
        while attempts <= tool.retries:
            attempts += 1
            if ctx.cancelled():
                raise ToolError("Mission arrêtée", retry=False)
            box = {}

            def target():
                try:
                    box["res"] = tool.run(ctx, **(args or {}))
                except BaseException as e:  # transmis au fil principal
                    box["err"] = e
            th = threading.Thread(target=target, daemon=True, name=f"outil-{name}")
            t0 = time.time()
            th.start()
            th.join(tool.timeout)
            if th.is_alive():
                ctx.cancel.set()   # l'outil vérifie ce drapeau entre ses opérations
                ctx.log(f"{tool.label} : délai dépassé ({int(tool.timeout)} s)", ok=False)
                raise ToolError(f"{tool.label} : délai dépassé ({int(tool.timeout)} s)", retry=False)
            if "err" in box:
                e = box["err"]
                if isinstance(e, NeedsApproval):
                    raise e
                last = e if isinstance(e, ToolError) else ToolError(f"{type(e).__name__} : {e}")
                ctx.log(f"{tool.label} : échec (essai {attempts})", str(last), ok=False)
                if not getattr(last, "retry", True):
                    break
                time.sleep(min(2.0, 0.4 * attempts))
                continue
            res = box.get("res") or {}
            res.setdefault("summary", tool.label)
            res["duration"] = round(time.time() - t0, 2)
            return res, attempts
        raise last or ToolError(f"{tool.label} : échec")
