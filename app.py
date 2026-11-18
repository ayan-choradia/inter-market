"""
Standalone Intermarket Curves Service
Run with: uvicorn app:app --host 0.0.0.0 --port 8050 --reload
"""
import os
import json
import asyncio
from typing import List
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import websockets
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
async def add_no_cache_headers(request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


app.include_router(intermarket_router)

HERE = os.path.dirname(os.path.abspath(__file__))

connected_ws: List[WebSocket] = []

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
    """Relay live Lightstreamer ticks from backend port 8000 to port 8050 clients."""
    while True:
        try:
            async with websockets.connect("ws://localhost:8000/ws") as b_ws:
                while True:
                    msg = await b_ws.recv()
                    data = json.loads(msg)
                    for c in list(connected_ws):
                        try:
                            await c.send_json(data)
                        except Exception:
                            pass
        except Exception:
            await asyncio.sleep(2)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(forward_backend_ws())

@app.get("/")
async def index():
    return FileResponse(os.path.join(HERE, "intermarket_curves_dashboard.html"))

@app.get("/intermarket_curves_dashboard.html")
async def dashboard():
    return FileResponse(os.path.join(HERE, "intermarket_curves_dashboard.html"))

@app.get("/Intermarket_Curve_Intelligence_Mathematical_Report.pdf")
async def pdf_report():
    return FileResponse(os.path.join(HERE, "Intermarket_Curve_Intelligence_Mathematical_Report.pdf"), media_type="application/pdf")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8050, reload=True)
