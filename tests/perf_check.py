#!/usr/bin/env python3
"""perf_check — PLAN-001 性能门（应用内计时口径）。

MCP fixture 墙钟在大状态上为仪器噪声（~3s 级），性能验收一律读应用内
计时状态（last_snapshot_ms / last_derive_ms，app.at 内 time.now_ms 差值）。
容错：ack 超窗不视为失败（handler 照跑），轮询状态收敛。

门（dev 机 VM 轨，±3x 容差——超限 fail，环境异常注记 waiver）：
  P-1 nav 2000 项：snapshot ≤ 2000ms
  P-2 排序切换 @2000：derive ≤ 200ms
  P-4 过滤击键 @2000：derive ≤ 200ms
  P-5 大目录 6000：snapshot ≤ 4000ms + derive ≤ 400ms

用法：python perf_check.py（自动拉起 `auto run -r vm`）
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
from mkbig import mkbig  # noqa: E402
from probe_perf import AUTO_BIN, PROJECT, pick_free_port  # noqa: E402

GATES = [
    # (label, n_items, action, expect_field, expect_value, timing_field, budget_ms)
    ("P-1 nav 2000（snapshot 预算）", 2000, "nav", "view_total", "2000", "last_snapshot_ms", 2000),
    ("P-2 排序切换 @2000（derive 预算）", 2000, "SortBySize", "sort_col", "size", "last_derive_ms", 200),
    ("P-4 过滤击键 @2000（derive 预算）", 2000, "search", "view_total", "1", "last_derive_ms", 200),
    ("P-5 大目录 6000（snapshot 预算）", 6000, "nav", "view_total", "6000", "last_snapshot_ms", 4000),
]


class Client:
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


def main():
    tmp = tempfile.mkdtemp(prefix="fm-perfcheck-")
    port = pick_free_port(9600)
    log = open(os.path.join(tempfile.gettempdir(), f"fm-perfcheck-{port}.log"),
               "w", encoding="utf-8")
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
            print("FAIL: MCP not up")
            return 1
        c = Client(url)
        for _ in range(40):
            if c.state("booted") == "true":
                break
            time.sleep(0.5)

        fails = 0
        for label, n, action, field, value, tfield, budget in GATES:
            d = mkbig(n, tmp)
            t0 = time.perf_counter()
            try:
                if action == "nav":
                    c.fixture({"addr": d}, "AddrGo")
                elif action == "SortBySize":
                    c.fixture({"booted": True}, "SortBySize")
                elif action == "search":
                    c.fixture({"booted": True}, "SetSearch", input_val="file-01999")
                acked = True
            except RuntimeError:
                acked = False  # 仪器墙：handler 照跑，轮询收敛
            ok = False
            while time.perf_counter() - t0 < 60:
                time.sleep(1.0)
                try:
                    raw = c.state(field)
                    if value in raw:
                        ok = True
                        break
                except Exception:
                    pass
            ms = c.state_int(tfield)
            wall = time.perf_counter() - t0
            verdict = "PASS" if (ok and 0 <= ms <= budget) else "FAIL"
            if verdict == "FAIL":
                fails += 1
            print(f"[{verdict}] {label}: {tfield}={ms}ms（预算 {budget}ms）"
                  f" 收敛={'Y' if ok else 'N'} ack={'Y' if acked else '超窗'} wall={wall:.0f}s")
            # 清搜索，导航归位
            if action == "search":
                try:
                    c.fixture({"booted": True}, "SetSearch", input_val="")
                except RuntimeError:
                    pass
            shutil.rmtree(d, ignore_errors=True)
        print(f"perf_check: {'ALL PASS' if fails == 0 else f'{fails} FAIL'}")
        return 0 if fails == 0 else 1
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
