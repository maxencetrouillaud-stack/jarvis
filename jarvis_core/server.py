"""Serveur local de Jarvis : sert l'interface et l'API sur 127.0.0.1 uniquement."""
import argparse
import json
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__, kb_api, launcher, memory as mem
from .backup import Backups
from .brain import ROUTES, Brain
from .collect import KnowledgeService
from .config import DEFAULT_PERSONALITY, Settings, data_dir
from .hub import MODULES, Hub
from .orchestrator import STRATEGIES
from .knowledge import Duplicate, Knowledge
from .keystore import SCHEME, SecretStore
from .providers import REGISTRY, ProviderError
from .research import Researcher
from .updater import UpdateError, Updater
from .sphere import Sphere

WEB = Path(__file__).resolve().parent.parent / "web"
MAX_BODY = 45 * 1024 * 1024


class App:
    def __init__(self, home: Path = None, kb_options=None):
        self.home = home or data_dir()
        self.settings = Settings(self.home / "settings.json")
        self.keys = SecretStore(self.home / "keys.json")
        self.store = mem.Store(self.home / "jarvis.db")
        self.kb = Knowledge(self.home / "knowledge.db")
        self.brain = Brain(self.settings, self.keys, self.store, self.kb)
        self.kbs = KnowledgeService(self.kb, self.brain, self.settings, self.home, **{
            k: v for k, v in (kb_options or {}).items() if k in ("wikipedia", "allow_private")})
        self.backups = Backups(self.home, self.store, self.settings, self.keys, self.kb)
        self.hub = Hub(self.store, self.kb, self.brain, self.settings, self.home)
        ko = kb_options or {}
        self.research = Researcher(self.kb, self.brain, self.settings, self.keys, self.hub, self.kbs,
                                   wikipedia=ko.get("wikipedia"), allow_private=ko.get("allow_private", False),
                                   endpoints=ko.get("endpoints"), pace_seconds=ko.get("pace_seconds", 3.1))
        self.hub.research = self.research
        self.brain.hub = self.hub
        self.kbs.hub = self.hub
        self.sphere = Sphere(self.store, self.kb, self.research)
        self.updater = Updater(self.settings, (kb_options or {}).get("app_dir"),
                               **({"raw": kb_options["update_raw"]} if kb_options and kb_options.get("update_raw") else {}))
        self.httpd = None
        self.restart_requested = False
        self.last_ui = 0.0   # dernier signe de vie de la fenêtre (mode application)
        self.app_runner = None  # remplacé dans les tests
        if self.settings.get("apps") is None:
            self.settings.update({"apps": launcher.DEFAULT_APPS})
        self.token = secrets.token_urlsafe(32)
        self._migrate_imported_documents()

    def _migrate_imported_documents(self):
        """v0.1 rangeait les documents importés dans la mémoire personnelle : on les déplace dans la base
        de connaissances, sans rien perdre."""
        with self.store.lock:
            rows = self.store.db.execute(
                "SELECT id, title, content FROM memories WHERE kind='connaissance' ORDER BY id").fetchall()
        groups = {}
        for r in rows:
            body = re.sub(r"^\[[^\]]*\]\n", "", r["content"], count=1)
            groups.setdefault(r["title"] or "Document importé", []).append((r["id"], body))
        for title, parts in groups.items():
            try:
                self.kb.ingest(title, [("", "\n\n".join(b for _, b in parts))], "texte", "import (version 0.1)")
            except Duplicate:
                pass
            for mid, _ in parts:
                self.store.delete_memory(mid)

    def close(self):
        self.store.close()
        self.kb.close()

    def state(self):
        return {
            "version": __version__,
            "settings": self.settings.all(),
            "providers": self.brain.providers_state(),
            "routes": ROUTES,
            "categories": mem.CATEGORIES,
            "kinds": mem.KINDS,
            "natures": mem.NATURES,
            "strategies": STRATEGIES,
            "stats": self.store.stats(),
            "kb": self.kb.stats(self.settings.get("kb_stale_days", 180)),
            "activity": self.brain.activity,
            "key_storage": SCHEME,
            "data_dir": str(self.home),
            "default_personality": DEFAULT_PERSONALITY,
            "embedding_active": self.brain.embedding_ref(),
            "modules": [{"key": k, "label": l, "color": c} for k, l, c in MODULES],
            "update": self.updater.info(),
            "first_run": not self.settings.get("user_name") and self.store.stats()["messages"] == 0,
        }


class HttpError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "Jarvis"

        def log_message(self, *a):
            pass

        # ----- utilitaires -----
        def _send(self, code, body: bytes, ctype="application/json; charset=utf-8"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                raise HttpError(413, "Requête trop volumineuse")
            if not n:
                return {}
            try:
                j = json.loads(self.rfile.read(n).decode("utf-8"))
            except ValueError:
                raise HttpError(400, "JSON invalide")
            if not isinstance(j, dict):
                raise HttpError(400, "Objet JSON attendu")
            return j

        def _guard(self, api: bool):
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
            if host not in ("127.0.0.1", "localhost", "::1"):
                raise HttpError(403, "Hôte refusé")
            origin = self.headers.get("Origin")
            if origin and urlparse(origin).hostname not in ("127.0.0.1", "localhost", "::1"):
                raise HttpError(403, "Origine refusée")
            if api and not secrets.compare_digest(self.headers.get("X-Jarvis-Token") or "", app.token):
                raise HttpError(401, "Jeton de session invalide — rechargez la fenêtre de Jarvis")

        def _dispatch(self, method):
            url = urlparse(self.path)
            path = url.path
            try:
                if path == "/api/ping":
                    self._guard(False)
                    return self._json({"app": "jarvis", "version": __version__})
                if path.startswith("/api/"):
                    self._guard(True)
                    app.last_ui = time.time()
                    return self._api(method, path, parse_qs(url.query))
                self._guard(False)
                if method != "GET":
                    raise HttpError(405, "Méthode refusée")
                return self._static(path)
            except HttpError as e:
                self._json({"error": str(e)}, e.code)
            except (ProviderError, ValueError) as e:
                self._json({"error": str(e)}, 400)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as e:
                try:
                    self._json({"error": f"Erreur interne : {type(e).__name__}: {e}"}, 500)
                except OSError:
                    pass

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def do_PATCH(self):
            self._dispatch("PATCH")

        def do_DELETE(self):
            self._dispatch("DELETE")

        def _static(self, path):
            if path in ("/", "/index.html"):
                html = (WEB / "index.html").read_text(encoding="utf-8").replace("__JARVIS_TOKEN__", app.token)
                self.send_response(200)
                body = html.encode("utf-8")
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Security-Policy",
                                 "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                                 "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
                self.end_headers()
                self.wfile.write(body)
                return
            target = (WEB / path.lstrip("/")).resolve()
            if WEB.resolve() not in target.parents or not target.is_file():
                raise HttpError(404, "Introuvable")
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if target.suffix == ".js":
                ctype = "text/javascript; charset=utf-8"
            self._send(200, target.read_bytes(), ctype)

        # ----- API -----
        def _api(self, method, path, q):
            b, s = app.brain, app.store
            m_exp = re.match(r"^/api/hub/exports/([\w.\-]+\.zip)$", path)
            if m_exp and method == "GET":
                target = (app.home / "exports" / m_exp.group(1)).resolve()
                if (app.home / "exports").resolve() not in target.parents or not target.is_file():
                    raise HttpError(404, "Export introuvable")
                return self._send(200, target.read_bytes(), "application/zip")
            if path.startswith(("/api/kb/", "/api/hub/", "/api/research")) or path == "/api/global-search":
                try:
                    res = kb_api.handle(app, method, path, q, self._body)
                except kb_api.Bad as e:
                    raise HttpError(e.code, str(e)) from None
                if res is None:
                    raise HttpError(404, "Route inconnue")
                return self._json(res)
            m = re.match(r"^/api/(conversations|memories)/([\w-]+)$", path)

            if path == "/api/state" and method == "GET":
                return self._json(app.state())

            if path == "/api/settings" and method == "POST":
                body = self._body()
                if "apps" in body:
                    body["apps"] = launcher.clean_apps(body["apps"])
                if "library_folder" in body:
                    body["library_folder"] = str(body["library_folder"] or "").strip().strip('"')[:500]
                if "research_on_gap" in body and body["research_on_gap"] not in ("proposer", "jamais"):
                    body["research_on_gap"] = "proposer"
                for key, allowed in (("orchestrator_decompose", ("auto", "jamais")),
                                     ("orchestrator_verify", ("auto", "toujours", "jamais")),
                                     ("memory_update_mode", ("valider", "auto"))):
                    if key in body and body[key] not in allowed:
                        body[key] = allowed[0]
                for key, lo, hi in (("orchestrator_max_steps", 2, 8), ("orchestrator_max_calls", 4, 40),
                                    ("orchestrator_max_seconds", 30, 1800)):
                    if key in body:
                        try:
                            body[key] = max(lo, min(hi, int(body[key])))
                        except (TypeError, ValueError):
                            body.pop(key)
                if "voice_wake_word" in body:
                    body["voice_wake_word"] = " ".join(str(body["voice_wake_word"]).lower().split())[:30] or "jarvis"
                app.settings.update(body)
                return self._json(app.state())

            if path == "/api/heartbeat" and method == "POST":
                return self._json({"ok": True})
            if path == "/api/update" and method == "GET":
                return self._json(app.updater.info())
            if path in ("/api/update/check", "/api/update/install", "/api/update/rollback") and method == "POST":
                try:
                    if path.endswith("check"):
                        return self._json(app.updater.check())
                    res = app.updater.install() if path.endswith("install") else app.updater.rollback()
                    return self._json({**res, **app.updater.info()})
                except UpdateError as e:
                    raise HttpError(400, str(e)) from None
            if path == "/api/restart" and method == "POST":
                if not app.httpd:
                    raise HttpError(400, "Redémarrage impossible depuis ce mode")
                app.restart_requested = True
                threading.Timer(0.4, app.httpd.shutdown).start()
                return self._json({"ok": True})

            if path == "/api/sphere" and method == "GET":
                return self._json(app.sphere.build())
            if path == "/api/sphere/version" and method == "GET":
                return self._json({"version": app.sphere.version()})
            if path == "/api/sphere/node" and method == "GET":
                d = app.sphere.detail((q.get("id") or [""])[0])
                if not d:
                    raise HttpError(404, "Cet élément n'existe plus")
                return self._json(d)

            if path == "/api/apps/launch" and method == "POST":
                query = str(self._body().get("query") or "")
                found = launcher.find(app.settings.get("apps") or [], query)
                if not found:
                    return self._json({"launched": False})
                try:
                    launcher.launch(found["target"], app.app_runner)
                except Exception as e:
                    s.log_action("application", f"{found['name']} : échec ({e})", False)
                    return self._json({"launched": False, "name": found["name"],
                                       "error": f"Impossible de lancer {found['name']} : {e}"})
                s.log_action("application", f"{found['name']} lancé(e) ({found['target']})")
                return self._json({"launched": True, "name": found["name"]})
            if path == "/api/actions" and method == "GET":
                return self._json(s.actions())

            if path == "/api/keys" and method == "POST":
                body = self._body()
                pid = body.get("provider")
                if pid not in REGISTRY:
                    raise HttpError(400, "Fournisseur inconnu")
                app.keys.set(pid, str(body.get("key") or ""))
                b.refresh_models(pid)
                return self._json(app.state())

            if path == "/api/models/refresh" and method == "POST":
                b.refresh_models(self._body().get("provider"))
                return self._json(app.state())

            if path == "/api/conversations" and method == "GET":
                return self._json(s.list_conversations())
            if path == "/api/conversations" and method == "POST":
                return self._json(s.create_conversation())
            if m and m.group(1) == "conversations":
                cid = m.group(2)
                if method == "GET":
                    conv = s.get_conversation(cid)
                    if not conv:
                        raise HttpError(404, "Conversation introuvable")
                    return self._json(conv)
                if method == "PATCH":
                    title = str(self._body().get("title") or "").strip()
                    if not title:
                        raise HttpError(400, "Titre vide")
                    s.rename_conversation(cid, title)
                    return self._json({"ok": True})
                if method == "DELETE":
                    s.delete_conversation(cid)
                    return self._json({"ok": True})

            if path == "/api/chat" and method == "POST":
                return self._chat(self._body())

            if path == "/api/search" and method == "GET":
                return self._json(s.search_messages((q.get("q") or [""])[0], None, 30))

            if path == "/api/brain" and method == "GET":
                return self._json({"stats": s.run_stats(), "runs": s.runs(int((q.get("limit") or ["25"])[0])),
                                   "habits": s.observed_habits(), "strategies": STRATEGIES})
            if path == "/api/memories/deleted" and method == "GET":
                return self._json(s.deleted_memories())
            if path == "/api/memories/projects" and method == "GET":
                return self._json(s.projects())
            if path == "/api/memories/proposals" and method == "GET":
                return self._json(s.proposals((q.get("status") or ["en attente"])[0]))
            m_prop = re.match(r"^/api/memories/proposals/(\d+)$", path)
            if m_prop and method == "POST":
                action = str(self._body().get("action") or "")
                if action not in ("accepter", "refuser"):
                    raise HttpError(400, "Action attendue : accepter ou refuser")
                p = s.get_proposal(int(m_prop.group(1)))
                res = s.decide_proposal(int(m_prop.group(1)), action == "accepter",
                                        b.embed(p["content"]) if p and action == "accepter" else None)
                if res:
                    app.hub.forget(f"m:{res['id']}")
                    app.hub.integrate(f"m:{res['id']}")
                s.log_action("mémoire", f"proposition {m_prop.group(1)} {'acceptée' if action == 'accepter' else 'refusée'}")
                return self._json({"ok": True, "memory": res})
            m_ver = re.match(r"^/api/memories/(\d+)/(versions|restore)$", path)
            if m_ver:
                mid = int(m_ver.group(1))
                if m_ver.group(2) == "versions" and method == "GET":
                    return self._json({"memory": s.get_memory(mid), "versions": s.versions(mid)})
                if m_ver.group(2) == "restore" and method == "POST":
                    vid = int(self._body().get("version_id") or 0)
                    v = next((x for x in s.versions(mid) if x["id"] == vid), None)
                    res = s.restore_version(mid, vid, b.embed(v["content"]) if v else None)
                    app.hub.forget(f"m:{mid}")
                    app.hub.integrate(f"m:{mid}")
                    s.log_action("mémoire", f"souvenir {mid} restauré (version {vid})")
                    return self._json(res)

            if path == "/api/memories" and method == "GET":
                query = (q.get("q") or [""])[0].strip()
                cat = (q.get("category") or [""])[0] or None
                nature = (q.get("nature") or [""])[0] or None
                project = (q.get("project") or [""])[0] or None
                if query:
                    res = s.search_memories(query, 50, b.embed(query))
                    for key, val in (("category", cat), ("nature", nature), ("project", project)):
                        if val:
                            res = [r for r in res if r.get(key) == val]
                    return self._json(res)
                return self._json(s.list_memories(cat, nature=nature, project=project))
            if path == "/api/memories" and method == "POST":
                body = self._body()
                content = str(body.get("content") or "").strip()
                if not content:
                    raise HttpError(400, "Le souvenir est vide")
                res = s.add_memory(content, body.get("category", "divers"), body.get("importance", 3),
                                   body.get("kind", "durable"), "manuel", body.get("days"), ext=b.embed(content),
                                   nature=body.get("nature"), project=body.get("project"))
                app.hub.integrate(f"m:{res['id']}")
                return self._json(s.get_memory(res["id"]))
            if m and m.group(1) == "memories":
                mid = int(m.group(2)) if m.group(2).isdigit() else -1
                if method == "PATCH":
                    body = self._body()
                    ext = b.embed(body["content"]) if body.get("content") else None
                    res = s.update_memory(mid, body, ext)
                    if not res:
                        raise HttpError(404, "Souvenir introuvable")
                    if body.get("content"):
                        app.hub.forget(f"m:{mid}")
                        app.hub.integrate(f"m:{mid}")
                    return self._json(res)
                if method == "DELETE":
                    s.delete_memory(mid)
                    app.hub.forget(f"m:{mid}")
                    return self._json({"ok": True})

            if path == "/api/memories/reindex" and method == "POST":
                return self._json(b.reindex())

            if path == "/api/knowledge/import" and method == "POST":
                body = self._body()
                text = str(body.get("text") or "").strip()
                title = str(body.get("title") or "Document").strip()[:120]
                if not text:
                    raise HttpError(400, "Document vide")
                if len(text) > 2_000_000:
                    raise HttpError(413, "Document trop long (2 Mo de texte au maximum)")
                try:
                    return self._json(b.import_knowledge(title, text))
                except Duplicate as e:
                    raise HttpError(409, str(e)) from None

            if path == "/api/backups" and method == "GET":
                return self._json(app.backups.list())
            if path == "/api/backups" and method == "POST":
                return self._json({"name": app.backups.create("manuel"), "list": app.backups.list()})
            if path == "/api/backups/restore" and method == "POST":
                res = app.backups.restore(str(self._body().get("name") or ""))
                b.refresh_models()
                return self._json(res)

            raise HttpError(404, "Route inconnue")

        def _chat(self, body):
            cid = str(body.get("conversation_id") or "")
            text = str(body.get("message") or "")
            if not text.strip():
                raise HttpError(400, "Message vide")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            gen = app.brain.chat(cid, text, body.get("model") or "auto", body.get("deep"))
            try:
                for ev in gen:
                    self.wfile.write(f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode("utf-8"))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass  # fenêtre fermée ou réponse interrompue : le texte déjà reçu est enregistré
            except Exception as e:
                try:
                    msg = {"type": "error", "message": f"Erreur interne : {type(e).__name__}: {e}"}
                    self.wfile.write(f"data: {json.dumps(msg, ensure_ascii=False)}\n\n".encode("utf-8"))
                except OSError:
                    pass
            finally:
                gen.close()

    return Handler


def serve(app: App, port: int):
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    httpd.daemon_threads = True
    return httpd


def _running_jarvis(port):
    """Version du Jarvis qui écoute déjà sur ce port, ou None."""
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/api/ping", timeout=1.5) as r:
            info = json.loads(r.read())
            return str(info.get("version") or "?") if info.get("app") == "jarvis" else None
    except Exception:
        return None


def open_window(url):
    """Ouvre Jarvis dans sa propre fenêtre (mode application d'Edge ou Chrome), sinon dans le navigateur."""
    cands = []
    if sys.platform == "win32":
        for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"),
                     os.environ.get("LOCALAPPDATA")):
            if base:
                cands += [Path(base) / "Microsoft/Edge/Application/msedge.exe",
                          Path(base) / "Google/Chrome/Application/chrome.exe"]
    else:
        cands = [Path(p) for n in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge")
                 if (p := shutil.which(n))]
    for exe in cands:
        if exe.exists():
            try:
                subprocess.Popen([str(exe), f"--app={url}", "--window-size=1320,860"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return "fenêtre dédiée"
            except OSError:
                continue
    webbrowser.open(url)
    return "navigateur par défaut"


def notify(text, title="Jarvis", gui=False):
    """Message à l'utilisateur : console, et boîte de dialogue Windows quand il n'y a pas de console."""
    print(text)
    if gui and sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, title, 0x40)
        except Exception:
            pass


def _relaunch(port, gui):
    """Démarre une nouvelle instance de Jarvis (après une mise à jour) une fois celle-ci arrêtée."""
    script = Path(__file__).resolve().parent.parent / "run.py"
    exe = sys.executable
    if sys.platform == "win32" and "WindowsApps" in exe:   # Python du Microsoft Store : passer par son alias
        alias = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WindowsApps"
        for name in (f"pythonw{sys.version_info.major}.{sys.version_info.minor}.exe", "pythonw.exe"):
            if (alias / name).exists():
                exe = str(alias / name)
                break
    args = [exe, str(script), "--port", str(port), "--no-window", "--wait"] + (["--app"] if gui else [])
    kw = {"cwd": str(script.parent), "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if sys.platform == "win32":
        kw["creationflags"] = 0x00000008 | 0x00000200   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    subprocess.Popen(args, **kw)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="jarvis", description="Jarvis — assistant personnel local")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-window", action="store_true", help="ne pas ouvrir la fenêtre")
    ap.add_argument("--app", action="store_true",
                    help="mode application : sans console, s'arrête quand la fenêtre de Jarvis est fermée")
    ap.add_argument("--wait", action="store_true", help="attendre que l'instance précédente soit arrêtée (redémarrage)")
    args = ap.parse_args(argv)
    gui = args.app or sys.stdout is None

    if gui:  # pas de console : le journal va dans un fichier
        log = data_dir() / "jarvis.log"
        try:
            if log.exists() and log.stat().st_size > 2_000_000:
                log.replace(log.with_suffix(".old.log"))
            sys.stdout = sys.stderr = open(log, "a", encoding="utf-8", buffering=1)
            print(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} démarrage ---")
        except OSError:
            pass

    port = args.port
    if args.wait:
        for _ in range(60):
            if _running_jarvis(port) is None:
                break
            time.sleep(0.5)
    app = None
    for _ in range(20):
        running = _running_jarvis(port)
        if running and running != __version__:
            notify(f"Une autre version de Jarvis ({running}) est encore ouverte sur ce PC.\n\n"
                   f"Cette version-ci est la {__version__} : elle ne peut pas démarrer tant que l'ancienne tourne.\n"
                   "Fermez l'ancienne (sa fenêtre noire, ou « Python » dans le Gestionnaire des tâches), puis relancez Jarvis.",
                   gui=gui)
            return 1
        if running:
            print(f"Jarvis tourne déjà : http://127.0.0.1:{port}")
            if not args.no_window:
                open_window(f"http://127.0.0.1:{port}/")
            return 0
        try:
            app = app or App()
            httpd = serve(app, port)
            break
        except OSError:
            port += 1
    else:
        notify("Impossible de trouver un port libre pour démarrer Jarvis.", gui=gui)
        return 1

    app.httpd = httpd
    url = f"http://127.0.0.1:{port}/"
    print(f"Jarvis {__version__} — {url}")
    print(f"Données : {app.home}")
    if not gui:
        print("Laissez cette fenêtre ouverte. Ctrl+C pour arrêter.")

    def warmup():
        try:
            app.store.purge_expired()
            app.backups.auto()
        except Exception as e:
            print(f"Sauvegarde automatique impossible : {e}")
        app.brain.refresh_models()

    def watchdog():
        """Mode application : Jarvis s'arrête quand sa fenêtre est fermée depuis 2 minutes
        (la fenêtre envoie un signe de vie toutes les 20 secondes), sauf si un travail est en cours."""
        started = time.time()
        idle = float(os.environ.get("JARVIS_IDLE_SECONDS", 120))
        while True:
            time.sleep(min(15.0, idle / 4))
            quiet = time.time() - (app.last_ui or started)
            busy = not app.kbs.idle.is_set() or not app.research.idle.is_set()
            if quiet > (idle if app.last_ui else idle * 2.5) and not busy:
                print("Fenêtre fermée : arrêt de Jarvis.")
                httpd.shutdown()
                return

    threading.Thread(target=warmup, daemon=True).start()
    threading.Thread(target=app.updater.auto_loop, daemon=True).start()
    if args.app:
        threading.Thread(target=watchdog, daemon=True).start()
    if not args.no_window:
        open_window(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt de Jarvis.")
    finally:
        httpd.server_close()
        app.close()
    if app.restart_requested:
        print("Redémarrage de Jarvis.")
        _relaunch(port, args.app)
    return 0


if __name__ == "__main__":
    sys.exit(main())
