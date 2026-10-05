---
plan_id: PLAN-006
status: drafting               # drafting → executing → execution_done → reviewed → archived
feature_name: fm-transfer-pipeline
author: [agent]
created_at: 2026-10-05
updated_at: 2026-10-05
plan_revision: 1
current_step: 0
total_steps: 6

# /auto-plan:review 结束时填写：
supersedes_spec_components: []
new_spec_components: []       # 预挂 SD-0061..0064（SPEC §4/§1.10/新注记）
touched_goals: []

affects: [src/front/app.at, tests/]
---

# [PLAN-006] fm-transfer-pipeline —— 传输非阻塞化 + 响应残余硬化

v0.7 需求 F7 中唯一未交付的 P1：**复制/移动大目录无进度反馈**。现状
`PasteOne` 对目录走单次 `fs.copy_recursive`（native 一口气拷整树）——
几千文件的目录复制期间 VM 线程整段阻塞：无进度、无取消、UI 全冻结
（002 交付的是同步版）。这是 001-005 复盘后剩余最大的一块体验缺口，
也是当初竞品调研里 Explorer 的经典痛点（"统一传输队列"教训）。

## 变更摘要

四件事：① **分片传输管线**——目录复制超过阈值（>200 文件）时展开为
文件级作业队列（平行标量数组 `x_src/x_dst`，规避记录墙），Tick 每拍
执行 ≤20 个 `copy_recursive`（单文件 native 拷贝）；状态栏进度段
（"复制中 34/3120 · 取消"可点取消）；小目录/单文件维持直接 native
（快路径）；剪切（rename 单系统调用）不变。② **搜索扫描上限**
（F-V3 收口）：RunTreeSearch 扫描量 > 60000 条路径即停 + 诚实标注
（"达扫描上限，请缩小范围"），杜绝巨树预算中止静默无操作。③ **启动
窗口收缩**：引导 8-tick（2s）→ 3-tick（750ms，iced boot 竞态的历史
保守值在其他路径稳定后收缩，套件多轮回归把关）。④ **hover 大目录
门控**：`d_total > 2000` 时 RowHover 不更新状态（免 mousemove 全量
重建；小目录高亮不变）。

## 目标

1. 大目录复制**不冻结**：传输期间导航/排序/滚动等交互仍响应（Tick
   与 handler 不被长时间占用）；复制完成后磁盘正确、汇总 toast 照旧。
2. 传输可见可控：进度段单调推进（done/total + 字节）；传输中可取消，
   取消即停（已完成部分保留，toast 报明完成量）。
3. 巨树搜索有界：扫描上限触发时返回已命中结果 + 上限标注，不再静默
   无操作。
4. 启动首屏 ≤1s（3-tick 引导），套件多轮全绿（boot 竞态回归门）。
5. 大目录 hover 不触发重建（fixture 直证 RowHover 在大目录 no-op）；
   小目录 hover 高亮不变。
6. 全量回归：desktop_mcp 全绿 + verify_p2/p3/p4 复跑绿 + vue codegen
   冒烟。

**非目标**：传输队列多任务并行/限速（P2）；剪切目录分片（rename 单
调用恒快）；F-V2 vue 演示空态（保持观察，诊断未竟）；P2 功能 backlog
（标签页/双栏/Quick Look/批量重命名/命令面板）。

## 架构方案

- **分片判定**（PasteOne 内）：`op == "copy" && is_dir` → `fs.walk`
  物化文件清单（F-8 纪律：push 入真 List）→ `count ≤ 200` → 直接
  `copy_recursive`（快路径，native 毫秒级）；`> 200` → **展开入
  xfer 队列**（平行数组 x_src/x_dst + 预建父目录链）→ `.x_active =
  true`。单文件与剪切路径完全不变。
- **Tick 执行臂**（与 preview-BFS/pending_refresh 同层）：`x_active`
  时每拍执行 ≤20 作业（每作业 = 1 次 copy_recursive 单文件 native，
  VM 指令开销可忽略；预算安全）+ 进度串重算 + `x_done == x_total`
  收尾（toast 汇总 + NavTo 刷新 + 清态）。取消：状态栏进度段即取消
  钮（`x_cancel` 置位，下拍停臂收尾）。
- **父目录预建**：展开期从文件路径集推导去重父目录集，按路径长度
  升序逐个 `file.create_dir`（单级 mkdir，短路径先建即成链）；覆盖
  语义 = 文件级覆盖（copy 语义），与直接路径一致。
- **xfer 状态（全标量/平行数组——B12 纪律）**：`x_src/x_dst` 列表、
  `x_done/x_total/x_bytes int`、`x_active/x_cancel bool`、`x_label
  str`（预计算进度串，模板零调用）。并发防护：x_active 期间禁再启
  PasteInto（toast"传输进行中"）。
- **扫描上限**：RunTreeSearch 主循环加 `scan_i >= 60000 → capped_scan
  break`（60k × ~40 指令 ≈ 2.4M，与过滤/键构建合计距 10M 墙余量
  充足）；标注串"已扫描 60000 条上限"。
- **启动收缩**：`.tick_count > 8` → `> 3`（750ms）。风险注记：8-tick
  是 PLAN-016 iced boot 竞态的保守实证值——收缩后以套件 ≥3 轮全绿
  为回归门，任何一轮 boot 挂起/超时即回落 8（执行内裁决点）。
- **hover 门控**：RowHover/RowLeave 首行 `if .d_total > 2000 {
  return }`。

## 技术栈

同 001-005：`.at` 双轨 + desktop_mcp 注入式驱动 + verify_pN 短探针。
无 auto-lang 改动。**依赖**：PLAN-005 先行执行（同触 NavTo 尾部/
render_cap 区域，避免同文件冲突；006 分支自 005 落地点起）。

## 需求分析与背景调查

- **授权**（2026-10-05 用户指令）：复盘 001-005、分析剩余优化空间、
  新建计划。允许仓库：本仓。**auto-lang 零改动**。
- **基线**：v0.6-dev @ 1bb51b1（PLAN-005 已起草未执行——006 排其后）。
- **证据**：PasteOne 目录分支 = 单次 copy_recursive（app.at 在档）；
  单 native 调用不耗 VM 指令预算（R9 只数指令）→ 冻结而非中止；
  竞品调研"统一传输队列/进度/取消"教训（REQUIREMENTS F7 溯源）；
  F-V3 巨树搜索（004 复审 finding 在册）。
- **取号**：.next-id=006，active/archive 无重号（单写者核对）。
- **执行环境**：worktree 组 `.wt/os-006/auto-os`（005 落地后开组；
  子模块 plan-006 分支）。

## 详细设计

### 状态与接线

```
model 增量：
  var x_src = []              // 传输作业源路径（平行数组）
  var x_dst = []
  var x_done int = 0
  var x_total int = 0
  var x_bytes int = 0         // 已传字节（进度串用）
  var x_active bool = false
  var x_cancel bool = false
  var x_label str = ""        // "复制中 34/3120 · 12 MB"

PasteOne（copy 目录分支改写）：
  walk 物化清单 → count ≤ 200 → copy_recursive 直接（现行为）
  count > 200 → 展开入 .x_src/.x_dst（含 keepboth 目标名改写）+
  父目录集预建入 x_dst 前置（目录作业：src="" 标记 mkdir）→ .x_active

Tick 传输臂（pending_refresh 臂之后）：
  if .x_active {
      if .x_cancel { 收尾（部分完成 toast）；return }
      var k int = 20
      while k > 0 && .x_done < .x_total {
          if .x_src[.x_done] == "" { file.create_dir(.x_dst[.x_done]) }
          else { fs.copy_recursive(.x_src[.x_done], .x_dst[.x_done])
                 .x_bytes += file.size(.x_src[.x_done]) }
          .x_done += 1；k -= 1
      }
      进度串重算
      if .x_done == .x_total { 收尾 }
  }

状态栏：.x_label 非空 → 进度段 + 取消按钮（onclick: .XferCancel）
RunTreeSearch：scan_i 上限 60000 + capped_scan 标注
Tick 引导：tick_count > 8 → > 3
RowHover/RowLeave：d_total > 2000 门控
```

### 陷阱核对（在册纪律）

- x_* 平行数组 = 标量（记录墙免疫）；作业 2 万上限 + 超限回落直接
  copy_recursive（诚实 toast"目录过大，整目录复制中…"——冻结但可达，
  与 8k cap 同哲学）。
- 模板零调用：进度串预计算；`file.size` 在传完的单文件上取（源仍在）。
- 取消语义：已建目录/已传文件保留（事务式删除不做，toast 报明）。
- 剪切跨盘 rename 失败路径：维持现状（002 口径）。

### 规范增量

| delta_id | add/modify/retire | target（本仓 SPEC.md） | before/after rule | rationale | acceptance IDs |
|----------|-------------------|------------------------|-------------------|-----------|----------------|
| SD-0061 | modify | SPEC §4 文件操作 | before：目录复制 = 单次 copy_recursive（同步阻塞）；after：>200 文件目录走分片传输管线（Tick ≤20/拍 + 进度/取消），≤200 与单文件维持直接 native | v0.7 F7 唯一未交付 P1；UI 冻结 | AC-01/AC-02 |
| SD-0062 | modify | SPEC §1.9 搜索 | before：巨树搜索可能预算中止静默；after：扫描上限 60000 + 诚实标注 | F-V3 收口 | AC-03 |
| SD-0063 | add | SPEC §1 注记 | 启动引导 3-tick（750ms）；hover 大目录（>2000）门控 | 启动/滑动响应 | AC-04/AC-05 |

## 测试设计

- **verify_p6.py**（短探针）：
  - 大目录复制不冻结：mkbig 树（含 >200 文件目录）→ CopySel →
    PasteInto 到目标 → 轮询期间**穿插 SortByName/NavHome 交互断言
    仍响应**（tick 活性）→ 收尾后磁盘逐文件比对（count + 抽样
    内容）；
  - 进度与取消：传输中读 x_label 单调推进；x_cancel 置位 → 传输臂
    停（x_active=false）+ 已完成文件在盘；
  - 快路径回归：小目录（<200 文件）复制即完成（无 x_active 中间态）；
  - 扫描上限：构造 >60000 路径不可行（太慢）——单测口径降为
    code-guard 证（scan 分支在场）+ 文档注记；运行时以现有 verify_p4
    搜索回归代偿；
  - 启动：booted 时 tick_count == 3（state 断言）+ 首目录就位；
  - hover 门控：大目录 fixture trigger RowHover(id) → hover_id 保持
    -1；小目录 → 正常更新。
- **套件**：全绿 ×3 轮（boot 收缩回归门——任何一轮启动失败即回落
  8-tick 执行内裁决）。
- **回归电池**：verify_p2/p3/p4 + vue codegen 冒烟。

## 验收标准

- **AC-01** 大目录复制不冻结：传输期间交互活性断言（≥2 次状态变更
  响应）+ 完成后磁盘正确（文件数一致 + 抽样内容）。
- **AC-02** 进度与取消：x_label 单调推进断言；取消后 x_active=false、
  已完成部分在盘、toast 报明完成量。
- **AC-03** 搜索扫描上限：guard 代码在场 + verify_p4 搜索链回归绿
  （运行时巨树路径以文档注记 + 上限常量审计代偿——构造 6 万路径的
  实测成本不成比例）。
- **AC-04** 启动 3-tick：booted 时 tick_count==3 断言 + 套件 3 轮全绿
  （boot 竞态回归门）。
- **AC-05** hover 门控：大目录 RowHover no-op、小目录正常（fixture
  双向断言）。
- **AC-06** 全量回归：desktop_mcp + verify_p2/p3/p4 + vue 冒烟全绿。

## 执行步骤

- **T-01** 分片传输管线（状态/展开/Tick 臂/进度取消 UI）
  文件：src/front/app.at
  验证：verify_p6（AC-01/AC-02 主体）。
  → AC-01/AC-02
- **T-02** 快路径与并发防护（≤200 直接；x_active 期间禁再启）
  验证：verify_p6 快路径 + 重复粘贴 toast。
  → AC-01
- **T-03** 搜索扫描上限（F-V3）
  文件：src/front/app.at（RunTreeSearch）
  验证：guard 审计 + verify_p4 回归。
  → AC-03
- **T-04** 启动收缩 + hover 门控
  文件：src/front/app.at
  验证：verify_p6 启动/hover 断言 + 套件 ×3 轮。
  → AC-04/AC-05
- **T-05** 全量回归电池
  验证：desktop_mcp + verify_p2/p3/p4 + vue 冒烟。
  → AC-06
- **T-06** 文档同步
  文件：SPEC.md（SD-0061..0063）、README、DESIGN §13.6 补记。
  → 全 AC 证据链

## 复审记录

- 2026-10-05 stage: new（auto-plan-new 起草，rev 1）。outcome: pass——
  001-005 复盘与授权齐备（用户指令在档），旗舰缺口（传输阻塞）证据
  充分。next: work（前置：PLAN-005 执行落地——同文件区域顺序约束）。
  执行内验证点两个：boot 收缩的竞态回归（回落预案 8-tick）、搜索
  上限以 guard 审计代偿实测（成本论证在测试设计）。

## 待澄清事项

- 无（两个验证点均带回落/代偿预案，属执行内裁决）。
