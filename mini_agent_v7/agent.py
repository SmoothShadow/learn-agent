#!/usr/bin/env python3
# Harness: the loop -- the model's first connection to the real world.

import json
import time
from fnmatch import fnmatch
import os
import sys
from pathlib import Path
import re
import yaml
from dotenv import load_dotenv
from anthropic import Anthropic

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import calculator, get_current_time, run_read, run_bash

try:
    import readline

    readline.parse_and_bind("set bind-tty-special-chars off")
    readline.parse_and_bind("set input-meta on")
    readline.parse_and_bind("set output-meta on")
    readline.parse_and_bind("set convert-meta off")
    readline.parse_and_bind("set enable-meta-keybindings on")
except ImportError:
    print("Module readline not available.")

load_dotenv(ROOT / ".env", override=True)

if not os.getenv("ANTHROPIC_API_KEY"):
    os.environ.pop("ANTHROPIC_API_TOKEN", None)

client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.environ["MODEL_ID"]

SUB_SYSTEM = """
你是一个子agent，可以用来执行具体的任务，帮助父agent净化上下文。当要求规划或者计划时，必须使用TODO工具来做回应。
"""

WORKDIR = Path.cwd()
THRESHOLD = 50000
TRANSCRIPT_DIR = WORKDIR / ".transcripts"
KEEP_RECENT = 3
PRESERVE_RESULT_TOOLS = {"read_file"}


def estimate_tokens(messages: list) -> int:
    """Estimate token count from messages."""
    return len(str(messages)) // 4


def micro_compact(messages: list) -> list:
    # Collect (msg_index, part_index, tool_result_dict) for all tool_result entries
    tool_result = []
    for msg_idx, msg in enumerate(messages):
        if msg.get("role") == "user":
            content = msg.get("content", [])
            if isinstance(content, list):
                for part_idx, part in enumerate(content):
                    if isinstance(part, dict) and part.get("type") == "tool_result":
                        tool_result.append((msg_idx, part_idx, part))
    if len(tool_result) < KEEP_RECENT:
        return messages
    # Find tool_name for each result by matching tool_use_id in prior assistant messages
    tool_name_map = {}
    for msg in messages:
        if msg["role"] == "assistant":
            content = msg.get("content", [])
            for part in content:
                if isinstance(part, dict) and part.get("type") == "tool_use":
                    tool_name = part.get("name")
                    if tool_name:
                        tool_name_map[part.get("id")] = tool_name
    # Clear old results (keep last KEEP_RECENT). Preserve read_file outputs because
    # they are reference material; compacting them forces the agent to re-read files.
    to_clear = tool_result[:-KEEP_RECENT]
    for _, _, result in to_clear:
        if (
            not isinstance(result.get("content"), str)
            or len(result.get("content")) < 100
        ):
            continue
        tool_id = result.get("tool_use_id", "")
        tool_name = result.get("tool_name", tool_name_map.get(tool_id, ""))
        if tool_name in PRESERVE_RESULT_TOOLS:
            continue
        result["content"] = f"[Previous: used {tool_name}]"
    return messages


# -- Layer 2: auto_compact - save transcript, summarize, replace messages --
def auto_compact(messages: list) -> list:
    # Save full transcript to disk
    TRANSCRIPT_DIR.mkdir(exist_ok=True)
    transcript_path = TRANSCRIPT_DIR / f"transcript_{int(time.time())}.json"
    with open(transcript_path, "w") as f:
        for msg in messages:
            f.write(json.dumps(msg, default=str) + "\n")

    # Ask LLM to summarize
    conversation_text = json.dumps(messages, default=str)[-80000:]
    response = client.messages.create(
        model=MODEL,
        messages=[
            {
                "role": "user",
                "content": f"请总结以下对话内容，包含：1.已完成什么 2.当前状态 3.关键决策\n\n{conversation_text}",
            },
        ],
        max_tokens=2000,
    )
    summary = next(
        (block.text for block in response.content if hasattr(block, "text")), ""
    )
    print(f"Summary: {summary}")
    if not summary:
        summary = "暂无总结。"

    # Replace old messages with summary
    return [
        {
            "role": "user",
            "content": f"对话总结：{summary}\n\n请基于此总结继续对话。",
        }
    ]


class TODO_MANAGER:
    def __init__(self):
        self.todo_list = []

    def update(self, todo_list: list) -> str:
        if len(todo_list) > 20:
            raise ValueError("最大todo数量不能超过20！")
        validated = []
        in_progress = 0
        for item in todo_list:
            status = item.get("status", "pending")
            if status not in ["pending", "progress", "completed"]:
                raise ValueError("状态非法")
            if status == "progress":
                in_progress += 1
            if in_progress > 1:
                raise ValueError("不能同时有两个进行中的任务！")
            validated.append(item)
        self.todo_list = validated
        return self.render()

    def render(self) -> str:
        lines = []
        completed_num = 0
        for item in self.todo_list:
            marker = {"pending": "[]", "progress": "[>]", "completed": "[✅]"}[
                item["status"]
            ]
            if item["status"] == "completed":
                completed_num += 1
            lines.append(f"{marker} {item['text']}")
        lines.append(f"{completed_num}/{len(self.todo_list)} completed!")
        return "\n".join(lines)


TODO = TODO_MANAGER()

PROJECT_DIR = Path(__file__).resolve().parent.parent
SKILLS_DIR = PROJECT_DIR / "skills"

MEMORY_DIR = PROJECT_DIR / "memory"
MEMORY_INDEX = MEMORY_DIR / "MEMORY.md"
MEMORY_TYPES = ("user", "feedback", "project", "reference")
MAX_INDEX_LINES = 200


class MemoryManager:
    def __init__(self):
        self.memory_dir = MEMORY_DIR
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

        for mem_type in MEMORY_TYPES:
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
        if mem_type not in MEMORY_TYPES:
            return f"Invalid memory type: {mem_type}. Must be one of {MEMORY_TYPES}"

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

        return f"Saved memory '{name}' [{mem_type}] to {file_path.relative_to(WORKDIR)}"

    def _rebuild_index(self):
        """Rebuild MEMORY.md from current in-memory state, capped at 200 lines."""
        lines = ["# memory Index", ""]
        for name, mem in self.memories.items():
            lines.append(f"- {name} ({mem['type']}): {mem['description']}")
            if len(lines) >= MAX_INDEX_LINES:
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


memory_mgr = MemoryManager()


class SKILL_REGISTRY:
    def __init__(self):
        self.skills = {}
        self.load_all()

    def load_all(self):
        if not SKILLS_DIR:
            return "没有找到skills目录"
        for f in SKILLS_DIR.rglob("SKILL.md"):
            text = f.read_text(encoding="utf-8")
            meta, body = self._parse_frontmatter(text)
            name = meta.get("name", f.parent.name)
            self.skills[name] = {"meta": meta, "body": body, "path": str(f)}

    def _parse_frontmatter(self, text: str) -> tuple:
        """Parse YAML frontmatter between --- delimiters."""
        match = re.match(r"^---\n(.*?)\n---\n(.*)", text, re.DOTALL)
        if not match:
            return {}, text
        try:
            meta = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError:
            meta = {}
        return meta, match.group(2).strip()

    def get_descriptions(self) -> str:
        """Layer 1: short descriptions for the system prompt."""
        if not self.skills:
            return "(no skills available)"
        lines = []
        for name, skill in self.skills.items():
            desc = skill["meta"].get("description", "No description")
            tags = skill["meta"].get("tags", "")
            line = f"  - {name}: {desc}"
            if tags:
                line += f" [{tags}]"
            lines.append(line)
        return "\n".join(lines)

    def get_content(self, name: str) -> str:
        """Layer 2: full skill body returned in tool_result."""
        skill = self.skills.get(name)
        if not skill:
            return f"Error: Unknown skill '{name}'. Available: {', '.join(self.skills.keys())}"
        return f"<skill name=\"{name}\">\n{skill['body']}\n</skill>"


SKILL_LOADER = SKILL_REGISTRY()
SYSTEM = f"""You are a coding agent at {PROJECT_DIR}.
Use load_skill to access specialized knowledge before tackling unfamiliar topics.

Skills available:
{SKILL_LOADER.get_descriptions()}"""

MODES = ["auto", "plan", "default"]

READ_ONLY_TOOLS = {"read_file", "bash_readonly"}

# Tools that modify state
WRITE_TOOLS = {"write_file", "edit_file", "bash"}


# -- Bash security validation --
class BashSecurityValidator:
    """
    Validate bash commands for obviously dangerous patterns.

    The teaching version deliberately keeps this small and easy to read.
    First catch a few high-risk patterns, then let the permission pipeline
    decide whether to deny or ask the user.
    """

    VALIDATORS = [
        ("shell_metachar", r"[;&|`$]"),  # shell metacharacters
        ("sudo", r"\bsudo\b"),  # privilege escalation
        ("rm_rf", r"\brm\s+(-[a-zA-Z]*)?r"),  # recursive delete
        ("cmd_substitution", r"\$\("),  # command substitution
        ("ifs_injection", r"\bIFS\s*="),  # IFS manipulation
    ]

    def validate(self, command: str) -> list:
        """
        Check a bash command against all validators.

        Returns list of (validator_name, matched_pattern) tuples for failures.
        An empty list means the command passed all validators.
        """
        failures = []
        for name, pattern in self.VALIDATORS:
            if re.search(pattern, command):
                failures.append((name, pattern))
        return failures

    def is_safe(self, command: str) -> bool:
        """Convenience: returns True only if no validators triggered."""
        return len(self.validate(command)) == 0

    def describe_failures(self, command: str) -> str:
        """Human-readable summary of validation failures."""
        failures = self.validate(command)
        if not failures:
            return "No security issues detected."
        parts = [f"{name}(pattern:{pattern})" for name, pattern in failures]
        return "Security flags: " + ", ".join(parts)


# Singleton validator instance used by the permission pipeline
bash_validator = BashSecurityValidator()

# -- Permission rules --
# Rules are checked in order: first match wins.
# Format: {"tool": "<tool_name_or_*>", "path": "<glob_or_*>", "behavior": "allow|deny|ask"}
DEFAULT_RULES = [
    # Always deny dangerous patterns
    {"tool": "bash", "content": "rm -rf /", "behavior": "deny"},
    {"tool": "bash", "content": "sudo *", "behavior": "deny"},
    # Allow reading anything
    {"tool": "read_file", "path": "*", "behavior": "allow"},
]


class PermissionManager:
    """
    Manages permission decisions for tool calls.

    Pipeline: deny_rules -> mode_check -> allow_rules -> ask_user

    The teaching version keeps the decision path short on purpose so readers
    can implement it themselves before adding more advanced policy layers.
    """

    def __init__(self, mode: str = "default", rules: list = None):
        if mode not in MODES:
            raise ValueError(f"Invalid mode: {mode}")
        self.mode = mode
        self.rules = rules or list(DEFAULT_RULES)
        # Simple denial tracking helps surface when the agent is repeatedly
        # asking for actions the system will not allow.
        self.consecutive_denials = 0
        self.max_consecutive_denials = 3

    def check(self, tool_name: str, tool_input: dict) -> dict:
        """
        Returns: {"behavior": "allow"|"deny"|"ask", "reason": str}
        """
        # Step 0: Bash security validation (before deny rules)
        # Teaching version checks early for clarity.
        if tool_name == "bash":
            command = tool_input.get("command", "")
            failures = bash_validator.validate(command)
            if failures:
                serve = {"sudo", "rm -rf"}
                serve_hits = [f for f in failures if f[0] in serve]
                if serve_hits:
                    return {
                        "behavior": "deny",
                        "reason": "Security violation: "
                        + ", ".join([f[1] for f in serve_hits]),
                    }
                desc = bash_validator.describe_failures(command)
                return {"behavior": "ask", "reason": "Bash validation failed: " + desc}
        # Step 1: Deny rules (bypass-immune, checked first always)
        for rule in self.rules:
            if rule.get("behavior") != "deny":
                continue
            if self._matches(rule, tool_name, tool_input):
                return {"behavior": "deny", "reason": f"Blocked by deny rule: {rule}"}

        # Step 2: Mode-based decisions
        if self.mode == "plan":
            # Plan mode: deny all write operations, allow reads
            if tool_name in WRITE_TOOLS:
                return {
                    "behavior": "deny",
                    "reason": "Plan mode: write operations not allowed",
                }
            return {"behavior": "allow", "reason": "Plan mode: read operations allowed"}

        if self.mode == "auto":
            # Auto mode: auto-allow read-only tools, ask for writes
            if tool_name in READ_ONLY_TOOLS or tool_name == "read_files":
                return {
                    "behavior": "allow",
                    "reason": "Auto mode: read-only tools allowed",
                }
            pass
        # Step 3: Allow rules
        for rule in self.rules:
            if rule.get("behavior") != "allow":
                continue
            if self._matches(rule, tool_name, tool_input):
                return {"behavior": "allow", "reason": f"Allowed by allow rule: {rule}"}
        # Step 4: Ask user (default behavior for unmatched tools)
        return {
            "behavior": "ask",
            "reason": f"No matching rule found for {tool_name}, ask user",
        }

    def ask_user(self, tool_name: str, tool_input: dict) -> bool:
        """Interactive approval prompt. Returns True if approved."""
        preview = json.dumps(tool_input, ensure_ascii=False)[:200]
        print(f"\n [Permission] {tool_name}: {preview}")
        try:
            answer = input("  Allow? (y/n/always): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False

        if answer == "always":
            self.rules.append({"tool": tool_name, "path": "*", "behavior": "allow"})
            self.consecutive_denials = 0
            return True
        elif answer in ("y", "yes"):
            self.consecutive_denials = 0
            return True

        self.consecutive_denials += 1
        if self.consecutive_denials >= self.max_consecutive_denials:
            print(
                f"  [{self.consecutive_denials} consecutive denials -- "
                "consider switching to plan mode]"
            )
            self.mode = "plan"
        return False

    def _matches(self, rule: dict, tool_name: str, tool_input: dict) -> bool:
        """Check if a rule matches the tool call."""
        # Tool name match
        if rule.get("tool") and rule["tool"] != "*":
            if rule["tool"] != tool_name:
                return False
        # Path pattern match
        if "path" in rule and rule["path"] != "*":
            path = tool_input.get("path", "")
            if not fnmatch(path, rule["path"]):
                return False
        # Content pattern match (for bash commands)
        if "content" in rule:
            command = tool_input.get("command", "")
            if not fnmatch(command, rule["content"]):
                return False
        return True


SUB_TOOL_HANDLERS = {
    "calculator": lambda **kw: calculator(kw["expression"]),
    "get_current_time": lambda: get_current_time(),
    "TODO": lambda **kw: TODO.update(kw["todo_list"]),
    "read_file": lambda **kw: run_read(kw["path"], kw.get("limit")),
}
SUB_TOOLS = [
    {
        "name": "calculator",
        "description": "一种安全的方式来计算数学表达式。",
        "input_schema": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "要计算的数学表达式，如 '2+3*4'",
                }
            },
            "required": ["expression"],  # ✅ 与 type、properties 平级
        },
    },
    {
        "name": "get_current_time",
        "description": "一个返回当前时间的工具",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],  # ✅ 是 required，不是 requires
        },
    },
    {
        "name": "TODO",
        "description": "一个待办任务列表，列出完成prompt任务需要的执行步骤",
        "input_schema": {
            "type": "object",
            "properties": {
                "todo_list": {
                    "type": "array",
                    "items": {  # ✅ 数组用 items 定义子元素结构
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "text": {"type": "string"},
                            "status": {
                                "type": "string",
                                "enum": ["pending", "progress", "completed"],
                            },
                        },
                        "required": ["id", "text", "status"],
                    },
                }
            },
            "required": ["todo_list"],  # ✅ 与 type、properties 平级
        },
    },
    {
        "name": "read_file",
        "description": "Read file contents.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["path"],
        },
    },
]

TOOL_HANDLERS = {
    "bash": lambda **kw: run_bash(kw["command"]),
    "task": lambda **kw: sub_agent(prompt=kw["prompt"]),
    "TODO": lambda **kw: TODO.update(kw["todo_list"]),
    "skill": lambda **kw: SKILL_LOADER.get_content(kw["name"]),
    "read_file": lambda **kw: run_read(kw["path"], kw.get("limit")),
    "compact": lambda **kw: "Manual compression requested.",
    "save_memory": lambda **kw: memory_mgr.save_memory(
        kw["name"], kw["description"], kw["mem_type"], kw["content"]
    ),
}

TOOLS = [
    {
        "name": "bash",
        "description": "Run a shell command.",
        "input_schema": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    },
    {
        "name": "task",
        "description": "一个子agent,可以用来执行具体的任务,帮助父agent净化上下文",
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "给子agent执行任务的提示词模板",
                }
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "TODO",
        "description": "一个待办任务列表，列出完成prompt任务需要的执行步骤",
        "input_schema": {
            "type": "object",
            "properties": {
                "todo_list": {
                    "type": "array",
                    "items": {  # ✅ 数组用 items 定义子元素结构
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "text": {"type": "string"},
                            "status": {
                                "type": "string",
                                "enum": ["pending", "progress", "completed"],
                            },
                        },
                        "required": ["id", "text", "status"],
                    },
                }
            },
            "required": ["todo_list"],  # ✅ 与 type、properties 平级
        },
    },
    {
        "name": "skill",
        "description": "获取技能详情",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "技能名称",
                }
            },
            "required": ["name"],
        },
    },
    {
        "name": "read_file",
        "description": "Read file contents.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["path"],
        },
    },
    {
        "name": "compact",
        "description": "compress messages.",
        "input_schema": {
            "type": "object",
            "properties": {
                "focus": {
                    "type": "string",
                    "description": "What to preserve in the summary",
                }
            },
        },
    },
    {
        "name": "save_memory",
        "description": "保存跨会话仍然有价值的非显示信息",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "记忆名称"},
                "description": {
                    "type": "string",
                    "description": "关于本段记忆的简要描述",
                },
                "mem_type": {
                    "type": "string",
                    "enum": ["user", "feedback", "project", "reference"],
                    "description": "user=preferences, feedback=corrections, project=non-obvious project conventions or decision reasons, reference=external resource pointers",
                },
                "content": {"type": "string", "description": "记忆详细内容"},
            },
        },
    },
]


def printToolName(payload: dict):
    print(f"Tool name: {payload["tool_name"]}")
    return {"exitCode": 0, "message": "tool name printed"}


def printToolOutput(payload: dict):
    print(f"Tool output: {payload["tool_output"]}")
    return {"exitCode": 0, "message": "tool output printed"}


HOOKS = {
    "SessionStart": [printToolName],
    "PreToolUse": [printToolName],
    "PostToolUse": [printToolOutput],
}


def run_hooks(event: str, payload: dict):
    if event in HOOKS:
        for handler in HOOKS[event]:
            result = handler(payload)
            if result.get("exitCode", 1) in (1, 2):
                return result
    return {"exitCode": 0, "message": "no hooks or all passed"}


def sub_agent(prompt: str):
    sub_messages = [{"role": "user", "content": prompt}]
    for _ in range(30):
        response = client.messages.create(
            model=MODEL,
            messages=sub_messages,
            system=SUB_SYSTEM,
            tools=SUB_TOOLS,
            max_tokens=8000,
        )
        sub_messages.append({"role": "assistant", "content": response.content})
        result = []
        for block in response.content:
            if block.type == "tool_use":
                handler = SUB_TOOL_HANDLERS.get(block.name)
                if handler:
                    result.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": str(handler(**block.input))[:5000],
                        }
                    )
                else:
                    return f"错误：子代理遇到了未知工具 '{block.name}'，无法执行。"
        if result:
            sub_messages.append({"role": "user", "content": result})
            continue
        if response.stop_reason == "tool_use":
            return "错误：子代理请求使用工具，但没有可用的工具处理程序。"
        return (
            "".join(b.text for b in response.content if hasattr(b, "text"))
            or "no summary"
        )
    return "错误：子代理执行轮次超限。"


def tool_handler(block: dict, state: dict) -> str:
    handler = TOOL_HANDLERS.get(block.name)
    try:
        output = handler(**block.input) if handler else f"未知的工具{block.name}"
    except Exception as e:
        output = f"error:{e}"
    print(f"> {block.name}:")
    print(str(output)[:200])
    if block.name == "TODO":
        state["use_todo"] = True
    else:
        state["use_todo"] = False
    state["round_since_todo"] = (
        0 if state["use_todo"] else state["round_since_todo"] + 1
    )
    return str(output)


MEMORY_GUIDANCE = """
When to save memories:
- User states a preference ("I like tabs", "always use pytest") -> type: user
- User corrects you ("don't do X", "that was wrong because...") -> type: feedback
- You learn a project fact that is not easy to infer from current code alone
  (for example: a rule exists because of compliance, or a legacy module must
  stay untouched for business reasons) -> type: project
- You learn where an external resource lives (ticket board, dashboard, docs URL)
  -> type: reference

When NOT to save:
- Anything easily derivable from code (function signatures, file structure, directory layout)
- Temporary task state (current branch, open PR numbers, current TODOs)
- Secrets or credentials (API keys, passwords)
"""


def build_system_prompt() -> str:
    """Assemble system prompt with memory content included."""
    parts = [f"{SYSTEM}"]

    # Inject memory content if available
    memory_section = memory_mgr.load_memory_prompt()
    if memory_section:
        parts.append(memory_section)

    parts.append(MEMORY_GUIDANCE)
    return "\n\n".join(parts)


# -- Agent loop with nag reminder injection --
def agent_loop(messages: list, perms: PermissionManager):
    state = {
        "use_todo": False,
        "round_since_todo": 0,
    }
    while True:
        system = build_system_prompt()
        micro_compact(messages)
        if estimate_tokens(messages) > THRESHOLD:
            print("[auto_compact triggered]")
            messages[:] = auto_compact(messages)
        response = client.messages.create(
            model=MODEL,
            messages=messages,
            system=system,
            tools=TOOLS,
            max_tokens=8000,
        )
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            return
        results = []
        manual_compact = False
        for block in response.content:
            if block.type == "tool_use":

                # PreToolUse hook
                hook_result = run_hooks(
                    "PreToolUse", {"tool_name": block.name, "tool_input": block.input}
                )
                if hook_result["exitCode"] == 1:
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": str(hook_result["message"]),
                        }
                    )
                    continue
                if hook_result["exitCode"] == 2:
                    messages.append({"role": "user", "content": hook_result["message"]})

                if block.name == "compact":
                    manual_compact = True
                    print("压缩中·······")
                else:
                    # -- Permission check --
                    decision = perms.check(block.name, block.input or {})
                    if decision["behavior"] == "deny":
                        output = f"  [Permission] {block.name} denied"
                    elif decision["behavior"] == "ask":
                        if perms.ask_user(block.name, block.input or {}):
                            output = tool_handler(block, state)
                        else:
                            output = f"Permission denied by user for {block.name}"
                            print(f"  [USER DENIED] {block.name}")
                    else:
                        output = tool_handler(block, state)
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": str(output),
                        }
                    )

                    # PostToolUse hook
                    hook_result = run_hooks(
                        "PostToolUse",
                        {
                            "tool_name": block.name,
                            "tool_input": block.input,
                            "tool_output": str(output),
                        },
                    )
                    if hook_result["exitCode"] == 2:
                        messages.append(
                            {"role": "user", "content": hook_result["message"]}
                        )

                if state["round_since_todo"] >= 3:
                    results.append({"type": "text", "text": "更新你的todo list"})
        messages.append({"role": "user", "content": results})
        if manual_compact:
            print("[manual compact]")
            messages[:] = auto_compact(messages)
            return


if __name__ == "__main__":
    # Load existing memories at session start
    memory_mgr.load_all()
    mem_count = len(memory_mgr.memories)
    if mem_count:
        print(f"[{mem_count} memories loaded into context]")
    else:
        print("[No existing memories. The agent can create them with save_memory.]")

    # Fire SessionStart hooks
    run_hooks("SessionStart", {"tool_name": "", "tool_input": {}})

    # Choose permission mode at startup
    print("Permission modes: default, plan, auto")
    mode_input = input("Mode (default): ").strip().lower() or "default"
    if mode_input not in MODES:
        mode_input = "default"

    perms = PermissionManager(mode=mode_input)
    print(f"[Permission mode: {mode_input}]")

    history = []
    while True:
        try:
            query = input("\033[35ms09 Memory System>>> \033[0m")
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break
        if query.strip().lower() in ["exit", "quit", "q"]:
            print("\nGoodbye!")
            break

        if query.startswith("/mode"):
            parts = query.split()
            if len(parts) == 2 and parts[1] in MODES:
                perms.mode = parts[1]
                print(f"[Switched to {parts[1]} mode]")
            else:
                print(f"Usage: /mode <{'|'.join(MODES)}>")
            continue

        # /rules command to show current rules
        if query.strip() == "/rules":
            for i, rule in enumerate(perms.rules):
                print(f"  {i}: {rule}")
            continue

        history.append({"role": "user", "content": query})
        agent_loop(history, perms)
        response_content = history[-1]["content"]
        if isinstance(response_content, list):
            for block in response_content:
                if hasattr(block, "text"):
                    print(f"{block.text}")
        print()
