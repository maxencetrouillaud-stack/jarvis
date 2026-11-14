"""Point d'entrée : python run.py  [--port 8765] [--no-window]"""
import sys

if sys.version_info < (3, 9):
    sys.exit("Jarvis demande Python 3.9 ou plus récent.")

from jarvis_core.server import main

sys.exit(main())
