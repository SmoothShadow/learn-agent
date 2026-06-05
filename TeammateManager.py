from pathlib import Path
from dataclasses import dataclass
import json
from threading import Thread

from anthropic import Anthropic
from pydantic import InstanceOf
from MessageBus import MessageBus
from tools import build_sub_tool_handlers, SUB_TOOLS
from todo_manager import TODO_MANAGER
from AgreementStore import AgreementStore
from ClaimablePredicate import ClaimablePredicate
from TaskManager import TaskManager
from WorkTreesIsolation import WorkTreesIsolation


@dataclass
class TeammateManagerConfig:
    work_dir: Path
    task_dir: Path
    client: Anthropic
    model: str
    bus: MessageBus
    agreement_store: AgreementStore
    task_manager: TaskManager
    claimable_predicate: ClaimablePredicate
    worktree_isolation: WorkTreesIsolation


class TeammateManager:
    def __init__(self, config: TeammateManagerConfig):
        self.config = config
        self.work_dir = config.work_dir
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.team = self._load_config()
        self.current_task_by_member = {}
        # 待办事项
        TODO = TODO_MANAGER()
        self.SUB_TOOL_HANDLERS = build_sub_tool_handlers(
            TODO,
            team_manager=self,
            message_bus=self.config.bus,
            agreement_store=self.config.agreement_store,
            task_manager=self.config.task_manager,
            claimable_predicate=self.config.claimable_predicate,
            worktree_isolation=self.config.worktree_isolation,
        )

    def _save_config(self):
        file_path = self.work_dir / "config.json"
        file_path.write_text(
            json.dumps({"team_name": "default", "members": self.team["members"]})
        )

    def _load_config(self):
        file_path = self.work_dir / "config.json"
        if not file_path.exists():
            return {"team_name": "default", "members": []}
        return json.loads(file_path.read_text())

    def _find_member(self, name: str):
        for member in self.team["members"]:
            if member["name"] == name:
                return member
        return None

    def spawn(self, name: str, role: str, prompt: str):
        member = self._find_member(name)
        if member:
            if member["status"] not in {"idle", "shutdown"}:
                return f"{name} now is working"
            member["status"] = "working"
            member["role"] = role
        else:
            self.team["members"].append(
                {"name": name, "role": role, "status": "working"}
            )
        self._save_config()
        thread = Thread(target=self._run_agent, args=(name, role, prompt))
        thread.start()
        return f"{name} spawned successfully"

    def _run_agent(self, name: str, role: str, prompt: str):
        system = (
            f"You are '{name}', role: {role}, at {self.work_dir}. "
            f"Use send_message to communicate. Complete your task."
            f"when you start task, use the worktree isolation to create a new worktree and work on it."
            f"run commands in those lanes, then choose keep/remove for closeout."
        )
        messages = [{"role": "user", "content": prompt}]
        member = self._find_member(name)
        for _ in range(50):
            message_box = self.config.bus.read_inbox(name)
            if message_box:
                for line in message_box:
                    msg_type = line.get("type")
                    request_id = line.get("request_id")
                    messages.append(
                        {
                            "role": "user",
                            "content": f"[from={line.get('from')}, type={msg_type}] {line.get('content')}",
                        }
                    )
                    if msg_type == "shutdown_request":
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    f"这不是普通聊天，而是关闭协议。"
                                    f"请完成必要收尾后，调用 response_shutdown "
                                    f"并传入 request_id='{request_id}', origin='{name}'。"
                                ),
                            }
                        )
                    elif msg_type == "plan_approval_response":
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    f"这不是普通聊天，而是计划审批结果。"
                                    f"请根据 request_id='{request_id}' 的审批结果决定是否继续执行。"
                                ),
                            }
                        )
            else:
                result = self.config.claimable_predicate.claim_task(name, role)
                if result:
                    worktree_result = self.config.worktree_isolation.create_worktree(
                        f"{name}_{result['id']}", result["id"]
                    )
                    if not isinstance(worktree_result, dict):
                        messages.append(
                            {
                                "role": "user",
                                "content": "创建worktree失败，请检查错误",
                            }
                        )
                        continue
                    self.set_current_task(name, result["id"])
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                f"任务主题: {result.get('subject')}\n"
                                f"任务描述: {result.get('description')}\n"
                                f"worktree已创建：工作目录:{worktree_result['path']}，分支名：{worktree_result['branch']}\n"
                                f"请在该工作目录下完成任务，任务锁定worktree状态下不允许使用bash工具，只允许使用run_bash工具执行command\n"
                                f"任务执行完成后，使用update_task工具更新任务状态并使用closeout_worktree工具处理worktree"
                            ),
                        }
                    )
                else:
                    continue
            try:
                response = self.config.client.messages.create(
                    model=self.config.model,
                    system=system,
                    messages=messages,
                    tools=SUB_TOOLS,
                    max_tokens=8000,
                )
                messages.append({"role": "assistant", "content": response.content})
                if response.stop_reason != "tool_use":
                    break
                result = []
                for block in response.content:
                    if block.type == "tool_use":
                        if block.name == "bash":
                            bash_result = self._check_worktree(name)
                            if bash_result:
                                result.append(
                                    {
                                        "type": "tool_result",
                                        "tool_use_id": block.id,
                                        "content": f"{name} have a task, worktree is locked, please use run_bash tool to execute command",
                                    }
                                )
                                continue
                        handler = self.SUB_TOOL_HANDLERS.get(block.name)
                        output = handler(**block.input)
                        if (
                            block.name == "closeout_worktree"
                            and "successfully" in output
                        ):
                            self.clear_current_task(name)
                        result.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": str(output),
                            }
                        )
                messages.append({"role": "user", "content": result})
            except Exception as e:
                if member:
                    member["status"] = "idle"
                    self._save_config()
                    self.clear_current_task(name)
                return f"Error: {e}"
        if member and member["status"] not in {"shutdown"}:
            member["status"] = "idle"
            self._save_config()
            self.clear_current_task(name)
            return f"{name} completed task"
        self.clear_current_task(name)
        return f"{name} completed task"

    def list_team(self):
        team = f"Team: {self.team['team_name']}\n"
        members = "\n".join(
            [f"{member['name']} ({member['role']})" for member in self.team["members"]]
        )
        return team + members

    def member_names(self):
        return [member["name"] for member in self.team["members"]]

    def set_current_task(self, member_name: str, task_id: str):
        self.current_task_by_member[member_name] = task_id

    def clear_current_task(self, member_name: str):
        self.current_task_by_member[member_name] = ""

    def _check_worktree(self, name: str) -> bool:
        """检查任务是否绑定工作树"""
        task_id = self.current_task_by_member.get(name)
        if not task_id:
            return False
        task_path = self.config.task_dir / f"{task_id}.json"
        if not task_path.exists():
            return False
        task = json.loads(task_path.read_text())
        if task["worktree_state"] == "active":
            return True
        else:
            return False
