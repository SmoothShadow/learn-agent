# tools.py
import subprocess
import math
from collections.abc import Callable
from pathlib import Path
from todo_manager import TODO_MANAGER
from Skill import SKILL_REGISTRY
from MemoryManager import MemoryManager
from TaskManager import TaskManager
from BackgroundManager import BackgroundManager

WORKDIR = Path.cwd()


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
    {
        "name": "create_tasks",
        "description": "创建一个包含依赖关系的较长任务列表，任务进度会写到文件中，防止程序意外退出后丢失进度",
        "input_schema": {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "subject": {"type": "string", "description": "任务主题"},
                            "description": {
                                "type": "string",
                                "description": "任务描述",
                            },
                            "owner": {"type": "string", "description": "分配给谁执行"},
                            "status": {
                                "type": "string",
                                "enum": [
                                    "pending",
                                    "in_progress",
                                    "completed",
                                    "deleted",
                                ],
                                "description": "当前状态",
                            },
                            "depends_on_subjects": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "依赖的任务主题列表",
                            },
                        },
                        "required": ["subject", "description", "owner", "status"],
                    },
                },
            },
            "required": ["tasks"],
        },
    },
    {
        "name": "update_task",
        "description": "更新文件夹中的任务列表的状态",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer", "description": "任务id"},
                "status": {
                    "type": "string",
                    "enum": ["pending", "in_progress", "completed", "deleted"],
                    "description": "任务状态",
                },
            },
            "required": ["task_id", "status"],
        },
    },
    {
        "name": "get_task",
        "description": "用任务id获取某个任务",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer", "description": "任务id"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "load_all",
        "description": "加载所有任务",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "clear_all",
        "description": "任务全部完成时，清空所有任务json文件",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "is_ready",
        "description": "根据任务id判断该任务是否可以开始",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer", "description": "任务id"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "run_background",
        "description": "在后台运行命令",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要运行的命令"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "get_notifications",
        "description": "获取后台任务的通知",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "read_result",
        "description": "读取后台任务的完整结果",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "任务id"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "read_task",
        "description": "读取后台任务的状态",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "任务id"},
            },
            "required": ["task_id"],
        },
    },
]

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


def build_tool_handlers(
    sub_agent: Callable[[str], str],
    TODO: TODO_MANAGER,
    skill_loader: SKILL_REGISTRY,
    memory_mgr: MemoryManager,
    task_manager: TaskManager,
    background_manager: BackgroundManager,
) -> dict[str, Callable[..., str]]:
    return {
        "bash": lambda **kw: run_bash(kw["command"]),
        "task": lambda **kw: sub_agent(prompt=kw["prompt"]),
        "TODO": lambda **kw: TODO.update(kw["todo_list"]),
        "skill": lambda **kw: skill_loader.get_content(kw["name"]),
        "read_file": lambda **kw: run_read(kw["path"], kw.get("limit")),
        "compact": lambda **kw: "Manual compression requested.",
        "save_memory": lambda **kw: memory_mgr.save_memory(
            kw["name"], kw["description"], kw["mem_type"], kw["content"]
        ),
        "create_tasks": lambda **kw: task_manager.create_tasks(kw["tasks"]),
        "update_task": lambda **kw: task_manager.update_task(
            kw["task_id"], kw["status"]
        ),
        "get_task": lambda **kw: task_manager.get_task(kw["task_id"]),
        "load_all": lambda **kw: task_manager.load_all(),
        "clear_all": lambda **kw: task_manager.clear_all(),
        "is_ready": lambda **kw: task_manager.is_ready(kw["task_id"]),
        "run_background": lambda **kw: background_manager.run(kw["command"]),
        "get_notifications": lambda **kw: background_manager.get_notifications(),
        "read_result": lambda **kw: background_manager.read_result(kw["task_id"]),
        "read_task": lambda **kw: background_manager.read_task(kw["task_id"]),
    }


def build_sub_tool_handlers(TODO: TODO_MANAGER) -> dict[str, Callable[..., str]]:
    return {
        "calculator": lambda **kw: calculator(kw["expression"]),
        "get_current_time": lambda: get_current_time(),
        "TODO": lambda **kw: TODO.update(kw["todo_list"]),
        "read_file": lambda **kw: run_read(kw["path"], kw.get("limit")),
    }


def safe_path(p: str) -> Path:
    path = (WORKDIR / p).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {p}")
    return path


def run_bash(command: str) -> str:
    try:
        r = subprocess.run(
            command,
            shell=True,
            cwd=WORKDIR,
            capture_output=True,
            text=True,
            timeout=120,
        )
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"


def calculator(expression: str) -> str:
    """安全地计算数学表达式。"""
    try:
        # 只允许安全函数
        allowed_names = {
            "abs": abs,
            "round": round,
            "min": min,
            "max": max,
            "pow": pow,
            "sqrt": math.sqrt,
        }
        result = eval(expression, {"__builtins__": {}}, allowed_names)
        return str(result)
    except Exception as e:
        return f"计算错误: {e}"


def get_current_time() -> str:
    """返回当前时间。"""
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def run_read(path: str, limit: int = None) -> str:
    try:
        lines = safe_path(path).read_text().splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more)"]
        return "\n".join(lines)[:50000]
    except Exception as e:
        return f"Error: {e}"
