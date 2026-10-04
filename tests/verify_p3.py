#!/usr/bin/env python3
"""verify_p3 — PLAN-003 预览面板验收短探针（单会话）。"""
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


PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name + ("" if ok else f"  [{detail}]"))


def main():
    tmp = tempfile.mkdtemp(prefix="fm-vp3-")
    d = os.path.join(tmp, "pv")
    os.makedirs(os.path.join(d, "sub"))
    with open(os.path.join(d, "notes.txt"), "w", encoding="utf-8") as f:
        f.write("PREVIEW-CONTENT-XYZ 中文预览行\n" * 30)
    with open(os.path.join(d, "photo.png"), "wb") as f:
        f.write(bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108020000009077"
            "53de0000000c4944415408d763f8cfc00000030101"))
    with open(os.path.join(d, "sub", "inner.txt"), "w", encoding="utf-8") as f:
        f.write("inner")
    with open(os.path.join(d, "blob.bin"), "wb") as f:
        f.write(b"\x00\x01\x02")

    port = pick_free_port(9750)
    log = open(os.path.join(tempfile.gettempdir(), f"fm-vp3-{port}.log"), "w", encoding="utf-8")
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

        c.fixture({"addr": d}, "AddrGo")
        time.sleep(1.2)
        # 行序（name asc，目录先）：blob.bin? 'b'... names: blob.bin, notes.txt, photo.png, sub → asc: blob.bin, notes.txt, photo.png, sub

        # 开面板 + 文本预览（notes.txt = id 1）
        c.fixture({"preview_on": True, "selected_id": 2}, "PreviewRun")
        time.sleep(0.5)
        check("文本形态", c.state("preview_kind") == '"text"', c.state("preview_kind"))
        check("文本头内容", "PREVIEW-CONTENT-XYZ" in c.state("preview_text"),
              c.state("preview_text")[:40])
        check("全文注脚", "全文" in c.state("preview_text_note"), c.state("preview_text_note"))

        # 图片预览（photo.png = id 2）
        c.fixture({"selected_id": 3}, "PreviewRun")
        time.sleep(0.5)
        check("图片形态", c.state("preview_kind") == '"image"', c.state("preview_kind"))
        check("图片 thumb URI", len(c.state("preview_img")) > 10, c.state("preview_img")[:30])

        # 目录摘要（sub = id 3；树 = 1 文件）
        c.fixture({"selected_id": 0}, "PreviewRun")
        time.sleep(0.3)
        check("目录形态", c.state("preview_kind") == '"dir"', c.state("preview_kind"))
        ok = False
        for _ in range(20):
            time.sleep(0.5)
            if c.state("preview_items_label") == '"1 项"':
                ok = True
                break
        check("目录摘要收敛 1 项", ok, c.state("preview_items_label"))

        # other（blob.bin = id 0）
        c.fixture({"selected_id": 1}, "PreviewRun")
        time.sleep(0.4)
        check("other 形态", c.state("preview_kind") == '"other"', c.state("preview_kind"))
        check("other 字段", c.state("preview_f_name") == '"blob.bin"',
              c.state("preview_f_name"))

        # 世代取消：选大目录立刻切回文件——摘要不覆盖焦点
        c.fixture({"selected_id": 0}, "PreviewRun")
        time.sleep(0.1)
        c.fixture({"selected_id": 1}, "PreviewRun")
        time.sleep(1.5)
        check("切换即时改焦点", c.state("preview_kind") == '"other"', c.state("preview_kind"))

        print(f"\nverify_p3: {len(PASS)} pass, {len(FAIL)} fail")
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
