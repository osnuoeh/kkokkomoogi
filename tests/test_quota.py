from concurrent.futures import ThreadPoolExecutor
import json
import httpx
import pytest
from qa import quota, public_ai, engine
from qa.llm_policy import policy
from qa.documents import examples
from qa.models import Question


def store():
    return quota.RedisQuotaStore('https://quota.invalid', 'redis-test-token-not-real', 'test-public')


def limits(**kwargs):
    defaults = dict(session_calls=10, daily_calls=100, total_calls=500,
                    daily_units=8_000_000, total_units=40_000_000, minute_calls=100, min_interval=0)
    return quota.Limits(**(defaults | kwargs))


def test_atomic_global_cap_with_concurrent_users(public_backend):
    def reserve(i):
        try:
            store().reserve(str(i), 1000, limits(total_calls=7))
            return True
        except quota.QuotaExceeded as error:
            assert error.reason == 'total'
            return False
    with ThreadPoolExecutor(max_workers=20) as pool:
        admitted = list(pool.map(reserve, range(50)))
    assert sum(admitted) == 7
    assert public_backend['redis'].hget(store().root, 'calls_total') == '7'
    assert public_backend['redis'].hget(store().root, 'units_total') == '7000'


@pytest.mark.parametrize('values,reason', [({'session_calls':1},'session'), ({'daily_calls':1},'daily'),
    ({'total_calls':1},'total'), ({'daily_units':1000},'daily'), ({'total_units':1000},'total'),
    ({'minute_calls':1},'minute'), ({'min_interval':30},'interval')])
def test_all_limit_types_block_before_second_reservation(public_backend, values, reason):
    store().reserve('session-A', 1000, limits(**values))
    with pytest.raises(quota.QuotaExceeded) as error:
        store().reserve('session-A', 1000, limits(**values))
    assert error.value.reason == reason
    assert public_backend['redis'].hget(store().root, 'calls_total') == '1'


def test_durable_shared_state_survives_new_app_objects_and_day_rollover(public_backend):
    store().reserve('old-session', 1000, limits(total_calls=2))
    # Simulate a new app object and a new accounting day; lifetime usage persists.
    public_backend['redis'].hset(store().root, mapping={'day':'-1', 'calls_day':'999', 'units_day':'99999999'})
    quota.RedisQuotaStore('https://quota.invalid', 'redis-test-token-not-real', 'test-public').reserve('new-session', 1000, limits(total_calls=2))
    assert public_backend['redis'].hget(store().root, 'calls_day') == '1'
    with pytest.raises(quota.QuotaExceeded, match='Usage limit'):
        store().reserve('third-session', 1000, limits(total_calls=2))
    assert public_backend['redis'].ttl(store().root) == -1


def test_missing_or_expiring_ledger_does_not_auto_reinitialize(public_backend):
    public_backend['redis'].delete(store().root)
    with pytest.raises(quota.QuotaUnavailable):
        store().reserve('session',1000,limits())
    assert not public_backend['redis'].exists(store().root)
    assert store().initialize()
    public_backend['redis'].expire(store().root,60)
    with pytest.raises(quota.QuotaUnavailable):
        store().reserve('session',1000,limits())


@pytest.mark.parametrize('field,value', [('calls_total','broken'), ('calls_total','-3'), ('units_total','0.5')])
def test_corrupted_ledger_fails_closed(public_backend, field, value):
    public_backend['redis'].hset(store().root, field, value)
    with pytest.raises(quota.QuotaUnavailable):
        store().reserve('session',1000,limits())


def test_operator_initialization_never_resets_existing_usage(public_backend):
    store().reserve('session',1000,limits())
    assert store().initialize() is False
    assert public_backend['redis'].hget(store().root,'calls_total')=='1'


def test_ledger_contains_only_counters_and_hashed_session_key(public_backend):
    session = 'private-browser-identity'
    store().reserve(session,1000,limits())
    keys=public_backend['redis'].keys('*')
    assert not any(session in key for key in keys)
    values=' '.join(str(public_backend['redis'].hgetall(key)) for key in keys)
    assert 'server-test-key' not in values and '발표' not in values


@pytest.mark.parametrize('url', ['http://quota.invalid','https://user:password@quota.invalid',
    'https://quota.invalid?token=secret','https://quota.invalid/path'])
def test_redis_endpoint_cannot_redirect_credentials_to_plaintext_or_url_parameters(url):
    with pytest.raises(quota.QuotaUnavailable):
        quota.RedisQuotaStore(url,'token','scope')


def test_redis_error_and_malformed_response_are_redacted(monkeypatch):
    def post(*args,**kwargs):
        return httpx.Response(200,json={'error':'SECRET_REDIS_PAYLOAD'},request=httpx.Request('POST','https://quota.invalid'))
    monkeypatch.setattr(quota.httpx,'post',post)
    with pytest.raises(quota.QuotaUnavailable) as error:
        store().reserve('s',1000,limits())
    assert 'SECRET' not in str(error.value)


def test_explicit_opt_in_complete_config_and_reviewed_model_required():
    base={'OPENAI_API_KEY':'key','PUBLIC_AI_REDIS_URL':'https://quota.invalid','PUBLIC_AI_REDIS_TOKEN':'token'}
    assert not public_ai.settings_from(base.get).ready
    assert public_ai.settings_from((base|{'PUBLIC_AI_ENABLED':'true'}).get).ready
    assert not public_ai.settings_from((base|{'PUBLIC_AI_ENABLED':'true','PUBLIC_AI_MODEL':'arbitrary-model'}).get).ready
    assert not public_ai.settings_from((base|{'PUBLIC_AI_ENABLED':'true','PUBLIC_AI_SESSION_CALL_LIMIT':'0'}).get).ready
    assert not public_ai.settings_from((base|{'PUBLIC_AI_ENABLED':'true','PUBLIC_AI_TOTAL_CALL_LIMIT':'nonsense'}).get).ready
    assert not public_ai.settings_from((base|{'PUBLIC_AI_ENABLED':'true','PUBLIC_AI_REDIS_TOKEN':''}).get).ready


def test_server_sdk_policy_limits_output_and_restores_context(public_backend):
    settings=public_ai.read_settings(); session=public_ai.PracticeSession()
    turn=public_ai.generate_question(settings,session,examples(),[],'일반 학생','보통')
    assert turn['mode']=='OpenAI' and turn['model']==settings.model
    call=public_backend['calls'][0]
    assert call['model']==settings.model and call['max_completion_tokens']==1000
    assert session.attempts==1 and policy.get() is None
    assert public_backend['redis'].hget(store().root,'calls_total')=='1'
    assert 'server-test-key' not in repr(settings) and 'redis-test-token' not in repr(settings)


def test_quota_service_failure_never_reaches_provider(public_backend):
    public_backend['failure']['quota']=True
    session=public_ai.PracticeSession()
    with pytest.raises(public_ai.PracticeUnavailable) as error:
        public_ai.generate_question(public_ai.read_settings(),session,examples(),[],'일반 학생','보통')
    assert not public_backend['calls'] and policy.get() is None
    assert 'SECRET' not in str(error.value) and session.pause_message


def test_failed_provider_consumes_reservation_once_and_has_no_retry(public_backend):
    public_backend['failure']['provider']=True
    session=public_ai.PracticeSession(); settings=public_ai.read_settings()
    with pytest.raises(public_ai.PracticeUnavailable):
        public_ai.generate_question(settings,session,examples(),[],'일반 학생','보통')
    assert len(public_backend['calls'])==1 and session.attempts==1
    assert public_backend['redis'].hget(store().root,'calls_total')=='1'
    assert policy.get() is None
    assert public_ai.generate_question(settings,session,examples(),[],'일반 학생','보통')['mode']=='데모'
    assert len(public_backend['calls'])==1


def test_oversized_payload_stops_before_quota_or_model(public_backend, monkeypatch):
    monkeypatch.setenv('PUBLIC_AI_MAX_REQUEST_BYTES','4096')
    pages=examples(); pages[0].text='가상의 긴 문서 '*1000
    with pytest.raises(public_ai.PracticeUnavailable,match='자료나 대화'):
        public_ai.generate_question(public_ai.read_settings(),public_ai.PracticeSession(),pages,[],'일반 학생','보통')
    assert not public_backend['calls']
    assert public_backend['redis'].hget(store().root,'calls_total')=='0'


def test_existing_demo_feedback_does_not_charge_even_when_ai_enabled(public_backend):
    t=engine.generate(examples(),[])
    t['answer']='답변'
    result,mode=public_ai.answer_feedback(public_ai.read_settings(),public_ai.PracticeSession(),examples(),t)
    assert mode=='데모' and '검토 필요' in result['accuracy'] and not public_backend['calls']


def test_two_request_policies_do_not_cross_threads(public_backend):
    # ContextVars stay isolated from unrelated threads; normal developer calls
    # cannot inherit the public policy from a separate user's thread.
    from qa.llm_policy import LLMPolicy
    seen=[]
    def worker(name):
        p=LLMPolicy('private-'+name,'gpt-4o-mini',1000,lambda *a:None)
        token=policy.set(p)
        try:
            seen.append((name,policy.get().api_key))
        finally:
            policy.reset(token)
        return policy.get()
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(worker,['A','B']))==[None,None]
    assert sorted(seen)==[('A','private-A'),('B','private-B')] and policy.get() is None


@pytest.mark.parametrize('missing', ['OPENAI_API_KEY','PUBLIC_AI_REDIS_TOKEN'])
def test_incomplete_configuration_uses_rules_without_provider(public_backend, missing):
    values=public_backend['env'].copy(); values[missing]=''
    settings=public_ai.settings_from(values.get)
    question=public_ai.generate_question(settings,public_ai.PracticeSession(),examples(),[],'일반 학생','보통')
    assert question['mode']=='데모' and not public_backend['calls']
    assert public_backend['redis'].hget(store().root,'calls_total')=='0'


def test_reserved_usage_unit_cap_blocks_actual_sdk_path(public_backend,monkeypatch):
    monkeypatch.setenv('PUBLIC_AI_DAILY_UNIT_LIMIT','1')
    with pytest.raises(public_ai.PracticeUnavailable,match='한도'):
        public_ai.generate_question(public_ai.read_settings(),public_ai.PracticeSession(),examples(),[],'일반 학생','보통')
    assert not public_backend['calls']
    assert public_backend['redis'].hget(store().root,'calls_total')=='0'
