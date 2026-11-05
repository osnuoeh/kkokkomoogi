from pathlib import Path
from streamlit.testing.v1 import AppTest

APP=Path(__file__).parents[1]/'app.py'

def button(at,label):
    return next(b for b in at.button if b.label==label)

def test_demo_ui_full_qa_and_debug_download_flow():
    at=AppTest.from_file(str(APP),default_timeout=20).run()
    assert not at.exception
    button(at,'예제 자료로 체험하기').click().run()
    assert len(at.session_state['pages'])==3
    button(at,'새 연습 시작 / 첫 질문 생성').click().run()
    assert not at.exception
    assert len(at.session_state['runs'][0]['turns'])==1
    assert not any(b.label=='다음 질문 생성' for b in at.button)
    next(t for t in at.text_area if t.label=='발표자로서 직접 답변해 주세요').set_value('검증 손실이 증가하므로 과적합을 의심합니다.')
    button(at,'답변 제출').click().run()
    button(at,'답변 피드백 받기 / 다시 평가').click().run()
    assert at.session_state['runs'][0]['turns'][0]['feedback']
    button(at,'다음 질문 생성').click().run()
    assert len(at.session_state['runs'][0]['turns'])==2
    button(at,'기존 질문과 생성 질문 비교').click().run()
    assert at.session_state['comparison']['mode']=='데모'
    button(at,'선택 질문 품질 평가').click().run()
    next(x for x in at.selectbox if x.label=='사람 평가 · 자료 관련성').select(4)
    button(at,'사람 평가 저장').click().run()
    assert at.session_state['runs'][0]['turns'][0]['human_scores']['자료 관련성']==4
    next(t for t in at.text_area if t.label.startswith('추가 생성 지침')).set_value('전제의 적용 조건을 물어보세요.')
    button(at,'프롬프트 버전 저장').click().run()
    button(at,'저장한 프롬프트로 새 실험 생성').click().run()
    assert len(at.session_state['runs'])==2
    before,after=at.session_state['runs']
    assert before['turns'][0]['prompt_version']!=after['turns'][0]['prompt_version']
    assert not at.exception
    assert len(at.get('download_button')) >= 4

def test_whitespace_answer_does_not_advance():
    at=AppTest.from_file(str(APP),default_timeout=20).run()
    button(at,'예제 자료로 체험하기').click().run()
    button(at,'새 연습 시작 / 첫 질문 생성').click().run()
    next(t for t in at.text_area if t.label=='발표자로서 직접 답변해 주세요').set_value('   ')
    button(at,'답변 제출').click().run()
    assert at.session_state['runs'][0]['turns'][0]['answer']==''
    assert not any(b.label=='다음 질문 생성' for b in at.button)
    assert not at.exception

def test_missing_api_key_keeps_existing_records(monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    at=AppTest.from_file(str(APP),default_timeout=20).run()
    button(at,'예제 자료로 체험하기').click().run()
    next(r for r in at.radio if r.label=='질문 생성 방식').set_value('OpenAI API').run()
    button(at,'새 연습 시작 / 첫 질문 생성').click().run()
    assert at.session_state['runs']==[]
    assert at.error and not at.exception
