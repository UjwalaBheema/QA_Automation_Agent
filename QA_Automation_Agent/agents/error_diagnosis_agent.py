from typing import Optional, List
import base64
import json
from pathlib import Path

import yaml
from loguru import logger

from utils.claude_client import ask
from utils.trace_parser import TraceParser

CONFIG_PATH = Path("config/agents_config.yaml")


class ErrorDiagnosisAgent:
    """
    Analyzes a failed test execution result using trace files, error messages,
    and screenshots to diagnose the root cause via Claude.
    """

    def __init__(self):
        with open(CONFIG_PATH) as f:
            config = yaml.safe_load(f)
        self.system_prompt = config["error_diagnosis"]["system_prompt"]

    async def run(self, execution_result: dict) -> dict:
        flow_name = execution_result.get("flow_name", "unknown")
        logger.info(f"[ErrorDiagnosis] Diagnosing failure for: {flow_name}")

        error_message = execution_result.get("error_message", "")
        stderr = execution_result.get("stderr", "")
        trace_path = execution_result.get("trace_path")

        # Parse trace file if available
        trace_summary = ""
        dom_snapshot = ""
        if trace_path and Path(trace_path).exists():
            parser = TraceParser(trace_path)
            trace_summary = parser.get_failure_summary()
            dom_snapshot = parser.get_dom_snapshot()
        else:
            trace_summary = "(No trace file available)"
            dom_snapshot = "(No DOM snapshot available)"

        # Load failure screenshot if available
        screenshot_b64 = self._load_failure_screenshot(execution_result)

        # Build prompt
        prompt = (
            f"A Playwright test for flow '{flow_name}' has failed.\n\n"
            f"=== ERROR MESSAGE ===\n{error_message or 'No error message captured'}\n\n"
            f"=== STDERR ===\n{stderr[:1000] if stderr else 'None'}\n\n"
            f"=== TRACE SUMMARY ===\n{trace_summary}\n\n"
            f"=== DOM SNAPSHOT ===\n{dom_snapshot}\n\n"
        )

        if screenshot_b64:
            prompt += f"A failure screenshot was captured (base64 encoded, {len(screenshot_b64)} chars).\n"

        prompt += "\nReturn your diagnosis as valid JSON."

        try:
            response = ask(self.system_prompt, prompt, max_tokens=1024)
            cleaned = response.strip()
            if cleaned.startswith("```"):
                lines = cleaned.split("\n")[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                cleaned = "\n".join(lines)
            diagnosis = json.loads(cleaned.strip())
            logger.info(
                f"[ErrorDiagnosis] Root cause: {diagnosis.get('root_cause_type', '?')} — "
                f"{diagnosis.get('root_cause', '')[:80]}"
            )
            return diagnosis
        except Exception as e:
            logger.error(f"[ErrorDiagnosis] Failed to parse Claude response: {e}")
            return {
                "root_cause_type": "unknown",
                "root_cause": error_message or "Unknown error",
                "affected_selector": None,
                "suggested_selectors": [],
                "fix_description": "Manual investigation required",
            }

    def _load_failure_screenshot(self, execution_result: dict) -> Optional[str]:
        screenshots = execution_result.get("screenshots", [])
        failure_shots = [s for s in screenshots if "FAILURE" in s or "failure" in s]
        target = failure_shots[0] if failure_shots else (screenshots[-1] if screenshots else None)
        if target and Path(target).exists():
            try:
                data = Path(target).read_bytes()
                return base64.b64encode(data).decode()
            except Exception:
                pass
        return None
