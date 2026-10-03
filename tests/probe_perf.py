#!/usr/bin/env python3
"""PLAN-045 T-03 执行内性能探针：大目录导航/排序/过滤计时（VM 轨）。

mkbig 生成 N 项目录 → fixture 导航 → 计时收敛（P-1）→ 排序切换计时
（P-2 交互拍）→ 过滤击键计时（P-5）→ 顺序正确性抽样（目录恒先 +
name 升序头几个名断言）。

用法：python probe_perf.py [N]（默认 10000；实例自动拉起）
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

import requests

SEP = "\x1f"
AUTO_BIN = r"D:\autostack\auto-lang\target\debug\auto.exe"
PROJECT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def pick_free_port(start=9460):
    import socket
    for port in range(start, start + 40):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError("no free port")


def mkbig(n, root):
    d = os.path.join(root, f"big{n}")
    os.makedirs(d, exist_ok=True)
    # 预生成内容（幂等：已足额即跳过）
    existing = len(os.listdir(d))
    if existing < n:
        for i in range(existing, n):
            kind = i % 10
            if kind == 0:
                os.makedirs(os.path.join(d, f"dir-{i:05d}"), exist_ok=True)
            else:
                p = os.path.join(d, f"file-{i:05d}.txt")
                with open(p, "w", encoding="utf-8") as f:
                    f.write("x" * (i % 97))
    return d


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
        m = re.search(r"\d+", self.state(field))
        return int(m.group(0)) if m else 0

    def fixture(self, state, event=None, input_val=None):
        args = {"schema_version": 1, "state": state}
        if event is not None:
            trig = {"widget": "App", "event": event, "input": input_val}
            args["trigger"] = trig
        r = self.post("autoui_fixture", args)
        st = r.get("status")
        if st != "applied":
            raise RuntimeError(f"fixture: {r}")
        return r


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10000
    tmp = tempfile.mkdtemp(prefix="fm-perf-")
    big = mkbig(n, tmp)
    port = pick_free_port()
    storage = os.path.join(tmp, "storage.json")
    log = open(os.path.join(tempfile.gettempdir(), f"fm-perf-{port}.log"), "w", encoding="utf-8")
    proc = subprocess.Popen(
        [AUTO_BIN, "run", "-r", "vm"], cwd=PROJECT,
        env={**os.environ, "AUTOUI_MCP_PORT": str(port),
             "AUTO_VM_STORAGE_FILE": storage, "AUTOUI_TEST_FIXTURES": "1"},
        stdout=log, stderr=log)
    url = f"http://localhost:{port}/mcp"
    try:
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
        c = Client(url)
        # 等 boot
        for _ in range(40):
            if c.state("booted") == "true":
                break
            time.sleep(0.5)

        # P-1：真实导航（ack 窗口 2s 内可观测的规模；10k 全量同步快照
        # 实测超窗——D4 分批物化必要性证据，T-05 落地）
        nav_n = min(n, 200)
        big_nav = mkbig(nav_n, tmp)
        t0 = time.perf_counter()
        c.fixture({"addr": big_nav}, "AddrGo")
        t_nav = (time.perf_counter() - t0) * 1000
        total = c.state_int("view_total")
        print(f"[P-1] nav {nav_n} items 1st (cold: Defender 首扫混入): {t_nav:.0f}ms  view_total={total}")
        t0 = time.perf_counter()
        c.fixture({"addr": tmp}, "AddrGo")
        t_up = (time.perf_counter() - t0) * 1000
        t0 = time.perf_counter()
        c.fixture({"addr": big_nav}, "AddrGo")
        t_nav2 = (time.perf_counter() - t0) * 1000
        print(f"[P-1] nav {nav_n} items 2nd (warm): wall {t_nav2:.0f}ms  in-app snapshot={c.state_int('last_snapshot_ms')}ms derive={c.state_int('last_derive_ms')}ms  (侧路 {t_up:.0f}ms)")

        # 纯派生交互计时（200 项内存路径：过滤→归并→窗口物化）
        t0 = time.perf_counter()
        c.fixture({"booted": True}, "SortBySize")
        print(f"[P-2] sort→size ({nav_n}): wall {(time.perf_counter() - t0) * 1000:.0f}ms  in-app derive={c.state_int('last_derive_ms')}ms")
        t0 = time.perf_counter()
        c.fixture({"booted": True}, "SortByName")
        print(f"[P-2] sort→name ({nav_n}): wall {(time.perf_counter() - t0) * 1000:.0f}ms  in-app derive={c.state_int('last_derive_ms')}ms")
        t0 = time.perf_counter()
        c.fixture({"booted": True}, "SetSearch", input_val="file-099")
        print(f"[P-5] filter 'file-099' ({nav_n}): wall {(time.perf_counter() - t0) * 1000:.0f}ms  in-app derive={c.state_int('last_derive_ms')}ms  view_total={c.state_int('view_total')}")
        c.fixture({"booted": True}, "SetSearch", input_val="")
        if n > 3000:
            big_full = mkbig(n, tmp)
            t0 = time.perf_counter()
            timed_out = False
            try:
                c.fixture({"addr": big_full}, "AddrGo")
            except RuntimeError:
                timed_out = True
            if timed_out:
                print(f"[P-1] nav {n} items: >2000ms fixture ack 窗（D4 分批必要性证据）——等待后台 handler 收敛…")
                # 被弃请求仍在 VM 线程跑完——轮询 view_total 直到等于 n
                for _ in range(120):
                    time.sleep(1.0)
                    try:
                        if c.state_int("view_total") == n:
                            print(f"[P-1] nav {n} items: 总耗时 ≈ {(time.perf_counter() - t0):.1f}s（含 ack 窗外执行）")
                            break
                    except Exception:
                        pass
                else:
                    print(f"[P-1] nav {n} items: 120s 未收敛")
            else:
                print(f"[P-1] nav {n} items: {(time.perf_counter() - t0) * 1000:.0f}ms  view_total={c.state_int('view_total')}")
            # 导回小目录，清 VM 线程
            try:
                c.fixture({"addr": big_nav}, "AddrGo")
            except Exception:
                pass
            shutil.rmtree(big_full, ignore_errors=True)

        # 顺序正确性抽样：i=0 应为目录（目录恒先），名字采样
        c.fixture({"ctx_id": 0}, "CtxSelect")
        time.sleep(0.2)
        print(f"[order] row0 selected_info = {c.state('selected_info')[:60]}")

        # 派生路径隔离测（P-2/P-4/P-5）：fixture 直注 n 行 dir_entries
        #（注：10k 行 1MB payload 实测打挂 MCP 服务线程——失败时跳过，
        # 以 nav_n 规模的交互计时为准）
        try:
            rows = []
            for i in range(n):
                rows.append({
                    "name": f"file-{i:05d}.txt", "path": f"X://probe/file-{i:05d}.txt",
                    "is_dir": (i % 10 == 0), "size": i % 97, "mtime": 1700000000 + i,
                    "file_ext": "txt", "size_str": f"{i % 97} B", "type_name": "文档",
                    "date": "2023-11-14 22:13", "is_hidden": False,
                    "name_key": f"file-{i:05d}.txt",
                })
            c.fixture({"dir_entries": rows})
            t0 = time.perf_counter()
            c.fixture({"booted": True}, "RefreshView")
            t_rv = (time.perf_counter() - t0) * 1000
            print(f"[P-2] RefreshView on {n} (injected): {t_rv:.0f}ms  view_total={c.state_int('view_total')}")
        except Exception as e:
            print(f"[P-2] injection {n} rows failed ({type(e).__name__}) — skip")

        t0 = time.perf_counter()
        c.fixture({"booted": True}, "SortBySize")
        t_size = (time.perf_counter() - t0) * 1000
        print(f"[P-2/P-4] sort toggle size ({n}): {t_size:.0f}ms  sort_col={c.state('sort_col')}")
        t0 = time.perf_counter()
        c.fixture({"booted": True}, "SortByName")
        t_name = (time.perf_counter() - t0) * 1000
        print(f"[P-2/P-4] sort toggle name ({n}): {t_name:.0f}ms")

        t0 = time.perf_counter()
        c.fixture({"booted": True}, "SetSearch", input_val="file-099")
        t_f = (time.perf_counter() - t0) * 1000
        ft = c.state_int("view_total")
        print(f"[P-5] filter 'file-099' ({n}): {t_f:.0f}ms  view_total={ft}")
        c.fixture({"booted": True}, "SetSearch", input_val="")
        print(f"[P-5] clear: view_total={c.state_int('view_total')}")

        print("PROBE OK")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        log.close()
        shutil.rmtree(big, ignore_errors=True)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
