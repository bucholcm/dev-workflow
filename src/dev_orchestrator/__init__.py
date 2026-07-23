"""dev-orchestrator — local AI dev-workflow orchestrator.

Linear (planning source of truth) + Bitbucket Cloud (code) with implementation
routed to Claude Code (`claude -p`) or Codex (`codex exec`) and a manual,
human-in-the-loop review → fix → merge loop. See dev-orchestrator-design.md.
"""

__version__ = "0.1.0"
