# Intermarket Fixed Income Curve Intelligence Platform

A high-performance quantitative analytics platform and interactive terminal for cross-economy fixed income STIR curves (Short-Term Interest Rate futures):
- **USD SOFR** (`SR3` on CME)
- **Eurozone Euribor** (`I` on ICE)
- **UK SONIA** (`SO3` on ICE)
- **Swiss SARON** (`SA3` on Eurex)

---

## Key Features

1. **Live Real-Time Streaming via Lightstreamer**:
   - Native integration with Lightstreamer client (`lightstreamer-client-lib`).
   - Real-time tick-by-tick outright prices, spreads, and butterfly updates streamed over WebSockets.
   - Live Outright tape and streaming BBO depth for SOFR, Euribor, SONIA, and SARON.

2. **Three-Layer Multi-Market Geometry**:
   - **Level 1 (Outrights)**: Implied forward yield curves ($100 - \text{VWAP}$).
   - **Level 2 (3M Calendar Spreads)**: Sequential quarter-to-quarter slopes ($S_k = r_{k+1} - r_k$).
   - **Level 3 (3M Butterflies)**: Discrete second differences / curvature ($F_k = S_k - S_{k+1}$).

3. **Arbitrary Curve Combinations & Structure Tweaker**:
   - **Generalized Combination Mode ($a - x \cdot b$)**: Compare any two contracts, spreads, or butterflies across any quarter ($Z26 \to Z30$) with custom or auto-calculated multiplier $x$.
   - **Higher-Order Multi-Fly Curvature**:
     - `Dec26 12M Fly - 2.0 × Dec26 6M Fly`
     - `Dec26 6M Fly - 2.0 × Dec26 3M Fly`
   - **Total Spread Strip Neutralizer ($\sum \text{Spreads} - y \cdot \text{Leg}$)**:
     - `Total 3M Spreads (1Y Strip) - 4.0 × (Dec26 3M Spread)`
     - `Total 6M Spreads - 2.0 × (Dec26 6M Fly)`
   - **Live Dynamic Curve Plotter**: Continuous cubic polynomial fit ($y(x) = c_3 x^3 + c_2 x^2 + c_1 x + c_0$), 1st derivative slope ($dy/dx$), 2nd derivative curvature ($d^2y/dx^2$), and $R^2$ fit quality.

4. **Institutional Curve Board**:
   - Matrix format covering:
     - `01 · December spreads` ($Z26\text{-}Z27, Z27\text{-}Z28, Z28\text{-}Z29, Z29\text{-}Z30$ across SRA, SON, ER, CRA).
     - `02 · 3-month flies` ($Z26 \to Z28$ quarterly legs).
     - `03 · 6-month flies` ($Z26 \to Z28$ half-yearly legs).

5. **Instant Publication-Quality PDF Report**:
   - Built-in ReportLab and Matplotlib PDF compilation engine serving institutional reports at `/Intermarket_Curve_Intelligence_Mathematical_Report.pdf`.

---

## Quick Start & Lightstreamer Setup

### 1. Installation
Clone the repository and install dependencies:
```bash
git clone https://github.com/ayan-choradia/inter-market.git
cd inter-market
pip install -r requirements.txt
```

### 2. Configure Environment & Lightstreamer Credentials
Copy the example environment template:
```bash
# On Windows (cmd):
copy .env.example .env

# On Linux / macOS:
cp .env.example .env
```

Open `.env` in any text editor and configure your credentials:
```bash
# Enable Lightstreamer Live Market Data Streaming (1 = ON, 0 = OFF)
ENABLE_LIGHTSTREAMER=1

# Lightstreamer Server & Adapter
LIGHTSTREAMER_URL=https://ls-md.corp.hertshtengroup.com/
LIGHTSTREAMER_ADAPTER=TTsdkLSAdapter
LIGHTSTREAMER_DATA_ADAPTER=HGL1_Adapter

# Your Lightstreamer Credentials
LS_USER=your.username
LS_TOKEN=YOUR_LIGHTSTREAMER_AUTH_TOKEN_HERE

# Server Port
PORT=8050
HOST=0.0.0.0
```

> **Note**: If you don't have active Lightstreamer credentials, set `ENABLE_LIGHTSTREAMER=0` or leave `LS_TOKEN` blank. The platform will automatically run with the embedded institutional dataset.

### 3. Run the Platform
On Windows:
```cmd
run.bat
```
Or directly with Python:
```bash
python -m uvicorn app:app --host 0.0.0.0 --port 8050 --reload
```
Open your browser to: **`http://localhost:8050/`**

---

## Repository Structure
```
inter-market/
├── app.py                                            # Standalone FastAPI server with WebSocket & LS support
├── lightstreamer_adapter.py                          # Native Lightstreamer client adapter & parser
├── instruments.py                                    # Instrument classification & registry mapper
├── instrument_registry.json                          # JSON instrument definitions for live feed
├── intermarket_curve_engine.py                       # Core mathematical curve engine
├── intermarket_curves_dashboard.html                 # 5-tab frontend dashboard with live plotting
├── Intermarket_Curve_Intelligence_Mathematical_Report.pdf # Publication-quality PDF report
├── .env.example                                      # Environment variables template
├── requirements.txt                                  # Python dependencies
├── run.bat                                           # Windows one-click launcher
├── PROMPT_SPECIFICATION.md                           # Mathematical specification & prompt guide
├── README.md                                         # Project documentation
└── [Data Files]
    ├── I outreight inst id                           # ICE Euribor outrights
    ├── I (Euribor spreads ) all inst id              # ICE Euribor spreads & flies
    ├── SA3 outirght inst id                          # Eurex SARON outrights
    ├── SA3 spread inst id                            # Eurex SARON spreads
    ├── So3 outirght inst isd                         # ICE SONIA outrights
    ├── SO3 spread Ins id                             # ICE SONIA spreads
    ├── SR3 spread inst id                            # CME SOFR spreads & flies
    └── src outirght curve inst is                    # CME SOFR outrights
```

---

## API Endpoints

- `GET /api/intermarket/markets`: List all connected markets and contract metadata.
- `GET /api/intermarket/curve/{market}`: Implied rate curve, spreads, and butterflies for a specific market.
- `GET /api/intermarket/curves/all`: Consolidated multi-market curve data across all economies.
- `POST /api/intermarket/curvature-4panel`: 4-panel relative curvature analytics and intermarket package gaps.
- `POST /api/intermarket/curve/fit`: Polynomial fitting ($y(x)$, $y'(x)$, $y''(x)$) and phase-space metrics.
- `POST /api/intermarket/ratio/optimize`: Dollar DV01 and volatility-weighted lot ratio solver.
