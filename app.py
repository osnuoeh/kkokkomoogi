"""Public presentation practice: no visitor credentials or technical controls."""
from datetime import datetime, timezone
import streamlit as st

from qa.documents import examples
from qa.ui_common import load_uploads, notify_error
from qa import public_ai
import json
from qa.public_reports import markdown, snapshot

st.set_page_config(page_title="꼬리에 꼬리 물기", page_icon="💬", layout="centered")
s = st.session_state
for key, default in {
    "pages": [], "runs": [], "data_label": "", "notices": [], "revision": 0, "active": 0
}.items():
    if key not in s:
        s[key] = default

# Remove credentials retained from the former visitor-key UI on a hot reload.
for legacy in ('api_key', 'public_api_key_input'):
    s.pop(legacy, None)
if '_public_ai_session' not in s:
    s._public_ai_session = public_ai.PracticeSession()
practice_session = s._public_ai_session
settings = public_ai.read_settings()


def show_error(exc):
    if isinstance(exc, public_ai.PracticeUnavailable):
        st.warning(str(exc))
    else:
        st.error("처리하지 못했습니다. 자료를 확인하고 잠시 후 다시 시도해 주세요. 기존 기록은 유지됩니다.")

st.title("꼬리에 꼬리 물기 🗣️")
st.caption("자료 업로드 → 연습 시작 → 직접 답변 → 꼬리질문")

with st.sidebar:
    st.subheader("연습 설정")
    audience = st.selectbox("질문하는 청중", ["일반 학생", "전공 교수", "심사위원"])
    difficulty = st.select_slider("질문 난이도", ["쉬움", "보통", "어려움"], value="보통")
    if st.button("모든 자료·대화 초기화"):
        # Reset practice data only: session identity and quota cannot be reset here.
        s.pages, s.runs, s.notices, s.data_label, s.active = [], [], [], '', 0
        s.revision += 1
        st.rerun()

if practice_session.pause_message:
    st.warning(practice_session.pause_message)
if public_ai.is_demo(settings, practice_session):
    st.info("현재는 문장 단서에 따른 규칙 기반 체험을 제공합니다. 실제 AI가 과학적 의미나 답변의 타당성을 분석하는 기능은 아닙니다.")

st.subheader("1. 발표 자료 준비")
presentations = st.file_uploader("발표 PDF 또는 PPTX", type=["pdf", "pptx"], accept_multiple_files=True)
papers = st.file_uploader("참고 논문 PDF (선택)", type=["pdf"], accept_multiple_files=True)
col1, col2 = st.columns(2)
with col1:
    if st.button("자료 적용", disabled=not presentations, use_container_width=True):
        try:
            pages, notices = load_uploads(presentations, papers)
            s.pages, s.notices, s.runs = pages, notices, []
            s.data_label = "직접 업로드한 자료"
            s.active = 0
            s.revision += 1
            st.success("자료 적용 완료! 아래에서 질문을 시작해 주세요.")
        except Exception as exc:
            notify_error(exc)
with col2:
    if st.button("예제 자료로 체험", use_container_width=True):
        s.pages, s.notices, s.runs = examples(), [], []
        s.data_label = "가상 예제 자료"
        s.active = 0
        s.revision += 1
        st.success("예제 자료를 불러왔어요.")

if s.pages:
    st.caption(f"{s.data_label} · {len(s.pages)}개 페이지/슬라이드 텍스트")
    for notice in s.notices:
        st.warning(notice)
    with st.expander("자료에서 추출한 텍스트 확인"):
        for p in s.pages:
            st.markdown(f"**{p.file} · {p.kind} {p.number}**")
            st.text(p.text[:2500] or "(텍스트 없음)")
else:
    st.info("발표 자료를 올리거나 예제 자료를 불러와 주세요.")

st.subheader("2. 꼬리질문 연습")
if st.button("연습 시작 · 첫 질문", disabled=not s.pages, type="primary"):
    try:
        with st.spinner("질문을 만들고 있어요…"):
            turn = public_ai.generate_question(settings, practice_session, s.pages, [], audience, difficulty)
        s.runs.append({"name":f"연습 {len(s.runs)+1}", "turns":[turn], "data_label":s.data_label,
                       "created_at":datetime.now(timezone.utc).isoformat()})
        s.active = len(s.runs)-1
        st.rerun()
    except Exception as exc:
        show_error(exc)

if s.runs:
    run_labels = [f'{i+1}. {r["name"]}' for i,r in enumerate(s.runs)]
    selected_run = st.selectbox("연습 기록 선택", run_labels, index=s.active)
    run_index = run_labels.index(selected_run)
    s.active = run_index
    run = s.runs[run_index]
    turns = run["turns"]
    with st.expander("지난 질문과 답변", expanded=False):
        for i, old in enumerate(turns[:-1], 1):
            st.markdown(f"**Q{i}. {old['question']['question']}**")
            st.write(old.get("answer",""))
    turn = turns[-1]
    question = turn["question"]
    st.markdown(f"### Q{len(turns)}. {question['question']}")
    st.caption("근거 구분: "+question["basis"])
    if question["basis"] == "근거 부족":
        st.warning("직접 인용이 부족합니다. 질문의 전제를 확인해 주세요.")
    st.caption("AI가 생성한 질문" if turn["mode"] == "OpenAI" else "규칙 기반 체험 질문 · 실제 AI 생성이 아닙니다")
    for citation in question["citations"]:
        p = next((x for x in s.pages if x.id == citation["page_id"]), None)
        if p:
            with st.expander(f"관련 자료: {p.file}, {p.kind} {p.number}"):
                st.write(citation["quote"])
                st.caption("인용 텍스트 확인만 수행했습니다. 질문의 과학적 타당성이 검증된 것은 아닙니다.")
    if not turn.get("answer"):
        with st.form(f"answer_{s.revision}_{run_index}_{len(turns)}"):
            answer = st.text_area("내 답변", height=170)
            if st.form_submit_button("답변 제출"):
                if len(answer) > 12000:
                    st.warning("답변은 12,000자 이하로 입력해 주세요.")
                elif answer.strip():
                    turn["answer"] = answer.strip()
                    st.rerun()
                else:
                    st.warning("답변을 먼저 입력해 주세요.")
    else:
        st.markdown("**내가 제출한 답변**")
        st.write(turn["answer"])
        if st.button("답변 피드백 보기"):
            try:
                with st.spinner("답변을 확인하고 있어요…"):
                    result, feedback_mode = public_ai.answer_feedback(settings, practice_session, s.pages, turn)
                    turn["feedback"], turn["feedback_mode"] = result, feedback_mode
            except Exception as exc:
                show_error(exc)
        if turn.get("feedback"):
            st.caption("AI 보조 피드백" if turn.get("feedback_mode",turn["mode"]) == "OpenAI" else "규칙 기반 체험 피드백 · 답변의 정확성을 판정하지 않습니다")
            for key, title in [("accuracy","정확성"), ("logic","논리성"), ("evidence","근거 충분성"), ("next_step","개선할 점")]:
                st.write(f"**{title}**: {turn['feedback'][key]}")
        new_topic = st.text_input("주제 변경 (선택사항, 비우면 현재 답변에 이어서)", max_chars=500)
        if st.button("꼬리질문 받기"):
            try:
                with st.spinner("다음 질문을 만들고 있어요…"):
                    turns.append(public_ai.generate_question(settings, practice_session, s.pages, turns, audience, difficulty, new_topic))
                st.rerun()
            except Exception as exc:
                show_error(exc)

if s.runs:
    st.subheader("3. 기록 저장")
    st.download_button("질문·답변 기록 다운로드", markdown(s.pages, s.runs),
                       file_name="kkokkomoo_qa.md", mime="text/markdown")
    st.download_button("전체 Q&A JSON", json.dumps(snapshot(s.pages,s.runs),ensure_ascii=False,indent=2),
                       file_name="qa_session.json", mime="application/json")

st.caption("문서·대화는 현재 연습 세션에서 처리됩니다. AI 기능이 제공되는 경우 선택된 문서 내용과 답변이 외부 AI 서비스로 전송됩니다. 연결 종료 전에 기록을 저장해 주세요.")
