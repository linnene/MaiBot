# local.virtual-travel

角色按现实时间规划、出发、游玩与返程的虚拟旅行生活。经费为虚拟人民币，独立记账；日程投影只读提供给麦生活。

## 行为

- 每月按 `Asia/Shanghai` 随机到账 15,000—25,000 元，余额累积；同月只入账一次，重启不重抽。
- 旅行阶段：想法、查资料、草拟、已确定、准备、途中、停留、返程、完成、取消；支持改期、延期与取消。
- 模型只提出方案，账本与状态机负责校验；查资料不产生订单，不能透支或增发。
- 已发生且已推进的自身事件才进入投递箱；长期记忆导出默认关闭，需显式开启。
- 主动游记默认关闭，白名单为空；发送前复核群会话、行程版本与有效期，未知回执不重试。
- WebUI 只读展示余额、行程、阶段、所在地与最近决策。

## 配置

`[plugin]` 节由宿主维护，必须包含 `enabled = true` 与 `config_version = "1.0.0"`。插件自身 `enabled` 默认 `false`。

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `timezone` | `Asia/Shanghai` | 月费结算与契机时区 |
| `monthly_min_yuan` / `monthly_max_yuan` | 15000 / 25000 | 每月虚拟经费区间 |
| `poll_seconds` | 60 | 轻量检查间隔 |
| `decision_daily_limit` | 8 | 每日内部决策上限 |
| `model_task` | `utils` | 内部决策模型路由 |
| `quiet_start` / `quiet_end` | 02:00 / 07:00 | 夜间暂停内部决策 |
| `auto_share` | `false` | 是否允许向白名单群主动发布游记 |
| `publish_whitelist` | `[]` | 允许发布游记的真实群会话 |
| `memory_export` | `false` | 是否把已发生经历导出为长期记忆 |

## 测试

```bash
uv venv --python 3.12
uv pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests
```

## 限制

- 不进行真实支付、预订、身份信息提交或文件申请。
- 目的地天气本轮不抓取；旅行期间环境快照明确标记 `weather_scope=destination_unknown`，不使用家乡天气冒充当地实况。
- 真实群发送链路未启用；代码中的白名单与回执校验先于发送，且需单独授权后再接 Host 主动任务。
