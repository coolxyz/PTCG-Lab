"""Start using the local environment, independent of the caller's directory."""
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
python = ROOT / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
if not python.exists():
    raise SystemExit('Run python scripts/portable/setup.py first.')
import sys
raise SystemExit(subprocess.call([str(python), '-X', 'utf8', str(ROOT / 'scripts/start.py'), *sys.argv[1:]], cwd=ROOT))
