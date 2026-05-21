import time
from pathlib import Path
from dataclasses import dataclass
import json
from anthropic import Anthropic


@dataclass
class CompactConfig:
    keep_recent: int
    preserve_result_tools: set
    transcript_dir: Path
    model: str


class Compactor:
    def __init__(self, config: CompactConfig, client: Anthropic) -> None:
        self.config = config
        self.client = client

    @staticmethod
    def estimate_tokens(messages: list) -> int:
        """Estimate token count from messages."""
        return len(str(messages)) // 4

    def micro_compact(self, messages: list) -> list:
        # Collect (msg_index, part_index, tool_result_dict) for all tool_result entries
        tool_result = []
        for msg_idx, msg in enumerate(messages):
            if msg.get("role") == "user":
                content = msg.get("content", [])
                if isinstance(content, list):
                    for part_idx, part in enumerate(content):
                        if isinstance(part, dict) and part.get("type") == "tool_result":
                            tool_result.append((msg_idx, part_idx, part))
        if len(tool_result) < self.config.keep_recent:
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
        to_clear = tool_result[: -self.config.keep_recent]
        for _, _, result in to_clear:
            if (
                not isinstance(result.get("content"), str)
                or len(result.get("content")) < 100
            ):
                continue
            tool_id = result.get("tool_use_id", "")
            tool_name = result.get("tool_name", tool_name_map.get(tool_id, ""))
            if tool_name in self.config.preserve_result_tools:
                continue
            result["content"] = f"[Previous: used {tool_name}]"
        return messages

    # -- Layer 2: auto_compact - save transcript, summarize, replace messages --
    def auto_compact(self, messages: list) -> list:
        # Save full transcript to disk
        self.config.transcript_dir.mkdir(exist_ok=True)
        transcript_path = (
            self.config.transcript_dir / f"transcript_{int(time.time())}.json"
        )
        with open(transcript_path, "w") as f:
            for msg in messages:
                f.write(json.dumps(msg, default=str) + "\n")

        # Ask LLM to summarize
        conversation_text = json.dumps(messages, default=str)[-80000:]
        response = self.client.messages.create(
            model=self.config.model,
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
