"""Create fresh private Compose credentials; never print passwords or overwrite files."""

import argparse
import os
from pathlib import Path
import secrets


def create(output):
    output = Path(output).resolve()
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    password = secrets.token_urlsafe(32)
    values = {
        "database-password": password + "\n",
        "api.env": f"PTCG_ONLINE_V2_DATABASE_URL=postgresql://ptcg_v2:{password}@database:5432/ptcg_v2\n",
    }
    for name, value in values.items():
        fd = os.open(output / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(value)
    print(
        "Dedicated credentials created. Keep this directory outside public source and images."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    create(parser.parse_args().output)
