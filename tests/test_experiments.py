import csv
import json
from pathlib import Path
import pytest
from qa import engine, evaluation
from qa.documents import examples
from qa.models import Historical, Question, Comparison, Match, Quality, Grade, METRICS
from qa.experiments import execute_suite, parse_scenario, AnswerStep, score_rows
from qa.experiment_reports import suite_markdown, suite_csv, coverage_csv
from qa.public_reports import snapshot, markdown


def references():
    return [Historical(question='HOLDOUT_REFERENCE_8321', answer='HOLDOUT_ANSWER_5842', feedback='HOLDOUT_FEEDBACK_888')]


def test_independent_first_question_repeats_and_fixed_conditions(monkeypatch):
    original = engine.generate
    histories = []
    def generate(pages, turns, *args, **kwargs):
        histories.append(len(turns))
        return original(pages, turns, *args, **kwargs)
    monkeypatch.setattr(engine, 'generate', generate)
    suite = execute_suite(examples(), references(), '', '전제를 질문하세요.', repeats=3)
    assert len(suite['runs']) == 6
    assert histories == [0]*6
    assert len({r['condition_id'] for r in suite['runs']}) == 1
    assert len({r['prompt_version'] for r in suite['runs']}) == 2
    assert all(len(r['turns']) == 1 for r in suite['runs'])
    assert all(g['score'] is None for r in suite['runs'] for t in r['turns'] for g in t['quality']['grades'])
    assert all(row['점수 평균'] is None and row['채점 수'] == 0 for row in score_rows(suite))


def test_scenario_replay_future_answers_notes_holdouts_never_reach_generation(monkeypatch):
    generator_requests = []
    judge_requests = []
    def fake(system, payload, schema, api_key=None):
        data = json.dumps(payload, ensure_ascii=False)
        if schema is Question:
            assert 'HOLDOUT_' not in data and 'SECRET_NOTE' not in data
            generator_requests.append(payload)
            return Question(question='주장의 근거는 무엇인가요?', focus='근거', connection='최근 답변', basis='근거 부족', citations=[])
        judge_requests.append(payload)
        # Both variants' full generation must finish before the first judgment.
        assert len(generator_requests) == 6
        return Quality(grades=[Grade(metric=m, score=None, reason='검토 필요') for m in METRICS], problems=[], suggestion='검토')
    def compare(refs, turns, demo):
        assert len(generator_requests) == 6 and refs[0].question == 'HOLDOUT_REFERENCE_8321'
        return Comparison(matches=[Match(reference_index=0, generated_indices=[], status='검토 필요', concept='개념', reason='검토')], limitations='모의').model_dump()
    monkeypatch.setattr(engine, 'llm', fake)
    monkeypatch.setattr(evaluation, 'compare', compare)
    scenario = [AnswerStep(answer='SCRIPT_FIRST_1', note='SECRET_NOTE'), AnswerStep(answer='SCRIPT_FUTURE_2', note='SECRET_NOTE')]
    suite = execute_suite(examples(), references(), '', '전제만 물어보세요.', repeats=1, scenario=scenario, demo=False)
    for offset in (0, 3):
        assert generator_requests[offset]['conversation'] == []
        assert 'SCRIPT_FUTURE_2' not in json.dumps(generator_requests[offset+1])
        assert generator_requests[offset+1]['conversation'][-1]['answer'] == 'SCRIPT_FIRST_1'
        assert generator_requests[offset+2]['conversation'][-1]['answer'] == 'SCRIPT_FUTURE_2'
    assert all(r['turns'][-1]['answer'] == '' for r in suite['runs'])
    assert suite['conditions']['answers_per_run'] == 2
    assert judge_requests


@pytest.mark.parametrize('data', [b'{}', b'[]', b'[{}]', b'[{"answer":"   "}]', b'not-json', b'[1]', json.dumps([{'answer':'x'}]*6).encode()])
def test_invalid_scenarios_rejected(data):
    with pytest.raises(ValueError, match='시나리오'):
        parse_scenario(data)


def test_scenario_valid_and_does_not_reuse_historical_answers():
    steps = parse_scenario('[{"answer":"실제 시나리오 답변", "note":"비공개 메모"}]'.encode())
    suite = execute_suite(examples(), references(), '', '전제', repeats=1, scenario=steps)
    assert len(suite['runs'][0]['turns']) == 2
    assert 'HOLDOUT_ANSWER' not in suite['runs'][0]['turns'][0]['answer']


@pytest.mark.parametrize('repeats', [0, 6])
def test_invalid_repeat_limits(repeats):
    with pytest.raises(ValueError):
        execute_suite(examples(), references(), '', '', repeats=repeats)


def test_material_and_scenario_changes_change_conditions():
    a = execute_suite(examples(), references(), '', '', repeats=1)
    pages = examples(); pages[0].text += '추가 데이터'
    b = execute_suite(pages, references(), '', '', repeats=1)
    c = execute_suite(examples(), references(), '', '', repeats=1, scenario=[AnswerStep(answer='답변')])
    assert len({s['condition_id'] for s in [a,b,c]}) == 3


def test_partial_generation_failure_is_preserved_and_secrets_not_logged(monkeypatch):
    original = engine.generate
    calls = 0
    def generate(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError('SECRET_PROVIDER_ERROR')
        return original(*args, **kwargs)
    monkeypatch.setattr(engine, 'generate', generate)
    suite = execute_suite(examples(), references(), '', '다른 지침', repeats=1)
    assert not suite['runs'][0]['turns'] and suite['runs'][1]['turns']
    assert len(suite['errors']) == 1
    assert 'SECRET_PROVIDER_ERROR' not in json.dumps(suite)
    csv_text = suite_csv(suite).decode('utf-8-sig')
    assert '생성 실패' in csv_text and '미채점' in csv_text


def test_judge_failure_not_converted_to_successful_score(monkeypatch):
    monkeypatch.setattr(engine, 'quality', lambda *a: (_ for _ in ()).throw(RuntimeError('SECRET')))
    suite = execute_suite(examples(), references(), '', '', repeats=1)
    assert suite['errors'] and all(row['채점 수'] == 0 for row in score_rows(suite))
    assert 'SECRET' not in json.dumps(suite)


def test_developer_exports_keep_conditions_provenance_comparisons_and_formula_safety():
    suite = execute_suite(examples(), references(), '', '=formula', repeats=1,
                          scenario=[AnswerStep(answer='=PRIVATE_FORMULA')])
    report = suite_markdown(examples(), references(), suite)
    assert '개발자' in report and suite['condition_id'] in report and '고정 시나리오' in report
    assert 'HOLDOUT_REFERENCE_8321' in report and 'before' in report and 'after' in report
    rows = list(csv.DictReader(suite_csv(suite).decode('utf-8-sig').splitlines()))
    assert rows[0]['answer'].startswith("'=")
    assert all(r['answer_source'] == '준비된 시나리오' for r in rows)
    assert 'HOLDOUT_REFERENCE_8321' in coverage_csv(suite, references()).decode('utf-8-sig')


def test_public_downloads_exclude_developer_fields_and_holdouts():
    t = engine.generate(examples(), [])
    for key in ['quality', 'human_feedback', 'errors', 'prompt']:
        t[key] = 'HOLDOUT_PRIVATE_9281'
    runs = [{'name': '연습', 'data_label': '예제', 'turns': [t], 'comparison': 'HOLDOUT_PRIVATE_9281'}]
    assert 'HOLDOUT_PRIVATE_9281' not in json.dumps(snapshot(examples(), runs))
    assert 'HOLDOUT_PRIVATE_9281' not in markdown(examples(), runs)


def test_exact_duplicates_are_candidates_not_semantic_scores():
    suite = execute_suite(examples(), references(), '', '후보', repeats=2)
    assert len(suite['exact_duplicates']) == 2
    assert all('반복 1' in row['등장 위치'] and '반복 2' in row['등장 위치'] for row in suite['exact_duplicates'])



def test_public_download_accepts_existing_sessions_without_data_label():
    turn = engine.generate(examples(), [])
    runs = [{'name': '기존 연습', 'turns': [turn]}]
    assert snapshot(examples(), runs)['sessions'][0]['data_label'] == '사용자 Q&A 기록'
    assert '기존 연습' in markdown(examples(), runs)
