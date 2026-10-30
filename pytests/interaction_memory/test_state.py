from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.config.official_configs import InteractionMemoryConfig
from src.services.interaction_memory import InteractionStore, parse_time

NOW = datetime(2030,1,2,12,tzinfo=timezone.utc).timestamp()


@pytest.fixture
def store(tmp_path):
    value=InteractionStore(tmp_path/'state.db')
    value.observe('session','m1','我明天考试',NOW-7200)
    return value


def operation(**extra):
    return dict(action='add',subject='user',title='考试',kind='follow_up',source_message_id='m1',quote='我明天考试',
                event_at=datetime.fromtimestamp(NOW-3600,timezone.utc).isoformat(),
                due_at=datetime.fromtimestamp(NOW-60,timezone.utc).isoformat(),**extra)


def add_due(store):
    assert store.apply('session',1,[operation()],NOW-120)


def decide(store, **kwargs):
    cfg=InteractionMemoryConfig()
    for key in list(kwargs):
        if hasattr(cfg,key):setattr(cfg,key,kwargs.pop(key))
    return store.decide('session',{'kind':'life','topic':'我正在图书馆','daily_cap':7},cfg,kwargs.get('now',NOW),reserve=kwargs.get('reserve',True))


def test_due_follow_up_precedes_own_life(store):
    add_due(store)
    decision=decide(store)
    assert decision['eligible'] and decision['kind']=='follow_up'
    assert decision['source_quote']=='我明天考试'


def test_no_duplicate_delivery_quota(store):
    add_due(store);first=decide(store)
    assert decide(store)['reason']=='pending_delivery'
    store.bind_task(first['id'],'task1');store.delivered('session','task1',NOW)
    store.delivered('session','task1',NOW+1)
    assert store.snapshot('session')['conversation']['unanswered']==1
    assert store.snapshot('session')['items'][0]['status']=='asked'


def test_shadow_has_no_delivery_or_item_mutation(store):
    add_due(store)
    d=decide(store,reserve=False)
    assert d['eligible']
    assert store.snapshot('session')['items'][0]['status']=='open'
    assert all(r['status']=='shadow' for r in store.overview()['decisions'])
    assert store.snapshot('session')['conversation']['last_proactive_at']==0


def test_concurrent_candidate_reservation_only_accepts_one(store):
    add_due(store)
    with ThreadPoolExecutor(max_workers=4) as pool:
        decisions=list(pool.map(lambda _:decide(store),range(4)))
    assert sum(d['eligible'] for d in decisions)==1


@pytest.mark.parametrize('text,reason',[('先不聊了','user_closed'),('我正在开会','user_busy')])
def test_user_explicit_pause_blocks_proactive(store,text,reason):
    store.observe('session','m2',text,NOW-3600)
    assert decide(store)['reason']==reason


def test_ordinary_question_about_bot_not_treated_as_user_busy(store):
    store.observe('session','m2','你在上课吗',NOW-3600)
    assert store.snapshot('session')['conversation']['state']=='open'


def test_user_reply_resets_unanswered(store):
    first=decide(store);store.bind_task(first['id'],'t1');store.delivered('session','t1',NOW)
    store.observe('session','m2','嗯嗯',NOW+1)
    assert store.snapshot('session')['conversation']['unanswered']==0


def test_no_response_backoff(store):
    store.run(lambda c:c.execute("UPDATE conversations SET unanswered=2"))
    assert decide(store)['reason']=='unanswered_backoff'


def test_life_sharing_is_not_daily_quota_to_fill(store):
    first=decide(store);store.bind_task(first['id'],'t1');store.delivered('session','t1',NOW)
    assert decide(store,now=NOW+3*3600)['reason']=='life_share_limit'


def test_actual_delivery_only_not_lease_consumes_quota(store):
    first=decide(store)
    assert store.snapshot('session')['conversation']['last_proactive_at']==0
    assert decide(store,now=NOW+181)['eligible']


def test_invalid_evidence_rolls_back_all_operations(store):
    bad=operation();bad['quote']='她明天上课'
    with pytest.raises(ValueError):store.apply('session',1,[operation(),bad],NOW)
    assert store.snapshot('session')['items']==[]


def test_bot_schedule_cannot_create_user_item(store):
    bad=operation();bad['subject']='bot'
    with pytest.raises(ValueError):store.apply('session',1,[bad],NOW)
    assert not store.snapshot('session')['items']


def test_uncertain_time_does_not_trigger_reminder(store):
    op=operation();op.update(event_at=None,due_at=None)
    store.apply('session',1,[op],NOW)
    assert store.snapshot('session')['items'][0]['status']=='uncertain'
    assert decide(store)['kind']!='follow_up'


def test_new_input_prevents_stale_model_completion(store):
    store.observe('session','m2','考试取消了',NOW)
    assert not store.apply('session',1,[operation()],NOW)
    assert not store.snapshot('session')['items']


def test_duplicate_and_delayed_messages_do_not_reopen_pause(store):
    assert not store.observe('session','m1','我明天考试',NOW)
    store.observe('session','m2','先不聊了',NOW)
    store.observe('session','old','早呀',NOW-10)
    assert store.snapshot('session')['conversation']['state']=='closed'


def test_cancel_and_postpone_are_user_correctable(store):
    add_due(store);item=store.snapshot('session')['items'][0]
    store.edit(item['id'],'cancelled',None,NOW)
    assert store.snapshot('session')['items'][0]['status']=='cancelled'
    assert decide(store)['kind']!='follow_up'


@pytest.mark.parametrize('value',['明天','2030-01-02T12:00:00','2032-01-01T12:00:00+00:00',123])
def test_ambiguous_or_unbounded_times_are_rejected(value):
    with pytest.raises((ValueError,TypeError)):parse_time(value,NOW)



def test_known_day_uses_after_day_window_without_fake_event_time(store):
    op=operation();op.update(event_at=None,due_at=None,event_date='2030-01-03')
    store.apply('session',1,[op],NOW)
    item=store.snapshot('session')['items'][0]
    assert item['event_at']==0 and item['event_day']=='2030-01-03'
    assert datetime.fromtimestamp(item['due_at'],timezone.utc).day==4
    assert decide(store)['kind']!='follow_up'



def test_relative_date_uses_source_day_not_retrieval_day(store):
    op=operation();op.update(event_at=None,due_at=None,event_date='2030-01-04')
    with pytest.raises(ValueError,match='original user message date'):
        store.apply('session',1,[op],NOW)


def test_postponement_replaces_event_day_and_preserves_source(store):
    op=operation();op.update(event_at=None,due_at=None,event_date='2030-01-03')
    store.apply('session',1,[op],NOW)
    item=store.snapshot('session')['items'][0]
    store.observe('session','m2','考试改到后天',NOW+1)
    store.apply('session',2,[{'action':'postpone','id':item['id'],'source_message_id':'m2','quote':'考试改到后天','event_date':'2030-01-04','due_at':None}],NOW+1)
    updated=store.snapshot('session')['items'][0]
    assert updated['event_day']=='2030-01-04' and updated['source_message_id']=='m2'
    assert updated['event_at']==0


def test_repeated_event_confirmation_does_not_duplicate_followups(store):
    op=operation();op.update(event_at=None,due_at=None,event_date='2030-01-03')
    store.apply('session',1,[op],NOW)
    store.observe('session','m2','我明天考试',NOW+1)
    duplicate=dict(op,source_message_id='m2')
    store.apply('session',2,[duplicate],NOW+1)
    assert len(store.snapshot('session')['items'])==1



def test_existing_strategy_deliveries_are_observed_once(store):
    store.delivered('session','legacy-proactive',NOW)
    store.delivered('session','legacy-proactive',NOW+1)
    assert store.snapshot('session')['conversation']['unanswered']==1
    assert len([row for row in store.overview()['decisions'] if row['status']=='sent'])==1


def expanded_frequency_config():
    return InteractionMemoryConfig(daily_target_min=3,daily_target_max=5,daily_hard_max=8,
                                   life_daily_max=2,min_interval_minutes=90,max_unanswered=2)


def test_expanded_frequency_retains_unanswered_and_interval_gates(store):
    cfg=expanded_frequency_config();candidate={'kind':'life','topic':'合成话题','daily_cap':8}
    store.run(lambda db:db.execute('UPDATE conversations SET unanswered=2'))
    assert store.decide('session',candidate,cfg,NOW,reserve=False)['reason']=='unanswered_backoff'
    store.run(lambda db:db.execute('UPDATE conversations SET unanswered=0,last_proactive_at=?,last_bot_at=?',(NOW-80*60,NOW-80*60)))
    assert store.decide('session',candidate,cfg,NOW,reserve=False)['reason']=='adaptive_cooldown'


def test_expanded_life_allowance_is_still_a_cap_not_a_send_requirement(store):
    cfg=expanded_frequency_config()
    for index in range(2):
        now=NOW+index*100*60
        result=store.decide('session',{'kind':'life','topic':'不同的生活话题'+str(index),'daily_cap':8},cfg,now,reserve=True)
        assert result['eligible']
        task='synthetic-expanded-'+str(index)
        store.bind_task(result['id'],task);store.delivered('session',task,now)
        store.observe('session','ack-'+str(index),'嗯嗯',now+1)
    result=store.decide('session',{'kind':'life','topic':'第三个生活话题','daily_cap':8},cfg,NOW+200*60,reserve=False)
    assert result['reason']=='life_share_limit'

def test_personal_intent_uses_existing_delivery_budget(store):
    cfg = InteractionMemoryConfig()
    candidate = {"kind": "personal_intent", "topic": "想聊摄影", "daily_cap": 8}
    first = store.decide("session", candidate, cfg, NOW, reserve=True)
    assert first["eligible"]
    assert store.decide("session", candidate, cfg, NOW, reserve=True)["reason"] == "pending_delivery"
    store.bind_task(first["id"], "reminder-task")
    store.delivered("session", "reminder-task", NOW)
    assert store.decide("session", candidate, cfg, NOW + 60, reserve=True)["reason"] == "adaptive_cooldown"


def test_recent_afterthought_is_bounded_and_does_not_ignore_pause(store):
    cfg = InteractionMemoryConfig()
    candidate = {
        "kind": "personal_intent",
        "intent_kind": "afterthought",
        "intent_created_at": NOW - 180,
        "topic": "刚想起来一件事",
    }
    store.observe("session", "new", "刚回来了", NOW - 180)
    store.run(lambda db: db.execute("UPDATE conversations SET last_bot_at=?", (NOW - 150,)))
    assert store.decide("session", candidate, cfg, NOW, reserve=False)["eligible"]
    assert store.decide("session", candidate, cfg, NOW - 100, reserve=False)["reason"] == "recent_conversation"
    store.observe("session", "close", "先不聊了", NOW - 140)
    assert store.decide("session", candidate, cfg, NOW, reserve=False)["reason"] == "user_closed"


def test_afterthought_cannot_bypass_unanswered_or_old_intent(store):
    cfg = InteractionMemoryConfig()
    candidate = {
        "kind": "personal_intent",
        "intent_kind": "afterthought",
        "intent_created_at": NOW - 180,
        "topic": "补一句",
    }
    store.run(lambda db: db.execute("UPDATE conversations SET unanswered=1,last_bot_at=?", (NOW - 150,)))
    assert store.decide("session", candidate, cfg, NOW, reserve=False)["reason"] == "adaptive_cooldown"
    store.run(lambda db: db.execute("UPDATE conversations SET unanswered=0,last_user_at=?", (NOW - 180,)))
    candidate["intent_created_at"] = NOW - 3600
    assert store.decide("session", candidate, cfg, NOW, reserve=False)["reason"] == "recent_conversation"
