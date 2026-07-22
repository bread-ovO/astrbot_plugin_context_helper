# AstrBot 群聊上下文助手

将群聊纯文本消息保存到 SQLite，在需要建设知识库时按时段读取并调用指定模型提炼。模型仅在执行总结指令时调用。

项目基于 AstrBot 官方 `Soulter/helloworld` 插件模板开发，要求 AstrBot `>=4.9.2,<5`。

## 功能

- 自动保存允许群聊中的纯文本消息
- SQLite WAL 模式与群聊时间索引
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
```

`/群聊总结` 是 `/上下文` 的别名。省略时段时使用 WebUI 中的默认回溯范围。

数据库位于：

```text
data/plugin_data/astrbot_plugin_context_helper/messages.sqlite3
```

## 隐私建议

启用前应向群成员说明消息记录范围、用途、保存期限和删除方式。推荐通过 `group_allowlist` 只启用明确授权的群。
