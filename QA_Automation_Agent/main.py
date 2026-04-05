from typing import Optional, List
"""
QA Automation AI Agent — Entry Point
Target: qa.greencheckhealth.com

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PIPELINE MODE  (runs multiple steps)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  python main.py                          Full pipeline (default)
  python main.py --task full              Discover → Generate → Execute → Regress
  python main.py --task discover          Discover flows only
  python main.py --task generate          Discover + Generate scripts (no execution)
  python main.py --task execute           Full pipeline, skip regression check
  python main.py --task full --flow nav   Run one specific flow end-to-end

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
MODULE MODE  (run a single agent in isolation)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  python main.py --module login           Login & save auth_state.json
  python main.py --module discover        Discover post-login flows
  python main.py --module generate        Generate scripts for all discovered flows
  python main.py --module generate        --flow dashboard_overview
  python main.py --module execute         --script scripts/generated/dashboard_overview.py
  python main.py --module diagnose        --script scripts/generated/dashboard_overview.py
  python main.py --module repair          --script scripts/generated/dashboard_overview.py
  python main.py --module regression      --flow dashboard_overview
"""
import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

load_dotenv(override=True)

# Ensure all result directories exist up front
from utils.run_context import RUN_DATE, log_path

for _d in [
    f"results/{RUN_DATE}/screenshots",
    f"results/{RUN_DATE}/videos",
    f"results/{RUN_DATE}/traces",
    f"results/{RUN_DATE}/logs",
    "results/baselines",
    "results/logs",
    "scripts/generated",
]:
    Path(_d).mkdir(parents=True, exist_ok=True)

# Logging — console + dated log file
logger.remove()
logger.add(
    sys.stderr,
    level="INFO",
    format="<green>{time:HH:mm:ss}</green> | <level>{level:<8}</level> | {message}",
)
logger.add(log_path(), level="DEBUG", rotation="10 MB")


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="QA Automation AI Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--task",
        choices=["full", "discover", "generate", "execute"],
        default=None,
        help="Pipeline mode — runs multiple steps in sequence",
    )
    mode.add_argument(
        "--module",
        choices=["login", "discover", "generate", "execute", "diagnose", "repair", "regression"],
        default=None,
        help="Module mode — run a single agent in isolation",
    )

    parser.add_argument("--flow",   default=None, help="Target a specific flow by name")
    parser.add_argument("--script", default=None, help="Path to a generated script (for execute/diagnose/repair)")
    return parser


# ──────────────────────────────────────────────
# Module runners
# ──────────────────────────────────────────────
async def run_module_login():
    logger.info("[Module] login — establishing auth session")
    result = subprocess.run(
        [sys.executable, "scripts/login_setup.py"],
        capture_output=False,
    )
    return {"status": "ok" if result.returncode == 0 else "failed"}


async def run_module_discover(flow: Optional[str]):
    from agents.flow_discovery_agent import FlowDiscoveryAgent
    import os
    url   = os.getenv("CHARTREQUEST_URL", "https://qa.greencheckhealth.com/login")
    flows = await FlowDiscoveryAgent().run(url)
    if flow:
        flows = [f for f in flows if f["name"] == flow]
    logger.info(f"[Module] discover — found {len(flows)} flow(s)")
    for f in flows:
        logger.info(f"  [{f.get('priority','?'):>6}] {f['name']}: {f.get('description','')}")
    return {"flows": flows}


async def run_module_generate(flow: Optional[str]):
    from agents.flow_discovery_agent import FlowDiscoveryAgent, AUTH_FLOW_NAMES
    from agents.script_generator_agent import ScriptGeneratorAgent
    import os
    url   = os.getenv("CHARTREQUEST_URL", "https://qa.greencheckhealth.com/login")
    flows = await FlowDiscoveryAgent().run(url)
    flows = [f for f in flows if f.get("name", "").lower() not in AUTH_FLOW_NAMES]
    if flow:
        flows = [f for f in flows if f["name"] == flow]
        if not flows:
            logger.warning(f"Flow '{flow}' not found — generating all")
            flows = await FlowDiscoveryAgent().run(url)

    gen = ScriptGeneratorAgent()
    paths = []
    for f in flows:
        path = await gen.run(f)
        if path:  # Skip None (failed generations)
            paths.append(path)
            logger.info(f"[Module] generate — {f['name']} → {path}")
        else:
            logger.warning(f"[Module] generate — {f['name']} SKIPPED (generation failed)")
    logger.info(f"[Module] generate — Generated {len(paths)} valid scripts")
    return {"scripts": paths}


async def run_module_execute(script: Optional[str], flow: Optional[str]):
    from agents.execution_agent import ExecutionAgent
    if script:
        scripts = [script]
    elif flow:
        p = Path(f"scripts/generated/{flow}.py")
        if not p.exists():
            logger.error(f"Script not found: {p}. Run --module generate --flow {flow} first.")
            return {"status": "failed"}
        scripts = [str(p)]
    else:
        scripts = sorted(str(p) for p in Path("scripts/generated").glob("*.py")
                         if "_repaired" not in p.name)

    agent   = ExecutionAgent()
    results = []
    for s in scripts:
        result = await agent.run(s)
        results.append(result)
        icon = "✓" if result["status"] == "passed" else "✗"
        logger.info(f"[Module] execute — {icon} {result['flow_name']}: {result['status']}")
    return {"results": results}


async def run_module_diagnose(script: Optional[str], flow: Optional[str]):
    from agents.execution_agent import ExecutionAgent
    from agents.error_diagnosis_agent import ErrorDiagnosisAgent

    target = script or (f"scripts/generated/{flow}.py" if flow else None)
    if not target:
        logger.error("Provide --script or --flow")
        return {}

    exec_result = await ExecutionAgent().run(target)
    if exec_result["status"] == "passed":
        logger.info("[Module] diagnose — script passed, no diagnosis needed")
        return {"status": "passed"}

    diagnosis = await ErrorDiagnosisAgent().run(exec_result)
    logger.info(f"[Module] diagnose — root cause: {diagnosis.get('root_cause_type')} | {diagnosis.get('root_cause','')[:100]}")
    print(json.dumps(diagnosis, indent=2))
    return diagnosis


async def run_module_repair(script: Optional[str], flow: Optional[str]):
    from agents.execution_agent import ExecutionAgent
    from agents.error_diagnosis_agent import ErrorDiagnosisAgent
    from agents.adaptive_repair_agent import AdaptiveRepairAgent

    target = script or (f"scripts/generated/{flow}.py" if flow else None)
    if not target:
        logger.error("Provide --script or --flow")
        return {}

    exec_result = await ExecutionAgent().run(target)
    if exec_result["status"] == "passed":
        logger.info("[Module] repair — script already passes, nothing to repair")
        return {"status": "already_passing"}

    diagnosis    = await ErrorDiagnosisAgent().run(exec_result)
    repaired     = await AdaptiveRepairAgent().run(target, diagnosis)
    verify       = await ExecutionAgent().run(repaired)
    logger.info(f"[Module] repair — after repair: {verify['status']}")
    return {"repaired_script": repaired, "status": verify["status"]}


async def run_module_regression(flow: Optional[str]):
    from agents.regression_monitor_agent import RegressionMonitorAgent
    agent = RegressionMonitorAgent()

    if flow:
        shots_dir = Path(f"results/{RUN_DATE}/screenshots/{flow}")
        screenshots = sorted(shots_dir.glob("*.png")) if shots_dir.exists() else []
        if not screenshots:
            logger.warning(f"No screenshots found for flow '{flow}' on {RUN_DATE}")
            return {}
        result = await agent.run(flow, str(screenshots[-1]))
        logger.info(f"[Module] regression — {flow}: {result.get('verdict')} ({result.get('similarity', '?')})")
        return result

    # All flows with screenshots today
    dated_shots = Path(f"results/{RUN_DATE}/screenshots")
    if not dated_shots.exists():
        logger.warning(f"No screenshots dir for {RUN_DATE}")
        return {}

    results = {}
    for flow_dir in sorted(dated_shots.iterdir()):
        if flow_dir.name.startswith("_"):
            continue
        shots = sorted(flow_dir.glob("*.png"))
        if shots:
            r = await agent.run(flow_dir.name, str(shots[-1]))
            results[flow_dir.name] = r
            logger.info(f"[Module] regression — {flow_dir.name}: {r.get('verdict')}")
    return results


# ──────────────────────────────────────────────
# Pipeline runner
# ──────────────────────────────────────────────
async def run_pipeline(task: str, flow: Optional[str]):
    from agents.orchestrator import QAOrchestrator
    results = await QAOrchestrator().run(task=task, flow=flow)

    print(f"\n{'=' * 62}")
    print(f"  QA RESULTS  [{RUN_DATE}]")
    print(f"{'=' * 62}")

    for r in results:
        if isinstance(r, dict) and "flows" in r:
            for f in r["flows"]:
                print(f"  [{f.get('priority','?'):>6}] {f['name']:<32} {f.get('description','')}")
            continue

        name       = r.get("flow_name", "?")
        status     = r.get("final_status", "?")
        repairs    = r.get("repair_attempts", 0)
        reg        = (r.get("regression") or {})
        verdict    = reg.get("verdict", "")
        similarity = reg.get("similarity")

        tag         = "PASS" if status == "passed" else "FAIL" if status == "failed" else status.upper()
        repair_str  = f" (+{repairs} repair{'s' if repairs!=1 else ''})" if repairs else ""
        reg_str     = f" | visual={verdict}" + (f" {similarity:.0%}" if similarity else "") if verdict else ""
        print(f"  [{tag:>4}] {name:<32}{repair_str}{reg_str}")

    print(f"{'=' * 62}\n")
    print(f"  Artifacts → results/{RUN_DATE}/")
    print(f"{'=' * 62}\n")


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────
async def main():
    args = build_parser().parse_args()

    # Default to full pipeline if nothing specified
    if args.module is None and args.task is None:
        args.task = "full"

    if args.module:
        logger.info(f"Running module: {args.module}")
        dispatch = {
            "login":      lambda: run_module_login(),
            "discover":   lambda: run_module_discover(args.flow),
            "generate":   lambda: run_module_generate(args.flow),
            "execute":    lambda: run_module_execute(args.script, args.flow),
            "diagnose":   lambda: run_module_diagnose(args.script, args.flow),
            "repair":     lambda: run_module_repair(args.script, args.flow),
            "regression": lambda: run_module_regression(args.flow),
        }
        await dispatch[args.module]()
    else:
        await run_pipeline(task=args.task, flow=args.flow)


if __name__ == "__main__":
    asyncio.run(main())
