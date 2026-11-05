"""Explicit per-request Gemini credentials. Never falls back to ambient keys."""
import json
import time
import threading
from contextvars import ContextVar
from contextlib import contextmanager
from dataclasses import dataclass, field
from google import genai
from google.genai import types

DEFAULT_MODEL = 'gemini-3.8-flash'


class GeminiFailure(Exception):
    def __init__(self, kind, message):
        self.kind = kind
        super().__init__(message)


@dataclass
class Meter:
    calls: int = 0
    last: float = 0
    max_calls: int = 100
    interval: float = 2



@dataclass
class Scope:
    key: str = field(repr=False)
    model: str = DEFAULT_MODEL
    images: tuple = field(default=(), repr=False)
    meter: Meter = field(default_factory=Meter, repr=False)


current = ContextVar('gemini_scope', default=None)
GLOBAL_CONCURRENCY = threading.BoundedSemaphore(4)  # counters only, no credentials


@contextmanager
def use(scope):
    token = current.set(scope)
    try:
        yield
    finally:
        current.reset(token)


def failure(exc):
    code = getattr(exc, 'code', None)
    if code in (401,403) or (code==400 and any(t in str(exc).upper() for t in ('API_KEY','API KEY'))):
        return GeminiFailure('auth', '키가 만료·취소되었거나 접근 권한이 없습니다. 나의 Gemini를 다시 연결해 주세요.')
    if code==429:
        return GeminiFailure('quota', '이 키가 속한 프로젝트의 할당량 또는 요청 한도에 도달했습니다. 무료 등급이라면 할당량 갱신을 기다리고, 유료 등급도 AI Studio에서 제한을 확인해 주세요. 자동 유료 전환은 하지 않습니다.')
    if code==404:
        return GeminiFailure('model', '현재 연결 모델을 사용할 수 없습니다. 연결을 해제하고 사용 가능한 빠른 모델을 확인해 주세요.')
    return GeminiFailure('request', 'Gemini 요청을 완료하지 못했습니다. 기록은 유지됩니다. 잠시 후 다시 시도해 주세요.')


def client(key):
    if not key or not key.strip():
        raise GeminiFailure('missing_key', '먼저 나의 Gemini를 연결해 주세요. 운영자 키로 대신 요청하지 않습니다.')
    return genai.Client(api_key=key.strip(), vertexai=False,
                        http_options=types.HttpOptions(timeout=45000,
                            retry_options=types.HttpRetryOptions(attempts=1)))


def connect(key, model=DEFAULT_MODEL):
    """Metadata check only. No generation/probe charge and no automatic fallback."""
    try:
        with client(key) as c:
            try:
                info = c.models.get(model=model)
                actions = getattr(info, 'supported_actions', None)
                if actions is not None and 'generateContent' not in actions:
                    raise GeminiFailure('model', '이 모델은 질문 생성을 지원하지 않습니다.')
                return model, []
            except GeminiFailure:
                raise
            except Exception as exc:
                if getattr(exc, 'code', None) not in (403,404):
                    raise
                alternatives=[]
                for i, item in enumerate(c.models.list()):
                    if i>=200:
                        break
                    name=(item.name or '').removeprefix('models/')
                    actions=getattr(item,'supported_actions',[]) or []
                    if (name.startswith('gemini-') and 'flash' in name and
                        not any(x in name for x in ('tts','image','audio','live')) and
                        'generateContent' in actions):
                        alternatives.append({'id':name,'label':item.display_name or f'빠른 모델 대안 {len(alternatives)+1}'})
                    if len(alternatives)>=5:
                        break
                return None, alternatives
    except GeminiFailure:
        raise
    except Exception as exc:
        raise failure(exc) from None


def call(system, payload, schema):
    scope=current.get()
    if scope is None:
        raise GeminiFailure('missing_key','Gemini 연결이 없습니다.')
    if len(scope.images)>4 or sum(len(i.data) for i in scope.images)>4*1024*1024:
        raise GeminiFailure('input','시각 분석 페이지 또는 이미지 크기를 줄여 주세요.')
    text=json.dumps(payload,ensure_ascii=False)
    if len(text.encode())+len(system.encode())>100_000:
        raise GeminiFailure('input','자료 또는 대화가 깁니다. 자료를 나누어 새 연습을 시작해 주세요.')
    # Each SDK call is counted, including failures. Inter-call debounce is handled
    # once at the public action boundary; analysis/generation must both be possible.
    if scope.meter.calls >= scope.meter.max_calls:
        raise GeminiFailure('local_limit','이번 세션의 요청 한도에 도달했습니다. 기록을 저장해 주세요.')
    scope.meter.calls += 1
    if not GLOBAL_CONCURRENCY.acquire(blocking=False):
        raise GeminiFailure('rate','서버 요청이 몰렸습니다. 잠시 후 다시 시도해 주세요.')
    try:
        parts=[types.Part.from_text(text=text)]
        if 'documents' in payload:
            for image in scope.images:
                parts.append(types.Part.from_text(text=f'시각 근거: file={image.file}, physical_page={image.number}, page_id={image.page_id}'))
                parts.append(types.Part.from_bytes(data=image.data,mime_type='image/jpeg'))
        with client(scope.key) as c:
            response=c.models.generate_content(model=scope.model,contents=parts,
                config=types.GenerateContentConfig(system_instruction=system,
                    response_mime_type='application/json', response_json_schema=schema.model_json_schema(),
                    max_output_tokens=4096,
                    thinking_config=types.ThinkingConfig(thinking_level='low') if scope.model.startswith('gemini-3') else None,
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)))
        if not response.text:
            raise GeminiFailure('empty','안전 필터 또는 응답 중단으로 결과를 받지 못했습니다. 기록은 유지됩니다.')
        return schema.model_validate_json(response.text)
    except GeminiFailure:
        raise
    except Exception as exc:
        raise failure(exc) from None
    finally:
        GLOBAL_CONCURRENCY.release()



def available_models(key):
    """Metadata only; never silently generates on a substitute model."""
    try:
        result=[]
        with client(key) as c:
            for index,item in enumerate(c.models.list()):
                if index>=200: break
                name=(item.name or '').removeprefix('models/')
                if (name.startswith('gemini-') and 'flash' in name and
                    not any(x in name for x in ('tts','image','audio','live')) and
                    'generateContent' in (item.supported_actions or [])):
                    result.append({'id':name,'label':item.display_name or f'빠른 모델 대안 {len(result)+1}'})
                if len(result)>=5: break
        return result
    except Exception as exc:
        if isinstance(exc,GeminiFailure): raise
        raise failure(exc) from None
