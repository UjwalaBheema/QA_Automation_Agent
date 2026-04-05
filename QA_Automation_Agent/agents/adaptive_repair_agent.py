from pathlib import Path

import yaml
from loguru import logger

from utils.claude_client import ask

CONFIG_PATH = Path("config/agents_config.yaml")
MAX_REPAIR_ATTEMPTS = 3


class AdaptiveRepairAgent:
    """
    Reads a failing Playwright script and an error diagnosis, asks Claude to
    produce a corrected version, validates it, and saves it as {name}_repaired.py.
    """

    def __init__(self):
        with open(CONFIG_PATH) as f:
            config = yaml.safe_load(f)
        self.system_prompt = config["adaptive_repair"]["system_prompt"]

    def _clean_code(self, raw: str) -> str:
        code = raw.strip()
        if code.startswith("```"):
            lines = code.split("\n")[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            code = "\n".join(lines)
        return code.strip()

    def _validate_python(self, code: str) -> tuple[bool, str]:
        try:
            compile(code, "<string>", "exec")
            return True, ""
        except SyntaxError as e:
            return False, str(e)

    async def run(self, script_path: str, diagnosis: dict, attempt: int = 1) -> str:
        if attempt > MAX_REPAIR_ATTEMPTS:
            logger.error(f"[AdaptiveRepair] Max repair attempts ({MAX_REPAIR_ATTEMPTS}) reached for {script_path}")
            return script_path

        script = Path(script_path)
        if not script.exists():
            logger.error(f"[AdaptiveRepair] Script not found: {script_path}")
            return script_path

        original_code = script.read_text()
        flow_name = script.stem.replace("_repaired", "")
        logger.info(f"[AdaptiveRepair] Repair attempt {attempt} for: {flow_name}")

        suggested_selectors = diagnosis.get("suggested_selectors", [])
        selectors_text = "\n".join(f"  - {s}" for s in suggested_selectors) if suggested_selectors else "  (none suggested)"

        prompt = (
            f"This Playwright script is failing. Apply the fix described below.\n\n"
            f"=== DIAGNOSIS ===\n"
            f"Root cause type: {diagnosis.get('root_cause_type', 'unknown')}\n"
            f"Root cause: {diagnosis.get('root_cause', '')}\n"
            f"Affected selector: {diagnosis.get('affected_selector', 'N/A')}\n"
            f"Suggested replacement selectors:\n{selectors_text}\n"
            f"Fix instructions: {diagnosis.get('fix_description', '')}\n\n"
            f"=== ORIGINAL SCRIPT ===\n{original_code}\n\n"
            "Return ONLY the corrected Python script. No markdown, no explanation."
        )

        repaired_code = self._clean_code(ask(self.system_prompt, prompt, max_tokens=3000))
        valid, err = self._validate_python(repaired_code)

        if not valid:
            logger.warning(f"[AdaptiveRepair] Repaired script has syntax error: {err}")
            if attempt < MAX_REPAIR_ATTEMPTS:
                # Retry with the syntax error as additional context
                fix_prompt = (
                    f"The repaired script has a syntax error: {err}\n\n"
                    f"Fix ONLY the syntax error:\n\n{repaired_code}"
                )
                repaired_code = self._clean_code(ask(self.system_prompt, fix_prompt, max_tokens=3000))
                valid, err = self._validate_python(repaired_code)
                if not valid:
                    logger.error(f"[AdaptiveRepair] Still invalid after syntax fix: {err}")

        repaired_path = script.parent / f"{flow_name}_repaired.py"
        repaired_path.write_text(repaired_code)
        logger.info(f"[AdaptiveRepair] Repaired script saved: {repaired_path}")
        return str(repaired_path)
