from typing import Literal
from pydantic import BaseModel, Field

METRICS = ['자료 관련성', '기술적 정확성', '논리적 연결성', '질문 다양성', '근거 신뢰성']
class Page(BaseModel):
    id: str
    file: str
    role: Literal['발표', '참고']
    number: int = Field(ge=1)
    kind: str
    text: str
class Citation(BaseModel):
    page_id: str
    quote: str
class Question(BaseModel):
    question: str = Field(min_length=5, max_length=2000)
    focus: str
    connection: str
    basis: Literal['원문 확인', '모델 추론', '근거 부족']
    citations: list[Citation]
class Feedback(BaseModel):
    accuracy: str
    logic: str
    evidence: str
    next_step: str
class Grade(BaseModel):
    metric: Literal['자료 관련성', '기술적 정확성', '논리적 연결성', '질문 다양성', '근거 신뢰성']
    score: int | None = Field(default=None, ge=1, le=5)
    reason: str
class Quality(BaseModel):
    grades: list[Grade]
    problems: list[str]
    suggestion: str
class Match(BaseModel):
    reference_index: int = Field(ge=0)
    generated_indices: list[int]
    status: Literal['다룸', '부분적으로 다룸', '놓침', '검토 필요']
    concept: str
    reason: str
class Comparison(BaseModel):
    matches: list[Match]
    limitations: str
class Historical(BaseModel):
    topic: str = ''
    material: str = ''
    question: str = Field(min_length=1)
    source: str = '사전 예상질문'
    type: str = ''
    answer: str = ''
    feedback: str = ''
    reference: str = ''
    page: str = ''
