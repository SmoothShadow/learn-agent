from pathlib import Path
from dataclasses import dataclass
import json
from threading import Thread

from anthropic import Anthropic
from MessageBus import MessageBus
from tools import build_sub_tool_handlers, SUB_TOOLS
from todo_manager import TODO_MANAGER
from AgreementStore import AgreementStore


@dataclass
class TeammateManagerConfig:
    work_dir: Path
    client: Anthropic
    model: str
    bus: MessageBus
    agreement_store: AgreementStore


class TeammateManager:
    def __init__(self, config: TeammateManagerConfig):
        self.config = config
        self.work_dir = config.work_dir
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.team = self._load_config()
        # 待办事项
        TODO = TODO_MANAGER()
        self.SUB_TOOL_HANDLERS = build_sub_tool_handlers(
            TODO,
            team_manager=self,
            message_bus=self.config.bus,
            agreement_store=self.config.agreement_store,
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
        )
        messages = [{"role": "user", "content": prompt}]
        member = self._find_member(name)
        for _ in range(50):
            for line in self.config.bus.read_inbox(name):
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
                        handler = self.SUB_TOOL_HANDLERS.get(block.name)
                        output = handler(**block.input)
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
                return f"Error: {e}"
        if member and member["status"] not in {"shutdown"}:
            member["status"] = "idle"
            self._save_config()
            return f"{name} completed task"
        return f"{name} completed task"

    def list_team(self):
        team = f"Team: {self.team['team_name']}\n"
        members = "\n".join(
            [f"{member['name']} ({member['role']})" for member in self.team["members"]]
        )
        return team + members

    def member_names(self):
        return [member["name"] for member in self.team["members"]]
