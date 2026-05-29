from pathlib import Path
from dataclasses import dataclass
import json
from threading import Thread

from anthropic import Anthropic
from MessageBus import MessageBus
from tools import build_sub_tool_handlers, SUB_TOOLS
from todo_manager import TODO_MANAGER


@dataclass
class TeammateManagerConfig:
    work_dir: Path
    client: Anthropic
    model: str
    bus: MessageBus


# 待办事项
TODO = TODO_MANAGER()

SUB_TOOL_HANDLERS = build_sub_tool_handlers(TODO)


class TeammateManager:
    def __init__(self, config: TeammateManagerConfig):
        self.config = config
        self.work_dir = config.work_dir
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.team = self._load_config()

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
                messages.append(
                    {
                        "role": "user",
                        "content": f"[from={line.get('from')}, type={line.get('type')}] {line.get('content')}",
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
                        handler = SUB_TOOL_HANDLERS.get(block.name)
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
