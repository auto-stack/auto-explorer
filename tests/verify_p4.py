#!/usr/bin/env python3
"""verify_p4 — PLAN-004 递归搜索/收藏夹/自然排序验收短探针。"""
import json
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

    def fixture(self, state, event=None, input_val=None):
        args = {"schema_version": 1, "state": state}
        if event is not None:
            args["trigger"] = {"widget": "App", "event": event, "input": input_val}
        r = self.post("autoui_fixture", args)
        return r.get("status") == "applied"


PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name + ("" if ok else f"  [{detail}]"))


def main():
    tmp = tempfile.mkdtemp(prefix="fm-vp4-")
    d = os.path.join(tmp, "tree")
    os.makedirs(os.path.join(d, "a", "deep"))
    os.makedirs(os.path.join(d, "needle-dir"))
    hits = [
        os.path.join(d, "a", "needle-file.txt"),
        os.path.join(d, "a", "deep", "needle-deep.md"),
        os.path.join(d, "needle-dir", "readme.txt"),
    ]
    for h in hits:
        with open(h, "w", encoding="utf-8") as f:
            f.write("x")
    with open(os.path.join(d, "a", "other.txt"), "w", encoding="utf-8") as f:
        f.write("y")
    # 自然排序语料
    nd = os.path.join(tmp, "nat")
    os.makedirs(nd)
    for nm in ("file10.txt", "file2.txt", "file1.txt"):
        with open(os.path.join(nd, nm), "w", encoding="utf-8") as f:
            f.write("n")

    port = pick_free_port(9800)
    log = open(os.path.join(tempfile.gettempdir(), f"fm-vp4-{port}.log"), "w", encoding="utf-8")
    proc = subprocess.Popen(
        [AUTO_BIN, "run", "-r", "vm"], cwd=PROJECT,
        env={**os.environ, "AUTOUI_MCP_PORT": str(port),
             "AUTO_VM_STORAGE_FILE": os.path.join(tmp, "s.json"),
             "AUTOUI_TEST_FIXTURES": "1"},
        stdout=log, stderr=log)
    storage_file = os.path.join(tmp, "s.json")
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

        print("[T24] 递归搜索")
        c.fixture({"addr": d}, "AddrGo")
        time.sleep(1.0)
        c.fixture({"search_scope": "tree"}, "SetSearch", input_val="needle")
        time.sleep(1.5)
        check("搜索态进入", c.state("search_mode") == "true", c.state("search_mode"))
        # fs.walk 实测不含目录条目（文档歧义在案）——命中 = 2 个文件
        check("命中 2 项", c.state_int("d_total") == 2, f"d_total={c.state_int('d_total')}")
        lbl = c.state("search_label")
        check("状态串", "子树命中" in lbl and "2 项" in lbl, lbl)
        # 逐词重跑（世代自愈——同步重算）
        c.fixture({"booted": True}, "SetSearch", input_val="needle-deep")
        time.sleep(1.2)
        check("逐词收敛 1 项", c.state_int("d_total") == 1, f"d_total={c.state_int('d_total')}")
        # 结果行导航：搜 readme（needle-dir 内文件）→ CtxOpen 跳父目录
        c.fixture({"booted": True}, "SetSearch", input_val="readme")
        time.sleep(1.2)
        c.fixture({"ctx_id": 0}, "CtxOpen")
        time.sleep(1.5)
        cur = c.state("current_path")
        check("结果行跳父目录", "needle-dir" in cur, cur[:60])
        check("导航退出搜索态", c.state("search_mode") == "false", c.state("search_mode"))

        print("[T25] 收藏夹")
        c.fixture({"booted": True}, "FavToggleCurrent")
        time.sleep(0.5)
        check("收藏 1 条", "vmref" in c.state("favs"), c.state("favs")[:40])
        stored = ""
        if os.path.exists(storage_file):
            stored = open(storage_file, encoding="utf-8", errors="replace").read()
        check("storage 落盘", "fileman.favs" in stored and chr(34) not in stored[-2:], stored[-100:])
        check("fav_view 行", "vmref" in c.state("fav_view"), c.state("fav_view")[:40])
        c.fixture({"booted": True}, "FavToggleCurrent")
        time.sleep(0.5)
        check("再点取消", "vmref" not in c.state("favs") or c.state("favs").count("vmref") == 0,
              c.state("favs")[:40])

        print("[T26] 自然排序")
        # 清残留搜索词（搜索词跨导航存续是产品语义——搜索框可见；测试须显式清）
        # tree 空词触发 ExitTreeSearch 导航——等它收敛再 nav
        c.fixture({"booted": True}, "SetSearch", input_val="")
        time.sleep(2.5)
        try:
            c.fixture({"addr": nd}, "AddrGo")
        except RuntimeError:
            pass
        for _ in range(20):
            time.sleep(1.0)
            if c.state_int("view_total") == 3:
                break
        names = []
        for i in range(3):
            c.fixture({"ctx_id": i}, "CtxSelect")
            time.sleep(0.15)
            si = c.state("selected_info")
            si2 = si.strip(chr(34))
            if si2.startswith("选定: "):
                names.append(si2[4:].split(" (")[0])
        check("file1 < file2 < file10", names == ["file1.txt", "file2.txt", "file10.txt"],
              str(names))

        print(f"\nverify_p4: {len(PASS)} pass, {len(FAIL)} fail")
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
