"""Smoke tests for the simplified public visitor app."""
from pathlib import Path
from streamlit.testing.v1 import AppTest

APP = Path(__file__).parents[1] / "app.py"

def press(at, label):
    return next(b for b in at.button if b.label == label)

def test_public_demo_qa_without_personal_key():
    at = AppTest.from_file(str(APP), default_timeout=20).run()
    assert not at.exception
    assert not any("디버깅" in item.label for item in at.get("tab"))
    press(at, "예제 자료로 체험").click().run()
    assert at.session_state["pages"]
    press(at, "연습 시작 · 첫 질문").click().run()
    assert not at.exception
    assert len(at.session_state["runs"][0]["turns"]) == 1
    next(x for x in at.text_area if x.label == "내 답변").set_value("근거를 확인해야 합니다.")
    press(at, "답변 제출").click().run()
    press(at, "답변 피드백 보기").click().run()
    assert at.session_state["runs"][0]["turns"][0]["feedback"]
    press(at, "꼬리질문 받기").click().run()
    assert len(at.session_state["runs"][0]["turns"]) == 2
    assert not at.exception

def test_api_mode_requires_user_key_and_does_not_use_server_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "should-not-be-used")
    at = AppTest.from_file(str(APP), default_timeout=20).run()
    press(at, "예제 자료로 체험").click().run()
    next(x for x in at.radio if x.label == "연습 방식").set_value("내 OpenAI API 키로 AI 연습").run()
    assert at.session_state["api_key"] == ""
    assert press(at, "연습 시작 · 첫 질문").disabled
    assert not at.exception
