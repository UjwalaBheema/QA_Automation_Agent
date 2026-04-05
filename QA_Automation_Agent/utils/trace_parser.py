import json
import zipfile
from pathlib import Path
from loguru import logger


class TraceParser:
    """Parse Playwright trace .zip files to extract failure context for AI diagnosis."""

    def __init__(self, trace_zip_path: str):
        self.trace_path = Path(trace_zip_path)
        self._data: dict = {}
        self._loaded = False

    def _load(self):
        if self._loaded:
            return
        if not self.trace_path.exists():
            logger.warning(f"Trace file not found: {self.trace_path}")
            self._data = {}
            self._loaded = True
            return
        try:
            with zipfile.ZipFile(self.trace_path) as z:
                names = z.namelist()
                # Playwright traces store events in trace.json or chunked files
                trace_files = [n for n in names if n.endswith(".trace")]
                events = []
                for tf in trace_files:
                    raw = z.read(tf).decode("utf-8", errors="replace")
                    for line in raw.splitlines():
                        line = line.strip()
                        if line:
                            try:
                                events.append(json.loads(line))
                            except json.JSONDecodeError:
                                pass
                self._data = {"events": events, "files": names}
        except Exception as e:
            logger.error(f"Failed to parse trace {self.trace_path}: {e}")
            self._data = {"events": [], "files": []}
        self._loaded = True

    def parse(self) -> dict:
        self._load()
        return self._data

    def get_failure_summary(self) -> str:
        self._load()
        events = self._data.get("events", [])

        actions = [e for e in events if e.get("type") in ("action", "event")]
        errors = [e for e in events if "error" in e or e.get("type") == "error"]
        network_failures = [
            e for e in events
            if e.get("type") == "resource-timing"
            and e.get("response", {}).get("status", 200) >= 400
        ]
        console_errors = [
            e for e in events
            if e.get("type") == "console" and e.get("messageType") == "error"
        ]

        last_actions = actions[-5:] if len(actions) >= 5 else actions

        lines = ["=== TRACE FAILURE SUMMARY ==="]
        lines.append(f"\nLast {len(last_actions)} actions before failure:")
        for a in last_actions:
            lines.append(f"  - {a.get('type', '?')}: {a.get('apiName', a.get('name', str(a)[:120]))}")

        if errors:
            lines.append(f"\nErrors ({len(errors)}):")
            for e in errors[:3]:
                lines.append(f"  - {str(e)[:200]}")

        if network_failures:
            lines.append(f"\nNetwork failures ({len(network_failures)}):")
            for n in network_failures[:3]:
                resp = n.get("response", {})
                lines.append(f"  - {resp.get('status')} {n.get('url', '?')[:120]}")

        if console_errors:
            lines.append(f"\nConsole errors ({len(console_errors)}):")
            for c in console_errors[:3]:
                lines.append(f"  - {c.get('text', str(c)[:120])}")

        if not events:
            lines.append("\n(No trace events found — trace may not have been captured)")

        return "\n".join(lines)

    def get_dom_snapshot(self) -> str:
        self._load()
        events = self._data.get("events", [])
        snapshots = [
            e for e in events
            if e.get("type") == "snapshot" and "html" in e
        ]
        if not snapshots:
            return "(No DOM snapshot available in trace)"
        # Return snapshot just before the last event
        snapshot = snapshots[-1].get("html", "")
        return snapshot[:3000] + ("..." if len(snapshot) > 3000 else "")
