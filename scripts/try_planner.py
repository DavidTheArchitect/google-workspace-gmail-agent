"""Interactive natural-language planner smoke test."""

import asyncio

from compliance_agent.llm.planner import build_planner
from compliance_agent.settings import Settings


async def try_planner() -> None:
    """Plan one request without opening a browser."""

    request = await asyncio.to_thread(input, "Request: ")
    result = await build_planner(Settings(plan_only=True)).plan(request)
    print(result.plan.model_dump_json(indent=2))


if __name__ == "__main__":
    asyncio.run(try_planner())
