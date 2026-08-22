---
name: llm-wiki-lite
description: 维护 wiki/ 知识库：录入（Ingest）、查询（Query）、检查（Lint）。触发词：建wiki、录入、ingest、知识库、查wiki、lint wiki。
version: 1.4.0
---

# 精简 LLM-Wiki（LLM-Wiki Lite）

## 意图

维护一个由 AI 编写和管理的知识库（`wiki/`）：把 `docs/` 源文档中的可复用知识编译成双链页面，随源文档增量更新，每个事实可回溯、可验证。**人负责 `docs/`（AI 只读），AI 负责 `wiki/`（唯一可写区）**。

## 操作入口（用户意图 → 动作）

| 用户意图 | 执行 |
|---------|------|
| 录入 / 新来源 / "文档更新了" | **Ingest**（四阶段流水线） |
| 问知识 / "库里有没有 X" / "总结一下 Y" | **Query** |
| 检查 / lint / 度量健康度 | **Lint**（含事实校验 + 行为度量；无遥测时度量部分跳过） |
| 记录决策 / 技术债 / 注意事项 / 待裁定 | 新建 dec- 页（带日期，可关闭） |
| 提交冲突处理 | 冲突解决规则表（文末） |
| 建库 / 初始化 | 首次 Ingest 自动初始化（见操作日志） |

## 操作规程

### Ingest（录入）——四阶段流水线

首次全量录入同样适用（diff 即全文）。仅沉淀**跨文档可复用**的知识。

1. **阶段 A 变更检测（零 LLM）**：`python skills/llm-wiki-lite/scripts/wiki_changed.py <project-root>`——版本漂移 + git 兜底（docs/ 自上次 wiki 提交以来的变更）+ 未录入文档盘点，一次产出待处理清单；无变化即结束（no-op）。
2. **阶段 B 影响面分析（低 token）**：读 **diff 而非全文**；`get_backlinks` 取受影响页面；**Triage 声明 disposition**（No material 则仅在 log 记台账后停止）。
3. **阶段 C 增量编译（LLM 集中投入）**：输入 = diff + 受影响页面；**输出 = 最小补丁，禁止整页重写**（无关段落不顺手动）；新字面量遵循 locate-before-write（见硬性约定）。
4. **阶段 D 确定性收尾（零 LLM）**：刷新 src 页 `raw`/`version`/`date`；执行连带更新；index 若启用则跑 `rebuild_index.py`；log 分小节追加。

**Triage 分诊表**（阶段 B 强制声明，编译之前；分诊前先用源文档关键实体检索 wiki）：

| Disposition | 含义 | 动作 |
|-------------|------|------|
| **New** | wiki 没有覆盖此知识 | 新建页面 |
| **Update** | 已有页面需吸收新信息 | 更新既有页面（含连带更新） |
| **Disputed** | 与已有内容矛盾 | 更新 + Status 块标注 |
| **No material** | 相对 wiki 现有内容无新增 | **停止，不硬凑文章**，仅在 log.md 记台账 |

New / Update / Disputed 可组合；No material 独占。

### Query（查询）

经检索面（优先 obsidian-mcp-pro：`search_notes` 语义检索 / `resolve_alias` 别名解析 / `get_backlinks` 反链）→ 无果再全文检索关键实体及同义词 → 读相关页面 → 不足则回源文档补充 → 回答时引用依据。**查询不写文件**。

### Lint（检查）

```
python scripts/wiki_lint.py <project-root> [--events telemetry.jsonl]
```

执行 Lint 检查（事实校验 + 行为度量，内部由工具决定），读取报告并按其中的行为建议响应：可自动修的修复，待录入/待修索引等缺口清单排入 Ingest 待办。报告尾部记入 `log.md` 的 `## lint` 小节。

---

## 硬性约定（执行中随时对照）

### 目录与读写边界

```
<project-root>/
├── docs/      # 源文档（AI 只读；跨仓库材料拷副本入此）
└── wiki/      # 知识库（AI 唯一可写区）
    ├── log.md          # 操作日志（只追加）
    ├── index.md        # 本地投影缓存（不提交，rebuild 重建）
    ├── sources/        # src-* 来源页（一文档一页，持有 raw 坐标）
    ├── concepts/       # con-* 概念页（跨文档可复用知识节点）
    ├── entities/       # ent-* 实体页（系统/模块/协议等实体）
    ├── syntheses/      # syn-* 综述页（跨多源知识拼装）
    └── decisions/      # dec-* 决策/技术债/待裁定页
```

- `docs/` 只读：不修改、不搬移、不改写；知识对原文只引用不转述，冲突以原文为准。
- 页面按前缀归子目录（见下「命名与双链」映射表），双链按文件名解析，迁移/重组零链接成本。

### 命名与双链（一页一物）

- 文件名固定 `<前缀>-<标识>.md`，前缀 ∈ {`con-`（概念）、`src-`（来源）、`ent-`（实体）、`dec-`（决策/技术债/待裁定）、`syn-`（主题综合）}；**标识语言定死一种，永不更改**。
- **子目录按前缀映射**（每种类型独立子目录，不再平铺 wiki 根）：

  | 前缀 | 子目录 | 说明 |
  |------|--------|------|
  | `src-` | `wiki/sources/` | 来源页 |
  | `con-` | `wiki/concepts/` | 概念页 |
  | `ent-` | `wiki/entities/` | 实体页 |
  | `syn-` | `wiki/syntheses/` | 综述页 |
  | `dec-` | `wiki/decisions/` | 决策/技术债页 |

  `log.md` 与 `index.md` 留在 `wiki/` 根。新建页面必须放进对应子目录。
- 链接固定 Obsidian 双链 `[[页面名]]`（vault 根 = project-root），与目标文件名完全一致，**不含目录路径**；双链解析与目录无关，迁移子目录零链接成本。
- 建页前先查检索面：近似概念**更新而非新建**；同一概念禁止两个页面（重复检测由检索面兜底）。

### 检索面与 index（本地投影缓存）

- 任何操作第一步经过同一可复现的检索面：MCP（`search_notes` / `list_tags`）。
- index.md 不提交（gitignore）：页面 <15 无需 index；需要时按投影纪律建（链接←文件名、摘要←固定小节首句，无独有信息）；重建用 `rebuild_index.py`，触发于每次会话开始 / pull 后；**陈旧 index 必须机械重建，禁止随手维护**；新克隆冷启动先跑一次。

### 证据坐标与版本锚点

每个页面必须声明证据坐标，无来源的论断不得写入（AI 推断须标注"推断"+依据）。字段规范（`raw` 证据路径用本仓库相对路径、禁止绝对盘符/外部仓库；`version` 录入时源文档版本号、无则省略；`date` 源文档日期；字段准入仅 `tags/aliases/raw/version/date`）与 locate-before-write（数字/版本/日期/引语写入前先回源定位、找不到出处不写精确值）的**完整细则以 `wiki_check.py` 校验为准——跑不过即改**。dec- 页 `raw` 可选、`date`=决策日期。

### 连带更新

写任何页面时同步：① 检索引用了本页主题的页面（优先 `get_backlinks`），更新其中过时表述（增量时只改 diff 涉及部分）；② 更新检索面（index 启用时重建对应行）。

### Status 块（过时/争议的标准处置）

旧论断不删，禁止静默改写被推翻的论断。格式（紧贴受影响论断正下方，Outdated 需含日期+变更说明+来源三要素、缺一即 malformed，由 `wiki_check.py` 校验）：

```markdown
- <旧论断原样保留>

  > **Status: Outdated** (2026-08-05)
  > <变更说明：新事实是什么>。来源：[[src-xxx]]
```

Disputed：来源打架时两边页面都标、并列写各自出处、不裁决。dec- 页关闭用 **Resolved**/**Superseded**（带日期，lint 同查三要素）。漂移闭环：lint 发现 `version` 落后 → 复核 → 过时论断加 Status 块、有效论断改写并刷新锚点。

### 操作日志（log.md）

只追加，永不修改或删除。Ingest 全部 disposition（含 No material）与 Lint 结论记入；Query 不记录。**访问纪律：只允许追加（所属小节末尾）/ 读尾部 / grep 定点检索，禁止全文读入上下文。**

格式（按来源分小节，小节按来源名排序插入）：

```
## src-SkillBus架构设计文档
- [2026-08-21] ingest | con-能力契约 · Disposition: Update · Cascade: con-能力执行与路由

## <源文档文件名>（no material 无 src 页，以文件名为小节名）
- [2026-08-21] ingest | no material: docs/req/薄文档.md · Disposition: No material

## lint（固定在文件末尾）
- [2026-08-21] 3 issues found, 1 auto-fixed
```

初始化：首次 Ingest 创建 `# Wiki Log` 标题，之后只追加。

---

## 页面模板

### 来源页 `src-<标识>.md`（落盘 `wiki/sources/`）

```markdown
---
tags: [...]
raw: <本仓库内相对路径；跨仓库材料先拷副本入 docs/；同一逻辑来源的多文件可写数组>
version: <录入时版本号，无则省略>
date: YYYY-MM-DD
---

# <来源标题>

## 摘要
<一段话：这个来源里有什么>

## 关键要点
1. <要点>（承重数字/版本/日期遵循 locate-before-write）

## 产出的概念
- [[con-xxx]]
```

### 概念页 `con-<概念名>.md`（落盘 `wiki/concepts/`）

```markdown
---
tags: [...]
aliases: [...]      # 可选：本概念的其他叫法（从源文档实际用词登记，不发明）
---

# <概念名>

## 定义
<一段话>

## 核心原理
<详细解释；被推翻的论断加 Status 块>

## 来源
- [[src-xxx]]

## 关联
- [[con-xxx]]
```

**主题枢纽模式**（需求/设计/调试三件套的典型用法）：con 页按**业务主题**建（如 `con-录像`），「核心原理」替换为知识维度分节——需求要点 / 设计要点 / 调试要点，每节末双链对应 src 页，同一主题散落在三类文档中的知识在一个页面内串联；src 页仍按**文档**建（一文档一页），同一文档可被多个主题枢纽引用，多源关系由 con 页来源区列表表达。

### 实体页 `ent-<实体名>.md`（落盘 `wiki/entities/`；实体名用其代码/系统原名，如 `ent-SkillBus`）

```markdown
---
tags: [...]
aliases: [...]
---

# <实体名>

## 定义
<一段话>

## 关键信息
- <信息点>

## 来源
- [[src-xxx]]

## 关联
- [[con-xxx]]
```

### 决策页 `dec-<主题>.md`（落盘 `wiki/decisions/`；ADR / 技术债 / 注意事项 / 待裁定）

```markdown
---
tags: [...]
raw: <决策依据文档，可选；无则省略>
date: YYYY-MM-DD        # 提出/决策日期
---

# <主题>

## 背景
<为什么需要决策 / 债务如何产生 / 注意事项来龙去脉>

## 决定 / 结论
<选定方案；待裁定写「待裁定」+ 候选方案；关闭状态由 Status 块表达，本区不改写>

## 依据
<理由与备选方案否决原因；无文档依据时写明决策人与场合>

## 关联
- [[con-xxx]] / [[src-xxx]]
```

### 综合页 `syn-<主题>.md`（落盘 `wiki/syntheses/`；主题综合：跨多源知识的拼装，如需求/设计/调试三件套）

```markdown
---
tags: [...]
aliases: [...]
---

# <主题>

## 定义
<一句话；已有概念页则双链 [[con-<主题>]]，不重复展开>

## 需求要点
- <提炼后的理解要点；承重事实遵循 locate-before-write>

依据：[[src-xxx]]

## 设计要点
- <同上>

依据：[[src-xxx]]

## 调试要点
- <同上>

依据：[[src-xxx]]

## 来源
- [[src-xxx]]
- [[src-xxx]]
- [[src-xxx]]

## 关联
- [[con-xxx]] / [[syn-<子主题>]]
```

- **懒建判据**：同一主题的知识拼装在查询中**重复发生**才建 syn——默认只建 src 页（syn 离开多源聚合不成立，预建是浪费）。
- **con/syn 分工**：con 存"是什么"（概念、原理，单页自足）；syn 存"全景拼装"（跨源多维）。简单主题可只有 syn 无独立 con。
- **小节化写入纪律**：每次更新只动所属小节。
- **膨胀拆分**：按**子主题**拆 `syn-<子主题>`（各自继承多维结构），本页降级为索引——**禁止按文档类型拆回三页**。
- 知识维度不限于需求/设计/调试，按主题实际涉及的来源类型定。

## 脚本工具（scripts/）

四个确定性脚本，零第三方依赖（Python 3.8+ 标准库）：

| 脚本 | 对应 | 行为 |
|------|------|------|
| `wiki_check.py` | Lint、验证端 | 字面量保真 + 版本漂移 + 证据错误（含文件名重复）三项扫描 |
| `rebuild_index.py` | 检索面 index | index 投影重建（分表、行按文件名排序、摘要←固定小节首句）；`--check` 只报陈旧度 |
| `wiki_changed.py` | Ingest 阶段 A | 版本漂移 + git 兜底 + 未录入盘点；输出待处理清单 |
| `wiki_coverage.py` | 供给侧度量 §10.2 | 静态覆盖度：取 `raw` 源文档章节树，与"吸收文本"（src 页正文 ∪ 所有 `[[src-xxx]]` 指向它的页面正文）做零 LLM 命中比对，输出每源覆盖率与未吸收章节 + `coverage_avg` |
| `wiki_metrics.py` | 需求侧度量 §10 | 消费 obsidian-mcp-pro 的调用遥测，算回源/失败/命中/跳数/refine 率 + 按源文档聚合 + 闭环对比；额外输出 `Knowledge gaps` 块，把未命中 query 分类为"待录入(MISSING)"或"待修索引(STALE_SEARCH)" |

### 需求侧度量（§10）操作规程

需求侧度量消费 obsidian-mcp-pro 在**工具调用层零 LLM 采集**的真实查询行为（§10.3 前提）：`wiki_metrics.py` 把遥测事件流做查询归并（§10.4）→ 算维度 4–8（§10.3）→ 按 `raw` 坐标聚合到每个源文档（§10.5）→ 可选 `--baseline`/`--report compare` 闭环对比（§10.6）。

启用与运行：

```bash
# 0. 静态覆盖度（冷启动/无查询行为时首选，零 LLM，report-only）
python scripts/wiki_coverage.py <project-root> [页面文件名 ...]
#   输出每源 章节/术语 命中率 + 未吸收章节清单 + coverage_avg；
#   标签 OK(>=80%) / LOW(>=40%) / GAP(<40%)。

# 1. 启动 obsidian-mcp-pro 时打开遥测（事件落盘）
OBSIDIAN_TELEMETRY=/abs/path/telemetry.jsonl node dist/index.js

# 2. 真实查询积累后，跑度量（vault = 项目根）
python scripts/wiki_metrics.py --events telemetry.jsonl --vault <project-root>

# 3. refine 前后对比（先存基线）
python scripts/wiki_metrics.py --events now.jsonl --vault <project-root> \
    --baseline before.jsonl --report compare
```

诚实边界（§10.7）：仅记录走 obsidian-mcp-pro 的调用，UI/manual 查询采集不到须标注；检索未命中用"短搜索回文"代理（可调 `--miss-bytes`），属近似。报告只给行为统计与 refine 优先级队列，不产出"质量分数"；refine 优先级清单作为**行动建议**交 AI 复核。

### 检查报告驱动操作

Lint 输出的报告（含事实校验与行为度量）直接作为后续操作的输入，而非事后体检：硬错误进入修复，缺口清单（待录入/待修索引、未吸收章节）进入 Ingest 待办并按优先级排序。

## 冲突解决规则（多人协作）

| 冲突文件 | 解决规则 | 执行者 |
|---------|---------|--------|
| index.md | 不存在冲突（本地缓存，不提交） | — |
| log.md | 两边条目全保留，按时间戳排序 | AI 可机械执行 |
| 页面内容 | 语义合并（按四阶段重新 compile 或人工） | 需判断，人/AI 协作 |

## 升级路径

按需扩展页面类型（如 `cmp-` 对比类）。
