#!/usr/bin/env python3
# Harness: the loop -- the model's first connection to the real world.

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

from tools import calculator, get_current_time

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


WORK_DIR = Path(__file__).resolve().parent
SKILLS_DIR = WORK_DIR / "skills"


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
SYSTEM = f"""You are a coding agent at {WORK_DIR}.
Use load_skill to access specialized knowledge before tackling unfamiliar topics.

Skills available:
{SKILL_LOADER.get_descriptions()}"""

print("=== SKILLS ===")
print(SKILL_LOADER.get_descriptions())
print("==============")

SUB_TOOL_HANDLERS = {
    "calculator": lambda **kw: calculator(kw["expression"]),
    "get_current_time": lambda: get_current_time(),
    "TODO": lambda **kw: TODO.update(kw["todo_list"]),
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
]

TOOL_HANDLERS = {
    "task": lambda **kw: sub_agent(prompt=kw["prompt"]),
    "TODO": lambda **kw: TODO.update(kw["todo_list"]),
    "skill": lambda **kw: SKILL_LOADER.get_content(kw["name"]),
}

TOOLS = [
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
]


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
        print("DEBUG: response.stop_reason =", response.stop_reason)  # 新增1
        for block in response.content:
            print(f"DEBUG: block.type = {block.type}")  # 新增2
            if block.type == "tool_use":
                print(f"DEBUG: 子代理想调用的工具名 = {repr(block.name)}")  # 新增3
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


# -- Agent loop with nag reminder injection --
def agent_loop(messages: list):
    round_since_todo = 0
    while True:
        response = client.messages.create(
            model=MODEL,
            messages=messages,
            system=SYSTEM,
            tools=TOOLS,
            max_tokens=8000,
        )
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            return
        results = []
        use_todo = False
        for block in response.content:
            if block.type == "tool_use":
                handler = TOOL_HANDLERS.get(block.name)
                try:
                    output = (
                        handler(**block.input) if handler else f"未知的工具{block.name}"
                    )
                except Exception as e:
                    output = f"error:{e}"
                print(f"> {block.name}:")
                print(str(output)[:200])
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": str(output),
                    }
                )
                if block.name == "TODO":
                    use_todo = True
                round_since_todo = 0 if use_todo else round_since_todo + 1
                if round_since_todo >= 3:
                    results.append({"type": "text", "text": "更新你的todo list"})
        messages.append({"role": "user", "content": results})


if __name__ == "__main__":
    history = []
    while True:
        try:
            query = input("\033[35ms05>>> \033[0m")
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break
        if query.strip().lower() in ["exit", "quit", "q"]:
            print("\nGoodbye!")
            break
        history.append({"role": "user", "content": query})
        agent_loop(history)
        response_content = history[-1]["content"]
        if isinstance(response_content, list):
            for block in response_content:
                if hasattr(block, "text"):
                    print(f"{block.text}")
        print()
