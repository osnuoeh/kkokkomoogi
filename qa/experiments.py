"""Developer-only paired experiments. Never imported by the public entrypoint.

The shared engine receives only documents and the answers already replayed.
Holdout questions, future answers, judge output and scenario notes stay here.
"""
import hashlib
import inspect
import json
import os
from datetime import datetime, timezone
from pydantic import BaseModel, Field
from . import engine, evaluation
from .documents import validate_size
from .models import Page
from .gemini_provider import current as gemini_scope


class AnswerStep(BaseModel):
    answer: str = Field(min_length=1, max_length=12000)
    note: str = ''


def parse_scenario(data: bytes):
    validate_size(data)
    try:
        raw = json.loads(data.decode('utf-8-sig'))
        if not isinstance(raw, list) or not 1 <= len(raw) <= 5:
            raise ValueError()
        steps = [AnswerStep.model_validate(item) for item in raw]
        if any(not step.answer.strip() for step in steps):
            raise ValueError()
    except Exception:
        raise ValueError('시나리오는 answer 필드를 가진 1~5개 객체의 JSON 배열이어야 합니다. 답변은 비어 있지 않은 12,000자 이하 문자열입니다.') from None
    return steps


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def execute_suite(pages, references, prompt_before, prompt_after, repeats=2,
                  audience='일반 학생', difficulty='보통', demo=True, scenario=None,
                  dataset_name='데이터셋', data_label='개발용 자료 (실제 사용자 품질 검증 아님)', progress=None):
    """Generate all runs first; only then evaluate them in separate requests.

    Each repeat starts from empty history. Both variants use the same frozen
    documents/settings/script. No model determinism or statistical claim is made.
    Partial failures remain visible and downloadable, without raw provider errors.
    """
    if not 1 <= repeats <= 5:
        raise ValueError('반복 횟수는 1~5회입니다.')
    if len(prompt_before) > 6000 or len(prompt_after) > 6000:
        raise ValueError('추가 생성 지침은 6,000자 이하입니다.')
    if not references or len(references) > 100:
        raise ValueError('평가용 질문은 1~100개 등록해 주세요.')
    frozen_pages = [Page.model_validate(p.model_dump()) for p in pages]
    engine.context(frozen_pages)  # Fail before launching if presentation text is missing.
    steps = [AnswerStep.model_validate(s.model_dump()) for s in (scenario or [])]
    if len(steps) > 5 or any(not s.answer.strip() for s in steps):
        raise ValueError('시나리오는 비어 있지 않은 답변을 최대 5개까지 지원합니다.')
    frozen_refs = [r.model_copy(deep=True) for r in references]
    condition = {'document_hash': digest([p.model_dump() for p in frozen_pages]),
                 'holdout_hash': digest([r.model_dump() for r in frozen_refs]),
                 'scenario_hash': digest([s.model_dump() for s in steps]),
                 'audience': audience, 'difficulty': difficulty, 'demo': demo,
                 'provider':'Gemini' if gemini_scope.get() else 'OpenAI',
                 'model': '규칙 기반' if demo else gemini_scope.get().model if gemini_scope.get() else os.getenv('OPENAI_MODEL','gpt-4o-mini'),
                 'visual_pages':[(i.page_id,hashlib.sha256(i.data).hexdigest()) for i in gemini_scope.get().images] if gemini_scope.get() else [],
                 'engine_hash': hashlib.sha256(inspect.getsource(engine).encode()).hexdigest(),
                 'repeats': repeats, 'answers_per_run': len(steps)}
    suite = {'purpose': '개발자 오프라인 보조 평가 — 실제 사용자 품질 결과 아님',
             'dataset': dataset_name, 'data_label': data_label, 'conditions': condition,
             'condition_id': digest(condition), 'created_at': datetime.now(timezone.utc).isoformat(),
             'scenario': [s.model_dump() for s in steps],
             'prompts': {'before': prompt_before, 'after': prompt_after},
             'runs': [], 'errors': []}
    max_calls = 2 * repeats * (2 * (len(steps)+1) + 1)
    done = 0

    def tick():
        nonlocal done
        done += 1
        if progress:
            progress(min(done/max_calls, 1.0))

    # Generation phase contains no calls to a judge and no reference-question input.
    for repeat in range(1, repeats+1):
        for variant, prompt in [('before', prompt_before), ('after', prompt_after)]:
            run = {'name': f'{variant} · 반복 {repeat}', 'variant': variant, 'repeat': repeat,
                   'data_label': data_label, 'condition_id': suite['condition_id'],
                   'turns': [], 'errors': [], 'comparison': None,
                   'prompt_version': engine.version(prompt), 'prompt': prompt,
                   'answer_source': '준비된 시나리오' if steps else '답변 없는 독립 첫 질문',
                   'status': '생성 완료'}
            suite['runs'].append(run)
            try:
                run['turns'].append(engine.generate(frozen_pages, [], audience, difficulty, prompt=prompt, demo=demo))
                tick()
                for step in steps:
                    # Only the current answer becomes visible after its question exists.
                    run['turns'][-1]['answer'] = step.answer.strip()
                    run['turns'][-1]['answer_source'] = 'scripted'
                    run['turns'].append(engine.generate(frozen_pages, run['turns'], audience, difficulty,
                                                        prompt=prompt, demo=demo))
                    tick()
            except Exception:
                run['status'] = '일부 또는 전체 생성 실패'
                run['errors'].append('질문 생성 실패: API 연결·모델 지원·입력 자료를 확인하세요. 원문 오류는 기록하지 않습니다.')

    for run in suite['runs']:
        for i, turn in enumerate(run['turns']):
            try:
                turn['quality'] = engine.quality(frozen_pages, run['turns'], i, demo)
                turn['quality_mode'] = '데모' if demo else 'OpenAI'
            except Exception:
                run['errors'].append(f'Q{i+1} 품질 평가 실패 (미채점)')
            tick()
        if run['turns']:
            try:
                run['comparison'] = evaluation.compare(frozen_refs, run['turns'], demo)
            except Exception:
                run['errors'].append('과거 질문 비교 실패 (미판정)')
            tick()
        if run['errors']:
            suite['errors'].append({'run': run['name'], 'messages': run['errors']})
    suite['exact_duplicates'] = duplicate_candidates(suite)
    if progress:
        progress(1.0)
    return suite


def score_rows(suite):
    rows = []
    for run in suite['runs']:
        for metric in engine.METRICS:
            values = [g['score'] for t in run['turns'] for g in (t.get('quality') or {}).get('grades', [])
                      if g['metric'] == metric and g['score'] is not None]
            rows.append({'구분': run['variant'], '반복': run['repeat'], '조건': run['condition_id'][:12],
                         '프롬프트': run['prompt_version'], '항목': metric,
                         '점수 평균': sum(values)/len(values) if values else None, '채점 수': len(values),
                         '생성 질문 수': len(run['turns']), '상태': run['status'], '오류 수': len(run['errors'])})
    return rows


def comparison_rows(suite, references):
    rows = []
    for run in suite['runs']:
        for match in (run.get('comparison') or {}).get('matches', []):
            rows.append({'실험': run['name'], '기존 질문': references[match['reference_index']].question,
                         '개념': match['concept'], '판정': match['status'], '이유': match['reason'],
                         '생성 질문': ' / '.join(run['turns'][i]['question']['question'] for i in match['generated_indices'])})
    return rows


def duplicate_candidates(suite):
    """Text equality diagnostic only, never presented as semantic diversity score."""
    groups = {}
    for run in suite['runs']:
        for i, turn in enumerate(run['turns'], 1):
            question = turn['question']['question']
            key = (run['variant'], ' '.join(question.split()))
            groups.setdefault(key, []).append(f'반복 {run["repeat"]} Q{i}')
    return [{'구분': variant, '질문': question, '등장 위치': ', '.join(positions)}
            for (variant, question), positions in groups.items() if len(positions) > 1]
