from pathlib import Path
import ast
from types import SimpleNamespace
import streamlit as st
from streamlit.testing.v1 import AppTest
from qa.prompts import PRODUCTION_INSTRUCTIONS
from qa import engine

ROOT = Path(__file__).parents[1]
APP = ROOT/'app.py'


def button(at, label):
    return next(b for b in at.button if b.label == label)


def workspace(at):
    return SimpleNamespace(pages=at.session_state['pages'], runs=at.session_state['runs'], active=at.session_state['active'])


def assert_public_only(at):
    assert not at.tabs
    labels = [getattr(x, 'label', '') for group in
              [at.button, at.text_area, at.text_input, at.selectbox, at.get('file_uploader'), at.get('download_button')]
              for x in group]
    forbidden = ['과거 질문', '평가', '프롬프트', '실험', '시나리오', '개발자', '디버깅', '데모 /']
    assert not at.radio and not at.sidebar.text_input
    assert not any(word in label for word in forbidden for label in labels)
    assert len(at.get('file_uploader')) == 2
    assert not at.exception


def test_public_demo_full_qa_feedback_followup_download_flow():
    at = AppTest.from_file(str(APP), default_timeout=20).run()
    assert_public_only(at)
    next(x for x in at.checkbox if x.label.startswith('연결 없이')).check().run()
    button(at, '예제 자료로 체험').click().run()
    assert len(workspace(at).pages) == 3
    button(at, '연습 시작 · 첫 질문').click().run()
    assert len(workspace(at).runs[0]['turns']) == 1
    assert not any(b.label == '꼬리질문 받기' for b in at.button)
    assert not any(b.label == '답변 피드백 보기' for b in at.button)
    next(t for t in at.text_area if t.label == '내 답변').set_value('검증 손실이 증가하므로 과적합을 의심합니다.')
    button(at, '답변 제출').click().run()
    button(at, '답변 피드백 보기').click().run()
    assert workspace(at).runs[0]['turns'][0]['feedback']
    button(at, '꼬리질문 받기').click().run()
    turns = workspace(at).runs[0]['turns']
    assert len(turns) == 2 and turns[0]['answer'] in turns[1]['question']['question']
    assert_public_only(at)
    assert [x.label for x in at.get('download_button')] == ['질문·답변 기록 다운로드', '전체 Q&A JSON']


def test_whitespace_answer_does_not_advance():
    at = AppTest.from_file(str(APP), default_timeout=20).run()
    next(x for x in at.checkbox if x.label.startswith('연결 없이')).check().run()
    button(at, '예제 자료로 체험').click().run()
    button(at, '연습 시작 · 첫 질문').click().run()
    next(t for t in at.text_area if t.label == '내 답변').set_value('   ')
    button(at, '답변 제출').click().run()
    assert workspace(at).runs[0]['turns'][0]['answer'] == ''
    assert not any(b.label == '꼬리질문 받기' for b in at.button)
    assert not at.exception


def test_first_visit_without_api_key_is_ready_for_rule_based_practice(monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    at = AppTest.from_file(str(APP), default_timeout=20).run()
    assert_public_only(at)
    assert any('실제 AI' in item.value for item in at.info)
    next(x for x in at.checkbox if x.label.startswith('연결 없이')).check().run()
    button(at, '예제 자료로 체험').click().run()
    assert not button(at, '연습 시작 · 첫 질문').disabled
    button(at, '연습 시작 · 첫 질문').click().run()
    assert workspace(at).runs[0]['turns'][0]['mode'] == '데모'
    assert not at.exception


def test_public_cannot_enable_developer_ui_by_query_environment_or_stale_state(monkeypatch):
    monkeypatch.setenv('KKOKKOMOOGI_DEV_LOCAL', '1')
    at = AppTest.from_file(str(APP), default_timeout=20)
    at.query_params.update({'admin': '1', 'dev': 'true', 'mode': 'debug', 'page': 'dev_app'})
    at.session_state['references'] = ['HOLDOUT_PRIVATE_91']
    at.session_state['prompt'] = 'HOLDOUT_PRIVATE_91'
    at.run()
    next(x for x in at.checkbox if x.label.startswith('연결 없이')).check().run()
    button(at, '예제 자료로 체험').click().run()
    button(at, '연습 시작 · 첫 질문').click().run()
    assert_public_only(at)
    turn = workspace(at).runs[0]['turns'][0]
    assert turn['prompt'] == PRODUCTION_INSTRUCTIONS
    assert turn['prompt_version'] == engine.version(PRODUCTION_INSTRUCTIONS)
    all_text = ' '.join(str(getattr(x, 'value', '')) for group in [at.markdown, at.caption, at.text, at.info] for x in group)
    assert 'HOLDOUT_PRIVATE_91' not in all_text


def test_public_entrypoint_has_no_developer_import_or_routing():
    tree = ast.parse(APP.read_text())
    modules = [n.module or '' for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    assert not any(m in {'qa.evaluation', 'qa.experiments', 'qa.experiment_reports', 'qa.history', 'dev_app'} for m in modules)
    assert not (ROOT/'pages').exists()


def test_topic_change_and_session_selection_still_work():
    at = AppTest.from_file(str(APP), default_timeout=20).run()
    next(x for x in at.checkbox if x.label.startswith('연결 없이')).check().run()
    button(at, '예제 자료로 체험').click().run()
    button(at, '연습 시작 · 첫 질문').click().run()
    next(t for t in at.text_area if t.label == '내 답변').set_value('검증했습니다.')
    button(at, '답변 제출').click().run()
    next(t for t in at.text_input if t.label.startswith('주제 변경')).set_value('외부 검증의 한계')
    button(at, '꼬리질문 받기').click().run()
    assert '외부 검증의 한계' in workspace(at).runs[0]['turns'][-1]['question']['question']
    button(at, '연습 시작 · 첫 질문').click().run()
    assert len(workspace(at).runs) == 2
    next(x for x in at.selectbox if x.label == '연습 기록 선택').select('1. 연습 1').run()
    assert workspace(at).active == 0
    assert_public_only(at)
