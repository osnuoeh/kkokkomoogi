from pathlib import Path
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

DEV = Path(__file__).parents[1]/'dev_app.py'


def button(at, label):
    return next(b for b in at.button if b.label == label)


def enable_local(monkeypatch):
    monkeypatch.setenv('KKOKKOMOOGI_DEV_LOCAL', '1')
    original = st.get_option
    monkeypatch.setattr(st, 'get_option', lambda key: '127.0.0.1' if key == 'server.address' else original(key))


def test_dev_app_disabled_by_default_even_with_query_params(monkeypatch):
    monkeypatch.delenv('KKOKKOMOOGI_DEV_LOCAL', raising=False)
    at = AppTest.from_file(str(DEV), default_timeout=20)
    at.query_params.update({'admin':'1', 'dev':'1'})
    at.run()
    assert at.error and not at.tabs and not at.get('file_uploader') and not at.exception


def test_dev_app_rejects_public_network_binding(monkeypatch):
    enable_local(monkeypatch)
    monkeypatch.setattr(st, 'get_option', lambda key: '0.0.0.0' if key == 'server.address' else None)
    at = AppTest.from_file(str(DEV), default_timeout=20).run()
    assert at.error and not at.tabs and not at.exception


def test_developer_legacy_workflow_and_automated_first_question_evaluation(monkeypatch):
    enable_local(monkeypatch)
    at = AppTest.from_file(str(DEV), default_timeout=20).run()
    assert not at.exception
    button(at, '예제 자료로 체험하기').click().run()
    button(at, '새 연습 시작 / 첫 질문 생성').click().run()
    next(t for t in at.text_area if t.label == '발표자로서 직접 답변해 주세요').set_value('검증 손실이 증가했습니다.')
    button(at, '답변 제출').click().run()
    button(at, '다음 질문 생성').click().run()
    button(at, '기존 질문과 생성 질문 비교').click().run()
    button(at, '선택 질문 품질 평가').click().run()
    next(x for x in at.selectbox if x.label == '사람 평가 · 자료 관련성').select(4)
    button(at, '사람 평가 저장').click().run()
    assert at.session_state['runs'][0]['turns'][0]['human_scores']['자료 관련성'] == 4
    next(t for t in at.text_area if t.label == '후보 생성 지침').set_value('핵심 전제를 질문하세요.')
    button(at, '자동 반복 평가 실행').click().run()
    suite = at.session_state['suite']
    assert len(suite['runs']) == 4
    assert len({r['prompt_version'] for r in suite['runs']}) == 2
    assert len({r['condition_id'] for r in suite['runs']}) == 1
    assert not at.exception
    assert {'자동 평가 Markdown', '자동 평가 CSV', '개념 비교 CSV'}.issubset({x.label for x in at.get('download_button')})
    # Applying another dataset invalidates previous batch results.
    button(at, '예제 자료로 체험하기').click().run()
    assert 'suite' not in at.session_state
