"""
Lightstreamer Live Price Feed Adapter
======================================
Subscribes to ZQ, SR3, and Bond futures prices via Lightstreamer.
Posts price updates to the FastAPI backend on each tick.
"""

import os
import sys
import time
import threading
import requests

# Global session for connection pooling
_session = requests.Session()
_adapter = requests.adapters.HTTPAdapter(pool_connections=200, pool_maxsize=200)
_session.mount('http://', _adapter)
_session.mount('https://', _adapter)

import logging
from typing import Dict, Optional
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("lightstreamer_adapter")

# Credentials loaded securely from environment (.env)
LS_TOKEN = os.getenv("LS_TOKEN", "")
LS_USER = os.getenv("LS_USER", "")

# ─── ITEM ID MAPPINGS ───
# Futures and spreads are sourced from the committed instrument registry
# (instruments.py / instrument_registry.json), generated from
# data/Instruments.xlsx. Bonds are not in that workbook, so they stay local here.
import instruments

BOND_FUTURES_ITEM_IDS: Dict[str, str] = {
    "ZT": "13063716953335510845",
    "ZN": "12780412946037980746",
}
BOND_REVERSE = {v: k for k, v in BOND_FUTURES_ITEM_IDS.items()}

FIELD_NAMES = [
    "Last", "Settle", "Price", "Volume",
    "BestBid", "BestAsk", "BestBidQty", "BestAskQty"
]

def extract_price(update_data: dict) -> Optional[float]:
    def _scale(val_str):
        try:
            v = float(val_str)
            return v / 100.0 if v > 1000.0 else v
        except (ValueError, TypeError):
            return None

    # 1. BBO VWAP
    bid = _scale(update_data.get("BestBid"))
    ask = _scale(update_data.get("BestAsk"))

    if bid is not None and ask is not None and bid > 0 and ask > 0:
        try:
            bid_qty = float(update_data.get("BestBidQty") or 0)
            ask_qty = float(update_data.get("BestAskQty") or 0)
            if (bid_qty + ask_qty) > 0:
                return (bid * ask_qty + ask * bid_qty) / (bid_qty + ask_qty)
        except (ValueError, TypeError):
            pass
        return (bid + ask) / 2.0

    # 2. Fallback for illiquid contracts
    for f in ["Last", "Settle", "Price"]:
        v = _scale(update_data.get(f))
        if v is not None:
            return v

    return None

def classify_item(item_id: str):
    inst_type, code = instruments.classify_item(item_id)
    if inst_type:
        return inst_type, code
    if item_id in BOND_REVERSE:
        return "BOND", BOND_REVERSE[item_id]
    return None, None

from lightstreamer.client import ClientListener, SubscriptionListener
import lightstreamer.client.ls_python_client_haxe as H

# Monkeypatch Lightstreamer Python SDK to allow purely numeric item IDs (which are otherwise rejected)
H.com_lightstreamer_internal__Types_Items_Impl_.allValidNames = lambda x: True

class BaseClientListener(ClientListener):
    def onListenStart(self, client):
        log.info("Connection attempt started.")
    def onListenEnd(self, client):
        log.info("Connection attempt ended.")
    def onPropertyChange(self, property):
        log.info(f"Property changed: {property}")
    def onServerError(self, errorCode, errorMessage):
        log.error(f"Server error: {errorCode} : {errorMessage}")
    def onStatusChange(self, status):
        log.info(f"Connection status: {status}")

class PriceFeedListener(SubscriptionListener):
    def __init__(self, api_url, on_price_callback=None, on_intermarket_callback=None):
        self.api_url = api_url
        self.on_price_callback = on_price_callback
        self.on_intermarket_callback = on_intermarket_callback
        self.price_cache: Dict[str, float] = {}
        self.update_count = 0

    def onSubscription(self):
        log.info("Subscription acknowledged by server.")

    def onSubscriptionError(self, code, message):
        log.error(f"Subscription Error {code}: {message}")

    def onItemUpdate(self, update):
        item_id = update.getItemName().replace("TT-", "")
        inst_type, code = classify_item(item_id)

        data = {}
        for field in FIELD_NAMES:
            try:
                val = update.getValue(field)
                data[field] = val if val else ""
            except Exception:
                data[field] = ""

        price = extract_price(data)

        # ── 1. Intermarket Curve Engine Update (Outrights, Spreads, Flies) ──
        try:
            from intermarket_curve_engine import get_engine as get_im_engine
            im_engine = get_im_engine()
            
            target_id = None
            if item_id in im_engine.instruments:
                target_id = item_id
            elif code:
                for pfx in ["USD", "EUR", "GBP", "CHF"]:
                    ck = f"{pfx}:{code}"
                    if ck in im_engine.code_to_id:
                        target_id = im_engine.code_to_id[ck]
                        break

            if target_id and target_id in im_engine.instruments:
                bid_v = parse_float(data.get("BestBid"))
                ask_v = parse_float(data.get("BestAsk"))
                last_v = parse_float(data.get("Last"))
                settle_v = parse_float(data.get("Settle"))
                vol_v = parse_float(data.get("Volume"))
                
                if bid_v and bid_v > 1000.0: bid_v /= 100.0
                if ask_v and ask_v > 1000.0: ask_v /= 100.0
                if last_v and last_v > 1000.0: last_v /= 100.0
                if settle_v and settle_v > 1000.0: settle_v /= 100.0
                
                vwap = price or calc_vwap(bid_v, ask_v, None, None, last_v, settle_v)
                
                im_payload = {
                    "price": vwap,
                    "vwap": vwap,
                    "bid": bid_v,
                    "ask": ask_v,
                    "last": last_v,
                    "settle": settle_v,
                    "volume": vol_v
                }
                im_engine.update_quote(target_id, im_payload)
                
                inst_info = im_engine.instruments[target_id]
                mkt = im_engine.id_to_market.get(target_id, "USD")
                
                if hasattr(self, 'on_intermarket_callback') and self.on_intermarket_callback:
                    try:
                        self.on_intermarket_callback({
                            "type": "intermarket_tick",
                            "instrument_id": target_id,
                            "market": mkt,
                            "contract": inst_info.get("contract"),
                            "code": inst_info.get("code"),
                            "tenor": inst_info.get("tenor", inst_info.get("code")),
                            "item_type": inst_info.get("type", "outright"),
                            "price": vwap,
                            "rate": round(100.0 - vwap, 4) if vwap is not None else None,
                            "bid": bid_v,
                            "ask": ask_v,
                            "last": last_v,
                            "volume": vol_v,
                            "timestamp": datetime.now().isoformat()
                        })
                    except Exception:
                        pass
        except Exception:
            pass

        # ── 2. Legacy Price Feed for ZQ / SR3 / Bond ──
        if price is None or not inst_type:
            return

        cache_key = f"{inst_type}:{code}"
        
        def _scale(val_str):
            try:
                v = float(val_str)
                return v / 100.0 if v > 1000.0 else v
            except (ValueError, TypeError):
                return None

        bid = _scale(data.get("bidPrice"))
        ask = _scale(data.get("askPrice"))
        try:
            vol = float(data.get("totalTradedQuantity") or 0)
        except (ValueError, TypeError):
            vol = 0.0

        self.price_cache[cache_key] = price
        self.update_count += 1
        if self.update_count % 10 == 1:
            log.info(f"[{inst_type}] {code} = {price:.4f} (B:{bid} A:{ask} V:{vol})")
            
        rich_payload = dict(data)
        rich_payload["price"] = price

        if hasattr(self, 'on_price_callback') and self.on_price_callback:
            try:
                self.on_price_callback(inst_type, {code: price}, {code: rich_payload})
            except Exception as e:
                log.warning(f"Callback error for {code}: {e}")
        else:
            def _send():
                try:
                    _session.post(
                        f"{self.api_url}/api/prices/update",
                        json={
                            "instrument_type": inst_type,
                            "prices": {code: price},
                            "rich_data": {
                                code: rich_payload
                            }
                        },
                        timeout=2,
                    )
                except requests.exceptions.RequestException as e:
                    log.warning(f"Failed to post {code}: {e}")
            threading.Thread(target=_send, daemon=True).start()


def start_lightstreamer_feed(
    server_url: str = "https://ls-md.corp.hertshtengroup.com/",
    adapter_set: str = "TTsdkLSAdapter",
    data_adapter: str = "HGL1_Adapter",
    api_url: Optional[str] = None,
    on_price_callback=None,
    on_intermarket_callback=None,
):
    from lightstreamer.client import LightstreamerClient, Subscription

    server_url = server_url or os.getenv("LIGHTSTREAMER_URL", "https://ls-md.corp.hertshtengroup.com/")
    adapter_set = adapter_set or os.getenv("LIGHTSTREAMER_ADAPTER", "TTsdkLSAdapter")
    data_adapter = data_adapter or os.getenv("LIGHTSTREAMER_DATA_ADAPTER", "HGL1_Adapter")
    api_url = api_url or os.getenv("API_URL", "http://localhost:8050")
    ls_user = os.getenv("LS_USER") or LS_USER
    ls_token = os.getenv("LS_TOKEN") or LS_TOKEN

    if not ls_token:
        log.warning("[LIGHTSTREAMER] No LS_TOKEN found in environment. Please set LS_TOKEN in your .env file.")

    log.info(f"Connecting to {server_url} (adapter: {adapter_set}) as user: {ls_user}")

    client = LightstreamerClient(server_url, adapter_set)
    client.connectionOptions.setFirstRetryMaxDelay(5000)
    client.connectionOptions.setRetryDelay(5000)
    client.connectionDetails.setUser(ls_user)
    client.connectionDetails.setPassword(ls_token)
    client.addListener(BaseClientListener())

    client.connect()
    
    # Wait for connection to establish before subscribing
    time.sleep(2)

    listener = PriceFeedListener(api_url, on_price_callback=on_price_callback, on_intermarket_callback=on_intermarket_callback)

    registry_ids = instruments.all_item_ids()
    try:
        from intermarket_curve_engine import get_engine as get_im_engine
        im_ids = get_im_engine().all_subscribed_ids()
    except Exception:
        im_ids = []
    
    combined_ids = list(set(registry_ids + list(BOND_FUTURES_ITEM_IDS.values()) + im_ids))
    all_item_ids = [f"TT-{x}" for x in combined_ids]

    subscriptions = []
    chunk_size = 200
    for i in range(0, len(all_item_ids), chunk_size):
        chunk = all_item_ids[i:i + chunk_size]
        try:
            sub = Subscription(
                "MERGE",
                chunk,
                FIELD_NAMES,
            )
            sub.setDataAdapter(data_adapter)
            sub.setRequestedMaxFrequency("1")
            sub.setRequestedSnapshot("yes")
            sub.addListener(listener)
            client.subscribe(sub)
            subscriptions.append(sub)
        except Exception as e:
            log.error(f"Failed to subscribe to chunk {chunk}: {e}")

    log.info(f"Subscribed to {len(all_item_ids)} instruments across {len(subscriptions)} successful subscriptions (registry={len(registry_ids)}, Bond={len(BOND_FUTURES_ITEM_IDS)})")

    return client, subscriptions


def main():
    client = None
    subscriptions = []
    try:
        from dotenv import load_dotenv
        load_dotenv()
        client, subscriptions = start_lightstreamer_feed()
        log.info("Lightstreamer feed loop running. Press CTRL+C to exit.")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Exiting Lightstreamer feed...")
    finally:
        if client and subscriptions:
            try:
                for sub in subscriptions:
                    client.unsubscribe(sub)
                client.disconnect()
            except Exception:
                pass

if __name__ == "__main__":
    main()
