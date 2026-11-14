"""Faux serveurs reproduisant le format de fil des 4 API (Anthropic, OpenAI, Gemini, Ollama).

Ils servent uniquement aux tests automatiques : ils vérifient que Jarvis envoie des requêtes
au bon format et sait lire les réponses en flux. Ils ne remplacent pas un essai avec une vraie clé.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Fake:
    """script(kind, body) -> texte de réponse ; kind est 'chat'."""

    def __init__(self, flavor, script=None, models=("modele-a", "modele-b"), fail=None):
        self.flavor, self.models, self.fail = flavor, list(models), fail
        self.script = script or (lambda body: "Réponse de test.")
        self.requests = []
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _j(self, obj, code=200):
                b = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def _stream(self, lines, ctype="text/event-stream"):
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.end_headers()
                for ln in lines:
                    self.wfile.write(ln.encode("utf-8"))
                    self.wfile.flush()

            def do_GET(self):
                fake.requests.append(("GET", self.path, dict(self.headers), None))
                f = fake.flavor
                if f == "anthropic" and self.path.startswith("/v1/models"):
                    if self.headers.get("x-api-key") != "cle-test":
                        return self._j({"error": {"message": "invalid x-api-key"}}, 401)
                    return self._j({"data": [{"id": m} for m in fake.models]})
                if f == "openai" and self.path == "/models":
                    return self._j({"data": [{"id": m} for m in fake.models] + [{"id": "text-embedding-3-small"}]})
                if f == "gemini" and self.path.startswith("/models"):
                    return self._j({"models": [{"name": f"models/{m}", "supportedGenerationMethods": ["generateContent"]}
                                               for m in fake.models]})
                if f == "ollama" and self.path == "/api/tags":
                    return self._j({"models": [{"name": m} for m in fake.models]})
                self._j({"error": "not found"}, 404)

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                fake.requests.append(("POST", self.path, dict(self.headers), body))
                f = fake.flavor
                if "embed" in self.path:
                    text = body.get("input") or body.get("content", {}).get("parts", [{}])[0].get("text", "")
                    vec = fake.embed(text)
                    if f == "openai":
                        return self._j({"data": [{"embedding": vec}]})
                    if f == "gemini":
                        return self._j({"embedding": {"values": vec}})
                    return self._j({"embeddings": [vec]})
                if fake.fail:
                    return self._j({"error": {"message": fake.fail}}, 500)
                if f in ("anthropic", "openai") and "temperature" in body and body["model"] == "sans-temperature":
                    return self._j({"error": {"message": "Unsupported parameter: temperature"}}, 400)
                text = fake.script(body)
                parts = [text[i:i + 7] for i in range(0, len(text), 7)] or [""]
                if f == "anthropic":
                    assert self.path == "/v1/messages" and body["stream"] and "max_tokens" in body
                    assert all(m["role"] in ("user", "assistant") for m in body["messages"])
                    lines = ['event: message_start\ndata: {"type":"message_start"}\n\n']
                    lines += ["event: content_block_delta\ndata: " + json.dumps(
                        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": p}}) + "\n\n"
                        for p in parts]
                    lines.append('event: message_stop\ndata: {"type":"message_stop"}\n\n')
                    return self._stream(lines)
                if f == "openai":
                    assert self.path == "/chat/completions" and body["stream"]
                    lines = ["data: " + json.dumps({"choices": [{"delta": {"content": p}}]}) + "\n\n" for p in parts]
                    lines.append("data: [DONE]\n\n")
                    return self._stream(lines)
                if f == "gemini":
                    assert ":streamGenerateContent" in self.path and "alt=sse" in self.path
                    assert all(c["role"] in ("user", "model") for c in body["contents"])
                    lines = ["data: " + json.dumps({"candidates": [{"content": {"parts": [{"text": p}]}}]}) + "\n\n"
                             for p in parts]
                    return self._stream(lines)
                if f == "ollama":
                    assert self.path == "/api/chat"
                    lines = [json.dumps({"message": {"role": "assistant", "content": p}, "done": False}) + "\n"
                             for p in parts]
                    lines.append(json.dumps({"message": {"content": ""}, "done": True}) + "\n")
                    return self._stream(lines, "application/x-ndjson")
                self._j({"error": "not found"}, 404)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        self.httpd.handle_error = lambda *a: None  # un client qui interrompt le flux n'est pas une erreur
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @staticmethod
    def embed(text):
        """Faux embedding « sémantique » : 3 axes thématiques, pour tester la recherche par le sens."""
        t = text.lower()
        axes = [("badminton", "raquette", "volant", "sport", "court"),
                ("cuisine", "recette", "plat", "manger", "repas"),
                ("python", "code", "programme", "logiciel")]
        v = [float(sum(w in t for w in ax)) for ax in axes] + [0.1]
        return v

    def last_body(self):
        return [r for r in self.requests if r[0] == "POST"][-1][3]

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class FakeWeb:
    """Faux site web + fausse API Wikipédia (format MediaWiki), pour tester la collecte."""

    def __init__(self):
        self.pages = {}      # chemin -> (type de contenu, octets)
        self.articles = {}   # titre -> texte
        self.hits = []
        web = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                from urllib.parse import parse_qs, urlparse
                u = urlparse(self.path)
                web.hits.append(self.path)
                if u.path == "/w/api.php":
                    q = {k: v[0] for k, v in parse_qs(u.query).items()}
                    assert "Jarvis" in self.headers.get("User-Agent", "")
                    if q.get("list") == "search":
                        words = q["srsearch"].lower().split()
                        res = [{"title": t, "snippet": f"<span class=\"searchmatch\">{t}</span> …"}
                               for t in web.articles if any(w in (t + " " + web.articles[t]).lower() for w in words)]
                        body = {"query": {"search": res[: int(q.get("srlimit", 10))]}}
                    else:
                        t = q.get("titles", "")
                        if t in web.articles:
                            body = {"query": {"pages": [{"title": t, "extract": web.articles[t],
                                                         "fullurl": f"https://fr.wikipedia.org/wiki/{t.replace(' ', '_')}",
                                                         "revisions": [{"timestamp": "2026-05-01T10:00:00Z"}]}]}}
                        else:
                            body = {"query": {"pages": [{"title": t, "missing": True}]}}
                    raw, ctype = json.dumps(body).encode(), "application/json"
                elif u.path == "/redirect-local":
                    self.send_response(302)
                    self.send_header("Location", "http://127.0.0.1:1/secret")
                    self.end_headers()
                    return
                elif u.path in web.pages:
                    ctype, raw = web.pages[u.path]
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class FakeScience:
    """Fausses API arXiv (Atom), OpenAlex, HAL et Brave Search, au format de fil des vraies, pour tester le chercheur."""

    def __init__(self):
        self.arxiv, self.openalex, self.hal, self.brave = [], [], [], []
        self.hits, self.pages = [], {}
        me = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, raw, ctype="application/json", code=200):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                from urllib.parse import parse_qs, urlparse
                from xml.sax.saxutils import escape
                u = urlparse(self.path)
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                me.hits.append((u.path, q, dict(self.headers)))
                if u.path == "/arxiv":
                    assert q["search_query"].startswith("all:") and "max_results" in q
                    entries = "".join(
                        f"<entry><id>http://arxiv.org/abs/{e['id']}</id><title>{escape(e['title'])}</title>"
                        f"<summary>{escape(e['summary'])}</summary><published>2026-03-01T00:00:00Z</published>"
                        f"<author><name>A. Auteur</name></author></entry>" for e in me.arxiv[: int(q["max_results"])])
                    return self._send(f'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">{entries}</feed>'.encode(),
                                      "application/atom+xml")
                if u.path == "/openalex/works":
                    assert "search" in q and "per_page" in q
                    res = []
                    for w in me.openalex[: int(q["per_page"])]:
                        inv = {}
                        for i, word in enumerate(w["abstract"].split()):
                            inv.setdefault(word, []).append(i)
                        res.append({"id": f"https://openalex.org/{w['id']}", "doi": f"https://doi.org/10.1000/{w['id']}",
                                    "display_name": w["title"], "publication_date": "2025-06-01", "type": "article",
                                    "is_retracted": w.get("retracted", False), "abstract_inverted_index": inv,
                                    "authorships": [{"author": {"display_name": "B. Chercheuse"}}],
                                    "primary_location": {"source": {"display_name": "Journal of Tests", "type": "journal"}}})
                    return self._send(json.dumps({"results": res}).encode())
                if u.path == "/hal":
                    assert q["wt"] == "json" and "fl" in q
                    docs = [{"halId_s": d["id"], "title_s": [d["title"]], "abstract_s": [d["abstract"]],
                             "uri_s": f"https://hal.science/{d['id']}", "authFullName_s": ["C. Doctorant"],
                             "publicationDateY_i": 2024, "docType_s": d.get("type", "ART")} for d in me.hal[: int(q["rows"])]]
                    return self._send(json.dumps({"response": {"numFound": len(docs), "docs": docs}}).encode())
                if u.path == "/brave":
                    if self.headers.get("X-Subscription-Token") != "cle-brave":
                        return self._send(b'{"error":"unauthorized"}', code=401)
                    res = [{"title": b["title"], "url": b["url"], "description": ""} for b in me.brave[: int(q["count"])]]
                    return self._send(json.dumps({"web": {"results": res}}).encode())
                if u.path in me.pages:
                    return self._send(me.pages[u.path].encode("utf-8"), "text/html; charset=utf-8")
                self._send(b"{}", code=404)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def endpoints(self):
        return {"arxiv": self.url + "/arxiv", "openalex": self.url + "/openalex", "hal": self.url + "/hal",
                "brave": self.url + "/brave"}

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
