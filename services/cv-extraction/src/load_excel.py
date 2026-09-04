from __future__ import annotations

from pathlib import Path

import pandas as pd

from .exceptions import InputFormatError


def load_excel_rows(path: str) -> list[dict]:
    input_path = Path(path)
    if not input_path.exists():
        raise InputFormatError(f"Input file does not exist: {input_path}")

    suffix = input_path.suffix.lower()
    if suffix not in {".xlsx", ".xls", ".csv"}:
        raise InputFormatError(f"Unsupported input file type: {input_path.suffix}")

    if suffix == ".csv":
        dataframe = pd.read_csv(input_path)
    else:
        dataframe = pd.read_excel(input_path)
    return dataframe.to_dict(orient="records")
