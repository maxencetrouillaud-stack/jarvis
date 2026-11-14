"""Tests des 4 modules de la version 0.4 : bibliothèque technique, mémoire vectorielle, chercheur autonome,
centre de connaissances — et de leur branchement au cerveau et à la sphère.

Lancer :  python -m unittest discover -s tests -v
"""
import io
import json
import re
import shutil
import sqlite3
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

from fakes import Fake, FakeScience, FakeWeb  # noqa: E402
from jarvis_core import __version__, extract, knowledge, library, vectors  # noqa: E402
from jarvis_core.collect import Wikipedia  # noqa: E402
from jarvis_core.memory import local_vector  # noqa: E402
from jarvis_core.providers import REGISTRY  # noqa: E402
from jarvis_core.research import quote_found, norm_text, site_of  # noqa: E402
from jarvis_core.server import App, serve  # noqa: E402

RDM1 = ("Cours de résistance des matériaux, première édition. Le module de Young de l'acier vaut environ 210 GPa. "
        "La contrainte normale en traction s'écrit σ = F / S avec F l'effort et S la section.\n\n"
        "| Matériau | E (GPa) |\n| acier | 210 |\n| aluminium | 69 |\n\n"
        "La flèche d'une poutre encastrée vaut f = F*L^3/(3*E*I). Limite d'élasticité du S235 : 235 MPa.")
RDM2 = RDM1.replace("première édition", "deuxième édition, corrigée") + (
    " Ce chapitre ajoute le flambage des poutres élancées et la torsion des arbres pleins.")
USINAGE = ("Le fraisage est un procédé d'usinage par enlèvement de copeaux. La vitesse de coupe dépend de la fraise "
           "et de la matière usinée. L'avance par dent règle l'état de surface.")
CREPES = "Recette de cuisine : la pâte à crêpes se prépare avec de la farine, des œufs, du sucre et du beurre fondu."

WIKI_YOUNG = ("Le module de Young est la constante qui relie la contrainte de traction et la déformation d'un matériau "
              "élastique. Le module de Young de l'acier vaut environ 210 GPa. Il caractérise la rigidité du matériau.")
HAL_ABS = ("Nous mesurons le module de Young de l'acier : 210 GPa en moyenne sur douze éprouvettes de traction. "
           "La rigidité dépend peu de la nuance d'acier étudiée.")
OA_ABS = ("The Young modulus of steel is about 210 GPa at room temperature and decreases with temperature in a "
          "nearly linear way up to 400 degrees.")
ARXIV_ABS = ("We report a Young modulus of steel of 180 GPa for the tested alloy which is lower than textbook values "
             "and discuss the measurement method.")


def research_script(b):
    s = json.dumps(b, ensure_ascii=False)
    if "Tu prépares une recherche documentaire" in s:
        return json.dumps({"fr": ["module Young acier"], "en": ["Young modulus steel"]})
    if "Tu es un documentaliste rigoureux" in s:
        text = b["messages"][-1]["content"]
        n = {}
        for num, title in re.findall(r"\[(\d+)\] (.+?) — ", text):
            n[title] = int(num)
        out = []
        w, h, o, a = (n.get("Module de Young"), n.get("Caractérisation élastique des aciers"),
                      n.get("Elastic properties of steel"), n.get("Steel stiffness revisited"))
        if w and h:
            out.append({"affirmation": "Le module de Young de l'acier vaut environ 210 GPa.", "sources": [
                {"n": w, "citation": "Le module de Young de l'acier vaut environ 210 GPa"},
                {"n": h, "citation": "le module de Young de l'acier : 210 GPa"}]})
        if o:
            out.append({"affirmation": "Le module de Young de l'acier diminue quand la température augmente.",
                        "sources": [{"n": o, "citation": "decreases with temperature"}]})
        if w:
            out.append({"affirmation": "L'acier a été inventé en 1850.",
                        "sources": [{"n": w, "citation": "L'acier a été inventé en 1850 par un forgeron anglais"}]})
        if a and w:
            out.append({"affirmation": "Selon une prépublication, un alliage testé présente un module de Young de 180 GPa.",
                        "sources": [{"n": a, "citation": "We report a Young modulus of steel of 180 GPa for the tested alloy"}],
                        "contredit_par": [{"n": w, "citation": "vaut environ 210 GPa"}]})
        return json.dumps(out, ensure_ascii=False)
    if "Classe ce document" in s:
        return "Résistance des matériaux"
    if "Tu gères la mémoire" in s or "Relève les relations" in s:
        return "[]"
    return "Réponse de test [S1]."


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="jarvis-mod-"))
        self.stops, self.apps = [], []

    def tearDown(self):
        for a in self.apps:
            a.close()
        for s in self.stops:
            s.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def app(self, with_llm=True, script=research_script, **settings):
        self.web, self.sci, self.llm = FakeWeb(), FakeScience(), Fake("anthropic", script)
        self.stops += [self.web, self.sci, self.llm]
        app = App(self.tmp, {"wikipedia": Wikipedia(self.web.url + "/w/api.php", True), "allow_private": True,
                             "endpoints": self.sci.endpoints(), "pace_seconds": 0})
        provs = {pid: {"enabled": pid == "anthropic" and with_llm, "base_url": self.llm.url} for pid in REGISTRY}
        app.settings.update({"providers": provs, "memory_auto_extract": False, "kb_graph_auto": False, **settings})
        app.keys.set("anthropic", "cle-test")
        app.brain.refresh_models()
        self.apps.append(app)
        return app

    def add(self, app, name, text):
        app.kbs.submit_file(name, text.encode())
        self.assertTrue(app.kbs.wait(30))
        job = app.kb.jobs(1)[0]
        self.assertEqual(job["status"], "terminé", job["detail"])
        return job

    def science(self):
        self.web.articles["Module de Young"] = WIKI_YOUNG
        self.sci.hal = [{"id": "hal-01", "title": "Caractérisation élastique des aciers", "abstract": HAL_ABS}]
        self.sci.openalex = [{"id": "W1", "title": "Elastic properties of steel", "abstract": OA_ABS},
                             {"id": "W2", "title": "Retracted steel paper", "abstract": OA_ABS, "retracted": True}]
        self.sci.arxiv = [{"id": "2603.00001", "title": "Steel stiffness revisited", "summary": ARXIV_ABS}]

    def run_research(self, app, question="Quel est le module de Young de l'acier ?", mode="rapide"):
        rid = app.research.create(question, mode)
        self.assertTrue(app.research.wait(40), "la recherche ne s'est pas terminée")
        r = app.research.get(rid)
        self.assertEqual(r["status"], "terminé", r["error"] or r["log"])
        return r


# =================================================================== bibliothèque technique
class Library(Base):
    def test_formulas_tables_and_data_are_extracted(self):
        ex = library.find_extracts([("p. 2", RDM1)])
        kinds = {}
        for e in ex:
            kinds.setdefault(e["kind"], []).append(e)
        self.assertIn("σ = F / S", [e["content"] for e in kinds["formule"]])
        self.assertIn("f = F*L^3/(3*E*I)", [e["content"] for e in kinds["formule"]])
        self.assertEqual(len(kinds["tableau"]), 1)
        self.assertIn("aluminium | 69", kinds["tableau"][0]["content"])
        young = next(e for e in kinds["donnée"] if "Young" in e["label"])
        self.assertEqual((young["value"], young["unit"], young["locator"]), (210.0, "GPa", "p. 2"))
        self.assertTrue(any(e["value"] == 235.0 and e["unit"] == "MPa" for e in kinds["donnée"]))
        # la prose n'est pas prise pour une formule
        self.assertEqual([e for e in library.find_extracts([("", "Le prix = bon marché. Il a dit que tout va bien.")])
                          if e["kind"] == "formule"], [])

    def test_import_stores_extracts_and_reports_them(self):
        app = self.app(with_llm=False)
        job = self.add(app, "cours-rdm.txt", RDM1)
        self.assertIn("relevé", job["detail"])
        doc = app.kb.list()[0]
        ex = app.kb.extracts_of(doc["id"])
        self.assertEqual({e["kind"] for e in ex}, {"formule", "tableau", "donnée"})
        self.assertTrue(app.kb.search_extracts("Young", "donnée"))
        self.assertEqual(app.kb.search_extracts("licorne"), [])
        app.kb.delete(doc["id"])
        self.assertEqual(app.kb.search_extracts(""), [])

    def test_new_formats(self):
        def z(files):
            b = io.BytesIO()
            with zipfile.ZipFile(b, "w") as f:
                for n, c in files.items():
                    f.writestr(n, c)
            return b.getvalue()
        a = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
        _, s = extract.segments("x.pptx", z({
            "ppt/slides/slide2.xml": f'<p:sld xmlns:p="p" {a}><a:p><a:r><a:t>Deux</a:t></a:r></a:p></p:sld>',
            "ppt/slides/slide1.xml": f'<p:sld xmlns:p="p" {a}><a:p><a:r><a:t>Titre</a:t></a:r></a:p></p:sld>'}))
        self.assertEqual(s, [("diapo 1", "Titre"), ("diapo 2", "Deux")])
        o = 'xmlns:office="o" xmlns:text="t" xmlns:table="tb"'
        _, s = extract.segments("x.ods", z({"content.xml": (
            f'<office:document-content {o}><office:body><office:spreadsheet><table:table table:name="Feuil1">'
            '<table:table-row><table:table-cell><text:p>E</text:p></table:table-cell><table:table-cell>'
            '<text:p>210</text:p></table:table-cell></table:table-row></table:table></office:spreadsheet>'
            '</office:body></office:document-content>')}))
        self.assertEqual(s, [("feuille « Feuil1 »", "E | 210")])
        _, s = extract.segments("x.odt", z({"content.xml": (
            f'<office:document-content {o}><office:body><office:text><text:h>Chapitre</text:h>'
            '<text:p>Bonjour<text:s/>monde</text:p></office:text></office:body></office:document-content>')}))
        self.assertEqual(s[0][1], "# Chapitre\n\nBonjour monde")
        _, s = extract.segments("x.rtf", rb"{\rtf1\ansi{\fonttbl{\f0 Arial;}}\f0 L'acier est r\'e9sistant.\par Fin.}")
        self.assertEqual(s[0][1], "L'acier est résistant.\nFin.")
        with self.assertRaises(extract.ExtractError):
            extract.segments("x.pptx", z({"autre.xml": "<a/>"}))

    def test_classification_without_model_uses_vocabulary(self):
        app = self.app(with_llm=False)
        job = self.add(app, "fraisage.txt", USINAGE)
        self.assertIn("classé dans « Ingénierie et industrie › Usinage » d'après son vocabulaire", job["detail"])
        doc = app.kb.list()[0]
        self.assertEqual(doc["class_how"], "vocabulaire")
        self.assertEqual(doc["module"], "bibliotheque")
        self.add(app, "crepes.txt", CREPES)
        crepes = next(d for d in app.kb.list() if d["title"] == "crepes")
        self.assertEqual(crepes["module"], "generale")
        self.assertEqual([d["title"] for d in app.kb.list(module="generale")], ["crepes"])
        self.assertEqual([d["title"] for d in app.kb.list(module="bibliotheque")], ["fraisage"])
        # un texte sans vocabulaire reconnaissable n'est pas classé au hasard
        self.assertEqual(library.classify_text("note", "Penser à rappeler Paul demain matin.")[0], None)

    def test_new_category_is_added_to_existing_bases(self):
        app = self.app(with_llm=False)
        self.assertTrue(app.kb.find_category("Logiciels de CAO (FreeCAD, CATIA, SolidWorks)"))
        with app.kb.lock:
            app.kb.db.execute("DELETE FROM categories WHERE name LIKE 'Logiciels de CAO%'")
            app.kb.db.commit()
        n = app.kb.stats()["categories"]
        app.kb.reopen()
        self.assertEqual(app.kb.stats()["categories"], n + 1)

    def test_older_version_is_flagged_then_archived_on_request(self):
        app = self.app(with_llm=False, kb_auto_categorize=False)
        self.add(app, "Cours RDM v1.txt", RDM1)
        job = self.add(app, "Cours RDM v2.txt", RDM2)
        self.assertIn("semble remplacer le document", job["detail"])
        v1 = next(d for d in app.kb.list() if d["title"] == "Cours RDM v1")
        v2 = next(d for d in app.kb.list() if d["title"] == "Cours RDM v2")
        self.assertEqual(v1["superseded_by"], v2["id"])
        self.assertIsNone(v2["superseded_by"])
        self.assertEqual([d["id"] for d in app.kb.list(state="obsolete")], [v1["id"]])
        # rien n'est retiré sans décision : l'ancienne version répond encore, derrière la nouvelle
        hits = app.kb.search("flèche poutre encastrée", 6)
        self.assertEqual(hits[0]["doc_id"], v2["id"])
        self.assertIn(v1["id"], [h["doc_id"] for h in hits])
        app.kb.update(v1["id"], {"archived": True})
        self.assertNotIn(v1["id"], [h["doc_id"] for h in app.kb.search("flèche poutre encastrée", 6)])
        self.assertEqual(app.kb.stats()["archived"], 1)
        app.kb.update(v1["id"], {"archived": False, "not_obsolete": True})
        self.assertIsNone(app.kb.get(v1["id"])["superseded_by"])
        # deux documents sans rapport ne sont jamais pris pour deux versions
        self.add(app, "Cours usinage v1.txt", USINAGE)
        self.assertEqual(app.kb.stats()["obsolete"], 0)

    def test_watched_folder_indexes_new_and_changed_files(self):
        app = self.app(with_llm=False, kb_auto_categorize=False)
        folder = self.tmp / "bibliotheque"
        (folder / "sous-dossier").mkdir(parents=True)
        (folder / "rdm.txt").write_text(RDM1, encoding="utf-8")
        (folder / "sous-dossier" / "usinage.md").write_text(USINAGE, encoding="utf-8")
        (folder / "image.png").write_bytes(b"\x89PNG")           # format non géré : ignoré
        (folder / "~$verrou.docx").write_bytes(b"x")            # fichier temporaire d'Office : ignoré
        with self.assertRaises(ValueError):
            app.kbs.scan_folder()
        app.settings.update({"library_folder": str(folder)})
        res = app.kbs.scan_folder()
        self.assertEqual((res["files"], res["new"], res["changed"]), (2, 2, 0))
        self.assertTrue(app.kbs.wait(30))
        self.assertEqual(sorted(d["title"] for d in app.kb.list()), ["rdm", "usinage"])
        self.assertEqual(app.kbs.folder_state()["indexed"], 2)
        self.assertEqual(app.kbs.scan_folder()["new"], 0)        # rien n'a changé : rien n'est refait
        time.sleep(1.1)
        (folder / "rdm.txt").write_text(RDM2, encoding="utf-8")
        res = app.kbs.scan_folder()
        self.assertEqual((res["new"], res["changed"]), (0, 1))
        self.assertTrue(app.kbs.wait(30))
        rdm = next(d for d in app.kb.list() if d["title"] == "rdm")
        self.assertEqual(rdm["version"], 2)
        self.assertEqual(len(app.kb.versions(rdm["id"])), 1)     # l'ancienne version est conservée
        (folder / "sous-dossier" / "usinage.md").unlink()
        self.assertEqual(app.kbs.scan_folder()["missing"], 1)
        self.assertEqual(app.kb.stats()["documents"], 2)         # un fichier disparu n'efface rien dans la base
        self.assertEqual(app.kbs.folder_state()["missing"], 1)

    def test_conflicting_data_between_documents(self):
        app = self.app(with_llm=False, kb_auto_categorize=False)
        self.add(app, "a.txt", "Fiche A. Le module de Young de l'acier vaut environ 210 GPa selon ce cours.")
        self.add(app, "b.txt", "Fiche B, autre source. Le module de Young de l'acier vaut 150 GPa d'après ce relevé.")
        self.add(app, "c.txt", "Fiche C. Le module de Young de l'acier vaut 205 GPa, valeur usuelle.")
        conf = app.kb.data_conflicts()
        pairs = {tuple(sorted((c["a"]["title"], c["b"]["title"]))) for c in conf}
        self.assertEqual(pairs, {("a", "b"), ("b", "c")})         # 210 et 205 sont compatibles (écart < 10 %)

    def test_failed_job_can_be_retried(self):
        app = self.app(with_llm=False, kb_auto_categorize=False)
        app.kbs.submit_url(self.web.url + "/fiche")               # la page n'existe pas encore
        self.assertTrue(app.kbs.wait(30))
        job = app.kb.jobs(1)[0]
        self.assertEqual(job["status"], "erreur")
        self.assertEqual(app.hub.dashboard()["errors"][0]["id"], job["id"])
        self.web.pages["/fiche"] = ("text/html; charset=utf-8", ("<html><title>Fiche</title><body><p>" + USINAGE
                                                                + "</p></body></html>").encode())
        jid = app.kbs.retry(job["id"])
        self.assertTrue(app.kbs.wait(30))
        self.assertGreater(jid, job["id"])
        self.assertEqual(app.kb.jobs(1)[0]["status"], "terminé")
        self.assertEqual(app.kb.stats()["documents"], 1)
        with self.assertRaises(ValueError):
            app.kbs.retry(999)
        app.kbs.submit_file("vide.txt", b"   ")                    # échec définitif : rien à relancer
        self.assertTrue(app.kbs.wait(30))
        with self.assertRaises(ValueError):
            app.kbs.retry(app.kb.jobs(1)[0]["id"])


# =================================================================== mémoire vectorielle
class VectorMemory(Base):
    def test_signatures_keep_neighbours_close(self):
        a = local_vector("acier alliage fer carbone traction résistance")
        b = local_vector("acier alliage fer carbone essai de traction")
        c = local_vector("recette crêpes farine œufs lait sucre")
        sa, sb, sc = vectors.signature(a), vectors.signature(b), vectors.signature(c)
        self.assertLess(vectors.popcount(sa ^ sb), vectors.popcount(sa ^ sc))
        self.assertTrue(0 <= sa < 2 ** 62)                       # tient dans un entier SQLite signé
        idx = vectors.SigIndex()
        idx.ensure("k", lambda: [(1, sb), (2, sc)])
        self.assertEqual(idx.nearest(sa, 1), [1])

    def test_mmr_prefers_diverse_passages(self):
        items = [{"id": 1, "s": 1.0, "v": [1.0, 0.0]}, {"id": 2, "s": 0.99, "v": [1.0, 0.0]},
                 {"id": 3, "s": 0.9, "v": [0.0, 1.0]}]
        out = vectors.mmr(items, lambda d: d["v"], lambda d: d["s"], 2, 0.7)
        self.assertEqual([d["id"] for d in out], [1, 3])

    def test_large_base_uses_approximate_index_and_finds_by_meaning(self):
        app = self.app(with_llm=False, kb_auto_categorize=False, kb_auto_links=False)
        old = knowledge.FULL_SCAN_LIMIT
        knowledge.FULL_SCAN_LIMIT = 10
        try:
            emb = lambda t: (Fake.embed(t), "test:sem")
            for i in range(30):
                app.kb.ingest(f"note {i}", [("", f"Document numéro {i} sur un sujet quelconque, section {i * 7}.")],
                              "fichier", embed=emb)
            app.kb.ingest("entraînement", [("", "Séance du mardi : raquette, volant et déplacements sur le court.")],
                          "fichier", embed=emb)
            self.assertTrue(app.kb.stats()["ann_active"])
            # aucun mot commun avec le passage : seul le sens (vecteurs) permet de le retrouver
            hits = app.kb.search("Parle-moi de badminton", 3, emb("Parle-moi de badminton"))
            self.assertEqual(hits[0]["title"], "entraînement")
            self.assertEqual(hits[0]["found_by"], "sens")
            self.assertEqual(app.kb.search("Parle-moi de badminton", 3), [])      # sans embeddings : rien d'inventé
            # la recherche par mots fonctionne toujours sur la grande base
            self.assertEqual(app.kb.search("section 147", 3)[0]["title"], "note 21")
        finally:
            knowledge.FULL_SCAN_LIMIT = old

    def test_old_base_is_upgraded_without_losing_anything(self):
        if sqlite3.sqlite_version_info < (3, 35):
            self.skipTest("SQLite trop ancien pour simuler une base 0.3")
        app = self.app(with_llm=False, kb_auto_categorize=False)
        self.add(app, "rdm.txt", RDM1)
        app.close()
        self.apps.clear()
        db = sqlite3.connect(self.tmp / "knowledge.db")
        db.execute("DROP INDEX idx_doc_title_key")
        for table, col, _ in knowledge.MIGRATIONS:               # on retire tout ce que la 0.4 a ajouté
            db.execute(f"ALTER TABLE {table} DROP COLUMN {col}")
        for t in ("extracts", "links", "watched_files", "source_rules", "research", "research_sources", "findings", "watch"):
            db.execute(f"DROP TABLE {t}")
        db.commit()
        db.close()
        app = self.app(with_llm=False, kb_auto_categorize=False)
        self.assertTrue(app.kbs.wait(30))
        self.assertEqual(app.kb.jobs(1)[0]["kind"], "index")
        with app.kb.lock:
            self.assertEqual(app.kb.db.execute("SELECT COUNT(*) FROM chunks WHERE sig_l IS NULL").fetchone()[0], 0)
        self.assertEqual(app.kb.search("module de Young acier")[0]["title"], "rdm")
        self.assertEqual(app.hub.dashboard()["totals"]["documents"], 1)
        self.assertEqual({e["kind"] for e in app.kb.extracts_of(1)}, {"formule", "tableau", "donnée"})   # relevé rattrapé

    def test_semantic_reindex_covers_documents(self):
        app = self.app(with_llm=False, kb_auto_categorize=False)
        self.add(app, "sport.txt", "Séance du mardi : raquette, volant et déplacements sur le court.")
        ollama = Fake("ollama", lambda b: "ok")
        self.stops.append(ollama)
        provs = app.settings.get("providers")
        provs["ollama"] = {"enabled": True, "base_url": ollama.url}
        app.settings.update({"providers": provs, "embedding_model": "ollama:nomic-embed-text"})
        app.brain.refresh_models()
        app.kbs.submit_reindex()
        self.assertTrue(app.kbs.wait(30))
        self.assertIn("1 passage(s) et 1 document(s)", app.kb.jobs(1)[0]["detail"])
        v = app.hub.dashboard()["vector"]
        self.assertEqual((v["chunks_semantic"], v["model"]), (1, "ollama:nomic-embed-text"))


# =================================================================== hub : cerveau et connexions
class HubTests(Base):
    def test_plan_picks_modules(self):
        app = self.app(with_llm=False)
        p = app.hub.plan("Salut !")
        self.assertTrue(p["small_talk"])
        self.assertFalse(p["kb"])
        self.assertEqual(p["memory"], "essentiel")
        p = app.hub.plan("Quelle vitesse de coupe pour le fraisage de l'aluminium ?")
        self.assertEqual(p["domain"], "Usinage")
        self.assertTrue(p["kb"])
        self.assertEqual(p["memory"], "ciblée")
        self.assertFalse(p["recall"])
        p = app.hub.plan("Où en est mon projet de treuil ?")
        self.assertEqual(p["memory"], "complète")
        self.assertTrue(p["recall"])
        self.assertTrue(app.hub.plan("Quelles sont les nouveautés récentes en impression 3D ?")["recent"])

    def test_chat_reports_modules_and_skips_search_for_small_talk(self):
        app = self.app(kb_auto_categorize=False)
        self.add(app, "acier.txt", "Le module de Young de l'acier vaut environ 210 GPa. L'acier est un alliage de fer.")
        app.store.add_memory("Maxence prépare un BUT GMP à Limoges", "identité", 5)
        c = app.store.create_conversation()
        ev = list(app.brain.chat(c["id"], "Quel est le module de Young de l'acier ?", "auto", None))
        meta = next(e for e in ev if e["type"] == "meta")
        mods = {m["key"]: m for m in meta["modules"]}
        self.assertEqual(mods["bibliotheque"]["hits"], 1)
        self.assertTrue(mods["bibliotheque"]["used"])
        self.assertEqual(meta["domain"], "Résistance des matériaux")
        self.assertIn("210 GPa", self.llm.last_body()["system"])
        ev = list(app.brain.chat(c["id"], "Merci !", "auto", None))
        meta = next(e for e in ev if e["type"] == "meta")
        mods = {m["key"]: m for m in meta["modules"]}
        self.assertFalse(mods["bibliotheque"]["used"])
        self.assertNotIn("<connaissances>", self.llm.last_body()["system"])
        self.assertIn("BUT GMP", self.llm.last_body()["system"])   # l'essentiel de la mémoire reste transmis
        saved = app.store.get_conversation(c["id"])["messages"][-1]                   # le détail est enregistré avec la réponse
        self.assertIn("modules", saved["meta"])

    def test_conflicting_sources_are_flagged_to_the_model(self):
        app = self.app(kb_auto_categorize=False)
        self.add(app, "a.txt", "Fiche A. Le module de Young de l'acier vaut environ 210 GPa selon ce cours.")
        self.add(app, "b.txt", "Fiche B, autre relevé. Le module de Young de l'acier vaut 150 GPa d'après cet essai.")
        g = app.hub.gather("Quel est le module de Young de l'acier ?", "general", None, None)
        self.assertEqual(len(g["conflicts"]), 1)
        c = app.store.create_conversation()
        ev = list(app.brain.chat(c["id"], "Quel est le module de Young de l'acier ?", "auto", None))
        self.assertIn("valeurs différentes pour une même grandeur", self.llm.last_body()["system"])
        self.assertTrue(next(e for e in ev if e["type"] == "meta")["conflicts"])

    def test_gap_offers_a_research_instead_of_inventing(self):
        app = self.app(kb_auto_categorize=False)
        self.add(app, "acier.txt", "Le module de Young de l'acier vaut environ 210 GPa.")
        g = app.hub.gather("Quelle est la température de fusion du tungstène ?", "general", None, None)
        self.assertTrue(g["kb_miss"])
        self.assertEqual(g["research_offer"], "manque")
        app.settings.update({"research_on_gap": "jamais"})
        self.assertEqual(app.hub.gather("Quelle est la température de fusion du tungstène ?", "general", None, None)["research_offer"], "")

    def test_new_knowledge_is_linked_to_what_exists(self):
        app = self.app(with_llm=False, kb_auto_categorize=False)
        self.add(app, "acier.txt", "L'acier est un alliage de fer et de carbone. Son module de Young vaut 210 GPa. "
                                   "La résistance à la traction de l'acier se mesure par un essai de traction.")
        job = self.add(app, "essai-traction.txt", "L'essai de traction de l'acier mesure la résistance à la traction "
                                                  "et le module de Young d'une éprouvette d'acier.")
        self.assertIn("1 connexion(s)", job["detail"])
        job = self.add(app, "crepes.txt", CREPES)
        self.assertIn("aucune connaissance proche", job["detail"])
        docs = {d["title"]: d["id"] for d in app.kb.list()}
        links = app.kb.links_of(f"d:{docs['acier']}")
        self.assertEqual({links[0]["a"], links[0]["b"]}, {f"d:{docs['acier']}", f"d:{docs['essai-traction']}"})
        res = app.store.add_memory("Je révise l'essai de traction de l'acier pour le partiel de résistance des matériaux", "études")
        self.assertGreaterEqual(app.hub.integrate(f"m:{res['id']}"), 1)
        self.assertTrue(any(l["kind"] == "sémantique" for l in app.kb.links_of(f"m:{res['id']}")))
        app.kb.delete(docs["acier"])
        self.assertEqual(app.kb.links_of(f"d:{docs['acier']}"), [])
        app.hub.forget(f"m:{res['id']}")
        self.assertEqual(app.kb.links_of(f"m:{res['id']}"), [])

    def test_cross_search_and_dashboard_and_export(self):
        app = self.app(with_llm=False)
        self.add(app, "fraisage.txt", USINAGE)
        self.add(app, "crepes.txt", CREPES)
        app.store.add_memory("Maxence usine ses pièces par fraisage à l'atelier de l'IUT", "projets")
        app.store.add_memory("Maxence aime les crêpes au sucre", "préférences")
        res = app.hub.search("fraisage")
        self.assertEqual({r["module"] for r in res}, {"bibliotheque", "projets"})
        self.assertEqual({r["module"] for r in app.hub.search("crêpes")}, {"generale", "perso"})
        self.assertEqual([r["module"] for r in app.hub.search("fraisage", ["projets"])], ["projets"])
        d = app.hub.dashboard()
        self.assertEqual((d["totals"]["documents"], d["totals"]["memories"]), (2, 2))
        self.assertEqual({m["key"]: m["count"] for m in d["modules"]},
                         {"bibliotheque": 1, "generale": 1, "recherche": 0, "vectorielle": 4, "perso": 1, "projets": 1})
        self.assertEqual(len(d["latest"]), 4)
        self.assertGreater(d["storage"]["total"], 0)
        self.assertGreater(d["storage"]["originals"], 0)
        exp = app.hub.export()
        with zipfile.ZipFile(self.tmp / "exports" / exp["name"]) as z:
            names = set(z.namelist())
            self.assertTrue({"knowledge.db", "documents.jsonl", "souvenirs.jsonl", "graphe.json", "connexions.json",
                             "recherches.json", "LISEZ-MOI.txt"} <= names)
            self.assertEqual(sum(n.startswith("fichiers-origine/") for n in names), 2)
            docs = [json.loads(l) for l in z.read("documents.jsonl").decode().splitlines()]
            self.assertIn("vitesse de coupe", next(x for x in docs if x["title"] == "fraisage")["text"])
        self.assertEqual(app.hub.exports()[0]["name"], exp["name"])


# =================================================================== chercheur autonome
class Research(Base):
    def test_quotes_must_be_in_the_source(self):
        t = norm_text(WIKI_YOUNG)
        self.assertTrue(quote_found("le module de young de l’acier vaut environ 210 GPa", t))
        self.assertFalse(quote_found("le module de Young de l'acier vaut environ 250 GPa", t))
        self.assertFalse(quote_found("210 GPa", t))               # trop court pour prouver quoi que ce soit
        self.assertEqual(site_of("https://fr.wikipedia.org/wiki/Acier"), "wikipedia.org")
        self.assertEqual(site_of("https://www.service-public.gouv.fr/x"), "service-public.gouv.fr")

    def test_full_run_checks_every_claim_then_waits_for_validation(self):
        app = self.app(kb_auto_categorize=False)
        self.science()
        r = self.run_research(app)
        self.assertEqual({s["provider"] for s in r["sources"]}, {"wikipedia", "hal", "openalex", "arxiv"})
        self.assertNotIn("Retracted steel paper", [s["title"] for s in r["sources"]])   # article rétracté écarté
        by = {f["claim"]: f for f in r["findings"]}
        self.assertEqual(len(by), 3, list(by))
        ok = by["Le module de Young de l'acier vaut environ 210 GPa."]
        self.assertEqual(ok["status"], "vérifié")
        self.assertEqual({s["site"] for s in ok["support"]}, {"wikipedia.org", "hal.science"})
        one = by["Le module de Young de l'acier diminue quand la température augmente."]
        self.assertEqual(one["status"], "à confirmer")
        self.assertLess(one["confidence"], ok["confidence"])
        clash = next(f for f in r["findings"] if "prépublication" in f["claim"])
        self.assertEqual(clash["status"], "contradictoire")
        self.assertEqual(clash["contra"][0]["site"], "wikipedia.org")
        self.assertNotIn("L'acier a été inventé en 1850.", by)    # citation introuvable : affirmation écartée
        self.assertTrue(any("citation(s) écartée(s)" in s["detail"] for s in r["log"]))
        self.assertEqual(r["category"], "Ingénierie et industrie › Résistance des matériaux")
        # rien n'est entré dans la base
        self.assertEqual(app.kb.stats()["documents"], 0)
        self.assertEqual(len(app.research.pending()), 3)
        self.assertEqual(app.hub.dashboard()["todo"]["findings"], 3)

        res = app.research.decide(ok["id"], "valider")
        self.assertEqual(res["decision"], "intégré")
        doc = app.kb.get(res["doc_id"])
        self.assertEqual((doc["source_type"], doc["module"], doc["reliability"]), ("recherche", "recherche", 4))
        self.assertIn("hal.science/hal-01", doc["refs"])
        text = app.kb.chunks_of(doc["id"])[0]["content"]
        self.assertIn("« Le module de Young de l'acier vaut environ 210 GPa »", text)
        self.assertEqual(app.kb.search("module de Young acier")[0]["module"], "recherche")
        res2 = app.research.decide(one["id"], "valider", "Le module de Young de l'acier baisse quand la température monte.")
        d2 = app.kb.get(res2["doc_id"])
        self.assertEqual(d2["reliability"], 2)                   # une seule source : fiabilité plafonnée
        self.assertIn("à confirmer", d2["description"])
        self.assertEqual(app.research.decide(clash["id"], "refuser")["decision"], "refusé")
        with self.assertRaises(ValueError):
            app.research.decide(ok["id"], "valider")
        self.assertEqual(app.research.pending(), [])

        # le cerveau présente une affirmation non confirmée comme telle
        g = app.hub.gather("Le module de Young baisse-t-il avec la température ?", "general", None, None)
        src = next(s for s in g["sources"] if s["doc_id"] == d2["id"])
        self.assertEqual((src["module"], src["status"]), ("recherche", "à confirmer"))
        self.assertIn("UNE seule source", app.brain.system_prompt("anthropic:modele-a", [], [], g["sources"]))

        # sphère : la recherche et ses connaissances validées apparaissent dans leur module
        data = app.sphere.build()
        node = next(n for n in data["nodes"] if n["id"] == f"r:{r['id']}")
        self.assertEqual((node["k"], node["m"]), ("research", "recherche"))
        self.assertEqual(next(m for m in data["modules"] if m["key"] == "recherche")["count"], 3)
        det = app.sphere.detail(f"r:{r['id']}")
        self.assertEqual({c["id"] for c in det["children"]}, {f"d:{doc['id']}", f"d:{d2['id']}"})

        # retirer la recherche de l'historique ne retire pas les connaissances validées
        self.assertTrue(app.research.delete(r["id"])["kept"])
        self.assertEqual(app.kb.stats()["documents"], 2)

    def test_without_model_only_measured_data_and_sources(self):
        app = self.app(with_llm=False, kb_auto_categorize=False)
        self.science()
        r = self.run_research(app)
        self.assertTrue(any("aucun modèle disponible" in s["detail"] for s in r["log"]))
        self.assertEqual(len(r["findings"]), 1)
        f = r["findings"][0]
        self.assertEqual((f["claim"], f["status"], f["origin"]),
                         ("Module de Young de l'acier : 210 GPa", "vérifié", "relevé automatique"))
        self.assertEqual({s["site"] for s in f["support"]}, {"wikipedia.org", "hal.science"})
        self.assertIn("210 GPa", f["support"][0]["quote"])

    def test_auto_integration_only_for_verified_and_never_against_existing_knowledge(self):
        app = self.app(with_llm=False, kb_auto_categorize=False, research_require_validation=False)
        self.science()
        r = self.run_research(app)
        self.assertEqual(r["findings"][0]["decision"], "intégré")
        self.assertIn("intégrée(s) automatiquement", r["summary"])
        self.assertEqual(app.kb.stats()["documents"], 1)
        app.close()
        self.apps.clear()
        shutil.rmtree(self.tmp)
        self.tmp.mkdir()
        app = self.app(with_llm=False, kb_auto_categorize=False, research_require_validation=False)
        self.science()
        self.add(app, "vieux-cours.txt", "Ancien cours. Le module de Young de l'acier vaut 150 GPa d'après ce polycopié.")
        r = self.run_research(app)
        f = r["findings"][0]
        self.assertEqual(f["decision"], "en attente")
        self.assertIn("contredit « vieux-cours »", f["note"])
        self.assertEqual(app.kb.stats()["documents"], 1)          # l'existant n'est ni remplacé ni contredit en silence

    def test_single_source_is_never_auto_integrated(self):
        app = self.app(kb_auto_categorize=False, research_require_validation=False)
        self.science()
        r = self.run_research(app)
        by = {f["status"]: f for f in r["findings"]}
        self.assertEqual(by["vérifié"]["decision"], "intégré")
        self.assertEqual(by["à confirmer"]["decision"], "en attente")
        self.assertEqual(by["contradictoire"]["decision"], "en attente")

    def test_source_rules_block_and_override_reliability(self):
        app = self.app(with_llm=False, kb_auto_categorize=False)
        self.science()
        app.kb.set_source_rule("hal.science", blocked=True)
        app.kb.set_source_rule("wikipedia.org", reliability=5)
        with self.assertRaises(ValueError):
            app.kb.set_source_rule("pas un domaine")
        r = self.run_research(app)
        self.assertEqual({s["provider"] for s in r["sources"]}, {"wikipedia"})
        self.assertEqual(r["sources"][0]["reliability"], 5)
        self.assertEqual(r["findings"][0]["status"], "à confirmer")   # une seule source restante
        tracked = {s["domain"]: s for s in app.research.tracked_sources()}
        self.assertTrue(tracked["hal.science"]["rule"]["blocked"])
        self.assertEqual((tracked["wikipedia.org"]["read"], tracked["wikipedia.org"]["used"]), (1, 1))

    def test_web_search_needs_a_key_and_keeps_only_excerpts(self):
        app = self.app(with_llm=False, kb_auto_categorize=False,
                       research_sources={"wikipedia": False, "hal": False, "openalex": False, "arxiv": False, "web": True})
        self.sci.pages["/doc"] = ("<html><head><title>Fiche acier</title></head><body><p>Le module de Young de "
                                  "l'acier vaut environ 210 GPa selon cette fiche technique du fabricant.</p>"
                                  + "<p>Texte de remplissage sur les aciers de construction.</p>" * 20 + "</body></html>")
        self.sci.brave = [{"title": "Fiche acier", "url": self.sci.url + "/doc"}]
        st = {p["key"]: p for p in app.research.providers_state()}
        self.assertFalse(st["web"]["ready"])
        rid = app.research.create("Quel est le module de Young de l'acier ?")
        self.assertTrue(app.research.wait(30))
        self.assertEqual(app.research.get(rid)["status"], "erreur")   # aucune source active : dit clairement
        app.keys.set("brave", "cle-brave")
        r = self.run_research(app)
        self.assertEqual(r["sources"][0]["provider"], "web")
        self.assertIn("droits de l'éditeur", r["sources"][0]["license"])
        self.assertLess(len(r["sources"][0]["excerpt"]), 2500)
        self.assertTrue(any(h[0] == "/brave" and h[2].get("X-Subscription-Token") == "cle-brave" for h in self.sci.hits))

    def test_watch_runs_with_recent_first(self):
        app = self.app(with_llm=False, kb_auto_categorize=False)
        self.science()
        app.research.add_watch("module de Young acier", 7)
        w = app.research.watches()[0]
        self.assertEqual(app.research.run_due_watches(), [1])     # jamais lancée : à échéance
        self.assertTrue(app.research.wait(40))
        self.assertEqual(app.research.get(1)["origin"], "veille")
        self.assertTrue(any(h[0] == "/arxiv" and h[1].get("sortBy") == "submittedDate" for h in self.sci.hits))
        self.assertTrue(any(h[0] == "/hal" and "desc" in h[1].get("sort", "") for h in self.sci.hits))
        self.assertEqual(app.research.run_due_watches(), [])      # pas avant la prochaine échéance
        self.assertEqual(len(app.research.run_due_watches(force_id=w["id"])), 1)
        app.research.wait(40)
        app.research.update_watch(w["id"], enabled=False)
        self.assertFalse(app.research.watches()[0]["enabled"])
        app.research.delete_watch(w["id"])
        self.assertEqual(app.research.watches(), [])

    def test_thin_topics_and_cancel(self):
        app = self.app(with_llm=False, kb_auto_categorize=False)
        self.add(app, "acier.txt", "Le module de Young de l'acier vaut environ 210 GPa.")
        app.hub.gather("Quelle est la température de fusion du tungstène ?", "general", None, None)
        thin = app.research.thin_topics()
        self.assertEqual(thin[0]["kind"], "question sans source")
        self.assertIn("tungstène", thin[0]["question"])
        rid = app.research.create("Température de fusion du tungstène", origin="lacune", gap_id=thin[0]["gap_id"])
        app.research.wait(30)
        self.assertEqual(app.research.get(rid)["status"], "terminé")   # aucune source trouvée : terminé, sans rien inventer
        self.assertEqual(app.research.get(rid)["findings"], [])
        self.assertFalse(app.research.delete(rid)["kept"])
        self.assertIsNone(app.research.get(rid))
        with self.assertRaises(ValueError):
            app.research.create("ab")


# =================================================================== sphère
class SphereModules(Base):
    def test_modules_zones_and_semantic_edges(self):
        app = self.app(with_llm=False, kb_auto_categorize=False)
        topics = [("acier", "L'acier est un alliage de fer et de carbone utilisé en construction mécanique. Fiche {i} "
                            "sur la traction de l'acier et son module de Young."),
                  ("crêpes", "Recette de crêpes numéro {i} : farine, œufs, lait, sucre et beurre pour la pâte à crêpes."),
                  ("python", "Programme Python numéro {i} : une fonction, une boucle et des variables dans le code.")]
        for i in range(6):
            for name, text in topics:
                app.kb.ingest(f"{name} {i}", [("", text.format(i=i))], "fichier")
        for d in app.kb.list():
            app.hub.integrate(f"d:{d['id']}")
        app.store.add_memory("Maxence fabrique un treuil pour son projet de BUT", "projets")
        data = app.sphere.build()
        mods = {m["key"]: m["count"] for m in data["modules"]}
        self.assertEqual(mods["bibliotheque"], 18)
        self.assertEqual(mods["projets"], 1)
        zones = [n for n in data["nodes"] if n["k"] == "cluster"]
        self.assertEqual(mods["vectorielle"], len(zones))
        self.assertGreaterEqual(len(zones), 2)
        labels = " ".join(z["l"] for z in zones)
        self.assertTrue(any(w in labels for w in ("crêpes", "farine", "recette", "pâte", "sucre", "beurre")), labels)
        self.assertEqual(labels, " ".join(n["l"] for n in app.sphere.build()["nodes"] if n["k"] == "cluster"))
        self.assertTrue(all(n["m"] in ("", "bibliotheque", "projets", "vectorielle") for n in data["nodes"]))
        self.assertGreater(data["stats"]["connections"], 0)
        self.assertTrue(any(e[2] == 5 for e in data["edges"]))
        det = app.sphere.detail(zones[0]["id"])
        self.assertEqual(det["kind"], "cluster")
        self.assertGreaterEqual(len(det["children"]), 3)
        doc = app.sphere.detail("d:1")
        self.assertEqual(doc["module"], "bibliotheque")
        self.assertTrue(doc["connections"])
        # un document archivé disparaît de la sphère, et l'affichage se met à jour
        v = app.sphere.version()
        app.kb.update(1, {"archived": True})
        self.assertNotEqual(app.sphere.version(), v)
        self.assertNotIn("d:1", [n["id"] for n in app.sphere.build()["nodes"]])


# =================================================================== API HTTP
class Api(Base):
    def setUp(self):
        super().setUp()
        self.a = self.app(with_llm=False, kb_auto_categorize=False)
        self.httpd = serve(self.a, 0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        super().tearDown()

    def call(self, method, path, body=None, raw=False):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method,
                                     headers={"Content-Type": "application/json", "X-Jarvis-Token": self.a.token},
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=30) as r:
                data = r.read()
                return r.status, data if raw else json.loads(data)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_center_and_research_routes(self):
        self.science()
        self.add(self.a, "rdm.txt", RDM1)
        code, d = self.call("GET", "/api/hub/dashboard")
        self.assertEqual((code, d["totals"]["documents"]), (200, 1))
        self.assertEqual(d["totals"]["extracts"]["formule"], 2)
        code, doc = self.call("GET", "/api/kb/documents/1")
        self.assertEqual({e["kind"] for e in doc["extracts"]}, {"formule", "tableau", "donnée"})
        code, ex = self.call("GET", "/api/kb/extracts?kind=formule&q=F")
        self.assertEqual(len(ex), 2)
        code, res = self.call("GET", "/api/hub/search?q=module%20de%20Young&modules=bibliotheque")
        self.assertEqual(res[0]["module"], "bibliotheque")
        code, q = self.call("POST", "/api/hub/query", {"question": "Quel est le module de Young de l'acier ?"})
        self.assertEqual((code, q["sources"][0]["title"], q["plan"]["domain"]), (200, "rdm", "Résistance des matériaux"))

        code, r = self.call("POST", "/api/research", {"question": "Quel est le module de Young de l'acier ?"})
        self.assertEqual(code, 200)
        self.assertTrue(self.a.research.wait(40))
        code, st = self.call("GET", "/api/research")
        self.assertEqual((st["runs"][0]["status"], st["runs"][0]["n_findings"], len(st["pending"])), ("terminé", 1, 1))
        self.assertFalse(st["llm_ready"])
        code, det = self.call("GET", f"/api/research/{r['id']}")
        self.assertEqual(len(det["sources"]), 2)
        code, res = self.call("POST", f"/api/research/findings/{st['pending'][0]['id']}", {"action": "valider"})
        self.assertEqual((code, res["decision"]), (200, "intégré"))
        code, sph = self.call("GET", "/api/sphere")
        self.assertIn(f"r:{r['id']}", [n["id"] for n in sph["nodes"]])
        code, _ = self.call("POST", "/api/research", {"question": "x"})
        self.assertEqual(code, 400)

        code, src = self.call("POST", "/api/kb/sources", {"domain": "hal.science", "reliability": 5, "note": "référence"})
        self.assertEqual(next(s for s in src if s["domain"] == "hal.science")["reliability"], 5)
        code, src = self.call("DELETE", "/api/kb/sources/hal.science")
        self.assertIsNone(next(s for s in src if s["domain"] == "hal.science")["rule"])
        code, st = self.call("POST", "/api/research/keys", {"name": "brave", "key": "abc"})
        self.assertTrue(next(p for p in st if p["key"] == "web")["has_key"])
        self.assertEqual(self.call("POST", "/api/research/keys", {"name": "autre", "key": "x"})[0], 400)
        code, w = self.call("POST", "/api/research/watch", {"topic": "impression 3D métal", "every_days": 14})
        self.assertEqual((code, w[0]["every_days"]), (200, 14))
        self.assertEqual(self.call("GET", "/api/research/thin")[0], 200)

        code, exp = self.call("POST", "/api/hub/export")
        self.assertEqual(code, 200)
        code, raw = self.call("GET", f"/api/hub/exports/{exp['name']}", raw=True)
        self.assertEqual(code, 200)
        self.assertIn("documents.jsonl", zipfile.ZipFile(io.BytesIO(raw)).namelist())
        self.assertEqual(self.call("GET", "/api/hub/exports/..%2Fsecrets.zip")[0], 404)
        self.assertEqual(self.call("POST", "/api/kb/folder/scan")[0], 400)
        code, state = self.call("GET", "/api/state")
        self.assertEqual((state["version"], len(state["modules"])), (__version__, 6))


if __name__ == "__main__":
    unittest.main()
