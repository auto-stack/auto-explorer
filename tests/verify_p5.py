#!/usr/bin/env python3
"""verify_p5 — PLAN-005 交互响应优化验收短探针。

- AC-01 普通目录零 Tick 延迟：mkbig 500 → AddrGo → applied 回执（handler
  同步完成）后立即读 view_total=500 就位 + last_snapshot_ms ≥ 0（内联臂
  哨兵反证：延迟臂置 -77）+ last_derive_ms ≤ 50（应用内预算）。
- AC-02 超大目录保险不回归：mkbig 9000 → cap 8000 + 截断标注 +
  d_real_total=9000 + last_snapshot_ms=-77（延迟臂哨兵实证）。
- AC-03 首屏窗口 120：mkbig 2500 → checkbox ≤130（120 窗+表头+容差）
  → GrowRender ×1 增长 → view_total 恒 2500。
- AC-04 菜单条件挂载：闭合态「打开」钮 0 个（旧泄漏面清零反断言）；
  ctx_id 状态直喂（popover open 绑定态）→ 「收藏此目录」可寻。

执行内发现（PLAN-005 记录）：VM 轨 handler 对新增 model 字段的写入
不可经 state 桥观测（fixture 写入可见、handler 写不落）——原设计的
nav_t0/last_nav_ms 导航全程计时仪器退役，改用 last_snapshot_ms 分臂
哨兵（旧字段，handler 写已证可靠）。
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
from mkbig import mkbig  # noqa: E402


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
                }, timeout=90)
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

    def state_str(self, field):
        raw = self.state(field)
        if len(raw) >= 2 and raw.startswith('"') and raw.endswith('"'):
            raw = raw[1:-1]
        return raw.replace("\\\\", "\\")

    def fixture(self, state, event=None):
        args = {"schema_version": 1, "state": state}
        if event is not None:
            args["trigger"] = {"widget": "App", "event": event, "input": None}
        r = self.post("autoui_fixture", args)
        return r.get("status") == "applied"

    def find_count(self, kind="checkbox", label=None, exact=False):
        args = {"limit": 900}
        if kind:
            args["kind"] = kind
        if label is not None:
            args["label"] = label
        text = self.post("autoui_find", args).get("content", [{}])[0].get("text", "")
        if "No nodes found" in text:
            return 0
        if label is None:
            return len(re.findall(rf"{kind} vnode_(\d+)", text))
        pat = rf'{kind} vnode_(\d+) \{{(?:label|placeholder): "([^"]*)"'
        n = 0
        for m in re.finditer(pat, text):
            clean = re.sub("[\ue000-\uf8ff]", "", m.group(2))
            if (clean == label) if exact else (label in clean):
                n += 1
        return n


PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name + ("" if ok else f"  [{detail}]"))


def main():
    tmp = tempfile.mkdtemp(prefix="fm-vp5-")
    big500 = mkbig(500, tmp)
    big9000 = mkbig(9000, tmp)
    big2500 = mkbig(2500, tmp)

    port = pick_free_port(9870)
    log = open(os.path.join(tempfile.gettempdir(), f"fm-vp5-{port}.log"), "w", encoding="utf-8")
    proc = subprocess.Popen(
        [AUTO_BIN, "run", "-r", "vm"], cwd=PROJECT,
        env={**os.environ, "AUTOUI_MCP_PORT": str(port),
             "AUTO_VM_STORAGE_FILE": os.path.join(tmp, "s.json"),
             "AUTOUI_TEST_FIXTURES": "1"},
        stdout=log, stderr=log)
    try:
        url = f"http://localhost:{port}/mcp"
        for _ in range(60):
            try:
                requests.post(url, json={"jsonrpc": "2.0", "method": "tools/list",
                                         "params": {}, "id": 1}, timeout=2)
                break
            except (requests.ConnectionError, requests.Timeout):
                time.sleep(1)
        c = C(url)
        for _ in range(40):
            if c.state("booted") == "true":
                break
            time.sleep(0.5)

        print("[AC-01] 普通目录零 Tick 延迟（mkbig 500）")
        c.fixture({"addr": big500}, "AddrGo")
        # applied 回执 = VM 线程同步执行完毕——立即读，至多 2 拍容限
        ok = False
        snap = -1
        vt = -1
        for _ in range(2):
            snap = c.state_int("last_snapshot_ms")
            vt = c.state_int("view_total")
            dm = c.state_int("last_derive_ms")
            if vt == 500 and 0 <= snap and 0 <= dm <= 50:
                ok = True
                break
            time.sleep(0.3)
        check("内联臂就位（view=500 + snapshot≥0 无哨兵）", ok,
              f"view_total={vt} last_snapshot_ms={snap} last_derive_ms={c.state_int('last_derive_ms')}")
        check("view_total=500 已就位", vt == 500, f"view_total={vt}")

        print("[AC-02] 超大目录预算保险不回归（mkbig 9000）")
        try:
            c.fixture({"addr": big9000}, "AddrGo")
        except RuntimeError:
            pass  # 仪器墙：handler 照跑，下方轮询收敛
        ok = False
        for _ in range(60):
            time.sleep(1.0)
            if c.state_int("view_total") == 8000 and c.state_int("d_real_total") == 9000:
                ok = True
                break
        check("cap 8000 + d_real_total=9000", ok,
              f"view_total={c.state_int('view_total')} d_real_total={c.state_int('d_real_total')}")
        check("截断标注在场", "已加载前 8000" in c.state_str("item_count_str"),
              c.state_str("item_count_str"))
        check("延迟臂哨兵 -77（分臂实证）", c.state_int("last_snapshot_ms") == -77,
              f"last_snapshot_ms={c.state_int('last_snapshot_ms')}")

        print("[AC-03] 首屏窗口 120（mkbig 2500）")
        try:
            c.fixture({"addr": big2500}, "AddrGo")
        except RuntimeError:
            pass
        for _ in range(30):
            time.sleep(1.0)
            if c.state_int("view_total") == 2500:
                break
        vt = c.state_int("view_total")
        check("view_total=2500", vt == 2500, f"view_total={vt}")
        cb_n = c.find_count(kind="checkbox")
        check("首屏 checkbox ≤130（120 窗+表头）", 121 <= cb_n <= 130, f"checkbox={cb_n}")
        c.fixture({"booted": True}, "GrowRender")
        grew = False
        for _ in range(15):
            time.sleep(1.0)
            if c.state_int("render_cap") >= 620:
                grew = True
                break
        cb_n2 = c.find_count(kind="checkbox")
        check("GrowRender ×1 扩窗增长", grew and cb_n2 > cb_n,
              f"render_cap={c.state_int('render_cap')} checkbox={cb_n}->{cb_n2}")
        check("view_total 恒 2500", c.state_int("view_total") == 2500,
              str(c.state_int("view_total")))
        check("状态栏全量计数不变", "2500" in c.state_str("item_count_str"),
              c.state_str("item_count_str"))

        print("[AC-04] 菜单条件挂载")
        open_n = c.find_count(kind="button", label="打开", exact=True)
        check("闭合态「打开」钮 0 个", open_n == 0, f"open_n={open_n}")
        c.fixture({"ctx_id": 0})
        time.sleep(0.8)
        fav_n = c.find_count(kind="button", label="收藏此目录", exact=True)
        check("打开态「收藏此目录」可寻", fav_n >= 1, f"fav_n={fav_n}")
        c.fixture({"booted": True}, "CtxClose")
        time.sleep(0.8)
        open_n2 = c.find_count(kind="button", label="打开", exact=True)
        check("关闭后「打开」钮归零", open_n2 == 0, f"open_n={open_n2}")

        print(f"\nverify_p5: {len(PASS)} pass, {len(FAIL)} fail")
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
    # MCP 服务器线程偶发静默失联（SPEC §6 在册框架病）——整趟重试同
    # desktop_mcp 纪律（全新进程/端口/存储）。
    for _attempt in range(3):
        try:
            sys.exit(main())
        except requests.ConnectionError:
            print(f"WARN: MCP 服务器中途失联（在册框架病）——整趟重试（{_attempt + 1}/3）", flush=True)
            time.sleep(3)
    sys.exit(1)
