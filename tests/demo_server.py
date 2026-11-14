"""Lance Jarvis sur un dossier temporaire avec les faux fournisseurs (pour le test de l'interface)."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes import Fake, FakeScience, FakeWeb  # noqa: E402
from jarvis_core.collect import Wikipedia  # noqa: E402
from jarvis_core.providers import REGISTRY  # noqa: E402
from jarvis_core.server import App, serve  # noqa: E402


def script(body):
    s = json.dumps(body, ensure_ascii=False)
    # --- cerveau central : tâche complexe découpée en étapes
    if "planificateur d'un assistant" in s:
        return json.dumps({"objectif": "Organiser les révisions du partiel de RDM", "etapes": [
            {"titre": "Chapitres à revoir", "consigne": "Lister les chapitres de RDM du partiel", "depend_de": []},
            {"titre": "Planning sur deux semaines", "consigne": "Répartir les chapitres sur deux semaines", "depend_de": [1]},
            {"titre": "Exercices types", "consigne": "Proposer trois exercices types", "depend_de": [1]}]}, ensure_ascii=False)
    if "Tu exécutes UNE étape" in s:
        n = s.split("Étape ")[1].split(" ")[0]
        return {"1": "Traction, flexion, torsion, flambage.", "2": "Semaine 1 : traction et flexion. Semaine 2 : torsion et flambage.",
                "3": "1) Poutre en flexion 2) Arbre en torsion 3) Flambage d'Euler"}[n]
    if "vérificateur rigoureux" in s and "plan de révision" in s:
        return '{"ok": true, "problemes": []}'
    if "<etapes>" in s:
        return "Voici votre **plan de révision** : traction et flexion la première semaine, torsion et flambage la seconde."
    if "Tu gères la mémoire" in s:
        import re
        ex = s.split("Échange :")[-1]
        known = re.search(r"#(\d+) L'utilisateur est étudiant en BUT GMP", s)
        if "deuxième année" in ex and known:
            return json.dumps([{"contenu": "L'utilisateur est étudiant en deuxième année de BUT GMP à Limoges",
                                "categorie": "profil", "nature": "personnelle", "importance": 5, "type": "durable",
                                "remplace": int(known.group(1)), "raison": "il est passé en deuxième année"}],
                              ensure_ascii=False)
        if "BUT GMP" in ex:
            return ('[{"contenu": "L\'utilisateur est étudiant en BUT GMP à Limoges", "categorie": "profil", '
                    '"importance": 5, "type": "durable", "jours": null}]')
        return "[]"
    if "Tu prépares une recherche documentaire" in s:
        if "Young" in s:
            return json.dumps({"fr": ["module Young acier"], "en": ["Young modulus steel"]})
        return json.dumps({"fr": ["flambage poutre"], "en": ["beam buckling"]})
    if "Tu es un documentaliste rigoureux" in s:
        import re
        n = {t: int(k) for k, t in re.findall(r"\[(\d+)\] (.+?) — ", body["messages"][-1]["content"])}
        w, h, o = n.get("Module de Young"), n.get("Caractérisation élastique des aciers"), n.get("Elastic properties of steel")
        out = []
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
        return json.dumps(out, ensure_ascii=False)
    if "Classe ce document" in s:
        return "Matériaux et métallurgie"
    if "Relève les relations" in s:
        return json.dumps([
            {"source": "acier", "type_source": "matériau", "relation": "a pour propriété", "cible": "résistance à la traction"},
            {"source": "résistance à la traction", "relation": "est mesurée par", "cible": "essai de traction", "type_cible": "procédé"},
            {"source": "acier", "type_source": "matériau", "relation": "a pour propriété", "cible": "module de Young", "type_cible": "formule"}],
            ensure_ascii=False)
    if "<connaissances>" in s and "Young" in s:
        return "D'après votre cours, le module de Young de l'acier vaut environ **210 GPa** [S1]."
    if "Décompose la demande" in s:
        return "1. Rappeler la formule du couple\n2. Convertir les unités\n3. Calculer et conclure"
    if "vérificateur rigoureux" in s:
        return '{"ok": false, "problemes": ["Le rayon doit être converti en mètres : 60 mm = 0,06 m"]}'
    if "Un contrôle a relevé" in s:
        return "Avec **r = 0,06 m** : C = F × r = 2000 × 0,06 = **120 N·m**."
    if "couple" in s:
        return "C = F × r = 2000 × 60 = **120 000 N·m**."
    if "BUT GMP" in s and "<memoire>" in s:
        return "Oui : vous êtes étudiant en **BUT GMP à Limoges**.\n\n<script>alert('xss')</script>"
    return ("Bien noté.\n\nVoici un exemple :\n\n```python\nprint('Bonjour')\n```\n\n"
            "- point un\n- point deux")


def main():
    home = Path(tempfile.mkdtemp(prefix="jarvis-demo-"))
    fa, fo = Fake("anthropic", script, models=("claude-test-grand", "claude-test-rapide")), \
        Fake("ollama", script, models=("llama-test:8b",))
    web = FakeWeb()
    web.articles = {"Soudage": "Le soudage est un procédé d'assemblage permanent.\n\n== Procédés ==\nTIG et MIG-MAG.",
                    "Flambage": "Le flambage est une instabilité d'une poutre comprimée, décrite par la formule d'Euler."}
    web.articles["Module de Young"] = (
        "Le module de Young est la constante qui relie la contrainte de traction et la déformation d'un solide "
        "élastique. Le module de Young de l'acier vaut environ 210 GPa. Il caractérise la rigidité.")
    sci = FakeScience()
    sci.hal = [{"id": "hal-01", "title": "Caractérisation élastique des aciers", "abstract": (
        "Nous mesurons le module de Young de l'acier : 210 GPa en moyenne sur douze éprouvettes de traction. "
        "La rigidité dépend peu de la nuance d'acier étudiée.")}]
    sci.openalex = [{"id": "W1", "title": "Elastic properties of steel", "abstract": (
        "The Young modulus of steel is about 210 GPa at room temperature and decreases with temperature in a "
        "nearly linear way up to 400 degrees.")}]
    sci.arxiv = []
    bibli = home / "bibli"
    bibli.mkdir()
    (bibli / "fraisage.md").write_text(
        "Le fraisage est un procédé d'usinage par enlèvement de copeaux. La vitesse de coupe dépend de la fraise "
        "et de la matière usinée. Vitesse de coupe de l'aluminium : 300 m/min.", encoding="utf-8")
    (bibli / "atelier.txt").write_text(
        "Consignes de l'atelier : porter les lunettes, ranger les fraises après usinage, nettoyer les copeaux.",
        encoding="utf-8")
    app = App(home, {"wikipedia": Wikipedia(web.url + "/w/api.php", True), "allow_private": True,
                     "endpoints": sci.endpoints(), "pace_seconds": 0})
    app.settings.update({"providers": {
        "anthropic": {"base_url": fa.url}, "ollama": {"base_url": fo.url},
        "openai": {"enabled": False}, "gemini": {"enabled": False}}})
    app.settings.update({"apps": [{"name": "Calculatrice", "target": "calc-test"}]})
    app.app_runner = lambda target: None   # aucun programme n'est réellement lancé pendant le test
    httpd = serve(app, 0)
    print(httpd.server_address[1], flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
