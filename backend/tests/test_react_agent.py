"""ReactAgent 循环逻辑单元测试（mock 引擎，不依赖真实 Ollama/云端）

覆盖：
- 工具循环：模型调工具 -> 工具返回结果 -> 模型出终稿
- 高危工具确认：循环暂停返回 needs_confirm，confirm / reject 后恢复
- 未知工具、工具失败收敛
- 步数触顶收敛

直接 `python tests/test_react_agent.py` 运行，亦可被 pytest 收集。
"""

from __future__ import annotations

import asyncio

from localflow.adapters.react_agent import ReactAgent
from localflow.ports.engine import ChatMessage, ChatResponse, GenerateOptions, LLMEngine
from localflow.ports.tool import Tool, ToolContext, ToolRegistry, ToolResult


# ---- 可脚本化的 mock 引擎 ----

class ScriptedEngine(LLMEngine):
    """按脚本逐步返回结果，便于测试确定性的循环路径"""
    name = "script"

    def __init__(self, script):
        self.script = list(script)
        self.calls = []           # 记录收到的消息，便于断言

    async def chat(self, model, messages, options=None):
        self.calls.append(messages)
        if not self.script:
            return ChatResponse(content="完成。", model=model, usage={})
        return self.script.pop(0)

    async def chat_stream(self, model, messages, options=None):
        raise NotImplementedError

    async def list_models(self):
        return []

    async def health(self):
        return True


# ---- 一个简单工具 + 一个高危工具 ----

class GrepTool(Tool):
    name = "grep"
    description = "grep"
    async def run(self, params, ctx=None):
        return ToolResult(ok=True, output=f"found:{params.get('kw')}")


class WriteTool(Tool):
    name = "write"
    description = "write; needs confirm"
    is_hazardous = True
    confirmation_counter = 0
    async def run(self, params, ctx=None):
        return ToolResult(ok=False, output="需确认写入", require_confirm=True,
                          confirm_token="w1", data=params)
    async def confirm_run(self, token, params, ctx=None):
        WriteTool.confirmation_counter += 1
        return ToolResult(ok=True, output="written")


def _tc(name, args, call_id="c1"):
    return [{"id": call_id, "name": name, "arguments": args}]


def _registry():
    reg = ToolRegistry()
    reg.register(GrepTool())
    reg.register(WriteTool())
    return reg


async def _run_ok():
    """循环：grep -> 结果 -> 终稿"""
    engine = ScriptedEngine([
        ChatResponse(content="", model="m", usage={"total_tokens": 1},
                     tool_calls=_tc("grep", {"kw": "x"})),
        ChatResponse(content="找到了。", model="m", usage={"total_tokens": 2}),
    ])
    reg = _registry()
    agent = ReactAgent(get_engine=lambda: engine, tool_registry=reg)
    res = await agent.run_once("找 x", "m")
    assert res.ok and "找到" in res.final_answer
    # 结果消息应被回填给模型
    roles = [m.role for m in engine.calls[1]]
    assert "tool" in roles, roles
    return res


async def _confirm_flow():
    """高危工具 -> 暂停 -> confirm -> 恢复出终稿"""
    engine = ScriptedEngine([
        ChatResponse(content="", model="m", usage={}, tool_calls=_tc("write", {"f": "a"})),
        ChatResponse(content="已写入。", model="m", usage={}),
    ])
    reg = _registry()
    agent = ReactAgent(get_engine=lambda: engine, tool_registry=reg)
    res = await agent.run_once("写文件", "m")
    assert not res.ok and res.error == "needs_confirm", res
    assert res.needs_confirm and res.needs_confirm["token"] == "w1"

    # 错误令牌应被拒
    bad = await agent.confirm_tool("wrong", "write", {"f": "a"})
    assert not bad.ok and "不匹配" in bad.error
    # 正确令牌 -> 恢复
    res2 = await agent.confirm_tool("w1", "write", {"f": "a"})
    assert res2.ok and "写入" in res2.final_answer
    return (res, res2)


async def _reject_flow():
    """拒绝高危工具 -> 注入拒绝 -> 模型收尾"""
    engine = ScriptedEngine([
        ChatResponse(content="", model="m", usage={}, tool_calls=_tc("write", {"f": "a"})),
        ChatResponse(content="好的，我不写了。", model="m", usage={}),
    ])
    reg = _registry()
    agent = ReactAgent(get_engine=lambda: engine, tool_registry=reg)
    res = await agent.run_once("写文件", "m")
    assert res.needs_confirm
    res2 = await agent.reject_tool("w1", "write", {"f": "a"})
    assert res2.ok and "不写" in res2.final_answer
    return res2


async def _unknown_tool_falls_back():
    """未知工具：注入错误并让模型继续到终稿"""
    engine = ScriptedEngine([
        ChatResponse(content="", model="m", usage={}, tool_calls=_tc("nope", {})),
        ChatResponse(content="抱歉，无法完成。", model="m", usage={}),
    ])
    reg = _registry()
    agent = ReactAgent(get_engine=lambda: engine, tool_registry=reg)
    res = await agent.run_once("做个事", "m")
    assert res.ok  # 模型收尾成功，动作收敛为说明
    return res


async def _max_steps_trims():
    """模型一直调工具不收敛 -> 步数触顶收敛"""
    engine = ScriptedEngine([
        ChatResponse(content="", model="m", usage={}, tool_calls=_tc("grep", {"kw": str(i)}))
        for i in range(20)
    ])
    reg = _registry()
    agent = ReactAgent(get_engine=lambda: engine, tool_registry=reg)
    res = await agent.run_once("循环", "m", max_steps=3)
    assert not res.ok and res.trimmed
    return res


def _main():
    async def all():
        await _run_ok()
        await _confirm_flow()
        await _reject_flow()
        await _unknown_tool_falls_back()
        await _max_steps_trims()

    asyncio.run(all())
    print("ALL REACT-AGENT UNIT TESTS PASSED")


async def test_all():
    await _run_ok()
    await _confirm_flow()
    await _reject_flow()
    await _unknown_tool_falls_back()
    await _max_steps_trims()


if __name__ == "__main__":
    _main()