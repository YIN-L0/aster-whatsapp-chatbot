# Aster WhatsApp MVP — 技术架构

> 版本：v0.1-draft · 面向：单人开发 + 单人运维

---

## 1. 架构原则与关键取舍

| 决策 | 选择 | 不选 | 理由 |
|---|---|---|---|
| 服务形态 | **模块化单体**：一个 Python 包、一个 Docker 镜像，运行为 `web` 与 `worker` 两个进程 | 微服务 | 单人开发；模块边界靠目录和接口约束，不靠网络 |
| Web 框架 | FastAPI + Pydantic v2 | — | 需求指定；Pydantic 同时用于 LLM 结构化输出验证 |
| 数据库 | PostgreSQL 16 + `pgvector` 扩展 | 独立向量库（Pinecone/Qdrant） | 知识库只有约 20 个页面、几百个 chunk，Postgres 足够；少一个系统 |
| ORM / 迁移 | SQLAlchemy 2.0（async）+ Alembic | — | 成熟、类型友好 |
| 后台任务与定时 | **Postgres 任务表 + 自写 worker 循环**（`SELECT … FOR UPDATE SKIP LOCKED`） | Celery + Redis、APScheduler 持久化 | 不引入 Redis；任务与业务数据同库事务一致；回访本质上是“到点执行的数据库行” |
| Agent 编排 | **单 Agent + 工具调用循环**（自写，约 200 行） | LangGraph、LangChain、Multi-Agent | 流程是确定的管道，只有“回复生成”一步需要 LLM 自主；自写更易调试、易替换模型 |
| LLM | `LLMProvider` 接口，适配器：Anthropic（默认）/ OpenAI / Fake（测试） | 绑定单一 SDK | 需求要求可替换；Fake 适配器让测试不依赖网络 |
| RAG | Postgres 混合检索：`tsvector` 全文 + `pgvector` 余弦，RRF 融合 | 复杂 reranker、GraphRAG | 语料极小，混合检索够用；评测证明不够再加 rerank |
| 安全 | **独立 Safety 模块**：确定性规则 + LLM 分诊 + 输出后检 | 只靠 system prompt | 医疗场景；安全判断不能被“人设”影响 |
| 员工侧 | 通知（Email / 员工 WhatsApp）+ 带签名链接的最小处理页 + CLI | Web Dashboard | 需求排除 Dashboard；但必须能关闭 handoff |
| 部署 | Docker 镜像 → 任意容器平台；托管 Postgres | K8s | 单人可运维 |

**扩展预留但不实现**：`channel_identities`（未来网页登录）、`clinic_id`（多诊所）、`BookingAdapter`（Vagaro）、`journey.visibility`（未来社区分享）、`Notifier` 多渠道。

---

## 2. 系统上下文

```
                         ┌────────────────────────────────────────────┐
  Patient (WhatsApp) ───▶│ Meta WhatsApp Cloud API                    │
          ▲              └───────────────┬──────────────▲─────────────┘
          │                    webhook   │              │ Graph API (send)
          │                              ▼              │
          │        ┌──────────────────────────────────────────────────┐
          │        │                 Aster (one image)                │
          │        │  ┌──────────────┐        ┌────────────────────┐  │
          │        │  │ web (FastAPI)│        │ worker             │  │
          │        │  │ - webhook    │ jobs   │ - inbound pipeline │  │
          │        │  │ - admin API  │──────▶ │ - check-in sender  │  │
          │        │  │ - health     │ (PG)   │ - reminders/expiry │  │
          │        │  └──────┬───────┘        └────────┬───────────┘  │
          │        │         └───────────┬─────────────┘              │
          │        │                     ▼                            │
          │        │         ┌──────────────────────┐                 │
          │        │         │ PostgreSQL + pgvector│                 │
          │        │         └──────────────────────┘                 │
          │        └───────┬─────────────────┬────────────────────────┘
          │                │                 │
          │                ▼                 ▼
          │      LLM / Embedding API    Notifier (Email / staff WhatsApp)
          │      (Anthropic | OpenAI)          │
          │                                    ▼
          └──────────── Clinic staff (phone / Vagaro / existing channels)
```

---

## 3. 代码结构（模块化单体）

```
aster/
├── app/
│   ├── main.py                 # FastAPI app factory，挂载路由
│   ├── config.py               # pydantic-settings，所有配置来自环境变量
│   ├── db/                     # engine、session、Base、alembic/
│   │
│   ├── channels/               # 渠道适配层（只有这里知道 WhatsApp）
│   │   ├── base.py             # ChannelAdapter 接口：parse_inbound / send_text / send_buttons / send_template
│   │   └── whatsapp/
│   │       ├── webhook.py      # GET 验证、POST 接收、签名校验
│   │       ├── client.py       # Graph API 调用、重试
│   │       ├── schemas.py      # Webhook payload Pydantic 模型
│   │       └── templates.py    # 已审核模板名与参数映射
│   │
│   ├── conversation/           # 入站消息管道编排（核心用例层）
│   │   ├── pipeline.py         # handle_inbound(user_id)：§5 的完整流程
│   │   ├── commands.py         # STOP / START / HUMAN / DELETE 等确定性命令
│   │   ├── context.py          # 组装 Agent 上下文（profile、journey、症状时间线、最近消息）
│   │   └── outbound.py         # 发送 + 24h 窗口判断 + 落库
│   │
│   ├── agent/                  # LLM 相关的一切
│   │   ├── llm/                # LLMProvider 接口 + anthropic.py / openai.py / fake.py
│   │   ├── prompts/            # system prompt 模板（版本化文件）
│   │   ├── tools/              # 工具定义与执行器（调用下面的领域服务）
│   │   ├── runner.py           # 工具调用循环、最大步数、超时
│   │   └── schemas.py          # AgentTurn 等结构化输出模型
│   │
│   ├── safety/
│   │   ├── rules/              # red_flags.yaml（版本化，需临床签字）
│   │   ├── prefilter.py        # 确定性规则匹配
│   │   ├── triage.py           # LLM 风险分诊（结构化输出）
│   │   ├── postcheck.py        # 输出后检（禁止话术、未引用的事实、预约承诺）
│   │   └── responses.py        # L3/L4 固定回复模板
│   │
│   ├── knowledge/
│   │   ├── ingest.py           # 抓取 → 清洗 → 人工审核文件 → chunk → embed
│   │   ├── retrieval.py        # 混合检索 + scope 过滤
│   │   └── content/            # 审核后的知识 Markdown（进 git，可 diff 审核）
│   │
│   ├── identity/               # users、channel_identities、consents
│   ├── journeys/               # journeys、journal_entries、symptom_observations、pending_actions
│   ├── followups/
│   │   ├── protocols/          # <treatment>.yaml（版本化，需临床签字）
│   │   ├── planner.py          # journey → checkins
│   │   └── sender.py           # 到点发送
│   ├── handoff/                # handoffs、通知、human_mode、签名链接处理页
│   ├── jobs/                   # 任务表、enqueue、worker 主循环
│   └── admin/                  # Bearer-token 保护的最小管理 API
│
├── scripts/
│   ├── simulate.py             # 本地终端模拟 WhatsApp 对话（不需要 Meta）
│   ├── seed_mock_data.py       # 模拟患者与 journey
│   └── run_evals.py            # 执行 tests/evals 场景
├── tests/{unit,integration,evals}/
├── docker-compose.yml          # postgres(pgvector) + web + worker
└── Dockerfile
```

**模块依赖规则**（用 `import-linter` 在 CI 中强制）：

```
channels  ──▶ (nothing domain)
conversation ──▶ channels, agent, safety, identity, journeys, handoff, followups
agent/tools ──▶ knowledge, journeys, handoff, followups   (只通过各模块 service.py)
safety ──▶ (不依赖 agent，可单独测试)
domain 模块（identity/journeys/followups/handoff/knowledge）──▶ db
```

领域模块之间不直接 import 彼此的 ORM 模型，只调用 `service.py` 中的函数。

---

## 4. WhatsApp Cloud API 集成要点

| 主题 | 设计 |
|---|---|
| Webhook 验证 | `GET /webhooks/whatsapp`：校验 `hub.verify_token`，返回 `hub.challenge` |
| 签名 | `POST` 校验 `X-Hub-Signature-256`（HMAC-SHA256，App Secret），失败返回 401 |
| 快速应答 | Webhook 只做：校验 → 解析 → 落库 `messages`（`wa_message_id` 唯一约束去重）→ 写 `jobs` → 返回 200。不在请求内调用 LLM |
| 幂等 | Meta 会重投；`INSERT … ON CONFLICT (wa_message_id) DO NOTHING` |
| 状态回调 | `statuses`（sent/delivered/read/failed）更新 `messages.delivery_status`；`failed` 记录错误码（如模板被拒、用户号码不可达） |
| 消息类型 | MVP 处理：`text`、`interactive.button_reply`、`interactive.list_reply`；`image/audio/video/document` → 固定回复“暂不支持”（照片策略待确认）；`reaction` 忽略 |
| 24h 客服窗口 | `outbound.py` 依据该用户最近一条入站消息时间判断：窗口内发自由文本；窗口外只能发审核过的模板 |
| 模板 | 回访首条用 utility 模板，如：`aster_checkin_v1`：“Hi {{1}}, it's Aster checking in on your recovery. How are you feeling today?”。**模板中不放治疗名或症状** |
| 交互按钮 | 旅程确认、同意书、退订使用 reply buttons（最多 3 个，标题 ≤ 20 字符） |
| 速率与顺序 | 同一用户的入站任务用 `pg_advisory_xact_lock(user_hash)` 串行；1.5 s 合并窗口把连发的多条消息合并处理 |
| 政策 | Meta 2026 年起限制“通用 AI 助手”使用 Business API，业务型客服 Bot 允许。Aster 必须定位为诊所服务助手，Prompt 中拒绝通用任务 |
| 本地开发 | `ChannelAdapter` 有 `ConsoleAdapter` 实现，`scripts/simulate.py` 直接驱动 pipeline |

> 需在接入时以 Meta 官方文档再次核对：模板分类与计费规则、交互消息限制、Graph API 版本号。

---

## 5. 入站消息处理流程

```
[Meta] ──POST──▶ webhook.py
                   1. verify signature
                   2. parse → InboundMessage[]
                   3. resolve/create user via channel_identities (wa_id)
                   4. INSERT messages ON CONFLICT DO NOTHING
                   5. INSERT jobs(kind='process_inbound', user_id, run_at=now()+1.5s)  ← 合并窗口
                   6. return 200
                                │
worker ◀────────────────────────┘
pipeline.handle_inbound(user_id)   [持有该用户 advisory lock]
  ├─ A. 读取该用户所有 status='received' 的入站消息（合并为一个 turn）
  ├─ B. 确定性命令？STOP / START / HUMAN / DELETE MY DATA → commands.py → 回复 → 结束
  ├─ C. 按钮回复？（payload 含 pending_action_id / consent）→ 确定性处理（提交或取消）→ 回复 → 结束
  ├─ D. 未完成同意？→ onboarding 流程（只允许 L0 服务问答）
  ├─ E. Safety prefilter（规则）  ─┐ 并行
  │     Safety triage（快速 LLM）  ─┘ → risk = max(rule, triage)
  │     L4 → 固定急救模板 + 创建 urgent handoff + 通知 → 结束（不调用主 Agent）
  ├─ F. human_mode == true？→ 不自动回复（仅首次给一句“团队已收到”）+ 把新消息追加到 handoff → 结束
  ├─ G. context.build()：profile、active journey、术后第 N 天、症状时间线、
  │     活跃 checkin 及其协议问题、pending actions、最近 20 条消息、triage 结果
  ├─ H. agent.runner.run(context) → 工具循环（≤ 5 步）→ AgentTurn（结构化）
  ├─ I. safety.postcheck(AgentTurn)
  │     - risk_final = max(risk, AgentTurn.risk_level)
  │     - 违规（剂量建议、诊断语、“已预约”、无出处的价格）→ 用安全回退文案替换或重新生成 1 次
  │     - risk_final ≥ L2 且未创建 handoff → 自动创建
  ├─ J. 事务提交：agent_run、journal/symptom 写入、pending_actions、handoff、checkin 状态
  ├─ K. outbound.send()（文本 / 按钮）→ messages(direction='out')
  └─ L. 标记入站消息 status='processed'；失败 → job 重试（指数退避，最多 3 次），
        最终失败 → 给用户发送“稍后回复”兜底文案并告警
```

**关键点**：

- LLM 只在 E（triage）和 H（主 Agent）两处被调用；其余都是确定性代码。
- 写正式数据（journey）只在 C 步由按钮确认触发，**永不由 LLM 直接写入**。
- Journal / 症状观察由工具在 H 步“提交给服务层”，服务层做 schema 校验后在 J 步事务落库。

---

## 6. 各模块如何协作：RAG × Journey × Journal × Follow-up

```
                 ┌───────────── followup_protocols/<treatment>.yaml ───────────────┐
                 │ schedule: [day1, day2, day7, day14]                              │
                 │ questions per day, expected_symptoms, red_flags, aftercare_ref   │
                 └──────┬─────────────────────────────────────┬────────────────────┘
                        │ (1) journey confirmed               │ (4) check-in context
                        ▼                                     ▼
  User msg ──▶ Agent ──propose_journey──▶ pending_action ──confirm──▶ journeys ──▶ planner ──▶ checkins
                 │                                                         │            │
                 │ (2) record_recovery_update                              │            │ (3) due
                 ▼                                                         │            ▼
           journal_entries + symptom_observations ◀── reply to check-in ──┼──── sender ──▶ WhatsApp
                 │                                                         │
                 │ (5) symptom timeline + trend                            │
                 ▼                                                         │
            context.build() ◀──────────────────────────────────────────────┘
                 │
                 │ (6) search_knowledge(scope)
                 ▼
     kb_chunks: service_info  (官网，服务问答)
                clinical_approved (诊所审核的护理说明，仅用于术后话术)
```

1. **Journey 确认** → `followups.planner` 读取该治疗的协议，生成 `checkins`（`due_at` 按用户时区和发送时段计算）。修改治疗日期 → 取消未发送的 checkins 并重新生成。
2. **Recovery Journal**：用户主动描述或回复 check-in → Agent 调用 `record_recovery_update` → 写入 `journal_entries`（摘要）与 `symptom_observations`（结构化），若有活跃 checkin 则关联并标记 `answered`。
3. **Follow-up 发送**：worker 每分钟扫描 `checkins where status='scheduled' and due_at <= now()` → 窗口外发模板 / 窗口内发自由文本（由 Agent 根据协议问题生成个性化开场，或用协议里的固定开场）→ `status='sent'`。无回复 `reminder_after` 小时后提醒一次 → 再无回复 `status='missed'`。
4. **Check-in 上下文**：用户回复时，context 注入“这是 Day 2 回访，协议要求询问 A、B、C，红旗为 X、Y、Z”。Agent 必须覆盖红旗问题（eval 中检查）。
5. **记忆**：症状时间线以表格形式注入 prompt，例如：

   ```
   Day | swelling | bruising | pain | notes
   D1  | moderate | mild     | none |
   D2  | mild ↓   | mild     | none | worried about asymmetry
   ```
   趋势由代码计算（不让 LLM 计算），`worsening` 自动提升 triage 风险。
6. **RAG 的作用域隔离**：`search_knowledge` 工具要求 `scope` 参数。术后症状相关的回复只能检索 `clinical_approved`；`service_info` 只用于服务问答。若某治疗还没有 `clinical_approved` 内容，Agent 只能使用协议中的固定话术并建议咨询诊所——**官网服务介绍不能变成术后护理指导**。

---

## 7. LLM Provider 抽象

```python
class LLMProvider(Protocol):
    async def generate(
        self,
        system: str,
        messages: list[ChatMessage],
        tools: list[ToolSpec] | None = None,
        response_model: type[BaseModel] | None = None,   # 结构化输出
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 1024,
    ) -> LLMResult: ...      # text | tool_calls | parsed, usage, latency, raw_id

class EmbeddingProvider(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...
```

- 配置：`LLM_PROVIDER=anthropic`，`LLM_MODEL_MAIN=claude-sonnet-5-5`，`LLM_MODEL_TRIAGE=claude-haiku-5-5`（具体型号在第 2 阶段用 eval 决定，可随时切到 OpenAI 等）。
- 结构化输出：优先使用供应商原生的 JSON schema / tool 强制模式；统一由 Pydantic 校验，失败重试 1 次，再失败走安全回退文案。
- `FakeLLMProvider`：按脚本返回预设输出，用于单元与集成测试。
- 每次调用写 `agent_runs`（见 DATA_MODEL）。
- Embedding 维度写入配置；更换 embedding 模型 = 重新跑 `ingest --reembed`。

---

## 8. 人工转接（无 Dashboard 的最小方案）

```
risk ≥ L2 / 用户要求 / 预约请求
   → handoff.create(user, reason, urgency, summary)
   → users.conversation_mode = 'human'（L2 可配置为不进入 human_mode，只通知）
   → Notifier.send(staff_channel, summary + 签名链接)
   → 回复患者：“我已经把你的情况转给 Vanished 团队，他们会在 {SLA} 内联系你。{安全网话术}”

员工收到通知（Email / WhatsApp）：
   - 摘要：姓名(模拟)、电话、治疗、术后第 N 天、症状时间线、触发原因、最近对话
   - 签名链接（JWT，24h 过期，单次）→ 极简页面：[Acknowledge] [Resolve & return to AI] [Add note]
   - 员工通过诊所原有渠道（电话 / Vagaro）联系患者
   - Resolve → conversation_mode='ai'，可选给患者发送“团队已跟进”
```

- SLA 超时未 Acknowledge → 升级通知（第二联系人）。
- `Notifier` 接口：`EmailNotifier`（SMTP / SendGrid）、`WhatsAppStaffNotifier`（向员工号码发模板），选哪个待客户确认。
- 未来若需要员工直接在 WhatsApp 里回复患者，可加 “relay” 模式，MVP 不做。

---

## 9. 部署与环境

| 环境 | 组成 | 数据 |
|---|---|---|
| local | docker-compose：`pgvector/pgvector:pg16` + web + worker；`ConsoleAdapter` 或 ngrok + Meta 测试号码 | 模拟 |
| staging | 容器平台（Render / Railway / Fly.io 任选）+ 托管 Postgres（需支持 pgvector）；Meta 测试 WABA | 仅模拟数据 |
| production（真实患者，未来） | **必须**迁到可签 BAA 的云（AWS / GCP / Azure），加密、备份、访问审计；LLM 供应商 BAA + ZDR | 完成合规审查后 |

- 进程：`web` = `uvicorn app.main:app`；`worker` = `python -m app.jobs.worker`。同一镜像。
- 配置：全部环境变量；密钥用平台 secret。
- 迁移：部署时 `alembic upgrade head`。
- 备份：托管 Postgres 每日快照 + PITR。
- 监控：结构化 JSON 日志（PII 脱敏）、Sentry（关闭请求体采集）、`/healthz`（DB 连通）、worker 心跳表；告警：任务失败、handoff SLA 超时、Meta 发送失败率。

---

## 10. 安全与隐私边界（架构层）

| 边界 | 措施 |
|---|---|
| 数据分级 | `messages.body`、`journal_entries`、`symptom_observations` = 健康数据；`kb_*` = 公开 |
| 存储 | 托管 Postgres 静态加密；健康文本字段 MVP 不额外列加密（只存模拟数据），生产前评估 `pgcrypto` / 应用层加密 |
| 日志 | 日志中不打印消息正文与电话号码（只打印 user_id、message_id）；LLM 原始 prompt 只存在 `agent_runs`，有保留期限 |
| 访问 | 无公开读取接口；admin API 用 Bearer token + IP 白名单；员工处理页用一次性签名链接 |
| 第三方 | 发往 LLM 的内容最小化：不发送电话号码；姓名使用 first name；不发送其他患者数据 |
| 删除 | `DELETE MY DATA` → 员工确认 → 硬删除健康数据，保留最小审计记录（无正文） |
| 保留 | 默认（待确认）：消息 12 个月、agent_runs 90 天 |
| 训练 | 合同与配置上禁止供应商用数据训练；Aster 自身不用对话训练 |
| WhatsApp | Meta 不签 BAA；在合规结论出来前，只用模拟数据 |

---

## 11. 未来扩展点（预留，不实现）

| 未来需求 | 现在预留的 | 现在不做的 |
|---|---|---|
| 网页 / App 登录 | `users` 与 `channel_identities` 分离 | 认证系统 |
| 社区分享 | `journal_entries.visibility` 默认 `private` | 任何发帖功能 |
| 多诊所 | `clinics` 表、`journeys.clinic_id` | 机构发现 |
| Vagaro 预约 | `BookingAdapter` 接口 + `ManualHandoffBooking` 实现 | 真实对接 |
| 照片 | `media_assets` 表结构设计（文档中） | 存储与访问链接 |
| 更多健康领域 | `treatments.domain`、协议与安全规则按文件配置 | 领域特定逻辑 |
| 积分 | 无 | 全部 |
