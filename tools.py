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
from CronScheduler import CronScheduler
from MessageBus import MessageBus
from TeammateManager import TeammateManager
from AgreementStore import AgreementStore
from ClaimablePredicate import ClaimablePredicate
from WorkTreesIsolation import WorkTreesIsolation

WORKDIR = Path.cwd()


TOOLS = [
    {
        "name": "bash",
        "description": "Run a shell command.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
            },
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
        "name": "write_file",
        "description": "Write file contents.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": "Edit file contents.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_text": {"type": "string"},
                "new_text": {"type": "string"},
            },
            "required": ["path", "old_text", "new_text"],
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
                            "allowed_roles": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "允许哪些角色接取这个任务",
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
                "name": {
                    "type": "string",
                    "description": "执行此操作的成员名称",
                },
            },
            "required": ["task_id", "status", "name"],
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
    {
        "name": "create_schedule",
        "description": "创建定时任务",
        "input_schema": {
            "type": "object",
            "properties": {
                "cron_expr": {
                    "type": "string",
                    "description": "cron表达式，如 '0 0 * * *'",
                },
                "prompt": {"type": "string", "description": "任务提示词"},
                "recurring": {"type": "boolean", "description": "是否重复"},
            },
            "required": ["cron_expr", "prompt"],
        },
    },
    {
        "name": "get_queue",
        "description": "获取定时任务队列",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "spawn_teammate",
        "description": "创建团队成员",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "成员名称"},
                "role": {"type": "string", "description": "成员角色"},
                "prompt": {"type": "string", "description": "成员提示词"},
            },
            "required": ["name", "role", "prompt"],
        },
    },
    {
        "name": "send_message",
        "description": "向指定成员发送消息",
        "input_schema": {
            "type": "object",
            "properties": {
                "sender": {"type": "string", "description": "发送者名称"},
                "to": {"type": "string", "description": "接收者名称"},
                "content": {"type": "string", "description": "消息内容"},
                "msg_type": {
                    "type": "string",
                    "enum": [
                        "message",
                        "broadcast",
                        "shutdown_request",
                        "shutdown_response",
                        "plan_approval",
                        "plan_approval_response",
                    ],
                    "description": "消息类型",
                },
                "extra": {"type": "object", "description": "额外信息"},
            },
            "required": ["sender", "to", "content", "msg_type"],
        },
    },
    {
        "name": "broadcast_teammates",
        "description": "向所有成员广播消息",
        "input_schema": {
            "type": "object",
            "properties": {
                "sender": {"type": "string", "description": "发送者名称"},
                "content": {"type": "string", "description": "消息内容"},
                "teammates": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "接收者名称列表",
                },
            },
            "required": ["sender", "content", "teammates"],
        },
    },
    {
        "name": "list_teammates",
        "description": "列出团队成员",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "member_names",
        "description": "获取团队成员名称列表",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "request_shutdown",
        "description": "请求子线程优雅的关闭",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "目标线程角色名称"},
            },
            "required": ["target"],
        },
    },
    {
        "name": "response_shutdown",
        "description": "子线程对lead的关闭请求进行响应",
        "input_schema": {
            "type": "object",
            "properties": {
                "request_id": {"type": "string", "description": "请求id"},
                "approve": {"type": "boolean", "description": "请求是否同意"},
                "origin": {"type": "string", "description": "被请求的线程的角色名称"},
                "response": {"type": "string", "description": "详细的响应内容"},
            },
            "required": ["request_id", "approve", "origin", "response"],
        },
    },
    {
        "name": "request_plan",
        "description": "向lead请求执行一些高危计划",
        "input_schema": {
            "type": "object",
            "properties": {
                "origin": {"type": "string", "description": "请求发起者角色名称"},
                "plan": {"type": "string", "description": "计划内容"},
            },
            "required": ["origin", "plan"],
        },
    },
    {
        "name": "response_plan",
        "description": "lead对子线程的计划请求进行响应",
        "input_schema": {
            "type": "object",
            "properties": {
                "request_id": {"type": "string", "description": "请求id"},
                "approve": {"type": "boolean", "description": "请求是否同意"},
                "target": {"type": "string", "description": "发起请求的线程的角色名称"},
                "response": {"type": "string", "description": "详细的响应内容"},
            },
            "required": ["request_id", "approve", "target", "response"],
        },
    },
    {
        "name": "get_request",
        "description": "根据请求id获取团队请求",
        "input_schema": {
            "type": "object",
            "properties": {
                "request_id": {"type": "string", "description": "请求ID"},
            },
            "required": ["request_id"],
        },
    },
    {
        "name": "create_worktree",
        "description": "创建一个隔离worktree环境来执行任务",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "worktree名称"},
                "task_id": {"type": "string", "description": "任务ID"},
            },
            "required": ["name", "task_id"],
        },
    },
    {
        "name": "run_bash",
        "description": "执行任务时在worktree隔离环境下执行bash指令",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "array",
                    "description": "bash指令数组",
                    "items": {"type": "string", "description": "command指令参数"},
                },
                "task_id": {"type": "string", "description": "任务ID"},
            },
            "required": ["command", "task_id"],
        },
    },
    {
        "name": "closeout_worktree",
        "description": "任务完成时删除worktree或保留以便review",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "任务ID"},
                "action": {
                    "type": "string",
                    "enum": ["keep", "remove"],
                    "description": "保留或关闭worktree",
                },
                "reason": {
                    "type": "string",
                    "description": "选择某个action的依据或理由",
                },
            },
        },
    },
]

SUB_TOOLS = [
    {
        "name": "bash",
        "description": "Run a shell command.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
            },
            "required": ["command"],
        },
    },
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
    {
        "name": "write_file",
        "description": "Write file contents.",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": "Edit file contents.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_text": {"type": "string"},
                "new_text": {"type": "string"},
            },
            "required": ["path", "old_text", "new_text"],
        },
    },
    {
        "name": "send_message",
        "description": "向指定成员发送消息",
        "input_schema": {
            "type": "object",
            "properties": {
                "sender": {"type": "string", "description": "发送者名称"},
                "to": {"type": "string", "description": "接收者名称"},
                "content": {"type": "string", "description": "消息内容"},
                "msg_type": {
                    "type": "string",
                    "enum": [
                        "message",
                        "broadcast",
                        "shutdown_request",
                        "shutdown_response",
                        "plan_approval",
                        "plan_approval_response",
                    ],
                    "description": "消息类型",
                },
                "extra": {"type": "object", "description": "额外信息"},
            },
            "required": ["sender", "to", "content", "msg_type"],
        },
    },
    {
        "name": "broadcast_teammates",
        "description": "向所有成员广播消息",
        "input_schema": {
            "type": "object",
            "properties": {
                "sender": {"type": "string", "description": "发送者名称"},
                "content": {"type": "string", "description": "消息内容"},
                "teammates": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "接收者名称列表",
                },
            },
            "required": ["sender", "content", "teammates"],
        },
    },
    {
        "name": "list_teammates",
        "description": "列出团队成员",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "member_names",
        "description": "获取团队成员名称列表",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "response_shutdown",
        "description": "子线程对lead的关闭请求进行响应",
        "input_schema": {
            "type": "object",
            "properties": {
                "request_id": {"type": "string", "description": "请求id"},
                "approve": {"type": "boolean", "description": "请求是否同意"},
                "origin": {"type": "string", "description": "被请求的线程的角色名称"},
                "response": {"type": "string", "description": "详细的响应内容"},
            },
            "required": ["request_id", "approve", "origin", "response"],
        },
    },
    {
        "name": "request_plan",
        "description": "向lead请求执行一些高危计划",
        "input_schema": {
            "type": "object",
            "properties": {
                "origin": {"type": "string", "description": "请求发起者角色名称"},
                "plan": {"type": "string", "description": "计划内容"},
            },
            "required": ["origin", "plan"],
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
        "name": "claim_task",
        "description": "子代理根据自己的角色主动接取一个可接取和可执行的任务",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "当前成员名称"},
                "role": {"type": "string", "description": "当前成员角色"},
            },
            "required": ["name", "role"],
        },
    },
    {
        "name": "update_claim_record",
        "description": "更新任务接取记录",
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "object",
                    "description": "任务对象",
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
                        "allowed_roles": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "允许哪些角色接取这个任务",
                        },
                    },
                },
                "name": {"type": "string", "description": "当前角色名称"},
                "status": {"type": "string", "description": "领取状态"},
                "claim_source": {
                    "type": "string",
                    "enum": ["auto", "manual"],
                    "description": "接取方式",
                },
            },
            "required": ["task", "name", "status", "claim_source"],
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
                "name": {
                    "type": "string",
                    "description": "执行此操作的成员名称",
                },
            },
            "required": ["task_id", "status", "name"],
        },
    },
    {
        "name": "create_worktree",
        "description": "创建一个隔离worktree环境来执行任务",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "worktree名称"},
                "task_id": {"type": "string", "description": "任务ID"},
            },
            "required": ["name", "task_id"],
        },
    },
    {
        "name": "run_bash",
        "description": "执行任务时在worktree隔离环境下执行bash指令",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "array",
                    "description": "bash指令数组",
                    "items": {"type": "string", "description": "command指令参数"},
                },
                "task_id": {"type": "string", "description": "任务ID"},
            },
            "required": ["command", "task_id"],
        },
    },
    {
        "name": "closeout_worktree",
        "description": "任务完成时删除worktree或保留以便review",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "任务ID"},
                "action": {
                    "type": "string",
                    "enum": ["keep", "remove"],
                    "description": "保留或关闭worktree",
                },
                "reason": {
                    "type": "string",
                    "description": "选择某个action的依据或理由",
                },
            },
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
    cron_scheduler: CronScheduler,
    team_manager: TeammateManager,
    message_bus: MessageBus,
    agreement_store: AgreementStore,
    worktree_isolation: WorkTreesIsolation,
) -> dict[str, Callable[..., str]]:
    return {
        "bash": lambda **kw: run_bash(kw["command"]),
        "task": lambda **kw: sub_agent(prompt=kw["prompt"]),
        "TODO": lambda **kw: TODO.update(kw["todo_list"]),
        "skill": lambda **kw: skill_loader.get_content(kw["name"]),
        "read_file": lambda **kw: run_read(kw["path"], kw.get("limit")),
        "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
        "edit_file": lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
        "compact": lambda **kw: "Manual compression requested.",
        "save_memory": lambda **kw: memory_mgr.save_memory(
            kw["name"], kw["description"], kw["mem_type"], kw["content"]
        ),
        "create_tasks": lambda **kw: task_manager.create_tasks(kw["tasks"]),
        "update_task": lambda **kw: task_manager.update_task(
            kw["task_id"], kw["status"], kw["name"]
        ),
        "get_task": lambda **kw: task_manager.get_task(kw["task_id"]),
        "load_all": lambda **kw: task_manager.load_all(),
        "clear_all": lambda **kw: task_manager.clear_all(),
        "is_ready": lambda **kw: task_manager.is_ready(kw["task_id"]),
        "run_background": lambda **kw: background_manager.run(kw["command"]),
        "get_notifications": lambda **kw: background_manager.get_notifications(),
        "read_result": lambda **kw: background_manager.read_result(kw["task_id"]),
        "read_task": lambda **kw: background_manager.read_task(kw["task_id"]),
        "create_schedule": lambda **kw: cron_scheduler.create_schedule(
            kw["cron_expr"], kw["prompt"], kw.get("recurring", False)
        ),
        "get_queue": lambda **kw: cron_scheduler.get_queue(),
        "spawn_teammate": lambda **kw: team_manager.spawn(
            kw["name"], kw["role"], kw["prompt"]
        ),
        "list_teammates": lambda **kw: team_manager.list_team(),
        "send_message": lambda **kw: message_bus.send(
            kw["sender"], kw["to"], kw["content"], kw["msg_type"], kw.get("extra")
        ),
        "broadcast_teammates": lambda **kw: message_bus.broadcast(
            kw["sender"], kw["content"], kw["teammates"]
        ),
        "member_names": lambda **kw: team_manager.member_names(),
        "request_shutdown": lambda **kw: agreement_store.request_shutdown(kw["target"]),
        "response_shutdown": lambda **kw: agreement_store.response_shutdown(
            kw["request_id"], kw["approve"], kw["origin"], kw["response"]
        ),
        "request_plan": lambda **kw: agreement_store.request_plan(
            kw["origin"], kw["plan"]
        ),
        "response_plan": lambda **kw: agreement_store.response_plan(
            kw["request_id"], kw["approve"], kw["target"], kw["response"]
        ),
        "get_request": lambda **kw: agreement_store.get_request(kw["request_id"]),
        "create_worktree": lambda **kw: worktree_isolation.create_worktree(
            kw["name"], kw["task_id"]
        ),
        "run_bash": lambda **kw: worktree_isolation.run_bash(
            kw["command"], kw["task_id"]
        ),
        "closeout_worktree": lambda **kw: worktree_isolation.closeout_worktree(
            kw["task_id"], kw["action"], kw["reason"]
        ),
    }


def build_sub_tool_handlers(
    TODO: TODO_MANAGER,
    team_manager: TeammateManager,
    message_bus: MessageBus,
    agreement_store: AgreementStore,
    task_manager: TaskManager,
    claimable_predicate: ClaimablePredicate,
    worktree_isolation: WorkTreesIsolation,
) -> dict[str, Callable[..., str]]:
    return {
        "bash": lambda **kw: run_bash(kw["command"]),
        "calculator": lambda **kw: calculator(kw["expression"]),
        "get_current_time": lambda: get_current_time(),
        "TODO": lambda **kw: TODO.update(kw["todo_list"]),
        "read_file": lambda **kw: run_read(kw["path"], kw.get("limit")),
        "write_file": lambda **kw: run_write(kw["path"], kw["content"]),
        "edit_file": lambda **kw: run_edit(kw["path"], kw["old_text"], kw["new_text"]),
        "list_teammates": lambda **kw: team_manager.list_team(),
        "send_message": lambda **kw: message_bus.send(
            kw["sender"], kw["to"], kw["content"], kw["msg_type"], kw.get("extra")
        ),
        "broadcast_teammates": lambda **kw: message_bus.broadcast(
            kw["sender"], kw["content"], kw["teammates"]
        ),
        "member_names": lambda **kw: team_manager.member_names(),
        "response_shutdown": lambda **kw: agreement_store.response_shutdown(
            kw["request_id"], kw["approve"], kw["origin"], kw["response"]
        ),
        "request_plan": lambda **kw: agreement_store.request_plan(
            kw["origin"], kw["plan"]
        ),
        "load_all": lambda **kw: task_manager.load_all(),
        "claim_task": lambda **kw: claimable_predicate.claim_task(
            kw["name"], kw["role"]
        ),
        "update_claim_record": lambda **kw: claimable_predicate.update_claim_record(
            kw["task"], kw["name"], kw["status"], kw["claim_source"]
        ),
        "update_task": lambda **kw: task_manager.update_task(
            kw["task_id"], kw["status"], kw["name"]
        ),
        "create_worktree": lambda **kw: worktree_isolation.create_worktree(
            kw["name"], kw["task_id"]
        ),
        "run_bash": lambda **kw: worktree_isolation.run_bash(
            kw["command"], kw["task_id"]
        ),
        "closeout_worktree": lambda **kw: worktree_isolation.closeout_worktree(
            kw["task_id"], kw["action"], kw["reason"]
        ),
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


def run_write(path: str, content: str) -> str:
    try:
        fp = safe_path(path)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content)
        return f"Wrote {len(content)} bytes"
    except Exception as e:
        return f"Error: {e}"


def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        fp = safe_path(path)
        c = fp.read_text()
        if old_text not in c:
            return f"Error: Text not found in {path}"
        fp.write_text(c.replace(old_text, new_text, 1))
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"
