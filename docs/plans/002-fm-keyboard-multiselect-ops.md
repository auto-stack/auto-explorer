---
plan_id: PLAN-002
status: executing               # drafting → executing → execution_done → reviewed → archived
feature_name: fm-keyboard-multiselect-ops
author: [agent]
created_at: 2026-10-04
updated_at: 2026-10-04
plan_revision: 1
current_step: 5
total_steps: 7

# /auto-plan:review 结束时填写：
supersedes_spec_components: []
new_spec_components: []       # 预挂 SD-0021/0462（docs/specs/apps/file-manager.md 增节）
touched_goals: []

affects: [apps/027-file-manager]   # auto-lang 不动（Category A 门）
---

# [PLAN-002] fm-keyboard-multiselect-ops —— 键盘效率·多选·批量操作·写操作强化

v0.7 竞争力重构第二计划（依赖 PLAN-001 三层基座落地）。Total Commander
键盘流 + 多选批量操作 + 递归删除 + 粘贴冲突三选。需求/设计依据：app 仓
`docs/REQUIREMENTS.md` F3/F4/F7/F8 + `docs/DESIGN.md` §7/§8/§11。

## 变更摘要

现状只有 Enter 一个键盘动作、一切操作单条目、非空目录不能删、粘贴遇
同名只会报错。本计划：① 全键盘面（导航/操作/范围选择，双声明面
actions + onkeydown dot-modifier，输入态守卫）；② 多选模型
（路径键集合，勾选列 + Ctrl/A/Shift 范围，统计与批量操作）；③ 写操作
强化（`file.remove_dir_all` 递归删除 + 分级确认；粘贴冲突三选模态 +
批量内记住选择）。

## 目标

1. 键盘可用性：↑↓/Home/End/PgUp/PgDn/Enter/Backspace/Alt+←→ 全键盘
   导航；F2/Delete/Ctrl+C/X/V/A/Esc/Ctrl+F 操作面——输入框聚焦态全部
   守卫（不误触）。
2. 多选：勾选列 + Ctrl+A 全选 + Esc 清除 + Shift+↑/↓ 范围扩展；状态栏
   多选统计（N 项/总大小）；批量复制/剪切/粘贴/删除（cap 500 门 +
   汇总 toast）。
3. 递归删除非空目录（`file.remove_dir_all`，含项数确认文案分级）。
4. 粘贴冲突三选：覆盖/跳过/保留两者（`原名 (2)` 递增），批量内可
   「对剩余项应用」。
5. 既有用例（T1-T18）全绿；写操作守卫语义（vue 轨 VM 专属）不回退。

**非目标**：命令面板/j-k vim 键位（P2 展望）、批量重命名（P2）、传输
队列与进度（P1 归 047/048 视余量，本计划不做）、预览面板（047）。

## 架构方案

依据 DESIGN §7/§8/§11：

- **多选键 = 路径**：`sel_paths` 字符串数组（contains 判定）——id 随
  排序/扩窗重编漂移，路径键免疫（R4-4 债的多选代解）；`anchor_id`
  为 Shift 范围锚（仅活窗口内有效，越界清锚）。行渲染投影 `sel` 字段
  （045 已预留 schema）。
- **焦点与集合正交**：单击/方向键改 `selected_id`（焦点，驱动 Enter
  打开与 047 预览）；勾选/Shift 范围/Ctrl+A 改 `sel_paths`（集合，驱动
  批量操作）。两者独立不互斥。
- **键盘双声明面**：可披露动作（F2/Delete/Ctrl+C/X/V/A、Enter/Backspace/
  Alt+←→）走 widget `actions { action(shortcut:) }`；导航键
  （↑↓/Home/End/PgUp/PgDn/shift.up/shift.down）走列表容器
  `onkeydown.<key>` dot-modifier（auto-term 双面先例）。
- **聚焦守卫**：`focus_in_input` 等效门 = `.addr_editing || 模态 open ||
  .search_focus`（vue 轨 focus 事件可加则加，VM 轨以状态门保守实现，
  两轨取交集语义）。所有全局键 handler 首行守卫。
- **键盘滚动 = 扩窗**：方向键移到窗口边缘（selected_id 接近
  render_cap）自动 GrowRender 一档——键盘可达全表（VM 无
  scroll_to_index 的语义化替代）。
- **批量操作**：循环 `sel_paths` 逐项执行既有单条操作原语
  （copy_recursive/rename/delete/remove_dir_all）；每项失败不中止
  （收集失败计数）；cap 500（超限 toast 拒绝并提示收敛选择）。
- **递归删除**：确认分级——单文件（现行文案）/空目录（现行）/
  非空目录「该文件夹包含 N 个项，将全部永久删除」（N = read_dir
  直接计数；read_dir 失败回落「多个项」文案）。
- **冲突三选**：alert-dialog 模态（`paste_conflict` 状态机：
  pending_name/pending_op/apply_all）；保留两者 = exists 循环探测
  `名 (2)`、`名 (3)`… 递增后缀。

## 技术栈

同 PLAN-001（.at 双轨 + desktop_mcp 注入式驱动）。新增依赖：
`file.remove_dir_all`（ffi 已注册 bare 别名，R5 首任务探针）；
onkeydown dot-modifier 链（auto-term app.at:1042 先例）。

## 需求分析与背景调查

- **授权**：同 PLAN-001（2026-10-04 用户指令，v0.6-dev 分支族，
  app 仓 be44391 文档）。
- **依赖**：PLAN-001 已 merge（sel 投影字段/handler 三层/渐进渲染
  在场）；若 045 未收口本计划不启动。
- **框架事实**：mouse-area onclick 无修饰键信息（无 shift/ctrl-click
  ——勾选列是鼠标多选唯一入口，DESIGN §7）；`File.remove_dir_all`
  ffi 注册（stdlib.rs:464，bare 别名无 auto.file.* 形态——.at 侧
  `file.remove_dir_all(path)` 解析可达性待探针，失败则本计划升格
  决策：要么 auto-lang 补别名（触发跨仓 plan + Category A 门），要么
  .at 侧 walk 逆序删除（有误删风险，v1 已裁定不做）——预期探针通过
  （file.remove_dir 同 bare 形态现行可用）。
- **基线**：045 merge 后的 v0.6-dev HEAD。

## 详细设计

### 状态与 msg 增量

```
var sel_paths = []          // 多选路径键集合
var sel_count int = 0       // 预计算（模板零 .len()）
var sel_bytes int = 0
var anchor_id int = -1
var search_focus bool = false
// 粘贴冲突状态机
var paste_conflict_open bool = false
var paste_conflict_name str = ""
var paste_conflict_apply_all bool = false
var paste_queue = []        // 待粘贴剩余项 {path,name,op}
var paste_done int = 0
var paste_skipped int = 0
```

msg 增量：`ToggleSel(int) SelectAll ClearSel SelectRange(int,int)
KeyNav(str) FocusSearch BlurSearch PasteConflictResolve(str)
PasteConflictApplyAll(bool) ...`（导航键走 onkeydown 直挂 msg）。

### 视图面

- 列表模式行首 28px checkbox 列（`checkbox (checked: item.sel)`，网格
  模式卡左上角浮动小 checkbox）；表头「全选」checkbox（三态降级双态）。
- 状态栏：`sel_count > 0` 时 `{sel_count} 项已选 · {total}` 置换
  selected_info 位；批量操作按钮（复制/剪切/删除 icon-only）随集合唱现。
- 右键菜单增「全选」「清除选择」「反向选择」（view_total ≤ 5000 时）。

### 键盘路由表（actions 声明面）

| 动作 | shortcut | handler |
|------|----------|---------|
| list.open_selected | Enter（已有） | OpenSelected |
| nav.up_level | Backspace | GoUp |
| nav.back / nav.forward | Alt+Left / Alt+Right | GoBack/GoForward |
| file.rename | F2 | StartRenameSelected |
| file.delete | Delete | DeleteSelected |
| edit.copy / cut / paste | Ctrl+C/X/V | CopySelected/CutSelected/PasteInto |
| select.all / select.none | Ctrl+A / Esc | SelectAll/ClearSelAndFilter |
| focus.filter | Ctrl+F | FocusSearch |

onkeydown 面（列表容器）：`up/down/home/end/pageup/pagedown/
shift.up/shift.down` → KeyNav(token)。

### 批量操作与冲突流程

```
PasteInto -> 集合/剪贴板 → 逐项：
  exists(dst)? → 无冲突直接执行
  冲突 → 首个弹 paste_conflict 模态（覆盖/跳过/保留两者 +
        [对剩余 N 项应用]）→ resolve 后继续队列（apply_all 时
        同类冲突自动按选择处理）→ 汇总 toast「已粘贴 X，跳过 Y，
        重命名 Z」→ Reload
DeleteSelected -> 确认模态（集合含目录 → 递归文案 + 项数）→
  逐项 file.delete / file.remove_dir_all → 汇总 toast → Reload
```

### 规范增量

| delta_id | add/modify/retire | docs/specs/... target | before/after rule | rationale | acceptance IDs |
|----------|-------------------|----------------------|-------------------|-----------|----------------|
| SD-0021 | add | docs/specs/apps/file-manager.md | 键盘面契约：双声明面（actions + onkeydown）、focus 守卫门、键盘滚动=扩窗 | TC 键盘流落地 | AC-01 |
| SD-0022 | add | 同上 | 多选契约：sel_paths 路径键模型、勾选列+范围选择、批量操作 cap 500、冲突三选状态机 | 单条目操作洞补齐 | AC-02/AC-03/AC-04 |
| （app 仓侧） | modify | apps/027-file-manager/SPEC.md | §4 文件操作扩展 + 新键盘面节 | 同步 | 全部 |

## 测试设计

- desktop_mcp 新用例（fixture + autoui_keyboard 双通道）：
  - T17 多选：注入 sel_paths → 统计断言 → 勾选/全选/清除/范围四路 →
    批量删除（testdata 副本）磁盘断言；
  - T18 键盘：keyboard 派发 ↑↓×3/End/Home → selected_id 断言；
    F2（模态开）/Delete（确认）/Ctrl+A（sel_count=view_total）/
    Backspace（current_path=parent）/输入态守卫（search_focus 时
    Delete 不触发删除）；
  - T19 冲突三选：构造同名 → 覆盖（内容断言）/跳过（原样）/保留两者
    （`名 (2)` 存在）三分支 + apply_all 批量路；
  - T20 递归删除：嵌套副本（3 层）→ 确认文案含项数 → 删除后
    fs.exists 断言子文件全灭。
- 回归：T1-T18 全绿；vue 轨键盘面 Playwright 冒烟（actions 生成面）。

## 验收标准

- **AC-01** 全键盘可用：T18 断言全过；输入态守卫零误触（焦点在
  搜索框/地址栏/模态时导航键与操作键不劫持）。
- **AC-02** 多选交互：勾选/Ctrl+A/Esc/Shift 范围四路统计与高亮正确；
  状态栏多选统计（N 项 + 总大小）与集合一致。
- **AC-03** 批量操作：批量复制/剪切/粘贴/删除对副本目录磁盘断言
  全过；>500 拒绝门生效；汇总 toast 计数与实际一致。
- **AC-04** 递归删除：3 层嵌套目录删除后子文件全灭；确认文案含
  项数分级；vue 轨守卫维持（写操作 VM 专属文案）。
- **AC-05** 冲突三选：覆盖/跳过/保留两者三分支 + apply_all 批量
  应用断言全过（T19）。
- **AC-06** 回归：T1-T18 + Playwright 全绿。

## 执行步骤

- **T-01** remove_dir_all 探针 + 递归删除落地
  文件：apps/027-file-manager/src/front/app.at
  操作：fixture 探针 `file.remove_dir_all` 解析可达性（失败 → 升格
  决策路径，见背景）；ExecuteDelete 分级文案 + 递归分支。
  验证：desktop_mcp T20。
  → AC-04
- **T-02** 多选模型与视图面
  文件：app.at + fs_util.at（sel 统计纯函数）
  操作：sel_paths/anchor/统计状态；勾选列/全选/范围/清除；状态栏
  批量按钮；RefreshView sel 投影接线。
  验证：desktop_mcp T17。
  → AC-02
- **T-03** 键盘双声明面 + 聚焦守卫
  文件：app.at
  操作：actions 路由表 + onkeydown 导航族 + focus 守卫门 + 边缘自动
  GrowRender。
  验证：desktop_mcp T18；vue 轨 actions 生成面冒烟。
  → AC-01
- **T-04** 批量操作
  文件：app.at
  操作：CopySelected/CutSelected/PasteInto/DeleteSelected 循环 + cap
  门 + 汇总 toast；右键菜单增项。
  验证：desktop_mcp T17 批量路 + T1-T14 回归。
  → AC-03
- **T-05** 粘贴冲突三选模态
  文件：app.at（paste_conflict 状态机 + alert-dialog）
  操作：三选 + apply_all + `名 (2)` 递增探测。
  验证：desktop_mcp T19。
  → AC-05
- **T-06** 测试收口
  文件：tests/desktop_mcp.py（T17-T20）+ Playwright 冒烟
  操作：按测试设计实现跑通。
  验证：全套件全绿。
  → AC-06
- **T-07** 文档同步
  文件：SPEC.md（§4+键盘面节）、README、REQUIREMENTS 状态列、
  DESIGN revision 绑定
  验证：diff 复查。
  → 全 AC 证据链

## 复审记录

- 2026-10-04 stage: new（auto-plan-new 起草，plan_revision 1）。outcome:
  pass。next: work（前置：PLAN-001 merged）。R5 探针为唯一外部依赖，
  失败时升格用户决策（auto-lang 变更门）。


- 2026-10-04 stage: work | PLAN-002 | rev 1 | outcome: **pass
  （execution_done）** | code_commit: fe57d26（分支 plan-002，基线 a5e8c4a
  = 001 tip 叠加——用户裁定全部实施完再复审）| task_ids: T-01..T-05 全
  + T-06 收口 + T-07 文档随终态统一（SPEC 更新并入 004 后统一提交）
  | evidence: tests/verify_p2.py **12/12**（多选统计/范围/批量复制粘贴/
  保留两者落盘 a (2).txt/递归树内容全灭/键盘导航+输入态守卫）；
  desktop_mcp **74/74** 全绿 | 执行内裁决记录：① Ctrl+F 聚焦延后
  （ui.focus 原生接线面未证，不冒险）；② fs.stem 符号不链入 app 会话
  （Undefined symbol 实证——sort_by 同族病，字符串切除替代）；③ actions
  标题渲染进 vtree（T7 确认改 fixture 通道）；④ 套件 T19-T22 以
  verify_p2.py 承载（套件编号冲突 + 环境病期独立短探针更稳——环境病
  = SPEC §6 MCP 失联恶化，stash 对照证 001 绿态同灭，非本计划回归）；
  ⑤ keepboth 序号插扩展名前（Windows 惯例）。

- 2026-10-04 stage: review | PLAN-002 | rev 1 | outcome: **pass** |
  reviewed_commit: 1422701（本计划增量 fe57d26）| base_commit: a5e8c4a |
  acceptance_results: AC-01 pass（键盘面全量：导航族 + 11 actions +
  输入态守卫——verify_p2 T20；Ctrl+F 延后有据）／AC-02 pass（多选
  统计/范围/勾选——T19）／AC-03 pass（批量复制粘贴/删除 + 500 门 +
  汇总——T19/T22 磁盘断言）／AC-04 pass（递归删除 3 层树 + 分级文案
  ——T22）／AC-05 pass（冲突三选 + 保留两者 a (2).txt——T21）／
  AC-06 pass（vue codegen 键盘/勾选要素生成 + 守卫文案在生成面）|
  findings: 无新增（F-V1/F-V2 记于 001 终态复审——共享面）|
  evidence: verify_p2 12/12 + 套件 74/74（1422701 全新跑）| next:
  merge。

## 待澄清事项

- 无（R5 探针失败路径已在背景节定义为升格决策，非本计划内静默回退）。
