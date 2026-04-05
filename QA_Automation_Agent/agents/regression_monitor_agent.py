import json
from pathlib import Path

import yaml
from loguru import logger

from utils.claude_client import ask
from utils.visual_diff import VisualDiff
from utils.run_context import RUN_DATE

CONFIG_PATH   = Path("config/agents_config.yaml")
BASELINES_DIR = Path("results/baselines")   # stable — never date-stamped


class RegressionMonitorAgent:
    """
    Compares a current screenshot against a saved baseline using perceptual hashing.
    If significant changes are detected, asks Claude to classify the change as a
    regression or an acceptable UI update.
    """

    def __init__(self):
        with open(CONFIG_PATH) as f:
            config = yaml.safe_load(f)
        self.system_prompt = config["regression_monitor"]["system_prompt"]
        BASELINES_DIR.mkdir(parents=True, exist_ok=True)

    async def run(self, test_name: str, current_screenshot: str) -> dict:
        logger.info(f"[RegressionMonitor] Checking: {test_name} [{RUN_DATE}]")

        current_path = Path(current_screenshot)
        if not current_path.exists():
            logger.warning(f"[RegressionMonitor] Screenshot not found: {current_screenshot}")
            return {"verdict": "skipped", "reason": "Screenshot file not found"}

        baseline_path = BASELINES_DIR / f"{test_name}.png"

        # No baseline exists yet — save current as baseline
        if not baseline_path.exists():
            VisualDiff.save_baseline(current_screenshot, str(baseline_path))
            logger.info(f"[RegressionMonitor] Baseline created for {test_name}")
            return {
                "verdict": "baseline_created",
                "similarity": 1.0,
                "reason": "First run — current screenshot saved as baseline",
            }

        # Compare current with baseline
        diff = VisualDiff.compare(str(baseline_path), current_screenshot)

        if diff.get("error"):
            return {
                "verdict": "skipped",
                "similarity": None,
                "reason": f"Comparison error: {diff['error']}",
            }

        similarity = diff["similarity"]
        changed = diff["changed"]
        distance = diff["distance"]

        logger.info(
            f"[RegressionMonitor] {test_name}: similarity={similarity} distance={distance} changed={changed}"
        )

        if not changed:
            return {
                "verdict": "acceptable",
                "similarity": similarity,
                "distance": distance,
                "reason": "No significant visual changes detected",
            }

        # Significant change — ask Claude to classify
        prompt = (
            f"Visual comparison for test '{test_name}':\n"
            f"  Similarity score: {similarity} (1.0 = identical, 0.0 = completely different)\n"
            f"  Perceptual hash distance: {distance} / 64\n"
            f"  Change detected: Yes\n\n"
            "Based on this visual diff data, is this a regression (bug) or an acceptable UI change?\n"
            "Return your verdict as valid JSON."
        )

        try:
            response = ask(self.system_prompt, prompt, max_tokens=512)
            cleaned = response.strip()
            if cleaned.startswith("```"):
                lines = cleaned.split("\n")[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                cleaned = "\n".join(lines)
            verdict_data = json.loads(cleaned.strip())
            verdict_data["similarity"] = similarity
            verdict_data["distance"] = distance
            logger.info(
                f"[RegressionMonitor] Verdict for {test_name}: {verdict_data.get('verdict')} "
                f"(confidence={verdict_data.get('confidence')})"
            )
            return verdict_data
        except Exception as e:
            logger.error(f"[RegressionMonitor] Failed to parse Claude verdict: {e}")
            return {
                "verdict": "regression",
                "similarity": similarity,
                "distance": distance,
                "confidence": 0.5,
                "reason": f"Visual change detected (similarity={similarity}) — manual review needed",
            }
