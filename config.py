"""虚拟旅行插件配置：只读观测、旅行账本与发布边界。"""

from __future__ import annotations

from maibot_sdk import Field, PluginConfigBase

from .domain.ledger import MONTHLY_MAX_YUAN, MONTHLY_MIN_YUAN


class PluginSection(PluginConfigBase):
    enabled: bool = Field(default=True, description="宿主插件开关")
    config_version: str = Field(default="1.0.0", description="配置版本")


class TravelConfig(PluginConfigBase):
    plugin: PluginSection = Field(default_factory=PluginSection)
    enabled: bool = Field(default=False, description="启用角色的虚拟旅行生活；默认关闭")
    timezone: str = Field(default="Asia/Shanghai", description="月费结算与契机使用的管理时区")
    monthly_min_yuan: int = Field(
        default=MONTHLY_MIN_YUAN, ge=MONTHLY_MIN_YUAN, description="每月虚拟经费下限（元）"
    )
    monthly_max_yuan: int = Field(
        default=MONTHLY_MAX_YUAN, le=MONTHLY_MAX_YUAN, description="每月虚拟经费上限（元）"
    )
    poll_seconds: int = Field(
        default=60, ge=30, le=600, description="轻量检查间隔；未到期不调用模型"
    )
    decision_daily_limit: int = Field(
        default=8, ge=0, le=24, description="每日内部决策次数上限，不是发送目标"
    )
    model_task: str = Field(default="utils", description="内部决策沿用现有模型路由")
    quiet_start: str = Field(default="02:00", description="夜间暂停内部决策的开始时间")
    quiet_end: str = Field(
        default="07:00", description="夜间暂停结束时间；相同值表示不设置此窗口"
    )
    auto_share: bool = Field(default=False, description="是否允许向白名单群主动发布游记")
    publish_whitelist: list[str] = Field(
        default_factory=list, description="允许主动游记的真实群会话；空列表表示不发布"
    )
    memory_export: bool = Field(
        default=False, description="是否把已发生的旅行经历导出为长期记忆；默认关闭"
    )
    memory_chat_id: str = Field(
        default="",
        description="导出记忆所属的真实私聊流 ID；为空时不导出，避免写进无法召回的孤立会话",
    )
