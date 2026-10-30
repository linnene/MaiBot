"""Offline evaluator checks; fixtures are invented and no provider is contacted."""
import pytest
from scripts.persona_effect_suite import evaluate, load_cases
from pathlib import Path
import os
import subprocess
import sys

CASES=Path(__file__).resolve().parents[2]/'docs/evaluation/persona_effect_cases.json'

def test_corpus_has_temporal_ownership_voice_and_proactive_coverage():
    cases=load_cases(CASES)
    assert len(cases)>=16
    assert {'ownership','time','voice','proactive','long_context','closure','external'} <= {t for c in cases for t in c['tags']}

def test_missing_results_are_unmeasured_not_successful():
    cases=load_cases(CASES)
    result=evaluate(cases,[],['current'])
    assert result['complete'] is False
    assert len(result['missing'])==len(cases)
    assert result['naturalness_score'] is None
    assert result['evaluated']==0

def test_protocol_leaks_and_casual_length_are_separate_flags():
    case=dict(id='x',max_chars=4,rubric=['人工判断归属'])
    result=evaluate([case],[dict(case_id='x',variant='current',text='<self_message>长长长长长</self_message>')],['current'])
    assert set(result['records'][0]['flags'])=={'internal_markup','casual_length'}
    assert result['naturalness_score'] is None

def test_detailed_answer_exempt_and_source_link_does_not_consume_casual_budget():
    cases=[dict(id='detail',max_chars=None,rubric=[]),dict(id='link',max_chars=4,rubric=[])]
    rows=[dict(case_id='detail',variant='current',text='解释'*100),dict(case_id='link',variant='current',text='看看 https://example.com/long/path')]
    result=evaluate(cases,rows,['current'])
    assert result['complete'] and all(not x['flags'] for x in result['records'])

def test_duplicates_unknown_cases_and_empty_replies_are_not_silently_accepted():
    cases=[dict(id='x',max_chars=24,rubric=[])]
    row=dict(case_id='x',variant='current',text='好呀')
    with pytest.raises(ValueError,match='duplicate'):evaluate(cases,[row,row],['current'])
    with pytest.raises(ValueError,match='unknown'):evaluate(cases,[dict(row,case_id='other')],['current'])
    result=evaluate(cases,[dict(row,text='')],['current'])
    assert 'empty_reply' in result['records'][0]['flags']

def test_missing_variant_and_unrelated_style_metrics_do_not_claim_human_likeness():
    cases=[dict(id='x',max_chars=24,rubric=[])]
    result=evaluate(cases,[dict(case_id='x',variant='a',text='嗯，晚点聊')],['a','b'])
    assert not result['complete']
    assert result['missing']==[{'case_id':'x','variant':'b'}]
    assert result['records'][0]['semantic_pass'] is None

def test_request_builder_does_not_inject_held_out_scoring_rubric():
    root=CASES.parents[2]
    code="""from scripts.persona_effect_suite import build_requests
case=dict(id='heldout',tags=['ownership'],current_time='2030-05-15T16:00:00+08:00',user='你好',history=[],rubric=['HELDOUT_SCORING_SECRET'],max_chars=24)
rows=build_requests([case],['active','compiler_candidate'])
import json
assert len(rows)==2
assert 'HELDOUT_SCORING_SECRET' not in json.dumps(rows)
assert all(any('你好' in item['content'] for item in row['messages']) for row in rows)
"""
    result=subprocess.run([sys.executable,'-c',code],cwd=root,env={**os.environ,'MAIBOT_INITIALIZE_ONLY':'1'},capture_output=True,text=True)
    assert result.returncode==0,result.stderr[-2000:]


def test_deliberate_proactive_silence_is_not_an_empty_reply_failure():
    case=dict(id='silent',max_chars=24,rubric=[],allow_silence=True)
    result=evaluate([case],[dict(case_id='silent',variant='active',text='')],['active'])
    assert result['complete'] and result['records'][0]['flags']==[]
    assert result['naturalness_score'] is None
