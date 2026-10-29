from types import SimpleNamespace
from unittest.mock import AsyncMock
import json
import time

import pytest

from src.config.config import global_config
from src.config.official_configs import InteractionMemoryConfig, RelationshipIdentityConfig, RelationshipIdentityBindingConfig
from src.plugin_runtime.capabilities.core import RuntimeCoreCapabilityMixin
from src.services import interaction_memory as service_module
from src.services.interaction_memory import InteractionMemoryService, InteractionStore


@pytest.fixture
def environment(tmp_path,monkeypatch):
    from src.chat.heart_flow.heartflow_manager import heartflow_manager
    from src.chat.message_receive.chat_manager import chat_manager
    cfg=InteractionMemoryConfig(mode='active')
    monkeypatch.setattr(global_config,'interaction_memory',cfg)
    monkeypatch.setattr(global_config,'relationship_identity',RelationshipIdentityConfig(enabled=True,bindings=[RelationshipIdentityBindingConfig(platform='qq',user_id='10001',relationship='family')]))
    session=SimpleNamespace(session_id='session',is_group_session=False,platform='qq',user_id='10001')
    runtime=SimpleNamespace(_chat_history=[],enqueue_proactive_task=AsyncMock(return_value={'task_id':'proactive:synthetic','queued':True}))
    monkeypatch.setattr(chat_manager,'get_existing_session_by_session_id',lambda _:session)
    monkeypatch.setattr(heartflow_manager,'get_or_create_heartflow_chat',AsyncMock(return_value=runtime))
    service=InteractionMemoryService(InteractionStore(tmp_path/'state.db'))
    monkeypatch.setattr(service_module,'interaction_memory',service)
    service.store.observe('session','m1','hello',time.time()-4*3600)
    return cfg,runtime,service


async def trigger(environment,delegated=True):
    return await RuntimeCoreCapabilityMixin()._cap_maisaka_proactive_trigger('maibot-community.mai-life','',{
        'stream_id':'session','intent':'mai_life_proactive','reason':json.dumps({'topic':'synthetic life','candidate_kind':'life','privacy':'owner_only','instruction':'preserve privacy'}),
        'metadata':{'interaction_delegate':delegated},
    })


@pytest.mark.asyncio
async def test_capability_binds_reservation_and_preserves_privacy_before_delivery(environment):
    cfg,runtime,service=environment
    result=await trigger(environment)
    assert result['success'] and result['task_id']=='proactive:synthetic'
    reason=json.loads(runtime.enqueue_proactive_task.call_args.kwargs['reason'])
    assert reason['instruction']=='preserve privacy' and reason['privacy']=='owner_only'
    assert service.store.snapshot('session')['conversation']['unanswered']==0
    assert service.store.overview()['decisions'][0]['task_id']=='proactive:synthetic'


@pytest.mark.asyncio
async def test_busy_user_does_not_enqueue_a_task(environment):
    cfg,runtime,service=environment
    service.store.observe('session','m2','我正在开会',time.time()-60)
    result=await trigger(environment)
    assert result['success'] and result['accepted'] is False
    runtime.enqueue_proactive_task.assert_not_called()


@pytest.mark.asyncio
async def test_shadow_keeps_original_reason_and_does_not_reserve_new_task(environment):
    cfg,runtime,service=environment;cfg.mode='shadow'
    result=await trigger(environment,delegated=False)
    assert result['success']
    reason=json.loads(runtime.enqueue_proactive_task.call_args.kwargs['reason'])
    assert 'interaction_decision' not in reason
    assert all(row['status']!='queued' for row in service.store.overview()['decisions'])
