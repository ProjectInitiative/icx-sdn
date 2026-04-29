import os
from pathlib import Path


def _project_root():
    env = os.environ.get("ICX_MONITOR_ROOT")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent.parent
