"""Tests de la base de connaissances. Lancer :  python -m unittest discover -s tests -v"""
import base64
import io
import json
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes import Fake, FakeWeb  # noqa: E402
from jarvis_core import extract  # noqa: E402
from jarvis_core.collect import CollectError, Wikipedia, fetch  # noqa: E402
from jarvis_core.knowledge import Duplicate, Knowledge  # noqa: E402
from jarvis_core.providers import REGISTRY  # noqa: E402
from jarvis_core.server import App, serve  # noqa: E402

ACIER = ("L'acier est un alliage de fer et de carbone. Sa résistance à la traction se mesure par un essai de "
         "traction normalisé. Le module de Young de l'acier vaut environ 210 GPa.")
ALU = ("L'aluminium est un métal léger. Son module de Young vaut environ 69 GPa. Il résiste bien à la corrosion "
       "grâce à une couche d'alumine.")
CUISINE = "La pâte à crêpes se prépare avec de la farine, des œufs et du lait. Laisser reposer une heure."


def blob(b):
    return json.dumps(b, ensure_ascii=False)


def llm_script(b):
    s = blob(b)
    if "Classe ce document" in s:
        return "Matériaux et métallurgie" if "acier" in s.lower() or "aluminium" in s.lower() else "Cuisine et recettes"
    if "Relève les relations" in s:
        return json.dumps([
            {"source": "acier", "type_source": "matériau", "relation": "a pour propriété",
             "cible": "résistance à la traction", "type_cible": "concept"},
            {"source": "résistance à la traction", "relation": "est mesurée par", "cible": "essai de traction"},
            {"source": "acier", "relation": "est produit par", "cible": "licorne magique"},  # absent du texte
        ], ensure_ascii=False)
    if "Tu gères la mémoire" in s:
        return "[]"
    return "Le module de Young de l'acier vaut environ 210 GPa [S1]."


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="jarvis-kb-"))
        self.stops = []

    def tearDown(self):
        for s in self.stops:
            s.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def app(self, with_llm=True, **settings):
        self.web = FakeWeb()
        self.llm = Fake("anthropic", llm_script)
        self.stops += [self.web, self.llm]
        app = App(self.tmp, {"wikipedia": Wikipedia(self.web.url + "/w/api.php", True), "allow_private": True})
        provs = {pid: {"enabled": pid == "anthropic" and with_llm, "base_url": self.llm.url} for pid in REGISTRY}
        app.settings.update({"providers": provs, "memory_auto_extract": False, **settings})
        app.keys.set("anthropic", "cle-test")
        app.brain.refresh_models()
        return app

    def done(self, app):
        self.assertTrue(app.kbs.wait(30), "la file d'indexation ne s'est pas vidée")
        return app.kb.jobs(1)[0]


class Extraction(unittest.TestCase):
    def test_text_csv_html(self):
        _, s = extract.segments("note.txt", "Ligne é\r\n\r\n\r\n\r\nsuite".encode("cp1252"))
        self.assertEqual(s[0][1], "Ligne é\n\nsuite")
        _, s = extract.segments("t.csv", "nom;rm\nS235;360\nS355;510\n".encode())
        self.assertIn("S355 | 510", s[0][1])
        title, s = extract.segments("p.html", b"<html><head><title>Le titre</title><script>alert(1)</script></head>"
                                    b"<body><nav>menu</nav><h1>Acier</h1><p>Un &eacute;l&eacute;ment.</p></body></html>")
        self.assertEqual(title, "Le titre")
        self.assertIn("# Acier", s[0][1])
        self.assertIn("Un élément.", s[0][1])
        self.assertNotIn("alert", s[0][1])
        self.assertNotIn("menu", s[0][1])

    def test_docx_with_table(self):
        import docx
        d = docx.Document()
        d.add_heading("Guidage du tambour", 1)
        d.add_paragraph("Le tambour est guidé par deux roulements 6204.")
        t = d.add_table(rows=2, cols=2)
        t.cell(0, 0).text, t.cell(0, 1).text, t.cell(1, 0).text, t.cell(1, 1).text = "Repère", "Désignation", "17", "Tambour"
        buf = io.BytesIO()
        d.save(buf)
        _, s = extract.segments("treuil.docx", buf.getvalue())
        self.assertIn("# Guidage du tambour", s[0][1])
        self.assertIn("roulements 6204", s[0][1])
        self.assertIn("17 | Tambour", s[0][1])

    def test_xlsx_sheets_and_formulas(self):
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Cordages"
        ws.append(["Modèle", "Jauge", "Prix"])
        ws.append(["BG65", 0.70, 8])
        ws["D2"] = "=B2*C2"
        wb.create_sheet("Notes").append(["Tension conseillée 11 kg"])
        buf = io.BytesIO()
        wb.save(buf)
        _, s = extract.segments("c.xlsx", buf.getvalue())
        self.assertEqual([loc for loc, _ in s], ["feuille « Cordages »", "feuille « Notes »"])
        self.assertIn("BG65 | 0.7 | 8", s[0][1])
        self.assertIn("= B2*C2", s[0][1])

    def test_pdf_pages(self):
        from reportlab.pdfgen import canvas
        buf = io.BytesIO()
        c = canvas.Canvas(buf)
        c.drawString(72, 720, "Page un : resistance des materiaux")
        c.showPage()
        c.drawString(72, 720, "Page deux : le flambage des poutres")
        c.save()
        _, s = extract.segments("cours.pdf", buf.getvalue())
        self.assertEqual([loc for loc, _ in s], ["p. 1", "p. 2"])
        self.assertIn("flambage", s[1][1])

    def test_hostile_or_unsupported_files(self):
        for name, data in [("x.exe", b"MZ\x00\x00"), ("x.docx", b"pas un zip"), ("vide.txt", b"   "),
                           ("bin.txt", b"\x00\x01\x02" * 50)]:
            with self.assertRaises(extract.ExtractError, msg=name):
                extract.segments(name, data)
        z = io.BytesIO()
        with zipfile.ZipFile(z, "w") as f:
            f.writestr("word/document.xml", '<!DOCTYPE x [<!ENTITY a "aaaa">]><w>&a;</w>')
        with self.assertRaises(extract.ExtractError):
            extract.segments("bombe.docx", z.getvalue())
        z = io.BytesIO()
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as f:
            f.writestr("word/document.xml", b"0" * (210 * 1024 * 1024))
        with self.assertRaises(extract.ExtractError) as cm:
            extract.segments("gros.docx", z.getvalue())
        self.assertIn("volumineux", str(cm.exception))


class Store(Base):
    def setUp(self):
        super().setUp()
        self.kb = Knowledge(self.tmp / "k.db")
        self.cat = self.kb.find_category("Matériaux et métallurgie")
        self.a = self.kb.ingest("Acier", [("p. 1", ACIER)], "fichier", "acier.pdf", self.cat, 4)
        self.b = self.kb.ingest("Aluminium", [("", ALU)], "fichier", "alu.txt", self.cat)
        self.c = self.kb.ingest("Crêpes", [("", CUISINE)], "fichier", "crepes.txt", self.kb.find_category("Cuisine et recettes"))

    def tearDown(self):
        self.kb.close()
        super().tearDown()

    def test_taxonomy_seeded(self):
        cats = self.kb.categories()
        self.assertEqual(len([c for c in cats if c["parent_id"] is None]), 6)
        self.assertEqual(self.kb.category_path(self.cat), "Sciences fondamentales › Matériaux et métallurgie")

    def test_search_ranks_and_filters(self):
        r = self.kb.search("Quel est le module de Young de l'acier ?")
        self.assertEqual(r[0]["title"], "Acier")
        self.assertEqual(r[0]["locator"], "p. 1")
        self.assertEqual({x["title"] for x in r}, {"Acier", "Aluminium"})
        self.assertEqual(self.kb.search("Quelle est la capitale du Pérou ?"), [])
        self.assertEqual(self.kb.search("coucou"), [])
        self.assertEqual(self.kb.search("aciers")[0]["title"], "Acier")          # pluriel
        self.assertEqual(self.kb.search("farine oeufs")[0]["title"], "Crêpes")   # sans accents ni ligature
        only = self.kb.search("module de Young", category_id=self.kb.find_category("Cuisine et recettes"))
        self.assertEqual(only, [])
        top = self.kb.find_category("Sciences fondamentales")
        self.assertEqual(len(self.kb.search("module de Young", category_id=top)), 2)  # catégorie parente

    def test_duplicates(self):
        with self.assertRaises(Duplicate):
            self.kb.ingest("Acier bis", [("", "  " + ACIER.replace(" ", "  "))], "fichier", "copie.txt")
        near = self.kb.ingest("Acier (variante)", [("", ACIER + " Fin.")], "fichier", "v.txt")
        self.assertEqual(self.kb.get(near)["dup_of"], self.a)
        self.assertEqual(len(self.kb.list(dups=True)), 1)
        self.kb.update(near, {"not_duplicate": True})
        self.assertEqual(self.kb.list(dups=True), [])

    def test_versions_and_journal(self):
        self.assertFalse(self.kb.reingest(self.a, [("p. 1", ACIER)])["changed"])
        res = self.kb.reingest(self.a, [("", ACIER.replace("210", "205"))])
        self.assertEqual(res, {"changed": True, "version": 2})
        self.assertIn("205 GPa", self.kb.search("module de Young acier")[0]["content"])
        v = self.kb.versions(self.a)
        self.assertEqual(len(v), 1)
        self.assertIn("210 GPa", self.kb.version_text(v[0]["id"]))
        self.kb.update(self.a, {"title": "Acier (fiche)", "reliability": 5})
        self.assertEqual(self.kb.search("fiche acier")[0]["title"], "Acier (fiche)")
        self.kb.delete(self.c)
        self.assertEqual(self.kb.search("farine"), [])
        actions = [j["action"] for j in self.kb.journal()]
        for a in ("import", "vérifié", "mis à jour", "modifié", "supprimé"):
            self.assertIn(a, actions)

    def test_graph(self):
        n = self.kb.add_relations([("Acier", "a pour propriété", "Résistance à la traction", "matériau"),
                                   ("résistance à la traction", "est mesurée par", "essai de traction"),
                                   ("acier", "a pour propriété", "résistance à la traction"),  # doublon
                                   ("x", "", "y"), ("a", "r", "a")], self.a)
        self.assertEqual(n, 2)
        rel = self.kb.related("Comment mesure-t-on la résistance à la traction ?")
        self.assertEqual({(r["src"], r["dst"]) for r in rel},
                         {("Acier", "Résistance à la traction"), ("Résistance à la traction", "essai de traction")})
        self.assertEqual(self.kb.related("Parle-moi des crêpes"), [])
        g = self.kb.graph("acier")
        self.assertEqual(len(g["nodes"]), 2)
        self.assertEqual(len(self.kb.graph()["nodes"]), 3)
        self.kb.delete(self.a)
        self.assertEqual(self.kb.stats()["relations"], 0)
        self.assertEqual(self.kb.stats()["entities"], 0)

    def test_gaps(self):
        self.kb.note_gap("Comment calculer une soudure d'angle ?")
        self.kb.note_gap("comment calculer une soudure d'angle")
        g = self.kb.gaps()
        self.assertEqual((len(g), g[0]["count"]), (1, 2))
        self.kb.set_gap(g[0]["id"], "ignoré")
        self.kb.note_gap("Comment calculer une soudure d'angle ?")
        self.assertEqual(self.kb.gaps(), [])

    def test_search_stays_fast_with_thousands_of_passages(self):
        import random
        rnd = random.Random(1)
        words = [f"mot{i}" for i in range(4000)]
        segs = [("", " ".join(rnd.choice(words) for _ in range(140))) for _ in range(200)]
        for i in range(15):   # 15 documents × 200 passages = 3000 passages
            self.kb.ingest(f"Doc {i}", [(l, t + f" lot{i}") for l, t in segs], "fichier", f"d{i}.txt")
        self.assertGreaterEqual(self.kb.stats()["chunks"], 3000)
        t = time.time()
        r = self.kb.search("module de Young de l'acier")
        took = time.time() - t
        print(f"\n    recherche parmi {self.kb.stats()['chunks']} passages : {took * 1000:.0f} ms", end=" ")
        self.assertEqual(r[0]["title"], "Acier")
        self.assertLess(took, 5)


class Service(Base):
    def test_file_import_classifies_and_builds_graph(self):
        app = self.app()
        app.kbs.submit_file("acier.txt", ACIER.encode())
        job = self.done(app)
        self.assertEqual(job["status"], "terminé", job)
        d = app.kb.list()[0]
        self.assertEqual(d["category"], "Sciences fondamentales › Matériaux et métallurgie")
        self.assertTrue((app.kbs.dir / "originals" / d["original"]).exists())
        self.assertEqual(list((app.kbs.dir / "incoming").iterdir()), [])
        rel = {(r["src"], r["label"], r["dst"]) for r in app.kb.related("acier")}
        self.assertIn(("acier", "a pour propriété", "résistance à la traction"), rel)
        self.assertFalse(any("licorne" in r[2] for r in rel), "une relation absente du texte a été gardée")
        app.kbs.submit_file("copie.txt", ACIER.encode())
        self.assertEqual(self.done(app)["status"], "doublon")
        app.kbs.submit_file("x.docx", b"fichier casse")
        job = self.done(app)
        self.assertEqual(job["status"], "erreur")
        self.assertIn("Office", job["detail"])
        self.assertEqual(app.kb.stats()["documents"], 1)
        app.close()

    def test_works_without_any_model(self):
        app = self.app(with_llm=False)
        app.kbs.submit_file("alu.txt", ALU.encode())
        job = self.done(app)
        self.assertEqual(job["status"], "terminé")
        self.assertIn("catégorie non déterminée", job["detail"])
        self.assertIn("aucun modèle", job["detail"])
        self.assertEqual(app.kb.search("module de Young aluminium")[0]["title"], "alu")
        app.close()

    def test_web_page_and_refresh(self):
        app = self.app(kb_graph_auto=False, kb_auto_categorize=False)
        self.web.pages["/doc"] = ("text/html", f"<html><title>Fiche acier</title><body><p>{ACIER}</p></body></html>".encode())
        app.kbs.submit_url(self.web.url + "/doc")
        self.assertEqual(self.done(app)["status"], "terminé")
        d = app.kb.list()[0]
        self.assertEqual((d["title"], d["source_type"], d["source"]), ("Fiche acier", "page web", self.web.url + "/doc"))
        app.kbs.submit_refresh(d["id"])
        self.assertIn("inchangée", self.done(app)["detail"])
        self.web.pages["/doc"] = ("text/html", f"<html><title>Fiche acier</title><body><p>{ACIER} Mise à jour 2026.</p></body></html>".encode())
        app.kbs.submit_refresh(d["id"])
        self.assertIn("version 2", self.done(app)["detail"])
        self.assertEqual(len(app.kb.versions(d["id"])), 1)
        app.kbs.submit_url(self.web.url + "/absent")
        self.assertIn("404", self.done(app)["detail"])
        app.close()

    def test_local_network_addresses_are_refused(self):
        for url in ("http://127.0.0.1:8765/api/state", "http://localhost/", "http://192.168.1.1/", "http://[::1]/",
                    "file:///etc/passwd", "ftp://example.org/x"):
            with self.assertRaises(CollectError, msg=url):
                fetch(url)
        web = FakeWeb()
        self.stops.append(web)
        with self.assertRaises(CollectError):   # redirection vers une adresse locale, même en mode test strict
            urllib.request.build_opener  # noqa
            from jarvis_core.collect import _Redirects
            _Redirects(False).redirect_request(None, None, 302, "", {}, "http://127.0.0.1:1/secret")

    def test_wikipedia_import_proposals_and_validation(self):
        app = self.app(kb_graph_auto=False, kb_auto_categorize=False)
        self.web.articles = {"Soudage": "Le soudage est un procédé d'assemblage permanent.\n\n== Procédés ==\nTIG, MIG.",
                             "Flambage": "Le flambage est une instabilité d'une poutre comprimée."}
        self.assertEqual([r["title"] for r in app.kbs.wiki.search("soudage")], ["Soudage"])
        app.kbs.submit_wikipedia("Soudage")
        self.assertEqual(self.done(app)["status"], "terminé")
        d = app.kb.list()[0]
        self.assertEqual((d["source_type"], d["published"], d["reliability"]), ("wikipédia", "2026-05-01", 3))
        self.assertIn("CC BY-SA", d["license"])
        self.assertIn("# Procédés", app.kb.chunks_of(d["id"])[0]["content"])
        app.kbs.submit_wikipedia("Article qui n'existe pas")
        self.assertIn("introuvable", self.done(app)["detail"])
        # question sans source -> proposition -> validation humaine -> import
        app.kb.note_gap("Comment calculer le flambage d'une poutre ?")
        res = app.kbs.suggest(max_searches=1)
        self.assertEqual(res["auto_imported"], 0)
        props = app.kb.proposals()
        self.assertEqual([p["title"] for p in props], ["Flambage"])
        self.assertIn("question restée sans source", props[0]["reason"])
        self.assertEqual(app.kb.stats()["documents"], 1)       # rien n'est importé sans accord
        app.kbs.accept(props[0]["id"])
        self.assertEqual(self.done(app)["status"], "terminé")
        self.assertEqual(app.kb.stats()["documents"], 2)
        self.assertEqual(app.kb.proposals(), [])
        self.assertTrue(any(t["imported"] for g in app.kbs.starter() for t in g["titles"] if t["title"] == "Soudage"))
        app.close()


class Rag(Base):
    def chat(self, app, text):
        c = app.store.create_conversation()
        ev = list(app.brain.chat(c["id"], text))
        bodies = [r[3] for r in self.llm.requests if r[0] == "POST" and "system" in r[3]]
        return ev, bodies[-1]["system"], app.store.get_conversation(c["id"])

    def test_answer_is_grounded_and_cites_passage(self):
        app = self.app(kb_graph_auto=False, kb_auto_categorize=False)
        a = app.kb.ingest("Cours RDM", [("p. 12", ACIER)], "fichier", "rdm.pdf", reliability=4)
        app.kb.ingest("Crêpes", [("", CUISINE)], "fichier", "c.txt")
        app.kb.add_relations([("acier", "a pour propriété", "module de Young")], a)
        app.store.add_memory("L'utilisateur prépare un projet de treuil en acier", "projets", 3)
        ev, system, conv = self.chat(app, "Quel est le module de Young de l'acier ?")
        self.assertIn("[S1] « Cours RDM » — rdm.pdf, p. 12 — fiabilité 4/5", system)
        self.assertIn("210 GPa", system)
        self.assertNotIn("crêpes", system.lower())
        self.assertIn("acier —[a pour propriété]→ module de Young", system)
        self.assertIn("projet de treuil", system)                 # mémoire personnelle interrogée en même temps
        meta = [e for e in ev if e["type"] == "meta"][0]
        self.assertEqual((meta["sources"][0]["title"], meta["sources"][0]["locator"]), ("Cours RDM", "p. 12"))
        self.assertNotIn("content", meta["sources"][0])
        msg = conv["messages"][-1]
        self.assertIn("[S1]", msg["content"])
        self.assertEqual(msg["meta"]["sources"][0]["doc_id"], a)
        self.assertEqual(app.kb.gaps(), [])
        app.close()

    def test_missing_knowledge_is_flagged_and_logged(self):
        app = self.app(kb_graph_auto=False, kb_auto_categorize=False)
        app.kb.ingest("Crêpes", [("", CUISINE)], "fichier", "c.txt")
        ev, system, _ = self.chat(app, "Explique en détail le calcul d'une soudure d'angle selon l'Eurocode")
        self.assertIn("ne contient aucun passage", system)
        self.assertNotIn("<connaissances>", system)
        self.assertTrue([e for e in ev if e["type"] == "meta"][0]["kb_miss"])
        self.assertEqual(len(app.kb.gaps()), 1)
        ev, system, _ = self.chat(app, "coucou")
        self.assertNotIn("base de connaissances", system)
        self.assertEqual(len(app.kb.gaps()), 1)
        app.settings.update({"kb_enabled": False})
        ev, system, _ = self.chat(app, "Comment faire une pâte à crêpes avec de la farine ?")
        self.assertNotIn("<connaissances>", system)
        app.close()

    def test_empty_answer_is_reported(self):
        app = self.app()
        self.llm.script = lambda b: ""
        ev, _, conv = self.chat(app, "Bonjour")
        self.assertEqual(ev[-1]["type"], "error")
        self.assertIn("aucun texte", ev[-1]["message"])
        app.close()


class Api(Base):
    def setUp(self):
        super().setUp()
        self.a = self.app(kb_graph_auto=False, kb_auto_categorize=False)
        self.httpd = serve(self.a, 0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.a.close()
        super().tearDown()

    def call(self, method, path, body=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method,
                                     headers={"Content-Type": "application/json", "X-Jarvis-Token": self.a.token},
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=20) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_http_flow(self):
        code, _ = self.call("POST", "/api/kb/import/file", {"name": "acier.txt", "data": base64.b64encode(ACIER.encode()).decode(),
                                                             "reliability": 4, "license": "cours personnel"})
        self.assertEqual(code, 200)
        self.done(self.a)
        self.assertEqual(self.call("POST", "/api/kb/import/file", {"name": "x.exe", "data": "TVo="})[0], 400)
        self.assertEqual(self.call("POST", "/api/kb/import/file", {"name": "x.txt", "data": "%%%"})[0], 400)
        self.assertEqual(self.call("POST", "/api/kb/import/url", {"url": "file:///etc/passwd"})[0], 400)
        st = self.call("GET", "/api/kb/state")[1]
        self.assertEqual((st["stats"]["documents"], st["pdf_ready"]), (1, True))
        doc = self.call("GET", "/api/kb/documents")[1][0]
        self.assertEqual((doc["reliability"], doc["license"]), (4, "cours personnel"))
        full = self.call("GET", f"/api/kb/documents/{doc['id']}")[1]
        self.assertEqual(full["chunks_total"], 1)
        self.assertEqual(self.call("GET", "/api/kb/search?q=module%20young")[1][0]["title"], "acier")
        self.a.store.add_memory("L'utilisateur étudie le module de Young en RDM")
        g = self.call("GET", "/api/global-search?q=module%20de%20Young")[1]
        self.assertEqual((len(g["knowledge"]), len(g["memories"])), (1, 1))
        cid = self.call("POST", "/api/kb/categories", {"name": "Treuil Huchez", "parent_id": self.a.kb.find_category("Mes documents")})[1]["id"]
        self.assertEqual(self.call("PATCH", f"/api/kb/documents/{doc['id']}", {"category_id": cid, "mark_verified": True})[1]["category"],
                         "Mes documents › Treuil Huchez")
        self.assertEqual(self.call("POST", "/api/kb/import/text", {"title": "x", "text": ACIER})[0], 409)
        self.assertEqual(self.call("DELETE", f"/api/kb/documents/{doc['id']}")[0], 200)
        self.assertEqual(list((self.a.kbs.dir / "originals").iterdir()), [])
        self.assertEqual(self.call("GET", "/api/kb/documents/999")[0], 404)
        self.assertEqual(self.call("GET", "/api/kb/nimportequoi")[0], 404)

    def test_backup_restores_knowledge_too(self):
        self.a.kb.ingest("Acier", [("", ACIER)], "fichier", "a.txt")
        name = self.call("POST", "/api/backups", {})[1]["name"]
        self.a.kb.delete(1)
        self.assertEqual(self.a.kb.stats()["documents"], 0)
        self.assertEqual(self.call("POST", "/api/backups/restore", {"name": name})[0], 200)
        self.assertEqual(self.a.kb.stats()["documents"], 1)
        self.assertEqual(self.a.kb.search("module de Young acier")[0]["title"], "Acier")


class Migration(Base):
    def test_v01_imported_documents_move_to_knowledge_base(self):
        from jarvis_core import memory as mem
        s = mem.Store(self.tmp / "jarvis.db")
        s.add_memory("L'utilisateur s'appelle Maxence", "profil", 5)
        for i, part in enumerate(["Les roulements du treuil sont des 6204.", "Le tambour porte le repère 17."]):
            cur = s.db.execute(
                "INSERT INTO memories(content,category,importance,kind,source,title,created,updated,vec_local) "
                "VALUES(?,?,3,'connaissance','import','Notes treuil',1,1,?)",
                (f"[Notes treuil — partie {i + 1}/2]\n{part}", "connaissances", mem.pack(mem.local_vector(part))))
            s.db.execute("INSERT INTO memories_fts(content,mem_id) VALUES(?,?)", (part, cur.lastrowid))
        s.db.commit()
        s.close()
        app = App(self.tmp)
        self.assertEqual([m["content"] for m in app.store.list_memories()], ["L'utilisateur s'appelle Maxence"])
        d = app.kb.list()
        self.assertEqual([x["title"] for x in d], ["Notes treuil"])
        text = app.kb.chunks_of(d[0]["id"])[0]["content"]
        self.assertIn("6204", text)
        self.assertIn("repère 17", text)
        self.assertNotIn("partie 1/2", text)
        app.close()
        app = App(self.tmp)   # relancer ne duplique rien
        self.assertEqual(len(app.kb.list()), 1)
        app.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
