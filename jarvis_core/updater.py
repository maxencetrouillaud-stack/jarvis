"""Mises à jour de Jarvis, sans archive à télécharger.

Chaque version publiée contient un fichier manifest.json : numéro de version, notes, et pour chaque fichier
de l'application son empreinte SHA-256. Pour se mettre à jour, Jarvis :
  1. lit le manifeste publié (dépôt GitHub public) et le compare à celui qui est installé ;
  2. télécharge seulement les fichiers qui ont changé, dans un dossier d'attente ;
  3. vérifie l'empreinte de chacun (un fichier altéré ou incomplet arrête tout, rien n'est modifié) ;
  4. copie la version installée dans « previous » (pour pouvoir revenir en arrière) ;
  5. remplace les fichiers, retire ceux que la nouvelle version n'utilise plus, puis redémarre.
Les données (mémoire, connaissances, clés, réglages) sont dans un autre dossier : une mise à jour n'y touche jamais.
"""
import hashlib
import json
import os
import re
import shutil
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import __version__

DEFAULT_REPO = "maxencetrouillaud-stack/jarvis"
RAW = "https://raw.githubusercontent.com/{repo}/{ref}/{path}"
MANIFEST = "manifest.json"
# seuls ces emplacements peuvent être écrits par une mise à jour
ALLOWED = re.compile(r"^(?:run\.py|README\.md|install\.py|install\.ps1|[^/]+\.bat|jarvis\.ico|package\.json|"
                     r"(?:jarvis_core|web|ui|tests|tools)/[A-Za-z0-9_./ \-]+)$")
SKIP_DIRS = {"__pycache__", "node_modules", ".git", "previous", "update-staging"}


class UpdateError(Exception):
    pass


def vtuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v))[:4]) or (0,)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 16), b""):
            h.update(block)
    return h.hexdigest()


def safe_path(p):
    p = str(p).replace("\\", "/")
    if p.startswith("/") or ".." in p.split("/") or ":" in p or not ALLOWED.match(p):
        raise UpdateError(f"Chemin refusé dans le manifeste : {p}")
    return p


def build_manifest(root, version, notes=""):
    """Manifeste d'un dossier d'application (utilisé pour publier une version)."""
    root = Path(root)
    files = {}
    for f in sorted(root.rglob("*")):
        rel = f.relative_to(root).as_posix()
        if f.is_dir() or any(part in SKIP_DIRS for part in rel.split("/")) or rel == MANIFEST:
            continue
        if f.suffix in (".pyc", ".zip") or not ALLOWED.match(rel):
            continue
        files[rel] = {"sha256": sha256(f), "size": f.stat().st_size}
    return {"app": "jarvis", "version": version, "notes": notes, "published": time.strftime("%Y-%m-%d"),
            "files": files}


class Updater:
    def __init__(self, settings, app_dir=None, raw=RAW, opener=None):
        self.settings = settings
        self.app_dir = Path(app_dir or Path(__file__).resolve().parent.parent)
        self.raw = raw
        self.opener = opener or urllib.request.build_opener()
        self.lock = threading.Lock()
        self.state = {"current": __version__, "available": None, "notes": "", "checked": None, "error": "",
                      "busy": "", "progress": "", "installed": None, "changes": 0, "bytes": 0}
        self._remote = None

    # ---------- état ----------
    def repo(self):
        r = str(self.settings.get("update_repo") or DEFAULT_REPO).strip()
        if not re.fullmatch(r"[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+", r):
            raise UpdateError("Dépôt de mise à jour invalide (format attendu : propriétaire/dépôt)")
        return r

    def local_manifest(self):
        p = self.app_dir / MANIFEST
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except ValueError:
                pass
        return None

    def info(self):
        d = dict(self.state)
        d["repo"] = self.settings.get("update_repo") or DEFAULT_REPO
        d["can_rollback"] = (self.app_dir.parent / "previous" / MANIFEST).exists()
        prev = self.app_dir.parent / "previous" / MANIFEST
        d["previous"] = json.loads(prev.read_text(encoding="utf-8")).get("version") if d["can_rollback"] else None
        d["app_dir"] = str(self.app_dir)
        return d

    # ---------- réseau ----------
    def _get(self, path, ref="main", timeout=20):
        url = self.raw.format(repo=self.repo(), ref=ref, path=path)
        req = urllib.request.Request(url, headers={"User-Agent": f"Jarvis/{__version__}", "Cache-Control": "no-cache"})
        try:
            with self.opener.open(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise UpdateError("Aucune version publiée trouvée sur le dépôt de mise à jour") from None
            raise UpdateError(f"Serveur de mise à jour : erreur {e.code}") from None
        except (urllib.error.URLError, OSError) as e:
            raise UpdateError(f"Pas de connexion au serveur de mise à jour ({getattr(e, 'reason', e)})") from None

    def check(self):
        """Lit la dernière version publiée. -> info()"""
        with self.lock:
            try:
                m = json.loads(self._get(MANIFEST).decode("utf-8"))
                if m.get("app") != "jarvis" or not isinstance(m.get("files"), dict) or not m.get("version"):
                    raise UpdateError("Manifeste de mise à jour invalide")
                for p in m["files"]:
                    safe_path(p)
                self._remote = m
                newer = vtuple(m["version"]) > vtuple(__version__)
                changes, size = self._diff(m) if newer else ([], 0)
                self.state.update(available=m["version"] if newer else None, notes=m.get("notes", ""),
                                  checked=time.time(), error="", changes=len(changes), bytes=size,
                                  published=m.get("published", ""))
            except (UpdateError, ValueError) as e:
                self.state.update(checked=time.time(), error=str(e))
        return self.info()

    def _diff(self, m):
        out, size = [], 0
        for p, meta in m["files"].items():
            f = self.app_dir / p
            if not f.exists() or f.stat().st_size != meta["size"] or sha256(f) != meta["sha256"]:
                out.append(p)
                size += meta["size"]
        return out, size

    # ---------- installation ----------
    def install(self):
        """Télécharge et applique la dernière version. Rien n'est modifié si un fichier est incorrect."""
        with self.lock:
            m = self._remote
            if not m or vtuple(m["version"]) <= vtuple(__version__):
                raise UpdateError("Aucune mise à jour à installer")
            ref = m.get("ref") or f"v{m['version']}"
            changes, _ = self._diff(m)
            stage = self.app_dir.parent / "update-staging"
            shutil.rmtree(stage, ignore_errors=True)
            stage.mkdir(parents=True)
            try:
                for i, p in enumerate(changes):
                    self.state["busy"] = "téléchargement"
                    self.state["progress"] = f"{i + 1}/{len(changes)} : {p}"
                    data = self._get(p, ref, 60)
                    if hashlib.sha256(data).hexdigest() != m["files"][p]["sha256"]:
                        raise UpdateError(f"Fichier altéré ou incomplet : {p} (mise à jour annulée, rien n'a changé)")
                    dest = stage / p
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(data)
                self.state.update(busy="installation", progress="sauvegarde de la version actuelle")
                prev = self.app_dir.parent / "previous"
                shutil.rmtree(prev, ignore_errors=True)
                shutil.copytree(self.app_dir, prev, ignore=shutil.ignore_patterns(*SKIP_DIRS, "*.pyc"))
                if not (prev / MANIFEST).exists():  # version installée à la main : on note ce qu'elle était
                    (prev / MANIFEST).write_text(json.dumps(build_manifest(prev, __version__)), encoding="utf-8")
                self.state["progress"] = "remplacement des fichiers"
                for p in changes:
                    dest = self.app_dir / p
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(stage / p, dest)
                old = self.local_manifest() or {"files": {}}
                for p in set(old.get("files", {})) - set(m["files"]):  # fichiers que la nouvelle version n'a plus
                    try:
                        (self.app_dir / safe_path(p)).unlink(missing_ok=True)
                    except UpdateError:
                        pass
                (self.app_dir / MANIFEST).write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
                self.state.update(installed=m["version"], busy="", progress="", error="")
                return {"installed": m["version"], "files": len(changes)}
            except Exception as e:
                self.state.update(busy="", progress="", error=str(e))
                raise
            finally:
                shutil.rmtree(stage, ignore_errors=True)

    def rollback(self):
        """Remet la version précédente (celle d'avant la dernière mise à jour)."""
        with self.lock:
            prev = self.app_dir.parent / "previous"
            if not (prev / MANIFEST).exists():
                raise UpdateError("Aucune version précédente conservée")
            pm = json.loads((prev / MANIFEST).read_text(encoding="utf-8"))
            cur = self.local_manifest() or {"files": {}}
            for p in set(cur.get("files", {})) - set(pm["files"]):
                (self.app_dir / safe_path(p)).unlink(missing_ok=True)
            for p in pm["files"]:
                src = prev / safe_path(p)
                if src.exists():
                    dest = self.app_dir / p
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dest)
            shutil.copy2(prev / MANIFEST, self.app_dir / MANIFEST)
            shutil.rmtree(prev, ignore_errors=True)
            self.state.update(installed=pm["version"], available=None)
            return {"installed": pm["version"]}

    def auto_loop(self):
        """Vérifie au démarrage puis toutes les 6 heures (si l'option est active)."""
        time.sleep(8)
        while True:
            if self.settings.get("update_check", True):
                self.check()
            time.sleep(6 * 3600)
