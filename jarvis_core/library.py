"""Bibliothèque technique : extraction de formules, tableaux et données chiffrées, classement sans modèle,
repérage des versions d'un même document et des données contradictoires.

Tout est déterministe et vérifiable : rien n'est déduit qui ne figure pas dans le texte.
"""
import re

from .memory import normalize, tokens

# ---------------------------------------------------------------- classement par lexique
# Mots (sans accents) qui signalent une sous-catégorie. Plusieurs mots d'un même domaine doivent apparaître
# pour qu'un document y soit classé : un mot isolé ne suffit jamais.
LEXICON = {
    "Mathématiques": "derivee integrale matrice equation polynome vecteur probabilite theoreme trigonometrie "
                     "algebre statistique logarithme exponentielle primitive determinant suite_numerique",
    "Physique": "force energie vitesse acceleration onde optique gravitation quantique electromagnetisme "
                "relativite photon masse_volumique",
    "Mécanique": "statique dynamique cinematique torseur principe_fondamental frottement inertie equilibre "
                 "moment_d_une_force moment_cinetique pfd",
    "Thermodynamique": "thermodynamique entropie enthalpie chaleur temperature cycle_de_carnot gaz_parfait "
                       "transfert_thermique conduction convection",
    "Chimie": "molecule atome reaction_chimique oxydation reduction acide ion covalente chimique stoechiometrie",
    "Matériaux et métallurgie": "acier alliage aluminium fonte metallurgie trempe durete composite polymere "
                                "inoxydable laiton cuivre titane recuit austenite martensite ferrite corrosion",
    "Électricité et électronique": "tension courant condensateur transistor diode circuit ohm electronique "
                                   "inductance microcontroleur arduino moteur_electrique electrique",
    "Astronomie": "planete etoile galaxie orbite telescope astronomie",
    "Biologie": "cellule adn organisme proteine gene biologie",
    "Génie mécanique": "liaison roulement engrenage arbre clavette accouplement reducteur transmission guidage "
                       "cotation ajustement tolerance visserie ressort poulie treuil mecanisme pignon",
    "CAO, FAO, DAO": "cao dao fao esquisse modelisation_3d assemblage mise_en_plan extrusion conception_assistee "
                     "maquette_numerique",
    "Logiciels de CAO (FreeCAD, CATIA, SolidWorks)": "freecad catia solidworks inventor fusion_360 part_design "
                                                    "sketcher workbench atelier_part",
    "CNC et programmation ISO": "cnc commande_numerique gcode code_g programme_iso origine_piece fanuc sinumerik "
                                "heidenhain post_processeur g01 g02 g03",
    "Impression 3D": "impression_3d filament pla petg slicer buse plateau fdm sla cura prusa",
    "Usinage": "usinage fraisage fraise tournage percage foret vitesse_de_coupe avance copeau rectification "
               "alesage taraudage plaquette outil_coupant",
    "Soudage, tôlerie, chaudronnerie": "soudage soudure tig mig mag electrode chaudronnerie tolerie pliage "
                                       "cintrage tole",
    "Résistance des matériaux": "rdm contrainte deformation traction compression flexion torsion cisaillement "
                                "flambage module_de_young elasticite moment_quadratique poutre fleche "
                                "effort_tranchant moment_flechissant von_mises coefficient_de_securite",
    "Simulation (FEM, CFD, fatigue, vibrations)": "elements_finis maillage fem cfd simulation fatigue vibration "
                                                  "frequence_propre ansys abaqus comsol",
    "Normes (ISO, DIN, EN)": "norme iso din nf_en afnor normalisation normatif",
    "Maintenance industrielle": "maintenance panne preventive curative amdec gmao lubrification",
    "Automatisme et robotique": "automate grafcet robot robotique capteur actionneur asservissement pid plc "
                                "scara cinematique_inverse ladder",
    "Métrologie": "metrologie incertitude pied_a_coulisse micrometre etalonnage rugosite comparateur "
                  "tolerance_geometrique mmt",
    "Langages de programmation": "python javascript typescript java rust langage syntaxe compilateur "
                                 "programmation fonction variable boucle",
    "Développement web et logiciel": "html css react frontend backend framework git test_unitaire",
    "Architecture informatique": "processeur memoire_vive cpu gpu architecture_informatique",
    "Réseaux et systèmes": "reseau tcp routeur linux systeme_d_exploitation dns protocole",
    "Cybersécurité défensive": "cybersecurite chiffrement pare_feu authentification vulnerabilite phishing",
    "Intelligence artificielle": "intelligence_artificielle apprentissage_automatique machine_learning "
                                 "reseau_de_neurones deep_learning apprentissage_profond",
    "LLM, RAG, embeddings": "llm modele_de_langage rag embedding embeddings plongement transformer prompt token",
    "Agents autonomes": "agent_autonome agents_autonomes orchestration",
    "Bases de données et API": "sql base_de_donnees requete sqlite postgresql api_rest json",
    "Cloud et DevOps": "cloud docker kubernetes deploiement devops conteneur",
    "Histoire et géographie": "histoire guerre siecle empire royaume geographie capitale",
    "Économie et gestion": "economie entreprise gestion budget comptabilite inflation finance",
    "Langues et traduction": "grammaire traduction vocabulaire conjugaison",
    "Littérature et philosophie": "roman poeme litterature philosophie",
    "Droit et réglementation": "droit loi reglementation contrat juridique rgpd",
    "Environnement et énergie": "environnement energie_renouvelable climat carbone eolien photovoltaique pollution",
    "Cuisine et recettes": "recette cuisine farine oeuf cuisson ingredient sucre beurre",
    "Sport et entraînement": "sport entrainement musculation footing seance fractionne badminton",
    "Automobile": "voiture automobile vidange pneu embrayage boite_de_vitesses",
    "Bricolage": "bricolage perceuse cheville peinture",
    "Informatique personnelle": "ordinateur imprimante wifi",
    "Organisation et productivité": "organisation planning productivite agenda priorite",
}
_LEX = {cat: [w.replace("_", " ") for w in words.split()] for cat, words in LEXICON.items()}


def _text_key(text):
    return " " + " ".join(re.findall(r"[a-z0-9]+", normalize(text))) + " "


def classify_text(title, text, min_score=4.0, question=False):
    """Sous-catégorie la plus probable d'après le lexique -> (nom, score, mots trouvés) ou (None, 0, [])."""
    head, body = _text_key(title), _text_key(text[:6000])
    scores = []
    for cat, words in _LEX.items():
        found, s = [], 0.0
        for w in words:
            k = f" {w} "
            # pluriel simple accepté (aciers, engrenages)
            in_head = k in head or f" {w}s " in head
            n_body = body.count(k) + body.count(f" {w}s ")
            if in_head or n_body:
                found.append(w)
                weight = 1.5 if " " in w else 1.0   # une expression (« module de young ») est plus sûre qu'un mot seul
                s += weight * ((3.0 if in_head else 0.0) + (1.0 + 0.3 * min(n_body - 1, 4) if n_body else 0.0))
        if found:
            # dans un document, un mot isolé ne suffit pas ; dans une question (courte), il compte
            scores.append((s if len(found) >= 2 or question else s * 0.5, cat, found))
    if not scores:
        return None, 0.0, []
    scores.sort(reverse=True)
    best = scores[0]
    second = scores[1][0] if len(scores) > 1 else 0.0
    if best[0] >= min_score and best[0] >= (1.2 if question else 1.4) * second:
        return best[1], round(best[0], 1), best[2]
    return None, 0.0, []


# ---------------------------------------------------------------- formules, tableaux, données
UNITS = (r"GPa|MPa|kPa|hPa|Pa|kN\.m|kN·m|N\.m|N·m|Nm|kN|daN|N|mm²|mm2|cm²|m²|m³|mm|cm|µm|μm|nm|km|m/s²|m/s|km/h|"
         r"kg/m³|kg/m3|g/cm³|kg|g|t|°C|K|kJ|J|kWh|Wh|kW|W|mV|kV|V|mA|A|Ω|ohms?|kHz|MHz|Hz|tr/min|rpm|bar|%|"
         r"HV|HRC|HB|ms|s|min|h|L|l|dB")
_NUM = r"-?\d+(?:[.,]\d+)?(?:\s?[x×·]\s?10\^?[-−]?\d+)?"
_DATA = re.compile(
    r"(?P<label>[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ0-9'’\- ]{2,70}?)\s*"
    r"(?::|=|≈|vaut|valent|est d['e]|sont d['e]|égale?|egale?|de l'ordre de|atteint|atteignent)\s*"
    r"(?:(?:environ|approximativement|à peu près|de l'ordre de|près de|presque)\s+|≈\s*|~\s*)?"
    rf"(?P<value>{_NUM})\s*(?P<unit>{UNITS})(?![A-Za-zÀ-ÿ0-9])")
_LATEX = re.compile(r"\$\$(.{2,200}?)\$\$|\$(.{2,120}?)\$|\\\((.{2,160}?)\\\)|\\\[(.{2,200}?)\\\]", re.S)
_EQ = re.compile(
    r"(?<![\wÀ-ÿ])(?P<lhs>[A-Za-zΑ-Ωα-ω][A-Za-z0-9_′'ₐ-ₜ₀-₉]{0,5}(?:\([^()]{0,10}\))?)\s*=\s*"
    r"(?P<rhs>[^=\n;]{1,110})")
_OPS = re.compile(r"[+\-*/×·÷^²³√∑∫∂π]|\b(?:sqrt|sin|cos|tan|exp|ln|log)\b|\d\s*[A-Za-zα-ω]|[A-Za-zα-ω]\s*\d")
_STOP_RHS = re.compile(r"\s(?:avec|où|ou|soit|et|donc|car|ce qui|pour|si|alors|lorsque|quand)\s|[,:](?=\s+[a-zà-ÿ]{4,})"
                       r"|\.(?:\s|$)")
_ARTICLES = re.compile(r"^(?:(?:le|la|les|l['’]|un|une|des|du|de la|de l['’]|son|sa|ses|leur|leurs|cette|ce|cet|"
                       r"on|il|elle|et|à|a|en)\s*)+", re.I)
_SUBJECT = re.compile(r"(?:^|\s)(?:nous|on|je|j['’]ai|il|elle|ils|elles|vous|que|qu['’]\w+|dont|mais|donc|car|ainsi|alors)"
                      r"\s+(?:\w+\s+)?(?=(?:le|la|les|l['’]|un|une|des|du|son|sa|ses)\b)", re.I)
_WORDISH = re.compile(r"^[a-zà-ÿ]{4,}$")


def _num(v):
    v = v.replace(",", ".").replace("−", "-").replace(" ", "")
    m = re.match(r"(-?\d+(?:\.\d+)?)(?:[x×·]10\^?(-?\d+))?$", v)
    if not m:
        return None
    x = float(m.group(1))
    return x * 10 ** int(m.group(2)) if m.group(2) else x


def _clean_label(label):
    label = re.sub(r"\s+", " ", label).strip(" -,.;")
    words = label.split(" ")
    label = " ".join(words[-7:])
    cut = None
    for m in _SUBJECT.finditer(label):   # « nous mesurons le module… » : on ne garde que le groupe nominal
        cut = m
    if cut:
        label = label[cut.end():]
    label = _ARTICLES.sub("", label).strip()
    return label


def _table_blocks(text):
    lines = text.split("\n")
    block, out = [], []
    for ln in lines + [""]:
        if ln.count("|") >= 1 and len(ln.strip()) > 2:
            block.append(ln)
        else:
            if len(block) >= 2:
                out.append(block)
            block = []
    return out


def find_extracts(segs, whole_table_locators=("feuille",)):
    """[(repère, texte)] -> liste de dicts {kind, label, content, locator, value, unit}."""
    out, seen = [], set()

    def add(kind, label, content, loc, value=None, unit=None):
        key = (kind, normalize(content)[:200])
        if key in seen or not content.strip():
            return
        seen.add(key)
        out.append({"kind": kind, "label": label[:120], "content": content[:3000], "locator": loc,
                    "value": value, "unit": unit})

    for loc, text in segs:
        # --- tableaux
        if loc.startswith(whole_table_locators) and "|" in text:
            rows = [r for r in text.split("\n") if r.strip()]
            add("tableau", rows[0][:120], "\n".join(rows[:60]), loc)
        else:
            for b in _table_blocks(text):
                rows = [r.strip() for r in b if not re.fullmatch(r"[|\s:\-]+", r)]
                if len(rows) >= 2:
                    add("tableau", rows[0].strip("| ")[:120], "\n".join(rows[:60]), loc)
        # --- formules : LaTeX, formules de tableur, égalités symboliques
        for m in _LATEX.finditer(text):
            f = next(g for g in m.groups() if g)
            add("formule", "LaTeX", f.strip(), loc)
        for m in re.finditer(r"\(= ([^)\n]{2,120}\)?)\)", text):
            add("formule", "formule de tableur", "= " + m.group(1).strip(), loc)
        for line in text.split("\n"):
            for m in _EQ.finditer(line):
                lhs, rhs = m.group("lhs"), m.group("rhs")
                if _WORDISH.match(lhs) and lhs.lower() not in ("sqrt", "sin", "cos"):
                    continue  # « prix = … » : de la prose, pas une formule
                cut = _STOP_RHS.search(rhs)
                rhs = (rhs[:cut.start()] if cut else rhs).strip(" .")
                if re.fullmatch(rf"(?:environ\s+)?{_NUM}\s*(?:{UNITS})?", rhs):
                    continue  # « E = 210 GPa » : une donnée, relevée plus bas
                prose = [w for w in re.findall(r"[A-Za-zÀ-ÿ]{4,}", rhs) if w.lower() not in ("sqrt", "sinus", "cosinus")]
                if not _OPS.search(rhs) or len(prose) > 3 or len(rhs) < 2:
                    continue
                add("formule", lhs, f"{lhs} = {rhs}", loc)
        # --- données chiffrées avec unité
        for m in _DATA.finditer(text):
            label = _clean_label(m.group("label"))
            if len(label) < 3 or not re.search(r"[A-Za-zÀ-ÿ]{3,}", label):
                continue
            value = _num(m.group("value"))
            unit = m.group("unit")
            add("donnée", label, f"{label} : {m.group('value')} {unit}", loc, value, unit)
    return out


def sentence_around(text, needle, width=260):
    """Phrase du texte qui contient `needle` (pour citer une donnée)."""
    i = text.find(needle)
    if i < 0:
        return needle
    a = max(text.rfind(". ", 0, i), text.rfind("\n", 0, i))
    b_candidates = [x for x in (text.find(". ", i), text.find("\n", i)) if x >= 0]
    b = min(b_candidates) if b_candidates else len(text)
    s = text[a + 1 if a >= 0 else 0: b + 1].strip()
    return s if len(s) <= width else s[:width].rsplit(" ", 1)[0] + "…"


def label_key(label):
    """Clé de comparaison d'une grandeur : mots significatifs, sans accents ni pluriels."""
    ws = [w[:-1] if len(w) > 4 and w[-1] in "sx" else w for w in tokens(label)]
    return " ".join(sorted(set(ws)))


def same_quantity(k1, k2):
    """Deux libellés désignent-ils la même grandeur ? (l'un contient tous les mots de l'autre, deux mots au moins)"""
    a, b = set(k1.split()), set(k2.split())
    small, big = (a, b) if len(a) <= len(b) else (b, a)
    return len(small) >= 2 and small <= big


def group_quantities(items):
    """Regroupe des données {label, unit, …} qui parlent de la même grandeur dans la même unité."""
    groups = []
    for it in items:
        if it.get("value") is None or not it.get("unit"):
            continue
        key, unit = label_key(it["label"]), it["unit"].replace("·", ".").lower()
        for g in groups:
            if g["unit"] == unit and same_quantity(g["key"], key):
                g["items"].append(it)
                if len(key) < len(g["key"]):
                    g["key"] = key
                break
        else:
            groups.append({"unit": unit, "key": key, "items": [it]})
    return [g["items"] for g in groups if len(g["key"].split()) >= 2]


def conflicts(items, tol=0.1):
    """Données en désaccord : même grandeur, même unité, valeurs écartées de plus de `tol` (10 %).

    items : [{label, value, unit, ...}] -> [(a, b)]
    Une grandeur d'un seul mot (« tension ») est trop vague pour conclure : elle est ignorée.
    """
    by_unit = {}
    for it in items:
        if it.get("value") is None or not it.get("unit"):
            continue
        key = label_key(it["label"])
        if len(key.split()) < 2:
            continue
        by_unit.setdefault(it["unit"].replace("·", ".").lower(), []).append((key, it))
    out = []
    for lst in by_unit.values():
        words = {}
        for idx, (key, _) in enumerate(lst):
            for w in key.split():
                words.setdefault(w, []).append(idx)
        seen = set()
        for i, (ki, a) in enumerate(lst):
            for w in ki.split():
                for j in words[w][:300]:
                    if j <= i or (i, j) in seen:
                        continue
                    seen.add((i, j))
                    kj, b = lst[j]
                    if same_quantity(ki, kj):
                        ref = max(abs(a["value"]), abs(b["value"])) or 1.0
                        if abs(a["value"] - b["value"]) / ref > tol:
                            out.append((a, b))
    return out


# ---------------------------------------------------------------- versions d'un même document
_VER = re.compile(r"(?:\b(?:v|version|ver|rev|révision|revision|ed|édition|edition)\.?\s*(\d+(?:\.\d+)?)\b)", re.I)
_YEAR = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")
_NOISE = re.compile(r"\b(?:final|finale|definitif|def|copie|copy|nouveau|new|old|ancien|draft|brouillon|maj|"
                    r"mise a jour)\b|\(\d+\)|[_\-.]+")


def title_stem(title):
    """Titre sans numéro de version, année, extension ni mentions « final », « copie »…"""
    t = normalize(re.sub(r"\.(pdf|docx?|xlsx?|odt|ods|pptx?|txt|md|csv|html?)$", "", title.strip(), flags=re.I))
    t = _VER.sub(" ", t)
    t = _YEAR.sub(" ", t)
    t = _NOISE.sub(" ", t)
    return " ".join(t.split())


def version_rank(title, published=""):
    """Ordre de version déductible du titre ou de la date : (version, année) ; plus grand = plus récent."""
    m = _VER.search(title)
    v = float(m.group(1)) if m else -1.0
    y = _YEAR.findall(f"{published} {title}")
    return (v, int(max(y)) if y else -1)
