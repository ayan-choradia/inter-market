"""
Instrument Registry
===================
Single source of truth mapping live-feed instrument IDs to platform contract
codes, built from `data/Instruments.xlsx`.

The workbook lays out six product blocks (SR3/ZQ/SR1 outrights, SR1-ZQ spreads,
SR3/SR1 calendar spreads), each with (Name, Code, ID) columns. Codes in the
workbook use CME month letters (e.g. ZQK26, SR3H26); we normalize them to the
platform's conventions used by `market_state` / `calendar_config`:

    SR3 outright   SR3H26        -> SFRH26          (type "SR3")
    ZQ  outright   ZQK26         -> MAY26           (type "ZQ")
    SR1 outright   SR1K26        -> SR1:MAY26       (type "SR1")
    SR1-ZQ spread  SR1K26-ZQK26  -> SR1_ZQ_MAY26    (type "SR1_ZQ")
    SR3 calendar   SR3H26-M26    -> SFRH26-SFRM26   (type "SR3_CAL")
    SR1 calendar   SR1K26-M26    -> SR1:MAY26-SR1:JUN26 (type "SR1_CAL")

Runtime reads the generated JSON (`instrument_registry.json`) so the feed does
not depend on openpyxl or the xlsx. Regenerate with `build_registry_json(...)`.
"""
from __future__ import annotations

import json
import os
from typing import Optional

HERE = os.path.dirname(__file__)
XLSX_PATH = os.path.join(HERE, "..", "data", "Instruments.xlsx")
REGISTRY_JSON = os.path.join(HERE, "instrument_registry.json")

# CME futures month letter -> month number
CME_MONTH = {
    "F": 1, "G": 2, "H": 3, "J": 4, "K": 5, "M": 6,
    "N": 7, "Q": 8, "U": 9, "V": 10, "X": 11, "Z": 12,
}
MONTH3 = {
    1: "JAN", 2: "FEB", 3: "MAR", 4: "APR", 5: "MAY", 6: "JUN",
    7: "JUL", 8: "AUG", 9: "SEP", 10: "OCT", 11: "NOV", 12: "DEC",
}

# workbook (name_col, code_col, id_col) column groups. The product is NOT implied
# by the column — some columns stack multiple products (e.g. ZQ calendars are
# stacked under SR1 calendars) — so type is inferred from the code prefix.
COLUMN_GROUPS = [
    (0, 1, 2),
    (6, 7, 8),
    (11, 12, 13),
    (17, 18, 19),
    (23, 24, 25),
    (29, 30, 31),
]

OUTRIGHT_TYPES = {"SR3", "ZQ", "SR1"}


def infer_type(code: str) -> str:
    """Infer instrument type from a workbook code prefix."""
    if "-" in code:
        left = code.split("-")[0]
        if left.startswith("SR3"):
            return "SR3_CAL"
        if left.startswith("SR1"):
            return "SR1_ZQ" if "ZQ" in code else "SR1_CAL"
        if left.startswith("ZQ"):
            return "ZQ_CAL"
    else:
        if code.startswith("SR3"):
            return "SR3"
        if code.startswith("SR1"):
            return "SR1"
        if code.startswith("ZQ"):
            return "ZQ"
    raise ValueError(f"cannot infer instrument type for code {code!r}")


# ─── code normalization ───

def _mon(letter: str) -> str:
    return MONTH3[CME_MONTH[letter]]


def norm_zq(code: str) -> str:
    """ZQK26 -> MAY26"""
    return f"{_mon(code[2])}{code[3:]}"


def norm_sr3(code: str) -> str:
    """SR3H26 -> SFRH26 (quarter letter preserved)"""
    return f"SFR{code[3:]}"


def norm_sr1(code: str) -> str:
    """SR1K26 -> SR1:MAY26"""
    return f"SR1:{_mon(code[3])}{code[4:]}"


def norm_sr1zq(code: str) -> str:
    """SR1K26-ZQK26 -> SR1_ZQ_MAY26"""
    left = code.split("-")[0]
    return f"SR1_ZQ_{_mon(left[3])}{left[4:]}"


def norm_sr3_cal(code: str) -> str:
    """SR3H26-M26 -> SFRH26-SFRM26"""
    near, far = code.split("-")
    return f"{norm_sr3(near)}-SFR{far}"


def norm_sr1_cal(code: str) -> str:
    """SR1K26-M26 -> SR1:MAY26-SR1:JUN26"""
    near, far = code.split("-")
    far_letter, far_yy = far[0], far[1:]
    return f"{norm_sr1(near)}-SR1:{_mon(far_letter)}{far_yy}"


def norm_zq_cal(code: str) -> str:
    """ZQK26-M26 -> MAY26-JUN26"""
    near, far = code.split("-")
    return f"{norm_zq(near)}-{_mon(far[0])}{far[1:]}"


_NORMALIZERS = {
    "SR3": norm_sr3,
    "ZQ": norm_zq,
    "SR1": norm_sr1,
    "SR1_ZQ": norm_sr1zq,
    "SR3_CAL": norm_sr3_cal,
    "SR1_CAL": norm_sr1_cal,
    "ZQ_CAL": norm_zq_cal,
}


def normalize(inst_type: str, raw_code: str) -> str:
    return _NORMALIZERS[inst_type](raw_code)


# ─── workbook parsing / registry build ───

def parse_workbook(xlsx_path: str = XLSX_PATH) -> list[dict]:
    """Parse the Instruments workbook into registry records."""
    import openpyxl

    wb = openpyxl.load_workbook(xlsx_path, data_only=True, read_only=True)
    ws = wb["Sheet1"]
    rows = list(ws.iter_rows(values_only=True))
    out: list[dict] = []
    seen_ids: set[str] = set()
    for n_col, c_col, i_col in COLUMN_GROUPS:
        for r in rows[1:]:
            if c_col >= len(r) or i_col >= len(r):
                continue
            raw_code = r[c_col]
            iid = r[i_col]
            if not raw_code or iid is None:
                continue
            iid = str(iid)
            raw_code = str(raw_code)
            inst_type = infer_type(raw_code)
            rec = {
                "name": r[n_col],
                "raw_code": raw_code,
                "code": normalize(inst_type, raw_code),
                "id": iid,
                "type": inst_type,
                "kind": "outright" if inst_type in OUTRIGHT_TYPES else "spread",
            }
            # de-dup on id (source data has a couple repeated ids)
            if iid in seen_ids:
                continue
            seen_ids.add(iid)
            out.append(rec)
    return out


def build_registry_json(xlsx_path: str = XLSX_PATH, out_path: str = REGISTRY_JSON) -> int:
    """(Re)generate the committed registry JSON from the workbook. Returns count."""
    records = parse_workbook(xlsx_path)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2)
    return len(records)


# ─── runtime registry (reads JSON) ───

def load_registry(path: str = REGISTRY_JSON) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


_REGISTRY: Optional[list[dict]] = None
_ID_TO_CODE: dict[str, tuple[str, str]] = {}
_CODE_TO_ID: dict[tuple[str, str], str] = {}


def _ensure_loaded():
    global _REGISTRY, _ID_TO_CODE, _CODE_TO_ID
    if _REGISTRY is not None:
        return
    _REGISTRY = load_registry()
    for rec in _REGISTRY:
        _ID_TO_CODE[rec["id"]] = (rec["type"], rec["code"])
        _CODE_TO_ID[(rec["type"], rec["code"])] = rec["id"]


def all_item_ids() -> list[str]:
    _ensure_loaded()
    return list(_ID_TO_CODE.keys())


def classify_item(item_id: str) -> tuple[Optional[str], Optional[str]]:
    """Feed ID -> (inst_type, platform_code), or (None, None) if unknown."""
    _ensure_loaded()
    t_c = _ID_TO_CODE.get(item_id)
    return t_c if t_c else (None, None)


def id_for(inst_type: str, code: str) -> Optional[str]:
    _ensure_loaded()
    return _CODE_TO_ID.get((inst_type, code))


def ids_by_type(inst_type: str) -> dict[str, str]:
    """{platform_code: id} for one instrument type."""
    _ensure_loaded()
    return {code: iid for iid, (t, code) in _ID_TO_CODE.items() if t == inst_type}


if __name__ == "__main__":
    n = build_registry_json()
    print(f"wrote {n} instruments to {REGISTRY_JSON}")
