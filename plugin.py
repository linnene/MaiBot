"""Opt-in, scoped voice examples. Examples are style data, never memories."""
from datetime import datetime
from pathlib import Path
from typing import Any
import asyncio
import copy
import hashlib
import json
import math
import time
import uuid

from maibot_sdk import Field, HookHandler, MaiBotPlugin, PluginConfigBase
from maibot_sdk.types import HookMode

MARKER = '【表达示例：仅参考说话方式，不是聊天记录或记忆】'

class PluginSection(PluginConfigBase):
    enabled: bool = Field(default=False, description='启用精选表达参考')
    config_version: str = Field(default='1.0.0', description='配置版本')

class VoiceSection(PluginConfigBase):
    mode: str = Field(default='off', description='off / shadow / active；shadow仅观测，不注入')
    allowed_sessions: list[str] = Field(default_factory=list, description='允许试用的真实会话ID；留空不处理任何会话')
    index_path: str = Field(default='voice-index.json', description='精选索引，相对SDK持久化数据目录')
    min_similarity: float = Field(default=.60, ge=0., le=1., description='最低相似度')
    min_margin: float = Field(default=.08, ge=0., le=1., description='第一名与第二名最小差距')

class VoiceConfig(PluginConfigBase):
    plugin: PluginSection = Field(default_factory=PluginSection)
    voice: VoiceSection = Field(default_factory=VoiceSection)

def vector(value: Any, dimension: int) -> list[float]:
    if not isinstance(value, list) or len(value) != dimension:
        raise ValueError('embedding dimension mismatch')
    result = [float(v) for v in value]
    if not all(math.isfinite(v) for v in result) or sum(v*v for v in result) == 0:
        raise ValueError('embedding must be finite and nonzero')
    return result

def cosine(left: list[float], right: list[float]) -> float:
    return sum(a*b for a,b in zip(left,right)) / math.sqrt(sum(a*a for a in left)*sum(b*b for b in right))

class CuratedVoicePlugin(MaiBotPlugin):
    config_model = VoiceConfig

    def _load_index(self) -> dict[str, Any]:
        path = Path(self.config.voice.index_path)
        if not path.is_absolute():
            path = Path(self.ctx.paths.data_dir) / path
        data = json.loads(path.read_text())
        dim = int(data['dimension'])
        if not 1 <= dim <= 4096 or not 2 <= len(data['entries']) <= 32:
            raise ValueError('invalid curated voice index size')
        data['probe_vector'] = vector(data['probe_vector'], dim)
        if not data.get('model_name') or not data.get('probe_text'):
            raise ValueError('missing index model profile')
        ids = set()
        for entry in data['entries']:
            if entry['id'] in ids:
                raise ValueError('duplicate example id')
            ids.add(entry['id'])
            for field in ('situation','input','output'):
                if not isinstance(entry[field], str) or not 0 < len(entry[field]) <= 160:
                    raise ValueError('invalid voice example text')
            entry['vector'] = vector(entry['vector'],dim)
        return data

    async def on_load(self) -> None:
        self._stopping = False
        self._gate = asyncio.Lock()
        self._index = await asyncio.to_thread(self._load_index)
        self._profile_verified = False
        self._query_cache = {}
        self._recent = {}
        self._decisions = []
        self.ctx.logger.info('精选表达参考已加载；仅允许会话、仅brief、每次最多一条')

    async def on_unload(self) -> None:
        self._stopping = True
        async with self._gate:
            self._query_cache.clear()
            self._recent.clear()
            self._decisions.clear()
            self._profile_verified = False

    async def on_config_update(self, scope: str, config_data: dict[str, Any], version: str) -> None:
        await self.on_load()

    async def _embed(self, text: str) -> list[float]:
        response = await self.ctx.llm.embed(text=text,task_name='embedding',rpc_timeout_ms=1800)
        if not response.get('success') or response.get('model_name') != self._index['model_name']:
            raise ValueError('embedding model mismatch or failure')
        return vector(response.get('embedding'),self._index['dimension'])

    async def _rank(self, query: str) -> list[tuple[float, dict[str, Any]]]:
        if not self._profile_verified:
            probe = await self._embed(self._index['probe_text'])
            if cosine(probe,self._index['probe_vector']) < .999:
                raise ValueError('embedding backend changed; rebuild curated index')
            self._profile_verified = True
        key = hashlib.sha256(query.encode()).hexdigest()
        cached = self._query_cache.get(key)
        if cached and time.monotonic()-cached[0] < 600:
            embedding = cached[1]
        else:
            embedding = await self._embed(query)
            self._query_cache[key] = (time.monotonic(),embedding)
            if len(self._query_cache)>64:
                del self._query_cache[next(iter(self._query_cache))]
        return sorted([(cosine(embedding,e['vector']),e) for e in self._index['entries']],key=lambda row:row[0],reverse=True)

    async def _record(self, session: str, reason: str, **fields: Any) -> None:
        row = dict(at=datetime.now().isoformat(),session_hash=hashlib.sha256(session.encode()).hexdigest()[:12],reason=reason,**fields)
        self._decisions.append(row);self._decisions=self._decisions[-100:]
        path=Path(self.ctx.paths.data_dir)/'voice-decisions.json'
        def write() -> None:
            path.parent.mkdir(parents=True,exist_ok=True)
            temp=path.with_suffix('.tmp');temp.write_text(json.dumps(self._decisions,ensure_ascii=False));temp.replace(path)
        try:
            await asyncio.to_thread(write)
        except OSError:
            self.ctx.logger.exception('精选表达观测记录保存失败')
        self.ctx.logger.info('精选表达 '+json.dumps(row,ensure_ascii=False))

    @HookHandler('maisaka.replyer.before_model_request',mode=HookMode.BLOCKING,timeout_ms=3000)
    async def on_request(self, **kwargs: Any) -> dict[str, Any]:
        if self._stopping or self._gate.locked():
            return {'action':'continue'}
        async with self._gate:
            return await self._on_request(**kwargs)

    async def _on_request(self, **kwargs: Any) -> dict[str, Any]:
        keep={'action':'continue'}
        cfg=self.config
        session=str(kwargs.get('session_id') or '')
        args=kwargs.get('reply_tool_args') or {}
        if not cfg.plugin.enabled or cfg.voice.mode not in {'active','shadow'} or session not in cfg.voice.allowed_sessions:
            return keep
        if args.get('reply_boundary','brief')!='brief' or '_host_proactive_task' in args:
            return keep
        items=kwargs.get('items')
        if kwargs.get('item_schema_version',1) != 1:
            return keep
        if not isinstance(items,list):
            return keep
        if any(MARKER in str(p.get('text','')) for i in items for p in i.get('parts',[]) if isinstance(p,dict)):
            return keep
        target_index=next((i for i in range(len(items)-1,-1,-1) if items[i].get('item_type')=='UserMessageItem'),None)
        if target_index is None:
            return keep
        text='\n'.join(str(p.get('text','')) for p in items[target_index].get('parts',[]) if p.get('type')=='text')
        if '- 发言内容：' not in text or '\n原始发言时间：' not in text:
            return keep
        target=text.split('- 发言内容：',1)[1].split('\n原始发言时间：',1)[0].strip()[:300]
        if not target:
            return keep
        query=target+'\n回应目的：'+str(args.get('reply_reference') or '').strip()[:160]
        try:
            ranked=await asyncio.wait_for(self._rank(query),timeout=2.)
        except (asyncio.TimeoutError,ValueError,KeyError,TypeError):
            await self._record(session,'embedding_unavailable')
            return keep
        except Exception:
            self.ctx.logger.exception('精选表达检索失败，保留原请求')
            return keep
        score,entry=ranked[0]
        if score<cfg.voice.min_similarity or score-ranked[1][0]<cfg.voice.min_margin:
            await self._record(session,'low_relevance',score=round(score,4))
            return keep
        previous=self._recent.get(session,[])
        if entry['id'] in previous:
            await self._record(session,'recent_example',example_id=entry['id'])
            return keep
        await self._record(session,'injected' if cfg.voice.mode=='active' else 'shadow',example_id=entry['id'],score=round(score,4))
        if cfg.voice.mode!='active':
            return keep
        reference=MARKER+'\n'+json.dumps({k:entry[k] for k in ('situation','input','output')},ensure_ascii=False)+'\n只借鉴语气和简短程度，不照抄，也不继承示例中的人物、事件或事实。若与当前意愿、关系身份或表达边界冲突，忽略示例。'
        new_item={'item_type':'UserMessageItem','meta':{'item_id':uuid.uuid4().hex,'logical_turn_id':None,'timestamp':datetime.now().isoformat()},'parts':[{'type':'text','text':reference}]}
        result=copy.deepcopy(items);result.insert(target_index,new_item)
        self._recent[session]=(previous+[entry['id']])[-2:]
        if len(self._recent)>128:
            del self._recent[next(iter(self._recent))]
        return {'action':'continue','kwargs':{'items':result}}

def create_plugin() -> CuratedVoicePlugin:
    return CuratedVoicePlugin()
