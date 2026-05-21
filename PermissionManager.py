from fnmatch import fnmatch
import json
from dataclasses import dataclass
from BashSecurityValidator import BashSecurityValidator


@dataclass
class PermissionConfig:
    modes: list[str]
    default_rules: list
    bash_validator: BashSecurityValidator
    write_tools: set[str]
    read_only_tools: set[str]


class PermissionManager:
    """
    Manages permission decisions for tool calls.

    Pipeline: deny_rules -> mode_check -> allow_rules -> ask_user

    The teaching version keeps the decision path short on purpose so readers
    can implement it themselves before adding more advanced policy layers.
    """

    def __init__(
        self, config: PermissionConfig, mode: str = "default", rules: list = None
    ):
        self.write_tools = config.write_tools
        self.read_only_tools = config.read_only_tools
        self.bash_validator = config.bash_validator
        self.modes = config.modes
        self.default_rules = config.default_rules
        if mode not in self.modes:
            raise ValueError(f"Invalid mode: {mode}")
        self.mode = mode
        self.rules = rules or list(self.default_rules)
        # Simple denial tracking helps surface when the agent is repeatedly
        # asking for actions the system will not allow.
        self.consecutive_denials = 0
        self.max_consecutive_denials = 3

    def check(self, tool_name: str, tool_input: dict) -> dict:
        """
        Returns: {"behavior": "allow"|"deny"|"ask", "reason": str}
        """
        # Step 0: Bash security validation (before deny rules)
        # Teaching version checks early for clarity.
        if tool_name == "bash":
            command = tool_input.get("command", "")
            failures = self.bash_validator.validate(command)
            if failures:
                serve = {"sudo", "rm -rf"}
                serve_hits = [f for f in failures if f[0] in serve]
                if serve_hits:
                    return {
                        "behavior": "deny",
                        "reason": "Security violation: "
                        + ", ".join([f[1] for f in serve_hits]),
                    }
                desc = self.bash_validator.describe_failures(command)
                return {"behavior": "ask", "reason": "Bash validation failed: " + desc}
        # Step 1: Deny rules (bypass-immune, checked first always)
        for rule in self.rules:
            if rule.get("behavior") != "deny":
                continue
            if self._matches(rule, tool_name, tool_input):
                return {"behavior": "deny", "reason": f"Blocked by deny rule: {rule}"}

        # Step 2: Mode-based decisions
        if self.mode == "plan":
            # Plan mode: deny all write operations, allow reads
            if tool_name in self.write_tools:
                return {
                    "behavior": "deny",
                    "reason": "Plan mode: write operations not allowed",
                }
            return {"behavior": "allow", "reason": "Plan mode: read operations allowed"}

        if self.mode == "auto":
            # Auto mode: auto-allow read-only tools, ask for writes
            if tool_name in self.read_only_tools or tool_name == "read_files":
                return {
                    "behavior": "allow",
                    "reason": "Auto mode: read-only tools allowed",
                }
            pass
        # Step 3: Allow rules
        for rule in self.rules:
            if rule.get("behavior") != "allow":
                continue
            if self._matches(rule, tool_name, tool_input):
                return {"behavior": "allow", "reason": f"Allowed by allow rule: {rule}"}
        # Step 4: Ask user (default behavior for unmatched tools)
        return {
            "behavior": "ask",
            "reason": f"No matching rule found for {tool_name}, ask user",
        }

    def ask_user(self, tool_name: str, tool_input: dict) -> bool:
        """Interactive approval prompt. Returns True if approved."""
        preview = json.dumps(tool_input, ensure_ascii=False)[:200]
        print(f"\n [Permission] {tool_name}: {preview}")
        try:
            answer = input("  Allow? (y/n/always): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False

        if answer == "always":
            self.rules.append({"tool": tool_name, "path": "*", "behavior": "allow"})
            self.consecutive_denials = 0
            return True
        elif answer in ("y", "yes"):
            self.consecutive_denials = 0
            return True

        self.consecutive_denials += 1
        if self.consecutive_denials >= self.max_consecutive_denials:
            print(
                f"  [{self.consecutive_denials} consecutive denials -- "
                "consider switching to plan mode]"
            )
            self.mode = "plan"
        return False

    def _matches(self, rule: dict, tool_name: str, tool_input: dict) -> bool:
        """Check if a rule matches the tool call."""
        # Tool name match
        if rule.get("tool") and rule["tool"] != "*":
            if rule["tool"] != tool_name:
                return False
        # Path pattern match
        if "path" in rule and rule["path"] != "*":
            path = tool_input.get("path", "")
            if not fnmatch(path, rule["path"]):
                return False
        # Content pattern match (for bash commands)
        if "content" in rule:
            command = tool_input.get("command", "")
            if not fnmatch(command, rule["content"]):
                return False
        return True
