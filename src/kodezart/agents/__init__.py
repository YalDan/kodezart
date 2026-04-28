"""Agent definitions.

Two agent surfaces are available:

1. claude-agent-sdk (Anthropic) — full agent loop with built-in tools:

    from claude_agent_sdk import query, ClaudeAgentOptions

    async def run_agent(prompt: str) -> str:
        result = ""
        async for message in query(
            prompt=prompt,
            options=ClaudeAgentOptions(allowed_tools=["Read", "Glob", "Grep"]),
        ):
            if hasattr(message, "result"):
                result = message.result
        return result

    Requires: ANTHROPIC_API_KEY env var.

2. pydantic-ai — typed structured outputs with schema validation:

    from pydantic_ai import Agent
    from kodezart.types.domain.some_domain import SomeDomainResult

    agent: Agent[None, SomeDomainResult] = Agent(
        model="claude-sonnet-4-6",
        result_type=SomeDomainResult,
        system_prompt="...",
    )

    Requires: ANTHROPIC_API_KEY env var.

Agents are called from the services/ layer, never directly from handlers or endpoints.
"""
