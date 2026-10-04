#!/usr/bin/env python3
"""verify_p2 — PLAN-002 核心验收独立短探针（单会话 ~90s，降低 MCP 环境病暴露面）。

覆盖：多选统计/批量复制粘贴/冲突保留两者/递归删除/键盘导航/守卫。
全走可靠通道：无参触发 + 状态注入 + 磁盘断言 + input-str 通道。
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from probe_perf import AUTO_BIN, PROJECT, pick_free_port  # noqa: E402


class C:
    def __init__(self, url):
        self.url = url
        self.rid = 0

    def post(self, name, args):
        last = None
        for _ in range(30):
            self.rid += 1
            try:
                r = requests.post(self.url, json={
                    "jsonrpc": "2.0", "method": "tools/call",
                    "params": {"name": name, "arguments": args}, "id": self.rid,
                }, timeout=60)
                return r.json().get("result", {})
            except (requests.ConnectionError, requests.Timeout, ValueError) as e:
                last = e
                time.sleep(1)
        raise last

    def state(self, field):
        r = self.post("autoui_state", {"fields": [field]})
        text = r.get("content", [{}])[0].get("text", "")
        m = re.search(rf"(\w+): (.+?) \((?:int|str|bool|list|val|float|unknown)\)", text)
        return m.group(2) if m else text

    def state_int(self, field):
        m = re.search(r"-?\d+", self.state(field))
        return int(m.group(0)) if m else -1

    def fixture(self, state, event=None, input_val=None):
        args = {"schema_version": 1, "state": state}
        if event is not None:
            args["trigger"] = {"widget": "App", "event": event, "input": input_val}
        r = self.post("autoui_fixture", args)
        return r.get("status") == "applied"

    def trig(self, event):
        try:
            return self.fixture({"booted": True}, event)
        except RuntimeError:
            return False  # ack 超窗——handler 可能照跑


PASS = []
FAIL = []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name + ("" if ok else f"  [{detail}]"))


def main():
    tmp = tempfile.mkdtemp(prefix="fm-vp2-")
    # 语料：src（4 文件）+ dst + conflict 种子 + 递归删除树
    src = os.path.join(tmp, "src")
    os.makedirs(src)
    for n, c in (("a.txt", "AAA"), ("b.txt", "BBB"), ("c.md", "CCC"), ("dir-d", None)):
        if c is None:
            os.makedirs(os.path.join(src, n))
        else:
            with open(os.path.join(src, n), "w", encoding="utf-8") as f:
                f.write(c)
    dst = os.path.join(tmp, "dst")
    os.makedirs(dst)
    with open(os.path.join(dst, "a.txt"), "w", encoding="utf-8") as f:
        f.write("OLD")
    rmtree = os.path.join(tmp, "rmtree")
    os.makedirs(os.path.join(rmtree, "sub", "deep"))
    for p in (rmtree, os.path.join(rmtree, "sub"), os.path.join(rmtree, "sub", "deep")):
        with open(os.path.join(p, "x.txt"), "w", encoding="utf-8") as f:
            f.write("x")

    port = pick_free_port(9700)
    log = open(os.path.join(tempfile.gettempdir(), f"fm-vp2-{port}.log"), "w", encoding="utf-8")
    proc = subprocess.Popen(
        [AUTO_BIN, "run", "-r", "vm"], cwd=PROJECT,
        env={**os.environ, "AUTOUI_MCP_PORT": str(port),
             "AUTO_VM_STORAGE_FILE": os.path.join(tmp, "s.json"),
             "AUTOUI_TEST_FIXTURES": "1"},
        stdout=log, stderr=log)
    try:
        url = f"http://localhost:{port}/mcp"
        up = False
        for _ in range(60):
            try:
                requests.post(url, json={"jsonrpc": "2.0", "method": "tools/list",
                                         "params": {}, "id": 1}, timeout=2)
                up = True
                break
            except (requests.ConnectionError, requests.Timeout):
                time.sleep(1)
        if not up:
            print("MCP not up")
            return 1
        c = C(url)
        for _ in range(40):
            if c.state("booted") == "true":
                break
            time.sleep(0.5)

        print("[T19] 多选与批量复制粘贴")
        c.fixture({"addr": src}, "AddrGo")
        time.sleep(1.0)
        c.trig("SelectAll")
        time.sleep(0.5)
        check("T19 全选计数", c.state_int("sel_count") == 4, f"sel_count={c.state_int('sel_count')}")
        check("T19 统计串", "4 项已选" in c.state("sel_label"), c.state("sel_label"))
        # 范围选择：anchor=0..selected=2 → 3 项
        c.trig("ClearSel")
        time.sleep(0.4)
        c.fixture({"anchor_id": 0, "selected_id": 2}, "SelRangeRun")
        time.sleep(0.5)
        check("T19 范围选择 3 项", c.state_int("sel_count") == 3, f"sel_count={c.state_int('sel_count')}")
        # 回全选 → 复制 → 粘贴到 dst（a.txt 冲突 → 保留两者）
        c.trig("SelectAll")
        time.sleep(0.4)
        c.trig("CopySel")
        time.sleep(0.4)
        c.fixture({"addr": dst}, "AddrGo")
        time.sleep(1.0)
        c.trig("PasteInto")
        time.sleep(1.0)
        check("T21 冲突模态弹出", c.state("paste_conflict_open") == "true",
              c.state("paste_conflict_open"))
        try:
            c.fixture({"booted": True}, "PasteConflictResolve", input_val="keepboth")
        except RuntimeError:
            pass
        time.sleep(1.5)
        check("T21 保留两者落盘", os.path.isfile(os.path.join(dst, "a (2).txt")),
              str(sorted(os.listdir(dst))))
        check("T19 粘贴齐", sorted(os.listdir(dst)) ==
              ["a (2).txt", "a.txt", "b.txt", "c.md", "dir-d"],
              str(sorted(os.listdir(dst))))

        print("[T22] 递归删除（批量）")
        c.fixture({"addr": rmtree}, "AddrGo")
        time.sleep(1.0)
        c.trig("SelectAll")
        time.sleep(0.4)
        c.trig("DeleteSel")
        time.sleep(0.6)
        check("T22 批量确认模态", c.state("confirm_multi_open") == "true",
              c.state("confirm_multi_open"))
        desc = c.state("confirm_multi_desc")
        check("T22 递归文案", "递归删除" in desc, desc[:50])
        c.trig("ExecuteDeleteSel")
        time.sleep(1.5)
        check("T22 内容全灭", not os.path.exists(os.path.join(rmtree, "sub")) and not os.path.exists(os.path.join(rmtree, "x.txt")))

        print("[T20] 键盘导航与守卫")
        c.fixture({"addr": src}, "AddrGo")
        time.sleep(1.0)
        c.trig("NavHome")
        time.sleep(0.3)
        si0 = c.state("selected_info")
        c.trig("NavDown")
        time.sleep(0.3)
        si1 = c.state("selected_info")
        check("T20 ↓ 移动", si0 != si1 and "选定" in si1, f"{si0[:24]} -> {si1[:24]}")
        c.trig("NavEnd")
        time.sleep(0.3)
        si_end = c.state("selected_info")
        check("T20 End 到尾", "dir-d" in si_end or "c.md" in si_end, si_end[:30])
        # 守卫：addr_editing 时导航不劫持
        c.fixture({"addr_editing": True}, "NavDown")
        time.sleep(0.3)
        si_guard = c.state("selected_info")
        check("T20 输入态守卫", si_guard == si_end, f"{si_guard[:24]} == {si_end[:24]}")

        print(f"\nverify_p2: {len(PASS)} pass, {len(FAIL)} fail")
        return 0 if not FAIL else 1
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except Exception:
            proc.kill()
        log.close()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
