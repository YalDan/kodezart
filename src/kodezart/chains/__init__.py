"""LangChain and LangGraph chain definitions.

Chains orchestrate multi-step LLM pipelines via LangGraph StateGraph
or LangChain LCEL. Each chain module should:
- Define a StateGraph or LCEL chain.
- Expose an async callable with typed input/output.
- Keep LLM configuration injectable.

Chains are called from the services/ layer, never directly from handlers.
"""
