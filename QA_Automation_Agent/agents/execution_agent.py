from typing import Optional, List
import asyncio
import sys
import time
from datetime import datetime
from pathlib import Path

from loguru import logger
from utils.run_context import RUN_DATE

RESULTS_DIR = Path("results")


class ExecutionAgent:
    """
    Runs a generated Playwright script as a subprocess and collects
    artifacts from the date-based output directory structure.

    Expected artifact layout:
      results/{YYYY-MM-DD}/
        screenshots/{flow_name}/   ← step screenshots
        videos/{flow_name}/        ← .webm recording
        traces/{flow_name}.zip     ← Playwright trace
    """

    async def run(self, script_path: str, timeout: int = 120) -> dict:
        script    = Path(script_path)
        flow_name = script.stem.replace("_repaired", "")

        if not script.exists():
            return self._error_result(script_path, flow_name, f"Script not found: {script_path}")

        logger.info(f"[Execution] ▶ {script.name}")
        start = time.time()

        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, str(script),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(Path.cwd()),
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                raise TimeoutError(f"Script timed out after {timeout}s")

            stdout    = stdout_bytes.decode("utf-8", errors="replace")
            stderr    = stderr_bytes.decode("utf-8", errors="replace")
            exit_code = proc.returncode
            duration  = round(time.time() - start, 2)
            status    = "passed" if exit_code == 0 else "failed"
            error_msg = stderr.strip() if exit_code != 0 else ""

        except Exception as e:
            duration  = round(time.time() - start, 2)
            status    = "failed"
            stdout = stderr = ""
            error_msg = str(e)
            logger.error(f"[Execution] Exception: {e}")

        level = "SUCCESS" if status == "passed" else "ERROR"
        logger.log(level, f"[Execution] {flow_name}: {status} in {duration}s")

        trace_path  = self._find_trace(flow_name)
        screenshots = self._find_screenshots(flow_name)
        video_path  = self._find_video(flow_name)

        if screenshots:
            logger.info(f"[Execution] {len(screenshots)} screenshot(s) → results/{RUN_DATE}/screenshots/{flow_name}/")
        if video_path:
            logger.info(f"[Execution] Video → {video_path}")
        if trace_path:
            logger.info(f"[Execution] Trace → {trace_path}")

        return {
            "status":           status,
            "script_path":      script_path,
            "flow_name":        flow_name,
            "error_message":    error_msg,
            "stdout":           stdout,
            "stderr":           stderr,
            "trace_path":       trace_path,
            "screenshots":      screenshots,
            "video_path":       video_path,
            "duration_seconds": duration,
            "timestamp":        datetime.utcnow().isoformat(),
        }

    # ------------------------------------------------------------------
    def _dated(self, *parts) -> Path:
        return RESULTS_DIR / RUN_DATE / Path(*parts)

    def _find_trace(self, flow_name: str) -> Optional[str]:
        for name in [flow_name, f"{flow_name}_repaired"]:
            p = self._dated("traces", f"{name}.zip")
            if p.exists():
                return str(p)
        return None

    def _find_screenshots(self, flow_name: str) -> list[str]:
        per_flow = self._dated("screenshots", flow_name)
        if per_flow.exists():
            shots = sorted(per_flow.glob("*.png"))
            if shots:
                return [str(s) for s in shots]
        return []

    def _find_video(self, flow_name: str) -> Optional[str]:
        per_flow = self._dated("videos", flow_name)
        if per_flow.exists():
            videos = list(per_flow.glob("*.webm"))
            if videos:
                return str(max(videos, key=lambda p: p.stat().st_mtime))
        return None

    def _error_result(self, script_path: str, flow_name: str, msg: str) -> dict:
        return {
            "status":           "failed",
            "script_path":      script_path,
            "flow_name":        flow_name,
            "error_message":    msg,
            "stdout": "", "stderr": "",
            "trace_path":       None,
            "screenshots":      [],
            "video_path":       None,
            "duration_seconds": 0,
            "timestamp":        datetime.utcnow().isoformat(),
        }
