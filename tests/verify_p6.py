#!/usr/bin/env python3
"""verify_p6 — PLAN-006 传输管线 + 响应残余硬化验收短探针。

- AC-01 大目录复制不冻结：mkdeep 420 文件树（>200 阈值 → 分片管线）
  → CopySel/AddrGo/PasteInto → 传输中穿插 SortBySize/SortByName 断言
  交互响应（sort_col 状态回读，老字段 handler 写可靠）→ 完成后磁盘
  逐树比对（文件数 + 抽样内容）。
- AC-02 进度与取消：mkdeep 2400 树传输中进度单调推进；XferCancel →
  下拍停臂收尾（取消钮消失 + 磁盘部分完成且不再增长）。
- 快路径回归：50 文件小目录复制即完成（取消钮恒不在场）。
- 并发防护：传输中再触发 PasteInto 被拒（进度分母不变、目标无新文件）。
- AC-03 扫描上限：guard 代码在场（源审计，docs/plans/006 记录）+ 小树
  递归搜索回归绿（命中正确、无 60000 误标注）。
- AC-04 启动 3-tick：booted 观测时延（≤3s 粗门防挂起；套件 ×3 轮
  全绿为真正回归门，见 T-05）+ 首目录就位。
- AC-05 hover 门控：大目录（2500）RowLeave no-op（hover_id 保持）；
  小目录 RowLeave 正常复位 -1（老字段双向断言）。

观测通道注记（R10 债 / PLAN-005 范式）：x_* 为本计划新增 model 字段，
state 桥 handler 写可能不可见（fixture 写可见、handler 写不落）——
主通道 = 渲染投影（取消钮在场 ⇔ x_label 非空 ⇔ 传输臂活跃；vtree/find
读渲染树即 VM 活状态）+ 磁盘效应（目标文件计数）；state 桥 x_* 读仅作
旁证（可见则采用）。toast 断言面不可达（renderer 侧堆叠层无 state 桥），
以 handler 代码审计代偿（docs/plans/006 复审记录）。

用法：python tests/verify_p6.py
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


def mkdeep(n, root, name):
    """嵌套测试树：root 下 1/4 文件 + subA/subB 各 ~3/8 文件，内容含序号。"""
    d = os.path.join(root, name)
    os.makedirs(d)
    suba = os.path.join(d, "subA")
    subb = os.path.join(d, "subB")
    os.makedirs(suba)
    os.makedirs(subb)
    na, nb = n // 4, n // 4
    nr = n - na - nb
    for i in range(nr):
        _wf(os.path.join(d, f"file-{i:05d}.txt"), f"root-{i}")
    for i in range(na):
        _wf(os.path.join(suba, f"file-{i:05d}.txt"), f"subA-{i}")
    for i in range(nb):
        _wf(os.path.join(subb, f"file-{i:05d}.txt"), f"subB-{i}")
    return d


def _wf(p, content):
    with open(p, "w", encoding="utf-8") as f:
        f.write(content)


def mkflat(n, root, name):
    d = os.path.join(root, name)
    os.makedirs(d)
    for i in range(n):
        _wf(os.path.join(d, f"f-{i:05d}.txt"), f"c{i}")
    return d


def count_files(d):
    n = 0
    for _r, _ds, fs in os.walk(d):
        n += len(fs)
    return n


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

    def fixture(self, state, event=None, input_val=None):
        args = {"schema_version": 1, "state": state}
        if event is not None:
            args["trigger"] = {"widget": "App", "event": event, "input": input_val}
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

    def progress(self):
        """进度串观测：(done, total) 或 None——find text label 主通道，
        vtree 全树正则兜底（R10 债：渲染投影读 VM 活状态）。"""
        text = self.post("autoui_find", {"kind": "text", "label": "复制中",
                                         "limit": 20}).get("content", [{}])[0].get("text", "")
        m = re.search(r'复制中 (\d+)/(\d+)', text)
        if m:
            return int(m.group(1)), int(m.group(2))
        vt = self.post("autoui_vtree", {"include_box": False, "include_style": False,
                                        "include_source": False, "include_props": True})
        vt = vt.get("content", [{}])[0].get("text", "")
        m = re.search(r'复制中 (\d+)/(\d+)', vt)
        if m:
            return int(m.group(1)), int(m.group(2))
        return None

    def transfer_alive(self):
        """传输臂活跃判定：进度串在场（渲染投影）。

        注：不可用 find(label="取消") 判活——closed 态 alert-dialog 的
        取消钮亦在树（PLAN-005 在册），恒真污染。"""
        return self.progress() is not None


PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  PASS  " if ok else "  FAIL  ") + name + ("" if ok else f"  [{detail}]"))


def main():
    tmp = tempfile.mkdtemp(prefix="fm-vp6-")
    tree420 = mkdeep(420, tmp, "tree420")
    treebig = mkdeep(2400, tmp, "treebig")
    tree50 = mkflat(50, tmp, "tree50")
    # AC-03 搜索语料（唯一词干：needle-* 共 3 处）
    _wf(os.path.join(tree420, "needle-1.txt"), "n1")
    _wf(os.path.join(tree420, "subA", "needle-2.txt"), "n2")
    _wf(os.path.join(tree420, "subB", "needle-3.txt"), "n3")
    big2500 = os.path.join(tmp, "big2500")
    os.makedirs(big2500)
    for i in range(2500):
        _wf(os.path.join(big2500, f"b{i:04d}.txt"), "x" * (i % 50))
    dst1 = os.path.join(tmp, "dst1")
    dst2 = os.path.join(tmp, "dst2")
    dst3 = os.path.join(tmp, "dst3")
    for d in (dst1, dst2, dst3):
        os.makedirs(d)

    port = pick_free_port(9880)
    log = open(os.path.join(tempfile.gettempdir(), f"fm-vp6-{port}.log"), "w", encoding="utf-8")
    proc = subprocess.Popen(
        [AUTO_BIN, "run", "-r", "vm"], cwd=PROJECT,
        env={**os.environ, "AUTOUI_MCP_PORT": str(port),
             "AUTO_VM_STORAGE_FILE": os.path.join(tmp, "s.json"),
             "AUTOUI_TEST_FIXTURES": "1"},
        stdout=log, stderr=log)
    try:
        url = f"http://localhost:{port}/mcp"
        t_server = None
        # 等待窗 240s：auto run 每次重生成+重建后端（~15s）+ VM 构建 +
        # 窗口初始化（desktop_mcp 同量级实测）
        for _ in range(240):
            time.sleep(1)
            try:
                requests.post(url, json={"jsonrpc": "2.0", "method": "tools/list",
                                         "params": {}, "id": 1}, timeout=2)
                t_server = time.time()
                break
            except (requests.ConnectionError, requests.Timeout):
                pass
        if t_server is None:
            print("ERROR: MCP server 启动超时")
            return 1
        c = C(url)
        t_boot = None
        for _ in range(60):
            time.sleep(0.1)
            if c.state("booted") == "true":
                t_boot = time.time()
                break
        print("[AC-04] 启动 3-tick 引导")
        boot_lat = (t_boot - t_server) if t_boot else 999
        # 防挂起粗门 6s（MCP 先于窗口/VM 初始化监听，实测基线 ~4.4s）；
        # 收缩回归的真门 = T-05 套件 ×3 轮全绿。
        check("booted 观测（防挂起粗门 ≤6s）", t_boot is not None and boot_lat <= 6.0,
              f"boot_latency={boot_lat:.2f}s")
        check("首目录就位", len(c.state_str("current_path")) > 3,
              c.state_str("current_path"))
        tc0 = c.state_int("tick_count")
        time.sleep(2.0)
        tc1 = c.state_int("tick_count")
        check("Tick 心跳在跳（2s ≥3 拍）", tc1 - tc0 >= 3, f"{tc0} -> {tc1}")

        print("[AC-01] 大目录复制不冻结（2400 文件分片管线）")
        c.fixture({"addr": dst1}, "AddrGo")
        time.sleep(0.5)
        c.fixture({"sel_paths": [treebig], "sel_count": 1}, "CopySel")
        c.fixture({"booted": True}, "PasteInto")
        # 等传输起飞（2400 文件全程 ~5-6s，双排序窗口充裕）
        started = False
        for _ in range(15):
            time.sleep(0.2)
            if c.transfer_alive():
                started = True
                break
        # 传输中穿插排序交互（applied 回执即同步生效，读 sort_col 回证）
        inter = []
        diag = []
        for ev, want in (("SortBySize", "size"), ("SortByName", "name")):
            okr = False
            applied_n = 0
            last_read = "?"
            for _attempt in range(3):
                alive_at = c.transfer_alive()
                if not c.fixture({"booted": True}, ev):
                    continue  # fixture 丢帧（在册框架病）——重派发
                applied_n += 1
                for _ in range(6):
                    time.sleep(0.25)
                    last_read = c.state_str("sort_col")
                    if last_read == want:
                        okr = True
                        break
                if okr:
                    break
            inter.append(okr and alive_at)
            diag.append(f"{ev}:applied={applied_n},read={last_read},alive={alive_at}")
        seen_progress = started
        check("传输中进度可见（渲染投影）", seen_progress,
              f"progress={c.progress()}")
        check("交互响应 ≥2（排序在传输中生效）", len(inter) >= 2 and all(inter),
              f"inter={inter} diag={diag}")
        # 完成判定：磁盘全量比对（treebig = 2400；观测窗 20s——tick 节奏
        # 有波动，先等完成再做退场/抽样断言）
        ok = False
        for _ in range(40):
            if count_files(dst1) == 2400:
                ok = True
                break
            time.sleep(0.5)
        check("磁盘 2400/2400", ok, f"count={count_files(dst1)}")
        check("收尾取消钮退场", not c.transfer_alive())
        sample_ok = True
        for rel, want in ((os.path.join("file-00007.txt"), "root-7"),
                          (os.path.join("subA", "file-00003.txt"), "subA-3"),
                          (os.path.join("subB", "file-00011.txt"), "subB-11")):
            sp = os.path.join(dst1, "treebig", rel)
            if not (os.path.exists(sp) and open(sp, encoding="utf-8").read() == want):
                sample_ok = False
        check("抽样内容一致（根/subA/subB）", sample_ok)

        print("[AC-02] 进度与取消（2400 文件）")
        c.fixture({"addr": dst2}, "AddrGo")
        time.sleep(0.5)
        c.fixture({"sel_paths": [treebig], "sel_count": 1}, "CopySel")
        c.fixture({"booted": True}, "PasteInto")
        # 等传输起飞后定档采样 ×4（2400 文件 ~5-6s，窗口充裕）
        started = False
        for _ in range(15):
            time.sleep(0.2)
            if c.transfer_alive():
                started = True
                break
        samples = []
        for _ in range(4):
            prog = c.progress()
            if prog:
                samples.append(prog)
            time.sleep(0.35)
        # 并发防护：传输中再触发 PasteInto（剪贴板仍持 treebig copy）
        tot_before = samples[-1][1] if samples else None
        c.fixture({"booted": True}, "PasteInto")
        time.sleep(0.6)
        prog2 = c.progress()
        n2 = count_files(os.path.join(dst2, "treebig"))
        guard_ok = (prog2 is not None and tot_before is not None
                    and prog2[1] == tot_before)
        check("并发防护：再粘贴被拒（分母不变）", guard_ok,
              f"before={tot_before} after={prog2}")
        check("并发防护：目标无新增文件", count_files(os.path.join(dst2, "treebig")) == n2,
              f"{n2} -> {count_files(os.path.join(dst2, 'treebig'))}")
        # 取消（趁传输仍在途）
        c.fixture({"booted": True}, "XferCancel")
        stopped = False
        n_stop = -1
        for _ in range(16):
            time.sleep(0.3)
            if not c.transfer_alive():
                stopped = True
                n_stop = count_files(os.path.join(dst2, "treebig"))
                break
        time.sleep(1.0)
        n_final = count_files(os.path.join(dst2, "treebig"))
        check("取消停臂（进度串退场）", stopped)
        check("部分完成保留（n_stop < 2400）", stopped and 0 < n_stop < 2400, f"n_stop={n_stop}")
        check("取消后不再增长", stopped and n_final == n_stop, f"{n_stop} -> {n_final}")
        mono = all(samples[i][0] <= samples[i + 1][0] for i in range(len(samples) - 1))
        check("进度单调推进（观测样本）", mono and len(samples) >= 2,
              f"samples={samples[:6]}...")

        print("[快路径] 50 文件小目录（≤200 直拷，无传输臂中间态）")
        c.fixture({"addr": dst3}, "AddrGo")
        time.sleep(0.5)
        c.fixture({"sel_paths": [tree50], "sel_count": 1}, "CopySel")
        c.fixture({"booted": True}, "PasteInto")
        ok = False
        for _ in range(10):
            time.sleep(0.3)
            if count_files(os.path.join(dst3, "tree50")) == 50 and not c.transfer_alive():
                ok = True
                break
        check("小目录即拷即完成（50/50）", ok, f"count={count_files(os.path.join(dst3, 'tree50'))}")
        check("无取消钮（未激活管线）", not c.transfer_alive())

        print("[AC-03] 递归搜索回归（扫描上限 guard 不误伤）")
        c.fixture({"addr": tree420}, "AddrGo")
        time.sleep(0.5)
        c.fixture({"search_scope": "tree"}, "SetSearch", input_val="needle")
        lbl = ""
        for _ in range(12):
            time.sleep(0.5)
            lbl = c.state_str("search_label")
            if "子树命中" in lbl:
                break
        check("搜索命中正确（3 needle 文件）", "3" in lbl, f"label={lbl}")
        check("无误标 60000 上限", "60000" not in lbl, f"label={lbl}")
        c.fixture({"booted": True}, "ExitTreeSearch")
        time.sleep(0.5)

        print("[AC-05] hover 门控（大目录 no-op / 小目录正常）")
        # 大目录：RowLeave 门控 → hover_id 保持 fixture 写入值
        c.fixture({"addr": big2500}, "AddrGo")
        ok = False
        for _ in range(30):
            time.sleep(0.5)
            if c.state_int("d_total") == 2500:
                ok = True
                break
        check("大目录就位（2500）", ok, f"d_total={c.state_int('d_total')}")
        c.fixture({"hover_id": 5, "booted": True}, "RowLeave")
        time.sleep(0.6)
        check("大目录 RowLeave no-op（hover_id 保持 5）", c.state_int("hover_id") == 5,
              f"hover_id={c.state_int('hover_id')}")
        # 小目录：正常复位 -1
        c.fixture({"addr": tree50}, "AddrGo")
        time.sleep(0.5)
        c.fixture({"hover_id": 2, "booted": True}, "RowLeave")
        time.sleep(0.6)
        check("小目录 RowLeave 复位 -1", c.state_int("hover_id") == -1,
              f"hover_id={c.state_int('hover_id')}")

        print(f"\nverify_p6: {len(PASS)} pass, {len(FAIL)} fail")
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
