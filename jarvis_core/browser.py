"""Navigateur intégré : onglets, historique, favoris, téléchargements, et deux moteurs d'affichage.

- Moteur « lecture » (toujours disponible, sans installation) : Jarvis télécharge la page et l'affiche nettoyée
  (texte, titres, liens, tableaux, images) ; aucun script de la page ne s'exécute. Ne fonctionne pas pour les
  sites qui construisent tout leur contenu en JavaScript ni pour les pages qui demandent une connexion.
- Moteur « Chromium » (si Playwright est installé) : un vrai navigateur (Edge ou Chrome déjà présents sur le PC,
  sinon Chromium) piloté par Jarvis, sans fenêtre ; ses images sont affichées en direct dans Jarvis et vos clics,
  défilements et frappes lui sont transmis.

L'agent web de Jarvis utilise les mêmes onglets (marqués « agent »), visibles en temps réel. Il respecte robots.txt,
ne remplit aucun formulaire, ne se connecte nulle part et ne télécharge rien sans votre accord.
"""
import html
import ipaddress
import os
import queue
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.robotparser
from collections import OrderedDict
from html.parser import HTMLParser
from pathlib import Path

from . import extract
from .collect import CollectError, fetch

UA_TOKEN = "Jarvis"
SEARCH_PAGES = {
    "duckduckgo": ("DuckDuckGo", "https://duckduckgo.com/?q={q}"),
    "qwant": ("Qwant", "https://www.qwant.com/?q={q}"),
    "google": ("Google", "https://www.google.com/search?q={q}"),
    "bing": ("Bing", "https://www.bing.com/search?q={q}"),
    "ecosia": ("Ecosia", "https://www.ecosia.org/search?q={q}"),
}
BLOCKED_EXT = {".exe", ".msi", ".msix", ".appx", ".bat", ".cmd", ".com", ".scr", ".ps1", ".psm1", ".vbs", ".vbe",
               ".js", ".jse", ".wsf", ".wsh", ".hta", ".cpl", ".jar", ".lnk", ".reg", ".dll", ".sys", ".pif",
               ".application", ".gadget", ".msc", ".inf", ".iso", ".img", ".vhd", ".vhdx"}
MAX_DOWNLOAD = 200 * 1024 * 1024
HOME = "jarvis:accueil"


class BrowserError(Exception):
    pass


def classify_input(text):
    """Barre d'adresse : ('url', adresse) ou ('search', requête)."""
    t = (text or "").strip()
    if not t or t == HOME:
        return "home", HOME
    if re.match(r"^https?://\S+$", t, re.I):
        return "url", t
    if " " not in t and re.match(r"^(localhost|[\w-]+(\.[\w-]+)+)(:\d+)?(/\S*)?$", t, re.I | re.U):
        host = t.split("/")[0].split(":")[0]
        try:
            ipaddress.ip_address(host)
            return "url", "http://" + t
        except ValueError:
            pass
        if host == "localhost" or re.search(r"\.[a-z]{2,24}$", host, re.I):
            return "url", "https://" + t
    return "search", t


def host_of(url):
    return (urllib.parse.urlparse(url).hostname or "").lower()


def blocked_name(name):
    return Path(name.lower()).suffix in BLOCKED_EXT


def safe_filename(name):
    name = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", "_", (name or "").strip()).strip(". ")
    return (name or "fichier")[:120]


# ----------------------------------------------------------------------------- mode lecture
class Reader(HTMLParser):
    """Transforme une page HTML en document sûr : liste blanche de balises, aucun attribut d'origine conservé
    sauf les liens (réécrits en adresses absolues) et les images (servies par le proxy signé de Jarvis)."""
    DROP = {"script", "style", "noscript", "template", "iframe", "object", "embed", "svg", "canvas", "form", "input",
            "button", "select", "textarea", "video", "audio", "map", "dialog", "math"}
    LAYOUT = {"nav", "aside", "footer"}
    ALLOWED = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "a", "strong", "b", "em", "i", "u", "code",
               "pre", "blockquote", "table", "thead", "tbody", "tfoot", "tr", "th", "td", "caption", "img", "figure",
               "figcaption", "br", "hr", "dl", "dt", "dd", "sup", "sub", "small", "mark", "div", "section", "span",
               "kbd", "abbr", "cite", "q", "time"}
    VOID = {"br", "hr", "img"}
    BLOCK = {"p", "div", "section", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote", "table",
             "ul", "ol", "dt", "dd", "figure", "figcaption", "br", "hr", "article", "main", "header"}

    def __init__(self, base, img_url):
        super().__init__(convert_charrefs=True)
        self.base, self.img_url = base, img_url
        self.title, self.description = "", ""
        self.all, self.main, self.text_all, self.text_main = [], [], [], []
        self.links, self.images = [], 0
        self.tables, self._table, self._row = [], None, None
        self._drop = 0
        self._layout = 0
        self._main_depth = 0
        self._main_seen = False
        self._in_title = False
        self._head = False
        self._stack = []
        self._cell = None

    def _emit(self, s, text=None):
        self.all.append(s)
        if self._main_depth:
            self.main.append(s)
        if text is not None:
            self.text_all.append(text)
            if self._main_depth:
                self.text_main.append(text)

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag == "base" and a.get("href"):
            self.base = urllib.parse.urljoin(self.base, a["href"])
        if tag == "head":
            self._head = True
        if tag == "title":
            self._in_title = True
        if tag == "meta" and a.get("name", "").lower() == "description":
            self.description = a.get("content", "")[:400]
        if self._drop or tag in self.DROP:
            if tag not in self.VOID and tag in self.DROP:
                self._drop += 1
            return
        if tag in self.LAYOUT or (tag == "header" and not self._main_depth):
            self._layout += 1
            return
        if self._layout or self._head:
            return
        if tag in ("main", "article") and (not self._main_seen or self._main_depth):
            self._main_depth += 1
            self._main_seen = True
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
        if tag in self.BLOCK:
            self._emit("", "\n")
        if tag in ("td", "th"):
            self._emit("", " | ")
        if tag not in self.ALLOWED:
            return
        if tag == "a":
            href = a.get("href", "").strip()
            url = urllib.parse.urljoin(self.base, href) if href else ""
            if url.startswith(("http://", "https://")):
                url = url.split("#")[0] if url.split("#")[0] != self.base.split("#")[0] else url
                self.links.append(url)
                e = html.escape(url, quote=True)
                self._emit(f'<a href="{e}" data-href="{e}">')
            else:
                self._emit("<a>")
            self._stack.append(tag)
            return
        if tag == "img":
            src = a.get("src") or a.get("data-src") or (a.get("srcset") or "").split(" ")[0]
            if not src or src.startswith("data:"):
                return
            url = urllib.parse.urljoin(self.base, src)
            if not url.startswith(("http://", "https://")):
                return
            for dim in ("width", "height"):
                if a.get(dim, "").isdigit() and int(a[dim]) <= 2:
                    return
            self.images += 1
            alt = html.escape(a.get("alt", "")[:200], quote=True)
            self._emit(f'<img src="{html.escape(self.img_url(url), quote=True)}" alt="{alt}" loading="lazy">')
            return
        attrs_out = ""
        if tag in ("td", "th"):
            for k in ("colspan", "rowspan"):
                if a.get(k, "").isdigit() and 0 < int(a[k]) < 50:
                    attrs_out += f' {k}="{int(a[k])}"'
        self._emit(f"<{tag}{attrs_out}>")
        if tag not in self.VOID:
            self._stack.append(tag)

    def handle_endtag(self, tag):
        if tag == "head":
            self._head = False
        if tag == "title":
            self._in_title = False
        if tag in self.DROP:
            self._drop = max(0, self._drop - 1)
            return
        if self._drop:
            return
        if tag in self.LAYOUT or (tag == "header" and self._layout):
            self._layout = max(0, self._layout - 1)
            return
        if self._layout or self._head:
            return
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            if any(self._row):
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            if len(self._table) >= 2:
                self.tables.append(self._table[:200])
            self._table = None
        if tag in self.BLOCK:
            self._emit("", "\n")
        if tag in self._stack:
            while self._stack:
                t = self._stack.pop()
                self._emit(f"</{t}>")
                if t == tag:
                    break
        if tag in ("main", "article") and self._main_depth:
            self._main_depth -= 1

    def handle_data(self, data):
        if self._in_title:
            self.title += data
            return
        if self._drop or self._layout or self._head:
            return
        if self._cell is not None:
            self._cell.append(data)
        self._emit(html.escape(data, quote=False), data)

    def result(self):
        while self._stack:
            self._emit(f"</{self._stack.pop()}>")
        main_text = extract.clean("".join(self.text_main))
        use_main = len(main_text) >= 400
        body = "".join(self.main if use_main else self.all)
        text = main_text if use_main else extract.clean("".join(self.text_all))
        body = re.sub(r"(<(p|div|span|section|li)>\s*</\2>)+", "", body)
        return {"title": re.sub(r"\s+", " ", self.title).strip()[:300], "description": self.description,
                "html": body[:1_500_000], "text": text[:200_000], "links": list(dict.fromkeys(self.links))[:400],
                "tables": self.tables[:20], "images": self.images}


def read_html(raw, base, img_url=lambda u: u):
    r = Reader(base, img_url)
    try:
        r.feed(raw)
        r.close()
    except Exception:
        pass
    return r.result()


# ----------------------------------------------------------------------------- robots.txt
class Robots:
    def __init__(self, allow_private=False, ttl=3600):
        self.allow_private, self.ttl = allow_private, ttl
        self._cache = {}
        self._lock = threading.Lock()

    def allowed(self, url):
        u = urllib.parse.urlparse(url)
        key = f"{u.scheme}://{u.netloc}"
        with self._lock:
            hit = self._cache.get(key)
        if not hit or time.time() - hit[0] > self.ttl:
            rp = urllib.robotparser.RobotFileParser()
            try:
                data, _, _ = fetch(key + "/robots.txt", self.allow_private, 8)
                rp.parse(extract.decode(data).splitlines())
            except CollectError as e:
                if "répondu 401" in str(e) or "répondu 403" in str(e):
                    rp.disallow_all = True       # robots.txt protégé : on s'abstient
                else:
                    rp.allow_all = True          # pas de robots.txt : rien n'est interdit
            hit = (time.time(), rp)
            with self._lock:
                self._cache[key] = hit
        return hit[1].can_fetch(UA_TOKEN, url)


# ----------------------------------------------------------------------------- moteur Chromium
class Chromium:
    """Navigateur réel piloté par Playwright dans un fil dédié (l'API synchrone n'est pas partageable entre fils)."""

    def __init__(self, home: Path, settings, on_event=None, on_download=None):
        self.home, self.settings = Path(home), settings
        self.on_event, self.on_download = on_event, on_download
        self.q = queue.Queue()
        self.pages = {}
        self.thread = None
        self.error = ""
        self.channel = ""
        self._ready = threading.Event()
        self._ctx = None
        self._pending_popups = []

    @staticmethod
    def installed():
        try:
            import playwright  # noqa: F401
            return True
        except ImportError:
            return False

    def available(self):
        if not self.installed():
            return False, "Playwright n'est pas installé (Réglages › Navigateur › Installer le moteur Chromium)"
        if self.error:
            return False, self.error
        return True, ""

    def _start(self):
        if self.thread and self.thread.is_alive():
            return
        self._ready.clear()
        self.error = ""
        self.thread = threading.Thread(target=self._loop, daemon=True, name="chromium")
        self.thread.start()
        self._ready.wait(60)
        if self.error:
            raise BrowserError(self.error)
        if not self._ready.is_set():
            raise BrowserError("Le navigateur Chromium ne démarre pas (délai dépassé)")

    def _launch(self, p):
        prof = self.home / "browser-profile"
        prof.mkdir(parents=True, exist_ok=True)
        pref = self.settings.get("browser_chromium_channel", "auto")
        order = [pref] if pref not in ("", "auto") else (["msedge", "chrome", "chromium"] if sys.platform == "win32"
                                                         else ["chromium", "chrome", "msedge"])
        last = None
        for ch in order:
            try:
                kw = {} if ch == "chromium" else {"channel": ch}
                ctx = p.chromium.launch_persistent_context(
                    str(prof / ch), headless=True, viewport={"width": 1280, "height": 800}, accept_downloads=True,
                    locale="fr-FR", **kw)
                self.channel = ch
                return ctx
            except Exception as e:
                last = e
        raise BrowserError(f"Aucun navigateur Chromium utilisable ({str(last).splitlines()[0][:200] if last else ''})")

    def _loop(self):
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                self._ctx = self._launch(p)
                self._ctx.on("page", lambda page: self._pending_popups.append(page))
                for pg in list(self._ctx.pages):   # page vide ouverte d'office par le profil persistant
                    pg.close()
                self._ready.set()
                while True:
                    try:
                        job = self.q.get(timeout=0.3)
                    except queue.Empty:
                        job = None
                    if job is None:
                        self._pump()
                        continue
                    if job == "stop":
                        break
                    fn, box, done = job
                    try:
                        box["res"] = fn()
                    except Exception as e:
                        box["err"] = e
                    done.set()
                    self._pump()
                self._ctx.close()
        except Exception as e:
            self.error = f"Chromium indisponible : {str(e).splitlines()[0][:240]}"
            self._ready.set()
        finally:
            self.pages.clear()
            self._ctx = None

    def _pump(self):
        """Laisse Playwright traiter ses événements (téléchargements, fenêtres surgissantes)."""
        try:
            if self.pages:
                next(iter(self.pages.values())).wait_for_timeout(1)
        except Exception:
            pass
        while self._pending_popups:
            page = self._pending_popups.pop(0)
            if page in self.pages.values():
                continue
            if self.on_event:
                self.on_event("popup", page)

    def call(self, fn, timeout=45):
        self._start()
        box, done = {}, threading.Event()
        self.q.put((fn, box, done))
        if not done.wait(timeout):
            raise BrowserError("Le navigateur ne répond pas (délai dépassé)")
        if "err" in box:
            e = box["err"]
            raise BrowserError(str(e).splitlines()[0][:300]) from None
        return box.get("res")

    def stop(self):
        if self.thread and self.thread.is_alive():
            self.q.put("stop")
            self.thread.join(10)

    # opérations (exécutées dans le fil Chromium)
    def new_page(self, tab_id, url=None):
        def go():
            page = self._ctx.new_page()
            self._wire(tab_id, page)
            if url:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
            return self._info(page)
        return self.call(go)

    def adopt(self, tab_id, page):
        """Fenêtre surgissante ouverte par une page : elle devient un onglet (appelé dans le fil Chromium)."""
        self._wire(tab_id, page)

    def _wire(self, tab_id, page):
        self.pages[tab_id] = page
        page.on("download", lambda d: self.on_download and self.on_download(tab_id, d))
        page.on("framenavigated", lambda fr: fr == page.main_frame and self.on_event and
                self.on_event("navigated", (tab_id, page)))

    def _info(self, page):
        try:
            title = page.title()
        except Exception:
            title = ""
        return {"url": page.url, "title": title}

    def page(self, tab_id):
        pg = self.pages.get(tab_id)
        if not pg or pg.is_closed():
            raise BrowserError("Cet onglet Chromium est fermé")
        return pg

    def goto(self, tab_id, url):
        def go():
            pg = self.page(tab_id)
            pg.goto(url, wait_until="domcontentloaded", timeout=30000)
            return self._info(pg)
        return self.call(go)

    def history_step(self, tab_id, step):
        def go():
            pg = self.page(tab_id)
            (pg.go_back if step < 0 else pg.go_forward)(wait_until="domcontentloaded", timeout=20000)
            return self._info(pg)
        return self.call(go)

    def reload(self, tab_id):
        return self.call(lambda: (self.page(tab_id).reload(wait_until="domcontentloaded", timeout=30000),
                                  self._info(self.page(tab_id)))[1])

    def info(self, tab_id):
        return self.call(lambda: self._info(self.page(tab_id)), 10)

    def frame(self, tab_id, quality=62):
        return self.call(lambda: self.page(tab_id).screenshot(type="jpeg", quality=quality), 20)

    def text(self, tab_id):
        def go():
            pg = self.page(tab_id)
            body = pg.inner_text("body", timeout=8000)
            return {**self._info(pg), "text": body[:200_000],
                    "links": pg.eval_on_selector_all("a[href]", "els => els.slice(0, 400).map(e => e.href)")}
        return self.call(go)

    def html(self, tab_id):
        return self.call(lambda: self.page(tab_id).content(), 20)

    def input(self, tab_id, action, **a):
        def go():
            pg = self.page(tab_id)
            if action == "click":
                pg.mouse.click(float(a["x"]), float(a["y"]), button=a.get("button", "left"),
                               click_count=2 if a.get("double") else 1)
            elif action == "move":
                pg.mouse.move(float(a["x"]), float(a["y"]))
            elif action == "scroll":
                pg.mouse.move(float(a.get("x", 640)), float(a.get("y", 400)))
                pg.mouse.wheel(float(a.get("dx", 0)), float(a.get("dy", 0)))
            elif action == "type":
                pg.keyboard.type(str(a.get("text", ""))[:2000])
            elif action == "key":
                key = str(a.get("key", ""))
                if not re.fullmatch(r"[\w+\-]{1,40}", key):
                    raise BrowserError("Touche inconnue")
                pg.keyboard.press(key)
            else:
                raise BrowserError("Action inconnue")
            pg.wait_for_timeout(120)
            return self._info(pg)
        return self.call(go, 30)

    def close(self, tab_id):
        def go():
            pg = self.pages.pop(tab_id, None)
            if pg and not pg.is_closed():
                pg.close()
        if self.thread and self.thread.is_alive():
            self.call(go, 10)


# ----------------------------------------------------------------------------- service
class Tab:
    def __init__(self, tid, engine, owner, mission_id=None):
        self.id, self.engine, self.owner, self.mission_id = tid, engine, owner, mission_id
        self.url, self.title, self.kind = HOME, "Accueil", "accueil"
        self.history, self.pos = [], -1
        self.doc = None
        self.loading, self.error = False, ""
        self.seq = 0
        self.created = self.updated = time.time()

    def view(self, full=False):
        v = {"id": self.id, "engine": self.engine, "owner": self.owner, "mission_id": self.mission_id, "url": self.url,
             "title": self.title, "kind": self.kind, "loading": self.loading, "error": self.error, "seq": self.seq,
             "can_back": self.pos > 0, "can_forward": 0 <= self.pos < len(self.history) - 1, "updated": self.updated}
        if full and self.doc:
            v["doc"] = {k: self.doc.get(k) for k in ("html", "description", "results", "query", "note", "file",
                                                     "images", "links_count", "tables_count")}
        return v


class Browser:
    def __init__(self, ws, settings, keys, research=None, allow_private=False, wikipedia=None, brave_endpoint=None,
                 install_cmds=None):
        self.ws, self.settings, self.keys, self.research = ws, settings, keys, research
        self.allow_private = allow_private
        self.wiki = wikipedia
        self.brave_endpoint = brave_endpoint or "https://api.search.brave.com/res/v1/web/search"
        self.tabs = OrderedDict()
        self._lock = threading.RLock()
        self._n = 0
        self.robots = Robots(allow_private)
        self.downloads_dir = ws.home / "telechargements"
        self.chromium = Chromium(ws.home, settings, self._chromium_event, self._chromium_download)
        self.install = {"state": "idle", "log": [], "started": None}
        self._install_cmds = install_cmds

    # ------------------------------------------------------------------ outils
    def _publish(self, tab, action, **extra):
        tab.seq += 1
        tab.updated = time.time()
        self.ws.bus.publish("browser", tab_id=tab.id, action=action, owner=tab.owner, url=tab.url, title=tab.title,
                            mission_id=tab.mission_id, engine=tab.engine, **extra)

    def proxy_url(self, url):
        return f"/media/p?u={urllib.parse.quote(url, safe='')}&s={self.ws.sign('p' + url)}"

    def engines(self):
        ok, why = self.chromium.available()
        return {"lecture": {"available": True, "label": "Lecture"},
                "chromium": {"available": ok, "label": "Chromium", "reason": why, "installed": self.chromium.installed(),
                             "channel": self.chromium.channel}}

    def _engine(self, wanted):
        wanted = wanted or self.settings.get("browser_engine", "auto")
        if wanted == "lecture":
            return "lecture"
        ok, _ = self.chromium.available()
        if wanted == "chromium" and not ok:
            raise BrowserError(self.chromium.available()[1])
        return "chromium" if ok and wanted in ("chromium", "auto") else "lecture"

    def get(self, tid) -> Tab:
        with self._lock:
            t = self.tabs.get(tid)
        if not t:
            raise KeyError("Onglet introuvable")
        return t

    def list(self):
        with self._lock:
            return [t.view() for t in self.tabs.values()]

    def state(self):
        return {"tabs": self.list(), "engines": self.engines(), "favorites": self.ws.favorites(),
                "search_engine": self.settings.get("browser_search_engine", "duckduckgo"),
                "web_search": bool(self.keys.has("brave")), "install": self.install_view()}

    # ------------------------------------------------------------------ onglets
    def open(self, target="", engine=None, owner="utilisateur", mission_id=None):
        eng = self._engine(engine) if owner == "utilisateur" else (
            "chromium" if engine == "chromium" and self.chromium.available()[0] else "lecture")
        with self._lock:
            self._n += 1
            tab = Tab(f"t{self._n}", eng, owner, mission_id)
            self.tabs[tab.id] = tab
            if owner == "agent":   # au plus 6 onglets d'agent ouverts : les plus anciens se ferment
                agents = [t for t in self.tabs.values() if t.owner == "agent"]
                for old in agents[:-6]:
                    self._close_nolock(old.id)
        if eng == "chromium":
            try:
                self.chromium.new_page(tab.id)
            except BrowserError as e:
                tab.engine, tab.error = "lecture", f"Chromium indisponible, mode lecture : {e}"
        self._publish(tab, "ouvert")
        if target:
            self.navigate(tab.id, target)
        else:
            self._show_home(tab)
        return tab.view(full=True)

    def _close_nolock(self, tid):
        t = self.tabs.pop(tid, None)
        if t and t.engine == "chromium":
            try:
                self.chromium.close(tid)
            except BrowserError:
                pass
        return t

    def close(self, tid):
        with self._lock:
            t = self._close_nolock(tid)
        if t:
            self.ws.bus.publish("browser", tab_id=tid, action="fermé", owner=t.owner)
        return self.list()

    def _show_home(self, tab):
        tab.url, tab.title, tab.kind, tab.error = HOME, "Accueil", "accueil", ""
        tab.doc = None
        self._publish(tab, "chargé")

    def _push(self, tab, url):
        if tab.pos < len(tab.history) - 1:
            tab.history = tab.history[:tab.pos + 1]
        if not tab.history or tab.history[-1] != url:
            tab.history.append(url)
        tab.pos = len(tab.history) - 1

    def navigate(self, tid, target, push=True):
        tab = self.get(tid)
        kind, value = classify_input(target)
        if kind == "home":
            if push:
                self._push(tab, HOME)
            self._show_home(tab)
            return tab.view(full=True)
        if kind == "search":
            return self.search_in_tab(tab, value, push)
        tab.loading, tab.error = True, ""
        tab.url = value
        self._publish(tab, "chargement")
        try:
            if tab.engine == "chromium":
                info = self.chromium.goto(tid, value)
                tab.url, tab.title, tab.kind, tab.doc = info["url"], info["title"] or info["url"], "page", None
            else:
                self._load_reader(tab, value)
            if push:
                self._push(tab, tab.url)
            self.ws.add_history(tab.url, tab.title, agent=tab.owner == "agent")
        except (BrowserError, CollectError) as e:
            tab.error = str(e)
            if push:
                self._push(tab, value)
        finally:
            tab.loading = False
        self._publish(tab, "erreur" if tab.error else "chargé")
        return tab.view(full=True)

    def step(self, tid, delta):
        tab = self.get(tid)
        if tab.engine == "chromium" and tab.kind == "page":
            try:
                info = self.chromium.history_step(tid, delta)
                tab.url, tab.title = info["url"], info["title"] or info["url"]
                self._publish(tab, "chargé")
                return tab.view(full=True)
            except BrowserError:
                pass
        new = tab.pos + delta
        if not 0 <= new < len(tab.history):
            return tab.view(full=True)
        tab.pos = new
        return self.navigate(tid, tab.history[new], push=False)

    def reload(self, tid):
        tab = self.get(tid)
        if tab.engine == "chromium" and tab.kind == "page":
            info = self.chromium.reload(tid)
            tab.url, tab.title = info["url"], info["title"] or info["url"]
            self._publish(tab, "chargé")
            return tab.view(full=True)
        if tab.kind == "recherche" and tab.doc:
            return self.search_in_tab(tab, tab.doc.get("query", ""), push=False)
        return self.navigate(tid, tab.url, push=False)

    def switch_engine(self, tid, engine):
        tab = self.get(tid)
        engine = self._engine(engine)
        if engine == tab.engine:
            return tab.view(full=True)
        if tab.engine == "chromium":
            self.chromium.close(tid)
        tab.engine = engine
        if engine == "chromium":
            self.chromium.new_page(tid)
        return self.navigate(tid, tab.url, push=False) if tab.url != HOME else tab.view(full=True)

    # ------------------------------------------------------------------ mode lecture
    def _load_reader(self, tab, url):
        data, ctype, final = fetch(url, self.allow_private, 30)
        tab.url = final
        if "html" in ctype or (not ctype and b"<html" in data[:3000].lower()) or "xml" in ctype and b"<html" in data[:3000].lower():
            doc = read_html(extract.decode(data), final, self.proxy_url)
            tab.title, tab.kind = doc["title"] or final, "page"
            doc["links_count"], doc["tables_count"] = len(doc["links"]), len(doc["tables"])
            tab.doc = doc
        elif ctype == "application/pdf" or final.lower().endswith(".pdf"):
            text = ""
            try:
                text = "\n\n".join(s[1] for s in extract.pdf_segments(data))
            except Exception as e:
                text = f"(texte du PDF illisible : {e})"
            name = safe_filename(Path(urllib.parse.urlparse(final).path).name or "document.pdf")
            tab.title, tab.kind = name, "fichier"
            paras = "".join(f"<p>{html.escape(p)}</p>" for p in text.split("\n\n")[:3000] if p.strip())
            tab.doc = {"html": paras, "text": text, "links": [], "tables": [], "images": 0,
                       "file": {"name": name, "mime": "application/pdf", "bytes": len(data)}}
        elif ctype.startswith("text/"):
            text = extract.clean(extract.decode(data))
            tab.title, tab.kind = Path(urllib.parse.urlparse(final).path).name or final, "page"
            tab.doc = {"html": f"<pre>{html.escape(text[:400000])}</pre>", "text": text, "links": [], "tables": [],
                       "images": 0}
        elif ctype.startswith("image/"):
            tab.title, tab.kind = Path(urllib.parse.urlparse(final).path).name or final, "page"
            tab.doc = {"html": f'<img src="{html.escape(self.proxy_url(final), quote=True)}" alt="">', "text": "",
                       "links": [], "tables": [], "images": 1}
        else:
            name = safe_filename(Path(urllib.parse.urlparse(final).path).name or "fichier")
            tab.title, tab.kind = name, "fichier"
            tab.doc = {"html": "", "text": "", "links": [], "tables": [], "images": 0,
                       "file": {"name": name, "mime": ctype or "application/octet-stream", "bytes": len(data),
                                "blocked": blocked_name(name)}}

    # ------------------------------------------------------------------ recherche
    def search(self, query, n=8, science=False):
        """Résultats de recherche réels : Brave Search (si une clé est réglée), Wikipédia et, pour les sujets
        scientifiques, HAL / OpenAlex / arXiv. Renvoie (résultats, notes)."""
        out, notes = [], []
        if self.keys.has("brave"):
            try:
                j = self._brave(query, n)
                for r in ((j.get("web") or {}).get("results") or [])[:n]:
                    if str(r.get("url", "")).startswith("http"):
                        out.append({"title": re.sub(r"<[^>]+>", "", r.get("title") or r["url"])[:200], "url": r["url"],
                                    "snippet": re.sub(r"<[^>]+>", "", r.get("description") or "")[:400],
                                    "provider": "Brave Search"})
            except (CollectError, ValueError) as e:
                notes.append(f"Brave Search : {e}")
        else:
            notes.append("Recherche web générale indisponible : ajoutez une clé Brave Search (offre gratuite) dans "
                         "Connaissances › Chercheur. Résultats limités à Wikipédia et aux sources scientifiques.")
        if self.wiki:
            lang = self.settings.get("kb_wikipedia_lang", "fr") or "fr"
            try:
                for r in self.wiki.search(query, lang, 4 if out else 6):
                    out.append({"title": r["title"] + " — Wikipédia", "url": r["url"], "snippet": r["snippet"],
                                "provider": "Wikipédia"})
            except CollectError as e:
                notes.append(f"Wikipédia : {e}")
        if science and self.research:
            for key in ("openalex", "hal", "arxiv"):
                src = self.research.sources.get(key)
                if not src or not src.ready()[0]:
                    continue
                try:
                    for r in src.search(query, 3):
                        out.append({"title": r["title"][:200], "url": r["url"], "snippet": r["text"][:400],
                                    "provider": src.label, "text": r["text"]})
                except Exception as e:
                    notes.append(f"{src.label} : {str(e)[:120]}")
        seen, uniq = set(), []
        for r in out:
            if r["url"] not in seen:
                seen.add(r["url"])
                uniq.append(r)
        return uniq, notes

    def _brave(self, query, n):
        import json as _json
        url = self.brave_endpoint + "?" + urllib.parse.urlencode({"q": query, "count": n, "search_lang": "fr"})
        data, _, _ = fetch(url, self.allow_private, 20, {"X-Subscription-Token": self.keys.get("brave"),
                                                         "Accept": "application/json"})
        return _json.loads(data.decode("utf-8"))

    def search_in_tab(self, tab, query, push=True):
        if tab.engine == "chromium" and tab.owner == "utilisateur":
            eng = self.settings.get("browser_search_engine", "duckduckgo")
            tmpl = SEARCH_PAGES.get(eng, SEARCH_PAGES["duckduckgo"])[1]
            return self.navigate(tab.id, tmpl.format(q=urllib.parse.quote_plus(query)), push)
        tab.loading, tab.error, tab.kind = True, "", "recherche"
        tab.url, tab.title = f"jarvis:recherche?q={urllib.parse.quote(query)}", f"Recherche : {query}"
        self._publish(tab, "chargement")
        results, notes = self.search(query, 8, science=bool(re.search(
            r"pdf|scientifique|article|publication|thèse|these|étude|etude|recherche", query, re.I)))
        tab.doc = {"query": query, "results": [{k: v for k, v in r.items() if k != "text"} for r in results],
                   "note": " ".join(notes), "html": "", "text": "\n".join(f"{r['title']} — {r['url']}\n{r['snippet']}"
                                                                         for r in results), "links": [r["url"] for r in results],
                   "tables": [], "images": 0, "_full": results}
        if not results:
            tab.error = "Aucun résultat." + (" " + notes[0] if notes else "")
        tab.loading = False
        if push:
            self._push(tab, tab.url)
        self.ws.add_history(tab.url, tab.title, agent=tab.owner == "agent")
        self._publish(tab, "erreur" if tab.error else "chargé")
        return tab.view(full=True)

    # ------------------------------------------------------------------ contenu pour l'IA
    def page_content(self, tid):
        tab = self.get(tid)
        if tab.engine == "chromium" and tab.kind == "page":
            t = self.chromium.text(tid)
            return {"url": t["url"], "title": t["title"], "text": extract.clean(t["text"]), "links": t["links"],
                    "tables": []}
        d = tab.doc or {}
        return {"url": tab.url, "title": tab.title, "text": d.get("text", ""), "links": d.get("links", []),
                "tables": d.get("tables", [])}

    # ------------------------------------------------------------------ agent web
    def agent_search(self, query, mission_id=None, n=8, science=False):
        tab = self.open("", owner="agent", mission_id=mission_id)
        t = self.get(tab["id"])
        self.search_in_tab(t, query)
        full = (t.doc or {}).get("_full", [])
        if science is True and not any(r.get("provider") in ("OpenAlex", "HAL", "arXiv") for r in full):
            pass
        return t.id, full[:n], (t.doc or {}).get("note", "")

    def agent_read(self, url, mission_id=None, tab_id=None, max_chars=60000):
        """Ouvre une page dans un onglet d'agent visible et renvoie son texte. Respecte robots.txt."""
        if not self.robots.allowed(url):
            raise BrowserError(f"robots.txt du site interdit la lecture automatique de cette page : {url}")
        if tab_id and tab_id in self.tabs:
            view = self.navigate(tab_id, url)
        else:
            view = self.open(url, owner="agent", mission_id=mission_id)
        if view.get("error"):
            raise BrowserError(view["error"])
        c = self.page_content(view["id"])
        c["tab_id"] = view["id"]
        c["text"] = c["text"][:max_chars]
        return c

    # ------------------------------------------------------------------ téléchargements
    def download(self, url, project_id=None, mission_id=None, by_agent=False, approved=False):
        """Téléchargement voulu par l'utilisateur (ou autorisé par lui pour un agent)."""
        if by_agent and not approved:
            raise BrowserError("Un agent ne télécharge rien sans votre accord.")
        name = safe_filename(Path(urllib.parse.urlparse(url).path).name or "fichier")
        if blocked_name(name):
            raise BrowserError(f"Téléchargement refusé : les fichiers {Path(name).suffix} peuvent exécuter du code.")
        data, ctype, final = fetch(url, self.allow_private, 120)
        return self._save_download(name, data, ctype, final, project_id, mission_id)

    def _save_download(self, name, data, ctype, url, project_id=None, mission_id=None):
        if len(data) > MAX_DOWNLOAD:
            raise BrowserError("Fichier trop volumineux (200 Mo au maximum)")
        if data[:2] == b"MZ":
            raise BrowserError("Téléchargement refusé : le fichier est un programme Windows.")
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        dest = self.downloads_dir / name
        k = 1
        while dest.exists():
            dest = self.downloads_dir / f"{Path(name).stem} ({k}){Path(name).suffix}"
            k += 1
        dest.write_bytes(data)
        r = self.ws.add_file("telechargement", data, ctype or "application/octet-stream", dest.name, project_id,
                             mission_id=mission_id, meta={"url": url, "saved_to": str(dest)}, saved=True,
                             name_hint=dest.name, dedupe=False)
        self.ws.log("jarvis", f"Téléchargement : {dest.name}", url, True, "navigateur", mission_id)
        return r

    # ------------------------------------------------------------------ Chromium : rappels
    def _chromium_event(self, kind, payload):
        if kind == "navigated":
            tid, page = payload
            tab = self.tabs.get(tid)
            if tab and not tab.loading:
                try:
                    if page.url and page.url != tab.url and not page.url.startswith("about:"):
                        tab.url, tab.kind = page.url, "page"
                        self._push(tab, tab.url)
                        self.ws.add_history(tab.url, tab.title, agent=tab.owner == "agent")
                except Exception:
                    pass
                self._publish(tab, "navigation")
        elif kind == "popup":
            with self._lock:
                self._n += 1
                tab = Tab(f"t{self._n}", "chromium", "utilisateur")
                self.tabs[tab.id] = tab
            self.chromium.adopt(tab.id, payload)
            tab.kind = "page"
            try:
                tab.url, tab.title = payload.url, payload.url
            except Exception:
                pass
            self._publish(tab, "ouvert")

    def _chromium_download(self, tab_id, download):
        """Appelé dans le fil Chromium quand une page lance un téléchargement (après un clic de l'utilisateur)."""
        tab = self.tabs.get(tab_id)
        name = safe_filename(download.suggested_filename)
        if blocked_name(name):
            download.cancel()
            if tab:
                tab.error = f"Téléchargement refusé : les fichiers {Path(name).suffix} peuvent exécuter du code."
                self._publish(tab, "erreur")
            return
        tmp = self.downloads_dir / f".{name}.part"
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        try:
            download.save_as(str(tmp))
            data = tmp.read_bytes()
            r = self._save_download(name, data, "", download.url)
            if tab:
                self._publish(tab, "téléchargé", resource_id=r["id"], file=r["title"])
        except Exception as e:
            if tab:
                tab.error = f"Téléchargement impossible : {e}"
                self._publish(tab, "erreur")
        finally:
            tmp.unlink(missing_ok=True)

    # ------------------------------------------------------------------ installation du moteur Chromium
    def install_view(self):
        return {"state": self.install["state"], "log": self.install["log"][-40:], "started": self.install["started"]}

    def start_install(self):
        if self.install["state"] == "en cours":
            return self.install_view()
        py = Path(sys.executable)
        cons = py.with_name("python.exe") if py.name.lower() == "pythonw.exe" else py
        cmds = self._install_cmds or [[str(cons), "-m", "pip", "install", "--user", "--upgrade", "playwright"]]
        if not self._install_cmds and sys.platform != "win32":
            cmds.append([str(cons), "-m", "playwright", "install", "chromium"])
        self.install = {"state": "en cours", "log": [], "started": time.time()}

        def run():
            ok = True
            for cmd in cmds:
                self.install["log"].append("$ " + " ".join(Path(c).name if i == 0 else c for i, c in enumerate(cmd)))
                try:
                    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    for line in p.stdout:
                        if line.strip():
                            self.install["log"].append(line.rstrip()[:300])
                    ok = p.wait() == 0 and ok
                except OSError as e:
                    self.install["log"].append(f"Erreur : {e}")
                    ok = False
                if not ok:
                    break
            if ok:
                import importlib
                import site
                importlib.invalidate_caches()
                try:
                    site.addsitedir(site.getusersitepackages())
                except Exception:
                    pass
                self.chromium.error = ""
            self.install["state"] = "terminée" if ok and Chromium.installed() else "échec"
            if self.install["state"] == "échec" and ok:
                self.install["log"].append("Playwright est installé mais n'est pas encore visible : redémarrez Jarvis.")
            self.ws.bus.publish("browser", action="installation", state=self.install["state"])
        threading.Thread(target=run, daemon=True).start()
        return self.install_view()

    def shutdown(self):
        self.chromium.stop()
