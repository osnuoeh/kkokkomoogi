"""Public Q&A trainer. Users provide their own API credentials per session; no owner key is used."""
from datetime import datetime, timezone
import streamlit as st

from qa.documents import examples
from qa.ui_common import load_uploads
from qa.prompts import PRODUCTION_INSTRUCTIONS
from qa.engine import generate, feedback
import json
from qa.public_reports import markdown, snapshot

st.set_page_config(page_title="꼬리에 꼬리 물기", page_icon="💬", layout="centered")
s = st.session_state
for key, default in {
    "pages": [], "runs": [], "api_key": "", "data_label": "", "notices": [], "revision": 0, "active": 0
}.items():
    if key not in s:
        s[key] = default

def show_error(exc):
    # Do not expose exception details from remote SDK responses.
    if isinstance(exc, ValueError) and type(exc).__module__ == "builtins":
        st.error(str(exc))
    else:
        st.error("처리하지 못했어요. 문서 형식이나 개인 API 키·잔액·모델 지원 여부를 확인해 주세요.")

st.title("꼬리에 꼬리 물기 🗣️")
st.caption("발표 자료를 바탕으로 질문하고, 답변에서 다시 질문하는 연습실")

with st.sidebar:
    st.subheader("AI 사용 방법")
    option = st.radio("연습 방식", ["무료 데모 체험", "내 OpenAI API 키로 AI 연습"])
    demo = option == "무료 데모 체험"
    if demo:
        st.info("무료 데모는 정해진 규칙으로 질문을 연결해요. 답변의 의미를 AI가 분석하지는 않습니다.")
    else:
        st.caption("개인 OpenAI API 키를 입력해 주세요. ChatGPT Plus 구독과 별개로 API 이용 요금이 발생할 수 있어요.")
        st.link_button("내 API 키 만들기", "https://platform.openai.com/api-keys")
        with st.form('visitor_key_connection', clear_on_submit=True):
            entered = st.text_input("개인 API 키", type="password", key="public_api_key_input",
                help="이 브라우저 세션에서만 사용하며 파일·GitHub·보고서에 저장하지 않습니다.")
            connected = st.form_submit_button("이 키로 연결")
            if connected:
                if entered.strip():
                    s.api_key = entered.strip()
                    st.success("키를 세션에 적용했어요. 실제 사용 가능 여부는 첫 요청에서 확인됩니다.")
                else:
                    st.warning("키를 입력해 주세요.")
        if s.api_key:
            st.success("개인 키 입력됨 (API 동작 여부 미확인)")
            def disconnect_key():
                s.api_key = ""
                s.public_api_key_input = ""
            st.button("키 연결 해제", on_click=disconnect_key)
        else:
            st.info("AI 연습을 사용하려면 본인의 API 키를 연결해 주세요.")
    audience = st.selectbox("질문하는 청중", ["일반 학생", "전공 교수", "심사위원"])
    difficulty = st.select_slider("질문 난이도", ["쉬움", "보통", "어려움"], value="보통")
    if st.button("모든 자료·대화 초기화"):
        s.clear()
        st.rerun()

if not demo and not s.api_key:
    st.warning("AI 연습에는 개인 API 키가 필요합니다. 키가 없다면 왼쪽에서 무료 데모를 선택해 주세요.")

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
            show_error(exc)
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
can_use = demo or bool(s.api_key)
if st.button("연습 시작 · 첫 질문", disabled=not s.pages or not can_use, type="primary"):
    try:
        with st.spinner("질문을 만들고 있어요…"):
            turn = generate(s.pages, [], audience, difficulty, prompt=PRODUCTION_INSTRUCTIONS, demo=demo,
                            api_key=None if demo else s.api_key)
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
    if turn["mode"] == "데모":
        st.caption("규칙 기반 데모 질문 — 실제 AI 생성 아님")
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
        if st.button("답변 피드백 보기", disabled=not(turn["mode"] == "데모" or s.api_key)):
            try:
                with st.spinner("답변을 확인하고 있어요…"):
                    turn["feedback"] = feedback(s.pages, turn, demo=turn["mode"] == "데모",
                                                api_key=s.api_key if turn["mode"] != "데모" else None)
            except Exception as exc:
                show_error(exc)
        if turn.get("feedback"):
            for key, title in [("accuracy","정확성"), ("logic","논리성"), ("evidence","근거 충분성"), ("next_step","개선할 점")]:
                st.write(f"**{title}**: {turn['feedback'][key]}")
        new_topic = st.text_input("주제 변경 (선택사항, 비우면 현재 답변에 이어서)")
        if st.button("꼬리질문 받기", disabled=not can_use):
            try:
                with st.spinner("다음 질문을 만들고 있어요…"):
                    turns.append(generate(s.pages, turns, audience, difficulty, topic=new_topic,
                                          prompt=PRODUCTION_INSTRUCTIONS, demo=demo, api_key=None if demo else s.api_key))
                st.rerun()
            except Exception as exc:
                show_error(exc)

if s.runs:
    st.subheader("3. 기록 저장")
    st.download_button("질문·답변 기록 다운로드", markdown(s.pages, s.runs),
                       file_name="kkokkomoo_qa.md", mime="text/markdown")
    st.download_button("전체 Q&A JSON", json.dumps(snapshot(s.pages,s.runs),ensure_ascii=False,indent=2),
                       file_name="qa_session.json", mime="application/json")

st.caption("공개 앱은 개발자 API 키를 사용하지 않습니다. 입력한 키와 문서·대화는 세션에서만 취급되며, AI 연습 시 선택된 문서 내용과 답변이 OpenAI에 전송됩니다. 스캔 PDF·이미지·그래프의 내용은 자동 해석하지 않습니다.")
