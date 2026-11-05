"""Public services are offline unless a test explicitly opts in."""
import json
import fakeredis
import httpx
import openai
import pytest
from qa import quota


@pytest.fixture(autouse=True)
def public_ai_off_by_default(monkeypatch):
    monkeypatch.setenv('PUBLIC_AI_ENABLED', 'false')


@pytest.fixture
def public_backend(monkeypatch):
    """Actual quota Lua + real OpenAI SDK using mocked HTTP; no external traffic."""
    redis = fakeredis.FakeRedis(decode_responses=True)
    calls = []
    failure = {'provider': False, 'quota': False}
    settings = {
        'PUBLIC_AI_ENABLED': 'true', 'OPENAI_API_KEY': 'server-test-key-not-real',
        'PUBLIC_AI_MODEL': 'gpt-4o-mini', 'PUBLIC_AI_REDIS_URL': 'https://quota.invalid',
        'PUBLIC_AI_REDIS_TOKEN': 'redis-test-token-not-real', 'PUBLIC_AI_QUOTA_NAMESPACE': 'test-public',
        'PUBLIC_AI_SESSION_CALL_LIMIT': '10', 'PUBLIC_AI_DAILY_CALL_LIMIT': '100',
        'PUBLIC_AI_TOTAL_CALL_LIMIT': '500', 'PUBLIC_AI_MINUTE_CALL_LIMIT': '100',
        'PUBLIC_AI_MIN_INTERVAL_SECONDS': '0',
    }
    for key, value in settings.items():
        monkeypatch.setenv(key, value)

    def redis_post(url, *, headers, json, timeout, follow_redirects):
        assert url == 'https://quota.invalid' and headers['Authorization'] == 'Bearer redis-test-token-not-real'
        assert follow_redirects is False
        if failure['quota']:
            raise httpx.ConnectError('SECRET_REDIS_ERROR')
        result = redis.execute_command(*json)
        return httpx.Response(200, json={'result': result}, request=httpx.Request('POST', url))
    monkeypatch.setattr(quota.httpx, 'post', redis_post)
    quota.RedisQuotaStore(settings['PUBLIC_AI_REDIS_URL'], settings['PUBLIC_AI_REDIS_TOKEN'], 'test-public').initialize()
    original = openai.OpenAI

    def model_handler(request):
        assert request.headers['Authorization'] == 'Bearer server-test-key-not-real'
        data = json.loads(request.content)
        calls.append(data)
        if failure['provider']:
            return httpx.Response(500, json={'error': {'message': 'SECRET_PROVIDER_ERROR', 'type': 'server_error'}})
        schema = data['response_format']['json_schema']['name']
        if schema == 'Question':
            payload=json.loads(data['messages'][1]['content'])
            anchor=payload.get('latest_answer_claim','')
            content = {'question': (f'답변의 “{anchor}”를 지지하는 근거는 무엇인가요?' if anchor else '자료에서 이 주장을 뒷받침하는 근거는 무엇인가요?'), 'focus': '근거',
                       'connection': '직전 답변의 근거 확인', 'basis': '근거 부족', 'citations': []}
        elif schema == 'Feedback':
            content = {'accuracy': '자료로 확인한 범위에서 검토 필요', 'logic': '주장과 근거를 연결해 주세요.',
                       'evidence': '인용 위치를 구체화해 주세요.', 'next_step': '적용 조건을 확인해 보세요.'}
        else:
            raise AssertionError('Unexpected public schema')
        return httpx.Response(200, json={'id': 'mock-response', 'object': 'chat.completion', 'created': 1,
            'model': data['model'], 'choices': [{'index': 0, 'finish_reason': 'stop',
                'message': {'role': 'assistant', 'content': json.dumps(content, ensure_ascii=False), 'refusal': None}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 100, 'total_tokens': 200}})

    def create_client(**kwargs):
        assert kwargs['max_retries'] == 0
        return original(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(model_handler)))
    monkeypatch.setattr(openai, 'OpenAI', create_client)
    return {'redis': redis, 'calls': calls, 'failure': failure, 'env': settings}
