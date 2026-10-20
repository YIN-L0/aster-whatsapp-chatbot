# WhatsApp Cloud API 配置（Demo 用，约 20–30 分钟）

> 目标：用 Meta 提供的**免费测试号码**跑 Demo。测试号码最多可以给 **5 个已验证的手机号**发消息——足够你、客户和一个“员工”号码使用。
> Meta 后台界面经常改版，按钮名称如有出入以实际为准。

## 1. 创建 App
1. 打开 https://developers.facebook.com/ → **My Apps → Create App**。
2. 用例选择 **Connect with customers through WhatsApp**（或类型选 *Business*）。
3. 关联一个 Business Portfolio（没有就按提示新建）。

## 2. 拿到测试号码与 Phone Number ID
1. 左侧 **WhatsApp → API Setup**。
2. 记下 **Phone number ID** → 填到 `WA_PHONE_NUMBER_ID`。
3. 在 **To** 下拉框 → **Manage phone number list**，添加并验证（短信验证码）以下号码：
   - 你自己的手机（演示“患者”）
   - 客户的手机（可选，让客户亲手试）
   - “员工”手机（接收转人工提醒，填到 `STAFF_WHATSAPP_NUMBERS`，格式如 `15551234567`，不带 `+`）

## 3. 永久 Access Token（重要）
API Setup 页面上的临时 token **24 小时后失效**，Demo 当天会挂。按以下步骤生成永久 token：
1. https://business.facebook.com/settings → **Users → System users → Add**，角色 *Admin*。
2. 选中该 system user → **Assign assets**：把这个 App（Full control）和 WhatsApp 账号（Full control）分配给它。
3. **Generate new token** → 选择这个 App → 有效期 *Never* → 勾选权限 `whatsapp_business_messaging`、`whatsapp_business_management`。
4. 复制 token → 填到 `WA_ACCESS_TOKEN`。

## 4. App Secret
App 后台 **App settings → Basic → App secret → Show** → 填到 `WA_APP_SECRET`（用于校验 webhook 签名）。

## 5. 配置 Webhook（Render 部署完成后）
1. **WhatsApp → Configuration → Webhook → Edit**。
2. Callback URL：`https://<你的-render-域名>/webhooks/whatsapp`
3. Verify token：Render 环境变量里的 `WA_VERIFY_TOKEN` 值。
4. 点 **Verify and save**（Aster 必须已经在运行）。
5. 在 **Webhook fields** 里订阅 **messages**。

## 6. 打开 24 小时窗口
WhatsApp 规定：用户最近 24 小时内给你发过消息，Bot 才能发自由文本。
- 演示前，“患者”手机和“员工”手机都先给测试号码发一句 `hi`。
- 主动回访如果发生在 24 小时窗口外，需要 Meta 审核过的模板（见第 7 步）。Demo 中用 `#demo` / `#checkin` 命令现场触发，窗口总是打开的，所以第 7 步可选。

## 7.（可选）回访模板
**WhatsApp Manager → Message templates → Create**：
- Category：**Utility**；名称 `aster_checkin_v1`；语言 English (US)
- Body：`Hi {{1}}, it's Aster checking in on your recovery. How are you feeling today?`
- 审核通过后设置 `WA_CHECKIN_TEMPLATE=aster_checkin_v1`。
模板里刻意不写治疗名或症状——手机锁屏上可能会显示消息内容。

## 8. 验证
用你的手机给测试号码发 `hi` → 应收到 Aster 的介绍和同意按钮。再发 `#help` 查看 Demo 命令。

## 常见问题
| 现象 | 原因 |
|---|---|
| Verify and save 失败 | 服务没启动、URL 少了 `/webhooks/whatsapp`、verify token 不一致 |
| 收不到回复，日志显示 `(#131030) Recipient phone number not in allowed list` | 号码没加进测试号码的收件人列表 |
| 日志显示 `outside 24h window` | 该号码 24 小时内没有发过消息 |
| 401 签名错误 | `WA_APP_SECRET` 填错 |
| 突然全部失败 `(#190)` | 用的是临时 token，已过期 |
