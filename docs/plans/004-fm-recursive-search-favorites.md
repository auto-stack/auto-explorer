---
plan_id: PLAN-004
status: drafting               # drafting → executing → execution_done → reviewed → archived
feature_name: fm-recursive-search-favorites
author: [agent]
created_at: 2026-10-04
updated_at: 2026-10-04
plan_revision: 1
current_step: 0
total_steps: 7

# /auto-plan:review 结束时填写：
supersedes_spec_components: []
new_spec_components: []       # 预挂 SD-0041/0482（docs/specs/apps/file-manager.md 增节）
touched_goals: []

affects: [apps/027-file-manager]   # auto-lang 不动（Category A 门）
---

# [PLAN-004] fm-recursive-search-favorites —— 递归搜索·收藏夹·自然排序（v0.7 收口）

v0.7 竞争力重构第四计划（依赖 PLAN-001；建议末位执行）。Everything 式
后台递归搜索 + DOpus 式收藏夹 + 自然排序收口，并做程序级收尾（债册/
需求状态列/README 终态）。需求/设计依据：app 仓 `docs/REQUIREMENTS.md`
F1/F2/F5 + `docs/DESIGN.md` §5.1/§10。

## 变更摘要

① 递归子树搜索：过滤框范围 chip「本目录/含子目录」，递归模式后台
`spawn` 协程（`fs.walk` 全量 JSON + 协程内 contains 过滤 + channel
流式回传），Tick 分批消费，世代计数取消（逐字重输即重启），结果
虚拟呈现于 files_view（点击目录定位/文件走打开链）。② 收藏夹：当前
目录星标入侧栏组（storage `fileman.favs` 持久化），行右键增删。③ 自然
排序：`natcmp` 比较器接入 name 排序（perf 门把关，超预算回落字典序
并登记债）。④ 程序收尾：KNOWN-DEBT 更新、REQUIREMENTS 状态列终态、
README/双仓互链核对。

## 目标

1. 递归搜索：命中流式呈现（≤1 拍批次延迟）、UI 零阻塞（导航可用）、
   取消即时（改词/清词/Esc ≤1 拍停采）、cap 2000、看门狗失败态。
2. 搜索结果交互：结果行打开/定位（目录 → NavTo(parent) 并选中该行）。
3. 收藏夹：星标/取消/侧栏导航/持久化重启四链路。
4. 自然排序：`file2 < file10`（数字段感知）；10000 项排序 ≤100ms
   perf 门，超预算回落 + 债册登记（不静默降级——测试与文档同改）。
5. 既有用例全绿；v0.7 程序级收尾产物齐备。

**非目标**：全盘索引/Everything 属性语法/保存搜索为虚拟文件夹（P2
展望，债册）；命令面板/批量重命名（P2）。

## 架构方案

依据 DESIGN §5.1/§10：

- **搜索范围**：`search_scope str = "dir" | "tree"`；过滤框左 chip
  切换。tree 模式下 `SetSearch` 走协程路径；dir 模式维持 045 的内存
  过滤（零成本路径不回归）。
- **协程形态**：
  ```
  .SetSearch(q) -> { .search_gen = .search_gen + 1; .search_q = q
      if .search_scope == "tree" && q != "" {
          let gen = .search_gen
          spawn(fn() {
              let raw = fs.walk(.current_path)     // 递归唯一面（全量 JSON）
              let paths = json.parse(raw)
              // 协程内逐条 base_name(name_key).contains(q_lower)
              // 命中 → chan.send({name, path})；每 200 条 yield_now()
              // 发送前比 gen != .search_gen 即 return（世代自弃）
          })
      } else { .RefreshView } }
  .Tick -> { // tree 模式且世代匹配：批内 ≤200 条入 results }
  ```
  - results 独立状态（`search_results` + `search_done` + 计数预计算），
    呈现时投影进 files_view（行 schema 同款，is_dir 按 walk 面补
    `file.is_dir` 惰性判定——仅结果窗口内判定）。
  - 看门狗：`search_stall` 拍计数（Tick 无新数据且未 done 超 40 拍 →
    失败 toast + 终止消费）。
  - **已知边界**（债册）：fs.walk 全量 JSON 非流式——万级子树单次
    native 侧可承受，.at 侧解析在协程内不阻塞 UI；真流式（fs.entries
    无递归形态）为框架侧展望。
- **世代取消一致性**：结果消费 Tick 首检 `gen == .search_gen`，过期
  整批丢弃（发送侧自弃是优化，接收侧门是正确性）。
- **收藏夹**：`favs` 数组（{path,label}）storage 持久化；工具栏星标
  按钮（当前目录在列高亮）；行右键「添加到收藏」；侧栏收藏组右键
  「移除」。label = base_name（重名允许，title 悬浮全路径）。
- **natcmp**：fs_util 纯函数（数字段感知：连续数字段 int 值比、前导
  零规则、非数字段字符序）；`cmp_name_asc/desc` 内换用；性能门
  perf_check P-4 复测。

## 技术栈

同 045-047。新增依赖：`fs.walk`（递归 JSON 数组）、`async.spawn/
channel/yield_now`、storage 数组持久化（017/018 先例）。

## 需求分析与背景调查

- **授权**：同 PLAN-001（2026-10-04 用户指令族）。
- **依赖**：PLAN-001 merged（RefreshView/快照行 schema 在场）；046/047
  建议先行（右键菜单增项/预览面板与结果行交互叠加），非硬依赖。
- **框架事实**：fs.walk 返回全量 JSON 路径数组（含目录与文件，无
  is_dir 标记——结果行惰性 file.is_dir）；json.parse 大数组 .at 侧
  for-in 消费（禁 .len()，F-8）；协程与状态交互只能经 channel +
  Tick（协程内不可直写 model 状态——async.at 语义）。
- **基线**：045（+046/047）merge 后 v0.6-dev HEAD。

## 详细设计

### 状态增量

```
var search_scope str = "dir"     // storage fileman.search_scope
var search_gen int = 0
var search_active bool = false
var search_results = []
var search_shown int = 0         // 预计算计数（模板零 .len()）
var search_done bool = false
var search_stall int = 0
var favs = []                    // storage fileman.favs [{path,label}]
```

### 结果视图交互

- 结果行单击 = 焦点（同目录视图）；双击 = is_dir ? NavTo(path) :
  OpenItem 链（文件打开走 041/031 open_with 既有链）。
- 「定位」右键项：目录 → NavTo(parent)；文件 → NavTo(parent) +
  选中该名行（RefreshView 后按路径找 id）。
- 退出 tree 模式（chip 切回/Esc/导航）→ results 清空回目录视图。

### 规范增量

| delta_id | add/modify/retire | docs/specs/... target | before/after rule | rationale | acceptance IDs |
|----------|-------------------|----------------------|-------------------|-----------|----------------|
| SD-0041 | add | docs/specs/apps/file-manager.md | 搜索契约：双范围（dir 内存过滤/tree 协程流式）、世代取消、接收侧门、cap 2000、看门狗 | Everything 式即时体验（框架现实内） | AC-01/AC-02 |
| SD-0042 | add | 同上 | 收藏夹契约（favs storage schema/星标交互）+ 自然排序契约（natcmp 规则与 perf 门回落条款） | DOpus 收藏 + 全体系自然序 | AC-03/AC-04 |
| （app 仓侧） | modify | apps/027-file-manager/SPEC.md、README | 新搜索/收藏节 + v0.7 终态特性表 | 同步 | 全部 |

## 测试设计

- desktop_mcp T22 递归搜索：testdata 深树（构造 3 层 × 分散命中名）→
  tree 模式输入 → 结果流式收敛断言（计数 + 路径含子树）→ 双击目录
  行 NavTo 断言 → 世代取消（输入改词后旧词结果不残留）→ Esc 退出
  回目录视图 → cap 构造（>2000 提示收敛）→ 看门狗（不可达路径失败
  态 toast）。
- T23 收藏夹：星标 → 侧栏组在场 → 点击导航 → 重启 storage 恢复 →
  移除。
- T24 自然排序：数字名构造（file2/file10/a1b2/a1b10）序断言 + P-4
  perf 门复测（10000 项）。
- 回归：T1-T21 全绿。
- 程序收尾核对单：KNOWN-DEBT 册增删（R1-R8 终态、fs.walk 非流式、
  natcmp 裁决结论）、REQUIREMENTS 状态列全刷新、README 特性表、
  两仓互链（app README ↔ auto-os docs/specs/apps/file-manager.md）。

## 验收标准

- **AC-01** 递归搜索正确且不阻塞：命中流式呈现（≤2 拍批次延迟）、
  搜索期间导航可用（T22 交互断言）、取消即时（改词 ≤1 拍停采）。
- **AC-02** 结果交互：打开/定位/退出三链路断言（T22）。
- **AC-03** 收藏夹四链路 + 持久化（T23）。
- **AC-04** 自然排序：数字段序断言全过；perf 门达标或按条款回落
  （回落时测试/文档/SPEC 三处同步，非静默）。
- **AC-05** 回归全绿（T1-T24）+ 收尾核对单产物齐备。

## 执行步骤

- **T-01** 搜索范围切换 + 世代取消骨架
  文件：app.at
  操作：search_scope/chip UI/世代状态/退出清理。
  验证：desktop_mcp 范围切换断言 + dir 模式零回归。
  → AC-01（结构）
- **T-02** 递归协程 + Tick 消费
  文件：app.at
  操作：spawn/fs.walk/过滤/channel/批消费/cap/看门狗。
  验证：T22 主链路。
  → AC-01
- **T-03** 结果视图交互
  文件：app.at
  操作：结果投影 files_view/双击/右键定位/退出。
  验证：T22 交互断言。
  → AC-02
- **T-04** 收藏夹
  文件：app.at（+fs_util）
  操作：favs storage/星标/侧栏组/右键增删。
  验证：T23。
  → AC-03
- **T-05** 自然排序 natcmp
  文件：fs_util.at
  操作：natcmp 实现 + cmp_name_* 接入 + perf 门复测（超预算按条款
  回落三处同步）。
  验证：T24。
  → AC-04
- **T-06** 测试与回归收口
  文件：tests/desktop_mcp.py
  验证：T1-T24 全绿。
  → AC-05
- **T-07** 程序级收尾
  文件：KNOWN-DEBT-AND-RISKS.md（auto-os docs/plans/）、REQUIREMENTS
  状态列、README、SPEC、互链核对
  验证：核对单逐项。
  → AC-05

## 复审记录

- 2026-10-04 stage: new（auto-plan-new 起草，plan_revision 1）。outcome:
  pass。next: work（前置：PLAN-001 merged）。

## 待澄清事项

- 无。
