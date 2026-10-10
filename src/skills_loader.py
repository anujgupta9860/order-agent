"""Hot-reloaded skill definitions.

Skills live in skills/skills.yaml, not in code. The loader checks the
file's mtime on every request and swaps in a new skill set after
validating it — a broken edit keeps the last good set serving and
records the error (see status()).

The agent card and the MCP tools/list are both built from here, so one
edit updates A2A discovery and MCP at once. The router's registry then
re-discovers via the card (TTL or POST /skills/refresh).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

# action names the agent can actually execute (see agent.OrderAgent)
KNOWN_ACTIONS = {
    "track", "cancel", "modify", "return", "reorder",
    "list", "estimate", "place", "order_info",
}


class SkillError(RuntimeError):
    pass


class SkillLoader:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._skills: list[dict] = []
        self._mtime: float | None = None
        self.last_reload: str | None = None
        self.last_error: str | None = None
        self.reload()

    # ------------------------------------------------------------------ API
    def reload(self) -> None:
        import yaml
        if not self.path.exists():
            raise SkillError(f"skills file not found: {self.path}")
        data = yaml.safe_load(self.path.read_text()) or {}
        skills = data.get("skills", [])
        if not isinstance(skills, list) or not skills:
            raise SkillError("skills file must contain a non-empty 'skills' list")
        seen = set()
        for i, s in enumerate(skills):
            where = f"skill #{i + 1} ({s.get('name', 'unnamed')!r})"
            if not isinstance(s, dict):
                raise SkillError(f"{where}: each skill must be a mapping")
            for key in ("name", "description", "action"):
                if not s.get(key):
                    raise SkillError(f"{where}: missing {key!r}")
            if s["name"] in seen:
                raise SkillError(f"{where}: duplicate skill name")
            seen.add(s["name"])
            if s["action"] not in KNOWN_ACTIONS:
                raise SkillError(
                    f"{where}: unknown action {s['action']!r}; "
                    f"use one of {sorted(KNOWN_ACTIONS)}")
            if not isinstance(s.get("mcp_input", {}), dict):
                raise SkillError(f"{where}: mcp_input must be a mapping")
        self._skills = skills
        self._mtime = self.path.stat().st_mtime
        self.last_reload = datetime.now(timezone.utc).isoformat()
        self.last_error = None

    def maybe_reload(self) -> bool:
        """Reload if the file changed. Broken edits keep the old set."""
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            return False
        if self._mtime is not None and mtime <= self._mtime:
            return False
        try:
            self.reload()
            return True
        except SkillError as exc:
            self._mtime = mtime
            self.last_error = str(exc)
            return False

    def skills(self) -> list[dict]:
        self.maybe_reload()
        return list(self._skills)

    def card_skills(self) -> list[dict]:
        return [{"name": s["name"], "description": s["description"]}
                for s in self.skills()]

    def mcp_tools(self) -> list[dict]:
        return [{"name": s["name"], "description": s["description"],
                 "inputSchema": s.get("mcp_input") or
                 {"type": "object", "properties": {}}}
                for s in self.skills()]

    def action_for(self, skill_name: str) -> str | None:
        for s in self.skills():
            if s["name"] == skill_name:
                return s["action"]
        return None

    def status(self) -> dict:
        return {
            "path": str(self.path),
            "skills": len(self._skills),
            "last_reload": self.last_reload,
            "last_error": self.last_error,
            "live": self.last_error is None,
        }
