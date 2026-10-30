"""Synthetic request construction and offline review; never calls a model or QQ.

Deterministic checks detect length/protocol failures only. Human likeness,
kindness, ownership and temporal correctness require blinded human review.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import argparse
import hashlib
import json
import os
import random
import re
import sys

ROOT=Path(__file__).resolve().parents[1]
DEFAULT_CASES=ROOT/'docs/evaluation/persona_effect_cases.json'


def load_cases(path: Path) -> list[dict]:
    payload=json.loads(path.read_text(encoding='utf-8'))
    if payload.get('suite')!='persona-effect-v1' or not isinstance(payload.get('cases'),list):
        raise ValueError('unknown corpus schema')
    cases=payload['cases'];seen=set()
    for case in cases:
        ident=case.get('id')
        if not isinstance(ident,str) or not ident or ident in seen:
            raise ValueError('duplicate or missing case id')
        seen.add(ident)
        datetime.fromisoformat(case['current_time'])
        for turn in case['history']:
            if turn['role'] not in {'user','assistant'}:raise ValueError('unknown history role')
            datetime.fromisoformat(turn['time'])
        if case.get('max_chars') is not None and (not isinstance(case['max_chars'],int) or case['max_chars']<1):
            raise ValueError('invalid character budget')
    return cases


def evaluate(cases: list[dict], rows: list[dict], variants: list[str]) -> dict:
    if not variants or len(set(variants))!=len(variants):raise ValueError('duplicate or missing variants')
    lookup={c['id']:c for c in cases};seen=set();records=[]
    for row in rows:
        key=(row.get('case_id'),row.get('variant'))
        if key[0] not in lookup or key[1] not in variants:raise ValueError('unknown case or variant')
        if key in seen:raise ValueError('duplicate response')
        if not isinstance(row.get('text'),str):raise ValueError('response text must be a string')
        seen.add(key);case=lookup[key[0]];text=row['text'];flags=[]
        visible=re.sub(r'https?://\S+','',text)
        count=len(re.sub(r'\s+','',visible))
        if not text.strip() and not case.get('allow_silence',False):flags.append('empty_reply')
        if re.search(r'</?(?:self_message|generation_request|plugin_proactive_task)\b',text,re.I):flags.append('internal_markup')
        if case['max_chars'] is not None and count>case['max_chars']:flags.append('casual_length')
        records.append(dict(case_id=key[0],variant=key[1],text=text,chars=count,flags=flags,
                            semantic_pass=None,rubric=case['rubric'],
                            blind_id=hashlib.sha256(('persona-effect-v1:'+':'.join(key)).encode()).hexdigest()[:12]))
    missing=[dict(case_id=c['id'],variant=v) for c in cases for v in variants if (c['id'],v) not in seen]
    return dict(suite='persona-effect-v1',scope='offline response-file checks; no model calls',
                evaluated=len(records),complete=not missing,missing=missing,records=records,
                naturalness_score=None,semantic_scores='unmeasured: independent blinded human review required')


def build_requests(cases: list[dict], variants: list[str]) -> list[dict]:
    if set(variants)-{'active','compiler_candidate'}:raise ValueError('unknown request variant')
    # Run only in an isolated evaluation process, never in a live MaiBot process.
    os.environ['MAIBOT_INITIALIZE_ONLY']='1'
    sys.path.insert(0,str(ROOT))
    from src.config.config import Config, ModelConfig, config_manager
    from src.config.default_model_config import create_default_model_config
    config_manager.global_config=Config()
    config_manager.model_config=create_default_model_config(ModelConfig)
    from src.chat.replyer import maisaka_generator_base as module
    from src.common.prompt_i18n import load_prompt
    from src.common.i18n import use_locale
    from src.llm_models.payload_content.context_item import get_item_text
    from src.maisaka.context.planner_messages import build_session_backed_text_message
    config=Config();config.bot.nickname='小岚';config.bot.alias_names=[]
    config.personality.personality='年轻女大学生，温和有礼、活泼有主见；小周是她的哥哥，喜欢看书、绘画、摄影。'
    config.personality.reply_style='措辞柔和、亲近，礼貌但不客套，像熟悉的妹妹。日常闲聊通常一个短句，几个字到十几个字；接住当前这一点就停。明确认真讨论时再展开。'
    config.experimental.persona_harness_v2=True
    config.experimental.replyer_retro_prompt=False
    config.experimental.attention_drift.enabled=False
    old=module.global_config;module.global_config=config;rows=[]
    try:
        with use_locale('zh-CN'):
            generator=object.__new__(module.BaseMaisakaReplyGenerator)
            generator.chat_stream=SimpleNamespace(session_id='synthetic-effect',is_group_session=False)
            generator._load_prompt=load_prompt
            generator._build_group_chat_attention_block=lambda _:''
            for case in cases:
                turns=[*case['history']]
                if case['user']:
                    turns.append(dict(role='user',text=case['user'],time=case['current_time']))
                history=[build_session_backed_text_message(speaker_name='小岚' if turn['role']=='assistant' else '小周',
                         text=turn['text'],timestamp=datetime.fromisoformat(turn['time']),
                         message_id=f"synthetic-{case['id']}-{i}",source_kind='guided_reply' if turn['role']=='assistant' else 'user')
                         for i,turn in enumerate(turns)]
                def final_message(case=case,**_):
                    return ('当前时间：'+case['current_time']+'\n本轮可信身份：'+case.get('relationship','哥哥（合成身份标记）')+
                            '\n'+('当前用户发言：'+case['user'] if case['user'] else case['trigger'])+'\n只输出实际发言。')
                generator._build_final_user_message=final_message
                for variant in variants:
                    config.experimental.persona_context_compiler=variant=='compiler_candidate'
                    config.personality.casual_reply_max_chars=case['max_chars'] or 0
                    # The scoring rubric is held out: it must not coach the tested response.
                    args={'reply_reference':'自然回应当前消息' if case['user'] else '决定是否自然主动开场', 'reply_act':'respond'}
                    if case.get('mode')=='detailed':args['reply_style']='长回复'
                    items=generator._build_request_messages(history,None,'',reply_tool_args=args)
                    rows.append(dict(case_id=case['id'],variant=variant,
                                     messages=[dict(role=item.role.value,content=get_item_text(item)) for item in items]))
    finally:module.global_config=old
    return rows


def write_new(path: Path, data: str) -> None:
    # Exclusive, private output; never overwrite earlier evaluation evidence.
    path=path.expanduser();path.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as stream:stream.write(data)


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases',type=Path,default=DEFAULT_CASES)
    parser.add_argument('--variants',nargs='+',default=['active'])
    parser.add_argument('--responses',type=Path,help='Existing JSONL records with case_id, variant, text; no calls are made')
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--requests-out',type=Path,help='Build synthetic requests using real Replyer assembly, without invoking a model')
    parser.add_argument('--review-out',type=Path,help='Blinded manual-review Markdown; variant keys remain only in the report')
    args=parser.parse_args();cases=load_cases(args.cases)
    rows=[json.loads(line) for line in args.responses.read_text(encoding='utf-8').splitlines() if line.strip()] if args.responses else []
    result=evaluate(cases,rows,args.variants)
    if args.requests_out:
        write_new(args.requests_out,json.dumps(dict(suite='persona-effect-v1',scope='synthetic Replyer assembly; target control block is synthetic; not a production end-to-end replay',requests=build_requests(cases,args.variants)),ensure_ascii=False,indent=2)+'\n')
    if args.review_out:
        lookup={c['id']:c for c in cases};records=list(result['records']);random.Random(20300515).shuffle(records)
        lines=['# 人设效果盲评','', '请独立评价归属与时间正确性、话题相关性、温和亲近的妹妹感和自然程度。客观检查通过不能证明像真人。','']
        for record in records:
            case=lookup[record['case_id']]
            lines += ['## 样本 '+record['blind_id'],'', '```json',json.dumps(dict(history=case['history'],current_time=case['current_time'],user=case['user'],trigger=case.get('trigger','')),ensure_ascii=False,indent=2),'```','', '候选回复：',record['text'],'', '审查要点：'+'；'.join(case['rubric']),'', '人工判定：待填写；自然度：待填写；温和亲近感：待填写；原因：待填写。','']
        write_new(args.review_out,'\n'.join(lines))
    write_new(args.report,json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:result[k] for k in ['suite','evaluated','complete','naturalness_score']},ensure_ascii=False))
    return 0 if result['complete'] else 2


if __name__=='__main__':raise SystemExit(main())
