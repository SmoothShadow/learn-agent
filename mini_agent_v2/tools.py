# tools.py
import math

def calculator(expression: str) -> str:
    """安全地计算数学表达式。"""
    try:
        # 只允许安全函数
        allowed_names = {"abs": abs, "round": round, "min": min, "max": max, "pow": pow, "sqrt": math.sqrt}
        result = eval(expression, {"__builtins__": {}}, allowed_names)
        return str(result)
    except Exception as e:
        return f"计算错误: {e}"

def get_current_time() -> str:
    """返回当前时间。"""
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")