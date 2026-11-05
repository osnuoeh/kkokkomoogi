"""Developer exports keep experimental conditions and missing/failed judgments."""
import json
from .history import csv_bytes
from .reports import markdown
from .experiments import comparison_rows


def suite_markdown(pages, references, suite):
    sections = ['# 개발자 반복 평가 리포트', '', suite['purpose'], suite['data_label'],
                '고정 시나리오의 답변은 생성 질문에 맞지 않을 수 있습니다. 실제 사용자 대화의 품질 검증과 구분하세요.',
                '```json', json.dumps({k: v for k, v in suite.items() if k != 'runs'}, ensure_ascii=False, indent=2), '```']
    for run in suite['runs']:
        sections.extend([f'## {run["name"]} · {run["status"]}',
                         f'답변 출처: {run["answer_source"]}',
                         f'조건: {run["condition_id"]}',
                         '오류: ' + ('; '.join(run['errors']) or '없음'),
                         markdown(pages, [run], references, run.get('comparison'))])
    return '\n\n'.join(sections)


def suite_csv(suite):
    # Include failures, condition hashes and scripted-vs-live provenance in each row.
    rows = []
    for run in suite['runs']:
        for i, turn in enumerate(run['turns'], 1):
            grades = (turn.get('quality') or {}).get('grades') or [{'metric': '미평가', 'score': None, 'reason': '미채점'}]
            for grade in grades:
                rows.append({'purpose': suite['purpose'], 'dataset': suite['dataset'],
                             'data_label': suite['data_label'], 'condition_id': suite['condition_id'],
                             'document_hash': suite['conditions']['document_hash'],
                             'scenario_hash': suite['conditions']['scenario_hash'],
                             'holdout_hash': suite['conditions']['holdout_hash'],
                             'engine_hash': suite['conditions']['engine_hash'],
                             'audience': suite['conditions']['audience'], 'difficulty': suite['conditions']['difficulty'],
                             'model': suite['conditions']['model'], 'demo': suite['conditions']['demo'],
                             'variant': run['variant'], 'repeat': run['repeat'], 'prompt_version': run['prompt_version'],
                             'answer_source': run['answer_source'], 'question_no': i,
                             'question': turn['question']['question'], 'answer': turn.get('answer', ''),
                             'metric': grade['metric'], 'score': grade['score'] or '검토 필요',
                             'reason': grade['reason'], 'status': run['status'], 'errors': '; '.join(run['errors'])})
        if not run['turns']:
            # Failed generations are never silently dropped from exported results.
            rows.append({'purpose': suite['purpose'], 'dataset': suite['dataset'], 'data_label': suite['data_label'],
                         'condition_id': suite['condition_id'], 'document_hash': suite['conditions']['document_hash'],
                         'scenario_hash': suite['conditions']['scenario_hash'], 'holdout_hash': suite['conditions']['holdout_hash'],
                         'engine_hash': suite['conditions']['engine_hash'], 'audience': suite['conditions']['audience'],
                         'difficulty': suite['conditions']['difficulty'], 'model': suite['conditions']['model'],
                         'demo': suite['conditions']['demo'], 'variant': run['variant'], 'repeat': run['repeat'],
                         'prompt_version': run['prompt_version'], 'answer_source': run['answer_source'],
                         'question_no': '', 'question': '', 'answer': '', 'metric': '생성 실패',
                         'score': '검토 필요', 'reason': '생성 실패로 미채점', 'status': run['status'], 'errors': '; '.join(run['errors'])})
    return csv_bytes(rows)


def coverage_csv(suite, references):
    return csv_bytes(comparison_rows(suite, references))
