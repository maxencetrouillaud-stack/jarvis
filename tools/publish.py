"""Prépare une version à publier : écrit manifest.json (version, notes, empreinte de chaque fichier).

    1. commit des fichiers de la version
    2. python tools/publish.py --ref <sha de ce commit> "Notes de version"
    3. commit de manifest.json, push de main

Jarvis lit manifest.json sur main, puis télécharge les fichiers modifiés depuis le commit indiqué par « ref » :
ce commit ne bouge plus, donc les fichiers téléchargés correspondent toujours aux empreintes du manifeste.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jarvis_core import __version__  # noqa: E402
from jarvis_core.updater import build_manifest  # noqa: E402

args = sys.argv[1:]
ref = f"v{__version__}"
if args[:1] == ["--ref"]:
    ref, args = args[1], args[2:]
m = build_manifest(ROOT, __version__, " ".join(args).strip())
m["ref"] = ref
(ROOT / "manifest.json").write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"manifest.json : version {__version__}, {len(m['files'])} fichiers")
