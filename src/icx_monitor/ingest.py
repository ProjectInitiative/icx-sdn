import subprocess
import sys
from . import _project_root

PROJECT_ROOT = _project_root()


def ingest():
    print("=== Step 1: Grab data from switch ===")
    result = subprocess.run(
        [sys.executable, "-m", "icx_monitor.grab_info"],
        cwd=PROJECT_ROOT,
    )
    if result.returncode != 0:
        print("Failed to grab data from switch")
        return False

    print("\n=== Step 2: Parse log data ===")
    result = subprocess.run(
        [sys.executable, "-m", "icx_monitor.parser"],
        cwd=PROJECT_ROOT,
    )
    if result.returncode != 0:
        print("Failed to parse data")
        return False

    print("\n=== Done ===")
    return True


def main():
    success = ingest()
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()
