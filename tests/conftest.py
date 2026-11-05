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


@pytest.fixture
def gemini_backend(monkeypatch):
    """Real google-genai SDK over mock HTTP. No real Google key/request."""
    from google import genai
    from google.genai import types
    from qa import gemini_provider as gp, gemini_access as access
    state={'requests':[], 'error':None, 'missing_model':False,'review':'supported'}
    original=genai.Client
    original_connection=access.Connection
    monkeypatch.setattr(access,'Connection',lambda:original_connection(meter=gp.Meter(interval=0)))
    def handler(request):
        key=request.headers.get('x-goog-api-key')
        assert key and key not in ('OWNER_KEY','SERVER_KEY')
        assert 'key=' not in str(request.url)
        if key=='invalid-key':
            return httpx.Response(400,json={'error':{'code':400,'message':'API_KEY_INVALID secret-details','status':'INVALID_ARGUMENT'}})
        path=request.url.path
        if request.method=='GET':
            if path.endswith('/models'):
                return httpx.Response(200,json={'models':[{'name':'models/gemini-3.6-flash','displayName':'대체 빠른 모델','supportedGenerationMethods':['generateContent']}]})
            if state['missing_model'] and path.endswith(gp.DEFAULT_MODEL):
                return httpx.Response(404,json={'error':{'code':404,'message':'missing','status':'NOT_FOUND'}})
            return httpx.Response(200,json={'name':'models/'+path.split('/')[-1],'supportedGenerationMethods':['generateContent']})
        body=json.loads(request.content)
        parts=body['contents'][0]['parts']
        payload=json.loads(parts[0]['text'])
        schema=body['generationConfig']['responseJsonSchema']['title']
        state['requests'].append({'key':key,'schema':schema,'body':body,'payload':payload})
        if state['error']:
            code=state['error']
            return httpx.Response(code,json={'error':{'code':code,'message':'SECRET_PROVIDER_DETAILS','status':'RESOURCE_EXHAUSTED' if code==429 else 'INTERNAL'}})
        documents=payload.get('documents',[])
        citation=next(({'page_id':p['id'],'quote':p['text'][:100]} for p in documents if p.get('text')),None)
        citations=[citation] if citation else []
        if schema=='AnswerReview':
            answer=payload['answer']
            status=state['review']
            content={'relevance':'off_topic' if status=='off_topic' else 'partial' if status=='partial' else 'direct',
                     'accuracy':'incorrect' if status=='incorrect' else 'uncertain' if status=='partial' else 'supported',
                     'claim':answer,'explanation':'SEI 형성 질문에 dendrite 관찰법만 답해 원래 반응 설명이 빠졌습니다.' if status=='off_topic' else '원문 범위에서 검토했습니다.',
                     'missing':['계면 반응의 적용 조건'] if status=='partial' else [],
                     'next_focus':'적용 조건과 한계' if status=='supported' else 'SEI 형성 반응',
                     'action':'retry' if status in ('off_topic','incorrect') else 'clarify' if status=='partial' else 'deepen',
                     'repair_question':'SEI 형성 반응과 dendrite 관찰을 구분해 원래 질문에 답해 주시겠어요?', 'citations':citations}
        elif schema=='Question':
            anchor=payload.get('latest_answer_claim','')
            focus=(payload.get('answer_review') or {}).get('next_focus','SEI 형성 반응')
            visual=any('inlineData' in p for p in parts)
            if visual:
                label=next(p['text'] for p in parts[1:] if 'text' in p)
                page_id=label.split('page_id=')[1]
                citations=[{'page_id':page_id,'quote':'','kind':'visual','detail':'곡선의 축 단위가 작아 해석에 불확실성이 있습니다.'}]
            content={'question':'고전압 조건에서 이 결론이 유지되지 않는 예외는 무엇인가요?' if anchor else 'SEI 형성에서 전해액 환원과 비가역 용량 손실은 어떻게 연결되나요?',
                     'focus':focus,'connection':anchor or '발표와 참고 문헌의 반응 설명 연결', 'basis':'모델 추론','citations':citations}
        elif schema=='Quality':
            from qa.models import METRICS
            content={'grades':[{'metric':m,'score':None,'reason':'모의 평가'} for m in METRICS], 'problems':[],'suggestion':'검토'}
        elif schema=='Comparison':
            content={'matches':[{'reference_index':i,'generated_indices':[],'status':'검토 필요','concept':'SEI','reason':'모의 비교'} for i,_ in enumerate(payload['references'])],'limitations':'모의 결과'}
        else:
            raise AssertionError(schema)
        return httpx.Response(200,json={'candidates':[{'content':{'role':'model','parts':[{'text':json.dumps(content,ensure_ascii=False)}]},'finishReason':'STOP'}],
                                       'usageMetadata':{'promptTokenCount':100,'candidatesTokenCount':100,'totalTokenCount':200}})
    def factory(**kwargs):
        # Exercise the production client factory, preserving its explicit key,
        # vertexai=False, timeout and retry policy; replace only HTTP transport.
        assert kwargs['vertexai'] is False and kwargs['api_key']
        assert kwargs['http_options'].retry_options.attempts == 1
        kwargs['http_options'].httpx_client=httpx.Client(transport=httpx.MockTransport(handler))
        return original(**kwargs)
    monkeypatch.setattr(genai,'Client',factory)
    return state
