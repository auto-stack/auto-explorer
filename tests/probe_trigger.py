#!/usr/bin/env python3
"""PLAN-045 执行内探针：MCP fixture 触发四形态实证（框架回归定位）。

背景：desktop_mcp 套件在 T4 清空步骤报 handler_not_found（namespaced
registry miss）——payload 编码事件名（Name\x1fs\x1fval）疑似在 PLAN-659
T-05 严格预检（renderer.rs has_handler_for 直查）中未剥 payload 而恒
miss。本探针对同一实例测四形态，产出裁定数据：

  A: {widget,event:"SetSearch\x1fs\x1fnotes"}   编码-str（套件现行形态）
  B: {widget,event:"SetSearch",input:"notes"}    input-str（Plan 370 通道）
  C: {widget,event:"ItemCtx\x1fi\x1f0"}          编码-int
  D: {widget,event:"ItemCtx",input:"0"}          input-int（类型协变观测）

用法：python probe_trigger.py <mcp_url>（实例须已启动）
"""
import json
import sys
import time

import requests

SEP = "\x1f"


def post(url, name, arguments, req_id):
    import time as _t
    last = None
    for _ in range(30):
        try:
            resp = requests.post(url, json={
                "jsonrpc": "2.0", "method": "tools/call",
                "params": {"name": name, "arguments": arguments}, "id": req_id,
            }, timeout=25)
            return resp.json().get("result", {})
        except (requests.ConnectionError, requests.Timeout, ValueError) as e:
            last = e
            _t.sleep(1)
    raise last


def state_str(url, field):
    r = post(url, "autoui_state", {"fields": [field]}, 9001)
    content = r.get("content", [])
    text = content[0].get("text", "") if content else ""
    import re as _re
    m = _re.search(rf"(\w+): (.+?) \((?:int|str|bool|list|val|float|unknown)\)", text)
    return m.group(2) if m else text


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:9427"
    rid = [0]

    def fixture(state, trigger):
        rid[0] += 1
        args = {"schema_version": 1, "state": state, "trigger": trigger}
        return post(url, "autoui_fixture", args, rid[0])

    def show(tag, result):
        status = result.get("status") or ("error" if result.get("isError") else "?")
        content = result.get("content", [])
        text = content[0].get("text", "")[:180] if content else ""
        print(f"[{tag}] status={status} {text}")

    # 前置：确认在场（booted）
    print("item_count_str =", state_str(url, "item_count_str"))

    show("A 编码-str SetSearch=notes",
         fixture({"booted": True}, {"widget": "App", "event": f"SetSearch{SEP}s{SEP}notes", "input": None}))
    time.sleep(0.3)
    print("  after A: item_count_str =", state_str(url, "item_count_str"),
          "| search_q =", state_str(url, "search_q"))

    show("B input-str SetSearch=notes",
         fixture({"booted": True}, {"widget": "App", "event": "SetSearch", "input": "notes"}))
    time.sleep(0.3)
    print("  after B: item_count_str =", state_str(url, "item_count_str"),
          "| search_q =", state_str(url, "search_q"))

    show("C 编码-int ItemCtx=0",
         fixture({"booted": True}, {"widget": "App", "event": f"ItemCtx{SEP}i{SEP}0", "input": None}))
    time.sleep(0.3)
    print("  after C: selected_info =", state_str(url, "selected_info"))

    show("D input-int ItemCtx=0",
         fixture({"booted": True}, {"widget": "App", "event": "ItemCtx", "input": "0"}))
    time.sleep(0.3)
    print("  after D: selected_info =", state_str(url, "selected_info"))

    # 清理：清搜索（用 input 空串）
    show("E input-str SetSearch=\"\"",
         fixture({"booted": True}, {"widget": "App", "event": "SetSearch", "input": ""}))
    time.sleep(0.3)
    print("  after E: item_count_str =", state_str(url, "item_count_str"),
          "| search_q =", repr(state_str(url, "search_q")))


if __name__ == "__main__":
    main()
