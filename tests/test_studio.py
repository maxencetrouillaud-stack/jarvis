"""Tests de l'espace de travail (0.7) : socle commun, artefacts, navigateur, images, missions multi-outils et leurs
communications réelles (faux fournisseurs au format de fil des vrais, pages servies localement, vrai Chromium)."""
import json
import re
import sys
import threading
import time
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes import Fake, FakeCloudflare, FakeImages, FakeScience, FakeWeb, png  # noqa: E402
from test_core import Base, blob, text_of  # noqa: E402
from jarvis_core import artifacts as art  # noqa: E402
from jarvis_core.browser import Browser, Reader, classify_input, read_html  # noqa: E402
from jarvis_core.collect import Wikipedia  # noqa: E402
from jarvis_core.missions import detect  # noqa: E402
from jarvis_core.providers import REGISTRY  # noqa: E402
from jarvis_core.server import App, serve  # noqa: E402
from jarvis_core.tools import Context, NeedsApproval, Registry, Tool, ToolError  # noqa: E402
from jarvis_core.workspace import EventBus  # noqa: E402

try:
    import playwright  # noqa: F401
    HAS_PW = True
except ImportError:
    HAS_PW = False

HTML_APP = """<!doctype html><html lang="fr"><head><title>Compteur</title><style>body{font-family:sans-serif}</style></head>
<body><h1 id="titre">Compteur</h1><button id="plus">Ajouter</button><p id="v">0</p>
<script>let n=0;document.getElementById('plus').onclick=()=>{n++;document.getElementById('v').textContent=n;};</script>
</body></html>"""
BROKEN_APP = HTML_APP.replace("let n=0;", "let n=0; inexistante();")


def studio_app(tmp, fakes=None, kb_options=None, **settings):
    fakes = fakes or {}
    app = App(Path(tmp), kb_options or {})
    provs = {pid: {"enabled": pid in fakes, "base_url": fakes[pid].url if pid in fakes else "http://127.0.0.1:9"}
             for pid in REGISTRY}
    app.settings.update({"providers": provs, "memory_auto_extract": False, **settings})
    for pid in fakes:
        app.keys.set(pid, "cle-test")
    app.brain.refresh_models()
    return app


def wait(pred, timeout=30, step=0.05):
    end = time.time() + timeout
    while time.time() < end:
        v = pred()
        if v:
            return v
        time.sleep(step)
    raise AssertionError("délai dépassé")


# ============================================================================ socle
class Foundation(Base):
    def test_event_bus_since_and_wait(self):
        bus = EventBus(keep=5)
        got = []
        th = threading.Thread(target=lambda: got.extend(bus.since(0, 5)))
        th.start()
        time.sleep(0.1)
        bus.publish("mission", mission_id=1, kind="plan")
        th.join(3)
        self.assertEqual(got[0]["topic"], "mission")
        for i in range(8):
            bus.publish("agent", n=i)
        self.assertEqual(len(bus.since(0, 0)), 5)            # tampon borné
        self.assertEqual(bus.since(bus.last_id, 0.05), [])   # rien de neuf : attente bornée

    def test_projects_resources_and_signatures(self):
        app = studio_app(self.tmp)
        ws = app.ws
        app.store.add_memory("L'utilisateur avance sur le treuil", "projets", 3, nature="projet", project="Treuil")
        names = [p["name"] for p in ws.projects()]
        self.assertIn("Treuil", names)                         # projets de la mémoire = projets de l'espace de travail
        pid = ws.ensure_project("treuil")                      # insensible à la casse : pas de doublon
        self.assertEqual(len([p for p in ws.projects() if p["name"].lower() == "treuil"]), 1)
        with self.assertRaises(ValueError):
            ws.create_project("TREUIL")
        ws.update_project(pid, {"name": "Treuil Huchez", "objective": "Concevoir le treuil"})
        self.assertEqual(app.store.projects()[0]["project"], "Treuil Huchez")   # les souvenirs suivent le nouveau nom
        r1 = ws.add_file("image", png(), "image/png", "bleu", pid)
        r2 = ws.add_file("image", png(), "image/png", "bleu encore", pid)
        self.assertEqual(r1["id"], r2["id"])                   # même contenu, même projet : dédupliqué
        self.assertTrue(r1["src"].startswith(f"/media/r/{r1['id']}?s="))
        self.assertTrue(ws.check(f"r{r1['id']}", r1["src"].split("s=")[1]))
        self.assertFalse(ws.check(f"r{r1['id'] + 1}", r1["src"].split("s=")[1]))
        s1 = ws.add_source("https://exemple.fr/a", "Page A", "extrait", pid)
        s2 = ws.add_source("https://exemple.fr/a", "Page A (maj)", "", pid)
        self.assertEqual(s1["id"], s2["id"])
        d = ws.project_detail(pid)
        self.assertEqual((d["counts"]["images"], d["counts"]["sources"], d["counts"]["memories"]), (1, 1, 1))
        c = app.store.create_conversation()
        a = app.artifacts.create("Plan", "markdown", "# Plan du treuil", conv_id=c["id"])
        ws.set_conversation_project(c["id"], pid)      # ce que la conversation a produit rejoint le projet
        self.assertEqual(app.artifacts.get(a["id"])["project_id"], pid)
        app.close()

    def test_temporary_images_are_purged_unless_used(self):
        app = studio_app(self.tmp)
        ws = app.ws
        keep = ws.add_file("image", png((1, 2, 3)), "image/png", "utilisée")
        gone = ws.add_file("image", png((9, 9, 9)), "image/png", "oubliée")
        saved = ws.add_file("image", png((5, 5, 5)), "image/png", "enregistrée", saved=True)
        app.artifacts.create("A", "html", f'<img src="jarvis-image:{keep["id"]}">')
        ws._x("UPDATE resources SET created=created-30*86400")
        self.assertEqual(ws.purge_temporary(7), 1)
        self.assertIsNone(ws.resource(gone["id"]))
        self.assertIsNotNone(ws.resource(keep["id"]))
        self.assertIsNotNone(ws.resource(saved["id"]))
        app.close()

    def test_backup_keeps_saved_creations(self):
        import zipfile
        app = studio_app(self.tmp)
        kept = app.ws.add_file("image", png(), "image/png", "gardée", saved=True)
        app.ws.add_file("image", png((1, 1, 1)), "image/png", "temporaire")
        name = app.backups.create("manuel")
        files = [n for n in zipfile.ZipFile(app.home / "backups" / name).namelist() if n.startswith("files/")]
        self.assertEqual(len(files), 1)                       # l'image temporaire n'est pas sauvegardée
        p, _ = app.ws.resource_path(kept["id"])
        p.unlink()
        app.backups.restore(name)
        self.assertIsNotNone(app.ws.resource_path(kept["id"])[0])
        app.close()

    def test_registry_limits_retries_and_approval(self):
        reg = Registry()
        calls = []

        def flaky(ctx, x=0):
            calls.append(x)
            if len(calls) < 2:
                raise ToolError("panne passagère")
            return {"summary": "ok", "text": str(x)}
        reg.register(Tool("t.flaky", "Instable", "chercheur", "test", flaky, retries=1))
        reg.register(Tool("t.slow", "Lent", "chercheur", "test", lambda ctx: time.sleep(3), timeout=0.3))
        reg.register(Tool("t.sens", "Sensible", "chercheur", "test", lambda ctx: {"summary": "fait"}, sensitive=True))
        ctx = Context(None)
        res, attempts = reg.run("t.flaky", {"x": 4}, ctx)
        self.assertEqual((res["text"], attempts), ("4", 2))
        with self.assertRaises(ToolError):
            reg.run("t.slow", {}, Context(None))
        with self.assertRaises(NeedsApproval):
            reg.run("t.sens", {}, ctx)
        ctx.approved.add("tool:t.sens")
        self.assertEqual(reg.run("t.sens", {}, ctx)[0]["summary"], "fait")
        with self.assertRaises(ToolError):
            reg.run("inconnu", {}, ctx)


# ============================================================================ artefacts
class Artifacts(Base):
    def test_parse_and_edit_blocks(self):
        raw = "TITRE: Compteur\nTYPE: html\n```html\n" + HTML_APP + "\n```"
        self.assertEqual(art.parse_creation(raw)[:2], ("Compteur", "html"))
        self.assertEqual(art.parse_creation("```jsx\nexport default function App(){return <p>Salut</p>}\n```")[1], "react")
        with self.assertRaises(art.ArtifactError):
            art.parse_creation("Je ne peux pas.")
        edits = ("<<<<<<< CHERCHER\n<h1 id=\"titre\">Compteur</h1>\n=======\n<h1 id=\"titre\">Mon compteur</h1>\n"
                 ">>>>>>> REMPLACER")
        new, n = art.apply_edits(HTML_APP, edits)
        self.assertIn("Mon compteur", new)
        self.assertEqual(n, 1)
        loose = "<<<<<<< CHERCHER\n<button   id=\"plus\">Ajouter</button>\n=======\n<button id=\"plus\">+1</button>\n>>>>>>> REMPLACER"
        self.assertIn(">+1<", art.apply_edits(HTML_APP, loose)[0])     # espaces différents tolérés
        with self.assertRaises(art.ArtifactError):
            art.apply_edits(HTML_APP, "<<<<<<< CHERCHER\nabsent\n=======\nx\n>>>>>>> REMPLACER")

    def test_render_is_isolated(self):
        doc, missing = art.build_document("T", "html", HTML_APP.replace("<p id", '<img src="jarvis-image:7"><p id'),
                                          {7: "data:image/png;base64,AAA"})
        self.assertIn("connect-src 'none'", doc)
        self.assertIn("form-action 'none'", doc)
        self.assertIn("data:image/png;base64,AAA", doc)
        self.assertIn("jarvis:true", doc)          # script d'aide (erreurs, sélection d'élément)
        self.assertEqual(missing, [])
        doc, missing = art.build_document("T", "html", '<img src="jarvis-image:99">')
        self.assertEqual(missing, [99])
        react, _ = art.build_document("R", "react", 'import React, { useState } from "react";\n'
                                                     "export default function Compteur(){const [n,s]=useState(0);"
                                                     "return <button onClick={()=>s(n+1)}>{n}</button>}")
        self.assertIn("React.createElement(Compteur)", react)
        self.assertNotIn('from "react"', react)
        exp, _ = art.build_document("T", "html", HTML_APP, helper=False)
        self.assertNotIn("jarvis:true", exp)
        slides, _ = art.build_document("P", "slides", "# Un\n\n---\n\n# Deux")
        self.assertIn("marked.parse", slides)

    def test_static_checks(self):
        issues = art.static_check("html", '<html><body><script src="https://evil.example/x.js"></script>'
                                          '<img src="jarvis-image:5"><script>fetch("/api")</script></body></html>', [])
        texts = " ".join(i["text"] for i in issues)
        self.assertIn("non autorisée", texts)
        self.assertIn("jarvis-image:5", texts)
        self.assertIn("réseau", texts)
        self.assertTrue(any("lodash" in i["text"] for i in art.static_check(
            "react", 'import _ from "lodash";\nexport default function App(){return null}')))
        self.assertEqual(art.static_check("html", HTML_APP), [])

    def test_versions_restore_and_export(self):
        app = studio_app(self.tmp)
        a = app.artifacts.create("Compteur", "html", HTML_APP)
        b = app.artifacts.new_version(a["id"], HTML_APP.replace("Ajouter", "Plus"), "libellé")
        self.assertEqual(b["version"], 2)
        same = app.artifacts.new_version(a["id"], b["content"], "rien")
        self.assertEqual(same["version"], 2)           # contenu identique : pas de version inutile
        c = app.artifacts.restore(a["id"], 1)
        self.assertEqual((c["version"], c["content"]), (3, HTML_APP))
        self.assertEqual([v["n"] for v in c["versions"]], [3, 2, 1])
        data, name = app.artifacts.export_html(a["id"])
        self.assertTrue(name.endswith(".html") and b"Compteur" in data and b"jarvis:true" not in data)
        z, zname = app.artifacts.export_zip([a["id"]], "Mes créations")
        self.assertTrue(zname.endswith(".zip") and z[:2] == b"PK")
        app.close()

    @unittest.skipUnless(HAS_PW, "Playwright absent")
    def test_runtime_check_finds_js_errors(self):
        ok_doc, _ = art.build_document("T", "html", HTML_APP, helper=False)
        bad_doc, _ = art.build_document("T", "html", BROKEN_APP, helper=False)
        r_ok, r_bad = art.runtime_check(ok_doc), art.runtime_check(bad_doc)
        self.assertTrue(r_ok["checked"], r_ok)
        self.assertEqual(r_ok["errors"], [])
        self.assertTrue(r_bad["checked"])
        self.assertTrue(any("inexistante" in e for e in r_bad["errors"]), r_bad)


# ============================================================================ navigateur
PAGE = """<!doctype html><html><head><title>Treuils électriques</title><meta name="description" content="Guide">
<script>alert('x')</script><style>body{color:red}</style></head><body>
<nav><a href="/menu">Menu du site</a></nav>
<header><p>Bandeau publicitaire</p></header>
<main><h1>Choisir un treuil</h1>
<p onclick="evil()">Un treuil électrique de <b>500 kg</b> convient à la plupart des ateliers. Le couple moteur dépend du
rayon du tambour et de la charge levée, ainsi que du rendement du réducteur choisi pour l'application.</p>
<p>Voir <a href="/fiche.pdf">la fiche technique</a> et <a href="javascript:evil()">ceci</a>.</p>
<img src="/img/treuil.png" alt="Treuil" width="300"><img src="/pixel.gif" width="1" height="1">
<iframe src="https://pub.example"></iframe>
<table><tr><th>Charge</th><th>Vitesse</th></tr><tr><td>500 kg</td><td>4 m/min</td></tr></table>
<form action="/envoyer"><input name="x"><button>Envoyer</button></form>
<p>Le rendement d'un réducteur à engrenages droits est souvent supérieur à 95 %, ce qui limite les pertes.</p>
</main><footer>Mentions légales</footer></body></html>"""


class BrowserTests(Base):
    def setUp(self):
        super().setUp()
        self.web = FakeWeb()
        self.web.pages["/treuil"] = ("text/html; charset=utf-8", PAGE.encode())
        self.web.pages["/autre"] = ("text/html", b"<html><head><title>Autre</title></head><body><p>Seconde page.</p></body></html>")
        self.web.pages["/fiche.pdf"] = ("application/pdf", b"%PDF-1.4 faux")
        self.web.pages["/outil.exe"] = ("application/octet-stream", b"MZ\x90\x00binaire")
        self.web.pages["/notice.zip"] = ("application/zip", b"PK\x03\x04contenu")
        self.web.pages["/robots.txt"] = ("text/plain", b"User-agent: *\nDisallow: /prive\n")
        self.web.pages["/prive"] = ("text/html", b"<html><body><p>Interdit aux robots.</p></body></html>")
        self.web.articles["Treuil"] = "Un treuil est un appareil de levage composé d'un tambour."

    def tearDown(self):
        self.web.stop()
        super().tearDown()

    def app(self, **kw):
        return studio_app(self.tmp, kb_options={"allow_private": True,
                                                "wikipedia": Wikipedia(self.web.url + "/w/api.php", True), **kw},
                          browser_engine="lecture")

    def test_address_bar(self):
        self.assertEqual(classify_input("https://exemple.fr/a b"), ("search", "https://exemple.fr/a b"))
        self.assertEqual(classify_input("exemple.fr/page"), ("url", "https://exemple.fr/page"))
        self.assertEqual(classify_input("127.0.0.1:8080"), ("url", "http://127.0.0.1:8080"))
        self.assertEqual(classify_input("couple moteur treuil"), ("search", "couple moteur treuil"))
        self.assertEqual(classify_input(""), ("home", "jarvis:accueil"))

    def test_reader_sanitizes_and_keeps_structure(self):
        d = read_html(PAGE, "https://site.fr/treuil", lambda u: "/media/p?u=" + u)
        h = d["html"]
        for bad in ("<script", "alert", "onclick", "<iframe", "<form", "<input", "Menu du site", "Bandeau",
                    "Mentions légales", "javascript:", "<style"):
            self.assertNotIn(bad, h, bad)
        self.assertIn('data-href="https://site.fr/fiche.pdf"', h)
        self.assertIn('src="/media/p?u=https://site.fr/img/treuil.png"', h)
        self.assertNotIn("pixel.gif", h)                  # pixel espion ignoré
        self.assertEqual(d["title"], "Treuils électriques")
        self.assertEqual(d["tables"][0], [["Charge", "Vitesse"], ["500 kg", "4 m/min"]])
        self.assertIn("500 kg", d["text"])

    def test_tabs_navigation_history_favorites(self):
        app = self.app()
        b = app.browser
        t = b.open(self.web.url + "/treuil")
        self.assertEqual((t["title"], t["kind"], t["engine"]), ("Treuils électriques", "page", "lecture"))
        self.assertIn("/media/p?u=", t["doc"]["html"])
        b.navigate(t["id"], self.web.url + "/autre")
        self.assertTrue(b.get(t["id"]).view()["can_back"])
        back = b.step(t["id"], -1)
        self.assertEqual(back["title"], "Treuils électriques")
        fwd = b.step(t["id"], 1)
        self.assertEqual(fwd["title"], "Autre")
        s = b.navigate(t["id"], "treuil levage")       # recherche sans clé Brave : Wikipédia seulement, et c'est dit
        self.assertEqual(s["kind"], "recherche")
        self.assertEqual(s["doc"]["results"][0]["title"], "Treuil — Wikipédia")
        self.assertIn("Brave", s["doc"]["note"])
        hist = app.ws.history()
        self.assertTrue(any(h["title"] == "Autre" for h in hist))
        app.ws.add_favorite(self.web.url + "/treuil", "Treuils")
        self.assertEqual(app.ws.favorites()[0]["title"], "Treuils")
        pdf = b.navigate(t["id"], self.web.url + "/fiche.pdf")
        self.assertEqual(pdf["kind"], "fichier")
        b.close(t["id"])
        self.assertEqual(b.list(), [])
        app.close()

    def test_downloads_are_checked(self):
        app = self.app()
        b = app.browser
        with self.assertRaises(Exception) as e:
            b.download(self.web.url + "/outil.exe")
        self.assertIn(".exe", str(e.exception))
        r = b.download(self.web.url + "/notice.zip")
        self.assertEqual((r["kind"], r["title"]), ("telechargement", "notice.zip"))
        self.assertTrue((app.home / "telechargements" / "notice.zip").exists())
        with self.assertRaises(Exception):
            b.download(self.web.url + "/notice.zip", by_agent=True)     # un agent ne télécharge pas sans accord
        app.close()

    def test_agent_respects_robots_and_brave_search(self):
        sci = FakeScience()
        sci.brave = [{"title": "Treuils", "url": self.web.url + "/treuil"}]
        app = self.app(endpoints=sci.endpoints())
        b = app.browser
        with self.assertRaises(Exception) as e:
            b.agent_read(self.web.url + "/prive")
        self.assertIn("robots.txt", str(e.exception))
        c = b.agent_read(self.web.url + "/treuil", mission_id=None)
        self.assertIn("500 kg", c["text"])
        self.assertEqual(b.get(c["tab_id"]).owner, "agent")
        app.keys.set("brave", "cle-brave")
        res, notes = b.search("treuil électrique")
        self.assertEqual(res[0]["provider"], "Brave Search")
        self.assertEqual(notes, [])
        sci.stop()
        app.close()

    @unittest.skipUnless(HAS_PW, "Playwright absent")
    def test_chromium_engine_live(self):
        self.web.pages["/clic"] = ("text/html", (
            "<html><head><title>Clic</title></head><body style='margin:0'>"
            "<a href='/autre' style='display:block;width:400px;height:200px;background:#09f'>Aller</a></body></html>").encode())
        app = self.app()
        app.settings.update({"browser_engine": "chromium"})
        b = app.browser
        t = b.open(self.web.url + "/clic", "chromium")
        self.assertEqual((t["engine"], t["title"]), ("chromium", "Clic"))
        frame = b.chromium.frame(t["id"])
        self.assertEqual(frame[:3], b"\xff\xd8\xff")      # image JPEG du rendu réel
        info = b.chromium.input(t["id"], "click", x=100, y=100)
        wait(lambda: "/autre" in b.chromium.info(t["id"])["url"], 10)
        self.assertIn("Seconde page", b.page_content(t["id"])["text"])
        with self.assertRaises(Exception):
            b.chromium.input(t["id"], "key", key="Control+Alt+Delete; rm")
        app.close()


# ============================================================================ images
class Images(Base):
    def setUp(self):
        super().setUp()
        self.gi = FakeImages()

    def tearDown(self):
        self.gi.stop()
        super().tearDown()

    def app(self):
        app = studio_app(self.tmp)
        app.settings.update({"providers": {"gemini": {"base_url": self.gi.url, "enabled": False}}, "image_provider": "gemini"})
        app.keys.set("gemini", "cle-test")
        return app

    def test_cloudflare_free_provider(self):
        from jarvis_core import images as imgmod
        cf = FakeCloudflare()
        app = studio_app(self.tmp)
        self.assertFalse(app.images.available()[0])                      # rien de réglé : indisponible, et c'est dit
        self.assertIn("identifiant de compte", app.images.available()[1])
        app.settings.update({"image_provider": "cloudflare", "image_cf_account": cf.account, "image_cf_base": cf.url})
        app.keys.set("cloudflare", "jeton-cloudflare-de-test-0001")
        out = app.images.generate("une maison moderne au bord d'un lac", "3d", "paysage", "", 2)
        self.assertEqual(len(out["images"]), 2)
        path, ctype, fields, files = cf.calls[0]
        self.assertTrue(path.endswith("/ai/run/@cf/black-forest-labs/flux-2-klein-4b"))
        self.assertTrue(ctype.startswith("multipart/form-data"))
        self.assertEqual((fields["width"], fields["height"]), ("1024", "576"))
        self.assertIn("rendu 3D", fields["prompt"])
        self.assertEqual(out["images"][0]["meta"]["provider"], "cloudflare")
        # modification : l'image d'origine est réduite sous 512 px et envoyée comme input_image_0
        big = app.ws.add_file("image", png((9, 9, 9), 900, 700), "image/png", "grande", saved=True)
        app.images.edit(big["id"], "ajoute un arbre")
        _, _, f2, files2 = cf.calls[-1]
        self.assertIn("input_image_0", files2)
        self.assertLess(max(imgmod.png_size(files2["input_image_0"])), 512)
        # FLUX.1 [schnell] : JSON, et refus clair pour une modification
        app.settings.update({"image_cf_model": "@cf/black-forest-labs/flux-1-schnell"})
        app.images.generate("un logo", "logo", "carré")
        self.assertEqual(cf.calls[-1][2]["prompt"][:8], "un logo\n")
        with self.assertRaises(Exception) as e:
            app.images.edit(big["id"], "ajoute un arbre")
        self.assertIn("FLUX.2", str(e.exception))
        cf.exhausted = True
        with self.assertRaises(Exception) as e:
            app.images.generate("un logo", "logo", "carré")
        self.assertIn("Limite gratuite du jour", str(e.exception))
        app.keys.set("cloudflare", "mauvais-jeton-0000000000")
        cf.exhausted = False
        with self.assertRaises(Exception) as e:
            app.images.generate("un logo", "logo", "carré")
        self.assertIn("Jeton Cloudflare refusé", str(e.exception))
        cf.stop()
        app.close()

    def test_reference_shrink_without_pillow(self):
        from jarvis_core import images as imgmod
        saved = sys.modules.get("PIL")
        sys.modules["PIL"] = None              # simule un PC sans Pillow
        try:
            small = png((1, 2, 3), 300, 200)
            self.assertEqual(imgmod.shrink(small)[0], small)          # déjà assez petite : envoyée telle quelle
            with self.assertRaises(imgmod.ImageError) as e:
                imgmod.shrink(png((1, 2, 3), 900, 700))
            self.assertIn("Installer Pillow", str(e.exception))
            self.assertFalse(imgmod.pillow_installed())
        finally:
            if saved is None:
                sys.modules.pop("PIL", None)
            else:
                sys.modules["PIL"] = saved

    def test_generate_edit_and_gallery(self):
        app = self.app()
        im = app.images
        out = im.generate("Jarvis, génère-moi une maison moderne", "3d", "paysage", "2K", 2)
        self.assertEqual(len(out["images"]), 2)
        body = self.gi.bodies[0]
        self.assertTrue(self.gi.paths[0].endswith("/models/gemini-3.1-flash-image:generateContent"))
        self.assertEqual(body["generationConfig"]["responseFormat"]["image"], {"aspectRatio": "16:9", "imageSize": "2K"})
        self.assertIn("rendu 3D", body["contents"][0]["parts"][0]["text"])
        self.assertNotIn("Jarvis,", body["contents"][0]["parts"][0]["text"])
        r = out["images"][0]
        self.assertEqual((r["kind"], r["saved"], r["meta"]["aspect"]), ("image", 0, "16:9"))
        self.assertEqual(app.ws.resource_bytes(r["id"])[0][:4], b"\x89PNG")
        ed = im.edit(r["id"], "ajoute un arbre dans le jardin")
        eb = self.gi.bodies[-1]["contents"][0]["parts"]
        self.assertIn("inline_data", eb[1])                       # l'image d'origine est envoyée au modèle
        self.assertIn("ajoute un arbre", eb[0]["text"])
        self.assertEqual(ed["images"][0]["meta"]["parent"], r["id"])
        app.ws.update_resource(r["id"], {"saved": True})
        self.assertEqual([x["id"] for x in app.ws.resources("image", saved=True)], [r["id"]])
        up = im.upload(png((200, 10, 10)), "photo.png")
        self.assertEqual(up["meta"]["origin"], "importée")
        with self.assertRaises(Exception):
            im.upload(b"pas une image", "x.png")
        app.close()

    def test_legacy_config_errors_and_missing_key(self):
        app = self.app()
        self.gi.legacy_only = True
        out = app.images.generate("un logo pour mon projet", "logo", "carré")
        self.assertEqual(len(out["images"]), 1)
        self.assertIn("imageConfig", self.gi.bodies[-1]["generationConfig"])   # repli sur l'ancien format
        self.gi.legacy_only = False
        self.gi.block = "IMAGE_SAFETY"
        with self.assertRaises(Exception) as e:
            app.images.generate("une image", "libre", "carré")
        self.assertIn("refusé", str(e.exception))
        self.gi.block = None
        self.gi.fail = (429, "Quota exceeded")
        with self.assertRaises(Exception) as e:
            app.images.generate("une image", "libre", "carré")
        self.assertIn("facturation", str(e.exception))
        app.keys.set("gemini", "")
        self.assertFalse(app.images.available()[0])
        app.close()


# ============================================================================ missions
def llm(log=None, page_quote="Un treuil électrique de 500 kg convient à la plupart des ateliers"):
    log = log if log is not None else []

    def script(b):
        s = blob(b)
        if "Tu crées un ARTEFACT" in s:
            log.append("create")
            imgs = re.findall(r"- jarvis-image:(\d+) —", s)     # liste « Images disponibles » transmise au modèle
            img = f'<img src="jarvis-image:{imgs[0]}" alt="illustration">' if imgs else ""
            return "TITRE: Compteur de levage\nTYPE: html\n```html\n" + HTML_APP.replace("<p id", img + "<p id") + "\n```"
        if "Tu modifies un artefact" in s:
            log.append("edit")
            if "Corrige ces erreurs" in s:
                return "<<<<<<< CHERCHER\nlet n=0; inexistante();\n=======\nlet n=0;\n>>>>>>> REMPLACER"
            return '<<<<<<< CHERCHER\n<h1 id="titre">Compteur</h1>\n=======\n<h1 id="titre">Compteur rouge</h1>\n>>>>>>> REMPLACER'
        if "Tu prépares une recherche web" in s:
            log.append("queries")
            return '{"requetes": ["treuil électrique 500 kg"], "scientifique": false}'
        if "Tu es le chercheur de Jarvis" in s:
            log.append("notes")
            if "500 kg" in s:
                return json.dumps([{"info": "Un treuil de 500 kg suffit en atelier", "citation": page_quote},
                                   {"info": "Inventé", "citation": "phrase qui n'existe pas dans la page du tout"}],
                                  ensure_ascii=False)
            return "[]"
        if "Tu es le vérificateur de Jarvis" in s:
            log.append("compare")
            return '{"points": [{"info": "500 kg suffit", "sources": [1], "statut": "confirmée"}]}'
        if "Tu planifies une mission" in s:
            log.append("plan")
            return json.dumps({"objectif": "Application sur les treuils", "etapes": [
                {"outil": "web.research", "consigne": "Trouver les caractéristiques des treuils électriques"},
                {"outil": "image.generate", "consigne": "Illustration d'un treuil électrique d'atelier"},
                {"outil": "artifact.create", "consigne": "Application présentant les treuils avec l'illustration"}]},
                ensure_ascii=False)
        if "Tes agents viennent d'exécuter une mission" in s:
            log.append("synth")
            return "Voici la synthèse : un treuil de 500 kg suffit [W1]. Source fantôme [W7]."
        if "Classe la demande" in s:
            log.append("intent")
            return '{"image": true, "modifier_image": false, "artefact": false, "modifier_artefact": false, "web": false}'
        log.append("chat")
        return "Réponse simple."
    return script


class Missions(Base):
    def setUp(self):
        super().setUp()
        self.gi = FakeImages()
        self.web = FakeWeb()
        self.web.pages["/treuil"] = ("text/html; charset=utf-8", PAGE.encode())
        self.sci = FakeScience()
        self.sci.brave = [{"title": "Treuils", "url": self.web.url + "/treuil"}]

    def tearDown(self):
        self.gi.stop()
        self.web.stop()
        self.sci.stop()
        super().tearDown()

    def app(self, log=None, **settings):
        f = self.fake("anthropic", llm(log))
        app = studio_app(self.tmp, {"anthropic": f}, {"allow_private": True, "endpoints": self.sci.endpoints(),
                                                      "wikipedia": Wikipedia(self.web.url + "/w/api.php", True)},
                         default_model="anthropic:modele-a", browser_engine="lecture", **settings)
        app.settings.update({"providers": {"gemini": {"base_url": self.gi.url, "enabled": False}}, "image_provider": "gemini"})
        app.keys.set("gemini", "cle-test")
        app.keys.set("brave", "cle-brave")
        return app

    def chat(self, app, cid, text, **opts):
        return list(app.brain.chat(cid, text, "auto", None, opts))

    def test_intent_detection(self):
        for text, want in [
            ("Jarvis, génère-moi une maison moderne en 3D.", None),               # ambigu : classé par le modèle
            ("Crée un logo pour mon projet.", ["image"]),
            ("Transforme cette pièce mécanique en rendu réaliste.", ["image"]),
            ("Fais-moi une illustration technique de ce mécanisme.", ["image"]),
            ("Crée-moi une application présentant les dernières technologies automobiles.", ["artifact", "web"]),
            ("Fais-moi une simulation d'un pendule", ["artifact"]),
            ("Compare les prix des imprimantes 3D", ["web"]),
            ("Quelle est la formule du moment quadratique ?", []),
            ("Je suis passé à SolidWorks 2025", []),
            ("Explique-moi la RDM", []),
        ]:
            with self.subTest(text):
                needs, sure, _ = detect(text)
                if want is None:
                    self.assertFalse(sure)
                else:
                    self.assertEqual(sorted(needs), sorted(want))
        self.assertEqual(detect("Change la couleur du bouton en rouge", open_artifact="Compteur")[0], ["artifact_edit"])
        self.assertEqual(detect("Modifie cette image en ajoutant un autre personnage", last_image=3)[0], ["image_edit"])
        self.assertEqual(detect("Crée une application avec des illustrations de voitures")[0], ["image", "artifact"])

    def test_image_request_in_chat(self):
        log = []
        app = self.app(log)
        c = app.store.create_conversation()
        ev = self.chat(app, c["id"], "Jarvis, génère-moi une maison moderne en 3D.")
        self.assertIn("intent", log)                                   # ambigu : le modèle a tranché
        done = [e for e in ev if e["type"] == "done"][0]
        self.assertEqual(len(done["meta"]["images"]), 1)
        self.assertIn("Enregistrer", done["content"])
        kinds = [e.get("kind") for e in ev if e["type"] == "mission"]
        self.assertEqual(kinds[0], "plan")
        self.assertIn("done", kinds)
        conv = app.store.get_conversation(c["id"])
        self.assertEqual(conv["messages"][-1]["meta"]["images"], done["meta"]["images"])
        # « modifie cette image » : l'image précédente est envoyée au générateur
        ev2 = self.chat(app, c["id"], "Modifie cette image en ajoutant un autre personnage")
        d2 = [e for e in ev2 if e["type"] == "done"][0]
        self.assertEqual(app.ws.resource(d2["meta"]["images"][0])["meta"]["parent"], done["meta"]["images"][0])
        app.close()

    def test_artifact_create_verify_and_edit_from_chat(self):
        log = []
        app = self.app(log, mission_runtime_check=HAS_PW)
        c = app.store.create_conversation()
        ev = self.chat(app, c["id"], "Crée-moi un outil compteur pour l'atelier")
        done = [e for e in ev if e["type"] == "done"][0]
        aid = done["meta"]["artifacts"][0]
        a = app.artifacts.get(aid)
        self.assertEqual((a["title"], a["kind"], a["conv_id"]), ("Compteur de levage", "html", c["id"]))
        steps = done["meta"]["mission"]["steps"]
        self.assertEqual([s["label"] for s in steps], ["Création d'artefact", "Contrôle de l'artefact"])
        self.assertTrue(steps[1]["summary"].startswith("Contrôle réussi"), steps[1])
        if HAS_PW:
            self.assertIn("navigateur isolé", steps[1]["summary"])
        # modification dans la conversation, l'artefact étant ouvert à droite
        ev = self.chat(app, c["id"], "Change le titre en rouge", artifact_id=aid)
        a2 = app.artifacts.get(aid)
        self.assertEqual(a2["version"], 2)
        self.assertIn("Compteur rouge", a2["content"])
        self.assertTrue(any(e.get("kind") == "artifact" for e in ev if e["type"] == "mission"))
        # un élément sélectionné dans l'aperçu impose la modification de cet artefact
        sel = {"selector": "#plus", "html": '<button id="plus">Ajouter</button>'}
        self.chat(app, c["id"], "Rends ce bouton plus grand", artifact_id=aid, selection=sel)
        last = [b for b in self.fakes[0].requests if b[0] == "POST"][-1][3]
        self.assertIn("#plus", blob(last))
        app.close()

    @unittest.skipUnless(HAS_PW, "Playwright absent")
    def test_verifier_fixes_runtime_error(self):
        app = self.app()
        a = app.artifacts.create("Cassé", "html", BROKEN_APP)
        mid = app.missions.launch("Contrôle", needs=["artifact_edit"], steps=[{"tool": "artifact.verify"}],
                                  artifact_id=a["id"])
        wait(lambda: app.missions.get(mid)["status"] not in ("en cours", "en attente"), 90)
        m = app.missions.get(mid)
        st = m["plan"]["steps"][0]
        self.assertEqual(st["result"]["data"]["fixes"], 1)
        self.assertTrue(st["result"]["data"]["ok"], st)
        self.assertNotIn("inexistante", app.artifacts.get(a["id"])["content"])
        app.close()

    def test_web_research_mission_with_verified_quotes(self):
        log = []
        app = self.app(log)
        c = app.store.create_conversation()
        ev = self.chat(app, c["id"], "Cherche sur internet quelle capacité de treuil choisir pour un atelier")
        done = [e for e in ev if e["type"] == "done"][0]
        src = done["meta"]["web_sources"]
        self.assertEqual(src[0]["url"], self.web.url + "/treuil")
        self.assertEqual(src[0]["quotes"], 1)                # la citation inventée a été écartée
        self.assertIn("[W1]", done["content"])
        self.assertIn("[W7] sans source", done["content"])  # citation fantôme signalée par le contrôle
        step = app.missions.get(done["meta"]["mission_id"])["plan"]["steps"][0]
        pts = step["result"]["data"]["points"]
        self.assertEqual(pts[0]["statut"], "source unique")  # « confirmée » refusé : une seule source
        self.assertEqual(app.ws.resources("source")[0]["url"], self.web.url + "/treuil")
        tabs = app.browser.list()
        self.assertTrue(any(t["owner"] == "agent" for t in tabs))   # l'agent a navigué dans des onglets visibles
        log_actions = [r["action"] for r in app.ws.agent_log(done["meta"]["mission_id"])]
        self.assertTrue(any(a.startswith("Lecture") for a in log_actions))
        app.close()

    def test_multi_tool_mission_passes_results_between_agents(self):
        log = []
        app = self.app(log, mission_runtime_check=False)
        pid = app.ws.ensure_project("Treuil")
        c = app.store.create_conversation()
        app.ws.set_conversation_project(c["id"], pid)
        ev = self.chat(app, c["id"], "Crée-moi une application présentant les dernières technologies de treuils "
                                     "avec des illustrations")
        self.assertIn("plan", log)
        done = [e for e in ev if e["type"] == "done"][0]
        labels = [s["label"] for s in done["meta"]["mission"]["steps"]]
        self.assertEqual(labels, ["Recherche web vérifiée", "Génération d'image", "Création d'artefact",
                                  "Contrôle de l'artefact"])
        aid = done["meta"]["artifacts"][0]
        img = done["meta"]["images"][0]
        a = app.artifacts.get(aid)
        self.assertIn(f"jarvis-image:{img}", a["content"])            # l'image du concepteur est dans l'artefact
        create_body = [blob(r[3]) for r in self.fakes[0].requests if r[0] == "POST" and "Tu crées un ARTEFACT" in blob(r[3])][0]
        self.assertIn("500 kg", create_body)                          # les résultats du chercheur aussi
        d = app.ws.project_detail(pid)
        self.assertEqual((d["counts"]["artifacts"], d["counts"]["images"], d["counts"]["sources"]), (1, 1, 1))
        self.assertEqual(app.missions.list(project_id=pid)[0]["progress"], 100)
        app.close()

    def test_sensitive_action_waits_for_approval_and_resume(self):
        app = self.app()
        self.web.pages["/notice.zip"] = ("application/zip", b"PK\x03\x04contenu")
        mid = app.missions.launch("Télécharger la notice", needs=["web"],
                                  steps=[{"tool": "web.download", "args": {"url": self.web.url + "/notice.zip"}}])
        wait(lambda: app.missions.get(mid)["status"] == "en attente")
        st = app.missions.get(mid)["plan"]["steps"][0]
        self.assertEqual(st["state"], "autorisation")
        self.assertFalse((app.home / "telechargements" / "notice.zip").exists())
        app.missions.decide(mid, "autoriser")
        wait(lambda: app.missions.get(mid)["status"] == "terminée")
        self.assertTrue((app.home / "telechargements" / "notice.zip").exists())
        # refus : l'étape n'est pas exécutée
        mid2 = app.missions.launch("Télécharger", needs=["web"],
                                   steps=[{"tool": "web.download", "args": {"url": self.web.url + "/notice.zip"}}])
        wait(lambda: app.missions.get(mid2)["status"] == "en attente")
        app.missions.decide(mid2, "refuser")
        wait(lambda: app.missions.get(mid2)["status"] == "terminée")
        self.assertEqual(app.missions.get(mid2)["plan"]["steps"][0]["state"], "refusée")
        # échec puis reprise à partir de l'étape en échec
        self.gi.fail = (500, "panne")
        mid3 = app.missions.launch("Image", needs=["image"], steps=[{"tool": "image.generate", "consigne": "un treuil"}])
        wait(lambda: app.missions.get(mid3)["status"] == "échec", 30)
        self.gi.fail = None
        app.missions.resume(mid3)
        wait(lambda: app.missions.get(mid3)["status"] == "terminée", 30)
        self.assertEqual(len(app.ws.resources("image", mission_id=mid3)), 1)
        app.close()

    def test_plain_question_stays_a_normal_answer(self):
        log = []
        app = self.app(log)
        c = app.store.create_conversation()
        ev = self.chat(app, c["id"], "Quelle est la formule du moment quadratique d'une section rectangulaire ?")
        self.assertFalse([e for e in ev if e["type"] == "mission"])
        self.assertEqual(text_of(ev), "Réponse simple.")
        self.assertNotIn("intent", log)            # aucun appel de classement pour une question ordinaire
        app.settings.update({"mission_auto_tools": False})
        ev = self.chat(app, c["id"], "Crée un logo pour mon projet.")
        self.assertFalse([e for e in ev if e["type"] == "mission"])
        app.close()


# ============================================================================ HTTP
class Http(Base):
    def test_sandbox_media_events_and_routes(self):
        app = studio_app(self.tmp)
        httpd = serve(app, 0)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        H = {"X-Jarvis-Token": app.token, "Content-Type": "application/json"}
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def call(method, path, body=None, headers=H):
            req = urllib.request.Request(base + path, json.dumps(body).encode() if body is not None else None,
                                         headers, method=method)
            try:
                with op.open(req, timeout=20) as r:
                    return r.status, r.headers, r.read()
            except urllib.error.HTTPError as e:
                return e.code, e.headers, e.read()
        st, _, raw = call("POST", "/api/artifacts", {"title": "Compteur", "kind": "html", "content": HTML_APP})
        a = json.loads(raw)
        st, _, raw = call("GET", f"/api/artifacts/{a['id']}")
        url = json.loads(raw)["sandbox_url"]
        st, hd, body = call("GET", url, headers={})
        self.assertEqual(st, 200)
        self.assertIn("sandbox allow-scripts", hd["Content-Security-Policy"])
        self.assertIn("connect-src 'none'", hd["Content-Security-Policy"])
        self.assertNotIn("allow-same-origin", hd["Content-Security-Policy"])
        self.assertEqual(call("GET", url.replace("s=", "s=x"), headers={})[0], 403)
        r = app.ws.add_file("image", png(), "image/png", "img")
        st, hd, body = call("GET", r["src"], headers={})
        self.assertEqual((st, hd["Content-Type"], body[:4]), (200, "image/png", b"\x89PNG"))
        self.assertEqual(call("GET", f"/media/r/{r['id']}?s=faux", headers={})[0], 403)
        self.assertEqual(call("GET", "/api/projects", headers={})[0], 401)
        st, _, raw = call("POST", "/api/projects", {"name": "Robot", "objective": "Bras SCARA"})
        pid = json.loads(raw)["id"]
        st, _, raw = call("PATCH", f"/api/artifacts/{a['id']}", {"project_id": pid, "content": HTML_APP + "<!-- v2 -->"})
        self.assertEqual(json.loads(raw)["version"], 2)
        st, _, raw = call("GET", f"/api/projects/{pid}")
        self.assertEqual(json.loads(raw)["counts"]["artifacts"], 1)
        st, hd, raw = call("GET", f"/api/artifacts/{a['id']}/export?format=html")
        self.assertIn("attachment", hd["Content-Disposition"])
        st, hd, raw = call("GET", f"/api/projects/{pid}/export")
        self.assertEqual(raw[:2], b"PK")
        # flux d'événements : la création d'un artefact arrive aux abonnés
        got = []

        def listen():
            req = urllib.request.Request(base + "/api/events?since=0", headers=H)
            with op.open(req, timeout=20) as resp:
                for line in resp:
                    if line.startswith(b"data:") and b'"artifact"' in line:
                        got.append(json.loads(line[5:]))
                        return
        th = threading.Thread(target=listen, daemon=True)
        th.start()
        th.join(10)
        self.assertTrue(got and got[0]["topic"] == "artifact")
        st, _, raw = call("GET", "/api/studio")
        self.assertIn("artifact.create", [t["name"] for t in json.loads(raw)["tools"]])
        httpd.shutdown()
        httpd.server_close()
        app.close()


if __name__ == "__main__":
    unittest.main()
