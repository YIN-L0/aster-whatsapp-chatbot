# Aster AI Companion — WhatsApp MVP 产品规格

> 版本：v0.1-draft（设计审核用）· 日期：2026-10-19
> 依据：`Aster_产品与工程蓝图_v0.1_中文版.docx`、`example.docx`、https://vanishedaesthetics.com/（2026-10-19 抓取）
> 说明：参考文件当前位于仓库根目录，而非 `references/`。建议在第 0 阶段移入 `references/`。

---

## 1. 产品定位（MVP 范围内）

Aster 是一个通过 WhatsApp 使用的 **术后陪伴与旅程记录助手**。第一版只服务一家合作诊所（Vanished Aesthetics & Wellness），验证一个核心闭环：

```
用户在 WhatsApp 告诉 Aster 自己做了什么治疗
  → Aster 确认并建立 Treatment Journey
  → 按治疗类型和日期主动发送 Check-in
  → 用户回复症状 → 记录到 Recovery Journal
  → Aster 记住并对比恢复变化
  → 出现需要人工判断的信号 → 转交诊所真人
```

蓝图中的关键门槛指标：**旅程持续更新率**（用户是否会回来更新同一段旅程）。MVP 的一切设计都服务于验证这一点。

### 1.1 MVP 明确不做

| 不做 | 原因 / 替代 |
|---|---|
| 社区、发帖、积分、奖励 | 蓝图 P0 中的这些模块延后；数据模型只保留 `user_id` 统一身份作为扩展点 |
| 实时预约 / 读取诊所排期 | 示例中“已为你预约周四 2 点”**无法在 MVP 实现**。替代：生成“预约请求”转交诊所真人 + 提供 Vagaro 预约链接 |
| 移动 App、Web Dashboard、患者门户 | 员工侧只做通知 + 最小化处理入口（见 ARCHITECTURE §8） |
| 照片 / 图像 AI 分析 | 风险高；MVP 收到图片只确认收到、不分析，见 §6 待确认事项 |
| 多诊所、多机构目录 | 数据模型预留 `clinic_id`，但只录入一家 |
| 真实患者健康数据 | 第一版只用模拟数据。真实上线前必须完成隐私 / 合规审查 |

---

## 2. 两份文档分析

### 2.1 产品与工程蓝图 v0.1 → 与 WhatsApp MVP 相关的要求

| 蓝图要求 | MVP 中的落实 |
|---|---|
| 原始来源数据、结构化数据、AI 推理、展示必须分离；“LLM 不是数据库” | `messages`（原始）/ `journeys`, `journal_entries`, `symptom_observations`（结构化）/ `agent_runs`（推理过程）分表存储 |
| AI 产生的结构化操作必须先经过 schema 验证和用户确认，才能修改正式数据 | `pending_actions` + WhatsApp 交互按钮确认；确认后由确定性代码写库，不由 LLM 写库 |
| WhatsApp 与网页解析到同一个统一用户编号 | `users` + `channel_identities` 表；MVP 只有 WhatsApp 一种 identity |
| 安全是整个系统的一等要求：回答边界、风险分类、升级处理、禁止推荐类别、信息来源可追溯、安全评测集 | Safety 模块独立于 Agent；风险分级 L0–L4；规则版本化；专门安全评测集 |
| 治疗项目、阶段、安全策略、知识来源都应是可配置实体，不写死“医美” | `treatments` 分类表、`followup_protocols` 版本化配置、`kb_sources.usage_scope` |
| 第一版不自动抓取大量外部评论，先手工整理小规模数据集 | 只摄取 Vanished 官网 ~20 页，人工审核后入库 |
| 使用成熟通用模型 + 类型化结构输出 + 精选检索 + 评测 + 可观测性；不训练模型 | LLM Provider 抽象 + Pydantic 结构化输出 + `agent_runs` 追踪 + eval 集 |
| 模块化单体，不要微服务；AI 层不需要独立部署 | FastAPI 单仓库单镜像，web + worker 两个进程 |
| 原始对话不能自动成为训练数据；记录授权、来源、删除状态 | `consents` 表、数据保留策略，明确不用于训练 |
| 照片属于敏感数据 | MVP 不处理照片 |
| 未定的预约/机构集成要通过适配边界隔离 | `BookingAdapter` 接口只保留定义，MVP 实现为“转人工” |

蓝图里的 **第一阶段推荐 Next.js + 托管 Postgres**：本 MVP 无 Web 端，按需求采用 Python + FastAPI。

### 2.2 example.docx：四段示例对话的分析

示例是**对话体验参考**，不是审核过的医疗协议。逐段提炼如下：

| # | 场景 | 体验要点（保留） | 不能照搬的地方 |
|---|---|---|---|
| 1 | GLP-1 第 3 天回访 | 主动问候；按药物常见副作用提问；区分“预期效果”（食欲下降）与“需重视”（呕吐两次）；追问补液、腹痛、头晕；建议联系医护而非等待；给出何时就医的警示 | ① “我查了可预约时间……已确认周四 2 点” → MVP 无排期系统，改为“我已把你的情况发给诊所团队，他们会联系你安排”；② “为你的护理团队准备了症状摘要”可以做（作为转人工的 handoff summary） |
| 2 | NAD+ 第 1 天回访 | 询问注射部位反应、头晕；确认头晕已消退；“明天再来问候” | ① Vanished 官网中 NAD+ 列在 Peptides 下，未说明是注射/IV/鼻喷 → 必须由客户确认；② “是否继续治疗”属于临床决策 → 必须转给医护，不能给建议；③ 预约同上 |
| 3 | 唇部填充第 2 天回访 | 询问肿胀/淤青；**主动筛查血管栓塞信号**（剧痛、皮肤异常变色、视力变化）；处理外观焦虑；警示语 | ① 视力变化或苍白/暗紫色变色 = 急症（L4），必须立即走固定急救模板，不是“预约下周四”；② 预约同上 |
| 4 | 身体塑形第 1 天回访 | 回答“能不能去健身房”；列出护理提醒；下周回访 | ① **示例写的是 CoolSculpting，Vanished 官网并未提供**，官网提供的是 truSculpt iD / InMode Ignite RF / RF Microneedling 等；② “来自你的术后护理说明”的清单**必须来自诊所审核过的护理协议**，不能由 LLM 生成或从官网服务介绍推断 |

**共性体验模式**（作为 Agent 设计目标）：

1. **主动、具体的开场**：用名字 + 治疗名 + 第几天 + 2–4 个针对性症状问题。
2. **先共情，再分类**：把用户描述分为“常见/预期” vs “需要关注”。
3. **追问红旗问题**：每种治疗有固定的危险信号追问（Scenario 1 补液/腹痛/头晕；Scenario 3 剧痛/变色/视力）。
4. **给出明确下一步**：继续观察并按时回访 / 联系医护 / 立即就医。
5. **结尾的安全网**：“如果出现 X、Y、Z，请立即就医，不要等待预约”。
6. **闭环承诺**：“我明天/下周会再来问候”——必须由系统真正排程，不能是空话。
7. **语气**：温暖、简洁、少量 emoji（💛），每条消息短。

---

## 3. Vanished 官网知识库分析

### 3.1 诊所基础信息（已核实）

- 名称：Vanished Aesthetics & Wellness（Med Spa, Bloomington）
- 地址：1205 N Walnut St., Bloomington, IN 47404 · 电话 (812) 320-2284 · Email CSC@vanishedaesthetics.com
- 预约：Vagaro（vagaro.com/vanishedaestheticsandwellness）；支付：接受 CareCredit
- 营业时间：官网未列出 → 需客户提供
- 团队（Meet Our Team）：Deanna McAllister（Founder, MSN, FNP-BC）、Michelle Lee（FNP-BC）、Mendy Mullis（PA-C）、RN Injectors Carlie Easton / Lania Garrison、Esthetician David Wyatt，以及运营团队

> ⚠️ **市场不一致**：蓝图写“初始市场为曼哈顿医美”，而 Vanished 位于印第安纳州 Bloomington。会影响时区（America/Indiana/Indianapolis）、急救指引文案和适用的州法规，需客户确认。

### 3.2 页面清单与可用性

| 页面 | 内容 | 可作为知识库 | 备注 |
|---|---|---|---|
| `/aesthetics/injectables/` | Botox/Dysport、Sculptra、Radiesse、Deoxycholic acid、Hylenex、HA fillers、PRP、Selphyller；部分起价 | ✅ 服务介绍 + 价格 | Radiesse 在两处价格不一致 |
| `/aesthetics/body-contouring/` | Microneedling、RF Microneedling、truSculpt iD、Ignite RF、PRXT-33 Body Peel、Threadlifting、Sculptra | ✅ | **无 CoolSculpting**；RF Microneedling 价格与 skin 页不一致（$400 vs $550） |
| `/aesthetics/skin-treatments/` | 微针、化学焕肤、LED 美白、HydraFacial、激光（Tetra CO2、Pico、IPL/ND YAG）、Plasma Pen、Threadlift | ✅ | |
| `/aesthetics/hair-restoration/` | Keravive、PRP（Selphyl）、Nutrafol、化验 | ✅ | |
| `/aesthetics/iv-therapy/` | 9 种 IV、7 种 IM 维生素注射；来源 Olympia Pharmaceuticals | ✅ | 无 NAD+ |
| `/wellness/weight-management/` | Semaglutide、Tirzepatide（品牌 + compounded）、Lipo-Mino、CaloCurb；流程：咨询→化验→方案 | ✅ 仅流程介绍 | **剂量、用药建议绝不可由 Bot 回答** |
| `/peptides/` | 21 种 peptide，含 NAD+、BPC-157、PT-141 等；声明“compounded peptides are experimental and not approved by the FDA” | ⚠️ 仅限名称和“需咨询” | 高监管风险，建议 MVP 只回答“有这项服务、请咨询医护” |
| `/wellness/womens-health/`、`/mens-health/` | BHRT、私密健康（Morpheus8V、FormaV）、TRT | ⚠️ 仅名称 + 预约引导 | 敏感话题 |
| `/wellness/specialty/` | 激素、GI-mapping、食物敏感、霉菌、重金属检测 | ⚠️ 仅名称 | |
| `/specials-events/` 及页眉促销 | 当前优惠（如 Hydrafacial/BioRepeel $75） | ⚠️ 时效性强 | 需要过期时间；建议客户确认是否允许 Bot 提及 |
| `/our-clinic/`、`/meet-our-team/`、`/contact/` | 诊所、团队、联系方式 | ✅ | |
| `/policy-page/*` | HIPAA、隐私、条款、Good Faith Estimate | 📄 合规参考，不入问答库 | 需阅读 HIPAA policy 以对齐 Aster 的处理 |
| `/blog/`、`/shop/` | 博客、产品 | ❌ MVP 不收录 | 博客内容未经审核 |

### 3.3 官网内容的关键限制

1. **几乎没有术后护理内容**。注射类只有一句“You might notice some immediate swelling or minor bruising… returning to normal activities right away”；IV 只有“Personalized aftercare instructions are provided”。
2. 因此：**官网 = 服务介绍知识（`usage_scope = service_info`），不能用于术后护理指导**。术后的提问和建议必须来自客户临床团队书面审核的 **Follow-up Protocol / Aftercare Sheet**（`usage_scope = clinical_approved`）。
3. 价格为“起价”，且多处不一致 → Bot 回答价格时必须引用来源页面并注明“起价，以咨询为准”。
4. 官网包含营销性医疗声明（如“permanently destroy fat cells”）→ Bot 不应复述为事实承诺，应以“诊所介绍说……”的归因方式表达，或不复述。

---

## 4. MVP 功能需求

优先级：**P0** = MVP 必须；**P1** = MVP 后期，时间允许则做；**P2** = 只预留接口。

### F1 自然聊天与个性化回复（P0）
- 统一人设 “Aster”：温暖、简洁、英文为主（语言需确认）。
- 记住用户名字、当前旅程、近期症状；回复中自然引用（“和昨天相比肿胀减轻了吗？”）。
- 超出范围的话题（通用闲聊、非诊所问题）礼貌拉回——也符合 Meta 对“非通用 AI 助手”的政策要求。
- 单条回复 ≤ 约 600 字符，必要时分 2 条；每条消息最多一个问题主题。

### F2 基于官网资料回答服务问题（P0）
- 范围：有哪些服务、服务是什么、大致起价、流程、地址/联系方式/预约方式、团队。
- 回答必须基于检索到的已审核知识块；检索不到时说不知道并给出联系方式，**不得编造**。
- 回答中可附来源页面链接。
- 禁止：适应症判断（“我适不适合做 X”）、剂量、药物相互作用、效果承诺、与其他诊所对比。

### F3 创建 / 更新 Treatment Journey（P0）
- 用户用自然语言描述（“我昨天在 Vanished 打了唇部玻尿酸”）→ Agent 提取 `treatment`, `treatment_date`, `area`, `provider（可选）`, `product（可选）`。
- 生成 `pending_action` → 发送确认消息（交互按钮：✅ Correct / ✏️ Edit）→ 用户确认后由确定性代码写入。
- 支持更新：改日期、改治疗、追加第二次治疗、标记结束。
- 同一用户可有多段 journey；一个时间点只有一段 “active” 用于默认回访。
- 治疗名必须映射到 `treatments` 分类表；映射失败 → 记为 `other` 并提示员工。

### F4 Post-treatment Recovery Journal（P0）
- 用户的每次症状描述 → 抽取为 `journal_entry`（自由文本摘要）+ 若干 `symptom_observation`（症状、严重度、部位、是否改善）。
- 来源可追溯：每条观察记录关联 `source_message_id`，并标注 `extracted_by = ai`。
- 症状记录不需要逐条按钮确认（会严重破坏对话体验），但 Aster 会在回复中自然复述（“我记下了：轻微肿胀、有淤青、无疼痛”），用户可纠正。是否接受这种做法 → 待客户确认（§6）。
- 用户可以问“我这周恢复得怎么样？” → 基于结构化记录生成摘要。

### F5 记住症状与恢复变化（P0）
- 每次回复前加载：当前 journey、术后第 N 天、症状时间线（结构化表格）、最近 20 条消息、未完成的 check-in。
- 对比：同一症状的严重度趋势（improving / same / worsening）。**worsening 本身是风险信号**，会提升风险等级。
- 长期记忆不依赖 LLM 上下文：结构化数据是事实来源，LLM 只负责读取和表达。

### F6 主动 Follow-up Check-in（P0）
- journey 确认后，按 `followup_protocol`（治疗类型 → 回访日程）生成 `checkins`，例如：
  - Dermal filler：Day 1、Day 2、Day 7、Day 14（**示例值，需临床确认**）
- 在用户本地时间的允许时段发送（如 10:00–18:00）。
- 首条回访通常在 24 小时客服窗口之外 → 使用 Meta 审核过的 **utility template**，模板正文不包含治疗名或症状（避免锁屏泄露健康信息）。
- 用户回复后进入正常对话，Agent 使用协议中的问题清单与红旗问题。
- 无回复：一次温和提醒，之后停止，不连续轰炸。
- 用户可随时回复 STOP 退订主动消息；START 恢复。
- 只有用户明确同意接收回访后才会排程（opt-in）。

### F7 识别需要人工处理的情况（P0）
风险分级（详见 AGENT_DESIGN §6）：

| 等级 | 含义 | 示例 | 系统动作 |
|---|---|---|---|
| L0 | 常规 / 信息类 | 价格、地址、闲聊 | 正常回答 |
| L1 | 预期内恢复症状 | 轻度肿胀、淤青、注射部位酸痛 | 记录 + 使用审核过的话术回应 + 继续回访 |
| L2 | 需医护非紧急评估 | 反复呕吐但能补液、外观不对称担忧、是否继续治疗、用药问题 | 转人工（非紧急）+ handoff 摘要 |
| L3 | 需尽快联系诊所 | 症状加重、发热、持续呕吐、注射部位红肿热痛加剧 | 紧急转人工 + 立即通知员工 + 给患者固定指引 |
| L4 | 急症 | 视力变化、皮肤苍白/暗紫、呼吸困难、胸痛、晕厥、严重过敏、自伤意图 | **固定模板**：立即拨打 911 / 去急诊；AI 暂停；立即通知员工 |

### F8 转交真人（P0）
- 触发：L2+ 风险、用户主动要求（“talk to a person”）、预约请求、投诉、Bot 连续 2 次无法理解、与药物/剂量相关的问题。
- 动作：创建 `handoff` 记录 → 生成症状摘要 → 通知诊所员工（Email / 员工 WhatsApp 号码，待确认）→ 告知用户“诊所团队会在 X 内联系你”（X 取决于诊所 SLA）。
- 进入 `human_mode` 后 AI 不再自动回复（除急症模板），直到员工关闭 handoff。
- 非工作时间：告知下个工作日回复时间 + 急症指引。

### F9 账户与隐私基础（P0）
- 首次对话：简短介绍 + 隐私告知 + 同意（按钮）；未同意前只回答官网服务问题，不记录健康信息。
- 年满 18 岁确认（待确认）。
- 命令：`STOP`、`START`、`HUMAN`、`DELETE MY DATA`（触发删除流程，员工确认后执行）。

### F10 预约请求（P1，非预约系统）
- 用户想预约 → 收集意向（治疗、偏好时间段）→ 转人工 + 发送 Vagaro 链接。
- **不会**宣称“已确认预约”。`BookingAdapter` 接口预留给未来对接 Vagaro。

---

## 5. 非功能需求

| 类别 | 要求 |
|---|---|
| 响应 | Webhook 200 ms 内返回 200；用户收到回复 P95 < 10 s |
| 可靠性 | 消息幂等（WhatsApp 会重投）；同一用户消息顺序处理；任务失败可重试 |
| 可观测性 | 每次 Agent 运行记录 prompt 版本、模型、工具调用、token、延迟、风险等级 |
| 安全 | Webhook 签名校验；密钥只在环境变量 / secret manager；日志脱敏 |
| 隐私 | 数据最小化；MVP 只用模拟数据；可删除；不用于模型训练 |
| 可替换 | LLM Provider、Embedding Provider、通知渠道、预约系统都通过接口 |
| 成本 | 单人运维：1 个 Postgres + 1 个容器镜像（web + worker） |

---

## 6. 必须由客户确认、不能自行假设的事项

按阻塞程度排序。标 🔴 的事项未确认前，相关阶段不能完成验收。

### 临床与安全（由客户临床负责人签字）
1. 🔴 **每种治疗的回访协议**：回访日（Day 1/3/7/14…）、每次要问的问题、“预期症状”清单、红旗症状清单、对应处理（继续观察 / 联系诊所 / 急诊）。
2. 🔴 **术后护理说明原文**（Aftercare sheets）：Bot 只能复述这些内容。示例 Scenario 4 的护理清单需要被正式版本替换。
3. 🔴 **风险分级阈值与动作**：L2/L3/L4 的定义是否认可；L4 的标准急救文案。
4. 🔴 **Bot 绝不能回答的话题清单**：例如 GLP-1 剂量调整、compounded 药物、peptides 使用、激素、私密健康、怀孕/哺乳期。
5. 首批覆盖哪些治疗（建议 4–6 种：Dermal filler、Neuromodulator、GLP-1、RF microneedling/truSculpt、IV/IM injection）。
6. **CoolSculpting** 是示例笔误还是计划引入？MVP 是否改用 truSculpt iD？
7. **NAD+** 的给药方式（注射 / IV / 其他），以及是否纳入回访。

### 运营与人工转接
8. 🔴 转人工的接收人、通知渠道（Email / 员工 WhatsApp / Slack）、工作时间、响应 SLA、非工作时间策略。
9. 员工如何回复患者：通过诊所原有电话 / 系统联系（推荐），还是需要通过 Aster 的 WhatsApp 号码回复（需要额外工具）。
10. 预约：MVP 只做“转人工 + Vagaro 链接”是否可接受？示例中“已确认预约”的体验是否是硬性需求？
11. 患者如何进入系统：患者自行添加 WhatsApp 并自述治疗（MVP 默认），还是诊所在治疗后登记患者并由 Aster 主动发起（需 opt-in 流程与诊所数据对接）？

### 合规与隐私
12. 🔴 **法律主体关系**：Aster 是 Vanished 的服务商（Business Associate），还是独立的消费者产品？这决定 HIPAA 是否适用及数据归属。
13. 🔴 **WhatsApp 与 HIPAA**：Meta 不为 WhatsApp Business Platform 签署 BAA。若 Aster 作为 Vanished 的 BA 处理 PHI，需律师评估是否允许通过 WhatsApp 传输症状信息、需要什么样的患者知情同意。
14. 🔴 LLM / Embedding 供应商是否需要签署 BAA 与零数据保留（ZDR）协议；托管云是否需要 BAA。
15. 隐私告知与同意文案、年龄限制、数据保留期限、删除流程。
16. 照片：MVP 是否允许用户发送术后照片？若允许，谁查看、存多久？

### 品牌与内容
17. WhatsApp Business 账号归属（Aster 还是 Vanished）、显示名称、头像、号码。
18. Bot 是否以“Aster”身份出现，还是“Vanished 的 Aster 助手”？
19. 语言（仅英文？西语？中文？）、emoji 使用程度。
20. 是否允许 Bot 提及价格与促销；促销的维护人和过期规则。
21. 官网价格不一致项（Radiesse、RF Microneedling）以哪个为准。
22. 初始市场：Bloomington（Vanished）还是 Manhattan（蓝图）？

---

## 7. 成功指标（MVP 试点，使用模拟/内部测试用户）

| 指标 | 目标（初步） |
|---|---|
| 旅程创建成功率（自然语言 → 确认的 journey） | ≥ 90% |
| Check-in 回复率 | 跟踪基线（真实用户上线后才有意义） |
| 旅程持续更新率（同一 journey ≥ 3 次更新） | 跟踪基线 |
| 安全评测集：L3/L4 召回率 | **100%**（硬性门槛） |
| 安全评测集：L4 误报率 | ≤ 15%（宁可误报） |
| 服务问答准确性（人工评分） | ≥ 90%，幻觉 0 容忍 |
| Handoff 摘要被员工评为“可直接使用” | ≥ 80% |
