"""Tests de la phase 1 : cerveau central (orchestration) et mémoire intelligente."""
import json
import sqlite3
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_core import Base, blob, make_app, run_chat, text_of  # noqa: E402
from jarvis_core import memory as mem  # noqa: E402
from jarvis_core.orchestrator import Orchestrator, assess, objective  # noqa: E402
from jarvis_core.server import serve  # noqa: E402

COMPLEX = ("Prépare-moi un plan d'action pour mon projet de treuil :\n"
           "- liste les pièces à dimensionner\n- calcule le couple moteur pour 200 kg à 0,5 m/s avec un tambour de 10 cm\n"
           "- propose ensuite un planning sur trois semaines\n- puis rédige un court mail pour mon tuteur")


def steps_script(plan_json, step_reply, final="Synthèse finale.", verify='{"ok": true, "problemes": []}', log=None):
    log = log if log is not None else []

    def script(b):
        s = blob(b)
        if "planificateur d'un assistant" in s:
            log.append("plan")
            return plan_json
        if "Tu exécutes UNE étape" in s:
            n = int(s.split("Étape ")[1].split(" ")[0])
            log.append(f"step{n}")
            return step_reply(n, s)
        if "vérificateur rigoureux" in s:
            log.append("verify")
            return verify
        if "Tu gères la mémoire" in s:
            return "[]"
        log.append("final")
        return final
    return script


PLAN3 = json.dumps({"objectif": "Préparer le projet de treuil", "etapes": [
    {"titre": "Pièces", "consigne": "Lister les pièces à dimensionner", "depend_de": []},
    {"titre": "Couple", "consigne": "Calculer le couple moteur", "depend_de": [1]},
    {"titre": "Pièces bis", "consigne": "Lister les pièces à dimensionner", "depend_de": []},   # doublon : ignoré
    {"titre": "Planning", "consigne": "Planning sur trois semaines", "depend_de": [2, 9, 4]},  # 9 et 4 : invalides
]}, ensure_ascii=False)


class Analysis(unittest.TestCase):
    def test_complexity_levels(self):
        self.assertEqual(assess("Salut !", "quick", small_talk=True)[0], "simple")
        self.assertEqual(assess("Quelle est la capitale du Japon ?", "quick")[0], "simple")
        self.assertEqual(assess("Combien font 17 × 3 ?", "quick")[0], "moyenne")
        self.assertEqual(assess("Corrige ce bug dans ma fonction Python", "code")[0], "moyenne")
        level, reasons = assess(COMPLEX, "reasoning")
        self.assertEqual(level, "complexe")
        self.assertTrue(any("points listés" in r for r in reasons))

    def test_objective(self):
        self.assertEqual(objective("Bonjour, peux-tu m'aider à réviser la RDM ? J'ai un partiel."),
                         "peux-tu m'aider à réviser la RDM ?")


class Orchestration(Base):
    def test_decomposed_task_runs_steps_in_order_and_verifies(self):
        log = []

        def step_reply(n, s):
            if n == 2:  # l'étape 2 dépend de la 1 : elle reçoit son résultat
                assert "Résultat pièces : arbre, tambour" in s, s
            return {1: "Résultat pièces : arbre, tambour", 2: "Résultat couple : C = 49 N·m", 3: "Résultat planning : S1 S2 S3"}[n]

        f = self.fake("anthropic", steps_script(PLAN3, step_reply, log=log))
        app = make_app(self.tmp, {"anthropic": f}, default_model="anthropic:modele-a")
        c = app.store.create_conversation()
        ev = run_chat(app, c["id"], COMPLEX)
        an = [e for e in ev if e["type"] == "analysis"][0]
        self.assertEqual(an["complexity"], "complexe")
        self.assertEqual(an["strategy"], "décomposée")
        self.assertTrue(an["verify"])
        plan = [e for e in ev if e["type"] == "steps_plan"][0]
        self.assertEqual([s["title"] for s in plan["steps"]], ["Pièces", "Couple", "Planning"])
        self.assertEqual(plan["steps"][2]["depends"], [2])
        self.assertEqual(log, ["plan", "step1", "step2", "step3", "final", "verify"])
        final_body = [r[3] for r in f.requests if r[0] == "POST" and "Résultat planning" in blob(r[3])
                      and "Tu exécutes UNE étape" not in blob(r[3])][0]
        self.assertIn("Résultat couple : C = 49 N·m", final_body["system"])
        self.assertEqual(text_of(ev), "Synthèse finale.")
        msg = app.store.get_conversation(c["id"])["messages"][-1]
        self.assertEqual([s["state"] for s in msg["meta"]["steps"]], ["terminée"] * 3)
        self.assertEqual(msg["meta"]["analysis"]["strategy"], "décomposée")
        run = app.store.runs()[0]
        self.assertEqual(run["strategy"], "décomposée")
        self.assertEqual(run["steps"], 3)
        self.assertEqual(run["calls"], 6)  # plan + 3 étapes + synthèse + vérification
        self.assertEqual(run["verified"], 1)
        self.assertTrue(run["ok"])
        self.assertEqual(run["message_id"], msg["id"])
        st = app.store.run_stats()
        self.assertEqual(st["by_strategy"]["décomposée"]["n"], 1)
        app.store.close()

    def test_repeated_step_is_discarded(self):
        f = self.fake("openai", steps_script(PLAN3, lambda n, s: "Toujours exactement le même texte produit par le modèle."))
        app = make_app(self.tmp, {"openai": f}, default_model="openai:modele-a")
        c = app.store.create_conversation()
        ev = run_chat(app, c["id"], COMPLEX)
        states = [e["state"] for e in ev if e["type"] == "step" and e["state"] != "en cours"]
        self.assertEqual(states, ["terminée", "redondante", "redondante"])
        app.store.close()

    def test_call_budget_stops_runaway_execution(self):
        f = self.fake("openai", steps_script(PLAN3, lambda n, s: f"Résultat distinct numéro {n} " + "x" * n))
        app = make_app(self.tmp, {"openai": f}, default_model="openai:modele-a", orchestrator_max_calls=4)
        c = app.store.create_conversation()
        ev = run_chat(app, c["id"], COMPLEX)
        states = [e["state"] for e in ev if e["type"] == "step" and e["state"] != "en cours"]
        self.assertEqual(states, ["terminée", "non exécutée", "non exécutée"])
        self.assertEqual(ev[-1]["type"], "done")  # la réponse finale est quand même produite
        app.store.close()

    def test_two_failed_steps_abort_and_fall_back(self):
        f = self.fake("ollama", steps_script(PLAN3, lambda n, s: ""))
        app = make_app(self.tmp, {"ollama": f}, default_model="ollama:modele-a")
        c = app.store.create_conversation()
        ev = run_chat(app, c["id"], COMPLEX)
        strat = [e for e in ev if e["type"] == "strategy"][0]
        self.assertEqual(strat["strategy"], "contextuelle")
        self.assertIn("deux étapes ont échoué", strat["note"])
        self.assertEqual(text_of(ev), "Synthèse finale.")
        app.store.close()

    def test_unusable_plan_falls_back(self):
        f = self.fake("gemini", steps_script("Je ne sais pas faire de plan.", lambda n, s: "x"))
        app = make_app(self.tmp, {"gemini": f}, default_model="gemini:modele-a", orchestrator_verify="jamais")
        c = app.store.create_conversation()
        ev = run_chat(app, c["id"], COMPLEX)
        self.assertEqual([e for e in ev if e["type"] == "strategy"][0]["strategy"], "contextuelle")
        self.assertFalse([e for e in ev if e["type"] == "step"])
        app.store.close()

    def test_decomposition_can_be_disabled(self):
        log = []
        f = self.fake("anthropic", steps_script(PLAN3, lambda n, s: "x", log=log))
        app = make_app(self.tmp, {"anthropic": f}, default_model="anthropic:modele-a", orchestrator_decompose="jamais")
        c = app.store.create_conversation()
        ev = run_chat(app, c["id"], COMPLEX)
        self.assertEqual([e for e in ev if e["type"] == "analysis"][0]["strategy"], "contextuelle")
        self.assertEqual(log, ["final"])
        app.store.close()

    def test_simple_request_costs_a_single_call(self):
        f = self.fake("anthropic", lambda b: "Tokyo.")
        app = make_app(self.tmp, {"anthropic": f}, default_model="anthropic:modele-a")
        c = app.store.create_conversation()
        ev = run_chat(app, c["id"], "Quelle est la capitale du Japon ?")
        an = [e for e in ev if e["type"] == "analysis"][0]
        self.assertEqual((an["complexity"], an["strategy"], an["verify"]), ("simple", "contextuelle", False))
        self.assertEqual(len([r for r in f.requests if r[0] == "POST"]), 1)
        self.assertEqual(app.store.runs()[0]["calls"], 1)
        app.store.close()

    def test_controls_flag_invented_sources_and_fake_actions(self):
        f = self.fake("anthropic", lambda b: "C'est fait, j'ai envoyé le mail à ton tuteur [S4].")
        app = make_app(self.tmp, {"anthropic": f}, default_model="anthropic:modele-a")
        c = app.store.create_conversation()
        ev = run_chat(app, c["id"], "Envoie le mail à mon tuteur")
        items = [e for e in ev if e["type"] == "controls"][0]["items"]
        self.assertEqual(items[0]["level"], "erreur")
        self.assertIn("[S4]", items[0]["text"])
        self.assertIn("j'ai envoyé", items[1]["text"])
        self.assertFalse(app.store.runs()[0]["ok"])
        self.assertEqual(Orchestrator.controls("Voir [S1].", [{"n": 1}]), [])
        app.store.close()

    def test_repeated_question_and_app_tool(self):
        f = self.fake("anthropic", lambda b: "Réponse.")
        app = make_app(self.tmp, {"anthropic": f}, default_model="anthropic:modele-a",
                       apps=[{"name": "Calculatrice", "target": "calc.exe"}])
        c = app.store.create_conversation()
        run_chat(app, c["id"], "Comment fonctionne un réducteur à engrenages ?")
        ev = run_chat(app, c["id"], "Comment fonctionne un réducteur à engrenages")
        an = [e for e in ev if e["type"] == "analysis"][0]
        self.assertIsNotNone(an["repeated"])
        self.assertIn("Ne répète pas ta réponse précédente", f.last_body()["system"])
        ev = run_chat(app, c["id"], "Lance la calculatrice")
        tool = [e for e in ev if e["type"] == "analysis"][0]["tools"][0]
        self.assertEqual((tool["tool"], tool["name"], tool["needs_confirmation"]), ("application", "Calculatrice", True))
        app.store.close()

    def test_interrupted_answer_is_traced(self):
        f = self.fake("ollama", lambda b: "Un deux trois quatre cinq six sept huit neuf dix")
        app = make_app(self.tmp, {"ollama": f})
        c = app.store.create_conversation()
        gen = app.brain.chat(c["id"], "Compte jusqu'à dix")
        for e in gen:
            if e["type"] == "delta":
                break
        gen.close()
        run = app.store.runs()[0]
        self.assertFalse(run["ok"])
        self.assertTrue(run["message_id"])
        app.store.close()


class SmartMemory(Base):
    def setUp(self):
        super().setUp()
        self.s = mem.Store(Path(self.tmp) / "t.db")

    def tearDown(self):
        self.s.close()
        super().tearDown()

    def test_old_database_is_migrated(self):
        self.s.close()
        path = Path(self.tmp) / "old.db"
        db = sqlite3.connect(str(path))
        db.executescript("""CREATE TABLE memories(id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT NOT NULL,
          category TEXT NOT NULL, importance INTEGER NOT NULL DEFAULT 3, kind TEXT NOT NULL DEFAULT 'durable',
          source TEXT NOT NULL DEFAULT 'manuel', title TEXT, created REAL NOT NULL, updated REAL NOT NULL,
          expires_at REAL, last_used REAL, use_count INTEGER NOT NULL DEFAULT 0, vec_local BLOB, vec_ext BLOB, vec_model TEXT);""")
        for content, cat, kind in (("Projet treuil", "projets", "durable"), ("Aime le café", "préférences", "durable"),
                                   ("Utilise SolidWorks 2024", "connaissances", "durable"), ("Oral jeudi", "tâches", "temporaire")):
            db.execute("INSERT INTO memories(content,category,kind,created,updated) VALUES(?,?,?,1,1)", (content, cat, kind))
        db.commit()
        db.close()
        self.s = mem.Store(path)
        got = {m["content"]: m["nature"] for m in self.s.list_memories()}
        self.assertEqual(got, {"Projet treuil": "projet", "Aime le café": "personnelle",
                               "Utilise SolidWorks 2024": "technique", "Oral jeudi": "temporaire"})

    def test_versions_keep_history_and_restore(self):
        r = self.s.add_memory("L'utilisateur habite à Limoges", "profil", 4)
        self.s.update_memory(r["id"], {"content": "L'utilisateur habite à Bordeaux"})
        self.s.update_memory(r["id"], {"importance": 4})  # rien ne change : pas de version inutile
        vs = self.s.versions(r["id"])
        self.assertEqual([v["content"] for v in vs], ["L'utilisateur habite à Limoges"])
        self.s.restore_version(r["id"], vs[0]["id"])
        self.assertEqual(self.s.get_memory(r["id"])["content"], "L'utilisateur habite à Limoges")
        self.assertEqual(len(self.s.versions(r["id"])), 2)
        self.assertTrue(self.s.search_memories("Limoges"))
        self.s.delete_memory(r["id"])
        last = self.s.versions(r["id"])[0]
        self.assertEqual(last["reason"], "supprimé")
        self.assertEqual([d["memory_id"] for d in self.s.deleted_memories()], [r["id"]])
        self.s.restore_version(r["id"], last["id"])  # un souvenir supprimé peut être récupéré
        self.assertEqual(self.s.get_memory(r["id"])["content"], "L'utilisateur habite à Limoges")
        self.assertTrue(self.s.search_memories("Limoges"))
        self.assertEqual(self.s.deleted_memories(), [])

    def test_rephrased_queries_find_memories(self):
        self.s.add_memory("L'utilisateur fait son stage dans une entreprise d'usinage", "projets", 3)
        self.s.add_memory("L'utilisateur roule dans une voiture électrique", "profil", 3)
        self.s.add_memory("L'utilisateur préfère les explications avec un exemple chiffré", "préférences", 4)
        self.assertIn("usinage", self.s.search_memories("Où est-ce que je bosse ?")[0]["content"])
        self.assertIn("voiture", self.s.search_memories("Parle-moi de ma bagnole")[0]["content"])
        self.assertIn("électrique", self.s.search_memories("électriques")[0]["content"])
        self.assertEqual(self.s.search_memories("Quelle est la capitale du Pérou ?"), [])
        # une préférence importante accompagne toutes les demandes
        res = self.s.search_memories("Explique la loi d'Ohm", always_core=True)
        self.assertTrue(any("exemple chiffré" in m["content"] for m in res))

    def test_proposals(self):
        r = self.s.add_memory("L'utilisateur vise un stage en bureau d'études", "projets", 3)
        pid = self.s.add_proposal(r["id"], "L'utilisateur a trouvé un stage chez Legrand", "projets", 4, "durable",
                                  None, "projet", "stage", "il annonce avoir trouvé")
        self.assertEqual(self.s.add_proposal(r["id"], "L'utilisateur a trouvé un stage chez Legrand", "projets", 4,
                                             "durable", None, "projet", "stage", "doublon"), pid)
        self.assertEqual(self.s.get_memory(r["id"])["content"], "L'utilisateur vise un stage en bureau d'études")
        self.s.decide_proposal(pid, True)
        m = self.s.get_memory(r["id"])
        self.assertEqual((m["content"], m["project"]), ("L'utilisateur a trouvé un stage chez Legrand", "stage"))
        self.assertEqual(self.s.versions(r["id"])[0]["content"], "L'utilisateur vise un stage en bureau d'études")
        with self.assertRaises(ValueError):
            self.s.decide_proposal(pid, False)
        pid2 = self.s.add_proposal(r["id"], "Autre", "projets", 3, "durable", None, "projet", None, "x")
        self.s.decide_proposal(pid2, False)
        self.assertEqual(self.s.get_memory(r["id"])["content"], "L'utilisateur a trouvé un stage chez Legrand")
        self.assertEqual(self.s.stats()["proposals"], 0)

    def test_habits_are_computed_not_guessed(self):
        c = self.s.create_conversation()
        self.s.add_message(c["id"], "user", "bonjour")
        self.assertEqual(self.s.observed_habits()["summary"], "")  # trop tôt pour généraliser
        evening = time.mktime((2026, 11, 10, 21, 0, 0, 0, 0, -1))
        for i in range(24):
            mid = self.s.add_message(c["id"], "user", "Corrige ma fonction Python " + "x" * 40)
            self.s.db.execute("UPDATE messages SET created=? WHERE id=?", (evening + i * 60, mid))
            self.s.add_message(c["id"], "assistant", "ok", "x:y", {"route": "code"})
        self.s.db.commit()
        h = self.s.observed_habits()
        self.assertTrue(h["enough"])
        self.assertIn("le soir", h["summary"])
        self.assertIn("programmation (100 %)", h["summary"])


class SmartMemoryInConversation(Base):
    def extract_script(self, items, answer="D'accord."):
        def script(b):
            s = blob(b)
            if "Tu gères la mémoire" in s:
                return json.dumps(items, ensure_ascii=False)
            if "mots-clés, synonymes" in s:
                return '["logement", "Limoges"]'
            return answer
        return script

    def test_update_is_proposed_then_accepted(self):
        f = self.fake("anthropic", lambda b: "")
        app = make_app(self.tmp, {"anthropic": f}, default_model="anthropic:modele-a", memory_auto_extract=True)
        old = app.store.add_memory("L'utilisateur habite à Limoges", "profil", 4)
        f.script = self.extract_script([{"contenu": "L'utilisateur habite à Bordeaux depuis novembre", "categorie": "profil",
                                         "nature": "personnelle", "importance": 4, "type": "durable",
                                         "remplace": old["id"], "raison": "il a déménagé"}])
        c = app.store.create_conversation()
        ev = run_chat(app, c["id"], "J'ai déménagé, j'habite à Bordeaux maintenant")
        m = [e for e in ev if e["type"] == "memories"][0]
        self.assertEqual(m["saved"], [])
        self.assertEqual(m["proposed"][0]["previous"], "L'utilisateur habite à Limoges")
        self.assertEqual(app.store.get_memory(old["id"])["content"], "L'utilisateur habite à Limoges")  # rien d'écrasé
        prompt = [r[3] for r in f.requests if r[0] == "POST" and "Tu gères la mémoire" in blob(r[3])][0]
        self.assertIn(f"#{old['id']} L'utilisateur habite à Limoges", blob(prompt))
        app.store.decide_proposal(m["proposed"][0]["id"], True)
        self.assertIn("Bordeaux", app.store.get_memory(old["id"])["content"])
        app.store.close()

    def test_update_applied_automatically_keeps_history(self):
        f = self.fake("anthropic", lambda b: "")
        app = make_app(self.tmp, {"anthropic": f}, default_model="anthropic:modele-a", memory_auto_extract=True,
                       memory_update_mode="auto")
        old = app.store.add_memory("L'utilisateur utilise SolidWorks 2022", "connaissances", 3)
        f.script = self.extract_script([
            {"contenu": "L'utilisateur utilise SolidWorks 2025", "categorie": "connaissances", "nature": "technique",
             "remplace": old["id"], "raison": "mise à niveau"},
            {"contenu": "L'utilisateur travaille sur le projet de treuil avec Hugo", "categorie": "projets",
             "nature": "projet", "projet": "Treuil", "remplace": 999999},   # numéro inconnu : nouveau souvenir
            {"contenu": "Ok ?", "categorie": "divers"}])                    # trop court : ignoré
        c = app.store.create_conversation()
        ev = run_chat(app, c["id"], "Je suis passé à SolidWorks 2025, et je bosse sur le treuil avec Hugo")
        m = [e for e in ev if e["type"] == "memories"][0]
        self.assertEqual(m["updated"][0]["content"], "L'utilisateur utilise SolidWorks 2025")
        self.assertEqual(len(m["saved"]), 1)
        self.assertEqual((m["saved"][0]["nature"], m["saved"][0]["project"]), ("projet", "Treuil"))
        v = app.store.versions(old["id"])[0]
        self.assertEqual(v["content"], "L'utilisateur utilise SolidWorks 2022")
        self.assertTrue(v["reason"].startswith("mis à jour automatiquement"))
        self.assertEqual(app.store.projects()[0]["project"], "Treuil")
        app.store.close()

    def test_rephrased_search_by_model(self):
        f = self.fake("anthropic", lambda b: "")
        app = make_app(self.tmp, {"anthropic": f}, default_model="anthropic:modele-a")
        app.store.add_memory("Le logement actuel est un studio à Limoges, rue Jean Jaurès", "divers", 3)
        f.script = self.extract_script([], answer="Tu vis dans un studio à Limoges.")
        c = app.store.create_conversation()
        ev = run_chat(app, c["id"], "Rappelle-moi où je réside")
        meta = [e for e in ev if e["type"] == "meta"][0]
        self.assertEqual(meta["memories"][0]["found_by"], "reformulation")
        self.assertIn("studio à Limoges", f.last_body()["system"])
        app.settings.update({"memory_query_expansion": False})
        ev = run_chat(app, c["id"], "Rappelle-moi encore où je réside, s'il te plaît")
        self.assertEqual([e for e in ev if e["type"] == "meta"][0]["memories"], [])
        app.store.close()

    def test_habits_reach_the_model(self):
        f = self.fake("anthropic", lambda b: "ok")
        app = make_app(self.tmp, {"anthropic": f}, default_model="anthropic:modele-a")
        c = app.store.create_conversation()
        for i in range(21):
            app.store.add_message(c["id"], "user", f"question {i}")
        run_chat(app, c["id"], "Encore une question")
        self.assertIn("Habitudes observées", f.last_body()["system"])
        app.settings.update({"habits_in_context": False})
        run_chat(app, c["id"], "Et une autre")
        self.assertNotIn("Habitudes observées", f.last_body()["system"])
        app.store.close()


class BrainHttp(Base):
    def setUp(self):
        super().setUp()
        self.f = self.fake("anthropic", lambda b: "Réponse.")
        self.app = make_app(self.tmp, {"anthropic": self.f}, default_model="anthropic:modele-a")
        self.httpd = serve(self.app, 0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.app.store.close()
        super().tearDown()

    def call(self, method, path, body=None):
        import urllib.error
        import urllib.request
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method,
                                     headers={"Content-Type": "application/json", "X-Jarvis-Token": self.app.token},
                                     data=json.dumps(body).encode() if body is not None else None)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=20) as r:
                raw = r.read().decode("utf-8")
                return r.status, (raw if raw.startswith("data:") else json.loads(raw or "null"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def test_brain_and_memory_routes(self):
        cid = self.call("POST", "/api/conversations", {})[1]["id"]
        self.call("POST", "/api/chat", {"conversation_id": cid, "message": "Bonjour"})
        code, brain = self.call("GET", "/api/brain")
        self.assertEqual(code, 200)
        self.assertEqual(brain["stats"]["runs"], 1)
        self.assertEqual(brain["runs"][0]["strategy"], "directe")
        self.assertIn("habits", brain)
        m = self.call("POST", "/api/memories", {"content": "L'utilisateur code en Python", "category": "connaissances",
                                                "project": "Jarvis"})[1]
        self.assertEqual((m["nature"], m["project"]), ("technique", "Jarvis"))
        self.assertEqual(self.call("GET", "/api/memories?nature=technique")[1][0]["id"], m["id"])
        self.assertEqual(self.call("GET", "/api/memories?project=Autre")[1], [])
        self.call("PATCH", f"/api/memories/{m['id']}", {"content": "L'utilisateur code en Python et en TypeScript"})
        v = self.call("GET", f"/api/memories/{m['id']}/versions")[1]
        self.assertEqual(v["versions"][0]["content"], "L'utilisateur code en Python")
        self.call("POST", f"/api/memories/{m['id']}/restore", {"version_id": v["versions"][0]["id"]})
        self.assertEqual(self.app.store.get_memory(m["id"])["content"], "L'utilisateur code en Python")
        pid = self.app.store.add_proposal(m["id"], "L'utilisateur code en Rust", "connaissances", 3, "durable", None,
                                          "technique", "Jarvis", "test")
        self.assertEqual(self.call("GET", "/api/state")[1]["stats"]["proposals"], 1)
        self.assertEqual(self.call("POST", f"/api/memories/proposals/{pid}", {"action": "n'importe"})[0], 400)
        code, res = self.call("POST", f"/api/memories/proposals/{pid}", {"action": "accepter"})
        self.assertEqual(res["memory"]["content"], "L'utilisateur code en Rust")
        self.assertEqual(self.call("GET", "/api/memories/projects")[1][0]["project"], "Jarvis")
        st = self.call("POST", "/api/settings", {"memory_update_mode": "n'importe", "orchestrator_max_calls": 999})[1]
        self.assertEqual(st["settings"]["memory_update_mode"], "valider")
        self.assertEqual(st["settings"]["orchestrator_max_calls"], 40)


if __name__ == "__main__":
    unittest.main()
