import test_plugin
import unittest
from unittest.mock import AsyncMock
from types import SimpleNamespace

class SplitDeliveryTests(test_plugin.PluginTests):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.p.config.sharing.separate_messages=True
    async def test_impression_image_link_have_independent_receipts_and_no_duplicate_tail(self):
        _, message = await self._queue_review_case(require_image=True)
        self.p._ctx.send=SimpleNamespace(image=AsyncMock(return_value={'sent':True,'message_id':'image-receipt'}),text=AsyncMock(return_value={'sent':True,'message_id':'link-receipt'}))
        tagged=(await self.p.tag_send(message=message,reply_message_id='review'))['modified_kwargs']['message']
        self.assertNotIn(self.item['url'],tagged['processed_plain_text'])
        self.assertEqual([part['type'] for part in tagged['raw_message']],['text'])
        tagged['platform_message_id']='impression-receipt'
        await self.p.after_send(message=tagged,sent=True)
        await self.p._delivery_task
        self.p.ctx.send.image.assert_awaited_once()
        self.p.ctx.send.text.assert_awaited_once()
        self.assertEqual(self.p.ctx.send.text.call_args.args[0],self.item['url'])
        self.assertEqual((await self.p.store.status())['shared'],1)
        await self.p.after_send(message=tagged,sent=True)
        self.p.ctx.send.text.assert_awaited_once()

    async def test_ambiguous_image_stops_link_and_does_not_mark_shared(self):
        _, message=await self._queue_review_case(require_image=True)
        self.p._ctx.send=SimpleNamespace(image=AsyncMock(return_value={'sent':True,'message_id':None}),text=AsyncMock())
        tagged=(await self.p.tag_send(message=message,reply_message_id='review'))['modified_kwargs']['message']
        tagged['platform_message_id']='impression-receipt'
        await self.p.after_send(message=tagged,sent=True)
        await self.p._delivery_task
        self.p.ctx.send.text.assert_not_awaited()
        self.assertEqual((await self.p.store.status())['shared'],0)

    async def test_only_lightweight_before_request_hook_tracks_tasks(self):
        _, message=await self._queue_review_case()
        # Large multimodal model-request items should not be transported to this plugin.
        self.assertFalse(any("before_model_request" in repr(v) for v in self.p.track_request.__dict__.values()))
        result=await self.p.share_brief(session_id='real-session',reply_message_id='review',reply_tool_args={'_host_proactive_task':{'task_id':'proactive:test'}})
        self.assertIn('modified_kwargs',result)

    async def test_revocation_between_impression_and_picture_cancels_tail(self):
        _, message=await self._queue_review_case(require_image=True)
        self.p._ctx.send=SimpleNamespace(image=AsyncMock(),text=AsyncMock())
        tagged=(await self.p.tag_send(message=message,reply_message_id='review'))['modified_kwargs']['message']
        tagged['platform_message_id']='impression-receipt'
        self.p.config.sharing.allowed_private_sessions=[]
        await self.p.after_send(message=tagged,sent=True)
        await self.p._delivery_task
        self.p.ctx.send.image.assert_not_awaited()
        self.assertEqual((await self.p.store.status())['shared'],0)

    async def test_link_only_final_segment_becomes_picture_then_link(self):
        _, message=await self._queue_review_case(require_image=True)
        self.p._ctx.send=SimpleNamespace(image=AsyncMock(),text=AsyncMock(return_value={'sent':True,'message_id':'link-receipt'}))
        message['processed_plain_text']=self.item['url']
        tagged=(await self.p.tag_send(message=message,reply_message_id='review'))['modified_kwargs']['message']
        self.assertEqual([p['type'] for p in tagged['raw_message']],['image'])
        tagged['platform_message_id']='image-receipt'
        await self.p.after_send(message=tagged,sent=True)
        await self.p._delivery_task
        self.p.ctx.send.image.assert_not_awaited()
        self.p.ctx.send.text.assert_awaited_once()

    async def test_delivery_claim_is_persistent_and_atomic(self):
        import asyncio
        from entertainment.core import Store
        a,b=await asyncio.gather(self.p.store.claim_delivery('t','first'),self.p.store.claim_delivery('t','first'))
        self.assertEqual([a,b].count(True),1)
        restarted=Store(self.p.store.path)
        await restarted.initialize()
        self.assertFalse(await restarted.claim_delivery('t','first'))

    async def test_automatic_browsing_requests_interest_search(self):
        self.p.config.plugin.enabled=True
        self.p.config.reading.auto_browse=True
        self.p.ctx.api.call=AsyncMock(return_value={'can_browse':True,'activity_kind':'leisure'})
        self.p.reader.browse=AsyncMock(return_value={'success':True,'share_candidates':[]})
        await self.p.browse()
        self.assertTrue(self.p.reader.browse.call_args.kwargs['prefer_search'])


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(SplitDeliveryTests(name) for name in SplitDeliveryTests.__dict__ if name.startswith("test_"))
