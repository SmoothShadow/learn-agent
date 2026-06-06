#!/usr/bin/env python3
# Harness: the loop -- the model's first connection to the real world.

from fnmatch import fnmatch
import os
import sys
import re
import copy
from pathlib import Path
from dotenv import load_dotenv
from anthropic import Anthropic, APIError
from httpx import request


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import build_tool_handlers, TOOLS, build_sub_tool_handlers, SUB_TOOLS
from todo_manager import TODO_MANAGER
from Compact import CompactConfig, Compactor
from MemoryManager import MemoryConfig, MemoryManager
from Skill import SkillConfig, SKILL_REGISTRY
from BashSecurityValidator import BashSecurityValidator, BashSecurityValidatorConfig
from PermissionManager import PermissionConfig, PermissionManager
from SystemPromptBuild import SystemPromptConfig, SystemPromptBuilder
from Recovery import Recovery, RecoveryConfig
from TaskManager import TaskManager, TaskManagerConfig
from BackgroundManager import BackgroundManager, BackgroundManagerConfig
from CronScheduler import CronScheduler, CronSchedulerConfig
from MessageBus import MessageBus, MessageBusConfig
from TeammateManager import TeammateManager, TeammateManagerConfig
from AgreementStore import AgreementStore, AgreementStoreConfig
from ClaimablePredicate import ClaimablePredicate, ClaimablePredicateConfig
from WorkTreesIsolation import WorkTreesIsolation, WorkTreesIsolationConfig

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
PROJECT_DIR = Path(__file__).resolve().parent.parent


# skill配置
SKILLS_DIR = PROJECT_DIR / "skills"
skill_loader = SKILL_REGISTRY(SkillConfig(skills_dir=SKILLS_DIR))

# 上下文压缩配置
THRESHOLD = 50000
TRANSCRIPT_DIR = WORKDIR / ".transcripts"
KEEP_RECENT = 3
PRESERVE_RESULT_TOOLS = {"read_file"}
compact = Compactor(
    CompactConfig(
        keep_recent=KEEP_RECENT,
        preserve_result_tools=PRESERVE_RESULT_TOOLS,
        transcript_dir=TRANSCRIPT_DIR,
        model=MODEL,
    ),
    client,
)

# 待办事项
TODO = TODO_MANAGER()

# memory配置
MEMORY_DIR = PROJECT_DIR / "memory"
MEMORY_TYPES = ("user", "feedback", "project", "reference")
MAX_INDEX_LINES = 200
memory_config = MemoryConfig(
    memory_dir=MEMORY_DIR,
    memory_types=MEMORY_TYPES,
    max_index_lines=MAX_INDEX_LINES,
    workdir=WORKDIR,
)
memory_mgr = MemoryManager(memory_config)


# bash校验配置
VALIDATORS = [
    ("shell_metachar", r"[;&|`$]"),  # shell metacharacters
    ("sudo", r"\bsudo\b"),  # privilege escalation
    ("rm_rf", r"\brm\s+(-[a-zA-Z]*)?r"),  # recursive delete
    ("cmd_substitution", r"\$\("),  # command substitution
    ("ifs_injection", r"\bIFS\s*="),  # IFS manipulation
]
# Singleton validator instance used by the permission pipeline
bash_validator = BashSecurityValidator(BashSecurityValidatorConfig(VALIDATORS))

# 权限配置
# -- Permission rules --
# Rules are checked in order: first match wins.
# Format: {"tool": "<tool_name_or_*>", "path": "<glob_or_*>", "behavior": "allow|deny|ask"}
DEFAULT_RULES = [
    # Always deny dangerous patterns
    {"tool": "bash", "content": "rm -rf /", "behavior": "deny"},
    {"tool": "bash", "content": "sudo *", "behavior": "deny"},
    # Allow reading anything
    {"tool": "read_file", "path": "*", "behavior": "allow"},
    {"tool": "mcp__docs__get_version", "path": "*", "behavior": "allow"},
    {"tool": "mcp__docs__search", "path": "*", "behavior": "allow"},
]
MODES = ["auto", "plan", "default"]

READ_ONLY_TOOLS = {"read_file", "bash_readonly"}

# 错误恢复
recovery = Recovery(RecoveryConfig())

# 任务管理
task_manager = TaskManager(TaskManagerConfig(task_dir=PROJECT_DIR / "tasks"))

# 后台任务管理
background_manager = BackgroundManager(
    BackgroundManagerConfig(work_dir=PROJECT_DIR / "background")
)

# 定时任务
cron_scheduler = CronScheduler(CronSchedulerConfig(work_dir=PROJECT_DIR / "cron"))

# 多agent协作
message_bus = MessageBus(
    MessageBusConfig(
        work_dir=PROJECT_DIR / "message_bus",
        VALID_MSG_TYPES={
            "message",
            "broadcast",
            "shutdown_request",
            "shutdown_response",
            "plan_approval",
            "plan_approval_response",
        },
    )
)
# 自主代理
claimable_predicate = ClaimablePredicate(
    ClaimablePredicateConfig(work_dir=PROJECT_DIR / "tasks")
)
# worktree 隔离
worktree_isolation = WorkTreesIsolation(
    WorkTreesIsolationConfig(work_dir=PROJECT_DIR, task_dir=PROJECT_DIR / "tasks")
)
# 团队协议
agreement_store = AgreementStore(
    AgreementStoreConfig(work_dir=PROJECT_DIR / "agreements", bus=message_bus)
)
team_manager = TeammateManager(
    TeammateManagerConfig(
        work_dir=PROJECT_DIR / "team",
        task_dir=PROJECT_DIR / "tasks",
        client=client,
        model=MODEL,
        bus=message_bus,
        agreement_store=agreement_store,
        task_manager=task_manager,
        claimable_predicate=claimable_predicate,
        worktree_isolation=worktree_isolation,
    )
)
# mcp plugin
mcp_clients = {}

# Tools that modify state
WRITE_TOOLS = {"write_file", "edit_file", "bash"}
permission_config = PermissionConfig(
    default_rules=DEFAULT_RULES,
    modes=MODES,
    read_only_tools=READ_ONLY_TOOLS,
    write_tools=WRITE_TOOLS,
    bash_validator=bash_validator,
)


SUB_TOOL_HANDLERS = build_sub_tool_handlers(
    TODO,
    team_manager=team_manager,
    message_bus=message_bus,
    agreement_store=agreement_store,
    task_manager=task_manager,
    claimable_predicate=claimable_predicate,
    worktree_isolation=worktree_isolation,
)


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


TOOL_HANDLERS = build_tool_handlers(
    sub_agent=sub_agent,
    TODO=TODO,
    skill_loader=skill_loader,
    memory_mgr=memory_mgr,
    task_manager=task_manager,
    background_manager=background_manager,
    cron_scheduler=cron_scheduler,
    team_manager=team_manager,
    message_bus=message_bus,
    agreement_store=agreement_store,
    worktree_isolation=worktree_isolation,
    mcp_clients=mcp_clients
)

def assemble_tool_pool():
    tools = copy.deepcopy(TOOLS)
    handler = copy.deepcopy(TOOL_HANDLERS)
    for client_name, client in mcp_clients.items():
        for tool_def in client.tools:
            tool_name = f"mcp__{normalize_mcp_name(client_name)}__{normalize_mcp_name(tool_def['name'])}"
            tool = {
                "name": tool_name,
                "description": tool_def["description"],
                "input_schema": tool_def["inputSchema"]
            }
            tools.append(tool)
            handler[tool_name] = lambda *, c=client, t=tool_def["name"], **kw: c.call_tool(t, kw)
    return {"tools": tools, "handler": handler}
            
_DISALLOWED_CHARS = re.compile(r'[^a-zA-Z0-9_-]')
def normalize_mcp_name(name: str) -> str:
    """Replace non [a-zA-Z0-9_-] with underscore."""
    return _DISALLOWED_CHARS.sub('_', name)

def print_tool_name(payload: dict):
    print(f"Tool name: {payload['tool_name']}")
    return {"exitCode": 0, "message": "tool name printed"}


def print_tool_output(payload: dict):
    print(f"Tool output: {payload['tool_output']}")
    return {"exitCode": 0, "message": "tool output printed"}


HOOKS = {
    "SessionStart": [print_tool_name],
    "PreToolUse": [print_tool_name],
    "PostToolUse": [print_tool_output],
}


def run_hooks(event: str, payload: dict):
    if event in HOOKS:
        for handler in HOOKS[event]:
            result = handler(payload)
            if result.get("exitCode", 1) in (1, 2):
                return result
    return {"exitCode": 0, "message": "no hooks or all passed"}


def tool_handler(block: dict, state: dict) -> str:
    tool_handler = assemble_tool_pool()["handler"]
    handler = tool_handler.get(block.name)
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


# def build_system_prompt() -> str:
#     """Assemble system prompt with memory content included."""
#     parts = [f"{SYSTEM}"]

#     # Inject memory content if available
#     memory_section = memory_mgr.load_memory_prompt()
#     if memory_section:
#         parts.append(memory_section)

#     parts.append(MEMORY_GUIDANCE)
#     return "\n\n".join(parts)


# -- Agent loop with nag reminder injection --

system_prompt_builder = SystemPromptBuilder(
    SystemPromptConfig(
        workdir=WORKDIR,
        tools=assemble_tool_pool()["tools"],
        skill_loader=skill_loader,
        memory_mgr=memory_mgr,
        model=MODEL,
        assemble_tool_pool=assemble_tool_pool
    )
)


def agent_loop(messages: list, perms: PermissionManager):
    state = {
        "use_todo": False,
        "round_since_todo": 0,
    }
    while True:
        # system = build_system_prompt()
        system = system_prompt_builder.build()
        notification = background_manager.get_notifications()
        notifications_text = "\n".join(
            f"type: {n['type']}; task_id: {n['task_id']}; status: {n['status']}; preview: {n['preview']}"
            for n in notification
        )
        if notification:
            messages.append(
                {
                    "role": "user",
                    "content": f"Background notifications:{notifications_text}",
                }
            )
        compact.micro_compact(messages)
        if compact.estimate_tokens(messages) > THRESHOLD:
            print("[auto_compact triggered]")
            messages[:] = compact.auto_compact(messages)
        for line in message_bus.read_inbox("lead"):
            msg_type = line.get("type")
            request_id = line.get("request_id", "")
            messages.append(
                {
                    "role": "user",
                    "content": f"[from={line.get('from')}, type={msg_type}] {line.get('content')}",
                }
            )
            if msg_type == "shutdown_response":
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"这不是一条普通的消息,"
                            f"而是一个来自{line.get('from')}的shutdown_response,"
                            f"请先使用get_request工具读取request_id={request_id}的json查看status是否同意关闭"
                        ),
                    }
                )
            if msg_type == "plan_approval":
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"这不是一条普通的消息,"
                            f"而是一个来自{line.get('from')}的plan_request,"
                            f"请使用get_request工具读取request_id={request_id}的json查看plan内容，"
                            f"然后使用response_plan工具来告诉对方是否同意其执行计划"
                        ),
                    }
                )
        try:
            response = client.messages.create(
                model=MODEL,
                messages=messages,
                system=system,
                tools=assemble_tool_pool()["tools"],
                max_tokens=8000,
            )
            messages.append({"role": "assistant", "content": response.content})
            if response.stop_reason == "max_tokens":
                error_info = recovery.choose_recovery(response.stop_reason, "")
                error_type = error_info.get("kind", "")
                if error_type == "continue":
                    print(error_info)
                    messages[:] = recovery.recovery_handler(
                        error_type,
                        messages,
                        compact,
                        "",
                    )
                    continue
                else:
                    print(error_info)
                    break
            recovery.reset_attempt()
            if response.stop_reason != "tool_use":
                return
            results = []
            manual_compact = False
            for block in response.content:
                if block.type == "tool_use":

                    # PreToolUse hook
                    hook_result = run_hooks(
                        "PreToolUse",
                        {"tool_name": block.name, "tool_input": block.input},
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
                        messages.append(
                            {"role": "user", "content": hook_result["message"]}
                        )

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
                messages[:] = compact.auto_compact(messages)
                return
        except (ConnectionError, TimeoutError, OSError, APIError) as e:
            error_body = str(e).strip().lower()
            error_info = recovery.choose_recovery("", error_body)
            error_type = error_info.get("kind", "")
            if error_type == "compact":
                print(error_info)
                messages[:] = recovery.recovery_handler(
                    error_type, messages, compact, e
                )
                continue
            if error_type == "retry":
                print(recovery.recovery_handler(error_type, messages, compact, e))
                continue
            if error_type == "fail":
                print(error_info)
                break


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

    perms = PermissionManager(permission_config, mode=mode_input)
    print(f"[Permission mode: {mode_input}]")

    history = []
    while True:
        try:
            query = input("\033[35ms19 mcp server>>> \033[0m")
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

        if query.startswith("/prompt"):
            system = system_prompt_builder.build()
            print("=== System Prompt ===")
            print(system)
            print("=== End System Prompt ===")
            continue

        if query.startswith("/sections"):
            system = system_prompt_builder.build()
            for lines in system.split("\n"):
                if lines.strip().startswith("#"):
                    print(lines)
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
