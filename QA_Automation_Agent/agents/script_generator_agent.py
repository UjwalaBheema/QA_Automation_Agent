import re
from pathlib import Path

import yaml
from loguru import logger

from utils.claude_client import ask

CONFIG_PATH = Path("config/agents_config.yaml")
GENERATED_DIR = Path("scripts/generated")
TEMPLATE_PATH = Path("scripts/templates/base_template.py")


class ScriptGeneratorAgent:
    """
    Generates Playwright Python scripts for a given flow using Claude.
    Validates generated code with compile() and retries once on syntax errors.
    """

    def __init__(self):
        with open(CONFIG_PATH) as f:
            config = yaml.safe_load(f)
        self.system_prompt = config["script_generator"]["system_prompt"]
        GENERATED_DIR.mkdir(parents=True, exist_ok=True)

    def _load_template(self) -> str:
        if TEMPLATE_PATH.exists():
            return TEMPLATE_PATH.read_text()
        return ""

    def _clean_code(self, raw: str) -> str:
        """Strip markdown code fences and fix common Unicode issues."""
        code = raw.strip()
        if code.startswith("```"):
            lines = code.split("\n")
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            code = "\n".join(lines)

        # Fix common Unicode issues from Claude
        code = code.replace("—", "-")  # em-dash → regular dash
        code = code.replace("–", "-")  # en-dash → regular dash
        code = code.replace(""", '"')  # smart quote → regular quote
        code = code.replace(""", '"')  # smart quote → regular quote
        code = code.replace("'", "'")  # smart apostrophe → regular apostrophe
        code = code.replace("'", "'")  # smart apostrophe → regular apostrophe

        return code.strip()

    def _validate_python(self, code: str) -> tuple[bool, str]:
        try:
            compile(code, "<string>", "exec")
            return True, ""
        except SyntaxError as e:
            return False, str(e)

    async def run(self, flow: dict) -> str:
        flow_name = flow.get("name", "unnamed_flow")
        logger.info(f"[ScriptGenerator] Generating script for flow: {flow_name}")

        template = self._load_template()
        steps_text = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(flow.get("steps", [])))

        prompt = (
            f"Generate a complete Playwright Python script for this user flow:\n\n"
            f"Flow name: {flow_name}\n"
            f"Description: {flow.get('description', '')}\n"
            f"Steps:\n{steps_text}\n"
            f"Expected outcome: {flow.get('expected_outcome', '')}\n\n"
            f"Use this base template:\n```python\n{template}\n```\n\n"
            "Replace '# --- GENERATED STEPS GO HERE ---' with actual Playwright code.\n"
            "Return ONLY valid Python code. No markdown, no explanations."
        )

        code = self._clean_code(ask(self.system_prompt, prompt, max_tokens=3000))
        valid, err = self._validate_python(code)

        # Retry 1: Ask to fix the error
        if not valid:
            logger.warning(f"[ScriptGenerator] Syntax error: {err}. Retry 1...")
            fix_prompt = (
                f"Fix this Python syntax error: {err}\n\n"
                f"Corrected script:\n{code}"
            )
            code = self._clean_code(ask(self.system_prompt, fix_prompt, max_tokens=2000))
            valid, err = self._validate_python(code)

        # Retry 2: Ultra-simple approach — minimal code only
        if not valid:
            logger.warning(f"[ScriptGenerator] Still invalid: {err}. Retry 2 (minimal code)...")
            simple_prompt = (
                f"Write minimal Playwright code for: {flow_name}\n\n"
                f"Steps: {steps_text}\n\n"
                f"Template:\n{template}\n\n"
                "ONLY replace '# --- GENERATED STEPS GO HERE ---' with 5-10 short lines of code.\n"
                "Keep it simple. Return ONLY Python, no markdown."
            )
            code = self._clean_code(ask(self.system_prompt, simple_prompt, max_tokens=1500))
            valid, err = self._validate_python(code)

        # Only save if valid
        if not valid:
            logger.error(f"[ScriptGenerator] Gave up after 3 attempts: {err}. Skipping {flow_name}.")
            return None  # Signal failure

        script_path = GENERATED_DIR / f"{flow_name}.py"
        script_path.write_text(code)
        logger.info(f"[ScriptGenerator] ✓ Script saved: {script_path}")
        return str(script_path)
