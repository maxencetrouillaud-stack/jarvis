"""Installe Jarvis comme une application Windows.

    py install.py                 installe depuis ce dossier
    py install.py --from-github   installe la dernière version publiée (utilisé par install.ps1)
    py install.py --uninstall     désinstalle (vos données sont conservées, sauf si vous demandez le contraire)

Ce que fait l'installation :
  - copie l'application dans %LOCALAPPDATA%\\Programs\\Jarvis\\app ;
  - crée les raccourcis « Jarvis » sur le Bureau et dans le menu Démarrer (sans fenêtre noire) ;
  - inscrit Jarvis dans « Applications installées » de Windows (pour pouvoir le désinstaller) ;
  - lance Jarvis.
Vos données (mémoire, connaissances, clés, réglages) restent dans %APPDATA%\\Jarvis : rien n'y est touché.
Ensuite, les nouvelles versions s'installent depuis Jarvis lui-même (Réglages › Mises à jour), sans rien télécharger à la main.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

REPO = "maxencetrouillaud-stack/jarvis"
RAW = "https://raw.githubusercontent.com/{repo}/{ref}/{path}"
HERE = Path(__file__).resolve().parent


def store_python():
    """Python du Microsoft Store : ses écritures dans AppData sont redirigées dans un dossier caché."""
    return sys.platform == "win32" and "WindowsApps" in sys.executable


def install_root():
    if store_python():  # hors d'AppData, pour que les raccourcis et l'Explorateur voient bien l'application
        return Path.home() / "Jarvis"
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "Programs" / "Jarvis"
    return Path.home() / ".local" / "share" / "jarvis-app"


def say(t=""):
    print(t, flush=True)


def ping(port=8765):
    try:
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with op.open(f"http://127.0.0.1:{port}/api/ping", timeout=1.5) as r:
            d = json.loads(r.read())
            return d.get("version") if d.get("app") == "jarvis" else None
    except Exception:
        return None


def get(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "Jarvis-installeur", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def local_files():
    sys.path.insert(0, str(HERE))
    from jarvis_core.updater import build_manifest  # noqa: E402
    from jarvis_core import __version__  # noqa: E402
    m = build_manifest(HERE, __version__)
    return m, lambda p: (HERE / p).read_bytes()


def remote_files(repo, raw):
    m = json.loads(get(raw.format(repo=repo, ref="main", path="manifest.json")).decode("utf-8"))
    ref = m.get("ref") or f"v{m['version']}"
    return m, lambda p: get(raw.format(repo=repo, ref=ref, path=urllib.parse.quote(p)))


def copy_app(m, read, app):
    """Écrit les fichiers dans un dossier temporaire, vérifie chaque empreinte, puis remplace l'ancien dossier."""
    tmp = app.parent / "app-nouveau"
    shutil.rmtree(tmp, ignore_errors=True)
    n = len(m["files"])
    for i, (p, meta) in enumerate(sorted(m["files"].items())):
        if p.startswith("/") or ".." in p.split("/"):
            raise SystemExit(f"Chemin refusé : {p}")
        data = read(p)
        if hashlib.sha256(data).hexdigest() != meta["sha256"]:
            raise SystemExit(f"Fichier altéré ou incomplet : {p}. Installation annulée, rien n'a été modifié.")
        dest = tmp / p
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        if i % 20 == 0 or i == n - 1:
            say(f"  {i + 1}/{n} fichiers")
    (tmp / "manifest.json").write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
    if app.exists():
        old = app.parent / "previous"
        shutil.rmtree(old, ignore_errors=True)
        app.rename(old)
    tmp.rename(app)


def pythonw():
    if store_python():  # le Python du Store se lance par ses alias, pas par son dossier protégé
        alias = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WindowsApps"
        for name in (f"pythonw{sys.version_info.major}.{sys.version_info.minor}.exe", "pythonw3.exe", "pythonw.exe"):
            if (alias / name).exists():
                return alias / name
    exe = Path(sys.executable)
    w = exe.with_name("pythonw.exe")
    return w if w.exists() else exe


def shortcuts(app, version):
    if sys.platform != "win32":
        return []
    target, script, icon = pythonw(), app / "run.py", app / "jarvis.ico"
    places = []
    desktop = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
    try:  # le Bureau peut être déplacé (OneDrive) : on demande son vrai emplacement à Windows
        out = subprocess.run(["powershell", "-NoProfile", "-Command", "[Environment]::GetFolderPath('Desktop')"],
                             capture_output=True, text=True, timeout=20).stdout.strip()
        if out:
            desktop = Path(out)
    except Exception:
        pass
    start = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    for folder in (desktop, start):
        if not folder.exists():
            continue
        lnk = folder / "Jarvis.lnk"
        ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');"
              f"$s.TargetPath='{target}';$s.Arguments='\"{script}\" --app';$s.WorkingDirectory='{app}';"
              f"$s.IconLocation='{icon},0';$s.Description='Jarvis, assistant personnel';$s.Save()")
        r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                           capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            places.append(str(lnk))
        else:
            say(f"  raccourci non créé dans {folder} : {r.stderr.strip()[:200]}")
    try:
        import winreg
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Jarvis")
        py = Path(sys.executable)
        for name, val in (("DisplayName", "Jarvis"), ("DisplayVersion", version), ("Publisher", "Maxence"),
                          ("DisplayIcon", str(icon)), ("InstallLocation", str(app.parent)),
                          ("UninstallString", f'"{py}" "{app / "install.py"}" --uninstall')):
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, val)
        winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)
        winreg.CloseKey(key)
    except Exception as e:
        say(f"  inscription dans « Applications installées » impossible : {e}")
    return places


def uninstall():
    root = install_root()
    if ping():
        say("Jarvis est ouvert : fermez sa fenêtre, attendez quelques secondes, puis relancez la désinstallation.")
        return 1
    say(f"Désinstallation de Jarvis ({root})")
    if sys.platform == "win32":
        desktop = Path(os.environ.get("USERPROFILE", "")) / "Desktop"
        start = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        for lnk in (desktop / "Jarvis.lnk", start / "Jarvis.lnk"):
            lnk.unlink(missing_ok=True)
        try:
            import winreg
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Jarvis")
        except OSError:
            pass
    os.chdir(Path.home())
    shutil.rmtree(root, ignore_errors=True)
    data = Path(os.environ.get("APPDATA", str(Path.home()))) / "Jarvis" if sys.platform == "win32" else Path.home() / ".jarvis"
    say("Application supprimée.")
    say(f"Vos données sont conservées dans {data} (mémoire, connaissances, clés, réglages).")
    try:
        if input("Les supprimer aussi ? Tapez SUPPRIMER pour confirmer, ou Entrée pour les garder : ").strip() == "SUPPRIMER":
            shutil.rmtree(data, ignore_errors=True)
            say("Données supprimées.")
    except EOFError:
        pass
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Installation de Jarvis")
    ap.add_argument("--from-github", action="store_true")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--dest", help=argparse.SUPPRESS)
    ap.add_argument("--repo", default=REPO, help=argparse.SUPPRESS)
    ap.add_argument("--raw", default=RAW, help=argparse.SUPPRESS)
    ap.add_argument("--no-launch", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if sys.version_info < (3, 9):
        say("Jarvis demande Python 3.9 ou plus récent : https://www.python.org/downloads/")
        return 1
    if a.uninstall:
        return uninstall()
    root = Path(a.dest) if a.dest else install_root()
    app = root / "app"
    for _ in range(40):  # une version ouverte bloquerait le remplacement des fichiers
        if not ping():
            break
        if _ == 0:
            say("Jarvis est ouvert : fermez sa fenêtre (ou sa fenêtre noire). L'installation continue dès qu'il est arrêté…")
        time.sleep(3)
    else:
        say("Jarvis est toujours ouvert : installation annulée.")
        return 1
    say("Installation de Jarvis")
    say("  téléchargement de la dernière version…" if a.from_github else f"  depuis {HERE}")
    try:
        m, read = remote_files(a.repo, a.raw) if a.from_github else local_files()
    except Exception as e:
        say(f"Impossible de lire la version à installer : {e}")
        return 1
    root.mkdir(parents=True, exist_ok=True)
    copy_app(m, read, app)
    places = shortcuts(app, m["version"])
    say(f"Jarvis {m['version']} est installé dans {app}")
    for p in places:
        say(f"  raccourci : {p}")
    say("Les prochaines versions s'installeront depuis Jarvis : Réglages › Mises à jour.")
    if not a.no_launch:
        args = [str(pythonw()), str(app / "run.py"), "--app"]
        kw = {"cwd": str(app)}
        if sys.platform == "win32":
            kw["creationflags"] = 0x00000008 | 0x00000200
        subprocess.Popen(args, **kw)
        say("Jarvis démarre…")
    return 0


if __name__ == "__main__":
    code = main()
    if sys.platform == "win32" and not os.environ.get("JARVIS_NO_PAUSE"):
        try:
            input("\nAppuyez sur Entrée pour fermer cette fenêtre.")
        except EOFError:
            pass
    sys.exit(code)
