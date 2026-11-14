"""Mises à jour sans archive et installation comme application."""
import hashlib
import json
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import install  # noqa: E402
from jarvis_core import __version__  # noqa: E402
from jarvis_core.config import Settings  # noqa: E402
from jarvis_core.updater import UpdateError, Updater, build_manifest, safe_path, vtuple  # noqa: E402

REPO = "proprio/jarvis"


class Published:
    """Faux raw.githubusercontent.com : /<propriétaire>/<dépôt>/<ref>/<chemin>."""

    def __init__(self, root):
        self.root = Path(root)
        self.hits = []
        me = self

        class H(SimpleHTTPRequestHandler):
            def __init__(self, *a, **k):
                super().__init__(*a, directory=str(me.root), **k)

            def log_message(self, *a):
                pass

            def do_GET(self):
                me.hits.append(self.path)
                super().do_GET()

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.raw = f"http://127.0.0.1:{self.httpd.server_address[1]}/{{repo}}/{{ref}}/{{path}}"

    def publish(self, tree, version, notes="", main=True):
        """Publie un dossier sous le tag v<version> (et sur main si demandé)."""
        m = build_manifest(tree, version, notes)
        m["ref"] = f"v{version}"
        dest = self.root / REPO / f"v{version}"
        shutil.rmtree(dest, ignore_errors=True)
        shutil.copytree(tree, dest)
        if main:
            (self.root / REPO / "main").mkdir(parents=True, exist_ok=True)
            (self.root / REPO / "main" / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
        return m

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def tree(root, files):
    root = Path(root)
    for p, c in files.items():
        f = root / p
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(c, encoding="utf-8")
    return root


V1 = {"run.py": "print('v1')\n", "Lancer Jarvis.bat": "@echo v1\r\n", "jarvis_core/a.py": "A = 1\n", "jarvis_core/old.py": "OLD = 1\n",
      "web/app.js": "console.log(1)\n", "README.md": "v1\n"}
V2 = {"run.py": "print('v2')\n", "Lancer Jarvis.bat": "@echo v2\r\n", "jarvis_core/a.py": "A = 1\n", "jarvis_core/new.py": "NEW = 2\n",
      "web/app.js": "console.log(2)\n", "README.md": "v1\n"}


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="jarvis-upd-"))
        self.pub = Published(self.tmp / "serveur")
        self.app = tree(self.tmp / "install" / "app", V1)
        (self.app / "manifest.json").write_text(json.dumps(build_manifest(self.app, __version__)), encoding="utf-8")
        self.settings = Settings(self.tmp / "settings.json")
        self.settings.update({"update_repo": REPO})
        self.up = Updater(self.settings, self.app, self.pub.raw)

    def tearDown(self):
        self.pub.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_version_compare_and_paths(self):
        self.assertGreater(vtuple("0.10.0"), vtuple("0.9.3"))
        self.assertEqual(safe_path("jarvis_core/x.py"), "jarvis_core/x.py")
        for bad in ("../x.py", "/etc/passwd", "C:/Windows/x.py", "jarvis_core/../../x.py", "autre/x.py", "x.exe"):
            with self.assertRaises(UpdateError):
                safe_path(bad)
        self.settings.update({"update_repo": "pas un dépôt"})
        self.assertIn("invalide", Updater(self.settings, self.app, self.pub.raw).check()["error"])

    def test_nothing_published_is_reported_plainly(self):
        info = self.up.check()
        self.assertIsNone(info["available"])
        self.assertIn("Aucune version publiée", info["error"])

    def test_same_version_is_not_an_update(self):
        self.pub.publish(tree(self.tmp / "v1", V1), __version__)
        info = self.up.check()
        self.assertEqual((info["available"], info["error"]), (None, ""))
        with self.assertRaises(UpdateError):
            self.up.install()

    def test_only_changed_files_are_downloaded_then_rollback(self):
        self.pub.publish(tree(self.tmp / "v2", V2), "99.0.0", "Nouvelle sphère")
        info = self.up.check()
        self.assertEqual((info["available"], info["notes"], info["changes"]), ("99.0.0", "Nouvelle sphère", 4))
        self.pub.hits.clear()
        res = self.up.install()
        self.assertEqual(res, {"installed": "99.0.0", "files": 4})
        got = sorted(h.split("/v99.0.0/")[1] for h in self.pub.hits)
        self.assertEqual(got, ["Lancer%20Jarvis.bat", "jarvis_core/new.py", "run.py", "web/app.js"])   # espace encodé
        self.assertEqual((self.app / "Lancer Jarvis.bat").read_bytes(), b"@echo v2\r\n")   # README et a.py inchangés : non téléchargés
        self.assertEqual((self.app / "run.py").read_text(), "print('v2')\n")
        self.assertTrue((self.app / "jarvis_core" / "new.py").exists())
        self.assertFalse((self.app / "jarvis_core" / "old.py").exists())        # retiré de la nouvelle version
        self.assertEqual(json.loads((self.app / "manifest.json").read_text())["version"], "99.0.0")
        self.assertFalse((self.tmp / "install" / "update-staging").exists())
        info = self.up.info()
        self.assertEqual((info["installed"], info["can_rollback"], info["previous"]), ("99.0.0", True, __version__))
        self.up.rollback()
        self.assertEqual((self.app / "run.py").read_text(), "print('v1')\n")
        self.assertTrue((self.app / "jarvis_core" / "old.py").exists())
        self.assertFalse((self.app / "jarvis_core" / "new.py").exists())
        self.assertFalse(self.up.info()["can_rollback"])

    def test_tampered_file_changes_nothing(self):
        self.pub.publish(tree(self.tmp / "v2", V2), "99.0.0")
        (self.pub.root / REPO / "v99.0.0" / "web" / "app.js").write_text("pirate()", encoding="utf-8")
        self.up.check()
        with self.assertRaises(UpdateError) as e:
            self.up.install()
        self.assertIn("web/app.js", str(e.exception))
        self.assertEqual((self.app / "run.py").read_text(), "print('v1')\n")
        self.assertTrue((self.app / "jarvis_core" / "old.py").exists())
        self.assertFalse((self.tmp / "install" / "previous").exists())

    def test_manifest_with_dangerous_path_is_refused(self):
        m = self.pub.publish(tree(self.tmp / "v2", V2), "99.0.0")
        m["files"]["../../evil.py"] = {"sha256": "0" * 64, "size": 1}
        (self.pub.root / REPO / "main" / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
        info = self.up.check()
        self.assertIsNone(info["available"])
        self.assertIn("Chemin refusé", info["error"])

    def test_real_project_manifest_covers_the_app(self):
        m = build_manifest(ROOT, __version__)
        for must in ("run.py", "install.py", "install.ps1", "jarvis.ico", "Lancer Jarvis.bat", "Installer Jarvis.bat",
                     "jarvis_core/updater.py", "web/app.js", "web/index.html"):
            self.assertIn(must, m["files"])
        self.assertFalse([p for p in m["files"] if "__pycache__" in p or p.endswith(".pyc") or "node_modules" in p])
        for p in m["files"]:
            safe_path(p)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="jarvis-inst-"))
        self.pub = Published(self.tmp / "serveur")

    def tearDown(self):
        self.pub.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_install_from_published_version_then_reinstall(self):
        self.pub.publish(tree(self.tmp / "v1", V1), "5.0.0")
        dest = self.tmp / "Programs" / "Jarvis"
        code = install.main(["--from-github", "--dest", str(dest), "--repo", REPO, "--raw", self.pub.raw, "--no-launch"])
        self.assertEqual(code, 0)
        self.assertEqual((dest / "app" / "run.py").read_text(), "print('v1')\n")
        self.assertEqual(json.loads((dest / "app" / "manifest.json").read_text())["version"], "5.0.0")
        self.pub.publish(tree(self.tmp / "v2", V2), "6.0.0")
        self.assertEqual(install.main(["--from-github", "--dest", str(dest), "--repo", REPO, "--raw", self.pub.raw,
                                       "--no-launch"]), 0)
        self.assertEqual((dest / "app" / "run.py").read_text(), "print('v2')\n")
        self.assertFalse((dest / "app" / "jarvis_core" / "old.py").exists())
        self.assertEqual((dest / "previous" / "run.py").read_text(), "print('v1')\n")

    def test_tampered_download_aborts_without_touching_the_install(self):
        self.pub.publish(tree(self.tmp / "v1", V1), "5.0.0")
        dest = self.tmp / "Programs" / "Jarvis"
        install.main(["--from-github", "--dest", str(dest), "--repo", REPO, "--raw", self.pub.raw, "--no-launch"])
        self.pub.publish(tree(self.tmp / "v2", V2), "6.0.0")
        (self.pub.root / REPO / "v6.0.0" / "run.py").write_text("pirate()", encoding="utf-8")
        with self.assertRaises(SystemExit):
            install.main(["--from-github", "--dest", str(dest), "--repo", REPO, "--raw", self.pub.raw, "--no-launch"])
        self.assertEqual((dest / "app" / "run.py").read_text(), "print('v1')\n")

    def test_install_from_this_folder(self):
        dest = self.tmp / "Programs" / "Jarvis"
        self.assertEqual(install.main(["--dest", str(dest), "--no-launch"]), 0)
        app = dest / "app"
        for must in ("run.py", "jarvis_core/server.py", "web/app.js", "jarvis.ico"):
            self.assertTrue((app / must).exists(), must)
        self.assertFalse(list(app.rglob("__pycache__")))
        m = json.loads((app / "manifest.json").read_text())
        self.assertEqual(m["version"], __version__)
        f = "jarvis_core/server.py"
        self.assertEqual(hashlib.sha256((app / f).read_bytes()).hexdigest(), m["files"][f]["sha256"])


class ServerRoutes(unittest.TestCase):
    def test_update_routes(self):
        import urllib.request
        from jarvis_core.server import App, serve
        tmp = Path(tempfile.mkdtemp(prefix="jarvis-srv-"))
        pub = Published(tmp / "serveur")
        appdir = tree(tmp / "inst" / "app", V1)
        pub.publish(tree(tmp / "v2", V2), "99.1.0", "Test")
        (tmp / "data").mkdir()
        a = App(tmp / "data", {"app_dir": appdir, "update_raw": pub.raw})
        a.settings.update({"update_repo": REPO})
        httpd = serve(a, 0)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def call(method, path):
            req = urllib.request.Request(f"http://127.0.0.1:{httpd.server_address[1]}{path}", method=method, data=b"{}" if method == "POST" else None,
                                         headers={"X-Jarvis-Token": a.token, "Content-Type": "application/json"})
            try:
                with op.open(req, timeout=20) as r:
                    return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read())
        try:
            self.assertEqual(call("GET", "/api/state")[1]["update"]["current"], __version__)
            code, info = call("POST", "/api/update/check")
            self.assertEqual((code, info["available"]), (200, "99.1.0"))
            code, res = call("POST", "/api/update/install")
            self.assertEqual((code, res["installed"]), (200, "99.1.0"))
            self.assertEqual((appdir / "run.py").read_text(), "print('v2')\n")
            self.assertEqual(call("POST", "/api/restart")[0], 400)        # pas lancé par main() : pas de redémarrage
            self.assertEqual(call("POST", "/api/heartbeat")[0], 200)
            self.assertGreater(a.last_ui, 0)
            self.assertEqual(call("POST", "/api/update/rollback")[0], 200)
            self.assertEqual((appdir / "run.py").read_text(), "print('v1')\n")
        finally:
            httpd.shutdown()
            httpd.server_close()
            a.close()
            pub.stop()
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
