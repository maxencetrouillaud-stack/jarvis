"""Sauvegardes automatiques et restauration (archives .zip dans le dossier backups/)."""
import json
import os
import re
import time
import zipfile
from pathlib import Path

from .config import DEFAULTS, _merge

NAME_RE = re.compile(r"^jarvis-\d{8}-\d{6}(-[a-z]+)?\.zip$")


class Backups:
    def __init__(self, home: Path, store, settings, keys, kb=None):
        self.home, self.store, self.settings, self.keys, self.kb = home, store, settings, keys, kb
        self.dir = home / "backups"
        self.dir.mkdir(parents=True, exist_ok=True)

    def list(self):
        out = []
        for p in sorted(self.dir.glob("jarvis-*.zip"), reverse=True):
            if NAME_RE.match(p.name):
                out.append({"name": p.name, "bytes": p.stat().st_size, "created": p.stat().st_mtime})
        return out

    def create(self, tag="manuel"):
        name = f"jarvis-{time.strftime('%Y%m%d-%H%M%S')}-{tag}.zip"
        tmp_db, tmp_kb = self.dir / "_snapshot.db", self.dir / "_snapshot_kb.db"
        try:
            self.store.snapshot(tmp_db)
            with zipfile.ZipFile(self.dir / name, "w", zipfile.ZIP_DEFLATED) as z:
                z.write(tmp_db, "jarvis.db")
                if self.kb:
                    self.kb.snapshot(tmp_kb)
                    z.write(tmp_kb, "knowledge.db")
                for f in ("settings.json", "keys.json"):
                    if (self.home / f).exists():
                        z.write(self.home / f, f)
        finally:
            tmp_db.unlink(missing_ok=True)
            tmp_kb.unlink(missing_ok=True)
        self._prune()
        return name

    def _prune(self):
        keep = max(1, int(self.settings.get("backup_keep", 7)))
        autos = [b for b in self.list() if b["name"].endswith("-auto.zip")]
        for b in autos[keep:]:
            (self.dir / b["name"]).unlink(missing_ok=True)

    def auto(self):
        """Une sauvegarde automatique par jour au plus."""
        autos = [b for b in self.list() if b["name"].endswith("-auto.zip")]
        if autos and time.time() - autos[0]["created"] < 20 * 3600:
            return None
        if self.store.stats()["messages"] == 0 and self.store.stats()["memories"] == 0:
            return None
        return self.create("auto")

    def restore(self, name):
        if not NAME_RE.match(name) or not (self.dir / name).exists():
            raise ValueError("Sauvegarde introuvable")
        with zipfile.ZipFile(self.dir / name) as z:
            if "jarvis.db" not in z.namelist():
                raise ValueError("Archive invalide : base de données absente")
            safety = self.create("avantrestauration")
            self.store.close()
            if self.kb and "knowledge.db" in z.namelist():
                self.kb.close()
                try:
                    for suffix in ("-wal", "-shm"):
                        Path(str(self.kb.path) + suffix).unlink(missing_ok=True)
                    tmpk = self.kb.path.with_suffix(".restore")
                    tmpk.write_bytes(z.read("knowledge.db"))
                    os.replace(tmpk, self.kb.path)
                finally:
                    self.kb.reopen()
            try:
                for suffix in ("-wal", "-shm"):
                    Path(str(self.store.path) + suffix).unlink(missing_ok=True)
                tmp = self.store.path.with_suffix(".restore")
                tmp.write_bytes(z.read("jarvis.db"))
                os.replace(tmp, self.store.path)
                if "keys.json" in z.namelist():
                    (self.home / "keys.json").write_bytes(z.read("keys.json"))
                if "settings.json" in z.namelist():
                    self.settings.data = _merge(DEFAULTS, json.loads(z.read("settings.json").decode("utf-8")))
                    self.settings.save()
            finally:
                self.store.reopen()
                self.keys.reload()
        return {"restored": name, "safety_backup": safety}
