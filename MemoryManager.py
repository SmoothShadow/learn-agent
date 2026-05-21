from dataclasses import dataclass
from pathlib import Path
import re
import yaml


@dataclass
class MemoryConfig:
    memory_dir: Path
    memory_types: set
    workdir: Path
    max_index_lines: int = 200


class MemoryManager:

    def __init__(self, config: MemoryConfig):
        self.workdir = config.workdir
        self.max_index_lines = config.max_index_lines
        self.memory_dir = config.memory_dir
        self.memory_types = config.memory_types
        self.memories = {}  # name -> {description, type, content}

    def load_all(self):
        """Load MEMORY.md index and all individual memory files."""
        self.memories = {}
        if not self.memory_dir.exists():
            return
        # Scan all .md files except MEMORY.md
        for md_file in self.memory_dir.glob("*.md"):
            if md_file.name == "MEMORY.md":
                continue
            # TODO: Load and parse the memory file
            parsed = self._parse_memory_file(md_file)
            if parsed:
                name = parsed.get("name", md_file.stem)
                self.memories[name] = {
                    "description": parsed.get("description", ""),
                    "type": parsed.get("type", "project"),
                    "content": parsed.get("content", ""),
                    "file": md_file.name,
                }

        count = len(self.memories)
        if count > 0:
            print(f"Loaded {count} memories")

    def load_memory_prompt(self) -> str:
        if not self.memories:
            return ""
        sections = []
        sections.append("# Memories (persistent across sessions)")
        sections.append("")

        for mem_type in self.memory_types:
            type_memories = {
                k: v for k, v in self.memories.items() if v["type"] == mem_type
            }
            if not type_memories:
                continue
            sections.append(f"## {mem_type}")
            for name, mem in type_memories.items():
                sections.append(f"### {name}: {mem['description']}")
                if mem["content"].strip():
                    sections.append(mem["content"])

        return "\n".join(sections)

    def _parse_memory_file(self, md_file: Path) -> dict | None:
        """Parse a memory .md file and return its metadata and content."""
        try:
            content = md_file.read_text(encoding="utf-8")
            # Extract frontmatter (YAML between ---)
            match = re.match(r"^---\s*(.*?)\s*---\s*(.*)", content, re.DOTALL)
            if not match:
                return None
            frontmatter, body = match.groups()
            data = yaml.safe_load(frontmatter) or {}
            return {
                "name": data.get("name", md_file.stem),
                "description": data.get("description", ""),
                "type": data.get("type", "user"),
                "content": body.strip(),
            }
        except Exception:
            return None

    def save_memory(
        self, name: str, description: str, mem_type: str, content: str
    ) -> str:
        """
        Save a memory to disk and update the index.

        Returns a status message.
        """
        if mem_type not in self.memory_types:
            return (
                f"Invalid memory type: {mem_type}. Must be one of {self.memory_types}"
            )

        # Sanitize name for filename
        safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", name.lower())
        if not safe_name:
            return "Error: invalid memory name"

        self.memory_dir.mkdir(parents=True, exist_ok=True)

        # Write individual memory file with frontmatter
        frontmatter = (
            f"---\n"
            f"name: {name}\n"
            f"description: {description}\n"
            f"type: {mem_type}\n"
            f"---\n"
            f"{content}\n"
        )
        file_name = f"{safe_name}.md"
        file_path = self.memory_dir / file_name
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(frontmatter)

        # Update in-memory store
        self.memories[name] = {
            "description": description,
            "type": mem_type,
            "content": content,
            "file": file_name,
        }

        # Rebuild MEMORY.md index
        self._rebuild_index()

        return f"Saved memory '{name}' [{mem_type}] to {file_path.relative_to(self.workdir)}"

    def _rebuild_index(self):
        """Rebuild MEMORY.md from current in-memory state, capped at 200 lines."""
        lines = ["# memory Index", ""]
        for name, mem in self.memories.items():
            lines.append(f"- {name} ({mem['type']}): {mem['description']}")
            if len(lines) >= self.max_index_lines:
                lines.append("... (truncated)")
                break

        self.memory_dir.mkdir(parents=True, exist_ok=True)

        # Write to MEMORY.md
        memory_path = self.memory_dir / "MEMORY.md"
        with open(memory_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    def _parse_frontmatter(self, text: str) -> dict | None:
        """Parse --- delimited frontmatter + body content."""
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", text, re.DOTALL)
        if not match:
            return None
        header, body = match.group(1), match.group(2)
        result = {"content": body.strip()}
        for line in header.splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                result[key.strip()] = value.strip()
        return result
