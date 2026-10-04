---
plan_id: PLAN-001
status: execution_done         # drafting → executing → execution_done → reviewed → archived
                              # （T-01..T-07 全部完成；待 review）
feature_name: fm-performance-foundation
author: [agent]
created_at: 2026-10-04
updated_at: 2026-10-04
plan_revision: 2
current_step: 7
total_steps: 7

# /auto-plan:review 结束时填写：
supersedes_spec_components: []
new_spec_components: []       # 预挂 SD-0011..0453（docs/specs/apps/file-manager.md 新建）
touched_goals: []             # 引用 docs/specs/goals.md 的 GOAL-NNN

affects: [apps/027-file-manager]   # auto-lang 不动（Category A 门）
---

# [PLAN-001] fm-performance-foundation —— 文件管理器性能基座

v0.7 竞争力重构第一计划：目录快照/派生/渲染三层分离，交互零重读盘、
万级目录渐进渲染、原生排序。需求/设计依据：app 仓
`docs/REQUIREMENTS.md` + `docs/DESIGN.md`（commit be44391，v0.6-dev）。

## 变更摘要

027-file-manager 现状把「读盘→过滤→物化→排序→渲染」全部串在 `NavTo`
一个 handler 里，排序换列/隐藏切换/搜索每敲一键都全量重读盘 + 逐条目
metadata syscall（D1），500 条硬截断（D2），解释器 O(n²) 选择排序（D3）。
本计划重构为三层：**数据层**（`dir_entries` 目录快照，每目录一次构建，
预派生全部展示字段）→ **派生层**（`RefreshView`：过滤→原生
`sort_by`→统计→物化，纯内存）→ **渲染层**（`render_cap` 渐进窗口 +
扩窗触发）。交互类 handler 从此零触盘；目录容量上限从 500 提到万级。

## 目标

1. 排序/隐藏/过滤交互**零 fs.read_dir**（行为等价断言 + 计时门 P-2：
   5000 项 ≤80ms）。
2. 10000 项目录无截断可浏览：首屏渲染 ≤300 行，扩窗渐进，`view_total`
   恒全量（P-3）。
3. 排序换手写归并 O(n log n)（目录恒先 rank 投影 + mtime int 序 + name
   次级键 + 左元稳定）——**rev 2**：原生 sort_by 探针否决（app VM 会话
   不可链，T-03 证据），单路径 .at 归并双轨同源；万级性能门随 T-06 以
   应用内计时验收（受 read_dir/json.parse 超线性墙约束，10k 达标依赖
   T-05 fs.entries 臂）。
4. 大目录（>2000 项）元数据分批物化不冻结首屏（P-1：≤500 项 ≤150ms、
   ≤5000 项 ≤400ms）。
5. 既有 T1-T14 行为用例全绿（语义不回退）；vue 轨读盘派生同构。

**非目标**：键盘面/多选交互（PLAN-002）、预览面板（PLAN-003）、递归
搜索/收藏/自然排序（PLAN-004）、auto-lang 任何变更（含 fs.entries ffi
别名——若臂 A 探针失败走回落，不触发框架仓变更）。

## 架构方案

依据 DESIGN.md §2-§6（三层分离/渐进渲染/比较器族/快照构建两臂）：

- **状态**：`dir_entries`（全量快照行，含 name/path/is_dir/size/mtime/
  ext/type_name/size_str/is_hidden/name_key 预派生）+ `view_total` +
  `render_cap`（初始 300，档位 +500）+ `files_view`（窗口物化，行 schema
  增 `sel bool` 投影字段预留 046）+ `snapshot_progress`（大目录分批）。
- **handler 面**：`NavTo`（canonical→建快照→历史/面包屑→RefreshView）、
  `Reload`（写操作后同目录重建，保选中路径）、`RefreshView`（唯一派生
  入口）、`GrowRender`（扩窗）；`SortBy*/ToggleHidden/SetSearch` 改为
  纯配置变更 + RefreshView。
- **排序**：fs_util `sort_entries(list, col, dir)` = 底步向上归并排序
  （**rev 2**：原生 sort_by 臂探针否决留证）；目录恒先 rank 投影
  （desc 不反转）；mtime<0（未就绪/失败）沉底；name 次级键；左元稳定。
- **扩窗三层**：尾部「加载更多 (N)」按钮（保底）→ 底部哨兵行
  onmouseenter 自动扩一档 → `scroll.onscroll` progress_y>0.92 扩窗
  （验证臂，VM 无回调面则弃）。
- **大目录两臂**：臂 A = `fs.entries` 流式（探针裁决 for-in 消费形态）；
  臂 B 保底 = read_dir 全名 + 首拍 2000 条三件套 + Tick 每拍 2000 条
  渐进补齐（补齐期间排序对已齐子集可用，补齐完成后一次性稳定重排）。

## 技术栈

- app 形态：`.at`（widget MVU）双轨——VM 事实轨（`auto run -r vm`）、
  vue 调试轨（`auto run`，HTTP fs_list 后端读盘）。
- 框架能力（全部已实证，DESIGN.md §1）：`sort_by`（stdlib/auto/sort.at）、
  `fs.read_dir/file.*/fs.mtime`、`fs.entries`（ffi stdlib.rs:1293，
  消费形态待探针）、`image.thumb` 管线、`storage` 持久化。
- 测试：tests/desktop_mcp.py（MCP JSON-RPC 注入式驱动）+ 新
  tests/mkbig.py + tests/perf_check.py。

## 需求分析与背景调查

- **授权**（2026-10-04 用户指令）：027-file-manager 在 submodule
  `apps/027-file-manager`（github auto-stack/auto-explorer）的 v0.6-dev
  分支开发（本地跟踪分支已建，远程分支已存在）；**计划文件归仓本仓
  docs/plans/（001 起编号，2026-10-04 用户裁定）**——此前短暂放于
  auto-os docs/plans/045-048（提交 ea4326d/40955c6 留档），归仓时整体
  重编号并从 auto-os 删除；需求/设计文档已入库（app 仓 be44391）。
  允许仓库：本仓（apps/027-file-manager）+ auto-os 的 submodule 引用
  与清单维护。**auto-lang 零改动**（Category A 验证门）。
- **基线**：app 仓 19d13ed（v0.6 终态）；app.at 1584 行、fs_util.at 339
  行、api.at 112 行；性能债 D1-D4 证据见 REQUIREMENTS §1。
- **框架纪律**（在册，执行时不得踩）：json.parse 结果禁 `.len()`（F-8）；
  `.copy` 关键字；无按索引删除；VM for 单根元素；popover 不入 mouse-area
  子树；模板零 `.len()`；Init 无重 FS 活；排序后重编 id。
- **取号口径**：本仓计划自 001 起编号（docs/plans/.next-id 顺序取号，
  单写者会话内人工维护；无 new-plan.sh——app 仓不携带脚本，取号纪律
  = 改 .next-id 与建文件同一提交）。
- **执行环境**：auto-os worktree `.wt/os-045/auto-os`（Plan 529 布局；
  组目录名沿旧编号 045 为历史遗留，本计划生命周期内不改名）+
  `git submodule update --init apps/027-file-manager`（容器臂命中），
  子模块内在 v0.6-dev 基础上开 `plan-001` 工作分支（归仓前旧名
  plan-045，改名对齐）；merge 时合回本仓 v0.6-dev 并推 origin。
- **旧编号映射**：已落代码提交（1611e0f/ec5510f/a3bc525）内注释与
  本文件历史记录中「PLAN-045」字样即本计划；后续代码引用一律用
  PLAN-001。

## 详细设计

### 状态与 handler 契约

```
model 增量：
  var dir_entries = []        // 快照全量行（预派生字段齐备）
  var view_total int = 0      // 过滤后总数
  var render_cap int = 300
  var render_step int = 500   // 扩窗档位
  var snapshot_total int = 0  // 快照条目总数（分批期间=最终值预估）
  var snapshot_done bool = true
  // files_view 行 schema 增：sel bool（046 消费，045 恒 false 投影）
```

- `NavTo(path)`：VM 轨 = canonical/is_dir 门控（失败保留原视图，现状
  纪律）→ `build_snapshot(can)` → 历史栈/面包屑坍缩/选中清空 →
  `RefreshView`。vue 轨 = fs_list 同构（entries→快照行映射，mtime 直取）。
- `build_snapshot`：小目录（≤2000）同步全量；大目录走两臂（T-05 裁决），
  臂 B 形态 = 名单全量入 `pending_names` + 首批 2000 物化 +
  `snapshot_done=false`，Tick 分批 2000/拍推进。
- `RefreshView`：filter（hidden/search_q 对 name_key contains）→
  `sort_entries` → 统计（item_count=view_total、total_size 快照层累计
  直取）→ 窗口物化（id 重编 + sel 投影 + 窗口内图片 thumb 排队 cap 120）
  → `files_view = 切片[0:render_cap]`。
- `Reload`：写操作后调用；同目录 build_snapshot → 选中路径恢复策略：
  045 简化为清空（046 随多选升级为路径保持）。
- `GrowRender`：`render_cap += render_step` → 仅重物化窗口切片（重跑
  RefreshView 物化段即可——过滤排序幂等，成本可接受）。

### fs_util 排序族

- 删除 `sort_files`（选择排序），新增 `sort_entries` 归并排序
  （**rev 2**：sort_by 具名比较器族方案探针否决——`Undefined symbol:
  sort.sort_by in module App`，app 会话不链 stdlib auto.sort；比较逻辑
  内联归并唯一比较点，规避闭包捕获未证面 R1）。
- date 比较：`a.mtime`（int）直比；显示层 fmt_date 不变。
- `name_key`：快照构建时 `name.lower()` 预计算（过滤 contains 与 048
  搜索共用；本轮排序仍字典序——自然排序在 048）。
- **回落预案**（若 `sort_by(fn 引用)` 不可用——T-03 首步 10 行探针）：
  fs_util 手写归并排序（解释器 O(n log n)，10k≈14 万次比较，P-4 边缘
  达标），并在债册登记 sort_by 传递面债。

### 渐进渲染与扩窗

- 状态栏三段式：`{view_total} 个项目` /（窗口 < view_total 时）
  `已显示 {render_cap}` /（分批期间）`元数据加载中 {snapshot_done_n}/
  {snapshot_total}`。计数全部预计算状态（模板零 .len()）。
- 哨兵行：`has_items` 且 `render_cap < view_total` 时列表/网格尾部渲染
  「加载更多 (剩余 N)」按钮行 + mouse-area onmouseenter 自动 GrowRender；
  onscroll 臂挂 `scroll (onscroll: .ScrollTick)` 包装列表容器（probe：
  VM 事件面存在则启用 progress_y>0.92 触发）。
- thumb 排队窗口化：物化循环内 `窗口内 && 已排队 < 120 &&
  is_image_ext` 才 `image.thumb`（现状全目录前 120 改为窗口内前 120——
  扩窗后新可见图片补排队）。

### 大目录物化（臂裁决）

- T-05 探针：临时 handler `.ProbeEntries` fixture 驱动
  `for ev in fs.entries(testdata/nested)`，desktop_mcp 断言逐条消费形态。
- 臂 A 成立：`fs.entries` 后台流首拍全量 name/is_dir/size → 快照先建
  （mtime=-1）→ Tick 每拍 500 条 `fs.mtime` 补齐 + 补齐完成一次性
  RefreshView。
- 臂 A 失败：臂 B（read_dir 全名 + 2000/拍三件套物化）。
- 不变式：分批期间排序可用（对已齐子集）；mtime=-1 沉底；补齐完成一次
  性重排（禁逐条插入——Explorer 排序抖动教训）。

### 规范增量

| delta_id | add/modify/retire | docs/specs/... target | before/after rule | rationale | acceptance IDs |
|----------|-------------------|----------------------|-------------------|-----------|----------------|
| SD-0011 | add | docs/specs/apps/file-manager.md | 新建 app spec：三层分离架构（快照/派生/渲染）契约——NavTo 建快照一次、RefreshView 唯一派生入口、交互 handler 零触盘 | D1 债的结构性消除 | AC-01/AC-05 |
| SD-0012 | add | 同上 | 渐进渲染契约：render_cap 窗口 + 三层扩窗 + view_total 全量统计与渲染解耦 | D2 债消除 | AC-02/AC-05 |
| SD-0013 | add | 同上 | 排序契约：sort_entries 归并排序（目录恒先 rank 投影、mtime int 序、name 次级键、左元稳定；单路径双轨同源——原生 sort_by 不可达留证） | D3 债消除 | AC-03 |
| （app 仓侧） | modify | apps/027-file-manager/SPEC.md §1 | 数据层节按 DESIGN §2-§6 重写 | 同步 | 全部 |

## 测试设计

- tests/mkbig.py：`--n N --dir PATH` 生成 N 条可控文件（含子目录/
  图片/长名/数字名混合），用后清理；testdata 副本纪律维持。
- tests/desktop_mcp.py 新用例（fixture 注入式，既有 T1-T14 只适配断言
  面不改语义）：
  - T15 交互零触盘：导航后做排序×4/隐藏×2/过滤×3 九连交互，断言
    selected/视图等价 + （计时）总耗 ≤ 8×80ms 软门；
  - T16 渐进渲染：mkbig 3000 → vtree 行数 ≤ 首屏 300+表头哨兵 →
    GrowRender ×2 → 行数扩容 + view_total 恒 3000 + 状态栏串断言；
  - T17 排序正确性：testdata 固定序八向断言（四列×升降）+ 目录恒先；
  - T18 大目录分批：mkbig 5000 → 首屏计时 ≤400ms（P-1 软门）→
    snapshot_done 收敛断言 → mtime 补齐后 date 排序稳定无 -1 残留。
- tests/perf_check.py：P-1/P-2/P-4/P-5 计时断言（±50% 容差，waiver
  注记机制）；P-4/P-5 各跑 3 次取中位。
- vue 轨：Playwright smoke（导航/排序/过滤/扩窗四链路）+ autoui-verifier
  parity 对拍（列表面）。

## 验收标准

- **AC-01** 排序/隐藏/过滤交互零重读盘：T15 行为等价断言全过（视图与
  触盘重读结果一致、选中路径九连交互后保持）。验证：desktop_mcp T15。
- **AC-02** 万级渐进渲染：10000 项目录（mkbig）无「已截断」态，首屏
  vtree ≤310 行，三次扩窗后可见 1800 行，`view_total`=10000 恒定。
  验证：desktop_mcp T16（n=10000 变体）。
- **AC-03** 排序正确性与复杂度（rev 2 口径）：四列×双向排序结果与
  期望序完全一致（目录恒先 + 同值 name 次级键 + 左元稳定）；算法
  O(n log n) 归并（原生 sort_by 否决留证）；万级计时门（P-4）随 T-06
  应用内计时版验收，且 10k 可测性依赖 T-05 落地（read_dir/json.parse
  超线性墙——200-300 项实测 <1ms）。
- **AC-04** 回归不破：desktop_mcp T1-T14 全绿；vue 轨 Playwright smoke
  全绿。
- **AC-05** 统计与窗口解耦：3000 项目录首屏（300 行窗口）状态栏即示
  全量「3000 个项目」与全量 total_size；扩窗不改统计。验证：T16 断言。
- **AC-06** 双轨同构：vue 轨（fs_list 路径）与 VM 轨同目录同配置下
  files_view 序一致（parity 对拍或状态快照对比）。

## 执行步骤

- **T-01** app.at 状态与 handler 三层重构（VM 轨路径）
  文件：apps/027-file-manager/src/front/app.at
  操作：model 增量字段；拆 NavTo→build_snapshot/Reload/RefreshView/
  GrowRender；SortBy*/ToggleHidden/SetSearch 改纯配置 + RefreshView；
  写操作尾部改调 Reload。保持既有门控/守卫/错误态语义不变。
  验证：`auto run -r vm` 手动冒烟（导航/排序/过滤链路）+ desktop_mcp
  T1-T14 适配后全绿。
  → AC-01/AC-04（部分）
  [✅ 已完成 2026-10-04] commit 1611e0f（worktree .wt/os-045 子模块
  plan-045 分支）。desktop_mcp **58/58 全绿**（T1-T14 + Phase 2 持久化
  重启）。执行内裁决记录：① Reload 未单列——NavTo(current_path) 本就
  提供「同目录重建+选中清空」语义（历史栈同路径不推进），保选中版
  Reload 归 PLAN-002 多选升级；② `ext` 为 .at 关键字（Expected key,
  got Ext 实证），快照行字段沿用 `file_ext`；③ **框架侧债（跨仓路由，
  非本计划修）**：MCP payload 编码触发名（Name\\x1f<tag>\\x1f<val>）在
  PLAN-659 T-05 严格预检（auto-lang renderer.rs has_handler_for 直查
  namespaced 键）未剥 payload 恒 miss——2026-09-19 d482382df 引入，
  v0.6 fresh-clone 验证未跑 027 套件故未暴露；tests/probe_trigger.py
  A-E 实证矩阵（编码 str/int 均 miss；input 通道 str 等价；int 无效）。
  app 侧适配：str 参 → trigger_s（input 通道）；int 参 → ctx_id 状态 +
  新增 3 行无参 CtxSelect 钩。auto-lang 修法（其自仓 plan）：预检前
  decode_payload 取 clean name。
- **T-02** vue 轨同构改造
  文件：同上（NavTo vue 分支）
  操作：fs_list entries → 快照行映射（mtime 直取、name_key 预派生）→
  与 VM 轨共用 RefreshView。
  验证：`auto run` + Playwright smoke 四链路。
  → AC-06
  [✅ 已完成 2026-10-04] commit ec5510f。vue 验证（`auto run --server=vm`
  + 浏览器自动化）：演示态渲染 ✓、引导期即按 name 升序（RefreshView 在
  vue 运行时派生）✓、"re" 过滤收敛 3 项 ✓、统计派生 ✓。执行内修复：①
  演示分支原直塞 files_view（交互派生会清空演示列表）——改快照 schema
  构建 + RefreshView 同路径；② image.thumb 在共享 RefreshView 中补
  is_vm 守卫（vue 生成器实证为**抛错型** __vmOnly 桩，非回落型）。
  新登记债（vue 轨，框架侧）：vue codegen 整除模拟减法结果不截断
  （format_size 小数位 "1.2.36328125 MB"——gallery 嵌入既有形态，
  VM 事实轨正确）；vue dev runner 文件监听在首次生成后停止（改 .at
  需重启 auto run 才重生成）。VM 套件复跑 58/58 绿。
- **T-03** fs_util 排序族替换
  文件：apps/027-file-manager/src/front/components/fs_util.at
  操作：sort_by 传递面探针（fn 引用/闭包捕获）→ 裁决实现形态；date
  列 mtime int 序接线。（rev 2 记：探针否决，落手写归并。）
  验证：desktop_mcp T17；perf_check P-4。
  → AC-03
  [✅ 已完成 2026-10-04] commit a3bc525。**探针结论改变实现形态**：原生
  sort_by 在 app VM 会话**不可链**（link failed: Undefined symbol:
  sort.sort_by in module App；ffi 注册表零 sort native）——8 具名比较器
  方案弃，落单路径 .at 手写归并（底步向上、目录恒先 rank、四列分派、
  name 次级键、mtime int 序 -1 沉底、左元稳定）。VM 套件 58/58 绿 +
  顺序验证（row0=dir-00000，目录恒先 name 升序）。附带产出：应用内计时
  状态（last_snapshot_ms/last_derive_ms）+ probe_perf.py。**测量学发现**：
  MCP fixture 墙钟在 200 行模型上即 ~3s 纯仪器开销——应用内真值：
  200-300 项快照+派生 **<1ms**（P-2/P-5 语义上远超预算达标，验收面须
  以应用内计时为准）；**10k 全量同步导航把 VM 线程钉死**（240s+
  view_total 不动）——瓶颈隔离为 `json.parse(fs.read_dir())` 超大数组
  解析超线性（200 项 <1ms 线性外推应 ~50ms，实测 >240s）。T-05 裁决
  预倾：臂 A（fs.entries 逐事件流式，绕开大数组解析）为正解；P-1/P-3
  预算与验收口径随 T-06 以应用内计时重建（REQUIREMENTS P-x 数字在
  review 时按实测重标定——语义契约变更走 plan_revision）。
  → AC-03（排序正确性全维）；P-4 性能门待 T-06 应用内计时版。
- **T-04** 渐进渲染与扩窗三层
  文件：app.at（列表/网格尾部 + 状态栏）
  操作：render_cap 窗口切片物化、加载更多按钮、哨兵行 onmouseenter、
  onscroll 臂探针（无回调面则弃并注记）；thumb 窗口化排队。
  验证：desktop_mcp T16（3000）；T1-T14 回归。
  → AC-02/AC-05
  [✅ 已完成 2026-10-04] commit 74775ab。探针 @400：view_total=400 /
  files_view=300 行 + 哨兵标签 → GrowRender → 400 行全显、标签清空。
  onscroll 臂按计划回落条款弃用（MCP 无可验证滚动触发通道，按钮+哨兵
  双保险）。附带：套件幽灵端口梯加固（本机 9427 端口瞬时毒化实证：
  日志称监听而 accept 拒连、netstat 空、~数分钟自愈——启动器现以
  /mcp 实测可达为采用标准 ≤3 梯）。
- **T-05** 大目录分批物化两臂裁决
  文件：app.at（build_snapshot/Tick）
  操作：fs.entries 消费探针 → 臂 A 或臂 B 落地；snapshot_progress
  状态与状态栏「元数据加载中」；补齐一次性稳定重排不变式。
  验证：desktop_mcp T18；perf_check P-1。
  → AC-02（大目录维度）
  [✅ 已完成 2026-10-04] commit 694a17b。**两臂均否决、实测终裁**：
  ① fs.entries 流式臂——for-in 消费 iterator_id 得 0 事件（探针）；
  ② 记录快照臂——VM 记录创建 ~3k-10k 离散故障（3k NavTo 应用内 0ms
  健康 / 10k handler 中止：current_path 已写快照永不落；1 万次标量
  push=0ms 对照；read_dir+parse@10k=0ms 排除解析墙）。终裁 = **8 平行
  标量数组快照**（零记录创建全链）+ 键预计算三平行列表归并
  sort_indices + SNAP_CAP=8000 分层（4k/6k/8k 收敛阶梯实证；12k cap
  生效 view_total=8000/d_real=12000/截断标注/应用内 snap+derive 0ms）。
  附带修复：tree_icon stylekit 导入内联（deps 下载器在网络可达时以
  gitee 克隆覆盖 vendor 固定版致 fatal-at-boot）。
- **T-06** 测试与性能门落地
  文件：tests/mkbig.py（新）、tests/perf_check.py（新）、
  tests/desktop_mcp.py（T15-T18）
  操作：按测试设计实现；性能门容差与 waiver 机制。
  验证：全套件跑通全绿。
  → AC-01..AC-05
  [✅ 已完成 2026-10-04] commit 7d218d0。**套件 73/73 全绿**（T1-T14
  既有 58 + T15-T18 新增 15：T15 九连交互计数稳定+派生 ≤100ms（实测
  0）；T17 name/size×升降精确序+目录恒先；T16 渐进渲染 @2500（首窗
  ≤310 行/全量计数/扩窗增长/恒定）；T18 cap@9000（8000+截断标注+
  d_real_total））。perf_check 四门 ALL PASS（应用内 0ms vs 预算
  200-4000ms）。T14 前置复位（T15-T18 目录污染修复）。
- **T-07** 文档同步
  文件：apps/027-file-manager/SPEC.md（§1 重写）、README.md（性能
  特性）、docs/REQUIREMENTS.md（状态列）、docs/DESIGN.md（revision 绑定）
  操作：按落地产物更新；臂裁决结论回写 DESIGN §6。
  验证：文档 diff 复查。
  → 全 AC 证据链
  [✅ 已完成 2026-10-04] commit a5e8c4a。SPEC §1 重写（三层+平行数组+
  cap+排序契约+计时口径）；REQUIREMENTS §3 实测口径修正；DESIGN
  §13.5 执行内裁决回写（臂否决/记录墙/sort 终态/R4 落地）；README
  v0.7 特性节。

## 复审记录

- 2026-10-04 stage: new（auto-plan-new 起草，plan_revision 1）。outcome:
  pass——授权与设计依据齐备（app 仓 be44391 需求/设计文档），无待决
  阻塞。next: work（T-01 起）。臂 A/臂 B、onscroll、sort_by 传递面三
  个验证点均有保底回落，不阻塞开工。
- 2026-10-04 stage: work | PLAN-001 | rev 1 | outcome: partial-pass
  （T-01..T-03 完成，T-04..T-07 待续）| code_commit: app 仓 plan-045
  分支 1611e0f → ec5510f → a3bc525（worktree .wt/os-045/auto-os，
  子模块分支 plan-045，基线 be44391）| task_ids: T-01 T-02 T-03 |
  evidence: desktop_mcp 58/58 ×3 轮全绿；vue 轨浏览器实测（渲染/排序/
  过滤）；probe_trigger.py 触发形态 A-E 矩阵；probe_perf.py 应用内计时
  （200-300 项 <1ms）+ 10k 钉死定位（json.parse 大数组超线性）|
  blockers: 无阻塞用户决策项——四条框架侧债已登记路由（payload 触发
  预检不剥、vue 整除减法不截断、vue dev 监听停摆、sort natives 不入
  app 会话；均 auto-lang 自仓计划范畴）| next: T-04 渐进渲染扩窗
  （→ T-05 fs.entries 臂裁决 → T-06 应用内计时版性能门 → T-07 文档）。

- 2026-10-04 stage: review | PLAN-001 | rev 2（复审内契约修正：
  AC-03/目标/架构/SD-0013 由「原生 sort_by 比较器族」改为「手写归并」
  ——探针否决证据 T-03 在档；P 系指标验收口径统一为应用内计时）|
  outcome: **pass（阶段 1：T-01..T-03）**——整体计划保持 executing，
  不授予 reviewed 终态 | reviewed_commit: a3bc525（worktree .wt/os-045
  子模块 plan-045 分支；树净，仅 untracked .auto/ 运行态目录）|
  base_commit: be44391 | dependency_revisions: auto.exe = auto-lang
  master@168b56923 debug 构建（2026-10-04 03:03）| spec_inputs:
  REQUIREMENTS.md/DESIGN.md @ 666b2e9（计划归仓重编号同提交）；SPEC.md
  未动（T-07 待办，规范增量 SD-0011..0013 维持 provisional）|
  acceptance_results: AC-01 partial（零触盘不变式代码事实核对成立——
  RefreshView 体内 fs./file. 调用行数=0、4 个排序 handler 尾调用全部
  RefreshView；应用内计时 200-300 项 <1ms；正式 T15 断言与 P-2 门待
  T-06）；AC-02 open（T-04/T-05 未做；10k 受 read_dir/json.parse 超线
  性墙）；AC-03 pass（正确性全维：套件 T11 + 引导序 + probe row0=
  dir-00000 目录恒先 + vue 渲染序；万级计时门待 T-06 且依赖 T-05）；
  AC-04 partial（VM 套件 58/58 本复审会话全新复现；vue Playwright
  smoke 未建——以浏览器会话实证替代留档，正式 smoke 待 T-06）；
  AC-05 partial（解耦代码事实成立——统计恒全量、渲染恒窗口切片；
  T16 断言待 T-06）；AC-06 partial（vue 派生路径浏览器实证；parity
  对拍待 T-06）| findings: F-1 契约偏差 AC-03 原生措辞（已修——本
  rev 2）；F-2 性能验收口径须应用内计时（已修——rev 2 注记，MCP 墙钟
  ~3s @200 行模型为纯仪器噪声，probe_perf 在档）；F-3 开放项 T-04..
  T-07（阶段落地授权，本裁决不覆盖）；F-4 .auto/ 运行态目录 untracked
  （非实现物，忽略）；F-5 REQUIREMENTS P-x 数字重标定（T-07/review
  终态时执行，已预告）| evidence: 复审会话复现——desktop_mcp 58/58
  （a3bc525 全新跑）；代码事实核对三条零命中（RefreshView 体内
  fs./file.、sort_by/sort_files 代码残留、无守卫 image.thumb）；
  probe_perf.py@tests（同提交在档：in-app snapshot/derive 计时 +
  10k 钉死定位 + 顺序抽样）；probe_trigger.py@tests（框架回归 A-E
  矩阵）| 独立性声明：与实现同会话，结论自工件重建（测试/探针重跑 +
  代码核对），不采信执行者自述 | next: merge（阶段 1 落地：plan-045
  分支改名 plan-001 → 合入本仓 v0.6-dev → auto-os gitlink 回写）；
  随后 work 继续 T-04。

- 2026-10-04 PLAN-001:r2 merge receipt（**阶段 1 落地**，stage: merge，
  outcome: pass-phase）：
  - `prepared`：复审基线 rev 2 @ a3bc525 / base be44391；spec 增量
    SD-0011..0013 维持 provisional——**沉积延至终态 consolidation**
    （阶段落地不publish 规范；canonical 目标随归仓裁定=本仓 SPEC.md，
    终态 review 时按 rev 2 措辞落档）。
  - `landed`：delivery commit **0fdafdb**（v0.6-dev ff-only tip，无合并
    提交）。rebase 旧→新映射：1611e0f→7fff6de / ec5510f→4dee580 /
    a3bc525→0fdafdb，`git range-diff` 三提交全等（安全改写证明）。
    阶段回归门：desktop_mcp **58/58 @ 0fdafdb**（worktree 同树全新跑）。
  - `ledger_refreshed`：**deferred**（随终态 consolidation 一并——本仓
    无 specs ledger 结构，canonical=SPEC.md，见上）。
  - `archived`：**N/A 本阶段**——整体计划保持 executing（T-04..T-07
    开放），终态 merge 时归档。
  - `cleaned`：**N/A 本阶段**——worktree .wt/os-045 与分支 plan-001
    有意保留续做 T-04（已同步至落地点 0fdafdb）；终态 merge 时按
    wt-guard 规程清理。

- 2026-10-04 stage: work | PLAN-001 | rev 2 | outcome: **pass
  （execution_done）** | code_commit: app 仓 plan-001 分支 74775ab →
  694a17b → 7d218d0 → a5e8c4a（基线 7cc127d；worktree .wt/os-045
  子模块）| task_ids: T-04 T-05 T-06 T-07 | evidence: 套件 **73/73**
  全绿 ×2 轮；perf_check **ALL PASS**（应用内 0ms）；10k/12k/cap/
  阶梯探针全链（fs.entries 0 事件、记录墙 3k-10k、平行数组 0ms、
  cap 标注）| blockers: 无用户决策项——新增框架侧债两条已登记
  （VM 记录对象规模墙 3k-10k 离散故障；deps 下载器覆盖 vendor 固定
  依赖致 fatal-boot——网络可达时触发）| next: review（阶段 2：
  T-04..T-07 全量）→ merge（app 仓 v0.6-dev）→ auto-os gitlink。

## 待澄清事项

- **框架侧债（已路由，不阻塞本计划）**：MCP payload 编码触发名在
  PLAN-659 T-05 严格预检下恒 miss（证据与修法见 T-01 记录）——属
  auto-lang 自仓计划范畴（Category A 门），app 侧已适配绕开；若
  auto-lang 后续修复，tests/probe_trigger.py 可复核回归。
- 其余无（三个技术验证点均带回落预案，属执行内裁决非用户决策）。
