from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import uvicorn
from apps.api.main import create_app

if __name__ == "__main__":
    uvicorn.run(create_app(), host="127.0.0.1", port=8765)
