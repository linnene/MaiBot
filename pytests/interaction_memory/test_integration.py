from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.config.config import global_config
from src.config.official_configs import InteractionMemoryConfig, RelationshipIdentityBindingConfig, RelationshipIdentityConfig
from src.services.interaction_memory import InteractionMemoryService, InteractionStore


@pytest.fixture
def service(tmp_path,monkeypatch):
    monkeypatch.setattr(global_config,'interaction_memory',InteractionMemoryConfig(mode='shadow'))
    monkeypatch.setattr(global_config,'relationship_identity',RelationshipIdentityConfig(enabled=True,bindings=[RelationshipIdentityBindingConfig(platform='qq',user_id='10001',relationship='family')]))
    return InteractionMemoryService(InteractionStore(tmp_path/'sidecar.db'))


def session(**changes):
    return SimpleNamespace(**dict(dict(session_id='session',is_group_session=False,platform='qq',user_id='10001'),**changes))


def test_scope_is_verified_private_partner_only(service):
    assert service.enabled_for(session())
    assert not service.enabled_for(session(is_group_session=True))
    assert not service.enabled_for(session(user_id='other'))


@pytest.mark.asyncio
async def test_disabled_service_creates_no_files_or_jobs(service,monkeypatch):
    monkeypatch.setattr(global_config.interaction_memory,'mode','off')
    await service.observe(session(),SimpleNamespace())
    assert not service.store.path.exists() and not service.jobs
    assert await service.context(session())==''


@pytest.mark.asyncio
async def test_delegation_requires_active_host_policy(service):
    result=await service.review(session(),'{}',True)
    assert not result['accepted']
    assert not service.store.overview()['decisions'][0]['status']=='queued'


@pytest.mark.asyncio
async def test_shadow_does_not_replace_existing_life_reason(service):
    reason='{"topic":"synthetic life","candidate_kind":"life"}'
    result=await service.review(session(),reason,False)
    assert result['accepted'] and result['reason']==reason
    assert result['decision_id']==''


@pytest.mark.asyncio
async def test_new_user_observation_uses_actual_actor_not_bot(service):
    message=SimpleNamespace(message_info=SimpleNamespace(user_info=SimpleNamespace(user_id='bot')),processed_plain_text='我明天考试',message_id='m1',timestamp=datetime.now(timezone.utc))
    await service.observe(session(),message)
    assert not service.store.path.exists()


@pytest.mark.asyncio
async def test_unrelated_greeting_does_not_inject_all_items(service,monkeypatch):
    import time
    monkeypatch.setattr(global_config.interaction_memory,'mode','active')
    now=time.time();service.store.observe('session','m1','我下周考试',now-10)
    service.store.apply('session',1,[{'action':'add','subject':'user','title':'下周考试','source_message_id':'m1','quote':'我下周考试','event_at':None,'due_at':None}],now)
    service.store.observe('session','m2','早上好',now)
    assert await service.context(session())==''
    service.store.observe('session','m3','还记得我的考试吗',now+1)
    assert '下周考试' in await service.context(session())


@pytest.mark.asyncio
async def test_model_failure_is_visible_and_does_not_write_items(service,monkeypatch):
    from src.services.llm_service import LLMServiceClient
    import time
    service.store.observe('session','m1','我明天考试',time.time())
    monkeypatch.setattr(LLMServiceClient,'generate_response',AsyncMock(return_value=SimpleNamespace(response='not json')))
    await service._extract('session')
    assert not service.store.snapshot('session')['items']
    assert service.store.overview()['writes'][-1]['status']=='failed'
