"""Convertit les CSV de data/raw en Parquet dans data/parquet."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from assistant.db import prepare_parquet  # noqa: E402

if __name__ == "__main__":
    prepare_parquet(force=True)
    print("Parquet prêt dans data/parquet/")
