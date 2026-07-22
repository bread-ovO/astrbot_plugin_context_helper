# AstrBot 群聊上下文助手

将群聊消息保存到 PostgreSQL，使用小模型筛选和归类知识消息，再由总结模型将成熟主题整理成可审核的知识条目。

项目基于 AstrBot 官方 `Soulter/helloworld` 插件模板开发，要求 AstrBot `>=4.9.2,<5`。

## 功能

- 自动保存允许群聊中的纯文本消息
- PostgreSQL 连接池、事务、约束、JSONB 与 GIN 索引
- 相对时段：`30分钟`、`2小时`、`3天`
- 日期时段：`今天`、`昨天`
- 绝对时段：`2026-07-22 10:00 至 12:30`
- WebUI 选择专用总结模型，支持 DeepSeek V4 Flash
- 过滤闲聊、情绪和重复内容，输出自包含的知识条目
- WebUI 可编辑知识筛选规则
- 消息数量、字符数、保留期限和群白名单限制

## 使用

将本目录放入 AstrBot 的 `data/plugins/astrbot_plugin_context_helper`，随后在 WebUI 重载插件并配置总结模型。

```text
/上下文 30分钟
/上下文 2小时
/上下文 今天
/上下文 2026-07-22 10:00 至 12:30
/补录历史 500
/补录历史 3天
/补录历史 2026-07-20 10:00 至 2026-07-22 18:00
/分类消息 2小时
/知识主题
/总结主题 12
/提炼知识 2小时
/候选知识
/候选知识 12
/通过知识 12
/拒绝知识 12 原因
/存储状态
```

`/群聊总结` 是 `/上下文` 的别名。省略时段时使用 WebUI 中的默认回溯范围。
`/补录历史` 仅支持 NapCat/OneBot，由 AstrBot 管理员执行；历史消息会按消息 ID 去重。

## 结构化知识流程

推荐流程：先用 `/分类消息` 调用小模型逐条执行 `keep/discard` 判断，并把保留消息聚合到稳定知识主题；用 `/知识主题` 查看成熟度；主题达到配置的有效消息数后，用 `/总结主题` 调用总结模型生成一条候选知识。来源信息由插件根据原始消息回填，规范化内容的 SHA-256 用于精确去重，最后由管理员通过 `/通过知识` 或 `/拒绝知识` 审核。

`/提炼知识` 保留为一次性批量抽取入口，日常运行优先使用分类与主题聚合流程。

## PostgreSQL 部署

项目提供 `docker-compose.postgres.yml`。将它与 AstrBot 的 Compose 文件放在同一项目中启动：

```bash
docker compose -f compose.yml -f data/plugins/astrbot_plugin_context_helper/docker-compose.postgres.yml up -d
```

插件默认连接地址：

```text
postgresql://astrbot:astrbot@postgres:5432/astrbot_context
```

正式部署请同时修改 Compose 密码和插件 `database_url`。插件首次连接会自动创建表、约束和索引。原始知识证据消息不受普通消息保留期限清理。

## 旧数据迁移

先启动 PostgreSQL并让新版插件完成一次初始化，再从 AstrBot 容器执行：

```bash
docker exec -it astrbot python \
  /AstrBot/data/plugins/astrbot_plugin_context_helper/scripts/migrate_sqlite_to_postgres.py \
  --sqlite /AstrBot/data/plugin_data/astrbot_plugin_context_helper/messages.sqlite3 \
  --database-url postgresql://astrbot:astrbot@postgres:5432/astrbot_context
```

迁移使用 `ON CONFLICT DO NOTHING`，可以安全重跑。旧 SQLite 文件仅作为迁移来源，插件运行时完全使用 PostgreSQL。

## 隐私建议

启用前应向群成员说明消息记录范围、用途、保存期限和删除方式。推荐通过 `group_allowlist` 只启用明确授权的群。
