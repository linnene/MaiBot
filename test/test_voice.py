import asyncio
import copy
import importlib.util
import json
import logging
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('voice_under_test',ROOT/'plugin.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def item(text,kind='UserMessageItem'):
 return {'item_type':kind,'meta':{'item_id':'original','logical_turn_id':None,'timestamp':'2026-10-29T10:00:00'},'parts':[{'type':'text','text':text}]}

class VoiceTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.temp=tempfile.TemporaryDirectory();self.calls=0
  index={'model_name':'test-embedding','dimension':2,'probe_text':'probe','probe_vector':[1.,0.], 'entries':[{'id':'a','situation':'打招呼','input':'在吗','output':'在呢','vector':[1.,0.]},{'id':'b','situation':'难过','input':'好烦','output':'抱抱','vector':[0.,1.]}]}
  self.index=Path(self.temp.name)/'index.json';self.index.write_text(json.dumps(index))
  async def embed(text,**kwargs):self.calls+=1;return {'success':True,'model_name':'test-embedding','embedding':[1.,0.]}
  self.p=m.create_plugin();self.p.set_plugin_config({'plugin':{'enabled':True,'config_version':'1.0.0'},'voice':{'mode':'active','allowed_sessions':['allowed'],'index_path':str(self.index)}})
  self.p._ctx=SimpleNamespace(paths=SimpleNamespace(data_dir=self.temp.name),llm=SimpleNamespace(embed=embed),logger=logging.getLogger('voice-test'))
  await self.p.on_load()
  self.payload={'session_id':'allowed','items':[item('identity','SystemMessageItem'),item('目标\n- 发言内容：在吗\n原始发言时间：2026-10-29T10:00:00')],'reply_tool_args':{'reply_reference':'打招呼','reply_boundary':'brief'},'reply_message_id':'m1'}
 async def asyncTearDown(self):self.temp.cleanup()
 async def test_inject_one_data_item_preserves_original(self):
  original=copy.deepcopy(self.payload)
  r=await self.p.on_request(**self.payload)
  items=r['kwargs']['items'];self.assertEqual(len(items),3);self.assertEqual(items[-1],original['items'][-1]);self.assertEqual(items[0],original['items'][0]);self.assertEqual(self.payload,original)
  self.assertEqual(items[-2]['item_type'],'UserMessageItem');self.assertIn('不是聊天记录',items[-2]['parts'][0]['text'])
 async def test_unallowed_no_embedding(self):
  self.payload['session_id']='group'
  self.assertEqual(await self.p.on_request(**self.payload),{'action':'continue'});self.assertEqual(self.calls,0)
 async def test_explain_and_proactive_and_missing_anchor_no_embedding(self):
  for args in [{'reply_boundary':'explain'},{'_host_proactive_task':{}}, {'reply_boundary':'clarify'}]:
   self.payload['reply_tool_args']=args
   self.assertEqual(await self.p.on_request(**self.payload),{'action':'continue'})
  self.assertEqual(self.calls,0)
 async def test_repeat_suppressed_and_cache_used(self):
  await self.p.on_request(**self.payload)
  self.payload['reply_message_id']='m2'
  self.assertEqual(await self.p.on_request(**self.payload),{'action':'continue'})
  self.assertEqual(self.calls,2) # public profile probe + one cached query
 async def test_shadow_does_not_modify_items(self):
  self.p.config.voice.mode='shadow'
  self.assertEqual(await self.p.on_request(**self.payload),{'action':'continue'})
 async def test_low_similarity_no_injection(self):
  self.p._profile_verified=True
  async def embed(text,**kwargs):return {'success':True,'model_name':'test-embedding','embedding':[-1.,0.]}
  self.p.ctx.llm.embed=embed
  self.assertEqual(await self.p.on_request(**self.payload),{'action':'continue'})
 async def test_wrong_model_and_dimension_no_injection(self):
  for response in [{'success':True,'model_name':'changed','embedding':[1.,0.]},{'success':True,'model_name':'test-embedding','embedding':[1.,0.,0.]}]:
   self.p._profile_verified=False
   async def embed(text,**kwargs):return response
   self.p.ctx.llm.embed=embed
   self.assertEqual(await self.p.on_request(**self.payload),{'action':'continue'})
 async def test_embedding_timeout_is_nonblocking(self):
  async def embed(text,**kwargs):raise asyncio.TimeoutError()
  self.p.ctx.llm.embed=embed
  self.assertEqual(await self.p.on_request(**self.payload),{'action':'continue'})
 async def test_no_target_no_embedding(self):
  self.payload['items']=[item('no target')]
  self.assertEqual(await self.p.on_request(**self.payload),{'action':'continue'});self.assertEqual(self.calls,0)
 async def test_duplicate_hook_idempotent(self):
  first=await self.p.on_request(**self.payload)
  self.payload['items']=first['kwargs']['items']
  self.assertEqual(await self.p.on_request(**self.payload),{'action':'continue'})

 async def test_future_item_schema_is_ignored(self):
  self.payload['item_schema_version']=999
  self.assertEqual(await self.p.on_request(**self.payload),{'action':'continue'});self.assertEqual(self.calls,0)
 async def test_concurrent_hook_does_not_duplicate_example(self):
  original=self.p.ctx.llm.embed
  async def delayed(text,**kwargs):
   await asyncio.sleep(.01)
   return await original(text,**kwargs)
  self.p.ctx.llm.embed=delayed
  results=await asyncio.gather(self.p.on_request(**self.payload),self.p.on_request(**self.payload))
  self.assertEqual(sum('kwargs' in r for r in results),1)

 async def test_required_sdk_lifecycle_is_implemented(self):
  from maibot_sdk import MaiBotPlugin
  for name in ('on_load','on_unload','on_config_update'):
   self.assertIsNot(getattr(type(self.p),name),getattr(MaiBotPlugin,name))
 async def test_unload_clears_state_and_stops_new_hooks(self):
  await self.p.on_request(**self.payload)
  await self.p.on_unload()
  self.assertFalse(self.p._query_cache);self.assertFalse(self.p._recent)
  self.assertEqual(await self.p.on_request(**self.payload),{'action':'continue'})

if __name__=='__main__':unittest.main()
