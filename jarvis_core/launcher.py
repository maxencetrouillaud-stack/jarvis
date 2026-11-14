"""Lancement d'applications : uniquement celles de la liste autorisée dans les réglages, sans argument."""
import os
import re
import subprocess
import sys
import webbrowser

from .memory import normalize

DEFAULT_APPS = [
    {"name": "Bloc-notes", "target": "notepad.exe"},
    {"name": "Calculatrice", "target": "calc.exe"},
    {"name": "Explorateur de fichiers", "target": "explorer.exe"},
    {"name": "Excel", "target": "excel.exe"},
    {"name": "Word", "target": "winword.exe"},
    {"name": "Paramètres", "target": "ms-settings:"},
] if sys.platform == "win32" else []


def clean_apps(apps):
    out, seen = [], set()
    for a in apps if isinstance(apps, list) else []:
        if not isinstance(a, dict):
            continue
        name = re.sub(r"\s+", " ", str(a.get("name") or "")).strip()[:40]
        target = str(a.get("target") or "").strip().strip('"')[:400]
        if name and target and "\n" not in target and normalize(name) not in seen:
            seen.add(normalize(name))
            out.append({"name": name, "target": target})
    return out[:60]


def find(apps, query):
    """Application autorisée dont le nom figure dans la demande (« lance la calculatrice »)."""
    q = " " + " ".join(re.findall(r"[a-z0-9]+", normalize(query))) + " "
    best = None
    for a in apps:
        n = " ".join(re.findall(r"[a-z0-9]+", normalize(a["name"])))
        if n and f" {n} " in q and (best is None or len(n) > len(best[0])):
            best = (n, a)
    return best[1] if best else None


def launch(target, runner=None):
    if runner:
        return runner(target)
    if re.match(r"^https?://", target):
        webbrowser.open(target)
    elif sys.platform == "win32":
        os.startfile(target)  # noqa: S606 — cible issue de la liste autorisée par l'utilisateur
    else:
        subprocess.Popen([target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
