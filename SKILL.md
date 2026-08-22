---
name: llm-wiki-lite
description: 维护 wiki/ 知识库：录入（Ingest）、查询（Query）、检查（Lint）。触发词：建wiki、录入、ingest、知识库、查wiki、lint wiki。
version: 1.5.0
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

1. **阶段 A 变更检测（零 LLM）**：`python scripts/wiki_changed.py <project-root>`——版本漂移 + git 兜底（docs/ 自上次 wiki 提交以来的变更）+ 未录入文档盘点，一次产出待处理清单；无变化即结束（no-op）。
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

执行 Lint 检查（事实校验 + 行为度量，内部由工具决定），读取报告并按其中的行为建议响应：可自动修的修复，待录入/待修索引等缺口清单排入 Ingest 待办。

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
- 页面按前缀归子目录（见上目录树），双链按文件名解析，迁移/重组零链接成本。

### 命名与双链（一页一物）

- 文件名固定 `<前缀>-<标识>.md`，前缀 ∈ {`con-`（概念）、`src-`（来源）、`ent-`（实体）、`dec-`（决策/技术债/待裁定）、`syn-`（主题综合）}；**标识语言定死一种，永不更改**。
- 页面按前缀归对应子目录（见上目录树）；`log.md` 与 `index.md` 留在 `wiki/` 根，新建页面必须放进对应子目录。
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

只追加，永不修改或删除。Ingest 全部 disposition（含 No material）记入；Query 不记录。**访问纪律：只允许追加（所属小节末尾）/ 读尾部 / grep 定点检索，禁止全文读入上下文。**

格式（按来源分小节，小节按来源名排序插入）：

```
## src-SkillBus架构设计文档
- [2026-08-21] ingest | con-能力契约 · Disposition: Update · Cascade: con-能力执行与路由

## <源文档文件名>（no material 无 src 页，以文件名为小节名）
- [2026-08-21] ingest | no material: docs/req/薄文档.md · Disposition: No material
```

初始化：首次 Ingest 创建 `# Wiki Log` 标题，之后只追加。

---

## 页面模板

模板与使用规则见 `assets/pages-templates.md`（src / con / ent / dec / syn 五类 + 懒建/syn 分工/膨胀拆分等）。

## 脚本工具（scripts/）

脚本路径均**相对本 skill 目录**（SKILL.md 所在目录），执行时以 skill 实际安装路径拼接。零第三方依赖（Python 3.8+ 标准库）：

| 脚本 | 对应 | 行为 |
|------|------|------|
| `wiki_lint.py` | Lint 检查 | 统一入口：编排事实校验 + 覆盖度 + 需求侧度量，合并为一份报告；含 `Knowledge gaps`（`[INGEST]` 待录入 / `[FIX-INDEX]` 待修索引） |
| `wiki_changed.py` | Ingest 阶段 A | 版本漂移 + git 兜底 + 未录入盘点；输出待处理清单 |
| `rebuild_index.py` | index 重建 | index 投影重建（阶段 D / 会话开始 / pull 后）；`--check` 只报陈旧度 |

## 冲突解决规则（多人协作）

| 冲突文件 | 解决规则 | 执行者 |
|---------|---------|--------|
| log.md | 两边条目全保留，按时间戳排序 | AI 可机械执行 |
| 页面内容 | 语义合并（按四阶段重新 compile 或人工） | 需判断，人/AI 协作 |

## 升级路径

按需扩展页面类型（如 `cmp-` 对比类）。
