"""Artefacts : créations interactives (applications web, composants React, schémas SVG, diagrammes, documents,
présentations) générées par Jarvis, versionnées et affichées dans un cadre isolé.

Isolation : l'aperçu est chargé dans une iframe `sandbox="allow-scripts"` (origine opaque : aucun accès à
l'interface de Jarvis, à son jeton ni à ses données) et le document reçoit une politique de sécurité qui bloque
toute connexion réseau (fetch, XHR, WebSocket, formulaires). Seules les bibliothèques de trois CDN publics
(cdnjs, jsDelivr, unpkg) peuvent être chargées. Les images de Jarvis sont incorporées au rendu (jarvis-image:N).
"""
import base64
import html
import io
import json
import re
import threading
import time
import zipfile
from html.parser import HTMLParser

KINDS = {
    "html": "Application web",
    "react": "Composant React",
    "svg": "Schéma vectoriel",
    "mermaid": "Diagramme",
    "markdown": "Document",
    "slides": "Présentation",
}
LANG = {"html": "html", "react": "jsx", "svg": "svg", "mermaid": "mermaid", "markdown": "markdown", "slides": "markdown"}
CDNS = ("https://cdnjs.cloudflare.com", "https://cdn.jsdelivr.net", "https://unpkg.com")
LIBS = {  # versions exactes chargées par les modèles de rendu
    "react": "https://unpkg.com/react@18.3.1/umd/react.production.min.js",
    "react-dom": "https://unpkg.com/react-dom@18.3.1/umd/react-dom.production.min.js",
    "babel": "https://unpkg.com/@babel/standalone@7.24.7/babel.min.js",
    "marked": "https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js",
    "katex-css": "https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css",
    "katex": "https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js",
    "katex-auto": "https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/contrib/auto-render.min.js",
    "mermaid": "https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js",
}
CSP = ("default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval' " + " ".join(CDNS) + "; "
       "style-src 'unsafe-inline' " + " ".join(CDNS) + " https://fonts.googleapis.com; "
       "font-src data: https://fonts.gstatic.com " + " ".join(CDNS) + "; img-src data: blob:; media-src data: blob:; "
       "connect-src 'none'; frame-src 'none'; worker-src blob:; form-action 'none'; base-uri 'none'")

CREATE_PROMPT = """Tu es le développeur de Jarvis. Tu crées un ARTEFACT : une création interactive autonome, affichée dans
un cadre isolé SANS accès réseau.

Type demandé : {kind}.
- html : un document HTML5 complet (<!doctype html>…), CSS et JavaScript intégrés au fichier.
- react : un seul fichier JSX qui définit un composant App (« export default function App() {{…}} »). React 18 est
  fourni : n'importe que depuis "react" ; aucune autre bibliothèque npm.
- svg : un seul élément <svg> autonome avec viewBox (schéma, plan, illustration technique).
- mermaid : uniquement le code Mermaid (organigramme, diagramme de séquence, Gantt…).
- markdown : un document structuré (titres, listes, tableaux ; formules LaTeX entre $…$ ou $$…$$).
- slides : une présentation en Markdown, diapositives séparées par une ligne « --- ».

Règles :
- Aucune requête réseau (fetch, XHR, WebSocket, formulaire envoyé) : elles sont bloquées. Écris les données dans le code.
- Bibliothèques autorisées uniquement depuis https://cdnjs.cloudflare.com, https://cdn.jsdelivr.net ou https://unpkg.com,
  avec un numéro de version exact (ex. https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js,
  https://unpkg.com/three@0.160.0/build/three.module.js).
- Images : utilise uniquement celles de la liste « Images disponibles », avec l'adresse exacte jarvis-image:N
  (ex. <img src="jarvis-image:12" alt="…">). N'invente aucune autre image : dessine sinon en CSS, SVG ou canvas.
- Données : utilise fidèlement les informations et sources fournies, n'invente aucun chiffre. Si une donnée manque,
  affiche-le clairement. Quand tu utilises des sources, cite-les (titre et lien) dans l'artefact.
- Interface en français, soignée et moderne, utilisable de 360 px à 1600 px de large ; thème sombre élégant par défaut.
- Le code doit fonctionner du premier coup : pas de pseudo-code, pas de « TODO », aucun bouton qui ne fait rien.
{engineering}
Réponds EXACTEMENT sous cette forme, sans rien d'autre :
TITRE: <titre court>
TYPE: <html|react|svg|mermaid|markdown|slides>
```<langage>
<le code complet>
```"""

ENGINEERING = """
Création d'ingénierie (schéma, simulation, calculateur, visualisation technique) :
- unités SI affichées, formules visibles, hypothèses écrites ; valeurs par défaut plausibles et modifiables ;
- simulation : intégration pas à pas avec un pas de temps borné, boutons Lecture / Pause / Réinitialiser, curseurs ;
- toute valeur de matériau ou de norme absente des données fournies est marquée « valeur indicative »."""

EDIT_PROMPT = """Tu modifies un artefact existant (type {kind}) selon la demande de l'utilisateur.
Réponds UNIQUEMENT par un ou plusieurs blocs de remplacement :
<<<<<<< CHERCHER
texte exact à remplacer, copié du code actuel, assez long pour être unique
=======
nouveau texte
>>>>>>> REMPLACER
Si la modification touche plus de la moitié du code, renvoie à la place le code complet dans un seul bloc ```…```.
Ne change rien d'autre que ce qui est demandé. Mêmes règles qu'à la création : aucune requête réseau, images
uniquement jarvis-image:N parmi celles fournies, interface en français."""

FULL_PROMPT = """Tu modifies un artefact existant (type {kind}). Renvoie le code COMPLET modifié selon la demande, dans un
seul bloc ```…```, sans rien d'autre. Mêmes règles : aucune requête réseau, images uniquement jarvis-image:N, français."""

_FENCE = re.compile(r"```[ \t]*([\w+-]*)[ \t]*\n(.*?)\n?```", re.S)
_BLOCK = re.compile(r"<{5,}\s*CHERCHER\s*\n(.*?)\n={5,}\s*\n(.*?)\n?>{5,}\s*REMPLACER", re.S)
_ENGINEERING_WORDS = re.compile(
    r"simulat|schéma|schema|mécanism|mecanism|cinémat|cinemat|dynamique|calculat|dimensionn|poutre|engrenage|"
    r"treuil|moteur|réducteur|reducteur|ressort|pendule|vibration|thermique|fluide|contrainte|rdm|force|couple|"
    r"circuit|cinéma|robot|bielle|came|piston|trajectoire|physique|mécanique|mecanique|ingénier|ingenier", re.I)


class ArtifactError(Exception):
    pass


def is_engineering(text):
    return bool(_ENGINEERING_WORDS.search(text or ""))


def guess_kind(text):
    t = (text or "").lower()
    if re.search(r"organigramme|diagramme de (séquence|gantt|flux)|flowchart|logigramme|diagramme mermaid", t):
        return "mermaid"
    if re.search(r"présentation|presentation|diaporama|diapositive|slides", t):
        return "slides"
    if re.search(r"\b(document|fiche|rapport|compte rendu|synthèse écrite)\b", t) and not re.search(
            r"applic|interact|outil|calcul|simul|jeu|tableau de bord|dashboard", t):
        return "markdown"
    if re.search(r"\bsvg\b|schéma (technique|de principe|coté)|plan coté|illustration vectorielle", t) and not re.search(
            r"interact|anim|simul", t):
        return "svg"
    if re.search(r"\breact\b|composant", t):
        return "react"
    return "html"


def parse_creation(raw, kind_hint="html"):
    """Extrait (titre, type, code) de la réponse du modèle."""
    title = ""
    m = re.search(r"^\s*TITRE\s*:\s*(.+)$", raw, re.M)
    if m:
        title = m.group(1).strip().strip("*#` ")[:120]
    kind = kind_hint
    m = re.search(r"^\s*TYPE\s*:\s*(\w+)", raw, re.M)
    if m and m.group(1).lower() in KINDS:
        kind = m.group(1).lower()
    fences = _FENCE.findall(raw)
    if fences:
        lang, code = max(fences, key=lambda f: len(f[1]))
        if not m and lang.lower() in ("jsx", "tsx"):
            kind = "react"
        elif not m and lang.lower() == "svg":
            kind = "svg"
        elif not m and lang.lower() == "mermaid":
            kind = "mermaid"
    else:
        code = re.sub(r"^\s*(TITRE|TYPE)\s*:.*$", "", raw, flags=re.M).strip()
        if not re.match(r"(<!doctype|<html|<svg|#|graph |flowchart )", code, re.I):
            raise ArtifactError("Le modèle n'a pas renvoyé de code exploitable.")
    code = code.strip()
    if len(code) < 20:
        raise ArtifactError("Le modèle a renvoyé un code vide.")
    if not title:
        t = re.search(r"<title>(.*?)</title>", code, re.I | re.S) or re.search(r"^#\s+(.+)$", code, re.M)
        title = (t.group(1).strip() if t else "Artefact")[:120]
    return title, kind, code


def apply_edits(code, raw):
    """Applique les blocs CHERCHER/REMPLACER. Renvoie (nouveau code, nb de blocs) ; lève ArtifactError si un bloc
    ne correspond pas au code actuel."""
    blocks = _BLOCK.findall(raw)
    if not blocks:
        fences = _FENCE.findall(raw)
        if fences:
            full = max(fences, key=lambda f: len(f[1]))[1].strip()
            if len(full) >= 20:
                return full, 0
        raise ArtifactError("aucun bloc de modification reconnu")
    out = code
    for search, repl in blocks:
        if search in out:
            out = out.replace(search, repl, 1)
            continue
        # tolérance : espaces de fin de ligne et indentation différents
        pat = r"\s*".join(re.escape(part) for part in search.split())
        m = re.search(pat, out)
        if not m:
            raise ArtifactError(f"passage introuvable : {search[:80]!r}")
        out = out[:m.start()] + repl + out[m.end():]
    return out, len(blocks)


# ----------------------------------------------------------------------------- rendu
HELPER_JS = r"""
(function(){
  function send(m){ try{ parent.postMessage(Object.assign({jarvis:true}, m), '*'); }catch(e){} }
  window.addEventListener('error', function(e){ send({kind:'error', message: String(e.message||e), line: e.lineno||0}); });
  window.addEventListener('unhandledrejection', function(e){ send({kind:'error', message: 'Promesse rejetée : '+String(e.reason)}); });
  var ce = console.error; console.error = function(){ send({kind:'error', message: Array.prototype.map.call(arguments, String).join(' ')}); ce.apply(console, arguments); };
  var picking = false, box = null, hovered = null;
  function selector(el){
    var parts = [];
    while (el && el.nodeType === 1 && el.tagName !== 'HTML' && parts.length < 5){
      if (el.id){ parts.unshift('#'+el.id); break; }
      var s = el.tagName.toLowerCase(), p = el.parentElement;
      if (el.classList.length) s += '.' + Array.prototype.slice.call(el.classList, 0, 2).join('.');
      if (p){ var same = Array.prototype.filter.call(p.children, function(c){ return c.tagName === el.tagName; });
              if (same.length > 1) s += ':nth-of-type(' + (same.indexOf(el)+1) + ')'; }
      parts.unshift(s); el = p;
    }
    return parts.join(' > ');
  }
  function outline(el){
    if (!box){ box = document.createElement('div'); box.style.cssText='position:fixed;pointer-events:none;z-index:2147483647;border:2px solid #22d3ee;background:rgba(34,211,238,.12);border-radius:3px;transition:all .06s'; document.documentElement.appendChild(box); }
    var r = el.getBoundingClientRect(); box.style.display='block';
    box.style.left=r.left+'px'; box.style.top=r.top+'px'; box.style.width=r.width+'px'; box.style.height=r.height+'px';
  }
  document.addEventListener('mouseover', function(e){ if(!picking) return; hovered = e.target; outline(hovered); }, true);
  document.addEventListener('click', function(e){
    if(!picking) return; e.preventDefault(); e.stopPropagation(); picking = false; if (box) box.style.display='none';
    var el = e.target;
    send({kind:'selected', selector: selector(el), tag: el.tagName.toLowerCase(), text: (el.innerText||'').slice(0,200), html: el.outerHTML.slice(0,800)});
  }, true);
  window.addEventListener('message', function(e){
    var d = e.data || {};
    if (d.jarvis === 'pick'){ picking = !!d.on; if(!picking && box) box.style.display='none'; }
  });
  window.addEventListener('load', function(){ send({kind:'ready'}); });
})();
"""


def _head(title, helper=True):
    out = [f'<meta http-equiv="Content-Security-Policy" content="{CSP}">', '<meta charset="utf-8">',
           '<meta name="viewport" content="width=device-width, initial-scale=1">']
    if helper:
        out.append(f"<script>{HELPER_JS}</script>")
    return "\n".join(out)


def _shell(title, body, head_extra="", helper=True, style=""):
    return (f"<!doctype html>\n<html lang=\"fr\"><head>{_head(title, helper)}<title>{html.escape(title)}</title>"
            f"<style>html,body{{margin:0;min-height:100%;background:#0b1020;color:#e5e7eb;"
            f"font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif}}{style}</style>{head_extra}</head>"
            f"<body>{body}</body></html>")


def _react_doc(title, code, helper):
    src = code
    name = "App"
    m = re.search(r"export\s+default\s+function\s+([A-Za-z_]\w*)", src)
    if m:
        name = m.group(1)
    src = re.sub(r"export\s+default\s+function", "function", src)
    m = re.search(r"export\s+default\s+([A-Za-z_]\w*)\s*;?", src)
    if m:
        name = m.group(1)
        src = src[:m.start()] + src[m.end():]
    src = re.sub(r"^\s*import\s+[^;]*?from\s+['\"]react(?:-dom(?:/client)?)?['\"];?\s*$", "", src, flags=re.M)
    src = re.sub(r"^\s*export\s+(?=(const|function|class)\b)", "", src, flags=re.M)
    prelude = ("const {useState, useEffect, useRef, useMemo, useCallback, useReducer, useContext, createContext, "
               "Fragment, useLayoutEffect, useId} = React;\n")
    boot = f"\nReactDOM.createRoot(document.getElementById('root')).render(React.createElement({name}));"
    js = (prelude + src + boot).replace("</script", "<\\/script")
    head = (f'<script src="{LIBS["react"]}"></script><script src="{LIBS["react-dom"]}"></script>'
            f'<script src="{LIBS["babel"]}"></script>')
    body = f'<div id="root"></div><script type="text/babel" data-presets="react">{js}</script>'
    return _shell(title, body, head, helper)


def _markdown_doc(title, md, helper, slides=False):
    data = json.dumps(md).replace("</", "<\\/")
    head = (f'<link rel="stylesheet" href="{LIBS["katex-css"]}"><script src="{LIBS["marked"]}"></script>'
            f'<script src="{LIBS["katex"]}"></script><script src="{LIBS["katex-auto"]}"></script>')
    style = ("main{max-width:860px;margin:0 auto;padding:32px 22px;line-height:1.65}h1,h2,h3{line-height:1.25}"
             "a{color:#67e8f9}table{border-collapse:collapse;width:100%;margin:1em 0}td,th{border:1px solid #334155;"
             "padding:6px 10px}th{background:#111a33}code{background:#111a33;padding:2px 5px;border-radius:4px}"
             "pre{background:#111a33;padding:12px;border-radius:8px;overflow:auto}img{max-width:100%}"
             "blockquote{border-left:3px solid #22d3ee;margin:0;padding-left:14px;color:#cbd5e1}")
    if slides:
        style += (".slide{display:none;min-height:100vh;box-sizing:border-box;padding:7vh 8vw;flex-direction:column;"
                  "justify-content:center}.slide.on{display:flex}.slide h1{font-size:2.6em}.slide h2{font-size:2em}"
                  ".slide{font-size:clamp(16px,2.2vw,26px)}nav{position:fixed;bottom:14px;right:18px;display:flex;gap:8px;"
                  "align-items:center;color:#94a3b8}nav button{background:#1e293b;color:#e5e7eb;border:1px solid #334155;"
                  "border-radius:8px;padding:6px 12px;cursor:pointer}")
        script = (f"const md={data};const parts=md.split(/\\n-{{3,}}\\s*\\n/);const root=document.getElementById('d');"
                  "parts.forEach((p,i)=>{const s=document.createElement('section');s.className='slide'+(i?'':' on');"
                  "s.innerHTML=marked.parse(p);root.appendChild(s);});let k=0;const n=document.getElementById('n');"
                  "function go(d){const all=root.children;all[k].classList.remove('on');k=Math.max(0,Math.min(all.length-1,k+d));"
                  "all[k].classList.add('on');n.textContent=(k+1)+' / '+all.length;}go(0);"
                  "document.getElementById('p').onclick=()=>go(-1);document.getElementById('x').onclick=()=>go(1);"
                  "addEventListener('keydown',e=>{if(['ArrowRight','PageDown',' '].includes(e.key))go(1);"
                  "if(['ArrowLeft','PageUp'].includes(e.key))go(-1);});"
                  "try{renderMathInElement(root,{delimiters:[{left:'$$',right:'$$',display:true},{left:'$',right:'$',display:false}]})}catch(e){}")
        body = ('<div id="d"></div><nav><button id="p" aria-label="Précédente">◀</button><span id="n"></span>'
                f'<button id="x" aria-label="Suivante">▶</button></nav><script>{script}</script>')
    else:
        script = (f"const md={data};document.getElementById('d').innerHTML=marked.parse(md);"
                  "try{renderMathInElement(document.getElementById('d'),{delimiters:[{left:'$$',right:'$$',display:true},"
                  "{left:'$',right:'$',display:false}]})}catch(e){}")
        body = f'<main id="d"></main><script>{script}</script>'
    return _shell(title, body, head, helper, style)


def _mermaid_doc(title, code, helper):
    head = f'<script src="{LIBS["mermaid"]}"></script>'
    body = (f'<div style="padding:24px;display:flex;justify-content:center"><pre class="mermaid">{html.escape(code)}</pre></div>'
            "<script>mermaid.initialize({startOnLoad:true,theme:'dark',securityLevel:'strict'});</script>")
    return _shell(title, body, head, helper)


def _html_doc(code, helper):
    inject = _head("", helper)
    m = re.search(r"<head[^>]*>", code, re.I)
    if m:
        return code[:m.end()] + inject + code[m.end():]
    m = re.search(r"<html[^>]*>", code, re.I)
    if m:
        return code[:m.end()] + f"<head>{inject}</head>" + code[m.end():]
    return f"<!doctype html><html lang=\"fr\"><head>{inject}</head><body>{code}</body></html>"


def build_document(title, kind, code, image_data=None, helper=True):
    """Document HTML complet prêt à charger dans l'iframe isolée (ou à exporter, helper=False)."""
    missing = []

    def img(m):
        rid = int(m.group(1))
        uri = (image_data or {}).get(rid)
        if not uri:
            missing.append(rid)
            return "data:image/gif;base64,R0lGODlhAQABAAAAACw="
        return uri
    code = re.sub(r"jarvis-image:(\d+)", img, code)
    if kind == "react":
        doc = _react_doc(title, code, helper)
    elif kind in ("markdown", "slides"):
        doc = _markdown_doc(title, code, helper, slides=kind == "slides")
    elif kind == "mermaid":
        doc = _mermaid_doc(title, code, helper)
    elif kind == "svg":
        doc = _shell(title, f'<div style="display:flex;min-height:100vh;align-items:center;justify-content:center;'
                            f'padding:16px;box-sizing:border-box">{code}</div>', "", helper,
                     "svg{max-width:100%;height:auto;max-height:96vh}")
    else:
        doc = _html_doc(code, helper)
    return doc, missing


class _Checker(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.scripts, self.tags, self.inline = [], 0, []
        self._in_script = False

    def handle_starttag(self, tag, attrs):
        self.tags += 1
        a = dict(attrs)
        if tag == "script":
            self._in_script = True
            if a.get("src"):
                self.scripts.append(a["src"])
        if tag == "link" and a.get("href") and "stylesheet" in (a.get("rel") or ""):
            self.scripts.append(a["href"])

    def handle_endtag(self, tag):
        if tag == "script":
            self._in_script = False

    def handle_data(self, data):
        if self._in_script and data.strip():
            self.inline.append(data)


def static_check(kind, code, known_images=()):
    """Contrôles sans exécution : problèmes bloquants (erreur) ou à surveiller (attention)."""
    issues = []
    if not code.strip():
        return [{"level": "erreur", "text": "Le code est vide."}]
    for rid in sorted({int(x) for x in re.findall(r"jarvis-image:(\d+)", code)} - set(known_images)):
        issues.append({"level": "erreur", "text": f"Image jarvis-image:{rid} introuvable."})
    if re.search(r"\bfetch\s*\(|XMLHttpRequest|new\s+WebSocket|EventSource\s*\(", code):
        issues.append({"level": "attention", "text": "Le code tente une requête réseau : elle sera bloquée dans l'aperçu."})
    if re.search(r"\bTODO\b|à compléter|lorem ipsum", code, re.I):
        issues.append({"level": "attention", "text": "Le code contient un passage inachevé (TODO / à compléter)."})
    if kind == "html":
        c = _Checker()
        try:
            c.feed(code)
        except Exception as e:
            issues.append({"level": "erreur", "text": f"HTML illisible : {e}"})
        if c.tags < 2:
            issues.append({"level": "erreur", "text": "Le document HTML ne contient presque aucune balise."})
        for src in c.scripts:
            if src.startswith(("http://", "https://", "//")) and not src.startswith(CDNS):
                issues.append({"level": "erreur", "text": f"Ressource externe non autorisée : {src[:120]}"})
    elif kind == "react":
        if not re.search(r"function\s+App\b|const\s+App\s*=|export\s+default", code):
            issues.append({"level": "erreur", "text": "Aucun composant App ni export par défaut."})
        bad = [m for m in re.findall(r"^\s*import\s+.*?from\s+['\"]([^'\"]+)['\"]", code, re.M)
               if m not in ("react", "react-dom", "react-dom/client")]
        for b in bad:
            issues.append({"level": "erreur", "text": f"Bibliothèque non disponible dans l'aperçu : {b}"})
    elif kind == "svg":
        if not re.search(r"<svg[\s>]", code, re.I):
            issues.append({"level": "erreur", "text": "Pas d'élément <svg>."})
    return issues


def runtime_check(doc, timeout=12.0):
    """Charge le document dans un Chromium sans fenêtre (Playwright, s'il est installé) et relève les erreurs
    JavaScript. Renvoie {"checked": bool, "errors": [...], "note": str}."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"checked": False, "errors": [], "note": "Playwright n'est pas installé : contrôle à l'exécution impossible"}
    box = {}

    def run():
        errs = []
        try:
            with sync_playwright() as p:
                try:
                    browser = p.chromium.launch()
                except Exception:
                    browser = p.chromium.launch(channel="msedge")
                page = browser.new_page(viewport={"width": 1100, "height": 760})
                page.on("pageerror", lambda e: errs.append(str(e)[:300]))
                page.on("console", lambda m: m.type == "error" and errs.append(m.text[:300]))
                page.set_content(doc, wait_until="load", timeout=int(timeout * 1000))
                page.wait_for_timeout(1200)
                box["empty"] = page.evaluate("() => (document.body ? document.body.innerText.trim().length : 0) === 0 "
                                             "&& !document.querySelector('canvas,svg,img,video')")
                browser.close()
            box["errors"] = errs
        except Exception as e:
            box["fail"] = f"{type(e).__name__}: {e}"[:300]
    th = threading.Thread(target=run, daemon=True)
    th.start()
    th.join(timeout + 15)
    if "fail" in box or "errors" not in box:
        return {"checked": False, "errors": [], "note": f"contrôle à l'exécution impossible ({box.get('fail', 'délai dépassé')})"}
    errors = [e for e in box["errors"] if "Content Security Policy" not in e or "connect-src" in e]
    if box.get("empty"):
        errors.append("La page s'affiche vide.")
    return {"checked": True, "errors": errors[:10], "note": ""}


class Artifacts:
    def __init__(self, ws, brain=None, settings=None):
        self.ws, self.brain, self.settings = ws, brain, settings

    # ------------------------------------------------------------------ stockage
    def _row(self, aid):
        return self.ws._q("SELECT * FROM artifacts WHERE id=? AND deleted IS NULL", (aid,), one=True)

    def create(self, title, kind, content, project_id=None, conv_id=None, mission_id=None, note="création",
               author="jarvis"):
        if kind not in KINDS:
            raise ArtifactError("Type d'artefact inconnu")
        if not content.strip():
            raise ArtifactError("Le contenu est vide.")
        now = time.time()
        aid = self.ws._x("INSERT INTO artifacts(title,kind,project_id,conv_id,mission_id,version,created,updated) "
                         "VALUES(?,?,?,?,?,1,?,?)", ((title or "Artefact")[:120], kind, project_id, conv_id, mission_id,
                                                      now, now))
        self.ws._x("INSERT INTO artifact_versions(artifact_id,n,content,note,author,created) VALUES(?,?,?,?,?,?)",
                   (aid, 1, content, note[:300], author, now))
        self.ws.touch_project(project_id)
        self.ws.bus.publish("artifact", artifact_id=aid, version=1, action="créé", project_id=project_id,
                            conv_id=conv_id, mission_id=mission_id)
        return self.get(aid)

    def new_version(self, aid, content, note="", author="jarvis"):
        a = self._row(aid)
        if not a:
            raise KeyError("Artefact introuvable")
        cur = self.ws._q("SELECT content FROM artifact_versions WHERE artifact_id=? AND n=?", (aid, a["version"]), one=True)
        if cur and cur["content"] == content:
            return self.get(aid)   # rien n'a changé : pas de nouvelle version
        n = a["version"] + 1
        self.ws._x("INSERT INTO artifact_versions(artifact_id,n,content,note,author,created) VALUES(?,?,?,?,?,?)",
                   (aid, n, content, note[:300], author, time.time()))
        self.ws._x("UPDATE artifacts SET version=?, updated=? WHERE id=?", (n, time.time(), aid))
        self.ws.touch_project(a["project_id"])
        self.ws.bus.publish("artifact", artifact_id=aid, version=n, action="modifié", project_id=a["project_id"],
                            conv_id=a["conv_id"])
        return self.get(aid)

    def get(self, aid, version=None):
        a = self._row(aid)
        if not a:
            return None
        n = int(version or a["version"])
        v = self.ws._q("SELECT * FROM artifact_versions WHERE artifact_id=? AND n=?", (aid, n), one=True)
        if not v:
            return None
        a["content"], a["shown_version"] = v["content"], n
        a["versions"] = self.ws._q("SELECT n, note, author, created, LENGTH(content) AS size FROM artifact_versions "
                                   "WHERE artifact_id=? ORDER BY n DESC", (aid,))
        a["kind_label"] = KINDS.get(a["kind"], a["kind"])
        a["images"] = sorted({int(x) for x in re.findall(r"jarvis-image:(\d+)", v["content"])})
        return a

    def list(self, project_id=None, q=None, conv_id=None, limit=200):
        where, args = ["a.deleted IS NULL"], []
        if project_id is not None:
            where.append("a.project_id=?")
            args.append(project_id)
        if conv_id:
            where.append("a.conv_id=?")
            args.append(conv_id)
        if q:
            where.append("a.title LIKE ?")
            args.append(f"%{q}%")
        rows = self.ws._q(f"SELECT a.*, p.name AS project FROM artifacts a LEFT JOIN projects p ON p.id=a.project_id "
                          f"WHERE {' AND '.join(where)} ORDER BY a.updated DESC LIMIT ?", (*args, limit))
        for r in rows:
            r["kind_label"] = KINDS.get(r["kind"], r["kind"])
        return rows

    def restore(self, aid, n):
        old = self.get(aid, n)
        if not old:
            raise KeyError("Version introuvable")
        return self.new_version(aid, old["content"], f"retour à la version {n}", "utilisateur")

    def update(self, aid, patch):
        a = self._row(aid)
        if not a:
            raise KeyError("Artefact introuvable")
        if "title" in patch and str(patch["title"]).strip():
            self.ws._x("UPDATE artifacts SET title=?, updated=? WHERE id=?", (str(patch["title"]).strip()[:120], time.time(), aid))
        if "project_id" in patch:
            pid = patch["project_id"]
            if pid is not None and not self.ws.project(pid):
                raise KeyError("Projet introuvable")
            self.ws._x("UPDATE artifacts SET project_id=? WHERE id=?", (pid, aid))
            self.ws.touch_project(pid)
        if "content" in patch:
            return self.new_version(aid, str(patch["content"]), str(patch.get("note") or "modification manuelle"),
                                    "utilisateur")
        self.ws.bus.publish("artifact", artifact_id=aid, version=a["version"], action="renommé")
        return self.get(aid)

    def delete(self, aid):
        if not self._row(aid):
            raise KeyError("Artefact introuvable")
        self.ws._x("UPDATE artifacts SET deleted=? WHERE id=?", (time.time(), aid))
        self.ws.bus.publish("artifact", artifact_id=aid, action="supprimé")

    def latest_in_conversation(self, conv_id):
        r = self.ws._q("SELECT id FROM artifacts WHERE conv_id=? AND deleted IS NULL ORDER BY updated DESC LIMIT 1",
                       (conv_id,), one=True)
        return r["id"] if r else None

    # ------------------------------------------------------------------ rendu et export
    def _image_data(self, code):
        out = {}
        for rid in {int(x) for x in re.findall(r"jarvis-image:(\d+)", code)}:
            data, mime = self.ws.resource_bytes(rid)
            if data:
                out[rid] = f"data:{mime or 'image/png'};base64,{base64.b64encode(data).decode()}"
        return out

    def render(self, aid, version=None, helper=True):
        a = self.get(aid, version)
        if not a:
            raise KeyError("Artefact introuvable")
        doc, missing = build_document(a["title"], a["kind"], a["content"], self._image_data(a["content"]), helper)
        return doc, missing

    def export_html(self, aid, version=None):
        doc, _ = self.render(aid, version, helper=False)
        a = self.get(aid, version)
        return doc.encode("utf-8"), _safe_name(a["title"]) + ".html"

    def export_zip(self, aids, title="artefacts"):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for aid in aids:
                a = self.get(aid)
                if not a:
                    continue
                base = f"{aid:03d}-{_safe_name(a['title'])}"
                doc, _ = self.render(aid, helper=False)
                z.writestr(f"{base}/index.html", doc)
                ext = {"react": "jsx", "svg": "svg", "mermaid": "mmd", "markdown": "md", "slides": "md"}.get(a["kind"], "html")
                z.writestr(f"{base}/source.{ext}", a["content"])
                for rid in a["images"]:
                    data, mime = self.ws.resource_bytes(rid)
                    if data:
                        z.writestr(f"{base}/images/{rid}{'.jpg' if mime == 'image/jpeg' else '.png'}", data)
        return buf.getvalue(), _safe_name(title) + ".zip"

    # ------------------------------------------------------------------ contrôle
    def check(self, aid, runtime=True):
        a = self.get(aid)
        if not a:
            raise KeyError("Artefact introuvable")
        known = [i for i in a["images"] if self.ws.resource(i)]
        issues = static_check(a["kind"], a["content"], known)
        rt = {"checked": False, "errors": [], "note": "exécution désactivée dans les paramètres" if not runtime
              else "exécution non tentée : le code contient déjà des erreurs"}
        if runtime and not any(i["level"] == "erreur" for i in issues):
            doc, _ = self.render(aid, helper=False)
            rt = runtime_check(doc)
            for e in rt["errors"]:
                issues.append({"level": "erreur", "text": f"Erreur à l'exécution : {e}"})
        return {"issues": issues, "runtime": rt, "ok": not any(i["level"] == "erreur" for i in issues)}

    # ------------------------------------------------------------------ génération par le modèle
    def _context_block(self, context):
        context = context or {}
        parts = []
        imgs = []
        for rid in context.get("images") or []:
            r = self.ws.resource(rid)
            if r:
                imgs.append(f"- jarvis-image:{rid} — {r['title']} ({(r.get('meta') or {}).get('prompt', '')[:160]})")
        parts.append("Images disponibles :\n" + ("\n".join(imgs) if imgs else "(aucune)"))
        srcs = context.get("sources") or []
        if srcs:
            parts.append("Sources :\n" + "\n".join(f"[{i + 1}] {s.get('title', '')} — {s.get('url', '')}\n"
                                                  f"{(s.get('excerpt') or '')[:700]}" for i, s in enumerate(srcs[:12])))
        notes = "\n\n".join(context.get("text") or [])
        if notes.strip():
            parts.append("Informations recueillies par les autres agents :\n" + notes[:14000])
        return "\n\n".join(parts)

    def generate(self, ref, brief, kind=None, context=None, project_id=None, conv_id=None, mission_id=None,
                 max_tokens=16000):
        kind = kind if kind in KINDS else guess_kind(brief)
        system = CREATE_PROMPT.format(kind=kind, engineering=ENGINEERING if is_engineering(brief) else "")
        user = f"DEMANDE :\n{brief}\n\n{self._context_block(context)}"
        raw = self.brain.complete(ref, system, [{"role": "user", "content": user}], max_tokens, 0.4)
        title, kind, code = parse_creation(raw, kind)
        return self.create(title, kind, code, project_id, conv_id, mission_id, "création par Jarvis")

    def edit(self, ref, aid, instruction, selection=None, context=None, max_tokens=16000):
        a = self.get(aid)
        if not a:
            raise KeyError("Artefact introuvable")
        sel = ""
        if selection and isinstance(selection, dict) and selection.get("selector"):
            sel = (f"\n\nÉlément sélectionné par l'utilisateur dans l'aperçu (la demande porte sur lui) :\n"
                   f"sélecteur CSS : {selection.get('selector')}\nextrait : {str(selection.get('html', ''))[:800]}")
        user = (f"CODE ACTUEL ({a['kind']}) :\n```{LANG[a['kind']]}\n{a['content']}\n```\n\nDEMANDE : {instruction}{sel}"
                f"\n\n{self._context_block(context)}")
        raw = self.brain.complete(ref, EDIT_PROMPT.format(kind=a["kind"]), [{"role": "user", "content": user}],
                                  max_tokens, 0.2)
        try:
            new, nblocks = apply_edits(a["content"], raw)
            how = f"{nblocks} remplacement(s)" if nblocks else "code complet"
        except ArtifactError as e:
            raw = self.brain.complete(ref, FULL_PROMPT.format(kind=a["kind"]), [{"role": "user", "content": user}],
                                      max_tokens, 0.2)
            fences = _FENCE.findall(raw)
            if not fences:
                raise ArtifactError(f"Modification impossible : {e}") from None
            new = max(fences, key=lambda f: len(f[1]))[1].strip()
            how = "code complet (les remplacements ne correspondaient pas)"
        if new.strip() == a["content"].strip():
            raise ArtifactError("Le modèle n'a rien changé au code.")
        out = self.new_version(aid, new, instruction[:240])
        out["edit_mode"] = how
        return out


def _safe_name(s):
    s = re.sub(r"[^\w\- ]+", "", s or "", flags=re.U).strip().replace(" ", "-")
    return (s or "artefact")[:60]
