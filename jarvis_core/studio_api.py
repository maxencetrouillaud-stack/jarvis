"""Routes HTTP de l'espace de travail : projets, ressources, artefacts, navigateur, images, missions, agents."""
import base64
import binascii
import io
import json
import re
import zipfile

from .artifacts import KINDS as ARTIFACT_KINDS, ArtifactError
from .browser import BrowserError, SEARCH_PAGES
from .collect import CollectError
from .images import ImageError
from .tools import AGENTS


class Bad(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


class Raw:
    """Réponse binaire (fichier à télécharger, image)."""

    def __init__(self, data, ctype, filename=None):
        self.data, self.ctype, self.filename = data, ctype, filename


def _int(v, default=None):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _slug(s):
    return re.sub(r"[^\w-]+", "-", s or "").strip("-") or "projet"


def _pid(v):
    """Identifiant de projet : None (aucun) ou entier."""
    if v in (None, "", "null", "aucun"):
        return None
    i = _int(v)
    if i is None:
        raise Bad(400, "Projet invalide")
    return i


def studio_state(app):
    return {"tools": app.missions.registry.describe(), "agents": AGENTS, "artifact_kinds": ARTIFACT_KINDS,
            "workspace": app.ws.stats(), "images": app.images.state(), "browser": app.browser.engines(),
            "search_pages": {k: v[0] for k, v in SEARCH_PAGES.items()}}


def project_export(app, pid):
    p = app.ws.project_detail(pid)
    if not p:
        raise Bad(404, "Projet introuvable")
    data, _ = app.artifacts.export_zip([a["id"] for a in p["artifacts"]], p["name"])
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n in src.namelist():
            z.writestr(f"artefacts/{n}", src.read(n))
        for r in p["images"]:
            d, mime = app.ws.resource_bytes(r["id"])
            if d:
                slug = _slug(r["title"])[:40]
                ext = ".jpg" if mime == "image/jpeg" else ".png"
                z.writestr(f"images/{r['id']}-{slug}{ext}", d)
        lines = [f"# {p['name']}", "", p["objective"] or "", "", "## Sources", ""]
        lines += [f"- [{s['title']}]({s['url']})" for s in p["sources"] if s.get("url")]
        lines += ["", "## Notes", "", p["notes"] or ""]
        z.writestr("projet.md", "\n".join(lines))
    return buf.getvalue()


def handle(app, method, path, q, body):
    """Renvoie un objet JSON, un Raw, ou None si la route n'existe pas. `body` est une fonction."""
    ws = app.ws
    arg = lambda k, d="": (q.get(k) or [d])[0]

    if path == "/api/studio" and method == "GET":
        return studio_state(app)

    # ------------------------------------------------------------------ projets
    if path == "/api/projects" and method == "GET":
        return ws.projects(arg("archived") == "1")
    if path == "/api/projects" and method == "POST":
        b = body()
        try:
            p = ws.create_project(b.get("name"), str(b.get("objective") or ""))
        except ValueError as e:
            raise Bad(400, str(e)) from None
        if b.get("conv_id"):
            ws.set_conversation_project(str(b["conv_id"]), p["id"])
        return p
    m = re.match(r"^/api/projects/(\d+)(/[\w-]+)?$", path)
    if m:
        pid, sub = int(m.group(1)), m.group(2) or ""
        if not ws.project(pid):
            raise Bad(404, "Projet introuvable")
        if not sub and method == "GET":
            d = ws.project_detail(pid)
            d["missions"] = app.missions.list(project_id=pid, limit=30)
            return d
        if not sub and method == "PATCH":
            try:
                return ws.update_project(pid, body())
            except ValueError as e:
                raise Bad(400, str(e)) from None
        if sub == "/conversations" and method == "POST":
            b = body()
            cid = str(b.get("conv_id") or "")
            if not app.store.get_conversation(cid):
                raise Bad(404, "Conversation introuvable")
            ws.set_conversation_project(cid, pid if not b.get("detach") else None)
            return ws.project_detail(pid)
        if sub == "/export" and method == "GET":
            return Raw(project_export(app, pid), "application/zip", _slug(ws.project(pid)["name"]) + ".zip")
        if sub == "/missions" and method == "POST":
            b = body()
            try:
                mid = app.missions.launch(str(b.get("goal") or ""), project_id=pid, requested=b.get("model") or "auto")
            except ValueError as e:
                raise Bad(400, str(e)) from None
            return app.missions.view(mid)

    # conversation ↔ projet
    m = re.match(r"^/api/conversations/([\w-]+)/project$", path)
    if m and method == "POST":
        cid = m.group(1)
        if not app.store.get_conversation(cid):
            raise Bad(404, "Conversation introuvable")
        try:
            ws.set_conversation_project(cid, _pid(body().get("project_id")))
        except KeyError as e:
            raise Bad(404, str(e)) from None
        return {"ok": True, "project_id": ws.conversation_project(cid)}
    m = re.match(r"^/api/conversations/([\w-]+)/workspace$", path)
    if m and method == "GET":   # ressources produites dans une conversation (panneau de droite)
        cid = m.group(1)
        return {"project_id": ws.conversation_project(cid), "artifacts": app.artifacts.list(conv_id=cid, limit=30),
                "images": ws.resources("image", conv_id=cid, limit=30),
                "missions": [x for x in app.missions.list(limit=200) if x["conv_id"] == cid][:20]}

    # ------------------------------------------------------------------ ressources
    if path == "/api/resources" and method == "GET":
        ids = [int(x) for x in arg("ids").split(",") if x.strip().isdigit()][:200]
        if ids:
            return [r for r in (ws.resource(i) for i in ids) if r]
        kind = arg("kind") or None
        kinds = kind.split(",") if kind else None
        saved = {"1": True, "0": False}.get(arg("saved"))
        return ws.resources(kinds, _pid(arg("project_id")) if arg("project_id") else None, saved,
                            _int(arg("mission_id")), arg("conv_id") or None, min(500, _int(arg("limit"), 200)),
                            arg("q") or None)
    m = re.match(r"^/api/resources/(\d+)(/[\w-]+)?$", path)
    if m:
        rid, sub = int(m.group(1)), m.group(2) or ""
        r = ws.resource(rid)
        if not r:
            raise Bad(404, "Ressource introuvable")
        if not sub and method == "PATCH":
            b = body()
            if "project_id" in b:
                b["project_id"] = _pid(b["project_id"])
            try:
                return ws.update_resource(rid, b)
            except KeyError as e:
                raise Bad(404, str(e)) from None
        if not sub and method == "DELETE":
            ws.delete_resource(rid)
            return {"ok": True}
        if sub == "/to-library" and method == "POST":
            # source web validée par l'utilisateur → bibliothèque technique (avec son accord explicite : ce clic)
            if r["kind"] != "source" or not r.get("url"):
                raise Bad(400, "Seule une source web peut être ajoutée à la bibliothèque")
            try:
                jid = app.kbs.submit_url(r["url"])
            except (CollectError, ValueError) as e:
                raise Bad(400, str(e)) from None
            ws.log("jarvis", "Source envoyée à la bibliothèque", r["url"])
            return {"ok": True, "job": jid}

    # ------------------------------------------------------------------ artefacts
    if path == "/api/artifacts" and method == "GET":
        return app.artifacts.list(_pid(arg("project_id")) if arg("project_id") else None, arg("q") or None,
                                  arg("conv_id") or None)
    if path == "/api/artifacts" and method == "POST":
        b = body()
        try:
            return app.artifacts.create(str(b.get("title") or "Sans titre"), str(b.get("kind") or "html"),
                                        str(b.get("content") or ""), _pid(b.get("project_id")), b.get("conv_id"),
                                        note="création manuelle", author="utilisateur")
        except ArtifactError as e:
            raise Bad(400, str(e)) from None
    m = re.match(r"^/api/artifacts/(\d+)(/[\w-]+)?$", path)
    if m:
        aid, sub = int(m.group(1)), m.group(2) or ""
        v = _int(arg("v"))
        a = app.artifacts.get(aid, v)
        if not a:
            raise Bad(404, "Artefact introuvable")
        if not sub and method == "GET":
            a["sandbox_url"] = sandbox_url(app, aid, a["shown_version"])
            return a
        if not sub and method == "PATCH":
            b = body()
            if "project_id" in b:
                b["project_id"] = _pid(b["project_id"])
            try:
                out = app.artifacts.update(aid, b)
            except (KeyError, ArtifactError) as e:
                raise Bad(400, str(e)) from None
            out["sandbox_url"] = sandbox_url(app, aid, out["shown_version"])
            return out
        if not sub and method == "DELETE":
            app.artifacts.delete(aid)
            return {"ok": True}
        if sub == "/restore" and method == "POST":
            try:
                out = app.artifacts.restore(aid, _int(body().get("n")))
            except KeyError as e:
                raise Bad(404, str(e)) from None
            out["sandbox_url"] = sandbox_url(app, aid, out["shown_version"])
            return out
        if sub == "/check" and method == "POST":
            return app.artifacts.check(aid, bool(body().get("runtime", True)))
        if sub == "/edit" and method == "POST":   # modification demandée depuis l'espace Artefacts
            b = body()
            instr = str(b.get("instruction") or "").strip()
            if len(instr) < 3:
                raise Bad(400, "Décrivez la modification.")
            mid = app.missions.launch(instr, conv_id=a.get("conv_id"), project_id=a.get("project_id"),
                                      requested=b.get("model") or "auto", needs=["artifact_edit"],
                                      selection=b.get("selection"), artifact_id=aid,
                                      planner="modification demandée depuis l'espace Artefacts")
            return app.missions.view(mid)
        if sub == "/export" and method == "GET":
            if arg("format") == "zip":
                data, name = app.artifacts.export_zip([aid], a["title"])
                return Raw(data, "application/zip", name)
            data, name = app.artifacts.export_html(aid, v)
            return Raw(data, "text/html; charset=utf-8", name)

    # ------------------------------------------------------------------ navigateur
    br = app.browser
    if path == "/api/browser" and method == "GET":
        return br.state()
    if path == "/api/browser/tabs" and method == "POST":
        b = body()
        try:
            return br.open(str(b.get("target") or ""), b.get("engine"))
        except BrowserError as e:
            raise Bad(400, str(e)) from None
    if path == "/api/browser/favorites" and method == "GET":
        return ws.favorites()
    if path == "/api/browser/favorites" and method == "POST":
        b = body()
        url = str(b.get("url") or "")
        if not re.match(r"^https?://", url):
            raise Bad(400, "Seules les adresses http(s) peuvent être mises en favori")
        return ws.add_favorite(url, str(b.get("title") or url))
    m = re.match(r"^/api/browser/favorites/(\d+)$", path)
    if m and method == "DELETE":
        return ws.remove_favorite(int(m.group(1)))
    if path == "/api/browser/history" and method == "GET":
        return ws.history(min(500, _int(arg("limit"), 200)), arg("q") or None)
    if path == "/api/browser/history" and method == "DELETE":
        ws.clear_history()
        return {"ok": True}
    if path == "/api/browser/download" and method == "POST":
        b = body()
        try:
            return br.download(str(b.get("url") or ""), _pid(b.get("project_id")))
        except (BrowserError, CollectError) as e:
            raise Bad(400, str(e)) from None
    if path == "/api/browser/install" and method == "POST":
        return br.start_install()
    m = re.match(r"^/api/browser/tabs/(t\d+)(/[\w-]+)?$", path)
    if m:
        tid, sub = m.group(1), m.group(2) or ""
        try:
            tab = br.get(tid)
        except KeyError:
            raise Bad(404, "Onglet fermé") from None
        try:
            if not sub and method == "GET":
                return tab.view(full=True)
            if not sub and method == "DELETE":
                return br.close(tid)
            if sub == "/navigate" and method == "POST":
                return br.navigate(tid, str(body().get("target") or ""))
            if sub == "/back" and method == "POST":
                return br.step(tid, -1)
            if sub == "/forward" and method == "POST":
                return br.step(tid, 1)
            if sub == "/reload" and method == "POST":
                return br.reload(tid)
            if sub == "/home" and method == "POST":
                return br.navigate(tid, "jarvis:accueil")
            if sub == "/engine" and method == "POST":
                return br.switch_engine(tid, str(body().get("engine") or "lecture"))
            if sub == "/frame" and method == "GET":
                if tab.engine != "chromium":
                    raise Bad(400, "Cet onglet est en mode lecture")
                return Raw(br.chromium.frame(tid), "image/jpeg")
            if sub == "/input" and method == "POST":
                if tab.engine != "chromium":
                    raise Bad(400, "Cet onglet est en mode lecture")
                b = body()
                action = str(b.pop("action", ""))
                info = br.chromium.input(tid, action, **b)
                if info["url"] != tab.url:
                    tab.url, tab.title = info["url"], info["title"] or info["url"]
                    br._push(tab, tab.url)
                    ws.add_history(tab.url, tab.title)
                else:
                    tab.title = info["title"] or tab.title
                br._publish(tab, "interaction")
                return tab.view()
            if sub == "/content" and method == "GET":
                c = br.page_content(tid)
                c["text"] = c["text"][:200000]
                return c
        except BrowserError as e:
            raise Bad(400, str(e)) from None

    # ------------------------------------------------------------------ images
    im = app.images
    if path == "/api/images" and method == "GET":
        saved = {"1": True, "0": False}.get(arg("saved"))
        return {"state": im.state(), "images": ws.resources("image", _pid(arg("project_id")) if arg("project_id") else None,
                                                             saved, limit=min(500, _int(arg("limit"), 200)),
                                                             q=arg("q") or None)}
    if path == "/api/images/models" and method == "POST":
        return {"models": im.refresh_models(), "state": im.state()}
    if path == "/api/images/generate" and method == "POST":
        b = body()
        try:
            return im.generate(str(b.get("prompt") or ""), str(b.get("style") or "libre"), str(b.get("format") or "1:1"),
                               str(b.get("size") or ""), _int(b.get("n"), 1),
                               [int(x) for x in b.get("refs") or [] if str(x).isdigit()], _pid(b.get("project_id")))
        except ImageError as e:
            raise Bad(400, str(e)) from None
    if path == "/api/images/upload" and method == "POST":
        b = body()
        try:
            data = base64.b64decode(str(b.get("data") or ""), validate=True)
        except (binascii.Error, ValueError):
            raise Bad(400, "Fichier illisible") from None
        try:
            return im.upload(data, str(b.get("name") or "image"), _pid(b.get("project_id")))
        except ImageError as e:
            raise Bad(400, str(e)) from None
    m = re.match(r"^/api/images/(\d+)/(edit|upscale)$", path)
    if m and method == "POST":
        rid, op = int(m.group(1)), m.group(2)
        b = body()
        try:
            if op == "edit":
                return im.edit(rid, str(b.get("instruction") or ""), str(b.get("style") or "libre"), _int(b.get("n"), 1),
                               extra_refs=[int(x) for x in b.get("refs") or [] if str(x).isdigit()])
            return im.upscale(rid, str(b.get("size") or "2K"))
        except ImageError as e:
            raise Bad(400, str(e)) from None

    # ------------------------------------------------------------------ missions et agents
    if path == "/api/missions" and method == "GET":
        return app.missions.list(_pid(arg("project_id")) if arg("project_id") else None, arg("status") or None,
                                 min(200, _int(arg("limit"), 50)))
    if path == "/api/missions" and method == "POST":
        b = body()
        try:
            mid = app.missions.launch(str(b.get("goal") or ""), conv_id=b.get("conv_id"),
                                      project_id=_pid(b.get("project_id")), requested=b.get("model") or "auto")
        except ValueError as e:
            raise Bad(400, str(e)) from None
        return app.missions.view(mid)
    m = re.match(r"^/api/missions/(\d+)(/[\w-]+)?$", path)
    if m:
        mid, sub = int(m.group(1)), m.group(2) or ""
        if not app.missions.get(mid):
            raise Bad(404, "Mission introuvable")
        try:
            if not sub and method == "GET":
                return app.missions.view(mid)
            if sub == "/approve" and method == "POST":
                return app.missions.decide(mid, str(body().get("decision") or "refuser"))
            if sub == "/stop" and method == "POST":
                return app.missions.stop(mid)
            if sub == "/resume" and method == "POST":
                return app.missions.resume(mid)
        except KeyError as e:
            raise Bad(409, str(e)) from None
        except ValueError as e:
            raise Bad(400, str(e)) from None
    if path == "/api/agents/log" and method == "GET":
        return ws.agent_log(_int(arg("mission_id")), min(500, _int(arg("limit"), 120)))
    if path == "/api/tools" and method == "GET":
        return app.missions.registry.describe()
    return None


def sandbox_url(app, aid, version):
    return f"/sandbox/a/{aid}?v={version}&s={app.ws.sign(f'a{aid}')}"


def to_json(o):
    return json.dumps(o, ensure_ascii=False)
