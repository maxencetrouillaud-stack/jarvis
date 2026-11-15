"""Connecteurs de modèles : Anthropic (Claude), OpenAI (GPT), Google (Gemini), Ollama (local).

Chaque connecteur expose la même interface :
    list_models() -> [str]
    stream(model, system, messages, temperature, max_tokens) -> itérateur de morceaux de texte
    embed(model, text) -> [float]   (si le fournisseur le permet)

Ajouter un futur fournisseur = ajouter une classe ici et l'inscrire dans REGISTRY.
Aucune dépendance externe : uniquement la bibliothèque standard.
"""
import json
import re
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT = 120


class ProviderError(Exception):
    pass


def _opener(url: str):
    host = urllib.parse.urlparse(url).hostname or ""
    if host in ("127.0.0.1", "localhost", "::1"):
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener()


def _request(url, headers=None, body=None, timeout=TIMEOUT):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    h = {"Content-Type": "application/json", "User-Agent": "Jarvis/0.1"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h, method="POST" if data is not None else "GET")
    try:
        return _opener(url).open(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        msg = raw
        try:
            j = json.loads(raw)
            err = j.get("error", j)
            msg = err.get("message", raw) if isinstance(err, dict) else str(err)
        except ValueError:
            pass
        raise ProviderError(f"HTTP {e.code} — {msg[:500]}") from None
    except urllib.error.URLError as e:
        raise ProviderError(f"Connexion impossible ({e.reason})") from None
    except (TimeoutError, OSError) as e:
        raise ProviderError(f"Connexion impossible ({e})") from None


def _json(url, headers=None, body=None, timeout=TIMEOUT):
    with _request(url, headers, body, timeout) as r:
        try:
            return json.loads(r.read().decode("utf-8"))
        except ValueError:
            raise ProviderError("Réponse illisible du fournisseur") from None


def _sse(resp):
    """Itère sur les charges utiles JSON d'un flux SSE (lignes 'data: ...')."""
    for raw in resp:
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            yield json.loads(payload)
        except ValueError:
            continue


class Provider:
    id = ""
    label = ""
    needs_key = True
    local = False

    def __init__(self, base_url: str, key: str = ""):
        self.base = (base_url or "").rstrip("/")
        self.key = key

    def ready(self) -> bool:
        return bool(self.base) and (bool(self.key) or not self.needs_key)

    def list_models(self):
        raise NotImplementedError

    def stream(self, model, system, messages, temperature=0.7, max_tokens=4096):
        raise NotImplementedError

    def embed(self, model, text):
        raise ProviderError(f"{self.label} ne fournit pas d'embeddings")

    def _retry_without_temperature(self, call, body, drop):
        """Certains modèles refusent le paramètre de température : on réessaie sans."""
        try:
            return call(body)
        except ProviderError as e:
            if "temperature" in str(e).lower() and str(e).startswith("HTTP 400"):
                drop(body)
                return call(body)
            raise


class Anthropic(Provider):
    id, label = "anthropic", "Claude (Anthropic)"

    def _h(self):
        return {"x-api-key": self.key, "anthropic-version": "2023-06-01"}

    def list_models(self):
        j = _json(f"{self.base}/v1/models?limit=100", self._h(), timeout=20)
        return [m["id"] for m in j.get("data", [])]

    def stream(self, model, system, messages, temperature=0.7, max_tokens=4096):
        body = {"model": model, "max_tokens": max_tokens, "messages": messages,
                "stream": True, "temperature": temperature}
        if system:
            body["system"] = system
        resp = self._retry_without_temperature(
            lambda b: _request(f"{self.base}/v1/messages", self._h(), b),
            body, lambda b: b.pop("temperature", None))
        with resp:
            for ev in _sse(resp):
                t = ev.get("type")
                if t == "content_block_delta" and ev.get("delta", {}).get("type") == "text_delta":
                    yield ev["delta"].get("text", "")
                elif t == "error":
                    raise ProviderError(ev.get("error", {}).get("message", "Erreur du fournisseur"))


class OpenAI(Provider):
    id, label = "openai", "GPT (OpenAI)"

    def _h(self):
        return {"Authorization": f"Bearer {self.key}"}

    def list_models(self):
        j = _json(f"{self.base}/models", self._h(), timeout=20)
        ids = [m["id"] for m in j.get("data", [])]
        skip = ("embedding", "whisper", "tts", "dall-e", "moderation", "audio", "realtime",
                "transcribe", "image", "davinci", "babbage", "search", "sora")
        chat = [i for i in ids if not any(s in i for s in skip)]
        return sorted(chat or ids)

    def stream(self, model, system, messages, temperature=0.7, max_tokens=4096):
        msgs = ([{"role": "system", "content": system}] if system else []) + messages
        body = {"model": model, "messages": msgs, "stream": True,
                "temperature": temperature, "max_completion_tokens": max_tokens}
        resp = self._retry_without_temperature(
            lambda b: _request(f"{self.base}/chat/completions", self._h(), b),
            body, lambda b: b.pop("temperature", None))
        with resp:
            for ev in _sse(resp):
                if ev.get("error"):
                    raise ProviderError(str(ev["error"].get("message", ev["error"])))
                for ch in ev.get("choices", []):
                    txt = (ch.get("delta") or {}).get("content")
                    if txt:
                        yield txt

    def embed(self, model, text):
        j = _json(f"{self.base}/embeddings", self._h(), {"model": model, "input": text}, timeout=30)
        return j["data"][0]["embedding"]


class Gemini(Provider):
    id, label = "gemini", "Gemini (Google)"

    def _h(self):
        return {"x-goog-api-key": self.key}

    def list_models(self):
        j = _json(f"{self.base}/models?pageSize=200", self._h(), timeout=20)
        out = []
        for m in j.get("models", []):
            name = m["name"].split("/", 1)[-1]
            # les modèles d'images (« -image ») ne savent pas tenir une conversation : ils vont dans Images
            if "generateContent" in m.get("supportedGenerationMethods", []) and not re.search(r"-image\b|imagen", name):
                out.append(name)
        return out

    def stream(self, model, system, messages, temperature=0.7, max_tokens=4096):
        contents = [{"role": "model" if m["role"] == "assistant" else "user",
                     "parts": [{"text": m["content"]}]} for m in messages]
        body = {"contents": contents,
                "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens}}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        url = f"{self.base}/models/{urllib.parse.quote(model)}:streamGenerateContent?alt=sse"
        got, reason = False, ""
        with _request(url, self._h(), body) as resp:
            for ev in _sse(resp):
                if ev.get("error"):
                    raise ProviderError(str(ev["error"].get("message", ev["error"])))
                reason = (ev.get("promptFeedback") or {}).get("blockReason") or reason
                for c in ev.get("candidates", []):
                    reason = c.get("finishReason") or reason
                    for p in (c.get("content") or {}).get("parts", []):
                        if p.get("text") and not p.get("thought"):
                            got = True
                            yield p["text"]
        if not got and reason and reason != "STOP":
            hint = " — augmentez la longueur maximale de réponse dans Réglages" if reason == "MAX_TOKENS" else ""
            raise ProviderError(f"réponse vide de Gemini (motif : {reason}){hint}")

    def embed(self, model, text):
        url = f"{self.base}/models/{urllib.parse.quote(model)}:embedContent"
        j = _json(url, self._h(), {"content": {"parts": [{"text": text}]}}, timeout=30)
        return j["embedding"]["values"]


class Ollama(Provider):
    id, label = "ollama", "Modèles locaux (Ollama)"
    needs_key = False
    local = True

    def _h(self):
        return {"Authorization": f"Bearer {self.key}"} if self.key else {}

    def list_models(self):
        j = _json(f"{self.base}/api/tags", self._h(), timeout=5)
        return [m["name"] for m in j.get("models", [])]

    def stream(self, model, system, messages, temperature=0.7, max_tokens=4096):
        msgs = ([{"role": "system", "content": system}] if system else []) + messages
        body = {"model": model, "messages": msgs, "stream": True,
                "options": {"temperature": temperature, "num_predict": max_tokens}}
        with _request(f"{self.base}/api/chat", self._h(), body, timeout=600) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("error"):
                    raise ProviderError(str(ev["error"]))
                txt = (ev.get("message") or {}).get("content")
                if txt:
                    yield txt

    def embed(self, model, text):
        j = _json(f"{self.base}/api/embed", self._h(), {"model": model, "input": text}, timeout=60)
        return j["embeddings"][0]


REGISTRY = {c.id: c for c in (Anthropic, OpenAI, Gemini, Ollama)}


def split_ref(ref: str):
    """'fournisseur:modèle' -> (fournisseur, modèle). Le modèle peut contenir ':' (ex. llama3:8b)."""
    if not ref or ":" not in ref:
        return "", ""
    p, m = ref.split(":", 1)
    return p, m
