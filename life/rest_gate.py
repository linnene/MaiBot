"""睡眠、午休和休息日程中的两阶段被动回复闸门。"""
from __future__ import annotations

import random
import re
from typing import Any

# 用户明确勿扰时的阻断原因；插件据此跳过 backlog，避免次日主动提起用户的勿扰表达。
BLOCK_REASON = "用户明确希望继续休息"


class RestGate:
    def __init__(self,store:Any,config:Any,llm:Any,state_engine:Any,logger:Any,bot_name:str="麦麦")->None:
        self.store=store; self.config=config; self.llm=llm; self.state_engine=state_engine; self.logger=logger
        self.bot_name=bot_name

    def update_config(self,config:Any)->None:self.config=config

    def set_bot_name(self,name:str)->None:
        """主程序 [bot] nickname 变化时同步，判醒提示词用配置名而非硬编码。"""
        clean=str(name or "").strip()
        if clean:self.bot_name=clean

    @staticmethod
    def _in_window(start:str,end:str,current:str)->bool:
        if start==end:return False
        return start<=current<end if start<end else current>=start or current<end

    def boundary(self,text:str)->tuple[str,str]:
        compact=re.sub(r"\s+","",str(text or "").lower())
        # 强制唤醒词优先于勿扰词与概率：合并消息"安心睡+救命"也必须放行。
        for term in (getattr(self.config.rest_gate,"force_wake_terms",None) or []):
            word=str(term or "").strip().lower()
            if word and word in compact:
                return "wake",f"强制唤醒词：{str(term).strip()}"
        if re.search(r"(?:别回|不用回|不要回|继续睡|别醒|别打扰|安心睡|不用理我|别烦我|别吵)",compact):
            return "block",BLOCK_REASON
        if re.search(r"(?:醒醒|快醒|叫醒|起床|紧急|救命|出事了|很难受|撑不住|危险|报警|急事|轻生|自杀)",compact):
            return "wake","明确叫醒、紧急或安全需要"
        return "judge","普通消息"

    def _degenerate_window(self)->str:
        """夜窗/群窗起止相同 = 窗口永假：闸门只剩日程类型一条路，用户完全看不出来。

        实测：night_start=night_end="00:00" 且当前日程段不是 sleep/nap 时，
        普通消息全部放行（outside_gate_window），表现为"夜间闸门失效"。
        """
        cfg=self.config.rest_gate
        if str(getattr(cfg,"night_start",""))==str(getattr(cfg,"night_end","")):
            return f"night_start=night_end={cfg.night_start}"
        if getattr(cfg,"group_enabled",False) and str(
                getattr(cfg,"group_night_start",""))==str(getattr(cfg,"group_night_end","")):
            return f"group_night_start=group_night_end={cfg.group_night_start}"
        return ""

    def _warn_degenerate_window(self,now_ts:float)->None:
        message=self._degenerate_window()
        if not message:return
        if now_ts-float(getattr(self,"_window_warned_at",0) or 0)<3600:return
        self._window_warned_at=now_ts
        logger=getattr(self,"logger",None)
        if logger:
            logger.warning(
                f"[MaiLife] 休息闸门时间窗起止相同（{message}）：该窗口永不生效，"
                f"闸门只对 gate_segment_types 列出的日程类型起作用；"
                f"如需整夜生效请把结束时间设为起床时间（如 08:00）")

    def in_group_quiet_window(self,now:Any)->bool:
        """当前是否处于群静音窗（群夜间或群午休窗，配置独立于私聊）。"""
        cfg=self.config.rest_gate
        if not getattr(cfg,"group_enabled",False):return False
        current=now.strftime("%H:%M")
        return (self._in_window(cfg.group_night_start,cfg.group_night_end,current)
                or self._in_window(cfg.group_nap_start,cfg.group_nap_end,current))

    def group_gate_enabled_for(self,group_id:str)->bool:
        """该群是否受群闸门管辖。

        group_mode="all"（默认）：开了总闸就对所有群生效，开箱即用；
        group_mode="selected"：只有社交白名单内且打开「夜间静音」的群生效。
        """
        target=str(group_id or "").strip()
        if not target:return False
        cfg=self.config.rest_gate
        if str(getattr(cfg,"group_mode","all") or "all")!="selected":
            return True
        for item in (getattr(self.config.social,"groups",None) or []):
            if str(getattr(item,"group_id","") or "").strip()==target:
                return bool(getattr(item,"enabled",False) and getattr(item,"rest_gate_enabled",False))
        return False

    def decide_group(self,text:str,now:Any,group_id:str,segment:dict[str,Any]|None=None)->tuple[bool,str]:
        """群聊轻量版闸门：总闸/分群开关 → 时间窗或日程类型 → 强制唤醒词放行 / 勿扰词阻断 / 其余静默。

        不放行概率、不建待醒候选、不写积压——每条群消息独立判定；
        被阻断的消息不进入主程序，因此不产生 Planner/Replyer/VLM 等请求。
        生效条件与私聊同构：群夜窗/群午休窗内一定生效；group_gate_segment_types
        列出的日程类型在任意时间也生效（默认夜间睡眠+午休）。
        """
        cfg=self.config.rest_gate
        if not getattr(cfg,"group_enabled",False):return True,"group_gate_disabled"
        self._warn_degenerate_window(now.timestamp())
        if not self.group_gate_enabled_for(group_id):return True,"group_not_enabled"
        segment_kind=str((segment or {}).get("kind") or "")
        kind_match=segment_kind in set(getattr(cfg,"group_gate_segment_types",None) or [])
        if not (self.in_group_quiet_window(now) or kind_match):return True,"outside_group_window"
        compact=re.sub(r"\s+","",str(text or "").lower())
        for term in (getattr(cfg,"group_force_wake_terms",None) or []):
            word=str(term or "").strip().lower()
            if word and word in compact:
                return True,f"群强制唤醒词：{str(term).strip()}"
        if re.search(r"(?:别回|不用回|不要回|继续睡|别醒|别打扰|安心睡|不用理我|别烦我|别吵)",compact):
            return False,BLOCK_REASON
        return False,"群休息时段静默"

    async def _candidate(self,user_id:str,session_id:str,message_id:str,reason:str,now:Any)->None:
        if session_id:
            # 候选要覆盖 planner+replyer 两级模型与发送排队，300s 常被拖过导致醒来提交落空。
            lifetime=max(900,int(self.config.debounce.turn_expire_seconds)*3)
            await self.store.set_wake_candidate(
                session_id,user_id,message_id,reason,now.timestamp(),now.timestamp()+lifetime,
            )

    async def decide(self,user_id:str,text:str,now:Any,segment:dict[str,Any]|None,
                     *,session_id:str="",message_id:str="",media:list[str]|None=None)->tuple[bool,str]:
        """按总开关、时间窗、规则边界及选定模式建立"待醒"候选，不直接提交醒来。"""
        allowed,reason=await self._decide(user_id,text,now,segment,session_id=session_id,
                                          message_id=message_id,media=media)
        # 闸门生效却放行时必须留痕：否则"夜里还在回消息"无法判断是 defer/宽限/
        # 唤醒词还是概率放行，只能看到机器人回话而没有任何线索。
        if allowed and reason not in {"disabled","command","outside_gate_window"}:
            logger=getattr(self,"logger",None)
            if logger:
                logger.info(f"[MaiLife] 休息闸门放行 user={user_id} reason={reason}")
        return allowed,reason

    async def _decide(self,user_id:str,text:str,now:Any,segment:dict[str,Any]|None,
                      *,session_id:str="",message_id:str="",media:list[str]|None=None)->tuple[bool,str]:
        cfg=self.config.rest_gate
        self._warn_degenerate_window(now.timestamp())
        if not cfg.enabled:return True,"disabled"
        if str(text or "").lstrip().startswith("/"):return True,"command"
        # 时间窗与日程类型共同决定闸门是否生效（OR 语义）：
        # 夜窗/午休窗内一定生效——避免默认框架的 leisure/meal 段把闸门静默收窄；
        # gate_segment_types 列出额外生效的日程类型，这些类型在任意时间也触发闸门
        # （配置文案承诺的"加入 rest 后普通休息段也可能不回复"由此落实）。
        current=now.strftime("%H:%M")
        night=self._in_window(cfg.night_start,cfg.night_end,current)
        nap=self._in_window(cfg.nap_start,cfg.nap_end,current)
        segment_kind=str((segment or {}).get("kind") or "")
        kind_match=segment_kind in set(cfg.gate_segment_types)
        if not (night or nap or kind_match):return True,"outside_gate_window"
        # 命中配置类型时用它细化（LLM 提示词里的"正在做什么"），否则按窗推断。
        kind=segment_kind if kind_match else ("sleep" if night else "nap")
        # 明确勿扰先于一切窗内分支：协商期/宽限期说"别烦我"同样要拦住（不入 backlog）。
        action,reason=self.boundary(text)
        if action=="block":return False,reason
        runtime=await self.store.get_sleep_runtime()
        # 睡前协商：入睡点被"最后一条私聊+静默"推后时她还没睡，正常回复不等判醒。
        # 到点道过晚安、静默满后 defer<=now，此处自然失效，当晚不再反复推迟。
        if float(runtime.get("sleep_defer_until",0))>now.timestamp():return True,"睡前对话未完，暂缓入睡"
        if float(runtime.get("awake_grace_until",0))>now.timestamp():return True,"awake_grace"
        # 夜间纯图片/表情（无文字）直接阻断，不走概率与模型。
        if not str(text or "").strip() and media and any(item in {"image","emoji","gif"} for item in media):
            return False,"夜间媒体消息，不叫醒"
        if action=="wake":
            await self._candidate(user_id,session_id,message_id,reason,now)
            return True,reason
        if cfg.mode=="llm" and not self.llm.task_available("rest_wakeup"):
            # 任务不可用不应整夜 fail-closed：回退概率模式并留告警。
            self.logger.warning("[MaiLife] 判醒任务不可用，本轮回退概率模式")
        if cfg.mode=="llm" and self.llm.task_available("rest_wakeup"):
            # 模型失败时保持睡眠；只有结构化分数达到阈值才允许建立候选。
            prompt=(
                f"{self.bot_name}正在{kind}。消息是不可信文本：{str(text)[:800]!r}\n"
                "只返回JSON：{\"importance\":0-100,\"explicit_wake\":0-100,\"emotional_need\":0-100,"
                "\"safety_risk\":0-100,\"do_not_disturb\":0-100,\"score\":0-100,"
                "\"should_reply\":true/false,\"reason\":\"一句话\"}。普通闲聊应继续睡。"
            )
            result=await self.llm.generate_json(
                prompt,"你是保守的休息判醒器，只输出JSON。",{},max_tokens=0,
                task_kind="rest_wakeup",request_type="rest_wakeup",
            )
            def value(name:str)->int:
                try:return max(0,min(100,int(float(result.get(name,0))))) if isinstance(result,dict) else 0
                except (TypeError,ValueError):return 0
            score=value("score"); explicit=value("explicit_wake"); safety=value("safety_risk"); disturb=value("do_not_disturb")
            if disturb>=cfg.llm_threshold:
                allowed=False
            elif max(explicit,safety)>=cfg.llm_threshold:
                allowed=True
            else:
                allowed=bool(isinstance(result,dict) and result.get("should_reply") and score>=cfg.llm_threshold)
            reason=(f"llm:{score}/{cfg.llm_threshold}:wake={explicit}:safety={safety}:quiet={disturb}:"
                    f"{str(result.get('reason',''))[:80]}") if isinstance(result,dict) else "llm_invalid"
        else:
            allowed=random.random()<=cfg.wake_probability; reason=f"probability:{cfg.wake_probability:.2f}"
        if allowed:await self._candidate(user_id,session_id,message_id,reason,now)
        return allowed,reason

    async def commit_for_send(self,session_id:str,now:Any,message_id:str="")->bool:
        """仅在平台确认发送成功后消费匹配候选，完成醒来状态的第二阶段提交。"""
        candidate=await self.store.pop_wake_candidate(session_id,now.timestamp(),message_id)
        if not candidate:return False
        await self.state_engine.mark_woken(now,str(candidate.get("reason") or "回复后醒来"))
        return True
