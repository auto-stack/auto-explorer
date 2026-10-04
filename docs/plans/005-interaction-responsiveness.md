---
plan_id: PLAN-005
status: drafting               # drafting → executing → execution_done → reviewed → archived
feature_name: fm-interaction-responsiveness
author: [agent]
created_at: 2026-10-04
updated_at: 2026-10-04
plan_revision: 1
current_step: 0
total_steps: 5

# /auto-plan:review 结束时填写：
supersedes_spec_components: []
new_spec_components: []       # 预挂 SD-0051（SPEC §1.10 修订：自适应内联）
touched_goals: []

affects: [src/front/app.at, src/front/components/fs_util.at, tests/]
---

# [PLAN-005] fm-interaction-responsiveness —— 交互响应优化

v0.7 交付后用户实测反馈：**目录切换有可感卡顿**。诊断结论（2026-10-04
会话内实测，证据在档）：.at 逻辑层非瓶颈（快照+派发应用内 <1ms @
6000 项）；卡顿主体 = ① PLAN-004 预算拆链引入的**每次导航固定
0~250ms Tick 延迟**（`pending_refresh` 等下一拍——为 8k+ 目录的 10M
指令预算墙上的全局保险，代价落在所有导航）＋ ② 渲染层控件爆炸
（300 行窗口 × 每行 ~20-25 控件，**每行一个含 7 菜单按钮的 popover
（闭合态也在视图树**——套件以「打开」按钮计数行数即为此证）≈ 6-8k
控件，hover 一个状态位即全量重建）。

## 变更摘要

三刀：① **自适应内联派发**——`d_total ≤ INLINE_CAP(4000)` 时 NavTo/
RunTreeSearch 直接内联 `RefreshView()`（恢复 004 之前的已证行为：预算
墙实测在 8000+ 链才触发，4000 内联合链余量充足），仅超大目录保留
pending_refresh 保险；② **首屏窗口 300 → 120**（`render_cap` 初值，
大目录首屏构建量 -60%，哨兵扩窗不变）；③ **popover 菜单内容条件化**
（content 内 7 按钮仅在该行 `ctx_id == item.id` 打开态挂载——闭合行
只留锚钮；套件行计数断言从「打开」按钮改数行 checkbox）。附带
`last_nav_ms` 应用内导航全程计时仪器（NavTo 起 → 派发物化止）。

## 目标

1. 普通目录（≤4000 项）点击→列表更新**零 Tick 延迟**：应用内
   `last_nav_ms ≤ 50ms`（含快照+派发+物化；实测基线 <5ms，预算充足）。
2. 超大目录（>4000）保持预算拆链保险：9000→cap 8000 链不回归
   （T18 口径不变）。
3. 大目录首屏渲染 ≤130 行（120 窗口 + 表头/哨兵）；扩窗与 view_total
   恒定语义不变（T16 断言口径 310→130）。
4. 列表行闭合态控件数下降（popover content 空挂载）；右键菜单功能
   全回归（打开/重命名/复制/剪切/删除/收藏/选择族）。
5. 全量回归绿：desktop_mcp 74+ 用例（行计数断言改口径后同步更新）
   + verify_p2/p3/p4 33 断言 + vue codegen 冒烟。

**非目标**：hover 高亮节流/大目录禁用（登记 P2 债，不在本计划）；
共享单例右键菜单重构（popover 条件化已够本计划目标，单例化为后续
可选）；网格模式右键菜单（现状无 ··· 锚，维持）；SNAP_CAP / 预算
拆链架构本身（已定案）。

## 架构方案

- **自适应内联**：NavTo（VM/vue/demo 三臂）与 RunTreeSearch 尾部
  改为：`if .d_total <= 4000 { .RefreshView() } else {
  .pending_refresh = true }`。阈值常量注记在 model（INLINE_CAP=4000，
  依据：预算墙触发点实测 ~9k 链；6000 拆臂实测应用内 0ms——内联合链
  在 4000 留 ≥2x 余量）。Tick 消费臂保留（大目录路径不变）。
- **首窗 120**：`render_cap` 初值 300→120（`render_step` 500 不变，
  GrowRender 语义不变；NavTo 各臂的 `.render_cap = 300` 重置同步改）。
- **popover 条件化**：list 行 popover-content 的 7 菜单按钮整体包
  `if .ctx_id == item.id { ... }`（锚钮 trigger 保持常驻——popover
  契约要求 trigger/content 子结构不变，仅 content 内部条件化；闭合行
  content 空挂载）。**风险注记**：F-2 债曾记「closed 态内容泄漏参与
  布局」——条件化恰好消除该泄漏面（内容不存在即不泄漏）；若框架对
  空 content 有渲染异常，回落方案 = 仅保留 `打开/删除` 两钮常驻、
  其余条件化。
- **导航计时仪器**：`nav_t0 int`（NavTo 入口记）、`last_nav_ms int`
  （RefreshView 末尾：`if .nav_t0 != 0 { .last_nav_ms = now - nav_t0;
  .nav_t0 = 0 }`）——内联路径即真实全程；延迟路径含 Tick 等待
  （诊断可见）。`time.now_ms` 调用全部 `is_vm` 守卫（F-V1 纪律）。

## 技术栈

同 001-004：`.at` 双轨（VM 事实轨 / vue 调试轨）+ desktop_mcp 注入式
驱动 + verify_pN 短探针。无 auto-lang 改动。

## 需求分析与背景调查

- **授权**（2026-10-04 用户指令）：基于卡顿诊断结论新建本计划并
  实施（"OK，新建一个计划"）。允许仓库：本仓。**auto-lang 零改动**。
- **基线**：v0.6-dev @ da63875（v0.7 程序终态；001-004 已归档）。
- **诊断证据**（本会话实测）：应用内 `last_snapshot_ms/
  last_derive_ms` = 0ms @ 200-6000 项；预算墙 = AddrGo→NavTo 链
  9k 目录 10M 指令中止（001 归档收据 R9）；「打开」按钮计数 = 行数
  （套件 T1/T2/T16 断言面）——闭合 popover content 在树实证。
- **取号**：.next-id=005，active/archive 无重号（单写者会话核对）。
- **执行环境**：worktree 组 `.wt/os-005/auto-os`（本仓 v0.6-dev 基线
  + submodule update --init；子模块开 plan-005 分支）。

## 详细设计

### 状态与接线

```
model 增量：
  var nav_t0 int = 0          // 导航全程计时起点（F-V1 纪律：is_vm 守卫）
  var last_nav_ms int = -1    // 导航→视图更新全程（诊断/验收面）
// render_cap 初值 300 → 120（model 初值 + NavTo 各臂重置点）

NavTo/RunTreeSearch 尾部（三臂 + 搜索同构）：
  if .d_total <= 4000 {
      .RefreshView()          // 内联——零 Tick 延迟
  } else {
      .pending_refresh = true // 超大目录保险（预算拆链原路径）
  }
  .PreviewRun()

RefreshView 尾部：
  if .is_vm == "vm" {
      if .nav_t0 != 0 {
          .last_nav_ms = time.now_ms() - .nav_t0
          .nav_t0 = 0
      }
      ...原 last_derive_ms 计时...
  }
NavTo 入口（VM 臂 canonical 门控后）：
  if .is_vm == "vm" { .nav_t0 = time.now_ms() }

popover-content 条件化（list 行）：
  popover-content {
      if .ctx_id == item.id {
          [原 7+ 菜单按钮整体]
      }
  }
```

### 断言口径迁移（「打开」→ checkbox）

- T1 行渲染计数一致 / T2 行渲染 4 / T16 首屏窗口行数：计数面从
  `find_ids(kind="button", label="打开")` 改 `find_ids(kind="checkbox")`
  （每行恒一 checkbox——比锚钮更稳：锚钮也是 button 但 label 为空，
  checkbox 是行级独有元素；表头 checkbox +1，断言按 `行数+1` 校准）。
- T16 首屏断言 ≤310 → **≤130**（120 窗口 + 表头 1 + 容差）。
- 右键菜单 UI 链回归：CtxOpen/CtxRename/CtxDelete 走 fixture（不依赖
  content 常驻）；新增一条 UI 链断言：写 ctx_id + ItemCtx 后
  `find_ids(label="收藏此目录")` 命中 ≥1（content 条件挂载实证）。

### 规范增量

| delta_id | add/modify/retire | target（本仓 SPEC.md） | before/after rule | rationale | acceptance IDs |
|----------|-------------------|------------------------|-------------------|-----------|----------------|
| SD-0051 | modify | SPEC §1.10 预算拆链 | before：NavTo/搜索一律 pending_refresh 下一拍派发；after：**自适应内联**——d_total ≤ 4000 内联 RefreshView（零延迟），>4000 走 pending_refresh（预算保险） | 拆链的固定 0~250ms 延迟是用户实测卡顿主因；4000 内联合链距预算墙 ≥2x 余量 | AC-01/AC-02 |
| SD-0052 | modify | SPEC §1（渲染层） | before：首窗 render_cap=300；after：120（哨兵扩窗语义不变） | 300 行 × ~20-25 控件首屏过重 | AC-03 |
| SD-0053 | add | SPEC §3 弹层节补注 | popover content 条件挂载（闭合行空 content）；行计数断言迁 checkbox 口径 | 消闭合态控件爆炸（~7 钮/行） | AC-04 |

## 测试设计

- **verify_p5.py**（短探针，单会话）：
  - 导航零延迟：mkbig 500 → AddrGo → 立即（无等待轮询至多 2 拍）
    读 `last_nav_ms ≤ 50` 且 `view_total` 已就位；
  - 超大目录保险不回归：mkbig 9000 → view_total=8000 + 截断标注
    （容忍 Tick 延迟轮询）；
  - 首窗 120：mkbig 2500 → vtree checkbox 数 = 120+表头+容差 ≤130 →
    GrowRender ×1 → 增长；view_total 恒 2500；
  - 菜单条件挂载：ctx_id 写入 + ItemCtx → 「收藏此目录」按钮可寻；
    未打开行闭合态「打开」按钮 **0 个**（旧口径反断言——条件化生效证）。
- **desktop_mcp 套件迁移**：T1/T2/T16 行计数改 checkbox 口径（+1 表头
  校准）；T16 首屏 ≤130；其余 74 用例不动语义。全套件绿。
- **回归电池**：verify_p2 12/12、verify_p3 10/10、verify_p4 11/11
  复跑绿；vue codegen 冒烟（F-V1 time 守卫不回归 + 新 if 挂载生成）。

## 验收标准

- **AC-01** 普通目录零 Tick 延迟：mkbig 500 导航后 `last_nav_ms ≤ 50`
  且无需轮询即读到位（verify_p5；应用内计时口径）。
- **AC-02** 超大目录保险：mkbig 9000 → cap 8000 + 截断标注 +
  d_real_total=9000（T18 口径不回归；延迟路径允许 Tick 轮询收敛）。
- **AC-03** 首屏窗口 120：mkbig 2500 → 首屏 checkbox 行 ≤130 → 扩窗
  增长 → view_total 恒 2500；状态栏全量计数不变。
- **AC-04** 菜单条件挂载：打开行「收藏此目录」可寻且 CtxOpen/
  Rename/Delete 功能链回归；闭合态「打开」按钮计数为 0；套件
  checkbox 口径迁移后全绿。
- **AC-05** 全量回归：desktop_mcp 全绿 + verify_p2/p3/p4 复跑绿 +
  vue codegen 冒烟过。

## 执行步骤

- **T-01** 自适应内联派发 + 导航计时仪器
  文件：src/front/app.at
  操作：INLINE_CAP=4000 分流（NavTo 三臂 + RunTreeSearch）；
  nav_t0/last_nav_ms 仪器（is_vm 守卫）；Tick 消费臂保留。
  验证：verify_p5（AC-01 + AC-02 前半）。
  → AC-01/AC-02
- **T-02** 首屏窗口 120
  文件：src/front/app.at（render_cap 初值 + 各臂重置点）
  验证：verify_p5 首窗口断言；T16 迁移后过。
  → AC-03
- **T-03** popover 条件化 + 断言口径迁移
  文件：src/front/app.at（popover-content 条件包裹）；
  tests/desktop_mcp.py（T1/T2/T16 计数面迁移 + 首屏口径）
  验证：verify_p5 菜单断言 + 全套件绿。
  → AC-04
- **T-04** 全量回归电池
  操作：desktop_mcp + verify_p2/p3/p4 + vue codegen 冒烟。
  验证：全绿。
  → AC-05
- **T-05** 文档同步
  文件：SPEC.md（SD-0051..0053）、README（响应优化注记）、
  docs/DESIGN.md（§13.6 补 P5 裁决）。
  → 全 AC 证据链

## 复审记录

- 2026-10-04 stage: new（auto-plan-new 起草，rev 1）。outcome: pass——
  诊断证据与授权齐备（用户实测反馈 + 会话内测量数据在档），无待决
  阻塞。next: work（T-01 起）。popover 空挂载为唯一框架行为验证点，
  已带回落方案（保留两钮常驻），不阻塞开工。

## 待澄清事项

- 无（popover 条件化的 F-2 交互为执行内验证点，回落方案在 §详细设计）。
