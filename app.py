"""
Standalone Intermarket Curves Service
Run with: uvicorn app:app --host 0.0.0.0 --port 8050 --reload
"""
import os
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from intermarket_curve_engine import router as intermarket_router, get_engine

app = FastAPI(title="Intermarket Curves Engine", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(intermarket_router)

HERE = os.path.dirname(os.path.abspath(__file__))

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
