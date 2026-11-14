"""Chemins de données et paramètres persistants (settings.json)."""
import copy
import json
import os
import sys
import threading
from pathlib import Path


def data_dir() -> Path:
    env = os.environ.get("JARVIS_HOME")
    if env:
        p = Path(env)
    elif sys.platform == "win32":
        p = Path(os.environ.get("APPDATA", str(Path.home()))) / "Jarvis"
    else:
        p = Path.home() / ".jarvis"
    p.mkdir(parents=True, exist_ok=True)
    (p / "backups").mkdir(exist_ok=True)
    return p


DEFAULT_PERSONALITY = (
    "Tu es {name}, l'assistant personnel de {user}. Tu es direct, précis et chaleureux, "
    "avec une pointe d'humour discret. Tu réponds en français sauf demande contraire. "
    "Tu raisonnes étape par étape sur les problèmes complexes, tu dis clairement quand tu "
    "n'es pas sûr, et tu ne prétends jamais avoir fait une action que tu n'as pas faite."
)

DEFAULTS = {
    "assistant_name": "Jarvis",
    "user_name": "",
    "personality": DEFAULT_PERSONALITY,
    "default_model": "",          # "fournisseur:modèle"
    "auto_route": True,
    "routes": {"code": "", "reasoning": "", "creative": "", "quick": "", "general": ""},
    "utility_model": "",          # modèle (idéalement rapide) pour mémoire / plans / vérification
    "embedding_model": "auto",    # "auto", "none" ou "fournisseur:modèle"
    "memory_auto_extract": True,
    "memory_top_k": 8,
    "search_past_conversations": True,
    "deep_mode_default": False,
    "temperature": 0.7,
    "max_tokens": 4096,
    "history_chars": 24000,
    "backup_keep": 7,
    "kb_enabled": True,            # interroger la base de connaissances pendant les conversations
    "kb_top_k": 6,
    "kb_graph_auto": True,         # extraire les relations à l'import (un appel au modèle utilitaire)
    "kb_auto_categorize": True,
    "kb_require_validation": True, # les propositions d'enrichissement attendent votre accord
    "kb_stale_days": 180,
    "kb_wikipedia_lang": "fr",
    "kb_auto_links": True,         # relier chaque nouvelle connaissance à ce qui existe déjà
    "library_folder": "",          # dossier du PC indexé automatiquement (bibliothèque technique)
    "library_watch": False,        # réanalyser ce dossier régulièrement
    "library_scan_minutes": 10,
    "research_sources": {"wikipedia": True, "hal": True, "openalex": True, "arxiv": True, "web": True},
    "research_require_validation": True,   # rien n'entre dans la base sans votre accord
    "research_on_gap": "proposer",         # "proposer" une recherche quand la base ne sait pas, ou "jamais"
    "research_watch": True,                # veille : relancer les sujets suivis à leur échéance
    "update_check": True,          # chercher les mises à jour au démarrage puis toutes les 6 heures
    "update_repo": "",             # dépôt GitHub public des versions (vide : dépôt officiel de Jarvis)
    "voice_wake_word": "jarvis",   # mot-clé du mode mains libres
    "voice_handsfree": False,      # écoute permanente du mot-clé
    "voice_speak": True,           # lire les réponses à voix haute quand la demande est vocale
    "voice_barge_in": True,        # couper la parole de l'assistant en parlant
    "voice_name": "",              # voix de synthèse (vide = première voix française)
    "voice_rate": 1.05,
    "voice_silence_ms": 1300,      # silence qui marque la fin d'une phrase
    "voice_followup_s": 12,        # durée d'écoute après une réponse, sans redire le mot-clé
    "apps": None,                  # applications que l'assistant a le droit de lancer (None = liste d'origine)
    "providers": {
        "anthropic": {"enabled": True, "base_url": "https://api.anthropic.com"},
        "openai": {"enabled": True, "base_url": "https://api.openai.com/v1"},
        "gemini": {"enabled": True, "base_url": "https://generativelanguage.googleapis.com/v1beta"},
        "ollama": {"enabled": True, "base_url": "http://127.0.0.1:11434"},
    },
}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Settings:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self.data = copy.deepcopy(DEFAULTS)
        if path.exists():
            try:
                self.data = _merge(DEFAULTS, json.loads(path.read_text(encoding="utf-8")))
            except (ValueError, OSError):
                path.rename(path.with_suffix(".corrupt.json"))
        self.save()

    def get(self, key, default=None):
        with self._lock:
            return copy.deepcopy(self.data.get(key, default))

    def all(self) -> dict:
        with self._lock:
            return copy.deepcopy(self.data)

    def update(self, patch: dict) -> dict:
        allowed = set(DEFAULTS)
        with self._lock:
            clean = {k: v for k, v in patch.items() if k in allowed}
            self.data = _merge(self.data, clean)
            self.save()
            return copy.deepcopy(self.data)

    def save(self):
        with self._lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)
