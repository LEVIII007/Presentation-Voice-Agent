"""Entrypoint shim — the app now lives in the `app` package.

Run with `python main.py` (same as before). The original single-file demo is
preserved as legacy_main.py (`uvicorn legacy_main:app`).
"""

import os

from app.main import app  # noqa: F401  (uvicorn target: main:app)

if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "7860"))
    uvicorn.run(app, host="0.0.0.0", port=port)
