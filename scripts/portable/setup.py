"""Install platform-local dependencies without changing data or engine sources."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parents[2]

def main():
    if sys.version_info < (3, 14):
        raise SystemExit('Use Python 3.14 or newer; validated with 3.14.6.')
    if not shutil.which('git'):
        raise SystemExit('Install Git first (required for upstream card images and updates).')
    npm = shutil.which('npm.cmd' if os.name == 'nt' else 'npm')
    if not npm:
        raise SystemExit('Install Node.js and npm first (validated with Node 26.4.0).')
    target = ROOT / '.venv'
    python = target / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.exists():
        venv.EnvBuilder(with_pip=True).create(target)
    subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(ROOT / 'apps/api/requirements.lock')], check=True, cwd=ROOT)
    subprocess.run([npm, 'ci'], check=True, cwd=ROOT / 'apps/web')
    subprocess.run([npm, 'run', 'build'], check=True, cwd=ROOT / 'apps/web')
    subprocess.run([str(python), '-X', 'utf8', '-c', 'from packages.battle.service import runtime; print("Engine:", runtime().ENGINE_VERSION)'], check=True, cwd=ROOT)
    print('Ready. Run: python scripts/start.py')

if __name__ == '__main__':
    main()
