# 03 — Email Templates

## ⚠️ Critical Workflow Rule

**所有外发邮件必须先发给内部审批人，等待批准后才能发给客户。**

审批人：
- karen.zhang@boxtray.com
- roy@boxtray.com

工具：`sales/tools/email_sender.py`

## 使用流程

```
1. 准备阶段
   - 把目标客户导入到 05-leads/leads_batch_XX.csv
   - 在 03-email-templates/ 选好要用的模板

2. 发送审批预览（不发给客户！）
   python email_sender.py preview 05-leads/leads_batch_XX.csv \
     03-email-templates/01-cold-outreach/detailed-version.md

3. 等待审批
   - karen.zhang@boxtray.com 和 roy@boxtray.com 会收到预览邮件
   - 他们回复 APPROVE 或 REJECT 后，AI 自动处理

4. 批准后自动发送给客户

5. 状态查询
   python email_sender.py list
```

## 文件夹结构

```
03-email-templates/
├── README.md                    ← 本文件
├── email-templates-master.md    ← 完整合集（参考用）
├── 01-cold-outreach/            ← 冷开发信
│   ├── short-version.md
│   └── detailed-version.md
├── 02-follow-up/                ← 跟进序列
│   ├── day3-light-touch.md
│   ├── day7-value-add.md
│   ├── day14-last-attempt.md
│   └── day30-final.md
└── 03-reply-handling/           ← 客户应答模板
    ├── pricing-inquiry.md
    ├── sample-request.md
    ├── more-info.md
    └── not-interested.md
```