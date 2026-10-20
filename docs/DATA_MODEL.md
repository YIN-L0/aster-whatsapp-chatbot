# Aster WhatsApp MVP — 数据模型

> PostgreSQL 16 + pgvector。所有主键使用 `uuid`（`gen_random_uuid()`），所有时间使用 `timestamptz`（UTC 存储），所有表都有 `created_at`、`updated_at`。
> 原则（来自蓝图）：**原始数据 / 结构化数据 / AI 推理过程 分表存储**；LLM 不写正式数据。

---

## 1. 实体关系总览

```
clinics 1─┬─* treatments (catalog, per clinic)            followup_protocols (versioned, per treatment)
          │                                                        │
users 1─* channel_identities                                       │
  │  1─* consents                                                  │
  │  1─* messages ─────────────┐                                   │
  │  1─* journeys *─1 treatments                                   │
  │        │ 1─* journey_events                                    │
  │        │ 1─* checkins *──────────────── protocol_version ──────┘
  │        │ 1─* journal_entries 1─* symptom_observations
  │  1─* pending_actions                     ▲ source_message_id ──▶ messages
  │  1─* handoffs
  │  1─* agent_runs ──▶ messages (input/output)
  │
jobs (generic queue)       audit_log        kb_sources 1─* kb_chunks (embedding)
```

数据分层：

| 层 | 表 |
|---|---|
| 原始来源 | `messages`、`kb_sources`（原始 HTML 快照路径） |
| 结构化产品数据 | `users`、`journeys`、`journey_events`、`journal_entries`、`symptom_observations`、`checkins`、`handoffs` |
| AI 推理过程 | `agent_runs`、`pending_actions`（AI 提议，未生效） |
| 配置 / 参考 | `clinics`、`treatments`、`followup_protocols`、`kb_chunks` |
| 系统 | `jobs`、`audit_log` |

---

## 2. 身份与同意

### `users`
| 列 | 类型 | 说明 |
|---|---|---|
| id | uuid PK | 统一用户编号（未来网页/App 共用） |
| first_name | text null | 用户自报或从 WhatsApp profile name 获取（需确认后使用） |
| timezone | text | 默认 `America/Indiana/Indianapolis`（待确认） |
| locale | text | 默认 `en` |
| conversation_mode | enum(`ai`,`human`,`paused`) | `human`：转人工中；`paused`：用户 STOP |
| onboarding_state | enum(`new`,`consent_pending`,`active`) | |
| proactive_opt_in | bool | 是否同意接收主动回访 |
| is_test_user | bool | MVP 全部为 true；生产数据隔离开关 |
| deleted_at | timestamptz null | 软删除标记（随后硬删除健康数据） |

### `channel_identities`
| 列 | 类型 | 说明 |
|---|---|---|
| id | uuid PK | |
| user_id | uuid FK users | |
| channel | enum(`whatsapp`) | 未来：`web`, `sms` |
| external_id | text | WhatsApp `wa_id`（E.164 不带 +） |
| display_name | text null | WhatsApp profile name |
| last_inbound_at | timestamptz | 用于 24h 客服窗口判断 |
| UNIQUE(channel, external_id) | | |

### `consents`
| 列 | 类型 | 说明 |
|---|---|---|
| id | uuid PK | |
| user_id | uuid FK | |
| kind | enum(`privacy_notice`,`health_data_processing`,`proactive_messages`,`age_18_plus`) | |
| version | text | 同意文案版本，例如 `privacy_v1` |
| granted | bool | |
| source_message_id | uuid FK messages | 用户点击按钮的那条消息 |
| granted_at / revoked_at | timestamptz | |

同意是**追加记录**而不是覆盖字段，保证可审计。

---

## 3. 对话

### `messages`
| 列 | 类型 | 说明 |
|---|---|---|
| id | uuid PK | |
| user_id | uuid FK | |
| direction | enum(`in`,`out`) | |
| wa_message_id | text UNIQUE null | Meta 消息 ID，入站去重的依据 |
| type | enum(`text`,`button_reply`,`list_reply`,`template`,`media`,`system`) | |
| body | text | 正文（健康数据） |
| payload | jsonb | 按钮 payload、模板参数、媒体元数据 |
| sender | enum(`user`,`aster_ai`,`aster_system`,`staff`) | 区分 AI 生成与固定模板 |
| status | enum(`received`,`processed`,`ignored`,`failed`) | 入站处理状态 |
| delivery_status | enum(`queued`,`sent`,`delivered`,`read`,`failed`) null | 出站状态 |
| error | jsonb null | Meta 错误码 |
| agent_run_id | uuid FK null | 出站消息由哪次 Agent 运行生成 |
| checkin_id | uuid FK null | 关联的回访 |
| sent_at / received_at | timestamptz | |

索引：`(user_id, created_at desc)`、`(user_id) where status='received'`。

---

## 4. 治疗目录与诊所

### `clinics`
`id, name, address, phone, email, booking_url, timezone, hours jsonb null`
MVP 一行：Vanished Aesthetics & Wellness。

### `treatments`
| 列 | 类型 | 说明 |
|---|---|---|
| id | uuid PK | |
| clinic_id | uuid FK | |
| slug | text | `dermal_filler_lips`, `neuromodulator`, `glp1_semaglutide`, `nad_plus`, `trusculpt_id` … |
| display_name | text | |
| category | text | `injectables`, `body_contouring`, `skin`, `wellness`, `weight_management`, `peptides`, `iv_therapy` |
| domain | text | `aesthetics`（未来：`dental`、`reproductive`…） |
| aliases | text[] | 用户可能的说法：“lip filler”, “juvederm”, “ozempic” |
| followup_supported | bool | 是否有已审核的回访协议 |
| restricted | bool | true = Bot 只能引导咨询（peptides、BHRT、TRT 等） |
| UNIQUE(clinic_id, slug) | | |

### `followup_protocols`
源文件为 `app/followups/protocols/<slug>.yaml`（git 管理、临床签字），启动时同步到表中，供历史 checkin 引用其版本。

| 列 | 类型 | 说明 |
|---|---|---|
| id | uuid PK | |
| treatment_id | uuid FK | |
| version | text | `dermal_filler_lips@2026-11-01` |
| status | enum(`draft`,`approved`,`retired`) | 只有 `approved` 会用于真实排程；`draft` 仅测试环境可用 |
| approved_by / approved_at | text / timestamptz | 临床审核人 |
| definition | jsonb | 见下例 |
| content_hash | text | 文件内容哈希，防止未经审核的修改 |

`definition` 示例（**内容为占位，需临床确认**）：

```yaml
treatment: dermal_filler_lips
schedule:
  - day: 1
    send_window: "10:00-18:00"
    opener: "checkin_generic"          # 模板 key
    ask:
      - swelling
      - bruising
      - tenderness
    red_flag_probes:
      - "severe or increasing pain"
      - "pale, white, or dusky skin color"
      - "any change in vision"
    reminder_after_hours: 6
  - day: 2
  - day: 7
  - day: 14
expected_symptoms: [swelling, bruising, tenderness, mild_asymmetry_due_to_swelling]
red_flags:
  L4: [vision_change, skin_blanching_or_dusky, severe_pain_increasing]
  L3: [fever, pus, spreading_redness, hard_painful_lump]
aftercare_ref: kb://clinical_approved/dermal_filler_aftercare_v1
```

---

## 5. Treatment Journey

### `journeys`
| 列 | 类型 | 说明 |
|---|---|---|
| id | uuid PK | |
| user_id | uuid FK | |
| clinic_id | uuid FK | |
| treatment_id | uuid FK | |
| treatment_label_raw | text | 用户原话（“lip flip & filler”） |
| treatment_date | date | 本地日期 |
| area | text null | |
| product | text null | |
| provider_name | text null | 用户自报 |
| status | enum(`active`,`completed`,`cancelled`) | |
| is_primary | bool | 当前默认回访的 journey；部分唯一索引 `(user_id) where is_primary` |
| protocol_version | text null | 使用的回访协议版本 |
| visibility | enum(`private`) | 未来扩展 `shared`，MVP 只有 private |
| source | enum(`user_reported`,`clinic_enrolled`,`seed`) | |
| confirmed_at | timestamptz | 用户确认时间 |

“术后第 N 天” 不存储，查询时用 `today_in_user_tz - treatment_date` 计算。

### `journey_events`
journey 的变更与里程碑，追加写入（审计 + 时间线展示）。

| 列 | 类型 | 说明 |
|---|---|---|
| id | uuid PK | |
| journey_id | uuid FK | |
| kind | enum(`created`,`updated`,`treatment_repeated`,`completed`,`cancelled`,`checkin_sent`,`handoff`) | |
| data | jsonb | 变更前后值 |
| actor | enum(`user`,`system`,`staff`) | |
| pending_action_id | uuid FK null | 由哪个确认动作触发 |

### `pending_actions`
AI 提出、等待用户确认的结构化操作。

| 列 | 类型 | 说明 |
|---|---|---|
| id | uuid PK | |
| user_id | uuid FK | |
| kind | enum(`create_journey`,`update_journey`,`complete_journey`) | |
| payload | jsonb | 经 Pydantic 校验的参数 |
| status | enum(`awaiting_user`,`confirmed`,`rejected`,`expired`,`superseded`) | |
| agent_run_id | uuid FK | |
| prompt_message_id | uuid FK messages | 带按钮的确认消息 |
| expires_at | timestamptz | 默认 24h |
| resolved_at | timestamptz null | |

同一用户同一时间最多一个 `awaiting_user` 的同类操作；新提议使旧提议 `superseded`。

---

## 6. Recovery Journal

### `journal_entries`
一次“恢复更新”（通常对应一个对话 turn）。

| 列 | 类型 | 说明 |
|---|---|---|
| id | uuid PK | |
| journey_id | uuid FK | |
| user_id | uuid FK | |
| checkin_id | uuid FK null | 若为回访的回复 |
| day_post_treatment | int | 写入时计算并冻结 |
| summary | text | AI 生成的一句话摘要（用户视角） |
| patient_quote | text | 原话摘录 |
| mood | enum(`positive`,`neutral`,`worried`,`distressed`) null | |
| risk_level | smallint | 0–4，当时的最终风险等级 |
| source_message_ids | uuid[] | |
| extracted_by | enum(`ai`,`user`,`staff`) | |
| agent_run_id | uuid FK null | |
| visibility | enum(`private`) | |

### `symptom_observations`
可比较的结构化症状记录，用于“记住症状和变化”。

| 列 | 类型 | 说明 |
|---|---|---|
| id | uuid PK | |
| journal_entry_id | uuid FK | |
| journey_id | uuid FK | 冗余，便于时间线查询 |
| symptom_code | text | 受控词表：`swelling`, `bruising`, `pain`, `tenderness`, `nausea`, `vomiting`, `dizziness`, `injection_site_redness`, `asymmetry_concern`, `numbness`, `appetite_reduced`, `vision_change`, `skin_discoloration`, `other` |
| present | bool | 否定也要记录（“没有疼痛”同样重要） |
| severity | enum(`none`,`mild`,`moderate`,`severe`) null | |
| frequency | text null | “threw up twice yesterday” → `2x/day` |
| body_area | text null | |
| status_change | enum(`new`,`improving`,`same`,`worsening`,`resolved`) null | 用户自述 |
| computed_trend | enum(…) null | 代码与上一条同症状记录比较得出 |
| free_text | text null | `other` 的描述 |
| observed_on | date | |

受控词表放在 `app/journeys/symptoms.py`，未在词表中的症状用 `other` + `free_text`，并记录用于后续扩充词表。

---

## 7. Follow-up

### `checkins`
| 列 | 类型 | 说明 |
|---|---|---|
| id | uuid PK | |
| journey_id | uuid FK | |
| user_id | uuid FK | |
| protocol_version | text | |
| protocol_step | int | 对应 `schedule[i]` |
| day_offset | int | |
| due_at | timestamptz | |
| status | enum(`scheduled`,`sent`,`reminded`,`answered`,`missed`,`cancelled`,`skipped_opt_out`,`failed`) | |
| sent_message_id / reminder_message_id | uuid FK null | |
| answered_at | timestamptz null | |
| UNIQUE(journey_id, protocol_step) | | 防重复排程 |

---

## 8. 人工转接

### `handoffs`
| 列 | 类型 | 说明 |
|---|---|---|
| id | uuid PK | |
| user_id | uuid FK | |
| journey_id | uuid FK null | |
| reason | enum(`risk_l2`,`risk_l3`,`risk_l4`,`user_request`,`booking_request`,`clinical_question`,`complaint`,`bot_failure`,`deletion_request`) | |
| urgency | enum(`routine`,`urgent`,`emergency`) | |
| status | enum(`open`,`acknowledged`,`resolved`,`expired`) | |
| summary | text | 给员工的摘要（AI 生成 + 结构化字段由代码拼接） |
| trigger_message_id | uuid FK | |
| risk_signals | jsonb | 命中的规则 / triage 理由 |
| notified_at / acknowledged_at / resolved_at | timestamptz | |
| acknowledged_by / resolution_note | text | |
| sla_due_at | timestamptz | |

---

## 9. AI 推理追踪

### `agent_runs`
| 列 | 类型 | 说明 |
|---|---|---|
| id | uuid PK | |
| user_id | uuid FK | |
| kind | enum(`triage`,`main`,`summary`,`checkin_opener`) | |
| provider / model | text | |
| prompt_version | text | 如 `main@v3` |
| input_message_ids | uuid[] | |
| context_snapshot | jsonb | 注入的结构化上下文（便于复现） |
| tool_calls | jsonb | 每步工具名、参数、结果摘要、耗时 |
| output | jsonb | 解析后的 AgentTurn |
| risk_rule / risk_triage / risk_agent / risk_final | smallint | 各层风险等级 |
| postcheck_violations | text[] | |
| input_tokens / output_tokens / latency_ms | int | |
| error | text null | |

保留期（待确认）默认 90 天，之后清空 `context_snapshot` 与 `output` 正文，只保留指标。

---

## 10. 知识库

### `kb_sources`
| 列 | 类型 | 说明 |
|---|---|---|
| id | uuid PK | |
| clinic_id | uuid FK | |
| url | text null | 官网来源 |
| title | text | |
| usage_scope | enum(`service_info`,`clinical_approved`,`clinic_policy`) | **检索作用域，核心安全边界** |
| review_status | enum(`draft`,`approved`,`retired`) | 只有 `approved` 可被检索 |
| reviewed_by / reviewed_at | text / timestamptz | |
| content_path | text | `app/knowledge/content/…md`（git 中） |
| content_hash | text | |
| fetched_at | timestamptz | 官网抓取时间 |
| valid_until | date null | 促销等时效内容 |

### `kb_chunks`
| 列 | 类型 | 说明 |
|---|---|---|
| id | uuid PK | |
| source_id | uuid FK | |
| ordinal | int | |
| heading_path | text | `Injectables > Dermal Fillers` |
| content | text | 300–600 tokens |
| treatment_slugs | text[] | 关联治疗，便于过滤 |
| tsv | tsvector | 生成列，GIN 索引 |
| embedding | vector(N) | HNSW 索引（cosine） |
| embedding_model | text | |

---

## 11. 系统表

### `jobs`
| 列 | 类型 | 说明 |
|---|---|---|
| id | bigserial PK | |
| kind | text | `process_inbound`, `send_checkin`, `checkin_reminder`, `handoff_sla_check`, `expire_pending_actions`, `retention_cleanup` |
| payload | jsonb | |
| dedupe_key | text UNIQUE null | 例：`process_inbound:{user_id}`（未完成时不重复入队） |
| run_at | timestamptz | |
| status | enum(`queued`,`running`,`done`,`failed`,`dead`) | |
| attempts / max_attempts | int | |
| locked_at / locked_by | timestamptz / text | 超时回收 |
| last_error | text | |

Worker 取任务：
```sql
SELECT * FROM jobs
WHERE status='queued' AND run_at <= now()
ORDER BY run_at
FOR UPDATE SKIP LOCKED
LIMIT 10;
```
周期任务（每分钟扫描 due checkins、SLA 检查）由 worker 主循环内的简单 ticker 触发，不需要额外调度器。

### `audit_log`
`id, actor (system|staff:<name>|user), action, entity, entity_id, data jsonb, created_at` —— 记录同意变更、handoff 处理、数据删除、协议版本切换、admin 操作。删除用户数据后审计记录不含正文。

---

## 12. 模拟数据（MVP 唯一允许的健康数据）

`scripts/seed_mock_data.py` 生成：

| 模拟用户 | 治疗 | 治疗日期 | 用途 |
|---|---|---|---|
| Sarah (test) | `glp1_semaglutide` | today − 3 | Scenario 1 |
| Michael (test) | `nad_plus`（形式待确认） | today − 1 | Scenario 2 |
| Emily (test) | `dermal_filler_lips` | today − 2 | Scenario 3 |
| Jessica (test) | `trusculpt_id`（替代 CoolSculpting，待确认） | today − 1 | Scenario 4 |

所有模拟用户 `is_test_user = true`，号码使用 Meta 测试号码或团队内部号码。

---

## 13. 未来表（只设计，不建表）

- `media_assets`：`id, user_id, journey_id, storage_key, mime, visibility, encrypted, retention_until` —— 照片功能确认后再建。
- `community_posts`、`credit_transactions`、`rewards`：按蓝图，验证旅程闭环后再设计。
