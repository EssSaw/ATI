import argparse
import asyncio
import os
import sys
from typing import Any, Dict

from .fixtures import EVAL_FIXTURES
from ..app.schemas import AgentRunRequest, Settings
from ..app.sessions import SessionStore
from ..app.agent.runner import run_agent


async def run_live_eval(provider: str, api_key: str, model: str = None) -> Dict[str, Any]:
    """Run evaluation against live LLM API if explicitly invoked by user with credentials."""
    store = SessionStore()
    settings = Settings(
        mode="direct",
        provider=provider,
        api_key=api_key,
        model=model,
        enable_code_execution=False,
    )

    results = []
    print(f"Running live evaluation against provider '{provider}' ({model or 'default model'})...")

    for fix in EVAL_FIXTURES:
        req = AgentRunRequest(
            code=fix.code,
            question=fix.question,
            settings=settings,
        )
        try:
            resp = await run_agent(req, store)
            matched_bug = any(fix.expected_indicator.lower() in b.message.lower() for b in resp.report.bugs)
            print(f"[RESPONSE] {fix.id}: indicator_match={matched_bug}; {len(resp.report.bugs)} finding(s), {resp.latency_ms}ms")
            results.append({"id": fix.id, "success": True, "indicator_match": matched_bug, "bugs": len(resp.report.bugs), "latency_ms": resp.latency_ms})
        except Exception as err:
            print(f"[ERR] {fix.id}: {err}")
            results.append({"id": fix.id, "success": False, "error": str(err)})

    return {"results": results}


def main():
    parser = argparse.ArgumentParser(description="Optional live evaluation runner for AI Coding Assistant.")
    parser.add_argument("--provider", default="openai", help="Provider name (openai, groq, etc.)")
    parser.add_argument("--api-key", default=os.getenv("OPENAI_API_KEY"), help="API key")
    parser.add_argument("--model", default=None, help="Model override")

    args = parser.parse_args()
    if not args.api_key:
        print("Note: Live evaluation requires an API key (--api-key or OPENAI_API_KEY env).")
        print("Use 'python -m backend.eval.run_eval' for the offline tool checks (runtime and logic detection are skipped).")
        sys.exit(0)

    asyncio.run(run_live_eval(provider=args.provider, api_key=args.api_key, model=args.model))


if __name__ == "__main__":
    main()
