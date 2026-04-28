"""Prompt for agent-generated commit messages."""

PROMPT = (
    "You are reviewing changes in a git worktree. "
    "Inspect the uncommitted changes using git diff and git status. "
    "Summarize all changes into a single conventional commit message. "
    "The title must be under 72 characters, using conventional commit format "
    "(feat:, fix:, refactor:, chore:, etc). "
    "The body should explain WHY the changes were made. "
    "Output ONLY the structured JSON."
)
