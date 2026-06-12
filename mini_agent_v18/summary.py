### 任务一

### 最小聊天循环
# messages = []
# query = input("agent>>>")
# messages.append({"role": "user", "content": query})
# while True:
#     response = llm(messages)
#     messages.append({"role": "assistant", "content": response.content})

### 最小agent循环
messages = []
query = input("agent>>>")
messages.append({"role": "user", "content": query})
while True:
    response = llm(messages, tools)
    messages.append({"role": "assistant", "content": response.content})
    if not any(block.type == "tool_use" for block in response.content):
        break
    else:
        results = []
        for block in response.content:
            if block.type == "tool_use":
                output = handler(block.name, block.input)
                results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": output}
                )
        messages.append({"role": "user", "content": results})

### 完整harness loop
sessionStart_hook()
messages = []
query = input("agent>>>")
messages.append({"role": "user", "content": query})
while True:
    system_prompt_builder()
    background_manager().get_notifications()
    cron_scheduler().get_queue()
    auto_compact()
    response = llm(messages, tools)
    messages.append({"role": "assistant", "content": response.content})
    if not any(block.type == "tool_use" for block in response.content):
        stop_hook()
        break
    else:
        results = []
        for block in response.content:
            if block.type == "tool_use":
                pre_tool_use_hook()
                check_permission(block.name)
                if block.name == "compact":
                    manual_compact()
                output = handler(block.name, block.input)
                post_tool_use_hook()
                results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": output}
                )
        messages.append({"role": "user", "content": results})
    if llm_error or stop_reason == "max_tokens":
        recovery()
