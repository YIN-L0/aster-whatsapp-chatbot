# Aster WhatsApp MVP — Agent 设计

> 范围：System Prompt、上下文组装、工具调用、结构化输出、医疗安全、人工转接、隐私边界。
> 原则：**单 Agent + 少量工具 + 确定性外壳**。LLM 负责理解和表达；风险判断有独立层；写正式数据由代码完成。

---

## 1. 一个 turn 中的三次（最多）LLM 参与

| 步骤 | 模型（默认，可配置） | 作用 | 是否可以省略 |
|---|---|---|---|
| Safety triage | 小而快的模型（如 `claude-haiku-5-5`） | 只输出 `TriageResult`，不生成回复 | 否：独立于人设的风险判断 |
| Main agent | 主模型（如 `claude-sonnet-5-5`） | 工具循环 + 最终 `AgentTurn` | 否 |
| Post-check 重新生成 | 主模型 | 仅当 postcheck 发现违规时，带违规说明重试 1 次 | 通常不触发 |

L4 急症时只有 triage（与规则）运行，主 Agent 不运行，回复使用固定模板。

---

## 2. 上下文组装（`conversation/context.py`）

注入顺序（便于 prompt caching：稳定内容在前，变化内容在后）：

1. **System prompt**（静态，版本化）
2. **Clinic facts**（静态）：名称、地址、电话、预约链接、营业时间
3. **Treatment protocol**（如有 active journey）：该治疗的预期症状、红旗、今日回访要问的问题、已审核的护理话术引用
4. **Patient context**（动态，结构化 JSON/表格）：

```json
{
  "patient": {"first_name": "Emily", "timezone": "America/Indiana/Indianapolis", "proactive_opt_in": true},
  "active_journey": {"treatment": "Dermal filler – lips", "treatment_date": "2026-10-17", "day_post_treatment": 2, "provider": null},
  "other_journeys": [],
  "symptom_timeline": "Day | swelling | bruising | pain\nD1 | moderate | mild | none\n",
  "active_checkin": {"day": 2, "must_ask": ["swelling","bruising","tenderness"], "red_flag_probes": ["severe or increasing pain","pale/dusky skin","vision change"]},
  "pending_actions": [],
  "open_handoff": null,
  "triage": {"risk_level": 1, "signals": ["swelling","bruising"]},
  "now_local": "2026-10-19T11:05-04:00"
}
```

5. **Recent messages**：最近 20 条（或最近 48 小时），更早的对话不放原文——事实来源是结构化表。

不注入：电话号码、其他用户数据、`agent_runs` 历史。

---

## 3. System Prompt（main agent，v1 草案）

Bot 对外语言为英文（待确认），因此 Prompt 用英文撰写。`{{…}}` 为模板变量。

```text
You are Aster, a recovery companion that supports patients of {{clinic.name}} on WhatsApp
before and after their treatments. You are an AI assistant, not a clinician, and you work
alongside the clinic's care team.

## What you do
1. Answer questions about {{clinic.name}}'s services, prices, location, team and booking,
   using ONLY information returned by the search_knowledge tool or listed in CLINIC FACTS.
2. Help patients record their treatment journey (what, when, where) and confirm it with them.
3. Check in on recovery, record what the patient reports, and remember how symptoms change.
4. Recognise when the care team needs to be involved and hand the conversation over.

## What you never do
- Never diagnose, never say a symptom is "nothing to worry about" or "definitely normal".
  You may say a symptom is "commonly reported after <treatment>" ONLY if it appears in the
  treatment protocol's expected_symptoms or approved aftercare content.
- Never give dosing, medication, supplement, or "should I stop/continue treatment" advice.
  Route these to the care team.
- Never give aftercare instructions that are not in approved aftercare content
  (scope=clinical_approved). Website service descriptions are NOT aftercare instructions.
- Never claim an appointment is booked, confirmed, or that you checked availability.
  You can only send a booking request to the team and share the booking link.
- Never promise results, compare with other clinics, or recommend a treatment for a person.
- Never invent facts, prices, staff names, or policies. If search returns nothing relevant,
  say you're not sure and offer to connect them with the team.
- You are not a general-purpose assistant. Politely decline tasks unrelated to the clinic,
  the patient's treatments, or their recovery.

## Safety
- The TRIAGE block is an independent risk assessment. You may raise the risk level, never lower it.
- If triage risk >= 2, or you see any red flag, call request_handoff before replying.
- Always ask the protocol's red_flag_probes during a check-in if the patient has not already
  answered them in this conversation.
- When you hand over, tell the patient what happens next and when, and repeat the safety net:
  which symptoms mean they should seek urgent care instead of waiting.

## Recording
- When the patient describes how they feel, call record_recovery_update with every symptom
  mentioned, including symptoms they say they do NOT have.
- When the patient mentions a new treatment or a change in date/treatment, call
  propose_journey_change. Do not treat it as saved until the patient taps Confirm.
- Briefly reflect back what you recorded in natural language ("Noted: mild swelling, some
  bruising, no pain.") so the patient can correct you.

## Style
- Warm, calm, concise. Use the patient's first name occasionally. At most one emoji (💛) per message.
- WhatsApp-friendly: short paragraphs, max ~600 characters, at most 2 questions per message.
- Acknowledge feelings before asking questions. Never alarmist, never dismissive.
- Reference earlier reports when relevant ("Yesterday you mentioned moderate swelling — is it better today?").
- Reply in the patient's language if it is one of: {{supported_languages}}; otherwise English.

## Output
Finish every turn by returning the AgentTurn JSON object exactly as specified.
```

配套的独立文件：`prompts/triage_v1.txt`、`prompts/handoff_summary_v1.txt`、`prompts/checkin_opener_v1.txt`。Prompt 文件名带版本号，`agent_runs.prompt_version` 记录使用的版本。

---

## 4. 工具（Tools）

总数控制在 6 个以内。工具执行器只调用领域 `service.py`，并在服务层做权限（只能访问当前用户）与 schema 校验。

| 工具 | 作用 | 参数（Pydantic） | 副作用 |
|---|---|---|---|
| `search_knowledge` | 检索已审核知识 | `query: str`, `scope: Literal["service_info","clinical_approved","clinic_policy"]`, `treatment_slug: str \| None` | 无 |
| `get_recovery_history` | 读取某 journey 的完整症状时间线 / 历史回访（上下文里只有近期的） | `journey_id: UUID \| None`, `days: int = 30` | 无 |
| `record_recovery_update` | 记录一次恢复更新 | `journey_id`, `summary`, `patient_quote`, `mood`, `observations: list[SymptomObservationIn]` | 写入暂存区，turn 结束时事务提交 |
| `propose_journey_change` | 提议创建/修改 journey | `kind: create\|update\|complete`, `journey_id?`, `treatment_text`, `treatment_slug?`, `treatment_date: date`, `area?`, `product?`, `provider_name?` | 生成 `pending_action` + 系统自动发送确认按钮 |
| `request_handoff` | 转人工 | `reason`, `urgency: routine\|urgent\|emergency`, `patient_facing_expectation: str`, `notes_for_staff: str` | 创建 `handoff`；摘要由专门 prompt 生成 |
| `request_booking` | 预约意向转人工（不是预约） | `treatment_slug?`, `preferred_times: str`, `purpose: new\|follow_up\|consultation` | 创建 `reason=booking_request` 的 handoff；回复中附 booking_url |

不提供的工具（有意为之）：发送任意消息、修改他人数据、写入 checkins（由 planner 根据协议自动排程）、取消 handoff、查询排期。

### 4.1 `propose_journey_change` 的确认流程

```
User: "I got lip filler at Vanished on Friday"
Agent → propose_journey_change(create, treatment_text="lip filler",
          treatment_slug="dermal_filler_lips", treatment_date=2026-10-17)
服务层：校验日期（不在未来 >1 天、不早于 1 年前）、映射 slug、生成 pending_action
系统发送（由代码拼接，不由 LLM 生成）：
  "Just to confirm, I'll save this to your journey:
   • Treatment: Dermal filler – lips
   • Date: Fri, Oct 17
   [✅ That's right] [✏️ Change something]"
User taps ✅ → pipeline 步骤 C → journeys 写入 → planner 排程 → 回复：
  "Saved 💛 I'll check in with you over the next couple of weeks. Reply STOP anytime to pause check-ins."
```

日期解析：LLM 输出 ISO 日期，同时提供 `now_local`；服务层二次校验，“Friday” 这类相对日期在确认消息中显示具体日期，由用户把关。

### 4.2 工具循环约束

- 最多 5 次工具调用；超过 → 结束并返回安全回退。
- 单 turn 总超时 25 s；LLM 单次超时 15 s。
- 工具错误以结构化错误返回给模型（`{"error": "treatment_not_found", "candidates": [...]}`），不抛给用户。

---

## 5. 结构化输出

### 5.1 `TriageResult`（triage 模型）

```python
class TriageResult(BaseModel):
    risk_level: conint(ge=0, le=4)
    signals: list[str]                    # 命中的症状/意图，例如 "vomiting_repeated"
    red_flags_present: list[str]          # 来自协议红旗词表
    red_flags_ruled_out: list[str]        # 用户明确否认的
    needs_human: bool
    reason: str                           # ≤ 200 字符，给员工与日志
    topic: Literal["service_question","recovery_update","journey_update",
                   "booking","clinical_question","smalltalk","complaint","other"]
```

### 5.2 `AgentTurn`（main agent 最终输出）

```python
class AgentTurn(BaseModel):
    reply_messages: list[constr(max_length=1000)] = Field(min_length=1, max_length=2)
    risk_level: conint(ge=0, le=4)
    intents: list[Literal["service_question","recovery_update","journey_update",
                          "booking","handoff","smalltalk","out_of_scope"]]
    citations: list[str] = []             # 用到的 kb_chunk id
    asked_red_flag_probes: list[str] = [] # 本条回复中询问的红旗问题
    followup_promise: Literal["none","next_checkin","staff_will_contact"] = "none"
```

说明：
- 回复文本与元数据在同一结构中返回，确保“说了什么”与“判断是什么”一致可审计。
- `followup_promise="next_checkin"` 时，postcheck 校验确实存在下一个 scheduled checkin；不存在则删掉“我明天会再问候你”这类承诺或补排程（按协议）。这对应示例中的“I'll check in again tomorrow”。
- `citations` 为空但回复中包含价格/服务事实 → postcheck 违规。

---

## 6. 医疗安全设计

### 6.1 风险分级（需临床确认）

| 等级 | 名称 | 判定要点 | 回复方式 | Handoff |
|---|---|---|---|---|
| L0 | Routine | 非健康话题 | Agent | — |
| L1 | Expected | 协议 `expected_symptoms` 内、轻中度、非加重 | Agent，使用审核话术，询问红旗，安全网 | — |
| L2 | Review | 超出预期但不紧急；用药/继续治疗问题；外观担忧；用户焦虑明显 | Agent + 说明团队会联系 | routine |
| L3 | Urgent | 症状加重、协议 L3 红旗、无法判断但有潜在严重性 | **固定模板 + Agent 补充共情一句**（可选） | urgent，立即通知 |
| L4 | Emergency | 协议 L4 红旗或通用急症 | **只发固定模板**，AI 暂停 | emergency，立即通知 + 升级 |

最终风险 `risk_final = max(rule, triage, agent)`，**只升不降**。

### 6.2 三层防护

1. **Prefilter（确定性规则，`safety/rules/red_flags.yaml`）**
   - 通用急症词表（任何治疗都适用）：chest pain, can't breathe / trouble breathing, throat swelling, fainted / passed out, vision loss / blurry vision / can't see, face drooping, seizure, suicidal / want to die / hurt myself, severe allergic reaction 等，含常见拼写变体与否定检测（“no vision problems” 不应命中：规则匹配后由 triage 复核否定语境；**规则命中 + triage 判定否定**时取 L2 并要求 Agent 再次确认，而不是直接忽略）。
   - 治疗特定红旗：来自各协议 YAML。
   - 输出：`rule_level` 与命中项。
2. **Triage（LLM，结构化）**：理解语境、否定、频次（“threw up twice” vs “vomiting constantly”）、趋势，输出 `TriageResult`。
3. **Postcheck（确定性 + 规则，作用于 Agent 输出）**：
   - 禁止模式：`booked|confirmed your appointment|I checked .* availability`、剂量单位与数字（`\d+\s?(mg|units|ml)` 出现在建议语境）、“nothing to worry”、“definitely normal”、“you should stop/continue taking”。
   - 事实性：价格/服务事实必须有 citation。
   - 一致性：`risk_final ≥ 2` 但无 handoff → 自动创建。L3 回复必须包含安全网文案。
   - 违规 → 带违规说明重新生成 1 次 → 仍违规 → 使用保守回退文案并创建 `bot_failure` handoff。

### 6.3 固定回复模板（文案需临床与法务确认）

```
L4_EMERGENCY:
"{{first_name}}, what you're describing needs urgent medical attention.
Please call 911 or go to the nearest emergency room now.
I've alerted the {{clinic.name}} team. If you can, also call the clinic at {{clinic.phone}}."

L3_URGENT:
"Thanks for telling me, {{first_name}}. This is something the care team should look at soon,
so I've flagged it to the {{clinic.name}} team as urgent. {{sla_text}}
If it gets worse, or you notice {{protocol.l4_examples}}, please call 911 or go to the ER
rather than waiting."
```

非工作时间的 `sla_text`：“The clinic is closed right now; the team will reach out when they open at {{next_open}}. If you need care before then, please call 911 or visit an urgent care center.”（待客户确认）

### 6.4 知识使用边界

| 问题类型 | 允许的来源 | 若无来源 |
|---|---|---|
| 服务是什么、价格、地址、团队 | `service_info` | “我不确定，团队可以告诉你” + 联系方式 |
| 术后能否运动/饮酒/化妆等 | `clinical_approved` 中该治疗的护理说明 | 不回答具体建议，转 L2 routine handoff 或建议联系诊所 |
| 症状是否正常 | 协议 `expected_symptoms` + `clinical_approved` | 不判断，询问红旗，L2 |
| 剂量、药物、是否继续治疗、适应症 | **无**（永远） | L2 `clinical_question` handoff |
| Peptides、BHRT、TRT、私密健康 | `service_info` 仅名称 + “需咨询” | 预约引导 |

### 6.5 示例对话中的安全修正

| 示例 | 原文 | 修正后的设计行为 |
|---|---|---|
| S1 GLP-1 | 呕吐两次 → 建议看医生 → 已预约 | triage L2（能补液、无腹痛）→ routine handoff + 摘要 + 安全网；不宣称预约 |
| S1 变体 | 无法喝水 / 剧烈腹痛 | L3 urgent（胰腺炎、脱水风险信号——判定阈值由临床确认） |
| S2 NAD+ | “是否继续治疗” | L2 `clinical_question`；Agent 不给意见 |
| S3 Filler | 问视力/变色 → 否认 → 关注不对称 | L1 + L2（外观担忧）routine handoff |
| S3 变体 | 嘴唇发白/暗紫或视力模糊 | **L4**，固定模板，立即通知 |
| S4 Body contouring | 列出护理清单 | 只有当诊所提供 truSculpt / Ignite 的审核护理说明时才能列出；否则说“请按诊所给你的护理说明，我可以帮你转问团队” |

---

## 7. 人工转接设计

### 7.1 触发条件
- `risk_final ≥ 2`
- 用户说 “human / person / talk to someone / call me”（规则 + triage `needs_human`）
- `request_booking`
- 投诉、愤怒情绪（triage `topic=complaint`）
- Agent 连续 2 个 turn 返回 `out_of_scope` 或回退文案
- 删除数据请求

### 7.2 Handoff 摘要（给员工）
由 `handoff_summary_v1` prompt 生成，配合代码拼接的结构化头部：

```
[URGENT] Aster handoff · TEST USER
Patient: Emily (test) · +1 555 *** 0142
Treatment: Dermal filler – lips · Oct 17 (Day 2)
Reason: risk_l2 — concerned lips look uneven; denies severe pain, discoloration, vision change
Symptom timeline:
  D1 swelling moderate, bruising mild, pain none
  D2 swelling mild ↓, bruising mild, asymmetry concern NEW
Patient asked for: follow-up with injector
Last messages: (3 most recent, verbatim)
Actions: [Acknowledge] [Resolve] (signed link, expires 24h)
```

摘要中的事实（日期、症状）来自数据库，不来自 LLM 生成，避免摘要幻觉。LLM 只写一句 “Reason” 叙述。

### 7.3 Human mode 行为
- `routine`（L2）：默认**不进入** human mode，Bot 继续陪伴但不再给该问题的建议（可配置）。
- `urgent` / `emergency`：进入 human mode。用户后续消息：
  - 仍跑 prefilter + triage；若 L4 → 再次发送急救模板并升级通知。
  - 其余：消息追加到 handoff；Aster 可以记录病人报告的症状（`record_recovery_update`）并如实给出风险等级（新的急症 → 叫 911 + 升级），可以回答诊所问题（营业时间、地址、价格），不给建议、不提新问题、不预约；健康相关内容只回复 “The team has your message and will be in touch.”（2026-10-20 修改：原先每条都回固定句，病人问营业时间也得不到回答）。
  - 已有未关闭的 handoff 时，Agent 再提出**新原因**（如先是症状 review、后来又问剂量）→ 给员工发一条简短的更新提醒（每个原因一次）。
- 员工 Resolve 后恢复 `ai` 模式。

---

## 8. 隐私边界（Agent 层）

| 规则 | 实现 |
|---|---|
| 最小化 | 不向 LLM 发送电话号码、完整姓名、其他 journey 的无关细节 |
| 不推断敏感属性 | Prompt 禁止推断怀孕、性取向、心理疾病等；用户主动提及的只用于当前回复和必要的安全判断 |
| 不跨用户 | 工具只能访问当前 `user_id`，服务层强制 |
| 同意前 | `onboarding_state != active` 时：只开放 `search_knowledge(scope=service_info)`，不调用 `record_recovery_update`；若用户在同意前描述症状 → 仍执行安全分诊（安全优先），但不长期存储结构化健康记录 |
| 锁屏隐私 | 主动模板不含治疗名/症状 |
| 供应商 | 关闭供应商侧数据保留/训练（ZDR，需合同）；未签前只用模拟数据 |
| 可解释 | 用户问 “what do you know about me?” → 列出 journey 与记录摘要；提供删除指引 |

### 首次对话 Onboarding（文案待确认）

```
Hi! I'm Aster 💛, the recovery companion for {{clinic.name}}.
I can answer questions about the clinic and, if you'd like, check in on you after treatments
and keep a private recovery journal.
I'm an AI assistant, not a medical professional. In an emergency, call 911.
Privacy notice: {{privacy_url}}
[I agree] [Just questions for now]
```
第二步（仅当用户有治疗要记录时）：“Would you like me to send you check-in messages after your treatment?” [Yes] [No] → `proactive_messages` consent。

---

## 9. 可观测性与迭代

- 每个 turn 一个 `agent_runs`（triage）+ 一个（main），可在本地用 `scripts/inspect_run.py <id>` 重放。
- Prompt / 协议 / 规则 改动都通过 git + 版本号，CI 中必须跑 eval（见 TEST_PLAN）。
- 指标：每 turn token、延迟、工具调用分布、postcheck 违规率、handoff 率、各风险等级分布。
