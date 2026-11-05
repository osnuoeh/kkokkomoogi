"""Server-managed public AI access. No developer routing or visitor credentials."""
import json
import os
import uuid
from dataclasses import dataclass, field
from . import engine
from .llm_policy import LLMPolicy, policy
from .quota import Limits, RedisQuotaStore, QuotaExceeded, QuotaUnavailable
from .prompts import PRODUCTION_INSTRUCTIONS


class PracticeUnavailable(Exception):
    """Safe, user-facing message only; never retain the original SDK exception."""


@dataclass(frozen=True)
class Settings:
    enabled: bool = False
    api_key: str = field(default='', repr=False)
    model: str = 'gpt-4o-mini'
    redis_url: str = field(default='', repr=False)
    redis_token: str = field(default='', repr=False)
    namespace: str = 'kkokkomoo-public-v1'
    limits: Limits = field(default_factory=Limits)
    max_output_tokens: int = 1000
    max_request_bytes: int = 80_000

    @property
    def ready(self):
        return bool(self.enabled and self.api_key and self.redis_url and self.redis_token)


@dataclass
class PracticeSession:
    id: str = field(default_factory=lambda: uuid.uuid4().hex, repr=False)
    attempts: int = 0
    pause_message: str = ''


def settings_from(get):
    """Read only server-owned values; invalid configuration defaults to free rules."""
    if str(get('PUBLIC_AI_ENABLED', '')).strip().lower() not in ('1', 'true'):
        return Settings()
    try:
        def number(name, default, low=1, high=100_000_000):
            raw = get(name, default)
            if isinstance(raw, bool):
                raise ValueError()
            value = int(str(raw))
            if not low <= value <= high:
                raise ValueError()
            return value
        model = str(get('PUBLIC_AI_MODEL', 'gpt-4o-mini'))
        # Reviewed non-reasoning models with bounded text/output requests.
        if model not in ('gpt-4o-mini', 'gpt-4.1-mini'):
            raise ValueError()
        limits = Limits(
            session_calls=number('PUBLIC_AI_SESSION_CALL_LIMIT', 10),
            daily_calls=number('PUBLIC_AI_DAILY_CALL_LIMIT', 100),
            total_calls=number('PUBLIC_AI_TOTAL_CALL_LIMIT', 500),
            daily_units=number('PUBLIC_AI_DAILY_UNIT_LIMIT', 8_000_000),
            total_units=number('PUBLIC_AI_TOTAL_UNIT_LIMIT', 40_000_000),
            minute_calls=number('PUBLIC_AI_MINUTE_CALL_LIMIT', 10),
            min_interval=number('PUBLIC_AI_MIN_INTERVAL_SECONDS', 2, 0, 3600))
        result = Settings(True, str(get('OPENAI_API_KEY', '')).strip(), model,
                          str(get('PUBLIC_AI_REDIS_URL', '')).strip(),
                          str(get('PUBLIC_AI_REDIS_TOKEN', '')).strip(),
                          str(get('PUBLIC_AI_QUOTA_NAMESPACE', 'kkokkomoo-public-v1')), limits,
                          number('PUBLIC_AI_MAX_OUTPUT_TOKENS', 1000, 128, 2000),
                          number('PUBLIC_AI_MAX_REQUEST_BYTES', 80_000, 4096, 100_000))
        if result.ready:
            RedisQuotaStore(result.redis_url, result.redis_token, result.namespace)
        return result
    except Exception:
        return Settings()


def read_settings():
    # Streamlit top-level Secrets also become environment variables. Explicit
    # fallback handles deployments that load Secrets lazily. No value is printed.
    def get(name, default=''):
        if name in os.environ:
            return os.environ[name]
        try:
            import streamlit as st
            return st.secrets.get(name, default)
        except Exception:
            return default
    return settings_from(get)


def is_demo(settings, session):
    return not settings.ready or bool(session.pause_message)


def _execute(settings, session, operation):
    if is_demo(settings, session):
        return operation(True)
    # Local counter is an additional guard; the authoritative shared counter is Redis.
    if session.attempts >= settings.limits.session_calls:
        session.pause_message = '이번 연습의 AI 이용 한도에 도달했습니다. 이후에는 규칙 기반 체험으로 이어집니다.'
        raise PracticeUnavailable(session.pause_message)

    def authorize(system, payload, schema):
        request_text = json.dumps({'system': system, 'input': payload,
                                  'schema': schema.model_json_schema()}, ensure_ascii=False)
        size = len(request_text.encode())
        if size > settings.max_request_bytes:
            raise PracticeUnavailable('자료나 대화가 길어 이번 질문을 만들지 못했습니다. 자료를 나누어 새 연습을 시작해 주세요.')
        # Conservative usage units, NOT a claim of exact tokens or dollar cost.
        # Reservations and failed requests are not refunded; SDK retries are disabled.
        units = size + settings.max_output_tokens + 2048
        store = RedisQuotaStore(settings.redis_url, settings.redis_token, settings.namespace)
        store.reserve(session.id, units, settings.limits)
        session.attempts += 1

    request_policy = LLMPolicy(settings.api_key, settings.model, settings.max_output_tokens, authorize)
    token = policy.set(request_policy)
    try:
        return operation(False)
    except QuotaExceeded as exc:
        if exc.reason in ('minute', 'interval'):
            raise PracticeUnavailable('요청이 잠시 몰렸습니다. 잠시 후 다시 시도해 주세요.') from None
        session.pause_message = ('이번 연습의 AI 이용 한도에 도달했습니다.' if exc.reason == 'session'
                                 else '서비스의 AI 이용 한도에 도달했습니다.') + ' 이후에는 규칙 기반 체험으로 이어집니다.'
        raise PracticeUnavailable(session.pause_message) from None
    except QuotaUnavailable:
        session.pause_message = '지금은 AI 연습을 안전하게 제공할 수 없습니다. 이후에는 규칙 기반 체험으로 이어집니다.'
        raise PracticeUnavailable(session.pause_message) from None
    except PracticeUnavailable:
        raise
    except Exception:
        session.pause_message = 'AI 연결에 문제가 생겨 이번 요청을 완료하지 못했습니다. 기록은 유지되며 이후에는 규칙 기반 체험으로 이어집니다.'
        raise PracticeUnavailable(session.pause_message) from None
    finally:
        policy.reset(token)


def generate_question(settings, session, pages, turns, audience, difficulty, topic=''):
    return _execute(settings, session, lambda demo: engine.generate(
        pages, turns, audience, difficulty, topic, PRODUCTION_INSTRUCTIONS, demo=demo))


def answer_feedback(settings, session, pages, turn):
    # Reopening feedback is free and does not issue a second model request.
    if turn.get('feedback'):
        return turn['feedback'], turn.get('feedback_mode', turn['mode'])
    if turn['mode'] == '데모':
        return engine.feedback(pages, turn, demo=True), '데모'
    result = _execute(settings, session, lambda demo: (engine.feedback(pages, turn, demo=demo),
                                                      '데모' if demo else 'OpenAI'))
    return result
