"""
run_context.py — Shared run metadata imported by all agents and generated scripts.

RUN_DATE  : "YYYY-MM-DD"   used as the top-level folder under results/
RUN_TS    : "YYYYMMDD_HHmmss"  used for unique file names within a run

Results tree per run:
    results/
    └── YYYY-MM-DD/
        ├── screenshots/
        │   └── {flow_name}/      ← step screenshots
        ├── videos/
        │   └── {flow_name}/      ← .webm recording
        ├── traces/
        │   └── {flow_name}.zip   ← Playwright trace
        ├── logs/
        │   └── run_{ts}.log
        └── run_summary_{ts}.json

Baselines (regression) remain outside dated dirs so comparisons are stable:
    results/
    └── baselines/
        └── {flow_name}.png
"""
from datetime import datetime

_now     = datetime.now()
RUN_DATE = _now.strftime("%Y-%m-%d")
RUN_TS   = _now.strftime("%Y%m%d_%H%M%S")


def dated_dir(sub: str) -> str:
    """Return e.g. 'results/2026-04-04/screenshots' """
    return f"results/{RUN_DATE}/{sub}"


def flow_dir(sub: str, flow_name: str) -> str:
    """Return e.g. 'results/2026-04-04/screenshots/dashboard_overview' """
    return f"results/{RUN_DATE}/{sub}/{flow_name}"


def trace_path(flow_name: str) -> str:
    return f"results/{RUN_DATE}/traces/{flow_name}.zip"


def summary_path() -> str:
    return f"results/{RUN_DATE}/run_summary_{RUN_TS}.json"


def log_path() -> str:
    return f"results/{RUN_DATE}/logs/run_{RUN_TS}.log"
