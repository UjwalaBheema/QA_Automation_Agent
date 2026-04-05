"""
Integration tests that run each discovered flow through the full agent pipeline.
These tests hit the real qa.chartrequest.com — set credentials in .env first.
"""
import pytest
from pathlib import Path


pytestmark = pytest.mark.asyncio


async def test_flow_discovery_returns_flows(chartrequest_url):
    """FlowDiscoveryAgent should return at least the seed flows."""
    from agents.flow_discovery_agent import FlowDiscoveryAgent, SEED_FLOWS
    agent = FlowDiscoveryAgent()
    flows = await agent.run(chartrequest_url)
    assert len(flows) >= len(SEED_FLOWS), "Should have at least the seed flows"
    names = [f["name"] for f in flows]
    assert "login" in names, "Login flow must always be present"


async def test_script_generator_produces_valid_python():
    """ScriptGeneratorAgent should generate compilable Python for the login flow."""
    from agents.script_generator_agent import ScriptGeneratorAgent
    agent = ScriptGeneratorAgent()
    flow = {
        "name": "test_login",
        "description": "Login with email and password",
        "steps": ["Navigate to login", "Fill email", "Fill password", "Click login"],
        "expected_outcome": "User is logged in",
    }
    script_path = await agent.run(flow)
    assert Path(script_path).exists(), "Script file must be created"
    code = Path(script_path).read_text()
    try:
        compile(code, script_path, "exec")
    except SyntaxError as e:
        pytest.fail(f"Generated script has syntax error: {e}")


async def test_full_login_flow(chartrequest_url, credentials):
    """End-to-end test: discover + generate + execute the login flow."""
    if not credentials["email"] or not credentials["password"]:
        pytest.skip("CHARTREQUEST_EMAIL and CHARTREQUEST_PASSWORD not set in .env")

    from agents.orchestrator import QAOrchestrator
    orchestrator = QAOrchestrator()
    results = await orchestrator.run(task="execute", flow="login")

    assert len(results) > 0, "Should have at least one result"
    login_result = next((r for r in results if r.get("flow_name") == "login"), None)
    assert login_result is not None, "Login flow result must be present"
    assert login_result["final_status"] in ("passed", "failed"), "Status must be determined"


async def test_visual_diff_no_change():
    """VisualDiff should report no change when comparing identical images."""
    from utils.visual_diff import VisualDiff
    from pathlib import Path
    import shutil

    # Use a real screenshot if one exists, otherwise skip
    screenshots = list(Path("results/screenshots").glob("*.png"))
    if not screenshots:
        pytest.skip("No screenshots available — run a flow first")

    img = str(screenshots[0])
    tmp = str(screenshots[0]).replace(".png", "_copy.png")
    shutil.copy2(img, tmp)

    result = VisualDiff.compare(img, tmp)
    assert result["similarity"] == 1.0, "Identical images should have similarity=1.0"
    assert result["distance"] == 0, "Identical images should have distance=0"
    assert not result["changed"]

    Path(tmp).unlink(missing_ok=True)


async def test_trace_parser_handles_missing_file():
    """TraceParser should gracefully handle missing trace files."""
    from utils.trace_parser import TraceParser
    parser = TraceParser("results/traces/nonexistent.zip")
    summary = parser.get_failure_summary()
    assert isinstance(summary, str)
    dom = parser.get_dom_snapshot()
    assert isinstance(dom, str)
