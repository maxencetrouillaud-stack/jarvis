"""Génération et modification d'images par IA, avec des adaptateurs interchangeables.

Fournisseur branché : Google Gemini (modèles « Nano Banana », API generateContent). Il utilise la même clé que
les modèles de texte Gemini, mais la génération d'images n'est pas comprise dans l'offre gratuite de l'API :
la facturation doit être activée sur le projet Google de la clé. Ajouter un fournisseur = écrire une classe
qui expose list_models() et generate(), et l'inscrire dans ADAPTERS.

Rien n'est simulé : sans clé ou en cas d'erreur du fournisseur, l'erreur exacte est affichée.
"""
import base64
import json
import re
import threading
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from .providers import ProviderError, _json

RATIOS = ["1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9", "1:4", "4:1", "1:8", "8:1"]
FORMATS = {"carré": "1:1", "portrait": "3:4", "paysage": "16:9", "vertical": "9:16", "panoramique": "21:9"}
SIZES = ["1K", "2K", "4K"]
STYLES = {
    "libre": ("Libre", ""),
    "realiste": ("Réaliste", "photographie réaliste, éclairage naturel, détails précis, rendu photographique"),
    "3d": ("3D", "rendu 3D de haute qualité, matériaux physiques réalistes, éclairage studio doux"),
    "dessin": ("Dessin", "dessin au crayon et à l'encre, traits nets, hachures"),
    "artistique": ("Artistique", "peinture numérique artistique, composition soignée, couleurs riches"),
    "technique": ("Technique", "illustration technique propre, vue claire et lisible, fond neutre, contours nets, "
                               "style schéma d'ingénierie"),
    "logo": ("Logo", "logo vectoriel minimaliste, formes simples, fond uni, lisible en petit"),
    "aquarelle": ("Aquarelle", "aquarelle, papier texturé, couleurs fondues"),
    "manga": ("Manga", "illustration style manga, encrage net, couleurs vives"),
    "pixel": ("Pixel art", "pixel art, palette limitée"),
}
MAX_REF_BYTES = 15 * 1024 * 1024
_SIG = {b"\x89PNG": "image/png", b"\xff\xd8\xff": "image/jpeg", b"GIF8": "image/gif", b"RIFF": "image/webp"}


class ImageError(Exception):
    pass


def sniff(data: bytes):
    for sig, mime in _SIG.items():
        if data.startswith(sig):
            if mime == "image/webp" and data[8:12] != b"WEBP":
                continue
            return mime
    return None


class GeminiImages:
    id, label = "gemini", "Google Gemini (Nano Banana)"
    key_name = "gemini"
    DEFAULT_MODELS = ["gemini-3.1-flash-image", "gemini-3.1-flash-lite-image", "gemini-3-pro-image",
                      "gemini-2.5-flash-image"]
    note = ("Clé Gemini de l'onglet Modèles. La génération d'images n'est pas incluse dans l'offre gratuite de "
            "l'API Gemini : activez la facturation sur le projet Google de la clé.")

    def __init__(self, base_url, key):
        self.base, self.key = base_url.rstrip("/"), key

    def _h(self):
        return {"x-goog-api-key": self.key}

    def list_models(self):
        j = _json(f"{self.base}/models?pageSize=200", self._h(), timeout=20)
        out = [m["name"].split("/", 1)[-1] for m in j.get("models", [])
               if "generateContent" in m.get("supportedGenerationMethods", []) and "image" in m.get("name", "")
               and "imagen" not in m.get("name", "")]
        return out

    def _body(self, prompt, refs, aspect, size, variant):
        parts = [{"text": prompt}] + [{"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode()}}
                                      for data, mime in refs]
        img = {"aspectRatio": aspect}
        if size:
            img["imageSize"] = size
        gen = {"responseModalities": ["TEXT", "IMAGE"]}
        if variant == "responseFormat":
            gen["responseFormat"] = {"image": img}
        elif variant == "imageConfig":
            gen["imageConfig"] = img
        return {"contents": [{"role": "user", "parts": parts}], "generationConfig": gen}

    def generate(self, model, prompt, refs=(), aspect="1:1", size=""):
        """-> (liste de (octets, type), texte éventuel du modèle)."""
        url = f"{self.base}/models/{urllib.parse.quote(model)}:generateContent"
        last = None
        # L'API a renommé le bloc de configuration d'image (imageConfig -> responseFormat.image) : on essaie la forme
        # actuelle, puis l'ancienne, puis sans réglage de format si le modèle n'en accepte aucun.
        for variant in ("responseFormat", "imageConfig", None):
            try:
                j = _json(url, self._h(), self._body(prompt, refs, aspect, size, variant), timeout=180)
                break
            except ProviderError as e:
                last = e
                msg = str(e)
                if "HTTP 400" in msg and re.search(r"responseFormat|imageConfig|Unknown name|Invalid JSON payload|"
                                                   r"aspect|imageSize|image_size", msg, re.I):
                    continue
                raise ImageError(_explain(msg)) from None
        else:
            raise ImageError(_explain(str(last)))
        images, text = [], []
        block = (j.get("promptFeedback") or {}).get("blockReason")
        reasons = []
        for c in j.get("candidates") or []:
            reasons.append(c.get("finishReason") or "")
            for p in (c.get("content") or {}).get("parts") or []:
                if p.get("thought"):
                    continue
                inline = p.get("inlineData") or p.get("inline_data")
                if inline and inline.get("data"):
                    images.append((base64.b64decode(inline["data"]),
                                   inline.get("mimeType") or inline.get("mime_type") or "image/png"))
                elif p.get("text"):
                    text.append(p["text"])
        if not images:
            why = block or next((r for r in reasons if r and r != "STOP"), "")
            detail = " ".join(text).strip()[:300]
            if why in ("SAFETY", "IMAGE_SAFETY", "PROHIBITED_CONTENT", "OTHER", "BLOCKLIST") or block:
                raise ImageError(f"Le fournisseur a refusé cette demande (motif : {why or block}). {detail}".strip())
            raise ImageError("Le modèle n'a renvoyé aucune image." + (f" Réponse : {detail}" if detail else ""))
        return images, " ".join(text).strip()


def _explain(msg):
    if "HTTP 429" in msg:
        return ("Quota de génération d'images atteint ou facturation non activée sur la clé Gemini "
                "(l'offre gratuite de l'API ne comprend pas la génération d'images). " + msg[:300])
    if "HTTP 403" in msg or "HTTP 401" in msg:
        return "Clé Gemini refusée pour la génération d'images. " + msg[:300]
    if "HTTP 404" in msg:
        return "Modèle d'images introuvable chez Google : choisissez-en un autre dans Réglages › Images. " + msg[:300]
    return msg[:500]


ADAPTERS = {"gemini": GeminiImages}


class ImageService:
    def __init__(self, ws, settings, keys):
        self.ws, self.settings, self.keys = ws, settings, keys
        self._models = {}
        self._lock = threading.Lock()

    def adapter(self, pid=None):
        pid = pid or self.settings.get("image_provider", "gemini")
        cls = ADAPTERS.get(pid)
        if not cls:
            raise ImageError(f"Fournisseur d'images inconnu : {pid}")
        if not self.keys.has(cls.key_name):
            raise ImageError(f"Aucune clé {cls.label} : ajoutez votre clé Gemini dans l'onglet Modèles. {cls.note}")
        base = (self.settings.get("providers", {}).get(cls.key_name) or {}).get("base_url") or \
            "https://generativelanguage.googleapis.com/v1beta"
        return cls(base, self.keys.get(cls.key_name))

    def available(self):
        pid = self.settings.get("image_provider", "gemini")
        cls = ADAPTERS.get(pid)
        if not cls:
            return False, "fournisseur d'images inconnu"
        if not self.keys.has(cls.key_name):
            return False, f"clé {cls.label} manquante (onglet Modèles)"
        return True, ""

    def state(self):
        out = []
        for pid, cls in ADAPTERS.items():
            out.append({"id": pid, "label": cls.label, "has_key": self.keys.has(cls.key_name), "note": cls.note,
                        "models": self._models.get(pid) or cls.DEFAULT_MODELS, "listed": pid in self._models})
        ok, why = self.available()
        return {"providers": out, "available": ok, "reason": why, "ratios": RATIOS, "formats": FORMATS,
                "sizes": SIZES, "styles": {k: v[0] for k, v in STYLES.items()},
                "provider": self.settings.get("image_provider", "gemini"), "model": self.model()}

    def refresh_models(self):
        try:
            ad = self.adapter()
            models = ad.list_models()
            if models:
                with self._lock:
                    self._models[ad.id] = models
            return models
        except (ImageError, ProviderError):
            return []

    def model(self):
        pid = self.settings.get("image_provider", "gemini")
        m = self.settings.get("image_model", "")
        return m or ADAPTERS.get(pid, GeminiImages).DEFAULT_MODELS[0]

    # ------------------------------------------------------------------ entrées
    def upload(self, data: bytes, name="image", project_id=None):
        if len(data) > MAX_REF_BYTES:
            raise ImageError("Image trop lourde (15 Mo au maximum)")
        mime = sniff(data)
        if not mime:
            raise ImageError("Format d'image non reconnu (PNG, JPEG, WebP ou GIF)")
        return self.ws.add_file("image", data, mime, name[:200] or "image importée", project_id,
                                meta={"origin": "importée"}, saved=True, name_hint=name)

    def _refs(self, ids):
        out = []
        for rid in ids or []:
            data, mime = self.ws.resource_bytes(int(rid))
            if not data:
                raise ImageError(f"Image de référence {rid} introuvable")
            out.append((data, mime or sniff(data) or "image/png"))
        return out

    @staticmethod
    def compose(prompt, style="libre", has_refs=False, edit=False):
        prompt = re.sub(r"^\s*(jarvis[\s,:]+)?", "", prompt.strip(), flags=re.I)
        s = STYLES.get(style, STYLES["libre"])[1]
        out = prompt
        if edit:
            out = ("Modifie l'image fournie selon cette consigne, en conservant tout le reste à l'identique "
                   f"(cadrage, éléments, lumière) : {prompt}")
        elif has_refs:
            out = f"En t'appuyant sur l'image ou les images de référence fournies : {prompt}"
        if s:
            out += f"\n\nStyle visuel : {s}."
        return out

    # ------------------------------------------------------------------ génération
    def generate(self, prompt, style="libre", aspect="1:1", size="", n=1, refs=(), project_id=None, conv_id=None,
                 mission_id=None, parent_id=None, edit=False, title=None):
        prompt = (prompt or "").strip()
        if len(prompt) < 3:
            raise ImageError("Décrivez l'image souhaitée.")
        aspect = FORMATS.get(aspect, aspect)
        if aspect not in RATIOS:
            raise ImageError(f"Format non pris en charge : {aspect}")
        if size and size not in SIZES:
            raise ImageError("Taille non prise en charge (1K, 2K ou 4K)")
        n = max(1, min(4, int(n or 1)))
        ad = self.adapter()
        model = self.model()
        ref_data = self._refs(refs)
        full = self.compose(prompt, style, bool(ref_data), edit)
        results, errors = [], []

        def one(i):
            return ad.generate(model, full + (f"\n\n(Variante {i + 1} : propose une composition différente.)"
                                              if n > 1 and i else ""), ref_data, aspect, size)
        with ThreadPoolExecutor(max_workers=min(n, 4)) as ex:
            futs = [ex.submit(one, i) for i in range(n)]
            for i, f in enumerate(futs):
                try:
                    imgs, text = f.result()
                except (ImageError, ProviderError) as e:
                    errors.append(str(e))
                    continue
                for data, mime in imgs[:1]:
                    meta = {"prompt": prompt, "full_prompt": full, "style": style, "aspect": aspect, "size": size,
                            "provider": ad.id, "model": model, "refs": [int(r) for r in refs or []],
                            "parent": parent_id, "variant": i + 1, "model_text": text[:500], "origin": "générée"}
                    r = self.ws.add_file("image", data, sniff(data) or mime, title or _title(prompt), project_id,
                                         conv_id, mission_id, meta, saved=False, name_hint=".png", dedupe=False)
                    results.append(r)
        if not results:
            raise ImageError(errors[0] if errors else "Aucune image générée.")
        self.ws.log("concepteur", f"{len(results)} image(s) générée(s) — {model}", prompt[:300], True, "images",
                    mission_id)
        return {"images": results, "errors": errors, "model": model, "prompt": full}

    def edit(self, rid, instruction, style="libre", n=1, project_id=None, conv_id=None, mission_id=None, extra_refs=()):
        src = self.ws.resource(rid)
        if not src or src["kind"] != "image":
            raise ImageError("Image introuvable")
        aspect = (src.get("meta") or {}).get("aspect") or "1:1"
        return self.generate(instruction, style, aspect, (src.get("meta") or {}).get("size", ""), n,
                             [rid, *extra_refs], project_id if project_id is not None else src.get("project_id"),
                             conv_id, mission_id, parent_id=rid, edit=True, title=_title(instruction))

    def upscale(self, rid, size="2K", project_id=None):
        src = self.ws.resource(rid)
        if not src or src["kind"] != "image":
            raise ImageError("Image introuvable")
        aspect = (src.get("meta") or {}).get("aspect") or "1:1"
        return self.generate("Reproduis cette image à l'identique (mêmes éléments, mêmes couleurs, même cadrage) en "
                             "plus haute résolution, plus nette et plus détaillée.", "libre", aspect, size, 1, [rid],
                             project_id if project_id is not None else src.get("project_id"), parent_id=rid,
                             edit=True, title=f"{src['title']} ({size})")


def _title(prompt):
    t = re.sub(r"^\s*(jarvis[\s,:]+)?(peux-tu |pourrais-tu )?(g[ée]n[èe]re|cr[ée]e|dessine|fais|produis|r[ée]alise|"
               r"modifie|transforme)(-moi| moi)?\s+", "", prompt.strip(), flags=re.I)
    t = re.sub(r"\s+", " ", t).strip(" .!?")
    return (t[:1].upper() + t[1:])[:80] or "Image"


def to_json(o):
    return json.dumps(o, ensure_ascii=False)
