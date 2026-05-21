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
