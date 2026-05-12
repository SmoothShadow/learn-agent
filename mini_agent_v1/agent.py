#!/usr/bin/env python3
# Harness: the loop -- the model's first connection to the real world.

import os
from tools import calculator, get_current_time
from dotenv import load_dotenv
from anthropic import Anthropic

try:
    import readline

    readline.parse_and_bind("set bind-tty-special-chars off")
    readline.parse_and_bind("set input-meta on")
    readline.parse_and_bind("set output-meta on")
    readline.parse_and_bind("set convert-meta off")
    readline.parse_and_bind("set enable-meta-keybindings on")
except ImportError:
    print("Module readline not available.")

load_dotenv(override=True)

if not os.getenv("ANTHROPIC_API_KEY"):
    os.environ.pop("ANTHROPIC_API_TOKEN", None)

client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.environ["MODEL_ID"]

SYSTEM = """
你是一个擅长制定计划的助手。当用户要求你"列计划"、"制定表格"或"创建清单"时，你必须：
1. 使用 TODO 工具把计划结构化地记录下来（action=add）
2. 然后告诉用户计划已经创建好了

不要直接在文本中输出计划内容，必须通过 TODO 工具来完成。
"""


class TODO_MANAGER:
    def __init__(self):
        self.todo_list = []

    def update(self, items: list) -> str:
        if len(items) > 20:
            raise ValueError("最大todo数量不能超过20！")
        validated = []
        in_progress = 0
        for item in items:
            status = item.get("status", "pending")
            if status not in ["pending", "progress", "completed"]:
                raise ValueError("状态非法")
            if status == "progress":
                in_progress += 1
            if in_progress > 1:
                raise ValueError("不能同时有两个进行中的任务！")
            self.todo_list = validated
        self.render(validated)

    def render(self) -> str:
        lines = []
        completed_num = 0
        for item in self.todo_list:
            status = {"pending": "[]", "progress": "[>]", "completed": "[✅]"}[
                item["status"]
            ]
            if status == "completed":
                completed_num += 1
            lines.append(f"{status} {item.text}")
        lines.append(f"{completed_num/len(self.todo_list)} completed!")
        return "\n".join(lines)


TODO = TODO_MANAGER()

TOOL_HANDLERS = {
    "calculator": lambda **kw: calculator(kw["expression"]),
    "get_current_time": lambda: get_current_time(),
    "TODO": lambda **kw: TODO.update(kw["items"]),
}
TOOLS = [
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
                if block.name == "todo":
                    use_todo = True
                round_since_todo = 0 if use_todo else round_since_todo + 1
                if round_since_todo >= 3:
                    results.append({"type": "text", "text": "更新你的todo list"})
        messages.append({"role": "user", "content": results})


if __name__ == "__main__":
    history = []
    while True:
        try:
            query = input("\033[35ms01-s03>>> \033[0m")
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
