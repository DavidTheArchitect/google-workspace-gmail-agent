"""Default structured-planner adapter for the local console."""

from compliance_agent.llm.structured import StructuredPlanner
from compliance_agent.schemas.plan import TaskPlan


class StructuredConsolePlanner:
    def __init__(self, planner: StructuredPlanner) -> None:
        self._planner = planner

    async def create_plan(self, request_text: str) -> TaskPlan:
        result = await self._planner.plan(request_text)
        return result.plan
