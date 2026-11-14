"""Prépare une version à publier : écrit manifest.json (version, notes, empreinte de chaque fichier).

    python tools/publish.py "Notes de version"

Ensuite : commit, tag v<version>, push du tag et de main. Jarvis lit manifest.json sur main,
puis télécharge les fichiers modifiés depuis le tag.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jarvis_core import __version__  # noqa: E402
from jarvis_core.updater import build_manifest  # noqa: E402

notes = " ".join(sys.argv[1:]).strip()
m = build_manifest(ROOT, __version__, notes)
m["ref"] = f"v{__version__}"
(ROOT / "manifest.json").write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"manifest.json : version {__version__}, {len(m['files'])} fichiers")
