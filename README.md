# Intermarket Fixed Income Curve Intelligence Platform

A high-performance quantitative analytics platform and interactive terminal for cross-economy fixed income STIR curves (Short-Term Interest Rate futures):
- **USD SOFR** (`SR3` on CME)
- **Eurozone Euribor** (`I` on ICE)
- **UK SONIA** (`SO3` on ICE)
- **Swiss SARON** (`SA3` on Eurex)

---

## Key Features

1. **Three-Layer Multi-Market Geometry**:
   - **Level 1 (Outrights)**: Implied forward yield curves ($100 - \text{VWAP}$).
   - **Level 2 (3M Calendar Spreads)**: Sequential quarter-to-quarter slopes ($S_k = r_{k+1} - r_k$).
   - **Level 3 (3M Butterflies)**: Discrete second differences / curvature ($F_k = S_k - S_{k+1}$).

2. **Relative Curvature & Structure Lab (4-Panel Visual Analytics)**:
   - Panel 1: Curve Shape & Rate Moves vs $T_0$ with zero-curvature straight line.
   - Panel 2: Slope of $3\text{M}$ flies vs $6\text{M}$ fly $\div 4$ even-share baseline.
   - Panel 3: Relative Trade Curvature Departure ($4 \times 3\text{M fly} - 6\text{M fly}$) and intermarket gaps.
   - Panel 4: Peak Curvature Bulge above straight line (verifying $\text{belly bulge} = \frac{1}{2} \times 6\text{M fly}$).

3. **Advanced Mathematical Curve Lab**:
   - **Hamiltonian Phase Portrait**: Mapping slope momentum $y'(x)$ against curvature restoring force $y''(x)$.
   - **Discrete Laplacian Green's Matrix**: Isolating $[+1, -2, +1]$ single-contract order flow noise from genuine policy shifts.
   - **Mechanical Bending Energy**: Scalar strain metric $E_{\text{bend}} = \sum F_k^2$.
   - **Frenet-Serret Torsion ($\tau$)**: Identifying 3-market basis mispricings (USD-EUR-GBP).
   - **Heat Diffusion Lead/Lag**: Calculating exact policy turning point phase offsets (USD leads EUR by 41 days).
   - **Mahalanobis $Z$-Score**: Objective statistical entry and exit triggers.

4. **DV01 Dollar Neutrality & Lot Sizing**:
   - Dollar DV01 per basis point adjusted for contract notional and currency FX:
     - **USD SOFR**: $25.00 / bp
     - **EUR Euribor**: $27.00 / bp (€25 × 1.08)
     - **GBP SONIA**: $32.00 / bp (£25 × 1.28)
     - **CHF SARON**: $28.50 / bp (CHF 25 × 1.14)
   - Dynamic lot ratios: **27 SR3 : 25 Euribor** (≈ 5 : 4.6).

5. **Instant Publication-Quality PDF Report**:
   - Built-in ReportLab and Matplotlib PDF compilation engine serving institutional reports at `/Intermarket_Curve_Intelligence_Mathematical_Report.pdf`.

---

## Quick Start

### Prerequisites
- Python 3.10+
- Install dependencies:
  ```bash
  pip install -r requirements.txt
  ```

### Run the Server
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
intermarket-curve-intelligence/
├── app.py                                            # Standalone FastAPI server
├── intermarket_curve_engine.py                       # Core mathematical curve engine
├── intermarket_curves_dashboard.html                 # Complete 5-tab frontend dashboard
├── Intermarket_Curve_Intelligence_Mathematical_Report.pdf # Publication-quality PDF report
├── requirements.txt                                  # Python dependencies
├── run.bat                                           # Windows one-click launcher
├── PROMPT_SPECIFICATION.md                           # Master prompt and mathematical specification
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
