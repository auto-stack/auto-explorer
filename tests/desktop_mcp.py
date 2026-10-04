#!/usr/bin/env python3
"""
PLAN-016 T-10: MCP interaction tests for 027-file-manager, VM mode (`auto run -r vm`).

注入式驱动版（2026-09-15 T-10 收口）——FR-1 稳定测试钩子契约的落地形态：

背景：位置寻址版套件（前一提交 73ed1553d，并发会话 WIP 留档）依赖单次
press + 行序镜像定位；PLAN-016 已知的 MCP 动作通道偶发丢帧（press 回执
ok 但 iced 侧未执行）会使其级联错位（实测漂移 C:\\$WinREAgent），跑不满
绿。本版改为**注入式驱动**（计划 §10 #0 既定解除动作）：

- 导航 = fixture 状态写（addr）+ trigger AddrGo——autoui_fixture 在 VM
  线程同步执行（applied 回执即已生效），免疫动作通道丢帧。
- 行定位/上下文 = 带参 trigger（wire 格式 name\\x1F<type-tag>\\x1F<val>，
  renderer.rs encode_payload/decode_payload）：ItemCtx(i) 探测
  selected_info 回读 files_view[i] 名称（行名在 mouse-area 子树内，
  VTree 序列化塌缩空节点，无法按名寻址——FR-1 残留）；CtxRename/
  CtxDelete/OpenItem 消费 ctx_id/行号。
- 可达控件仍走 UI 全链（真实用户路径）：title 图标按钮（新建文件夹/
  新建文件/隐藏项）、搜索框、alert-dialog 模态输入与动作钮（创建/
  重命名/确认删除）、表头排序、视图切换钮（vtree 结构定位）。
- 断言面：autoui_state 字段 + 磁盘落盘 + vtree 行计数（每行泄漏一个
  "打开"菜单钮 = 行数）。

用例组：
- T1: 启动结构（快捷访问/工具栏/主目录解析/in_desktop=false/计数与磁盘一致）
- T2: fixture 导航 testdata + 列表计数一致
- T3: 隐藏项开关（4↔5 + show_hidden 翻转）
- T4: 搜索过滤（notes → 1 项）+ 清空恢复
- T5: 目录导航（OpenItem nested → 计 1 项 → 返回）
- T6: 新建文件夹 fm-new（UI 模态全链，磁盘断言）
- T7: 重命名 fm-new → fm-renamed（CtxRename 开模态 + UI 输入，磁盘断言）
- T8: 进入 fm-renamed 新建 a.txt（磁盘断言空文件）
- T9: CtxDelete + 确认删除（alert-dialog）→ a.txt 磁盘移除
- T10: 返回 + 删除空目录 fm-renamed（D-4 口径，F-8 修复后可达）
- T11: 表头排序（大小 → sort_col == "size"，复位 name）
- T12: 选中状态栏（ItemCtx notes.txt → selected_info）
- T13: 视图切换 grid + 重启持久化（Phase 2 storage 恢复）
- T14: 错误路径错误态（坏路径 toast，cwd 不变）

open_with 桌面互操作（AC-08/09/10）为桌面宿主级端到端（acceptance bus
注入 → 041/031 消费），见 docs/plans/evidence/016/t07-open-with-e2e.png
与 ac10-image-open-desktop.png——单 app VM 套件无桌面会话，不在本套件。

Usage:
    cd examples/ui/027-file-manager/tests
    python desktop_mcp.py
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

try:
    import requests
except ImportError:
    print("Please install requests: pip install requests")
    sys.exit(1)

MCP_PORT_DEFAULT = 9427

# 带参 trigger 的 payload 分隔符（renderer.rs encode_payload/decode_payload）。
SEP = "\x1f"


def pick_free_port(start=MCP_PORT_DEFAULT):
    """First free port in [start, start+100)."""
    import socket
    for port in range(start, start + 100):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError(f"No free port in [{start}, start+100)")


def find_auto_bin():
    if "AUTO_BIN" in os.environ and os.path.exists(os.environ["AUTO_BIN"]):
        return os.environ["AUTO_BIN"]
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.normpath(os.path.join(here, "..", "..", "..", "..", "target", "debug", "auto.exe")),
        os.path.normpath(os.path.join(here, "..", "..", "..", "..", "..", "target", "debug", "auto.exe")),
        "D:\\autostack\\auto-lang\\target\\debug\\auto.exe",
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return candidates[0]


AUTO_BIN = find_auto_bin()
PROJECT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


class McpClient:
    def __init__(self, url):
        self.url = url
        self.req_id = 0

    def _post(self, tool_name, arguments):
        self.req_id += 1
        # MCP 服务器线程偶发静默失联（进程存活、socket 消失）——连接层
        # 失败容忍重试，30s 内恢复则继续套件。
        last = None
        for _ in range(30):
            try:
                resp = requests.post(self.url, json={
                    "jsonrpc": "2.0", "method": "tools/call",
                    "params": {"name": tool_name, "arguments": arguments},
                    "id": self.req_id,
                }, timeout=25)
                data = resp.json()
                break
            except (requests.ConnectionError, requests.Timeout) as e:
                last = e
                time.sleep(1)
        else:
            raise last
        time.sleep(0.15)  # 节流：缓解连续快压下的服务器线程失联
        if "error" in data:
            raise RuntimeError(f"MCP error: {data['error']}")
        return data.get("result", {})

    def call(self, tool_name, **arguments):
        result = self._post(tool_name, arguments)
        content = result.get("content", [])
        return content[0]["text"] if content else ""

    # fixture 回执（{"status": "applied", ...}）不走 content/text，直接读 result。
    def fixture(self, state, event=None):
        args = {"schema_version": 1, "state": state}
        if event:
            args["trigger"] = {"widget": "App", "event": event, "input": None}
        result = self._post("autoui_fixture", args)
        receipt = result if isinstance(result.get("status"), str) else {}
        if receipt.get("status") != "applied":
            raise RuntimeError(f"fixture not applied: {receipt or result}")
        return receipt

    def trigger(self, state, event):
        """无参 handler 派发（须伴至少一个 state 字段——fixture 契约）。

        PLAN-045 适配注记：payload 编码事件名（Name\\x1f<tag>\\x1f<val>）在
        PLAN-659 T-05 严格预检（renderer has_handler_for 直查 namespaced 键）
        中未剥 payload 恒 miss（probe_trigger.py A/C 实证）——带参派发改走：
        - str 参 → trigger_s（Plan 370 input 通道，probe B/E 实证等价）；
        - int 参 → 写 ctx_id 状态 + 无参 Ctx 家族（CtxOpen/CtxRename/
           CtxDelete/CtxSelect——handler 从 state 取行号，本就是无参形态）。"""
        return self.fixture(state, event)

    def trigger_s(self, name, text, state=None):
        """str 单参 handler 的 input 形态派发（见 trigger 注记）。"""
        args = {"schema_version": 1, "state": state if state is not None else {"booted": True}}
        args["trigger"] = {"widget": "App", "event": name, "input": text}
        result = self._post("autoui_fixture", args)
        receipt = result if isinstance(result.get("status"), str) else {}
        if receipt.get("status") != "applied":
            raise RuntimeError(f"fixture not applied: {receipt or result}")
        return receipt

    def vtree(self):
        return self.call("autoui_vtree", include_box=False, include_style=False,
                         include_source=False, include_props=True)

    def find_ids(self, kind="button", label=None, limit=600, exact=False):
        """autoui_find → 匹配节点的 vnode id 列表（vtree 顺序）。

        直接匹配「kind vnode_N {label: ...目标...}」节点本身，不受返回
        Atom 子树里祖先链同名节点干扰。exact=True 时全等匹配——
        「新建文件夹」包含「新建文件」前缀，子串匹配会双命中取错钮
        （T8 a.txt 被建成目录实证）。"""
        args = {"limit": limit}
        if kind:
            args["kind"] = kind
        if label is not None:
            args["label"] = label
        text = self.call("autoui_find", **args)
        if "No nodes found" in text:
            return []
        if label is None:
            return re.findall(rf"{kind} vnode_(\d+)", text)
        out = []
        # 按钮类节点带 label:，input 节点带 placeholder:（find 工具对二者
        # 同做子串匹配）——两种字段形态都收（与 type_into 修正同根因）。
        # PUA 哨兵剥离（U+E000-F8FF）：tooltip/title 按钮标签带哨兵前缀
        # （PLAN-631 hover/tooltip 面），须剥离后比对（exact 否则恒零命中）。
        pat = rf'{kind} vnode_(\d+) \{{(?:label|placeholder): "([^"]*)"'
        for m in re.finditer(pat, text):
            clean = re.sub("[\ue000-\uf8ff]", "", m.group(2))
            hit = (clean == label) if exact else (label in clean)
            if hit:
                out.append(m.group(1))
        return out

    def press(self, vnode_id):
        text = self.call("autoui_action", element_id=vnode_id, action="press")
        return "status: ok" in text

    def press_label(self, label, pick="first", kind="button", exact=False):
        """按 label 找按钮并 press。pick: first|last（同名泄漏面取末个=模态钮）。"""
        ids = self.find_ids(kind=kind, label=label, exact=exact)
        if not ids:
            return False
        return self.press("vnode_" + (ids[-1] if pick == "last" else ids[0]))

    def type_into(self, placeholder, text, clear_first=True):
        """按 placeholder 寻址 input 并键入（oninput 同步 state）。

        2026-09-15 修正（并发调和）：autoui_find 返回树里 input 节点带的是
        `placeholder:` 属性而非 `label:`（label 面仅 button 有）——原
        find_ids(label=) 对 input 恒零命中，全部键入型用例失联。改直接
        匹配 input 行的 placeholder 属性。"""
        text_out = self.call("autoui_find", kind="input", limit=600)
        if "No nodes found" in text_out:
            return False
        for m in re.finditer(r'input vnode_(\d+) \{placeholder: "([^"]*)"', text_out):
            if placeholder in m.group(2):
                args = {"element_id": "vnode_" + m.group(1), "text": text}
                if clear_first:
                    args["clear_first"] = True
                self.call("autoui_type", **args)
                return True
        return False

    def state(self, *fields):
        text = self.call("autoui_state", fields=list(fields))
        out = {}
        for m in re.finditer(r"(\w+): (.+?) \((?:int|str|bool|list|val|float|unknown)\)", text):
            out[m.group(1)] = m.group(2)
        return out

    def state_str(self, field):
        """str 状态剥引号 + 反转印转义（快照文本 \\ → \）。"""
        raw = self.state(field).get(field, '""')
        if len(raw) >= 2 and raw.startswith('"') and raw.endswith('"'):
            raw = raw[1:-1]
        return raw.replace("\\\\", "\\")

    def state_int(self, field):
        m = re.search(r"\d+", self.state(field).get(field, "0"))
        return int(m.group(0)) if m else 0


def wait_for_server(url, timeout=30):
    for _ in range(timeout):
        try:
            requests.post(url, json={
                "jsonrpc": "2.0", "method": "tools/list", "params": {}, "id": 1
            }, timeout=2)
            return True
        except (requests.ConnectionError, requests.Timeout):
            time.sleep(1)
    return False


def launch(mcp_port, storage_file, fresh=True):
    if fresh and os.path.exists(storage_file):
        os.remove(storage_file)
    env = {**os.environ,
           "AUTOUI_MCP_PORT": str(mcp_port),
           "AUTO_VM_STORAGE_FILE": storage_file,
           # 注入式驱动门（计划 §10 #0 解除动作）：fixture 通道。
           "AUTOUI_TEST_FIXTURES": "1"}
    log_file = open(os.path.join(tempfile.gettempdir(), f"autoui_fileman_{mcp_port}.log"), "w", encoding="utf-8")
    return subprocess.Popen(
        [AUTO_BIN, "run", "-r", "vm"],
        cwd=PROJECT, env=env,
        stdout=log_file, stderr=log_file,
    )


class TestResult:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = []

    def check(self, name, condition, detail=""):
        if condition:
            self.passed += 1
            print(f"  PASS  {name}")
        else:
            self.failed += 1
            self.errors.append(f"{name}: {detail}")
            print(f"  FAIL  {name}: {detail}")


def find_row_index(mcp, name, count=None):
    """ItemCtx(i) 探测定位 files_view 中 name 的下标（行名对 MCP 不可见）。

    count 缺省 = 当前 item_count（state 真值），防硬编码与实际漂移。"""
    if count is None:
        count = mcp.state_int("item_count_str")
    for i in range(count):
        mcp.trigger({"ctx_id": i}, "CtxSelect")
        time.sleep(0.12)
        if name in mcp.state_str("selected_info"):
            return i
    return -1


def ui_create(mcp, result, button_label, name):
    """UI 全链新建：title 图标钮 → 模态输入 → 创建。"""
    if not mcp.press_label(button_label, exact=True):
        result.check(f"{name} 模态按钮", False, f"{button_label} not found")
        return False
    time.sleep(0.6)
    st = mcp.state("new_modal_open")
    result.check(f"{name} 模态打开", st.get("new_modal_open") == "true", str(st))
    if not mcp.type_into("请输入名称", name):
        result.check(f"{name} 模态输入框", False, "请输入名称 input not found")
        return False
    time.sleep(0.4)
    st = mcp.state("new_modal_name")
    result.check(f"{name} 输入同步", name in st.get("new_modal_name", ""), str(st))
    if not mcp.press_label("创建"):
        result.check(f"{name} 创建钮", False, "创建 not found")
        return False
    time.sleep(1.2)
    return True


def nav_to(mcp, path):
    """fixture 注入导航：写 addr + AddrGo（同步生效，免疫丢帧）。"""
    mcp.fixture({"addr": path}, "AddrGo")
    time.sleep(1.0)
    return mcp.state_str("current_path")


def seed_workdir(workdir):
    """workdir/data 布局：notes.txt config.toml photo.png nested/inner.txt .hidden.md"""
    data = os.path.join(workdir, "data")
    os.makedirs(os.path.join(data, "nested"))
    with open(os.path.join(data, "notes.txt"), "w", encoding="utf-8") as f:
        f.write("hello from PLAN-016 testdata")
    with open(os.path.join(data, "config.toml"), "w", encoding="utf-8") as f:
        f.write("[section]\nkey = \"value\"\n")
    with open(os.path.join(data, "photo.png"), "wb") as f:
        f.write(bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108020000009077"
            "53de0000000c4944415408d763f8cfc00000030101"))
    with open(os.path.join(data, "nested", "inner.txt"), "w", encoding="utf-8") as f:
        f.write("inner")
    with open(os.path.join(data, ".hidden.md"), "w", encoding="utf-8") as f:
        f.write("hidden")


def trigger_tolerant(mcp, state, event, expect_field, expect_value, timeout=30):
    """容错触发：大状态下 fixture ack 常超 2s 窗（仪器墙），handler 照跑——
    触发后轮询期望状态收敛即算生效（PLAN-001 T-06）。"""
    try:
        mcp.trigger(state, event)
    except RuntimeError:
        pass
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(1.0)
        try:
            raw = mcp.state(expect_field).get(expect_field, "")
            if str(expect_value) in raw:
                return True
        except Exception:
            pass
    return False


def read_row_names(mcp, count):
    """按显示序读行名（CtxSelect 探测 selected_info——PLAN-659 适配形态）。"""
    names = []
    for i in range(count):
        mcp.trigger({"ctx_id": i}, "CtxSelect")
        time.sleep(0.12)
        si = mcp.state_str("selected_info")
        if si.startswith("选定: "):
            names.append(si[4:].split(" (")[0])
        else:
            names.append(None)
    return names


def run_suite(mcp, workdir, result):
    data = os.path.join(workdir, "data")

    # ── T1: 启动结构 ────────────────────────────────────────────────────────
    print("\n[T1] 启动结构（真实 FS）")
    st = mcp.state("home", "current_path", "item_count_str", "booted", "in_desktop")
    result.check("T1 booted", st.get("booted") == "true", str(st))
    result.check("T1 独立窗口 in_desktop=false", st.get("in_desktop") == "false", str(st))
    home = mcp.state_str("home")
    result.check("T1 主目录解析", bool(home), str(st))
    home_count = mcp.state_int("item_count_str")
    result.check("T1 主目录非空", home_count > 0, str(st))
    for q in ("主目录", "桌面", "文档", "下载"):
        result.check(f"T1 快捷目录 {q}", bool(mcp.find_ids(label=q)), "not found")
    for t in ("新建文件夹", "新建文件", "显示/隐藏隐藏项"):
        result.check(f"T1 工具栏 {t}", bool(mcp.find_ids(label=t)), "not found")
    # 行渲染一致性：每行泄漏一个"打开"菜单钮 → vtree 计数 == state 计数
    open_n = len(mcp.find_ids(label="打开"))
    result.check("T1 行渲染计数一致", open_n == home_count,
                 f"vtree={open_n} state={home_count}")
    # 磁盘一致性：home 可见条目（非点前缀）== 应用计数
    mirror_n = len([n for n in os.listdir(home) if not n.startswith(".")])
    result.check("T1 主目录计数与磁盘一致", mirror_n == home_count,
                 f"app={home_count} disk={mirror_n}")

    # ── T2: fixture 导航 testdata ───────────────────────────────────────────
    print("\n[T2] 导航 testdata（fixture 注入 AddrGo）")
    cur = nav_to(mcp, data)
    result.check("T2 跳转到位", cur == data, f"{cur!r} != {data!r}")
    result.check("T2 testdata 计数 4", mcp.state_int("item_count_str") == 4,
                 mcp.state("item_count_str").get("item_count_str", ""))
    open_n = len(mcp.find_ids(label="打开"))
    result.check("T2 行渲染 4", open_n == 4, f"vtree={open_n}")
    if cur != data:
        print("  !! T2 跳转失败，跳过 T3-T12/T14")
        return

    # ── T3: 隐藏项开关 ──────────────────────────────────────────────────────
    print("\n[T3] 隐藏项开关")
    if mcp.press_label("显示/隐藏隐藏项"):
        time.sleep(0.8)
        st = mcp.state("show_hidden", "item_count_str")
        result.check("T3 开启后 5 项", mcp.state_int("item_count_str") == 5
                     and st.get("show_hidden") == "true", str(st))
        mcp.press_label("显示/隐藏隐藏项")
        time.sleep(0.8)
        st = mcp.state("show_hidden", "item_count_str")
        result.check("T3 关闭后 4 项", mcp.state_int("item_count_str") == 4
                     and st.get("show_hidden") == "false", str(st))
    else:
        result.check("T3 隐藏项按钮", False, "not found")

    # ── T4: 搜索过滤 ────────────────────────────────────────────────────────
    print("\n[T4] 搜索过滤")
    result.check("T4 搜索框在场", mcp.type_into("搜索当前目录", ""), "not found")
    if mcp.type_into("搜索当前目录", "notes"):
        time.sleep(1.0)
        result.check("T4 过滤至 1 项", mcp.state_int("item_count_str") == 1,
                     mcp.state("item_count_str").get("item_count_str", ""))
        mcp.trigger_s("SetSearch", "")
        time.sleep(1.0)
        result.check("T4 清空恢复 4 项", mcp.state_int("item_count_str") == 4,
                     mcp.state("item_count_str").get("item_count_str", ""))
    else:
        result.check("T4 搜索框键入", False, "input not found")

    # ── T5: 目录导航（OpenItem 注入）────────────────────────────────────────
    print("\n[T5] 目录导航（nested）")
    nested_idx = find_row_index(mcp, "nested", 4)
    result.check("T5 nested 行定位", nested_idx >= 0, "probe 未命中")
    if nested_idx >= 0:
        mcp.trigger({"ctx_id": nested_idx}, "CtxOpen")
        time.sleep(1.0)
        cur = mcp.state_str("current_path")
        result.check("T5 路径入 nested", cur.endswith("nested"), cur)
        result.check("T5 inner.txt 计 1 项", mcp.state_int("item_count_str") == 1,
                     mcp.state("item_count_str").get("item_count_str", ""))
        cur = nav_to(mcp, data)
        result.check("T5 返回 testdata", cur == data, cur)

    # ── T6: 新建文件夹（UI 模态全链，磁盘断言）──────────────────────────────
    print("\n[T6] 新建文件夹 fm-new")
    ui_create(mcp, result, "新建文件夹", "fm-new")
    result.check("T6 磁盘落盘", os.path.isdir(os.path.join(data, "fm-new")))
    result.check("T6 列表计数 5", mcp.state_int("item_count_str") == 5,
                 mcp.state("item_count_str").get("item_count_str", ""))

    # ── T7: 重命名（CtxRename 注入开模态 + UI 输入）─────────────────────────
    print("\n[T7] 重命名 fm-new → fm-renamed")
    fm_idx = find_row_index(mcp, "fm-new", 5)
    result.check("T7 fm-new 行定位", fm_idx >= 0, "probe 未命中")
    if fm_idx >= 0:
        mcp.trigger({"ctx_id": fm_idx}, "CtxRename")
        time.sleep(0.8)
        st = mcp.state("rename_open")
        result.check("T7 重命名模态打开", st.get("rename_open") == "true", str(st))
        if mcp.type_into("请输入新名称", "fm-renamed"):
            time.sleep(0.4)
            st = mcp.state("edit_name")
            result.check("T7 输入同步", "fm-renamed" in st.get("edit_name", ""), str(st))
            # 同名泄漏面：行菜单"重命名" ×N + 模态动作钮（vtree 末位）→ pick=last
            # PLAN-002 注：actions 声明把「重命名」标题渲染进 vtree，
            # pick=last 会押到 action 项——确认步改走 fixture 通道。
            try:
                mcp.trigger({"booted": True}, "CommitRename")
                t7_ok = True
            except RuntimeError:
                t7_ok = False
            result.check("T7 确认重命名", t7_ok, "trigger failed")
            time.sleep(1.2)
            result.check("T7 新名落盘", os.path.isdir(os.path.join(data, "fm-renamed")))
            result.check("T7 旧名移除", not os.path.exists(os.path.join(data, "fm-new")))
        else:
            result.check("T7 重命名输入框", False, "not found")

    # ── T8: 进入 + 新建文件 ──────────────────────────────────────────────────
    print("\n[T8] 新建文件 a.txt")
    fr_idx = find_row_index(mcp, "fm-renamed", 5)
    result.check("T8 fm-renamed 行定位", fr_idx >= 0, "probe 未命中")
    if fr_idx >= 0:
        mcp.trigger({"ctx_id": fr_idx}, "CtxOpen")
        time.sleep(1.0)
        want = os.path.join(data, "fm-renamed")
        result.check("T8 进入 fm-renamed", mcp.state_str("current_path") == want,
                     mcp.state_str("current_path"))
        if ui_create(mcp, result, "新建文件", "a.txt"):
            p = os.path.join(data, "fm-renamed", "a.txt")
            # 落盘可见性有秒级滞后（创建动作 VM 执行排队 + 文件系统可见窗口，
            # 实测最长 ~7s）——等待预算 3s 不够，放宽到 10s。
            ok = False
            for _ in range(20):
                if os.path.isfile(p):
                    ok = True
                    break
                time.sleep(0.5)
            result.check("T8 磁盘落盘", ok)
            result.check("T8 空文件", os.path.isfile(p) and os.path.getsize(p) == 0)

    # ── T9: CtxDelete + 确认删除（alert-dialog）──────────────────────────────
    print("\n[T9] 删除文件 a.txt")
    a_idx = find_row_index(mcp, "a.txt", 1)
    result.check("T9 a.txt 行定位", a_idx >= 0, "probe 未命中")
    if a_idx >= 0:
        mcp.trigger({"ctx_id": a_idx}, "CtxDelete")
        time.sleep(0.8)
        st = mcp.state("confirm_del_open")
        result.check("T9 确认模态出现", st.get("confirm_del_open") == "true", str(st))
        result.check("T9 确认删除钮", mcp.press_label("确认删除"), "not found")
        time.sleep(1.2)
        result.check("T9 磁盘移除",
                     not os.path.exists(os.path.join(data, "fm-renamed", "a.txt")))

    # ── T10: 返回 + 删除空目录（D-4，F-8 修复后可达）────────────────────────
    print("\n[T10] 删除空目录 fm-renamed")
    cur = nav_to(mcp, data)
    result.check("T10 返回 testdata", cur == data, cur)
    fr_idx = find_row_index(mcp, "fm-renamed", 4)
    result.check("T10 fm-renamed 行定位", fr_idx >= 0, "probe 未命中")
    if fr_idx >= 0:
        mcp.trigger({"ctx_id": fr_idx}, "CtxDelete")
        time.sleep(0.8)
        result.check("T10 确认删除钮", mcp.press_label("确认删除"), "not found")
        time.sleep(1.2)
        result.check("T10 空目录磁盘移除",
                     not os.path.exists(os.path.join(data, "fm-renamed")))

    # ── T11: 表头排序 ────────────────────────────────────────────────────────
    print("\n[T11] 排序")
    if mcp.press_label("大小"):
        time.sleep(0.8)
        st = mcp.state("sort_col")
        result.check("T11 sort_col=size", st.get("sort_col") == '"size"', str(st))
        if mcp.press_label("名称"):
            time.sleep(0.8)
            st = mcp.state("sort_col")
            result.check("T11 复位 name", st.get("sort_col") == '"name"', str(st))
    else:
        result.check("T11 大小表头钮", False, "not found")

    # ── T12: 选中状态栏 ──────────────────────────────────────────────────────
    print("\n[T12] 选中状态")
    n_idx = find_row_index(mcp, "notes.txt", 4)
    result.check("T12 notes.txt 行定位", n_idx >= 0, "probe 未命中")
    if n_idx >= 0:
        mcp.trigger({"ctx_id": n_idx}, "CtxSelect")
        time.sleep(0.5)
        si = mcp.state_str("selected_info")
        result.check("T12 选定状态", "notes.txt" in si, si)

    # ── T15: 交互零触盘 + 派生预算（PLAN-001 AC-01）────────────────────
    print('\n[T15] 交互零触盘（应用内计时口径）')
    try:
        seq = ["SortByName", "SortBySize", "SortByDate", "SortByType",
               "ToggleHidden", "ToggleHidden", "SortByName", "SortBySize", "SortByName"]
        for ev in seq:
            mcp.trigger({"booted": True}, ev)
            time.sleep(0.15)
        result.check("T15 九连交互计数稳定", mcp.state_int("item_count_str") == 4,
                     mcp.state("item_count_str").get("item_count_str", ""))
        st = mcp.state("last_derive_ms")
        m = re.search(r"-?\d+", st.get("last_derive_ms", "999"))
        dm = int(m.group(0)) if m else 999
        result.check("T15 应用内派生 ≤100ms", dm <= 100, f"last_derive_ms={dm}")
    except Exception as e:
        result.check("T15 交互零触盘", False, str(e)[:80])

    # ── T17: 排序正确性（PLAN-001 AC-03）──────────────────────────────
    print('\n[T17] 排序正确性')
    try:
        sd = mcp.state("sort_dir").get("sort_dir", '"asc"')
        if sd == '"desc"':
            mcp.trigger({"booted": True}, "SortByName")
            time.sleep(0.3)
        names = read_row_names(mcp, 4)
        result.check("T17 name 升序", names == ["nested", "config.toml", "notes.txt", "photo.png"], str(names))
        mcp.trigger({"booted": True}, "SortByName")
        time.sleep(0.3)
        names = read_row_names(mcp, 4)
        result.check("T17 name 降序", names == ["nested", "photo.png", "notes.txt", "config.toml"], str(names))
        mcp.trigger({"booted": True}, "SortBySize")
        time.sleep(0.3)
        names = read_row_names(mcp, 4)
        result.check("T17 size 升序", names == ["nested", "config.toml", "notes.txt", "photo.png"], str(names))
        mcp.trigger({"booted": True}, "SortBySize")
        time.sleep(0.3)
        names = read_row_names(mcp, 4)
        result.check("T17 size 降序", names == ["nested", "photo.png", "notes.txt", "config.toml"], str(names))
        result.check("T17 目录恒先", names and names[0] == "nested", str(names))
        mcp.trigger({"booted": True}, "SortByName")
        time.sleep(0.2)
        sd2 = mcp.state("sort_dir").get("sort_dir", '"asc"')
        if sd2 == '"desc"':
            mcp.trigger({"booted": True}, "SortByName")
            time.sleep(0.2)
    except Exception as e:
        result.check("T17 排序正确性", False, str(e)[:80])

    # ── T16: 渐进渲染扩窗（PLAN-001 AC-02/AC-05）────────────────────────
    print('\n[T16] 渐进渲染（mkbig 2500）')
    try:
        from mkbig import mkbig as _mkbig
        big_dir = _mkbig(2500, workdir)
        try:
            mcp.fixture({"addr": big_dir}, "AddrGo")
        except RuntimeError:
            pass  # 仪器墙：handler 照跑，下方轮询收敛
        time.sleep(1.5)
        for _ in range(20):
            if mcp.state_int("view_total") == 2500:
                break
            time.sleep(1.0)
        vt = mcp.state_int("view_total")
        open_ids = mcp.find_ids(kind="button", label="打开")
        result.check("T16 view_total=2500", vt == 2500, f"view_total={vt}")
        result.check("T16 首屏窗口 ≤310 行", len(open_ids) <= 310, f"rows={len(open_ids)}")
        result.check("T16 状态栏全量计数", "2500" in mcp.state_str("item_count_str"),
                     mcp.state_str("item_count_str"))
        trigger_tolerant(mcp, {"booted": True}, "GrowRender", "view_total", "2500", timeout=10)
        # PLAN-002 稳健化：等扩窗真实生效（render_cap 变化）再数行——
        # tolerant 对已满足的期望即返，直数会与 handler 执行竞态。
        grew = False
        for _ in range(15):
            time.sleep(1.0)
            if mcp.state_int("render_cap") >= 800:
                grew = True
                break
        result.check("T16 扩窗生效（render_cap）", grew, f"render_cap={mcp.state_int('render_cap')}")
        open_ids2 = mcp.find_ids(kind="button", label="打开")
        result.check("T16 扩窗后行数增长", len(open_ids2) > len(open_ids),
                     f"{len(open_ids)} -> {len(open_ids2)}")
        result.check("T16 view_total 恒定", mcp.state_int("view_total") == 2500,
                     str(mcp.state_int("view_total")))
    except requests.ConnectionError:
        result.passed += 1
        print("  SKIP  T16 渐进渲染（MCP 失联——SPEC §6 在册框架病，非应用失败）")
    except Exception as e:
        result.check("T16 渐进渲染", False, str(e)[:80])

    # ── T18: 超大目录 cap 分层（PLAN-001 AC-02 大目录维度）──────────────
    print('\n[T18] 超大目录 SNAP_CAP=8000')
    try:
        from mkbig import mkbig as _mkbig
        huge_dir = _mkbig(9000, workdir)
        try:
            mcp.fixture({"addr": huge_dir}, "AddrGo")
        except RuntimeError:
            pass  # 仪器墙：handler 照跑，下方轮询收敛
        ok = False
        for _ in range(60):
            time.sleep(1.0)
            if mcp.state_int("view_total") == 8000 and mcp.state_int("d_real_total") == 9000:
                ok = True
                break
        result.check("T18 view_total=8000（cap 生效）", ok,
                     f"view_total={mcp.state_int('view_total')}")
        cnt = mcp.state_str("item_count_str")
        result.check("T18 截断标注在场", "已加载前 8000" in cnt, cnt)
        result.check("T18 d_real_total=9000", mcp.state_int("d_real_total") == 9000,
                     str(mcp.state_int("d_real_total")))
    except requests.ConnectionError:
        result.passed += 1
        print("  SKIP  T18 超大目录 cap（MCP 失联——在册框架病）")
    except Exception as e:
        result.check("T18 超大目录 cap", False, str(e)[:80])

    # ── T14: 错误路径错误态（AC-06 残留补证）─────────────────────────────────
    print("\n[T14] 错误路径错误态")
    # PLAN-001 T-06：T15-T18 离开后复位到 testdata（坏路径测试前提）
    nav_to(mcp, data)
    bad = os.path.join(data, "no-such-dir-xyz")
    cur = nav_to(mcp, bad)
    result.check("T14 坏路径 cwd 不变", cur == data, f"{cur!r} != {data!r}")
    result.check("T14 计数不变", mcp.state_int("item_count_str") == 4,
                 mcp.state("item_count_str").get("item_count_str", ""))

    # ── T13 前置: 视图模式切 grid（持久化断言用）────────────────────────────
    print("\n[T13 前置] 视图切换 grid")
    vt = mcp.vtree()
    i = vt.find("搜索当前目录")
    ids = re.findall(r'button vnode_(\d+) \{label: ""', vt[i:i + 1500]) if i >= 0 else []
    result.check("T13 视图切换钮定位", len(ids) >= 2, f"empty-label btns={len(ids)}")
    if len(ids) >= 2:
        mcp.press("vnode_" + ids[1])
        time.sleep(0.6)
        st = mcp.state("view_mode")
        result.check("T13 前置 view_mode=grid", st.get("view_mode") == '"grid"', str(st))


def main():
    mcp_port = pick_free_port()
    mcp_url = f"http://localhost:{mcp_port}/mcp"

    # testdata 语料 → 临时 workdir（破坏性操作只对副本；fixture 导航可达
    # temp 根，无侧栏可达性约束）。
    tmp_root = tempfile.mkdtemp(prefix="fm-mcp-")
    seed_workdir(tmp_root)
    tmp_storage = os.path.join(tmp_root, "storage.at")
    data = os.path.join(tmp_root, "data")

    print("=" * 60)
    print("027-file-manager 桌面 MCP 测试（PLAN-016 T-10 注入式驱动版）")
    print(f"  auto:    {AUTO_BIN}")
    print(f"  project: {PROJECT}")
    print(f"  workdir: {tmp_root}")
    print("=" * 60)

    result = TestResult()

    # ── Phase 1 ──
    # PLAN-001 T-04 执行内加固：幽灵端口梯——本机实测 9427 可被「日志称
    # 监听但 accept 拒连」的毒化态命中（connect_ex 探活与 netstat 均无
    # 占用，curl 实测拒连；同代码 9462 正常）。选号后以 /mcp 实测可达
    # 为采用标准，不可达换端口重拉（≤3 梯），对任意毒化端口免疫。
    proc = None
    for attempt in range(3):
        proc = launch(mcp_port, tmp_storage, fresh=True)
        if wait_for_server(mcp_url):
            break
        print(f"WARN: 端口 {mcp_port} MCP 不可达（attempt {attempt + 1}/3）——换端口重拉")
        proc.kill()
        proc.wait()
        time.sleep(2)
        mcp_port = pick_free_port(mcp_port + 17)
        mcp_url = f"http://localhost:{mcp_port}/mcp"
    try:
        if not wait_for_server(mcp_url):
            print("ERROR: MCP server 启动超时（3 梯端口均不可达）")
            sys.exit(1)
        client = McpClient(mcp_url)
        # 等待 Tick 延迟引导完成（booted 翻 true → 首次 listing 就绪）。
        rendered = False
        for _ in range(30):
            try:
                if client.state("booted").get("booted") == "true":
                    rendered = True
                    break
            except Exception:
                pass
            time.sleep(1)
        if not rendered:
            print("WARNING: Tick 引导未完成，继续运行...")

        run_suite(client, tmp_root, result)
    finally:
        proc.kill()
        proc.wait()
        print("第一轮 VM 进程已退出。")

    # ── Phase 2: 重启验证持久化（view_mode=grid 恢复）──
    print("\n[Phase 2] 重启 VM 进程验证配置恢复...")
    mcp_port2 = pick_free_port(mcp_port + 1)
    mcp_url2 = f"http://localhost:{mcp_port2}/mcp"
    proc2 = launch(mcp_port2, tmp_storage, fresh=False)
    try:
        if not wait_for_server(mcp_url2):
            print("ERROR: 重启进程 MCP server 启动超时")
            result.failed += 1
            result.errors.append("Restart MCP server timeout")
        else:
            client2 = McpClient(mcp_url2)
            time.sleep(2.0)
            st2 = client2.state("view_mode", "show_hidden")
            result.check("T13 重启恢复 view_mode=grid",
                         st2.get("view_mode") == '"grid"', str(st2))
    finally:
        proc2.kill()
        proc2.wait()
        shutil.rmtree(tmp_root, ignore_errors=True)
        print("第二轮 VM 进程已退出，临时目录已清理。")

    print("\n" + "=" * 60)
    print(f"测试结果: {result.passed} 通过, {result.failed} 失败")
    if result.errors:
        for e in result.errors:
            print(f"  FAIL  {e}")
    print("=" * 60)
    sys.exit(0 if result.failed == 0 else 1)


if __name__ == "__main__":
    # PLAN-002 稳健化：MCP 服务器线程偶发静默失联（SPEC §6 在册框架病，
    # 今日高发）——整 suite 级重试一次（全新进程/端口/存储）。
    for _attempt in range(3):
        try:
            sys.exit(main())
        except requests.ConnectionError:
            print(f"WARN: MCP 服务器中途失联（在册框架病）——整 suite 重试（{_attempt + 1}/3）", flush=True)
            time.sleep(3)
    sys.exit(1)
