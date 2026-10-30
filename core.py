"""Source-separated reading archive. No chat ingestion and no send implementation."""
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo
import asyncio
import hashlib
import ipaddress
import json
import re
import sqlite3
import time
import uuid

CHANNELS = frozenset({'qzone', 'private', 'group'})


def clean_url(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 2048:
        return ''
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or '').lower().rstrip('.')
        if parsed.scheme != 'https' or not host or parsed.username or parsed.password or parsed.port not in {None, 443}:
            return ''
        if host in {'localhost', 'localhost.localdomain'} or '.' not in host or host.endswith(('.local', '.internal', '.localhost')):
            return ''
        try:
            if not ipaddress.ip_address(host).is_global:
                return ''
        except ValueError:
            if not re.fullmatch(r'[a-z0-9.-]+', host):
                return ''
        if any(ord(c) < 33 for c in value) or '\\' in value or '%' in parsed.netloc:
            return ''
        return urlunsplit(('https', host, parsed.path or '/', '', ''))
    except (TypeError, ValueError):
        return ''


def text(value, limit=300):
    value = value if isinstance(value, str) else ''
    # Do not propagate authentication fields embedded in source or model text.
    value = re.sub(r'(?i)(xsec[_-]?token|cookie|authorization|access[_-]?token|user[_-]?id)\s*[:=]\s*[^\s,;]+', '[redacted]', value)
    value = re.sub(r'https?://[^\s<>]+', lambda m: clean_url(m.group()) or '[invalid URL]', value)
    return ''.join(c for c in value if c >= ' ' or c == '\n')[:limit].strip()


def normalize_feed(raw: dict) -> dict:
    card = raw.get('noteCard') or raw.get('note_card') or raw
    feed_id = str(raw.get('id') or raw.get('note_id') or card.get('noteId') or card.get('note_id') or '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{3,80}', feed_id):
        raise ValueError('Invalid feed identifier')
    url = 'https://www.xiaohongshu.com/explore/' + feed_id
    images = []
    for image in (card.get('imageList') or card.get('image_list') or [])[:6]:
        if not isinstance(image, dict):
            continue
        image_url = clean_url(image.get('urlDefault') or image.get('url_default') or image.get('url'))
        # Source-linked CDN images only; do not permit arbitrary remote attachments.
        if image_url and urlsplit(image_url).hostname.endswith(('.xhscdn.com', '.xiaohongshu.com')) and image_url not in images:
            images.append(image_url)
    user = card.get('user') if isinstance(card.get('user'), dict) else {}
    return {'id':hashlib.sha256(url.encode()).hexdigest()[:24], 'source_kind':'external_reading', 'title':text(card.get('displayTitle') or card.get('display_title') or card.get('title') or '阅读笔记', 200), 'url':url, 'author':text(user.get('nickname') or card.get('author'), 80), 'observed_at':datetime.now(timezone.utc).isoformat(), 'body':text(card.get('desc') or card.get('content'), 3000), 'images':images}


class Store:
    def __init__(self, path: Path, timezone_name: str='Asia/Shanghai'):
        self.path = Path(path)
        self.timezone = ZoneInfo(timezone_name)

    def _work(self, fn):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(self.path), timeout=10) as db:
            self.path.chmod(0o600)
            db.row_factory = sqlite3.Row
            return fn(db)

    async def initialize(self):
        def init(db):
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS readings(id TEXT PRIMARY KEY, material TEXT NOT NULL, remembered INTEGER NOT NULL, observed REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS shares(material_id TEXT PRIMARY KEY, channel TEXT NOT NULL, target_hash TEXT NOT NULL, receipt_id TEXT NOT NULL, shared REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS leases(material_id TEXT PRIMARY KEY, channel TEXT NOT NULL, lease_id TEXT NOT NULL, target_hash TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS budgets(day TEXT PRIMARY KEY, used INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS decisions(id INTEGER PRIMARY KEY, at REAL NOT NULL, reason TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS pending(task_id TEXT PRIMARY KEY, material_id TEXT NOT NULL, channel TEXT NOT NULL, session TEXT NOT NULL, expires REAL NOT NULL);
            ''')
        await asyncio.to_thread(self._work, init)

    async def save(self, material: dict, impression: str, remember: bool):
        allowed = {key:material[key] for key in ('id','source_kind','title','url','author','observed_at','images')}
        allowed['impression'] = text(impression, 500)
        def save(db):
            db.execute('INSERT INTO readings VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET material=CASE WHEN readings.remembered=1 AND excluded.remembered=0 THEN readings.material ELSE excluded.material END, remembered=MAX(readings.remembered,excluded.remembered)', (allowed['id'], json.dumps(allowed, ensure_ascii=False), int(remember), time.time()))
            # Archive is deliberately bounded; receipts remain for global dedup.
            db.execute('DELETE FROM readings WHERE id IN (SELECT id FROM readings ORDER BY observed DESC LIMIT -1 OFFSET 2000)')
        await asyncio.to_thread(self._work, save)

    async def materials(self, limit: int):
        def get(db):
            return [json.loads(row[0]) for row in db.execute('SELECT material FROM readings WHERE remembered=1 AND id NOT IN (SELECT material_id FROM shares) AND id NOT IN (SELECT material_id FROM leases) ORDER BY observed DESC LIMIT ?', (max(1, min(5, int(limit))),))]
        return await asyncio.to_thread(self._work, get)

    async def reserve(self, material_id: str, channel: str, target_id=''):
        if channel not in CHANNELS:
            return {'success':False, 'error':'unknown_channel'}
        def reserve(db):
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM readings WHERE id=? AND remembered=1', (material_id,)).fetchone():
                return {'success':False, 'error':'unknown_material'}
            if db.execute('SELECT 1 FROM shares WHERE material_id=?', (material_id,)).fetchone():
                return {'success':False, 'error':'already_shared'}
            if db.execute('SELECT 1 FROM leases WHERE material_id=?', (material_id,)).fetchone():
                return {'success':False, 'error':'reserved'}
            lease_id = uuid.uuid4().hex
            db.execute('INSERT INTO leases VALUES(?,?,?,?)', (material_id,channel,lease_id,hashlib.sha256(str(target_id).encode()).hexdigest()))
            return {'success':True, 'lease_id':lease_id}
        return await asyncio.to_thread(self._work, reserve)

    async def release(self, material_id: str, lease_id: str):
        def release(db):
            deleted = db.execute('DELETE FROM leases WHERE material_id=? AND lease_id=?', (material_id,lease_id)).rowcount
            db.execute('DELETE FROM pending WHERE material_id=? AND NOT EXISTS(SELECT 1 FROM leases WHERE material_id=?)', (material_id,material_id))
            return {'success':bool(deleted)}
        return await asyncio.to_thread(self._work, release)

    async def record_share(self, material_id, channel, target_id='', receipt_id='', lease_id=''):
        if channel not in CHANNELS:
            return {'success':False, 'error':'unknown_channel'}
        if not isinstance(receipt_id, str) or not receipt_id.strip() or len(receipt_id) > 256:
            return {'success':False, 'error':'successful_receipt_required'}
        def record(db):
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM readings WHERE id=? AND remembered=1', (material_id,)).fetchone():
                return {'success':False, 'error':'unknown_material'}
            previous = db.execute('SELECT 1 FROM shares WHERE material_id=?', (material_id,)).fetchone()
            if previous:
                return {'success':True, 'already_shared':True}
            lease = db.execute('SELECT channel,lease_id,target_hash FROM leases WHERE material_id=?', (material_id,)).fetchone()
            if lease and (not lease_id or lease['lease_id'] != lease_id or lease['channel'] != channel or lease['target_hash'] != hashlib.sha256(str(target_id).encode()).hexdigest()):
                return {'success':False, 'error':'lease_mismatch'}
            if lease_id and not lease:
                return {'success':False, 'error':'lease_missing'}
            db.execute('INSERT INTO shares VALUES(?,?,?,?,?)', (material_id, channel, hashlib.sha256(str(target_id).encode()).hexdigest(), receipt_id, time.time()))
            db.execute('DELETE FROM leases WHERE material_id=?', (material_id,))
            db.execute('DELETE FROM pending WHERE material_id=?', (material_id,))
            return {'success':True, 'already_shared':False}
        return await asyncio.to_thread(self._work, record)

    async def recall(self, query, limit=3):
        query = text(query, 80)
        if not query:
            return []
        def recall(db):
            # literal substring, not SQL LIKE wildcards; no chat text is retained.
            rows = db.execute('SELECT material FROM readings WHERE remembered=1 ORDER BY observed DESC LIMIT 2000').fetchall()
            return [item for row in rows if query.casefold() in json.dumps(item := json.loads(row[0]), ensure_ascii=False).casefold()][:max(1,min(5,int(limit)))]
        return await asyncio.to_thread(self._work, recall)

    async def take_budget(self, maximum):
        if maximum <= 0:
            return False
        day = datetime.now(self.timezone).strftime('%Y-%m-%d')
        def take(db):
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM budgets WHERE day<?', (day,))
            used = db.execute('SELECT used FROM budgets WHERE day=?', (day,)).fetchone()
            if used and used[0] >= maximum:
                return False
            db.execute('INSERT INTO budgets VALUES(?,1) ON CONFLICT(day) DO UPDATE SET used=used+1', (day,))
            return True
        return await asyncio.to_thread(self._work, take)

    async def decision(self, reason):
        def save(db):
            db.execute('INSERT INTO decisions(at,reason) VALUES(?,?)', (time.time(), reason))
            db.execute('DELETE FROM decisions WHERE id NOT IN (SELECT id FROM decisions ORDER BY id DESC LIMIT 100)')
        await asyncio.to_thread(self._work, save)

    async def status(self):
        def status(db):
            return {'readings':db.execute('SELECT COUNT(*) FROM readings').fetchone()[0], 'remembered':db.execute('SELECT COUNT(*) FROM readings WHERE remembered=1').fetchone()[0], 'shared':db.execute('SELECT COUNT(*) FROM shares').fetchone()[0], 'pending_reservations':db.execute('SELECT COUNT(*) FROM leases').fetchone()[0], 'decisions':[dict(row) for row in db.execute('SELECT at,reason FROM decisions ORDER BY id DESC LIMIT 20')]}
        return await asyncio.to_thread(self._work, status)

    async def bind_task(self, task_id, material_id, channel, session):
        def bind(db):
            if db.execute('SELECT 1 FROM leases WHERE material_id=?',(material_id,)).fetchone() and not db.execute('SELECT 1 FROM shares WHERE material_id=?',(material_id,)).fetchone():
                db.execute('INSERT OR REPLACE INTO pending VALUES(?,?,?,?,?)', (task_id,material_id,channel,session,time.time()+600))
        await asyncio.to_thread(self._work, bind)

    async def pending_task(self, task_id, session):
        def get(db):
            row = db.execute('SELECT * FROM pending WHERE task_id=? AND session=? AND expires>?', (task_id,session,time.time())).fetchone()
            return dict(row) if row else {}
        return await asyncio.to_thread(self._work, get)


class Reader:
    def __init__(self, store, client, judge):
        self.store, self.client, self.judge = store, client, judge
        self.gate = asyncio.Lock()

    async def browse(self, enabled, activity, allowed_activities, interests, daily_budget, max_items):
        if not enabled:
            return {'success':False, 'reason':'disabled'}
        if not activity or activity not in allowed_activities:
            return {'success':False, 'reason':'not_idle'}
        if self.gate.locked():
            return {'success':False, 'reason':'busy'}
        async with self.gate:
            if not await self.store.take_budget(max(0,min(24,int(daily_budget)))):
                return {'success':False, 'reason':'budget'}
            try:
                result = await asyncio.wait_for(self._read(interests, max_items), timeout=240.)
                await self.store.decision('browsed')
                return result
            except asyncio.CancelledError:
                raise
            except Exception:
                # Never log raw sidecar exceptions, token-bearing tool results, or URLs.
                await self.store.decision('browse_failed')
                return {'success':False, 'reason':'browse_failed'}

    async def _read(self, interests, max_items):
        login = await self.client.call('check_login_status')
        logged_in = login.get('is_logged_in') is True if isinstance(login, dict) else False
        # Official sidecar currently emits plain Chinese login status text.
        if isinstance(login, dict) and '已登录' in str(login.get('text', '')) and '未登录' not in str(login.get('text', '')):
            logged_in = True
        if not logged_in:
            await self.store.decision('login_required')
            return {'success':False, 'reason':'login_required'}
        interests = [text(v, 40) for v in interests if isinstance(v,str) and v.strip()][:12]
        if not interests:
            return {'success':False, 'reason':'no_interests'}
        # Interest-only query; no session/user/chat content enters search.
        keyword = interests[int(time.time() // 3600) % len(interests)]
        result = await self.client.call('list_feeds')
        feeds = result.get('feeds', []) if isinstance(result, dict) else []
        if not isinstance(feeds,list) or not feeds:
            result = await self.client.call('search_feeds', {'keyword':keyword})
            feeds = result.get('feeds', []) if isinstance(result, dict) else []
        if not isinstance(feeds,list):
            raise ValueError('Unexpected feed list schema')
        remembered = 0
        share_candidates = []
        for raw in feeds[:max(1,min(3,int(max_items)))]:
            if not isinstance(raw, dict):
                continue
            try:
                material = normalize_feed(raw)
                note_id = str(raw.get('id') or raw.get('note_id'))
                token = raw.get('xsecToken') or raw.get('xsec_token')
                if not isinstance(token, str) or len(token) > 1024:
                    continue
                # Authentication routing token is transient; never persisted or returned.
                detail = await self.client.call('get_feed_detail', {'feed_id':note_id, 'xsec_token':token})
                data = detail.get('data', detail) if isinstance(detail,dict) else {}
                note = data.get('note', {})
                if not isinstance(note, dict) or not note:
                    continue
                note = dict(note, id=note_id)
                material = normalize_feed(note)
                verdict = await asyncio.wait_for(self.judge(material), timeout=15.)
                remember = isinstance(verdict,dict) and verdict.get('remember') is True
                impression = verdict.get('impression','') if isinstance(verdict,dict) else ''
                if not text(impression,500):
                    remember = False
                await self.store.save(material, impression, remember)
                remembered += int(remember)
                share_channel = verdict.get('share_channel','none') if isinstance(verdict,dict) else 'none'
                if remember and share_channel in CHANNELS:
                    share_candidates.append({'material_id':material['id'],'channel':share_channel})
            except asyncio.CancelledError:
                raise
            except Exception:
                await self.store.decision('item_ignored')
        return {'success':True, 'remembered':remembered, 'share_candidates':share_candidates}
