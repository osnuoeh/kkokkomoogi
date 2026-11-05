"""Public BYOK service. Does not import the legacy shared-key/Redis path."""
import time
from dataclasses import dataclass,field
from . import engine
from .gemini_provider import Scope,Meter,use,connect,GeminiFailure,DEFAULT_MODEL,available_models
from .prompts import PRODUCTION_INSTRUCTIONS


@dataclass
class Connection:
    key: str = field(default='',repr=False)
    model: str = DEFAULT_MODEL
    meter: Meter = field(default_factory=Meter,repr=False)
    pending_key: str = field(default='',repr=False)
    alternatives: list = field(default_factory=list)
    metadata_checked: bool = False
    last_connect: float = 0

    @property
    def ready(self):
        return bool(self.key and self.metadata_checked)

    def disconnect(self):
        self.key='';self.pending_key='';self.alternatives=[];self.metadata_checked=False
        # Do not reset the local usage/debounce counter by reconnecting.

    def find_alternatives(self):
        key=self.key or self.pending_key
        if not key:
            raise GeminiFailure('missing_key','다시 키를 연결해 주세요.')
        if time.monotonic()-self.last_connect<2:
            raise GeminiFailure('rate','잠시 후 다시 시도해 주세요.')
        self.last_connect=time.monotonic()
        choices=available_models(key)
        self.pending_key=key;self.key='';self.metadata_checked=False;self.alternatives=choices

    def attach(self,key,model=DEFAULT_MODEL):
        if time.monotonic()-self.last_connect<2:
            raise GeminiFailure('rate','연결 요청이 빠릅니다. 잠시 후 다시 시도해 주세요.')
        self.last_connect=time.monotonic()
        self.key='';self.metadata_checked=False;self.pending_key='';self.alternatives=[]
        selected, alternatives=connect(key,model)
        if selected:
            self.key=key.strip();self.model=selected;self.metadata_checked=True
        else:
            self.pending_key=key.strip();self.alternatives=alternatives
        return self.ready


def run(connection, operation, images=()):
    if not connection.ready:
        raise GeminiFailure('missing_key','먼저 나의 Gemini를 연결해 주세요. 실제 AI 요청은 보내지 않았습니다.')
    now=time.monotonic()
    if now-connection.meter.last<connection.meter.interval:
        raise GeminiFailure('rate','잠시 후 다시 요청해 주세요.')
    connection.meter.last=now
    try:
        with use(Scope(connection.key,connection.model,tuple(images),connection.meter)):
            return operation()
    except GeminiFailure as exc:
        if exc.kind=='auth':
            connection.disconnect()
        raise


def generate(connection,pages,turns,audience,difficulty,topic='',images=()):
    return run(connection,lambda:engine.generate(pages,turns,audience,difficulty,topic,
               PRODUCTION_INSTRUCTIONS,demo=False),images)


def feedback(connection,pages,turn,images=()):
    return run(connection,lambda:engine.feedback(pages,turn,demo=False),images)
