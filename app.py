"""
Standalone Intermarket Curves Service
Run with: uvicorn app:app --host 0.0.0.0 --port 8050 --reload
"""
import os
import json
import asyncio
from typing import List
from dotenv import load_dotenv

# Load local environment variables (.env)
load_dotenv()
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.middleware.cors import CORSMiddleware
import websockets
import httpx
from intermarket_curve_engine import router as intermarket_router, get_engine

app = FastAPI(title="Intermarket Curves Engine", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def add_no_cache_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

# Reverse proxy to port 8000 when available to ensure 100% live tick sync without demo/CSV fallback discrepancy
@app.get("/api/intermarket/{subpath:path}")
async def proxy_or_serve_intermarket(subpath: str, request: Request):
    backend_url = f"http://localhost:8000/api/intermarket/{subpath}"
    if request.query_params:
        backend_url += f"?{request.query_params}"
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            resp = await client.get(backend_url)
            if resp.status_code == 200:
                return Response(
                    content=resp.content,
                    status_code=resp.status_code,
                    media_type="application/json",
                    headers={
                        "Cache-Control": "no-cache, no-store, must-revalidate, max-age=0",
                        "Pragma": "no-cache",
                    }
                )
    except Exception:
        pass

    # Fallback to local engine if port 8000 is not reachable
    engine = get_engine()
    if subpath == "curves/all":
        return engine.get_all_curves_summary()
    elif subpath.startswith("curve/"):
        mkt = subpath.split("/")[1]
        return engine.get_market_curve(mkt)
    elif subpath == "curvature-4panel":
        return engine.get_curvature_4panel()
    raise HTTPException(status_code=404, detail="Endpoint not found")

app.include_router(intermarket_router)

HERE = os.path.dirname(os.path.abspath(__file__))

connected_ws: List[WebSocket] = []

CME_MAP = {
    'F': 'JAN', 'G': 'FEB', 'H': 'MAR', 'J': 'APR',
    'K': 'MAY', 'M': 'JUN', 'N': 'JUL', 'Q': 'AUG',
    'U': 'SEP', 'V': 'OCT', 'X': 'NOV', 'Z': 'DEC'
}

def _cme_to_tenor(c: str) -> str:
    import re
    m = re.match(r'^(?:SFR|SR3|GE|SO3|SA3|I)?([FGHJKMNQUVXZ])(\d{2})$', c.strip().upper())
    return f"{CME_MAP.get(m.group(1), '')}{m.group(2)}" if m else c

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    connected_ws.append(ws)
    try:
        engine = get_engine()
        await ws.send_json({
            "type": "intermarket_full",
            "data": engine.get_all_curves_summary()
        })
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        if ws in connected_ws:
            connected_ws.remove(ws)

async def forward_backend_ws():
    """Relay live Lightstreamer ticks from backend port 8000 to port 8050 clients and sync local engine."""
    while True:
        try:
            # Sync initial full snapshot from port 8000 into local engine
            try:
                async with httpx.AsyncClient(timeout=2.0) as client:
                    r = await client.get("http://localhost:8000/api/intermarket/curves/all")
                    if r.status_code == 200:
                        sdata = r.json()
                        engine = get_engine()
                        for mkt, cdata in sdata.get("markets", {}).items():
                            for o in cdata.get("outrights", []):
                                iid = o.get("instrument_id")
                                if iid:
                                    engine.update_quote(iid, o)
            except Exception:
                pass

            async with websockets.connect("ws://localhost:8000/ws") as b_ws:
                while True:
                    msg = await b_ws.recv()
                    data = json.loads(msg)

                    # Keep local engine updated in real time
                    try:
                        engine = get_engine()
                        msg_type = data.get("type")
                        if msg_type == "intermarket_tick":
                            iid = data.get("instrument_id")
                            if iid and iid in engine.instruments:
                                engine.update_quote(iid, data)
                            else:
                                m = data.get("market", "USD")
                                tenor = data.get("tenor") or data.get("code")
                                if tenor:
                                    tk = f"{m}:{tenor}".upper()
                                    for k, cid in engine.code_to_id.items():
                                        if k.upper() == tk:
                                            engine.update_quote(cid, data)
                        elif msg_type == "price_tick":
                            if data.get("instrument_type") in ("SR3", "SR3_SPREAD_EXT", "SR3_CAL"):
                                prices = data.get("prices", {})
                                for c_code, p_val in prices.items():
                                    tenor_norm = _cme_to_tenor(c_code)
                                    target_key = f"USD:{tenor_norm}".upper()
                                    for k, iid in engine.code_to_id.items():
                                        if k.upper() == target_key:
                                            engine.update_quote(iid, {
                                                "price": p_val,
                                                "vwap": p_val,
                                                "rate": round(100.0 - p_val, 4)
                                            })
                    except Exception:
                        pass

                    # Broadcast to connected clients
                    for c in list(connected_ws):
                        try:
                            await c.send_json(data)
                        except Exception:
                            pass
        except Exception:
            await asyncio.sleep(2)

@app.on_event("startup")
async def startup_event():
    app.state.loop = asyncio.get_running_loop()

    # 1. Native Lightstreamer support if enabled in .env
    if os.getenv("ENABLE_LIGHTSTREAMER", "0") == "1":
        import threading
        def _run_native_ls():
            try:
                from lightstreamer_adapter import start_lightstreamer_feed
                print("[STANDALONE] Starting native Lightstreamer live streaming feed...", flush=True)

                def on_im_tick(im_tick):
                    loop = getattr(app.state, 'loop', None)
                    if loop and not loop.is_closed():
                        for client in list(connected_ws):
                            try:
                                asyncio.run_coroutine_threadsafe(client.send_json(im_tick), loop)
                            except Exception:
                                pass

                def on_price_tick(inst_type, prices, rich_data):
                    tick_msg = {
                        "type": "price_tick",
                        "instrument_type": inst_type,
                        "prices": prices,
                        "rich_data": rich_data
                    }
                    loop = getattr(app.state, 'loop', None)
                    if loop and not loop.is_closed():
                        for client in list(connected_ws):
                            try:
                                asyncio.run_coroutine_threadsafe(client.send_json(tick_msg), loop)
                            except Exception:
                                pass

                start_lightstreamer_feed(
                    server_url=os.getenv("LIGHTSTREAMER_URL", "https://ls-md.corp.hertshtengroup.com/"),
                    adapter_set=os.getenv("LIGHTSTREAMER_ADAPTER", "TTsdkLSAdapter"),
                    data_adapter=os.getenv("LIGHTSTREAMER_DATA_ADAPTER", "HGL1_Adapter"),
                    api_url=f"http://localhost:{os.getenv('PORT', '8050')}",
                    on_price_callback=on_price_tick,
                    on_intermarket_callback=on_im_tick
                )
            except Exception as e:
                print(f"[STANDALONE] Native Lightstreamer startup failed: {e}", flush=True)

        threading.Thread(target=_run_native_ls, daemon=True).start()

    # 2. Also relay live ticks from port 8000 if backend is running locally
    asyncio.create_task(forward_backend_ws())

@app.get("/")
async def index():
    return FileResponse(os.path.join(HERE, "intermarket_curves_dashboard.html"))

@app.get("/intermarket_curves_dashboard.html")
async def dashboard():
    return FileResponse(os.path.join(HERE, "intermarket_curves_dashboard.html"))

@app.get("/stir_research")
@app.get("/stir_global_research_dashboard.html")
async def stir_research():
    return FileResponse(os.path.join(HERE, "stir_global_research_dashboard.html"))

@app.get("/Intermarket_Curve_Intelligence_Mathematical_Report.pdf")
async def pdf_report():
    return FileResponse(os.path.join(HERE, "Intermarket_Curve_Intelligence_Mathematical_Report.pdf"), media_type="application/pdf")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8050, reload=True)
