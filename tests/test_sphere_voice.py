"""Tests de la sphère 3D (données) et du lancement d'applications."""
import json
import math
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis_core import launcher  # noqa: E402
from jarvis_core.server import App, serve  # noqa: E402

ACIER = "L'acier est un alliage de fer et de carbone. Module de Young 210 GPa, résistance à la traction élevée, essai normalisé."
INOX = "L'acier inoxydable est un alliage de fer, de carbone et de chrome. Module de Young 200 GPa, résistance à la traction élevée."
PYTHON = "Python est un langage de programmation interprété, avec des fonctions, des classes et des modules."


def dist(a, b):
    return math.dist(a["p"], b["p"])


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="jarvis-sph-"))
        self.app = App(self.tmp)

    def tearDown(self):
        self.app.close()
        shutil.rmtree(self.tmp, ignore_errors=True)


class SphereData(Base):
    def fill(self):
        kb = self.app.kb
        mat, lang = kb.find_category("Matériaux et métallurgie"), kb.find_category("Langages de programmation")
        self.a = kb.ingest("Acier", [("", ACIER)], "fichier", "acier.pdf", mat)
        self.b = kb.ingest("Acier inoxydable", [("", INOX)], "fichier", "inox.pdf", mat)
        self.c = kb.ingest("Python", [("", PYTHON)], "fichier", "py.txt", lang)
        kb.ingest("Dérivée", [("", "La dérivée mesure la variation d'une fonction.")], "fichier", "d.txt", kb.find_category("Mathématiques"))
        kb.ingest("Réseau de neurones", [("", "Un réseau de neurones apprend par rétropropagation.")], "fichier", "n.txt",
                  kb.find_category("Intelligence artificielle"))
        kb.add_relations([("acier", "a pour propriété", "module de Young")], self.a)
        self.m1 = self.app.store.add_memory("L'utilisateur prépare un treuil pour son projet de construction", "projets", 4)["id"]
        self.m2 = self.app.store.add_memory("L'utilisateur préfère les explications avec un exemple chiffré", "préférences", 3)["id"]

    def node(self, data, nid):
        return next(n for n in data["nodes"] if n["id"] == nid)

    def test_empty_base_shows_only_families(self):
        d = self.app.sphere.build()
        self.assertEqual({n["k"] for n in d["nodes"]}, {"family"})
        self.assertTrue(all(v == 0 for v in d["stats"].values()), d["stats"])
        self.assertTrue(all(m["count"] == 0 for m in d["modules"]))

    def test_every_node_is_a_real_row(self):
        self.fill()
        d = self.app.sphere.build()
        self.assertEqual(d["stats"]["documents"], 5)
        self.assertEqual(d["stats"]["memories"], 2)
        self.assertEqual(d["stats"]["concepts"], 2)
        for n in d["nodes"]:
            det = self.app.sphere.detail(n["id"])
            self.assertIsNotNone(det, n["id"])
            if n["k"] == "doc":
                self.assertEqual(det["name"], self.app.kb.get(int(n["id"][2:]))["title"])
            if n["k"] == "memory":
                self.assertEqual(det["description"], self.app.store.get_memory(int(n["id"][2:]))["content"])
        self.assertIsNone(self.app.sphere.detail("d:999"))
        self.assertIsNone(self.app.sphere.detail("x:1"))
        self.assertIsNone(self.app.sphere.detail("m:abc"))

    def test_families_and_colours(self):
        self.fill()
        d = self.app.sphere.build()
        fam = lambda nid: self.node(d, nid)["f"]
        self.assertEqual(fam(f"d:{self.a}"), "sciences")
        self.assertEqual(fam(f"d:{self.c}"), "informatique")
        self.assertEqual(fam("d:4"), "maths")
        self.assertEqual(fam("d:5"), "ia")
        self.assertEqual(fam(f"m:{self.m1}"), "projets")
        self.assertEqual(fam(f"m:{self.m2}"), "perso")
        self.assertEqual(fam("e:1"), "sciences")          # le concept prend la famille de son document
        cols = {f["key"]: f["color"] for f in d["families"]}
        self.assertEqual(len(set(cols.values())), len(cols))
        self.assertEqual({f["key"]: f["count"] for f in d["families"] if f["count"]},
                         {"sciences": 4, "informatique": 1, "maths": 1, "ia": 1, "perso": 1, "projets": 1})

    def test_positions_follow_category_and_content(self):
        self.fill()
        d = self.app.sphere.build()
        a, b, c = (self.node(d, f"d:{i}") for i in (self.a, self.b, self.c))
        self.assertLess(dist(a, b), 0.35)                  # même catégorie, textes voisins : proches
        self.assertGreater(dist(a, c), 2.5 * dist(a, b))   # autre famille : loin
        for n in d["nodes"]:
            r = math.hypot(*n["p"])
            if n["k"] == "concept":
                self.assertLess(r, 0.8)                    # concepts à l'intérieur de la sphère
            else:
                self.assertTrue(0.85 < r < 1.15, (n["id"], r))
        e = self.node(d, "e:1")
        self.assertLess(dist(e, a), dist(e, c))            # le concept est du côté de son document
        again = self.app.sphere.build()
        self.assertIs(again, d)                            # rien n'a changé : pas de recalcul
        self.app.sphere._cache = (None, None)
        self.assertEqual([n["p"] for n in self.app.sphere.build()["nodes"]], [n["p"] for n in d["nodes"]])  # déterministe

    def test_edges(self):
        self.fill()
        d = self.app.sphere.build()
        ids = [n["id"] for n in d["nodes"]]
        pairs = {(ids[a], ids[b], t) for a, b, t in d["edges"]}
        has = lambda x, y, t: (x, y, t) in pairs or (y, x, t) in pairs
        self.assertTrue(has("e:1", "e:2", 1))                         # relation du graphe
        self.assertTrue(has("e:1", f"d:{self.a}", 2))                 # concept -> document source
        self.assertTrue(has(f"d:{self.a}", f"d:{self.b}", 3))         # contenus proches
        self.assertFalse(has(f"d:{self.a}", f"d:{self.c}", 3))
        self.assertTrue(has("f:projets", f"m:{self.m1}", 0))
        cat = self.app.kb.find_category("Matériaux et métallurgie")
        self.assertTrue(has(f"c:{cat}", f"d:{self.a}", 0))
        self.assertTrue(has("f:sciences", f"c:{cat}", 0))

    def test_version_changes_with_the_data(self):
        self.fill()
        v = self.app.sphere.version()
        self.assertEqual(self.app.sphere.version(), v)
        self.app.store.add_memory("L'utilisateur joue au badminton")
        v2 = self.app.sphere.version()
        self.assertNotEqual(v, v2)
        self.app.kb.delete(self.c)
        v3 = self.app.sphere.version()
        self.assertNotEqual(v2, v3)
        self.app.kb.add_relations([("fer", "compose", "acier")])
        self.assertNotEqual(v3, self.app.sphere.version())
        self.assertNotIn(f"d:{self.c}", [n["id"] for n in self.app.sphere.build()["nodes"]])

    def test_details(self):
        self.fill()
        d = self.app.sphere.detail(f"d:{self.a}")
        self.assertEqual((d["category"], d["source"]), ("Sciences fondamentales › Matériaux et métallurgie", "acier.pdf"))
        self.assertEqual(d["relations"][0]["label"], "a pour propriété")
        self.assertTrue(d["added"] and d["verified"])
        e = self.app.sphere.detail("e:1")
        self.assertEqual(e["docs"], [{"doc_id": self.a, "title": "Acier"}])
        f = self.app.sphere.detail("f:sciences")
        self.assertEqual(f["children_total"], 1)
        c = self.app.sphere.detail(f"c:{self.app.kb.find_category('Matériaux et métallurgie')}")
        self.assertEqual({k["label"] for k in c["children"]}, {"Acier", "Acier inoxydable"})


class Apps(Base):
    def setUp(self):
        super().setUp()
        self.ran = []
        self.app.app_runner = self.ran.append
        self.app.settings.update({"apps": [{"name": "Calculatrice", "target": "calc.exe"}, {"name": "Excel", "target": "excel.exe"},
                                           {"name": "Bloc-notes", "target": "notepad.exe"}]})
        self.httpd = serve(self.app, 0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        super().tearDown()

    def call(self, method, path, body=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method,
                                     headers={"Content-Type": "application/json", "X-Jarvis-Token": self.app.token},
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=20) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_only_whitelisted_apps_run(self):
        self.assertEqual(self.call("POST", "/api/apps/launch", {"query": "Lance la calculatrice s'il te plaît"})[1],
                         {"launched": True, "name": "Calculatrice"})
        self.assertEqual(self.call("POST", "/api/apps/launch", {"query": "ouvre le bloc notes"})[1]["name"], "Bloc-notes")
        self.assertEqual(self.call("POST", "/api/apps/launch", {"query": "ouvre EXCEL"})[1]["name"], "Excel")
        for q in ("lance photoshop", "lance cmd.exe /c del", "excellent travail", "calc.exe", "", "lance ../../x"):
            self.assertEqual(self.call("POST", "/api/apps/launch", {"query": q})[1], {"launched": False}, q)
        self.assertEqual(self.ran, ["calc.exe", "notepad.exe", "excel.exe"])   # jamais le texte prononcé, toujours la cible réglée
        self.assertEqual(len(self.call("GET", "/api/actions")[1]), 3)

    def test_failure_is_reported_and_logged(self):
        def boom(_):
            raise FileNotFoundError("introuvable")
        self.app.app_runner = boom
        r = self.call("POST", "/api/apps/launch", {"query": "lance excel"})[1]
        self.assertFalse(r["launched"])
        self.assertIn("Impossible de lancer Excel", r["error"])
        self.assertEqual(self.call("GET", "/api/actions")[1][0]["ok"], 0)

    def test_settings_are_sanitised(self):
        st = self.call("POST", "/api/settings", {"apps": [{"name": "  Mon   Appli ", "target": ' "C:\\x y\\a.exe" '}, {"name": "", "target": "x"},
                                                           {"name": "mon appli", "target": "doublon"}, "pas un objet", {"name": "N", "target": "a\nb"}],
                                                  "voice_wake_word": "  Dis   JARVIS  "})[1]
        self.assertEqual(st["settings"]["apps"], [{"name": "Mon Appli", "target": "C:\\x y\\a.exe"}])
        self.assertEqual(st["settings"]["voice_wake_word"], "dis jarvis")
        self.assertEqual(self.call("POST", "/api/settings", {"voice_wake_word": "   "})[1]["settings"]["voice_wake_word"], "jarvis")

    def test_sphere_api(self):
        self.app.store.add_memory("L'utilisateur aime le badminton")
        code, d = self.call("GET", "/api/sphere")
        self.assertEqual((code, d["stats"]["memories"]), (200, 1))
        self.assertEqual(self.call("GET", "/api/sphere/version")[1]["version"], d["version"])
        self.assertEqual(self.call("GET", "/api/sphere/node?id=m:1")[1]["kind"], "memory")
        self.assertEqual(self.call("GET", "/api/sphere/node?id=m:99")[0], 404)

    def test_default_apps_helper(self):
        self.assertEqual(launcher.find([{"name": "Explorateur de fichiers", "target": "explorer.exe"}, {"name": "Fichiers", "target": "x"}],
                                       "ouvre l'explorateur de fichiers")["target"], "explorer.exe")   # le nom le plus long l'emporte


if __name__ == "__main__":
    unittest.main(verbosity=2)
