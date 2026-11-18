"""
Intermarket Curve Engine
========================
High-performance, modular engine for multi-market fixed income curves:
1. Outrights Forward Rate Curve (A, B, C...)
2. 3M Calendar Spread Curve (A-B, B-C...) with Synthetic vs Quoted comparison
3. 3M Butterfly Fly Curve (A-2B+C, B-2C+D...) with Synthetic vs Quoted comparison
4. Cross-Market Basis Overlays (USD vs EUR vs GBP vs CHF)

Designed to be fully self-contained and easily bifurcated into an independent package.
"""

import os
import re
import csv
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

log = logging.getLogger("intermarket_curves")

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(HERE)

# Month mappings
MONTH_NAME_TO_NUM = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}
CME_MONTH_TO_NUM = {
    "F": 1, "G": 2, "H": 3, "J": 4, "K": 5, "M": 6,
    "N": 7, "Q": 8, "U": 9, "V": 10, "X": 11, "Z": 12
}
NUM_TO_MONTH_NAME = {v: k for k, v in MONTH_NAME_TO_NUM.items()}

def parse_float(val: Any) -> Optional[float]:
    if val is None or val == "":
        return None
    try:
        clean = str(val).replace(",", "").strip()
        if not clean:
            return None
        return float(clean)
    except (ValueError, TypeError):
        return None

def calc_vwap(bid: Optional[float], ask: Optional[float], 
              bid_qty: Optional[float], ask_qty: Optional[float], 
              last: Optional[float] = None, settle: Optional[float] = None) -> Optional[float]:
    """Calculate BBO VWAP with fallback to Mid, Last, or Settle (supporting negative spreads/flies)."""
    if bid is not None and ask is not None:
        bq = bid_qty or 0.0
        aq = ask_qty or 0.0
        if (bq + aq) > 0:
            return (bid * aq + ask * bq) / (bq + aq)
        return (bid + ask) / 2.0
    if last is not None:
        return last
    if settle is not None:
        return settle
    if bid is not None:
        return bid
    if ask is not None:
        return ask
    return None

class IntermarketCurveEngine:
    def __init__(self):
        self.markets = {
            "EUR": {"name": "Eurozone Euribor", "code": "I", "currency": "EUR", "symbol": "€", "color": "#38bdf8"},
            "GBP": {"name": "UK SONIA", "code": "SO3", "currency": "GBP", "symbol": "£", "color": "#a855f7"},
            "CHF": {"name": "Swiss SARON", "code": "SA3", "currency": "CHF", "symbol": "CHF", "color": "#f59e0b"},
            "USD": {"name": "US SOFR", "code": "SR3", "currency": "USD", "symbol": "$", "color": "#00d97e"},
        }
        
        # Raw items and indexed mappings
        self.instruments: Dict[str, Dict] = {}        # inst_id -> item data
        self.market_outrights: Dict[str, List[Dict]] = {m: [] for m in self.markets}
        self.market_spreads_3m: Dict[str, List[Dict]] = {m: [] for m in self.markets}
        self.market_flies_3m: Dict[str, List[Dict]] = {m: [] for m in self.markets}
        
        # Live price cache (inst_id -> {price, bid, ask, last, vwap, volume, ...})
        self.live_quotes: Dict[str, Dict] = {}
        
        # Quick lookups
        self.code_to_id: Dict[str, str] = {}
        self.id_to_market: Dict[str, str] = {}
        
        self.load_all_data()

    def parse_contract_expiry(self, contract_name: str) -> Tuple[int, int]:
        """Extract (year, month_num) from contract name like 'I Dec26', 'SO3 Sep27', 'SFRH26'."""
        clean = contract_name.strip()
        parts = clean.split()
        subtag = parts[1] if len(parts) >= 2 else parts[0]
        
        # Check standard format e.g. Dec26, Mar27
        for m_str in ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]:
            if m_str in subtag.upper():
                mo = MONTH_NAME_TO_NUM[m_str]
                yr_match = re.search(r'\d{2}', subtag)
                year = 2000 + int(yr_match.group(0)) if yr_match else 2026
                return year, mo

        # Check CME format e.g. SFRH26, SR3H26, ZQK26
        m_cme = re.search(r'([FGHJKMNQUVXZ])(\d{2})$', subtag.upper())
        if m_cme:
            letter = m_cme.group(1)
            yr = 2000 + int(m_cme.group(2))
            return yr, CME_MONTH_TO_NUM[letter]
                    
        return 2099, 12

    def load_all_data(self):
        """Load and index all CSV files for I, SA3, SO3, and SR3 from registry."""
        file_map = {
            "EUR": {
                "outright": os.path.join(PROJECT_ROOT, "I outreight inst id"),
                "spreads": os.path.join(PROJECT_ROOT, "I (Euribor spreads ) all inst id"),
                "prefix": "I"
            },
            "CHF": {
                "outright": os.path.join(PROJECT_ROOT, "SA3 outirght inst id"),
                "spreads": os.path.join(PROJECT_ROOT, "SA3 spread inst id"),
                "prefix": "SA3"
            },
            "GBP": {
                "outright": os.path.join(PROJECT_ROOT, "So3 outirght inst isd"),
                "spreads": os.path.join(PROJECT_ROOT, "SO3 spread Ins id"),
                "prefix": "SO3"
            }
        }
        
        for mkt, paths in file_map.items():
            self._load_csv_market(mkt, paths["outright"], paths["spreads"], paths["prefix"])

        # Check for real SR3 outright file provided by user
        sr3_outright_candidates = [
            os.path.join(PROJECT_ROOT, "src outirght curve inst is"),
            os.path.join(PROJECT_ROOT, "SR3 outright inst id"),
            os.path.join(PROJECT_ROOT, "SR3 outirght inst id"),
            os.path.join(PROJECT_ROOT, "sr3 outirght curve inst is"),
        ]
        sr3_outright_file = next((p for p in sr3_outright_candidates if os.path.exists(p)), None)
        sr3_spread_file = os.path.join(PROJECT_ROOT, "SR3 spread inst id")

        if sr3_outright_file and os.path.exists(sr3_spread_file):
            log.info(f"Loading real SOFR outrights from: {sr3_outright_file}")
            self._load_csv_market("USD", sr3_outright_file, sr3_spread_file, "SR3")
        else:
            log.warning("Real SR3 outright file not found, falling back to registry mock.")
            self._load_usd_sr3()
            
        log.info(f"Loaded Intermarket Engine: {len(self.instruments)} instruments across 4 markets.")

    def _load_csv_market(self, mkt: str, outright_file: str, spread_file: str, prefix: str):
        if not os.path.exists(outright_file) or not os.path.exists(spread_file):
            log.warning(f"Files missing for {mkt}")
            return

        # 1. Outrights
        outrights = []
        with open(outright_file, "r", encoding="utf-8", errors="ignore") as f:
            reader = csv.DictReader(f)
            for r in reader:
                inst_id = r.get("instrumentId", "").strip()
                contract = r.get("contract", "").strip()
                if not inst_id or not contract:
                    continue
                yr, mo = self.parse_contract_expiry(contract)
                is_quarterly = mo in [3, 6, 9, 12]
                
                bid = parse_float(r.get("bid"))
                ask = parse_float(r.get("ask"))
                bid_qty = parse_float(r.get("bidQty"))
                ask_qty = parse_float(r.get("askQty"))
                last = parse_float(r.get("last"))
                settle = parse_float(r.get("settle"))
                vol = parse_float(r.get("volume")) or 0.0
                
                vwap = calc_vwap(bid, ask, bid_qty, ask_qty, last, settle)
                code = contract.replace(f"{prefix} ", "").strip()
                
                item = {
                    "market": mkt,
                    "type": "outright",
                    "contract": contract,
                    "code": code,
                    "tenor": f"{NUM_TO_MONTH_NAME.get(mo, 'DEC')}{str(yr)[-2:]}",
                    "instrument_id": inst_id,
                    "year": yr,
                    "month": mo,
                    "is_quarterly": is_quarterly,
                    "bid": bid,
                    "ask": ask,
                    "bid_qty": bid_qty,
                    "ask_qty": ask_qty,
                    "last": last,
                    "settle": settle,
                    "volume": vol,
                    "vwap": vwap,
                }
                self.instruments[inst_id] = item
                self.code_to_id[f"{mkt}:{item['code']}"] = inst_id
                self.id_to_market[inst_id] = mkt
                self.live_quotes[inst_id] = item
                outrights.append(item)

        # Sort outrights by expiry
        outrights.sort(key=lambda x: (x["year"], x["month"]))
        self.market_outrights[mkt] = outrights

        # 2. Spreads and Butterflies lookup
        spread_lookup = {}
        with open(spread_file, "r", encoding="utf-8", errors="ignore") as f:
            reader = csv.DictReader(f)
            for r in reader:
                contract = r.get("contract", "").strip()
                inst_id = r.get("instrumentId", "").strip()
                if not contract or not inst_id:
                    continue
                bid = parse_float(r.get("bid"))
                ask = parse_float(r.get("ask"))
                bid_qty = parse_float(r.get("bidQty"))
                ask_qty = parse_float(r.get("askQty"))
                last = parse_float(r.get("last"))
                settle = parse_float(r.get("settle"))
                vol = parse_float(r.get("volume")) or 0.0
                vwap = calc_vwap(bid, ask, bid_qty, ask_qty, last, settle)
                
                info = {
                    "contract": contract,
                    "instrument_id": inst_id,
                    "bid": bid,
                    "ask": ask,
                    "bid_qty": bid_qty,
                    "ask_qty": ask_qty,
                    "last": last,
                    "settle": settle,
                    "volume": vol,
                    "vwap": vwap,
                }
                spread_lookup[contract] = info
                self.instruments[inst_id] = info
                self.id_to_market[inst_id] = mkt
                self.live_quotes[inst_id] = info

        spread_lookup_upper = {k.upper(): v for k, v in spread_lookup.items()}

        # 3. Generate consecutive 3M Calendar Spreads and Butterflies
        quarterlies = [o for o in outrights if o["is_quarterly"]]
        
        spreads_3m = []
        for i in range(len(quarterlies) - 1):
            q1 = quarterlies[i]
            q2 = quarterlies[i + 1]
            c1_code = q1["code"]
            c2_code = q2["code"]
            
            # Find quoted calendar (case-insensitive)
            expected_names = [
                f"{prefix} {c1_code}-{c2_code} Calendar",
                f"{prefix} {c1_code}-{c2_code}",
            ]
            quoted = None
            for ename in expected_names:
                if ename.upper() in spread_lookup_upper:
                    quoted = spread_lookup_upper[ename.upper()]
                    break
            if not quoted:
                # Fuzzy search
                for k, v in spread_lookup.items():
                    if f"{c1_code}-{c2_code}".upper() in k.upper() and "CALENDAR" in k.upper():
                        quoted = v
                        break
                        
            pair = {
                "market": mkt,
                "name": f"{c1_code}-{c2_code}",
                "front": q1["contract"],
                "back": q2["contract"],
                "front_code": c1_code,
                "back_code": c2_code,
                "front_id": q1["instrument_id"],
                "back_id": q2["instrument_id"],
                "quoted_id": quoted["instrument_id"] if quoted else None,
                "quoted": quoted
            }
            spreads_3m.append(pair)
        self.market_spreads_3m[mkt] = spreads_3m

        flies_3m = []
        for i in range(len(quarterlies) - 2):
            q1 = quarterlies[i]
            q2 = quarterlies[i + 1]
            q3 = quarterlies[i + 2]
            c1_code = q1["code"]
            c2_code = q2["code"]
            c3_code = q3["code"]
            
            # Quoted butterfly name e.g. "SR3 Dec26 3mo Butterfly"
            expected_names = [
                f"{prefix} {c1_code} 3mo Butterfly",
                f"{prefix} {c1_code} 3M Butterfly",
                f"{prefix} {c1_code} Butterfly",
            ]
            quoted = None
            for ename in expected_names:
                if ename.upper() in spread_lookup_upper:
                    quoted = spread_lookup_upper[ename.upper()]
                    break
            if not quoted:
                for k, v in spread_lookup.items():
                    if c1_code.upper() in k.upper() and ("3MO BUTTERFLY" in k.upper() or "3M BUTTERFLY" in k.upper()):
                        quoted = v
                        break
                        
            fly = {
                "market": mkt,
                "name": f"{c1_code}-2{c2_code}+{c3_code}",
                "label": f"{c1_code} Fly",
                "wing1": q1["contract"],
                "body": q2["contract"],
                "wing2": q3["contract"],
                "w1_code": c1_code,
                "body_code": c2_code,
                "w2_code": c3_code,
                "w1_id": q1["instrument_id"],
                "body_id": q2["instrument_id"],
                "w2_id": q3["instrument_id"],
                "quoted_id": quoted["instrument_id"] if quoted else None,
                "quoted": quoted
            }
            flies_3m.append(fly)
        self.market_flies_3m[mkt] = flies_3m

    def _load_usd_sr3(self):
        """Load US SR3 from backend/instrument_registry.json."""
        reg_path = os.path.join(HERE, "instrument_registry.json")
        if not os.path.exists(reg_path):
            return
            
        with open(reg_path, "r", encoding="utf-8") as f:
            reg = json.load(f)
            
        sr3_outrights = [r for r in reg if r.get("type") == "SR3"]
        outrights = []
        for r in sr3_outrights:
            code = r["code"]  # e.g. SFRH26
            iid = r["id"]
            yr, mo = self.parse_contract_expiry(code)
            is_quarterly = mo in [3, 6, 9, 12]
            
            tenor_name = f"{NUM_TO_MONTH_NAME.get(mo, 'MAR')}{str(yr)[-2:]}"
            
            # Baseline market price estimation
            yr_offset = yr - 2026
            mo_offset = mo / 12.0
            base_yield = 4.30 - (yr_offset * 0.15) - (mo_offset * 0.05)
            last = round(100.0 - base_yield, 3)
            
            item = {
                "market": "USD",
                "type": "outright",
                "contract": f"SR3 {tenor_name} ({code})",
                "code": tenor_name,
                "raw_code": code,
                "tenor": tenor_name,
                "instrument_id": iid,
                "year": yr,
                "month": mo,
                "is_quarterly": is_quarterly,
                "bid": round(last - 0.005, 3),
                "ask": round(last + 0.005, 3),
                "bid_qty": 2500,
                "ask_qty": 2500,
                "last": last,
                "settle": last,
                "volume": 15000,
                "vwap": last,
            }
            self.instruments[iid] = item
            self.code_to_id[f"USD:{tenor_name}"] = iid
            self.id_to_market[iid] = "USD"
            self.live_quotes[iid] = item
            outrights.append(item)
            
        outrights.sort(key=lambda x: (x["year"], x["month"]))
        # Keep quarterly outrights for the primary 3M curve
        quarterly_sr3 = [o for o in outrights if o["is_quarterly"]]
        self.market_outrights["USD"] = quarterly_sr3
        
        # 3M Spreads & Flies for SR3 from 'SR3 spread inst id' or registry fallback
        sr3_spread_file = os.path.join(PROJECT_ROOT, "SR3 spread inst id")
        spread_lookup = {}
        if os.path.exists(sr3_spread_file):
            with open(sr3_spread_file, "r", encoding="utf-8", errors="ignore") as f:
                reader = csv.DictReader(f)
                for r in reader:
                    contract = r.get("contract", "").strip()
                    inst_id = r.get("instrumentId", "").strip()
                    if not contract or not inst_id:
                        continue
                    bid = parse_float(r.get("bid"))
                    ask = parse_float(r.get("ask"))
                    bid_qty = parse_float(r.get("bidQty"))
                    ask_qty = parse_float(r.get("askQty"))
                    last = parse_float(r.get("last"))
                    settle = parse_float(r.get("settle"))
                    vol = parse_float(r.get("volume")) or 0.0
                    vwap = calc_vwap(bid, ask, bid_qty, ask_qty, last, settle)
                    
                    info = {
                        "contract": contract,
                        "instrument_id": inst_id,
                        "bid": bid,
                        "ask": ask,
                        "bid_qty": bid_qty,
                        "ask_qty": ask_qty,
                        "last": last,
                        "settle": settle,
                        "volume": vol,
                        "vwap": vwap,
                    }
                    spread_lookup[contract] = info
                    self.instruments[inst_id] = info
                    self.id_to_market[inst_id] = "USD"
                    self.live_quotes[inst_id] = info
        else:
            sr3_spreads_all = [r for r in reg if r.get("type") in ["SR3_CAL", "SR3_SPREAD_EXT"]]
            spread_lookup = {r.get("name", "").strip(): r for r in sr3_spreads_all}
        
        # Build case-insensitive lookup
        spread_lookup_upper = {k.upper(): v for k, v in spread_lookup.items()}
        
        spreads_3m = []
        for i in range(len(quarterly_sr3) - 1):
            q1 = quarterly_sr3[i]
            q2 = quarterly_sr3[i + 1]
            c1_code = q1["code"]
            c2_code = q2["code"]
            
            # Look for quoted calendar spread (case-insensitive)
            expected_name = f"SR3 {c1_code}-{c2_code} Calendar".upper()
            quoted = spread_lookup_upper.get(expected_name)
            quoted_id = quoted["instrument_id"] if (quoted and "instrument_id" in quoted) else (quoted.get("id") if quoted else None)
            
            spreads_3m.append({
                "market": "USD",
                "name": f"{c1_code}-{c2_code}",
                "front": q1["contract"],
                "back": q2["contract"],
                "front_code": c1_code,
                "back_code": c2_code,
                "front_id": q1["instrument_id"],
                "back_id": q2["instrument_id"],
                "quoted_id": quoted_id,
                "quoted": quoted
            })
        self.market_spreads_3m["USD"] = spreads_3m
        
        # 3M Butterflies for SR3
        flies_3m = []
        for i in range(len(quarterly_sr3) - 2):
            q1 = quarterly_sr3[i]
            q2 = quarterly_sr3[i + 1]
            q3 = quarterly_sr3[i + 2]
            c1_code = q1["code"]
            c2_code = q2["code"]
            c3_code = q3["code"]
            
            expected_name = f"SR3 {c1_code} 3mo Butterfly".upper()
            quoted = spread_lookup_upper.get(expected_name)
            quoted_id = quoted["instrument_id"] if (quoted and "instrument_id" in quoted) else (quoted.get("id") if quoted else None)
            
            flies_3m.append({
                "market": "USD",
                "name": f"{c1_code}-2{c2_code}+{c3_code}",
                "label": f"{c1_code} Fly",
                "wing1": q1["contract"],
                "body": q2["contract"],
                "wing2": q3["contract"],
                "w1_code": c1_code,
                "body_code": c2_code,
                "w2_code": c3_code,
                "w1_id": q1["instrument_id"],
                "body_id": q2["instrument_id"],
                "w2_id": q3["instrument_id"],
                "quoted_id": quoted_id,
                "quoted": quoted
            })
        self.market_flies_3m["USD"] = flies_3m

    def update_quote(self, inst_id: str, quote_data: Dict[str, Any]):
        """Update live quote from Lightstreamer or API."""
        if inst_id in self.live_quotes:
            self.live_quotes[inst_id].update(quote_data)
        else:
            self.live_quotes[inst_id] = quote_data

    def get_price(self, inst_id: Optional[str]) -> Optional[float]:
        if not inst_id:
            return None
        q = self.live_quotes.get(inst_id)
        if not q:
            return None
        return q.get("vwap") or q.get("last") or q.get("settle")

    def get_market_curve(self, mkt: str) -> Dict[str, Any]:
        """Generate the complete 3-layer curve payload for a specific market."""
        if mkt not in self.markets:
            raise HTTPException(404, f"Market {mkt} not supported")
            
        outrights_raw = self.market_outrights.get(mkt, [])
        spreads_raw = self.market_spreads_3m.get(mkt, [])
        flies_raw = self.market_flies_3m.get(mkt, [])
        
        # 1. Outrights
        outrights_data = []
        for o in outrights_raw:
            q = self.live_quotes.get(o["instrument_id"], o)
            bid = q.get("bid")
            ask = q.get("ask")
            last = q.get("last")
            vwap = q.get("vwap") or calc_vwap(bid, ask, q.get("bid_qty"), q.get("ask_qty"), last, q.get("settle"))
            rate = round(100.0 - vwap, 4) if vwap is not None else None
            
            outrights_data.append({
                "contract": o["contract"],
                "code": o["code"],
                "tenor": o.get("tenor", o["code"]),
                "instrument_id": o["instrument_id"],
                "year": o["year"],
                "month": o["month"],
                "is_quarterly": o["is_quarterly"],
                "bid": bid,
                "ask": ask,
                "last": last,
                "vwap": round(vwap, 4) if vwap is not None else None,
                "rate": rate,
                "volume": q.get("volume", 0)
            })

        # 2. 3M Spreads
        spreads_data = []
        for s in spreads_raw:
            p_front = self.get_price(s["front_id"])
            p_back = self.get_price(s["back_id"])
            
            synth_bps = None
            if p_front is not None and p_back is not None:
                synth_bps = round((p_front - p_back) * 100.0, 2)
                
            is_already_bps = (mkt == "USD")
            q_info = self.live_quotes.get(s["quoted_id"]) if s["quoted_id"] else s.get("quoted")
            quoted_val = None
            quoted_bps = None
            if q_info:
                qv = q_info.get("vwap") if q_info.get("vwap") is not None else (q_info.get("last") if q_info.get("last") is not None else q_info.get("settle"))
                if qv is not None:
                    quoted_val = qv
                    quoted_bps = round(qv, 2) if is_already_bps else round(qv * 100.0, 2)
                    
            basis_bps = None
            if quoted_bps is not None and synth_bps is not None:
                basis_bps = round(quoted_bps - synth_bps, 2)
                
            q_bid = q_info.get("bid") if q_info else None
            q_ask = q_info.get("ask") if q_info else None
            quoted_bid = (round(q_bid, 2) if is_already_bps else round(q_bid * 100.0, 2)) if q_bid is not None else None
            quoted_ask = (round(q_ask, 2) if is_already_bps else round(q_ask * 100.0, 2)) if q_ask is not None else None
                
            spreads_data.append({
                "name": s["name"],
                "front": s["front"],
                "back": s["back"],
                "front_code": s["front_code"],
                "back_code": s["back_code"],
                "synthetic_bps": synth_bps,
                "quoted_bps": quoted_bps,
                "basis_bps": basis_bps,
                "quoted_bid": quoted_bid,
                "quoted_ask": quoted_ask,
                "volume": q_info.get("volume", 0) if q_info else 0,
                "quoted_id": s["quoted_id"]
            })

        # 3. 3M Butterflies (Flies)
        flies_data = []
        for f in flies_raw:
            p_w1 = self.get_price(f["w1_id"])
            p_body = self.get_price(f["body_id"])
            p_w2 = self.get_price(f["w2_id"])
            
            synth_fly_bps = None
            if p_w1 is not None and p_body is not None and p_w2 is not None:
                synth_fly_bps = round((p_w1 - 2.0 * p_body + p_w2) * 100.0, 2)
                
            is_already_bps = (mkt == "USD")
            q_info = self.live_quotes.get(f["quoted_id"]) if f["quoted_id"] else f.get("quoted")
            quoted_fly_bps = None
            if q_info:
                qv = q_info.get("vwap") if q_info.get("vwap") is not None else (q_info.get("last") if q_info.get("last") is not None else q_info.get("settle"))
                if qv is not None:
                    quoted_fly_bps = round(qv, 2) if is_already_bps else round(qv * 100.0, 2)
                    
            basis_bps = None
            if quoted_fly_bps is not None and synth_fly_bps is not None:
                basis_bps = round(quoted_fly_bps - synth_fly_bps, 2)
                
            q_bid = q_info.get("bid") if q_info else None
            q_ask = q_info.get("ask") if q_info else None
            quoted_bid = (round(q_bid, 2) if is_already_bps else round(q_bid * 100.0, 2)) if q_bid is not None else None
            quoted_ask = (round(q_ask, 2) if is_already_bps else round(q_ask * 100.0, 2)) if q_ask is not None else None
                
            flies_data.append({
                "name": f["name"],
                "label": f["label"],
                "wing1": f["wing1"],
                "body": f["body"],
                "wing2": f["wing2"],
                "synthetic_bps": synth_fly_bps,
                "quoted_bps": quoted_fly_bps,
                "basis_bps": basis_bps,
                "quoted_bid": quoted_bid,
                "quoted_ask": quoted_ask,
                "volume": q_info.get("volume", 0) if q_info else 0,
                "quoted_id": f["quoted_id"]
            })

        return {
            "market": mkt,
            "meta": self.markets[mkt],
            "timestamp": datetime.now().isoformat(),
            "outrights": outrights_data,
            "spreads_3m": spreads_data,
            "flies_3m": flies_data
        }

    def get_all_curves_summary(self) -> Dict[str, Any]:
        """Return unified view across all 4 markets including cross-market spreads."""
        data = {}
        for mkt in self.markets:
            data[mkt] = self.get_market_curve(mkt)
            
        common_tenors = [
            "DEC26", "MAR27", "JUN27", "SEP27", "DEC27",
            "MAR28", "JUN28", "SEP28", "DEC28",
            "MAR29", "JUN29", "SEP29", "DEC29",
            "MAR30", "JUN30"
        ]
        
        rates_by_mkt = {}
        for mkt, curve in data.items():
            rates_by_mkt[mkt] = {}
            for o in curve["outrights"]:
                t_code = o.get("tenor", o["code"]).upper()
                for t in common_tenors:
                    if t == t_code or t in t_code:
                        rates_by_mkt[mkt][t] = o["rate"]
                        
        cross_spreads = []
        for t in common_tenors:
            usd = rates_by_mkt.get("USD", {}).get(t)
            eur = rates_by_mkt.get("EUR", {}).get(t)
            gbp = rates_by_mkt.get("GBP", {}).get(t)
            chf = rates_by_mkt.get("CHF", {}).get(t)
            
            cross_spreads.append({
                "tenor": t,
                "USD_rate": usd,
                "EUR_rate": eur,
                "GBP_rate": gbp,
                "CHF_rate": chf,
                "USD_minus_EUR_bps": round((usd - eur) * 100.0, 2) if usd is not None and eur is not None else None,
                "GBP_minus_EUR_bps": round((gbp - eur) * 100.0, 2) if gbp is not None and eur is not None else None,
                "USD_minus_GBP_bps": round((usd - gbp) * 100.0, 2) if usd is not None and gbp is not None else None,
                "EUR_minus_CHF_bps": round((eur - chf) * 100.0, 2) if eur is not None and chf is not None else None,
            })
            
        return {
            "timestamp": datetime.now().isoformat(),
            "markets": data,
            "cross_market_tenors": cross_spreads
        }

    def all_subscribed_ids(self) -> List[str]:
        """Return all instrument IDs for Lightstreamer subscription."""
        return list(self.instruments.keys())

# Singleton instance
_engine: Optional[IntermarketCurveEngine] = None

def get_engine() -> IntermarketCurveEngine:
    global _engine
    if _engine is None:
        _engine = IntermarketCurveEngine()
    return _engine

# ─── FASTAPI ROUTER ───
router = APIRouter(prefix="/api/intermarket", tags=["Intermarket Curves"])

@router.get("/markets")
async def list_markets():
    engine = get_engine()
    return {"status": "ok", "markets": engine.markets}

@router.get("/curve/{market}")
async def get_single_curve(market: str):
    engine = get_engine()
    return engine.get_market_curve(market.upper())

@router.get("/curves/all")
async def get_all_curves():
    engine = get_engine()
    return engine.get_all_curves_summary()

@router.get("/instrument-ids")
async def get_all_instrument_ids():
    engine = get_engine()
    return {
        "status": "ok",
        "count": len(engine.instruments),
        "ids": engine.all_subscribed_ids()
    }

class QuoteUpdateRequest(BaseModel):
    instrument_id: str
    price: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    last: Optional[float] = None
    volume: Optional[float] = None

@router.post("/quote/update")
async def update_live_quote(req: QuoteUpdateRequest):
    engine = get_engine()
    update_data = {}
    if req.price is not None:
        update_data["vwap"] = req.price
    if req.bid is not None:
        update_data["bid"] = req.bid
    if req.ask is not None:
        update_data["ask"] = req.ask
    if req.last is not None:
        update_data["last"] = req.last
    if req.volume is not None:
        update_data["volume"] = req.volume
        
    engine.update_quote(req.instrument_id, update_data)
    return {"status": "success", "instrument_id": req.instrument_id}

class ScenarioShiftRequest(BaseModel):
    market: str
    outright_shift_bps: float = 0.0
    curve_twist_bps: float = 0.0

@router.post("/scenario/shift")
async def apply_scenario_shift(req: ScenarioShiftRequest):
    """Dynamically shift curve to test sensitivity."""
    engine = get_engine()
    mkt = req.market.upper()
    if mkt not in engine.market_outrights:
        raise HTTPException(400, "Unknown market")
        
    outrights = engine.market_outrights[mkt]
    n = len(outrights)
    for i, o in enumerate(outrights):
        iid = o["instrument_id"]
        # linear twist
        twist = req.curve_twist_bps * (i / max(1, n - 1) - 0.5)
        total_shift_pct = (req.outright_shift_bps + twist) / 100.0
        price_shift = -total_shift_pct  # higher yield = lower price
        curr_price = engine.get_price(iid) or o.get("vwap") or 97.0
        engine.update_quote(iid, {"vwap": round(curr_price + price_shift, 4)})
        
    return {"status": "ok", "market": mkt, "shift_bps": req.outright_shift_bps}

# ─── MATHEMATICAL CURVE FITTING & DERIVATIVES ───

FX_RATES = {
    "EURUSD": 1.0850,
    "GBPUSD": 1.2920,
    "USDCHF": 0.8840,
    "USDUSD": 1.0000,
}

def get_dv01_usd(market: str) -> float:
    local_dv01 = 25.0
    m = market.upper()
    if m == "USD":
        return local_dv01
    elif m == "EUR":
        return local_dv01 * FX_RATES["EURUSD"]
    elif m == "GBP":
        return local_dv01 * FX_RATES["GBPUSD"]
    elif m == "CHF":
        return local_dv01 / FX_RATES["USDCHF"]
    return local_dv01

def fit_analytical_curve(x_vals: List[float], y_vals: List[float], model: str = "poly3") -> Dict[str, Any]:
    import numpy as np
    from scipy.optimize import curve_fit
    from scipy.interpolate import CubicSpline

    x = np.array(x_vals, dtype=float)
    y = np.array(y_vals, dtype=float)
    if len(x) < 3:
        return {"error": "Not enough points"}

    x_dense = np.linspace(x.min(), x.max(), 100)
    
    if model.startswith("poly"):
        deg = int(model.replace("poly", "")) if model.replace("poly", "").isdigit() else 3
        deg = max(1, min(deg, len(x) - 1))
        coeffs = np.polyfit(x, y, deg)
        p = np.poly1d(coeffs)
        p_d1 = np.polyder(p, 1)
        p_d2 = np.polyder(p, 2)
        p_d3 = np.polyder(p, 3)
        
        y_pred = p(x)
        ss_res = np.sum((y - y_pred) ** 2)
        ss_tot = np.sum((y - np.mean(y)) ** 2)
        r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 1.0
        
        terms_latex = []
        for i, c in enumerate(coeffs):
            pwr = deg - i
            if pwr == 0:
                terms_latex.append(f"{c:+.4f}")
            elif pwr == 1:
                terms_latex.append(f"{c:+.4f}x")
            else:
                terms_latex.append(f"{c:+.4f}x^{{{pwr}}}")
        eq_latex = "y(x) = " + " ".join(terms_latex).lstrip("+")
        
        # Derivatives latex
        d1_terms = []
        for i, c in enumerate(p_d1.coeffs):
            pwr = len(p_d1.coeffs) - 1 - i
            if pwr == 0:
                d1_terms.append(f"{c:+.4f}")
            elif pwr == 1:
                d1_terms.append(f"{c:+.4f}x")
            else:
                d1_terms.append(f"{c:+.4f}x^{{{pwr}}}")
        d1_latex = "y'(x) = " + " ".join(d1_terms).lstrip("+") if len(d1_terms) > 0 else "0"

        d2_terms = []
        for i, c in enumerate(p_d2.coeffs):
            pwr = len(p_d2.coeffs) - 1 - i
            if pwr == 0:
                d2_terms.append(f"{c:+.4f}")
            elif pwr == 1:
                d2_terms.append(f"{c:+.4f}x")
            else:
                d2_terms.append(f"{c:+.4f}x^{{{pwr}}}")
        d2_latex = "y''(x) = " + " ".join(d2_terms).lstrip("+") if len(d2_terms) > 0 else "0"

        return {
            "model": model,
            "equation": eq_latex,
            "d1_equation": d1_latex,
            "d2_equation": d2_latex,
            "r2": round(float(r2), 4),
            "coeffs": [round(float(c), 6) for c in coeffs],
            "x_dense": [round(float(v), 4) for v in x_dense],
            "y_dense": [round(float(v), 4) for v in p(x_dense)],
            "d1_dense": [round(float(v), 4) for v in p_d1(x_dense)],
            "d2_dense": [round(float(v), 4) for v in p_d2(x_dense)],
            "d3_dense": [round(float(v), 4) for v in p_d3(x_dense)],
        }
    elif model == "nelson_siegel":
        def ns_func(t, b0, b1, b2, lam):
            lam = max(lam, 0.05)
            t_scaled = t / lam
            term = (1.0 - np.exp(-t_scaled)) / t_scaled
            return b0 + b1 * term + b2 * (term - np.exp(-t_scaled))
        try:
            popt, _ = curve_fit(ns_func, x, y, p0=[float(y[-1]), float(y[0]-y[-1]), 0.0, 2.0], maxfev=4000)
            b0, b1, b2, lam = popt
            y_pred = ns_func(x, *popt)
            ss_res = np.sum((y - y_pred) ** 2)
            ss_tot = np.sum((y - np.mean(y)) ** 2)
            r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 1.0
            
            y_dense = ns_func(x_dense, *popt)
            d1_dense = np.gradient(y_dense, x_dense)
            d2_dense = np.gradient(d1_dense, x_dense)
            
            eq_latex = f"y(t) = {b0:.4f} + {b1:.4f}\\left(\\frac{{1-e^{{-t/{lam:.2f}}}}}{{t/{lam:.2f}}}\\right) + {b2:.4f}\\left(\\frac{{1-e^{{-t/{lam:.2f}}}}}{{t/{lam:.2f}}} - e^{{-t/{lam:.2f}}}\\right)"
            return {
                "model": "nelson_siegel",
                "equation": eq_latex,
                "d1_equation": "y'(t) = Instantaneous Forward Slope",
                "d2_equation": "y''(t) = Instantaneous Curvature Rate",
                "r2": round(float(r2), 4),
                "coeffs": [round(float(c), 4) for c in popt],
                "x_dense": [round(float(v), 4) for v in x_dense],
                "y_dense": [round(float(v), 4) for v in y_dense],
                "d1_dense": [round(float(v), 4) for v in d1_dense],
                "d2_dense": [round(float(v), 4) for v in d2_dense],
                "d3_dense": [round(float(v), 4) for v in np.gradient(d2_dense, x_dense)],
            }
        except Exception:
            return fit_analytical_curve(x_vals, y_vals, "poly3")
            
    elif model == "spline":
        cs = CubicSpline(x, y, bc_type='natural')
        cs_d1 = cs.derivative(1)
        cs_d2 = cs.derivative(2)
        cs_d3 = cs.derivative(3)
        return {
            "model": "spline",
            "equation": "Natural Cubic Spline S(x) \\in C^2(\\mathbb{R})",
            "d1_equation": "Continuous 1st Derivative S'(x)",
            "d2_equation": "Continuous 2nd Derivative S''(x)",
            "r2": 1.0,
            "coeffs": [],
            "x_dense": [round(float(v), 4) for v in x_dense],
            "y_dense": [round(float(v), 4) for v in cs(x_dense)],
            "d1_dense": [round(float(v), 4) for v in cs_d1(x_dense)],
            "d2_dense": [round(float(v), 4) for v in cs_d2(x_dense)],
            "d3_dense": [round(float(v), 4) for v in cs_d3(x_dense)],
        }
    return fit_analytical_curve(x_vals, y_vals, "poly3")

class CurveFitRequest(BaseModel):
    market: str
    product_type: str = "outrights"  # outrights, spreads_3m, flies_3m
    model: str = "poly3"             # poly2, poly3, poly4, nelson_siegel, spline
    custom_points: Optional[List[List[float]]] = None

@router.post("/curve/fit")
async def get_curve_fit(req: CurveFitRequest):
    """Fit analytical model, compute 1st and 2nd derivatives, and return exact equations."""
    engine = get_engine()
    mkt = req.market.upper()
    
    if req.custom_points and len(req.custom_points) >= 3:
        x_vals = [p[0] for p in req.custom_points]
        y_vals = [p[1] for p in req.custom_points]
        labels = [f"P{i+1}" for i in range(len(x_vals))]
    else:
        curve = engine.get_market_curve(mkt)
        items = curve.get(req.product_type, [])
        if not items:
            raise HTTPException(400, f"No data for {req.product_type} in {mkt}")
            
        x_vals = []
        y_vals = []
        labels = []
        for i, item in enumerate(items):
            val = None
            if req.product_type == "outrights":
                val = item.get("rate")
                lbl = item.get("code")
            elif req.product_type == "spreads_3m":
                val = item.get("synthetic_bps")
                lbl = item.get("name")
            else:
                val = item.get("synthetic_bps")
                lbl = item.get("name")
                
            if val is not None:
                x_vals.append(float(i + 1))
                y_vals.append(float(val))
                labels.append(lbl)
                
    fit_res = fit_analytical_curve(x_vals, y_vals, req.model)
    fit_res["labels"] = labels
    fit_res["original_x"] = x_vals
    fit_res["original_y"] = y_vals
    fit_res["market"] = mkt
    fit_res["product_type"] = req.product_type
    return fit_res

class CustomCompareRequest(BaseModel):
    markets: List[str]               # e.g. ["EUR", "USD"]
    product_type: str = "outrights"  # outrights, spreads_3m, flies_3m

@router.post("/custom-compare")
async def get_custom_comparison(req: CustomCompareRequest):
    """Dynamically compare arbitrary combinations of markets and product types."""
    engine = get_engine()
    results = {}
    
    for m in req.markets:
        m_up = m.upper()
        if m_up in engine.markets:
            c = engine.get_market_curve(m_up)
            items = c.get(req.product_type, [])
            results[m_up] = {
                "meta": engine.markets[m_up],
                "items": items
            }
            
    return {
        "product_type": req.product_type,
        "markets": results,
        "timestamp": datetime.now().isoformat()
    }

class RatioOptimizeRequest(BaseModel):
    market1: str
    structure1: str       # "outrights", "spreads_3m", "flies_3m"
    index1: int           # 0-based index or contract tag
    market2: str
    structure2: str
    index2: int

@router.post("/ratio/optimize")
async def optimize_ratio(req: RatioOptimizeRequest):
    """
    Calculate intermarket ratio, curvature divergence, and execution ticket.
    E.g. Trading EUR Euribor Fly vs USD SOFR Fly.
    """
    engine = get_engine()
    m1 = req.market1.upper()
    m2 = req.market2.upper()
    
    c1 = engine.get_market_curve(m1)
    c2 = engine.get_market_curve(m2)
    
    items1 = c1.get(req.structure1, [])
    items2 = c2.get(req.structure2, [])
    
    if req.index1 >= len(items1) or req.index2 >= len(items2):
        raise HTTPException(400, "Index out of range")
        
    it1 = items1[req.index1]
    it2 = items2[req.index2]
    
    val1 = it1.get("rate") if req.structure1 == "outrights" else it1.get("synthetic_bps")
    val2 = it2.get("rate") if req.structure2 == "outrights" else it2.get("synthetic_bps")
    name1 = it1.get("contract") or it1.get("name")
    name2 = it2.get("contract") or it2.get("name")
    
    dv01_1 = get_dv01_usd(m1)
    dv01_2 = get_dv01_usd(m2)
    
    # DV01 Neutral Ratio: N1 * dv01_1 = N2 * dv01_2 => N2 / N1 = dv01_1 / dv01_2
    dv01_ratio = dv01_1 / dv01_2
    
    # Extract slopes & curvatures from fitted curves
    fit1 = fit_analytical_curve(
        list(range(1, len(items1)+1)), 
        [it.get("rate") if req.structure1 == "outrights" else it.get("synthetic_bps") or 0.0 for it in items1],
        "poly3"
    )
    fit2 = fit_analytical_curve(
        list(range(1, len(items2)+1)), 
        [it.get("rate") if req.structure2 == "outrights" else it.get("synthetic_bps") or 0.0 for it in items2],
        "poly3"
    )
    
    x1_idx = min(req.index1, len(fit1["d1_dense"]) - 1)
    x2_idx = min(req.index2, len(fit2["d1_dense"]) - 1)
    
    slope1 = fit1["d1_dense"][x1_idx]
    slope2 = fit2["d1_dense"][x2_idx]
    
    curv1 = fit1["d2_dense"][x1_idx]
    curv2 = fit2["d2_dense"][x2_idx]
    
    # Curvature ratio
    curv_ratio = abs(curv1 / curv2) if abs(curv2) > 1e-4 else 1.0
    recommended_qty1 = 100
    recommended_qty2 = int(round(recommended_qty1 * dv01_ratio))
    
    spread = round(val1 - val2, 2) if val1 is not None and val2 is not None else None
    
    return {
        "status": "ok",
        "leg1": {
            "market": m1,
            "structure": req.structure1,
            "name": name1,
            "value": val1,
            "dv01_usd": round(dv01_1, 2),
            "slope": round(float(slope1), 4),
            "curvature": round(float(curv1), 4)
        },
        "leg2": {
            "market": m2,
            "structure": req.structure2,
            "name": name2,
            "value": val2,
            "dv01_usd": round(dv01_2, 2),
            "slope": round(float(slope2), 4),
            "curvature": round(float(curv2), 4)
        },
        "spread_delta": spread,
        "dv01_neutral_ratio": round(dv01_ratio, 4),
        "curvature_ratio": round(curv_ratio, 4),
        "execution_ticket": {
            "leg1_action": "BUY" if spread is not None and spread < 0 else "SELL",
            "leg1_qty": recommended_qty1,
            "leg1_contract": f"{m1} {name1}",
            "leg2_action": "SELL" if spread is not None and spread < 0 else "BUY",
            "leg2_qty": recommended_qty2,
            "leg2_contract": f"{m2} {name2}",
            "net_dv01_usd": round(recommended_qty1 * dv01_1 - recommended_qty2 * dv01_2, 2)
        },
        "fit_leg1": fit1,
        "fit_leg2": fit2
    }

class FourPanelCurvatureRequest(BaseModel):
    market1: str = "USD"
    market2: str = "EUR"
    start_tenor: str = "Dec26"
    mode: str = "diagram"  # "diagram", "live", "custom"
    delta12m_1: Optional[float] = 46.0
    delta12m_2: Optional[float] = 46.0
    flies1: Optional[List[float]] = [13.2, 11.5, 6.0]
    flies2: Optional[List[float]] = [8.0, 9.5, 7.0]

@router.post("/curvature-4panel")
async def get_4panel_curvature_analytics(req: FourPanelCurvatureRequest):
    """
    Computes the exact 4-panel relative curvature analytics:
    Panel 1: Curve sitting vs T0 + Straight line
    Panel 2: 3M flies vs 6M fly / 4 + double flies
    Panel 3: Trade curvature (4 * 3M fly - 6M fly) + gaps
    Panel 4: Curvature bulge (curve - straight line)
    """
    engine = get_engine()
    m1_name = "SR3" if req.market1.upper() == "USD" else req.market1.upper()
    m2_name = "Euribor" if req.market2.upper() == "EUR" else req.market2.upper()
    
    # 5 standard quarterly tenors
    t_start = req.start_tenor.upper()
    # Find sequence of 5 tenors
    all_q = ["SEP26", "DEC26", "MAR27", "JUN27", "SEP27", "DEC27", "MAR28", "JUN28", "SEP28", "DEC28", "MAR29", "JUN29", "SEP29", "DEC29"]
    start_idx = 1  # default DEC26
    for i, q in enumerate(all_q):
        if q in t_start:
            start_idx = i
            break
            
    five_tenors = all_q[start_idx:start_idx+5]
    if len(five_tenors) < 5:
        five_tenors = ["DEC26", "MAR27", "JUN27", "SEP27", "DEC27"]
        
    tenor_labels = [f"{t[:3].title()}{t[3:]}" for t in five_tenors]
    
    if req.mode == "live":
        # Pull live values from engine
        c1 = engine.get_market_curve(req.market1.upper())
        c2 = engine.get_market_curve(req.market2.upper())
        
        def extract_pts(curve):
            outrights = curve["outrights"]
            pts = []
            for t in five_tenors:
                match = next((o for o in outrights if t in o["code"].upper()), None)
                pts.append(match["vwap"] if match else 95.0)
            return pts
            
        p1 = extract_pts(c1)
        p2 = extract_pts(c2)
        
        # 12M spread = (P(T0) - P(T4)) * 100
        d12_1 = round((p1[0] - p1[4]) * 100.0, 2)
        d12_2 = round((p2[0] - p2[4]) * 100.0, 2)
        
        f1 = [
            round((p1[0] - 2.0*p1[1] + p1[2]) * 100.0, 2),
            round((p1[1] - 2.0*p1[2] + p1[3]) * 100.0, 2),
            round((p1[2] - 2.0*p1[3] + p1[4]) * 100.0, 2)
        ]
        f2 = [
            round((p2[0] - 2.0*p2[1] + p2[2]) * 100.0, 2),
            round((p2[1] - 2.0*p2[2] + p2[3]) * 100.0, 2),
            round((p2[2] - 2.0*p2[3] + p2[4]) * 100.0, 2)
        ]
    else:
        # User diagram numbers or custom inputs
        d12_1 = req.delta12m_1 if req.delta12m_1 is not None else 46.0
        d12_2 = req.delta12m_2 if req.delta12m_2 is not None else 46.0
        f1 = req.flies1 if req.flies1 and len(req.flies1) == 3 else [13.2, 11.5, 6.0]
        f2 = req.flies2 if req.flies2 and len(req.flies2) == 3 else [8.0, 9.5, 7.0]

    # Exact mathematical derivations:
    fly6m_1 = round(f1[0] + 2.0 * f1[1] + f1[2], 2)
    fly6m_2 = round(f2[0] + 2.0 * f2[1] + f2[2], 2)
    
    even_1 = round(fly6m_1 / 4.0, 2)
    even_2 = round(fly6m_2 / 4.0, 2)
    
    tilt_1 = round(f1[2] - f1[0], 2)
    tilt_2 = round(f2[2] - f2[0], 2)
    
    double_fly_1 = [round(f1[1] - f1[0], 2), round(f1[2] - f1[1], 2)]
    double_fly_2 = [round(f2[1] - f2[0], 2), round(f2[2] - f2[1], 2)]
    
    # Solve 3M spreads from 12M and flies
    def solve_spreads(delta, fl):
        f0, f1, f2 = fl
        s0 = (delta + 3.0*f0 + 2.0*f1 + f2) / 4.0
        s1 = s0 - f0
        s2 = s1 - f1
        s3 = s2 - f2
        return [round(s0, 2), round(s1, 2), round(s2, 2), round(s3, 2)]
        
    s1 = solve_spreads(d12_1, f1)
    s2 = solve_spreads(d12_2, f2)
    
    curve1 = [0.0, s1[0], round(s1[0]+s1[1], 2), round(s1[0]+s1[1]+s1[2], 2), d12_1]
    curve2 = [0.0, s2[0], round(s2[0]+s2[1], 2), round(s2[0]+s2[1]+s2[2], 2), d12_2]
    
    straight1 = [round(i * (d12_1 / 4.0), 2) for i in range(5)]
    straight2 = [round(i * (d12_2 / 4.0), 2) for i in range(5)]
    straight_avg = [round(i * ((d12_1 + d12_2) / 8.0), 2) for i in range(5)]
    
    # Panel 3: Trade Curvature 4*Fly - 6M Fly
    curv1 = [round(4.0 * f - fly6m_1, 2) for f in f1]
    curv2 = [round(4.0 * f - fly6m_2, 2) for f in f2]
    gaps = [round(curv1[i] - curv2[i], 2) for i in range(3)]
    
    # Identify largest gap for recommended trade
    abs_gaps = [abs(g) for g in gaps]
    max_idx = abs_gaps.index(max(abs_gaps))
    best_tenor = tenor_labels[max_idx + 2]  # e.g. Jun27
    gap_val = gaps[max_idx]
    
    # Panel 4: Bulge above straight line
    bulge1 = [round(curve1[i] - straight1[i], 2) for i in range(5)]
    bulge2 = [round(curve2[i] - straight2[i], 2) for i in range(5)]

    trade_text = f"Trade: {'BUY' if gap_val < 0 else 'SELL'} {m1_name} / {'SELL' if gap_val < 0 else 'BUY'} {m2_name} (4 × {best_tenor} fly – 1 × 6M fly)"

    return {
        "status": "ok",
        "market1": req.market1.upper(),
        "market2": req.market2.upper(),
        "m1_name": m1_name,
        "m2_name": m2_name,
        "tenors": tenor_labels,
        "fly_names": [f"{tenor_labels[0]} 3M fly", f"{tenor_labels[1]} 3M fly", f"{tenor_labels[2]} 3M fly"],
        "spread_names": [
            f"4 × {tenor_labels[0]} fly - 6M fly",
            f"4 × {tenor_labels[1]} fly - 6M fly",
            f"4 × {tenor_labels[2]} fly - 6M fly"
        ],
        "delta12m_1": d12_1,
        "delta12m_2": d12_2,
        "curve1": curve1,
        "curve2": curve2,
        "straight_line": straight_avg,
        "straight_step": round((d12_1 + d12_2) / 8.0, 2),
        "flies1": f1,
        "flies2": f2,
        "fly6m_1": fly6m_1,
        "fly6m_2": fly6m_2,
        "even_1": even_1,
        "even_2": even_2,
        "tilt_1": tilt_1,
        "tilt_2": tilt_2,
        "double_fly_1": double_fly_1,
        "double_fly_2": double_fly_2,
        "curv1": curv1,
        "curv2": curv2,
        "gaps": gaps,
        "bulge1": bulge1,
        "bulge2": bulge2,
        "belly_bulge_1": bulge1[2],
        "belly_bulge_2": bulge2[2],
        "trade_callout": trade_text,
        "sum_check_1": round(curv1[0] + 2.0*curv1[1] + curv1[2], 2),
        "sum_check_2": round(curv2[0] + 2.0*curv2[1] + curv2[2], 2)
    }

