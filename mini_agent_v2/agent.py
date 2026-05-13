#!/usr/bin/env python3
# Harness: the loop -- the model's first connection to the real world.

import os
from re import sub
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
你是一个父agent，负责理解用户意图并把任务委派给子agent。

  规则：
  1. 当用户要求制定计划、清单、步骤拆解时，必须调用 task 工具，不要直接回答。
  2. 当用户要求计算、获取时间等具体执行任务时，必须调用 task 工具，不要直接回答。
  3. 只有在闲聊或解释性问答时，才可以直接文本回复。
"""
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
        for block in response.content:
            if block.type == "tool_use":
                handler = SUB_TOOL_HANDLERS.get(block.name)
                if handler:
                    result.append(
                        {
                            "type": "tool_result",
                            "tool_result_id": block.id,
                            "content": str(handler(**block.input))[:5000],
                        }
                    )
        sub_messages.append({"role": "user", "content": result})
    return (
        "".join(b.text for b in response.content if hasattr(b, "text")) or "no summary"
    )


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
