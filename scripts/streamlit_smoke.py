"""Headless Streamlit startup smoke used by release validation."""

import os
from pathlib import Path
import sys

from streamlit.testing.v1 import AppTest


def main():
    project = Path(__file__).resolve().parents[1]
    os.chdir(project)
    if str(project) not in sys.path:
        sys.path.insert(0, str(project))
    app = AppTest.from_file(str(project / "app.py"), default_timeout=30)
    app.run()
    if app.exception:
        messages = "; ".join(str(item.value) for item in app.exception)
        raise RuntimeError(f"Streamlit smoke failed: {messages}")
    print("STREAMLIT_SMOKE_OK")


if __name__ == "__main__":
    main()
