"""Reversible conversation working state; long-term facts remain in A_Memorix."""
from __future__ import annotations

from datetime import date, datetime, time as day_time, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

import asyncio
import hashlib
import json
import re
import sqlite3
import threading
import time
import uuid

from src.common.logger import get_logger
from src.common.paths import get_instance_root
from src.common.prompt_i18n import load_prompt

logger = get_logger("interaction_memory")
MODES = {"off", "shadow", "active"}
STATUSES = {"open", "asked", "done", "cancelled", "uncertain", "expired"}
CUES = re.compile(r"明天|后天|下周|周末|考试|面试|约好|约定|提醒|记得|取消|改到|做完|结束|结果|考完|面完|过了|没过|改期|推迟|tomorrow|exam|interview|cancel|finished", re.I)
CLOSED = re.compile(r"不聊了|先不聊|晚安|睡了|别说了|不要说话|回头聊|下次聊|bye|good ?night", re.I)
BUSY = re.compile(r"我(?:正在|在)?(?:忙|开会|上课|考试)|先忙|等会聊|暂时没空|I.am busy|I.am in a meeting", re.I)


class InteractionStore:
    """Independent additive SQLite state, safe across WebUI/main-loop threads."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()

    def run(self, operation: Callable[[sqlite3.Connection], Any]) -> Any:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path, timeout=10)
            self.path.chmod(0o600)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS conversations(
                  session_id TEXT PRIMARY KEY, last_user_at REAL DEFAULT 0, last_bot_at REAL DEFAULT 0,
                  last_proactive_at REAL DEFAULT 0, unanswered INTEGER DEFAULT 0,
                  state TEXT DEFAULT 'open', busy_until REAL DEFAULT 0, revision INTEGER DEFAULT 0);
                CREATE TABLE IF NOT EXISTS observations(
                  session_id TEXT NOT NULL, message_id TEXT NOT NULL, source_at REAL NOT NULL,
                  text TEXT NOT NULL, PRIMARY KEY(session_id,message_id));
                CREATE TABLE IF NOT EXISTS followups(
                  id TEXT PRIMARY KEY, session_id TEXT NOT NULL, title TEXT NOT NULL, kind TEXT NOT NULL,
                  source_message_id TEXT NOT NULL, quote TEXT NOT NULL, source_at REAL NOT NULL,
                  event_at REAL DEFAULT 0, due_at REAL DEFAULT 0, expires_at REAL NOT NULL,
                  status TEXT NOT NULL, updated_at REAL NOT NULL, event_day TEXT DEFAULT '');
                CREATE TABLE IF NOT EXISTS decisions(
                  id TEXT PRIMARY KEY, session_id TEXT NOT NULL, created_at REAL NOT NULL,
                  kind TEXT NOT NULL, topic_key TEXT NOT NULL, reason TEXT NOT NULL,
                  status TEXT NOT NULL, item_id TEXT DEFAULT '', task_id TEXT DEFAULT '',
                  lease_until REAL DEFAULT 0, sent_at REAL DEFAULT 0, detail TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS recall_cache(
                  session_id TEXT NOT NULL, query TEXT NOT NULL, updated_at REAL NOT NULL,
                  payload TEXT NOT NULL, PRIMARY KEY(session_id,query));
                CREATE TABLE IF NOT EXISTS write_audit(
                  id INTEGER PRIMARY KEY, session_id TEXT, created_at REAL, status TEXT, detail TEXT);
                CREATE INDEX IF NOT EXISTS followups_session ON followups(session_id,status,due_at);
                CREATE INDEX IF NOT EXISTS decisions_session ON decisions(session_id,created_at);
            """)
            try:
                with connection:
                    return operation(connection)
            finally:
                connection.close()

    def observe(self, session_id: str, message_id: str, text: str, at: float) -> bool:
        def operation(c: sqlite3.Connection) -> bool:
            inserted = c.execute("INSERT OR IGNORE INTO observations VALUES(?,?,?,?)", (session_id,message_id,at,text[:2000])).rowcount
            if not inserted:
                return False
            state = "closed" if CLOSED.search(text) else "busy" if BUSY.search(text) else "open"
            busy_until = at + (8*3600 if state == "closed" and re.search(r"睡了|晚安|good.?night",text,re.I) else 2*3600 if state in {"busy","closed"} else 0)
            # A delayed/duplicate delivery cannot reopen a more recent conversation.
            c.execute("INSERT OR IGNORE INTO conversations(session_id) VALUES(?)", (session_id,))
            c.execute("UPDATE conversations SET last_user_at=?,unanswered=0,state=?,busy_until=?,revision=revision+1 WHERE session_id=? AND last_user_at<=?", (at,state,busy_until,session_id,at))
            c.execute("DELETE FROM observations WHERE session_id=? AND source_at<?", (session_id,at-30*86400))
            return True
        return self.run(operation)

    def snapshot(self, session_id: str) -> Dict[str, Any]:
        def operation(c: sqlite3.Connection) -> Dict[str, Any]:
            row=c.execute("SELECT * FROM conversations WHERE session_id=?",(session_id,)).fetchone()
            return {"conversation":dict(row) if row else {},
                    "messages":[dict(r) for r in c.execute("SELECT * FROM observations WHERE session_id=? ORDER BY source_at DESC LIMIT 8",(session_id,))][::-1],
                    "items":[dict(r) for r in c.execute("SELECT * FROM followups WHERE session_id=? ORDER BY updated_at DESC LIMIT 30",(session_id,))]}
        return self.run(operation)

    def apply(self, session_id: str, revision: int, operations: List[Dict[str, Any]], now: float, timezone_name: str = "Asia/Shanghai") -> bool:
        """Only user-quoted evidence can create/update items; model edits are bounded."""
        def transaction(c: sqlite3.Connection) -> bool:
            row=c.execute("SELECT revision FROM conversations WHERE session_id=?",(session_id,)).fetchone()
            if row is None or row[0]!=revision:
                return False
            sources={r['message_id']:dict(r) for r in c.execute("SELECT * FROM observations WHERE session_id=? ORDER BY source_at DESC LIMIT 8",(session_id,))}
            for op in operations[:5]:
                source=sources.get(str(op.get('source_message_id','')))
                quote=op.get('quote')
                if not source or not isinstance(quote,str) or not quote.strip() or quote not in source['text']:
                    raise ValueError('Follow-up evidence must quote an actual user message')
                action=op.get('action','add')
                if action in {'done','cancelled','postpone'}:
                    item_id=str(op.get('id',''))
                    existing=c.execute("SELECT * FROM followups WHERE id=? AND session_id=?",(item_id,session_id)).fetchone()
                    if existing is None:
                        raise ValueError('Unknown follow-up item')
                    if source['source_at']<=existing['source_at']:
                        raise ValueError('Changing an item requires newer user evidence')
                    if action=='postpone':
                        due=parse_time(op.get('due_at'),now)
                        new_day=str(op.get('event_date') or '')
                        if new_day:
                            known_day=validated_day(new_day,quote,source['source_at'],now,timezone_name)
                            if not due:due=datetime.combine(known_day+timedelta(days=1),day_time(10),ZoneInfo(timezone_name)).timestamp()
                        if not due:raise ValueError('Postponement requires an explicit future time')
                        c.execute("UPDATE followups SET due_at=?,expires_at=?,status='open',source_message_id=?,quote=?,source_at=?,updated_at=?,event_day=?,event_at=? WHERE id=?",(due,due+3*86400,source['message_id'],quote,source['source_at'],now,new_day or existing['event_day'],0 if new_day else existing['event_at'],item_id))
                    else:
                        c.execute("UPDATE followups SET status=?,source_message_id=?,quote=?,source_at=?,updated_at=? WHERE id=?",(action,source['message_id'],quote,source['source_at'],now,item_id))
                    continue
                if action!='add' or op.get('subject')!='user':
                    raise ValueError('Only explicit user-owned events may create follow-ups')
                title=str(op.get('title','')).strip()[:120]
                if not title:raise ValueError('Empty follow-up title')
                due=parse_time(op.get('due_at'),now)
                event=parse_time(op.get('event_at'),now,allow_past=True)
                if event and due and due<event:raise ValueError('A follow-up cannot precede its event')
                event_day=str(op.get('event_date') or '')
                if event_day:
                    known_day=validated_day(event_day,quote,source['source_at'],now,timezone_name)
                    # The date can be known without inventing the time of the event.
                    if not due:
                        due=datetime.combine(known_day+timedelta(days=1),day_time(10),ZoneInfo(timezone_name)).timestamp()
                        due=max(now,due)
                status='open' if due else 'uncertain'
                duplicate=c.execute("SELECT id FROM followups WHERE session_id=? AND title=? AND event_at=? AND event_day=? AND status IN ('open','asked','uncertain')",(session_id,title,event,event_day)).fetchone()
                if duplicate:continue
                identity=hashlib.sha256((session_id+source['message_id']+quote).encode()).hexdigest()[:24]
                c.execute("INSERT OR IGNORE INTO followups VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",(identity,session_id,title,str(op.get('kind','follow_up'))[:24],source['message_id'],quote,source['source_at'],event,due,(due or now)+7*86400,status,now,event_day))
            c.execute("INSERT INTO write_audit(session_id,created_at,status,detail) VALUES(?,?,?,?)",(session_id,now,'applied',f'{len(operations[:5])} operations'))
            return True
        return self.run(transaction)

    def audit(self, session_id: str, status: str, detail: str, now: float) -> None:
        self.run(lambda c:c.execute("INSERT INTO write_audit(session_id,created_at,status,detail) VALUES(?,?,?,?)",(session_id,now,status,detail[:1000])).rowcount)

    def decide(self, session_id: str, candidate: Dict[str, Any], cfg: Any, now: float, *, reserve: bool) -> Dict[str, Any]:
        """Atomic candidate selection/lease; only actual delivery consumes quota."""
        def operation(c: sqlite3.Connection) -> Dict[str, Any]:
            row=c.execute("SELECT * FROM conversations WHERE session_id=?",(session_id,)).fetchone()
            state=dict(row) if row else {}
            zone=ZoneInfo(cfg.timezone);local=datetime.fromtimestamp(now,zone)
            day_start=local.replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
            sent=[dict(r) for r in c.execute("SELECT * FROM decisions WHERE session_id=? AND status='sent' AND sent_at>=?",(session_id,day_start))]
            target=cfg.daily_target_min+int(hashlib.sha256(f'{session_id}:{local.date()}'.encode()).hexdigest()[:8],16)%(cfg.daily_target_max-cfg.daily_target_min+1)
            decision={'id':uuid.uuid4().hex,'session_id':session_id,'kind':'none','topic':'','item_id':'','eligible':False,'reason':'','daily_target':target,'silence_seconds':max(0,int(now-state.get('last_user_at',now)))}
            reason=''
            if not state:reason='no_user_context'
            elif c.execute("SELECT 1 FROM decisions WHERE session_id=? AND status='queued' AND lease_until>? LIMIT 1",(session_id,now)).fetchone():reason='pending_delivery'
            elif state['busy_until']>now:reason='user_'+state['state']
            elif state['unanswered']>=cfg.max_unanswered:reason='unanswered_backoff'
            elif now-state['last_user_at']<cfg.after_user_minutes*60:reason='recent_conversation'
            elif now-max(state['last_proactive_at'],state['last_bot_at'])<cfg.min_interval_minutes*60*(1+state['unanswered']):reason='adaptive_cooldown'
            elif len(sent)>=min(cfg.daily_hard_max,int(candidate.get('daily_cap',cfg.daily_hard_max))):reason='daily_hard_limit'
            if not reason:
                due=c.execute("SELECT * FROM followups WHERE session_id=? AND status='open' AND due_at>0 AND due_at<=? AND expires_at>? ORDER BY due_at LIMIT 1",(session_id,now,now)).fetchone()
                if due:
                    decision.update(kind='follow_up',topic=due['title'],item_id=due['id'],source_message_id=due['source_message_id'],source_quote=due['quote'],event_at=due['event_at'],event_day=due['event_day'],due_at=due['due_at'])
                else:
                    kind=str(candidate.get('kind','life'))
                    decision.update(kind=kind,topic=str(candidate.get('topic',''))[:240])
                    if len(sent)>=target and kind!='occasion':reason='daily_soft_target'
                    elif kind=='life' and sum(s['kind']=='life' for s in sent)>=cfg.life_daily_max:reason='life_share_limit'
                    elif kind not in {'life','external','morning','night','shared_interest','occasion'}:reason='unsupported_candidate'
                    elif not decision['topic']:reason='empty_candidate'
                topic_key=decision.get('item_id') or hashlib.sha256(re.sub(r'\s+','',decision['topic']).encode()).hexdigest()[:24]
                if not reason and c.execute("SELECT 1 FROM decisions WHERE session_id=? AND topic_key=? AND (status='sent' OR (status='queued' AND lease_until>?)) AND created_at>?",(session_id,topic_key,now,now-cfg.topic_cooldown_hours*3600)).fetchone():reason='topic_cooldown'
            else:topic_key=''
            decision.update(eligible=not bool(reason),reason=reason or ('due_follow_up' if decision['kind']=='follow_up' else 'selected_candidate'))
            status='queued' if reserve and decision['eligible'] else 'shadow' if decision['eligible'] else 'skipped'
            c.execute("INSERT INTO decisions(id,session_id,created_at,kind,topic_key,reason,status,item_id,lease_until,detail) VALUES(?,?,?,?,?,?,?,?,?,?)",(decision['id'],session_id,now,decision['kind'],topic_key,decision['reason'],status,decision['item_id'],now+180 if status=='queued' else 0,json.dumps(decision,ensure_ascii=False)))
            c.execute("UPDATE followups SET status='expired' WHERE session_id=? AND status IN ('open','uncertain') AND expires_at<?",(session_id,now))
            c.execute("DELETE FROM decisions WHERE created_at<? AND status!='queued'",(now-30*86400,))
            return decision
        return self.run(operation)

    def bind_task(self, decision_id: str, task_id: str) -> None:
        self.run(lambda c:c.execute("UPDATE decisions SET task_id=? WHERE id=? AND status='queued'",(task_id,decision_id)).rowcount)

    def delivered(self, session_id: str, task_id: str, now: float) -> None:
        def operation(c: sqlite3.Connection) -> None:
            c.execute("UPDATE conversations SET last_bot_at=? WHERE session_id=?",(now,session_id))
            if not task_id:return
            decision=c.execute("SELECT * FROM decisions WHERE task_id=? AND session_id=? AND status='queued'",(task_id,session_id)).fetchone()
            if not decision:
                if c.execute("SELECT 1 FROM decisions WHERE task_id=? AND session_id=? AND status='sent'",(task_id,session_id)).fetchone():return
                # Observe existing proactive deliveries during shadow mode without
                # treating the shadow proposal as a message that was actually sent.
                identity='observed:'+hashlib.sha256(task_id.encode()).hexdigest()[:24]
                detail=json.dumps({'topic':'旧策略主动发送（观察记录）','silence_seconds':0,'daily_target':0})
                inserted=c.execute("INSERT OR IGNORE INTO decisions(id,session_id,created_at,kind,topic_key,reason,status,task_id,sent_at,detail) VALUES(?,?,?,?,?,?,?,?,?,?)",(identity,session_id,now,'legacy',identity,'observed_delivery','sent',task_id,now,detail)).rowcount
                if inserted:
                    c.execute("UPDATE conversations SET last_proactive_at=?,unanswered=unanswered+1 WHERE session_id=?",(now,session_id))
                return
            c.execute("UPDATE decisions SET status='sent',sent_at=? WHERE id=?",(now,decision['id']))
            c.execute("UPDATE conversations SET last_proactive_at=?,unanswered=unanswered+1 WHERE session_id=?",(now,session_id))
            if decision['item_id']:c.execute("UPDATE followups SET status='asked',updated_at=? WHERE id=? AND status='open'",(now,decision['item_id']))
        self.run(operation)

    def edit(self, item_id: str, status: str, due_at: Optional[float], now: float) -> bool:
        if status not in STATUSES:raise ValueError('Invalid item status')
        if due_at is not None and not now<=due_at<=now+366*86400:raise ValueError('Due time must be within the next 366 days')
        def operation(c: sqlite3.Connection) -> bool:
            row=c.execute("SELECT * FROM followups WHERE id=?",(item_id,)).fetchone()
            if not row:return False
            c.execute("UPDATE followups SET status=?,due_at=?,expires_at=?,updated_at=? WHERE id=?",(status,due_at if due_at is not None else row['due_at'],max(row['expires_at'],(due_at or now)+3*86400),now,item_id))
            c.execute("INSERT INTO write_audit(session_id,created_at,status,detail) VALUES(?,?,?,?)",(row['session_id'],now,'manual_correction',item_id+':'+status))
            return True
        return self.run(operation)

    def recalled(self, session_id: str, query: str, now: float) -> Optional[List[Dict[str, Any]]]:
        def operation(c: sqlite3.Connection) -> Optional[List[Dict[str, Any]]]:
            row=c.execute("SELECT payload FROM recall_cache WHERE session_id=? AND query=? AND updated_at>?",(session_id,query,now-7200)).fetchone()
            return json.loads(row[0]) if row else None
        return self.run(operation)

    def cache_recall(self, session_id: str, query: str, payload: List[Dict[str, Any]], now: float) -> None:
        def operation(c: sqlite3.Connection) -> None:
            c.execute("INSERT OR REPLACE INTO recall_cache VALUES(?,?,?,?)",(session_id,query,now,json.dumps(payload,ensure_ascii=False)))
            c.execute("DELETE FROM recall_cache WHERE updated_at<?",(now-7*86400,))
        self.run(operation)

    def overview(self) -> Dict[str, Any]:
        return self.run(lambda c:{'conversations':[dict(r) for r in c.execute('SELECT * FROM conversations')],
                                 'items':[dict(r) for r in c.execute('SELECT * FROM followups ORDER BY updated_at DESC LIMIT 100')],
                                 'decisions':[dict(r) for r in c.execute('SELECT * FROM decisions ORDER BY created_at DESC LIMIT 100')],
                                 'writes':[dict(r) for r in c.execute('SELECT * FROM write_audit ORDER BY id DESC LIMIT 100')]})


def validated_day(value: str, quote: str, source_at: float, now: float, timezone_name: str) -> date:
    known=date.fromisoformat(value);zone=ZoneInfo(timezone_name)
    today=datetime.fromtimestamp(now,zone).date()
    if not today-timedelta(days=30)<=known<=today+timedelta(days=366):raise ValueError('Event date outside bounded window')
    source_day=datetime.fromtimestamp(source_at,zone).date()
    offset=2 if '后天' in quote else 1 if re.search(r'明天|tomorrow',quote,re.I) else 0 if '今天' in quote else None
    if offset is not None and known!=source_day+timedelta(days=offset):raise ValueError('Relative date must match the original user message date')
    return known


def parse_time(value: Any, now: float, *, allow_past: bool = False) -> float:
    if value in (None,'',0):return 0
    if not isinstance(value,str):raise ValueError('Time must be an explicit ISO timestamp with timezone')
    parsed=datetime.fromisoformat(value)
    if parsed.tzinfo is None:raise ValueError('Time requires timezone')
    stamp=parsed.timestamp()
    if stamp>now+366*86400 or stamp<now-(30*86400 if allow_past else 60):raise ValueError('Time outside bounded follow-up window')
    return stamp


class InteractionMemoryService:
    def __init__(self, store: Optional[InteractionStore] = None) -> None:
        self.store=store or InteractionStore(get_instance_root()/'data'/'interaction-memory'/'state.db')
        self.jobs: Dict[str,asyncio.Task] = {}

    @property
    def config(self) -> Any:
        from src.config.config import global_config
        return global_config.interaction_memory

    def enabled_for(self, session: Any) -> bool:
        from src.config.config import global_config
        return self.config.mode!='off' and global_config.relationship_identity.enabled and not session.is_group_session and any(
            binding.platform==session.platform and binding.user_id==session.user_id
            for binding in global_config.relationship_identity.bindings)

    async def seed(self, session: Any, history: List[Any]) -> None:
        """Seed only contact timing from actual history, without replaying it to a model."""
        if not self.enabled_for(session):return
        snapshot=await asyncio.to_thread(self.store.snapshot,session.session_id)
        if snapshot['conversation']:return
        from src.maisaka.context.messages import SessionBackedMessage
        for message in reversed(history):
            if not isinstance(message,SessionBackedMessage) or message.source_kind!='user' or message.original_message is None:continue
            original=message.original_message
            if original.message_info.user_info.user_id!=session.user_id:continue
            await asyncio.to_thread(self.store.observe,session.session_id,str(original.message_id),original.processed_plain_text,message.timestamp.timestamp())
            own_times=[m.timestamp.timestamp() for m in history if isinstance(m,SessionBackedMessage) and m.source_kind=='guided_reply']
            if own_times:
                await asyncio.to_thread(self.store.delivered,session.session_id,'',max(own_times))
            return

    async def observe(self, session: Any, message: Any) -> None:
        if not self.enabled_for(session):return
        if message.message_info.user_info.user_id!=session.user_id:return
        text=message.processed_plain_text.strip()
        inserted=await asyncio.to_thread(self.store.observe,session.session_id,str(message.message_id),text,message.timestamp.timestamp())
        if inserted and CUES.search(text) and session.session_id not in self.jobs and len(self.jobs)<8:
            task=asyncio.create_task(self._extract(session.session_id))
            self.jobs[session.session_id]=task
            task.add_done_callback(lambda _task:self.jobs.pop(session.session_id,None))

    async def _extract(self, session_id: str) -> None:
        try:
            await asyncio.sleep(2)
            from src.services.llm_service import LLMServiceClient
            from src.common.data_models.llm_service_data_models import LLMGenerationOptions
            for _ in range(2):
                if self.config.mode=='off':return
                snapshot=await asyncio.to_thread(self.store.snapshot,session_id)
                revision=snapshot['conversation']['revision'];now=time.time()
                prompt=load_prompt('interaction_extract',current_time=datetime.fromtimestamp(now,ZoneInfo(self.config.timezone)).isoformat(),
                                   messages=json.dumps(snapshot['messages'],ensure_ascii=False),items=json.dumps(snapshot['items'][:10],ensure_ascii=False))
                result=await LLMServiceClient(task_name='utils',request_type='interaction_memory_extract').generate_response(prompt, LLMGenerationOptions(temperature=0.2,max_tokens=1200), session_id=session_id)
                raw=str(result.response or '').strip()
                if raw.startswith('```'):raw=re.sub(r'^```(?:json)?\s*|\s*```$','',raw)
                parsed=json.loads(raw);ops=parsed.get('operations')
                if not isinstance(ops,list) or len(ops)>5 or not all(isinstance(op,dict) for op in ops):raise ValueError('Invalid interaction extraction operations')
                if self.config.mode=='off':return
                if await asyncio.to_thread(self.store.apply,session_id,revision,ops,now,self.config.timezone):return
            await asyncio.to_thread(self.store.audit,session_id,'stale','New user input superseded extraction',time.time())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning('Interaction extraction failed: %s',exc)
            await asyncio.to_thread(self.store.audit,session_id,'failed',str(exc),time.time())

    async def context(self, session: Any) -> str:
        if self.config.mode!='active' or not self.enabled_for(session):return ''
        snapshot=await asyncio.to_thread(self.store.snapshot,session.session_id)
        now=time.time();items=[{k:r[k] for k in ('id','title','status','source_message_id','source_at','event_at','event_day','due_at')} for r in snapshot['items'] if r['status'] in {'open','asked','uncertain'} and r['expires_at']>now][:3]
        recent_text=snapshot['messages'][-1]['text'] if snapshot['messages'] else ''
        if not re.search(r'记得|之前|上次|约定|remember|previous',recent_text,re.I):
            def grams(text: str) -> set:
                clean=re.sub(r'[^\w]','',text.casefold())
                return {clean[i:i+2] for i in range(len(clean)-1)}
            words=grams(recent_text)
            items=[item for item in items if len(words & grams(item['title']))>=1]
        if not items:return ''
        return load_prompt('interaction_context',items=json.dumps(items,ensure_ascii=False))

    async def review(self, session: Any, reason: str, delegated: bool) -> Dict[str, Any]:
        if not self.enabled_for(session):return {'accepted':not delegated,'reason':reason,'decision_id':''}
        raw=json.loads(reason)
        candidate={'topic':raw.get('topic',''),'kind':raw.get('candidate_kind','life'),'daily_cap':raw.get('daily_cap',self.config.daily_hard_max)}
        related=[]
        snapshot=await asyncio.to_thread(self.store.snapshot,session.session_id)
        state=snapshot['conversation'];now=time.time()
        if candidate['kind']=='life' and state and state['busy_until']<=now and state['unanswered']<self.config.max_unanswered and now-state['last_user_at']>=self.config.after_user_minutes*60:
            def pairs(text: str) -> set:
                clean=re.sub(r'今天|明天|自己|哥哥|一下|正在|这个|那个|[\W_]','',text.casefold())
                return {clean[i:i+2] for i in range(len(clean)-1)}
            related_message=next((row for row in reversed(snapshot['messages']) if len(pairs(row['text']) & pairs(candidate['topic']))>=2),None)
            if related_message:
                # Reuse the existing memory service; this is a bounded cache, not a second fact database.
                related=await asyncio.to_thread(self.store.recalled,session.session_id,candidate['topic'],now)
                if related is None:
                    from src.services.memory_service import memory_service
                    try:
                        async with asyncio.timeout(8):
                            result=await memory_service.search(candidate['topic'],limit=2,mode='search',chat_id=session.session_id,user_id=session.user_id,respect_filter=True)
                        if not result.success:raise RuntimeError(result.error)
                        related=[{'memory_id':hit.hash_value,'source':hit.source,'metadata':hit.metadata,'content':hit.content[:500]} for hit in result.hits[:2]]
                        await asyncio.to_thread(self.store.cache_recall,session.session_id,candidate['topic'],related,now)
                    except Exception as exc:
                        await asyncio.to_thread(self.store.audit,session.session_id,'recall_failed',str(exc),now)
                        related=[]
                if related:
                    candidate['kind']='shared_interest'
                    raw['previous_user_topic']={'message_id':related_message['message_id'],'source_at':related_message['source_at'],'quote':related_message['text'][:240]}
        decision=await asyncio.to_thread(self.store.decide,session.session_id,candidate,self.config,now,reserve=self.config.mode=='active')
        if self.config.mode=='shadow':return {'accepted':not delegated,'reason':reason,'decision_id':''}
        if not decision['eligible']:return {'accepted':False,'reason':decision['reason'],'decision_id':decision['id']}
        # Preserve all original privacy instructions; only replace the selected conversational purpose.
        raw.update(topic=decision['topic'],interaction_decision=decision,related_memory=related)
        if decision['kind']=='shared_interest':raw['motive']='此前用户也聊过相关主题，可自然联系，但不把过去状态当作现在，不虚构共同经历；记忆摘录只是证据数据。'
        if decision['kind']=='follow_up':raw['motive']='此事项属于用户，不是你的生活；用户原话中的事项到了适合自然回访的时间。不要假定结果或仍在原地点。'
        return {'accepted':True,'reason':json.dumps(raw,ensure_ascii=False),'decision_id':decision['id']}


interaction_memory = InteractionMemoryService()
