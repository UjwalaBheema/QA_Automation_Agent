from typing import Optional, List
import json
import os
import subprocess
import sys
from pathlib import Path

from loguru import logger
from utils.run_context import RUN_DATE, RUN_TS, summary_path, log_path

from agents.flow_discovery_agent import FlowDiscoveryAgent, AUTH_FLOW_NAMES
from agents.script_generator_agent import ScriptGeneratorAgent
from agents.execution_agent import ExecutionAgent
from agents.error_diagnosis_agent import ErrorDiagnosisAgent
from agents.adaptive_repair_agent import AdaptiveRepairAgent
from agents.regression_monitor_agent import RegressionMonitorAgent

RESULTS_DIR    = Path("results")
AUTH_STATE     = RESULTS_DIR / "auth_state.json"
MAX_REPAIR_ATTEMPTS = 3


class QAOrchestrator:
    """
    Central coordinator:
      Step 0  — Ensure auth state exists (run login_setup.py if needed)
      Step 1  — Flow discovery (post-login pages only)
      Step 2  — Script generation (uses auth_state, per-flow output dirs)
      Step 3  — Execution
      Step 4  — Diagnose + Repair (up to 3 attempts)
      Step 5  — Regression monitoring
    """

    def __init__(self):
        self.url              = os.getenv("CHARTREQUEST_URL", "https://qa.greencheckhealth.com/login")
        self.flow_discovery   = FlowDiscoveryAgent()
        self.script_generator = ScriptGeneratorAgent()
        self.execution        = ExecutionAgent()
        self.error_diagnosis  = ErrorDiagnosisAgent()
        self.adaptive_repair  = AdaptiveRepairAgent()
        self.regression_monitor = RegressionMonitorAgent()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    async def run(self, task: str = "full", flow: Optional[str] = None) -> List[dict]:
        logger.info(f"[Orchestrator] Starting — task={task!r}  flow={flow!r}  target={self.url}")
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)

        # --- STEP 0: Ensure authenticated session ---
        if task not in ("discover",):
            await self._ensure_auth()

        # --- STEP 1: Flow Discovery ---
        if task in ("full", "discover", "generate", "execute"):
            logger.info("[Orchestrator] Phase 1: Flow Discovery (post-login)")
            flows = await self.flow_discovery.run(self.url)
        else:
            flows = SEED_FLOWS_FALLBACK

        # Exclude any residual auth flows
        flows = [f for f in flows if f.get("name", "").lower() not in AUTH_FLOW_NAMES]

        # Filter to specific flow if requested
        if flow:
            filtered = [f for f in flows if f["name"] == flow]
            if not filtered:
                logger.warning(f"[Orchestrator] Flow '{flow}' not found in discovered list.")
            flows = filtered or flows

        if task == "discover":
            logger.info(f"[Orchestrator] Discovery complete — {len(flows)} flows:")
            for f in flows:
                logger.info(f"  [{f.get('priority','?'):>6}] {f['name']}: {f.get('description','')}")
            return [{"task": "discover", "flows": flows}]

        # --- STEP 2-5: Per-flow pipeline ---
        all_results = []
        for flow_spec in flows:
            result = await self._run_flow(flow_spec, task=task)
            all_results.append(result)
            self._log_flow_result(result)

        # Save run summary under the dated directory
        sp = Path(summary_path())
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_text(json.dumps(all_results, indent=2, default=str))
        logger.info(f"[Orchestrator] Summary → {sp}")

        passed = sum(1 for r in all_results if r.get("final_status") == "passed")
        failed = len(all_results) - passed
        logger.info(f"[Orchestrator] Done: {passed} passed / {failed} failed / {len(all_results)} total")
        return all_results

    # ------------------------------------------------------------------
    # Auth setup
    # ------------------------------------------------------------------
    async def _ensure_auth(self):
        if AUTH_STATE.exists():
            logger.info(f"[Orchestrator] Auth state found: {AUTH_STATE}")
            return
        logger.info("[Orchestrator] No auth state — running login_setup.py ...")
        result = subprocess.run(
            [sys.executable, "scripts/login_setup.py"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            logger.error(f"[Orchestrator] login_setup.py failed:\n{result.stderr}")
        else:
            logger.info("[Orchestrator] login_setup.py completed successfully")

    # ------------------------------------------------------------------
    # Per-flow pipeline
    # ------------------------------------------------------------------
    async def _run_flow(self, flow_spec: dict, task: str = "full") -> dict:
        flow_name = flow_spec["name"]
        logger.info(f"[Orchestrator] ── Flow: {flow_name} ──")

        result: dict = {
            "flow_name":     flow_name,
            "flow_spec":     flow_spec,
            "final_status":  "pending",
            "repair_attempts": 0,
            "diagnosis":     None,
            "regression":    None,
        }

        # Step 2 — Script generation
        try:
            script_path = await self.script_generator.run(flow_spec)
            if not script_path:  # Generation failed after 3 retries
                logger.error(f"[Orchestrator] Script generation failed for {flow_name} after 3 attempts")
                result.update({"final_status": "failed", "error": "Script generation failed"})
                return result
            result["script_path"] = script_path
        except Exception as e:
            logger.error(f"[Orchestrator] Script generation exception for {flow_name}: {e}")
            result.update({"final_status": "failed", "error": str(e)})
            return result

        if task == "generate":
            result["final_status"] = "generated"
            return result

        # Step 3 — Execution
        execution_result = await self.execution.run(script_path)
        result["execution"] = execution_result

        # Step 4 — Diagnose + Repair loop
        repair_attempts = 0
        current_script  = script_path

        while execution_result["status"] == "failed" and repair_attempts < MAX_REPAIR_ATTEMPTS:
            repair_attempts += 1
            logger.info(f"[Orchestrator] Repair {repair_attempts}/{MAX_REPAIR_ATTEMPTS} for {flow_name}")
            diagnosis       = await self.error_diagnosis.run(execution_result)
            result["diagnosis"] = diagnosis
            current_script  = await self.adaptive_repair.run(current_script, diagnosis, attempt=repair_attempts)
            execution_result = await self.execution.run(current_script)
            result["execution"] = execution_result

        result["repair_attempts"] = repair_attempts
        result["final_status"]    = execution_result["status"]

        # Step 5 — Regression monitoring
        if execution_result["status"] == "passed" and task != "execute":
            screenshots = execution_result.get("screenshots", [])
            if screenshots:
                regression = await self.regression_monitor.run(flow_name, screenshots[-1])
                result["regression"] = regression
                if regression.get("verdict") == "regression":
                    logger.warning(f"[Orchestrator] REGRESSION detected in {flow_name}!")

        return result

    # ------------------------------------------------------------------
    def _log_flow_result(self, result: dict):
        name       = result.get("flow_name", "?")
        status     = result.get("final_status", "?")
        repairs    = result.get("repair_attempts", 0)
        regression = (result.get("regression") or {})
        verdict    = regression.get("verdict", "n/a")
        similarity = regression.get("similarity")

        icon         = "✓" if status == "passed" else "✗"
        repair_note  = f" (fixed in {repairs} repair{'s' if repairs!=1 else ''})" if repairs else ""
        reg_note     = (
            f" | visual={verdict}" + (f" {similarity:.0%}" if similarity else "")
            if verdict not in ("n/a", "baseline_created", "skipped") else ""
        )
        logger.info(f"  {icon} {name}: {status}{repair_note}{reg_note}")


# Fallback if discovery is skipped
SEED_FLOWS_FALLBACK = [
    {
        "name": "dashboard_overview",
        "description": "Verify dashboard loads correctly after login",
        "steps": ["Navigate to dashboard", "Wait for page to load", "Screenshot"],
        "expected_outcome": "Dashboard visible with no errors",
        "priority": "high",
    }
]
