"""Prompt for agent-generated branch names."""

PROMPT = (
    "Analyze the following task description and generate a short, "
    "descriptive git branch name slug. Use lowercase letters, numbers, "
    "and hyphens only. Max 50 characters. No prefix needed. "
    "Examples: 'fix-auth-middleware', 'add-user-settings'. "
    "Output ONLY the structured JSON."
)
