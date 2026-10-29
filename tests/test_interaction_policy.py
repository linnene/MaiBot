"""Host delegation preserves quiet windows and counts only confirmed sends."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from Mai_life.config import MaiLifeSettings, UserProfile
from Mai_life.core.storage import LifeStore
from Mai_life.life.proactive import ProactiveEngine
from Mai_life.tests.support import DummyLogger


class DelegationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=LifeStore(Path(self.tmp.name)/'life.db')
        await self.store.initialize();self.config=MaiLifeSettings();self.config.proactive.delegate_interaction_policy=True
        self.now=datetime(2030,1,2,12,tzinfo=timezone(timedelta(hours=8)))
        await self.store.sync_users([UserProfile(user_id='10001',role='owner',proactive_enabled=True,daily_proactive_max=7)])
        await self.store.set_user_stream('10001','session')
        self.calls=[]
        async def trigger(**kwargs):
            self.calls.append(kwargs)
            return {'success':True,'accepted':False,'reason':'no_due_item'}
        self.ctx=SimpleNamespace(maisaka=SimpleNamespace(proactive=SimpleNamespace(trigger=trigger)))
        self.engine=ProactiveEngine(self.ctx,self.store,self.config,None,DummyLogger())

    async def asyncTearDown(self):
        await self.store.close();self.tmp.cleanup()

    async def test_empty_life_candidates_still_poll_due_items(self):
        self.assertFalse(await self.engine.patrol(self.now,await self.store.get_state()))
        self.assertEqual(len(self.calls),1)
        self.assertTrue(self.calls[0]['metadata']['interaction_delegate'])
        row=await self.store.get_user('10001')
        self.assertEqual(row['proactive_count'],0)
        self.assertEqual(self.store.conn.execute('select status from proactive_events').fetchone()[0],'skipped')

    async def test_host_can_decide_recent_user_cooldown(self):
        await self.store.record_interaction('10001','hello',self.now.timestamp()-60,12)
        self.assertFalse(await self.engine.patrol(self.now,await self.store.get_state()))
        self.assertEqual(len(self.calls),1)

    async def test_legacy_cooldown_remains_when_delegation_off(self):
        self.config.proactive.delegate_interaction_policy=False
        await self.store.record_interaction('10001','hello',self.now.timestamp()-60,12)
        await self.store.add_opportunity({'id':'life','framework_id':'f','topic':'a','motive':'a','weight':1,'privacy':'normal','expires_at':self.now.timestamp()+3600})
        await self.engine.patrol(self.now,await self.store.get_state())
        self.assertEqual(self.calls,[])

    async def test_quiet_window_is_not_bypassed(self):
        self.assertFalse(await self.engine.patrol(self.now.replace(hour=3),await self.store.get_state()))
        self.assertEqual(self.calls,[])
