from dataclasses import dataclass
from pathlib import Path
from Skill import SKILL_REGISTRY
from MemoryManager import MemoryManager
import datetime
import os


@dataclass
class SystemPromptConfig:
    workdir: Path
    tools: list[dict]
    skill_loader: SKILL_REGISTRY
    memory_mgr: MemoryManager
    model: str


class SystemPromptBuilder:
    """
    Assemble the system prompt from independent sections.

    The teaching goal here is clarity:
    each section has one source and one responsibility.

    That makes the prompt easier to reason about, easier to test, and easier
    to evolve as the agent grows new capabilities.
    """

    def __init__(self, config: SystemPromptConfig):
        self.workdir = config.workdir
        self.tools = config.tools
        self.skill_loader = config.skill_loader
        self.memory_mgr = config.memory_mgr
        self.model = config.model

    def _build_core(self) -> str:
        return (
            f"You are a coding agent operating in {self.workdir}.\n"
            "Use the provided tools to explore, read, write, and edit files.\n"
            "Always verify before assuming. Prefer reading files over guessing."
        )

    def _build_tools(self) -> str:
        tools_section = ""
        for tool in self.tools:
            name = tool.get("name")
            description = tool.get("description")
            schemua = []
            for key in tool.get("input_schema", {}).get("properties", {}):
                schemua.append(key)
            tools_section += f"- {name}(params: {', '.join(schemua)}): {description} \n"
        return f"# Tools\n{tools_section}"

    def _build_skills(self) -> str:
        skills_section = ""
        skills_section += self.skill_loader.get_descriptions()
        return f"# Skills\n{skills_section}"

    def _build_memory(self) -> str:
        memory_section = ""
        memory_section += self.memory_mgr.load_memory_prompt()
        return f"# Memory\n{memory_section}"

    def _build_claude_md(self) -> str:
        """
        Load CLAUDE.md files in priority order (all are included):
        1. ~/.claude/CLAUDE.md (user-global instructions)
        2. <project-root>/CLAUDE.md (project instructions)
        3. <current-subdir>/CLAUDE.md (directory-specific instructions)
        """
        claude_md_section = ""
        user_md = Path.home() / ".claude" / "CLAUDE.md"
        if user_md.exists():
            claude_md_section += f"## From {user_md}:\n{user_md.read_text().strip()}\n"
        project_md = self.workdir / "CLAUDE.md"
        if project_md.exists():
            claude_md_section += (
                f"## From {project_md}:{project_md.read_text().strip()}\n"
            )
        return f"# CLAUDE.md\n{claude_md_section}"

    # -- Section 6: Dynamic context --
    def _build_dynamic_context(self) -> str:
        lines = [
            f"Current date: {datetime.date.today().isoformat()}",
            f"Working directory: {self.workdir}",
            f"Model: {self.model}",
            f"Platform: {os.uname().sysname}",
        ]
        return "# Dynamic context\n" + "\n".join(lines)

    def build(self) -> str:
        sections = []

        core = self._build_core()
        if core:
            sections.append(core)

        tools = self._build_tools()
        if tools:
            sections.append(tools)

        skills = self._build_skills()
        if skills:
            sections.append(skills)

        memory = self._build_memory()
        if memory:
            sections.append(memory)

        claude_md = self._build_claude_md()
        if claude_md:
            sections.append(claude_md)

        dynamic = self._build_dynamic_context()
        if dynamic:
            sections.append(dynamic)

        return "\n\n".join(sections)
