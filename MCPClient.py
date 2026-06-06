class MCPClient:
    def __init__(self, name:str):
        self.name = name
        self.tools:list[dict] = []
        self._handler: dict[str, callable] = {}
    
    def register(self, tool_defs: list[dict], handlers: dict[str, callable]):
        self.tools = tool_defs
        self._handler = handlers
    
    def call_tool(self, name:str,args:dict):
        if name not in self._handler:
            return f"Tool {name} not found"
        try:
            return self._handler.get(name)(**args)
        except Exception as e:
            return f"MCP error: {e}"
    
    