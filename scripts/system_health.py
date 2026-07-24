"""Print the unified non-secret production health report."""

from pathlib import Path
import json
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.system_health import collect_system_health


if __name__ == "__main__":
    print(json.dumps(collect_system_health(), indent=2, default=str))
