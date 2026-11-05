"""Public presentation practice: no visitor credentials or technical controls."""
from datetime import datetime, timezone
import streamlit as st

from qa.documents import examples
from qa.ui_common import load_uploads, notify_error
from qa import gemini_access, engine
from qa.gemini_provider import GeminiFailure, DEFAULT_MODEL
from qa.media import choose, render
import json
from qa.public_reports import markdown, snapshot

st.set_page_config(page_title="꼬리에 꼬리 물기", page_icon="💬", layout="centered")
s = st.session_state
for key, default in {
    "pages": [], "runs": [], "data_label": "", "notices": [], "revision": 0, "active": 0
}.items():
    if key not in s:
        s[key] = default

for legacy in ('api_key','public_api_key_input','_public_ai_session'):
    s.pop(legacy,None)
if '_gemini_connection' not in s:
    s._gemini_connection=gemini_access.Connection()
connection=s._gemini_connection
if 'pdf_sources' not in s:
    s.pdf_sources={}
if 'demo_allowed' not in s:
    s.demo_allowed=False
images=()


def show_error(exc):
    if isinstance(exc,GeminiFailure):
        st.warning(str(exc))
    else:
        st.error('결과를 확인하지 못했습니다. 기록은 유지됩니다. 자료와 연결 상태를 확인해 주세요.')


def attach_key():
    entered=s.get('gemini_key_input','')
    s.gemini_key_input=''
    if not s.get('gemini_cost_consent',False):
        s.connection_notice='사용량·비용 및 서버 전송 안내에 동의해 주세요.'
        return
    try:
        ok=connection.attach(entered)
        s.connection_notice='모델 조회로 연결을 확인했습니다. 실제 생성·할당량은 첫 요청에서 확인됩니다.' if ok else '기본 모델을 이 키로 확인하지 못했습니다. 사용 가능한 대안을 직접 선택할 수 있습니다.'
    except GeminiFailure as exc:
        s.connection_notice=str(exc)


def disconnect():
    connection.disconnect()
    s.gemini_key_input=''
    s.connection_notice='연결을 해제하고 키를 삭제했습니다.'


def first_or_follow(turns,topic=''):
    if connection.ready:
        return gemini_access.generate(connection,s.pages,turns,audience,difficulty,topic,images)
    if not s.demo_allowed:
        raise GeminiFailure('missing_key','먼저 나의 Gemini를 연결하거나 규칙 체험을 선택해 주세요.')
    return engine.generate(s.pages,turns,audience,difficulty,topic,demo=True)


def analyze(turn):
    if connection.ready:
        result=gemini_access.feedback(connection,s.pages,turn,images)
        return result,'Gemini'
    if not s.demo_allowed:
        raise GeminiFailure('missing_key','나의 Gemini를 다시 연결하거나 규칙 체험을 선택해 주세요.')
    return engine.feedback(s.pages,turn,demo=True),'데모'

st.title("꼬리에 꼬리 물기 🗣️")
st.caption("자료 업로드 → 연습 시작 → 직접 답변 → 꼬리질문")

st.subheader('나의 Gemini 연결')
if connection.ready:
    st.success('나의 Gemini 연결됨 · 내 키가 속한 프로젝트의 할당량을 사용합니다.')
    with st.expander('연결 관리'):
        st.button('연결 해제',on_click=disconnect)
        if st.button('다른 빠른 연결 대안 확인'):
            try:
                connection.find_alternatives()
                st.rerun()
            except GeminiFailure as exc:
                show_error(exc)
else:
    st.link_button('Google AI Studio에서 개인 키 발급', 'https://aistudio.google.com/api-keys')
    st.caption('키는 이 앱 서버로 전달되어 세션 메모리에서만 처리됩니다. 파일·다운로드·브라우저 저장소에 영구 저장하지 않습니다. Google 로그인이나 Gemini 구독만으로 API 할당량이 연결되지는 않습니다.')
    st.info('무료 등급은 모델별 제한이 있고, 유료 프로젝트의 키는 비용이 발생할 수 있습니다. 키만으로 요금 등급을 판정하지 않습니다. 무료 서비스의 데이터 이용 조건도 확인하세요.')
    st.link_button('요금·무료 등급 확인','https://ai.google.dev/gemini-api/docs/pricing')
    st.link_button('Google 데이터 처리 조건','https://ai.google.dev/gemini-api/terms')
    with st.form('gemini_connection',clear_on_submit=True):
        st.text_input('나의 Gemini API 키',type='password',key='gemini_key_input')
        st.checkbox('키·자료가 서버와 Google로 전송되며 유료 프로젝트에서는 비용이 발생할 수 있음을 이해했습니다.',key='gemini_cost_consent')
        st.form_submit_button('연결하기',on_click=attach_key)
    if connection.pending_key and connection.alternatives:
        labels=[item['label'] for item in connection.alternatives]
        selected=st.selectbox('이 키에서 조회된 빠른 모델 대안',range(len(labels)),format_func=lambda i:labels[i])
        agreed=st.checkbox('대체 모델도 요금·할당량 조건이 다를 수 있음을 확인했습니다.')
        if st.button('선택한 대안으로 연결',disabled=not agreed):
            key=connection.pending_key; model=connection.alternatives[selected]['id']
            try:
                connection.attach(key,model)
                st.rerun()
            except GeminiFailure as exc:
                show_error(exc)
    elif connection.pending_key:
        st.warning('조회 가능한 Flash 대안이 없습니다. AI Studio에서 접근 권한을 확인해 주세요.')
    if connection.pending_key:
        st.button('연결 해제',on_click=disconnect)
    s.demo_allowed=st.checkbox('연결 없이 규칙 기반 체험으로 진행 (실제 AI 아님)',value=s.demo_allowed)
if s.get('connection_notice'):
    st.caption(s.connection_notice)

with st.sidebar:
    st.subheader('연습 설정')
    audience=st.selectbox('질문하는 청중',['일반 학생','전공 교수','심사위원'])
    difficulty=st.select_slider('질문 난이도',['쉬움','보통','어려움'],value='보통')
    if st.button('모든 자료·대화 초기화'):
        s.pages,s.runs,s.notices,s.pdf_sources,s.active=[],[],[],{},0
        s.revision+=1
        st.rerun()
if not connection.ready:
    st.info('연결 전 규칙 체험은 문장 단서·템플릿만 사용합니다. 실제 AI의 과학적 의미 분석이 아닙니다.')

st.subheader("1. 발표 자료 준비")
presentations = st.file_uploader("발표 PDF 또는 PPTX", type=["pdf", "pptx"], accept_multiple_files=True)
papers = st.file_uploader("참고 논문 PDF (선택)", type=["pdf"], accept_multiple_files=True)
col1, col2 = st.columns(2)
with col1:
    if st.button("자료 적용", disabled=not presentations, use_container_width=True):
        try:
            pages, notices = load_uploads(presentations, papers, allow_visual=True)
            s.pdf_sources={}
            from qa.documents import extract
            for role,uploads in [('발표',presentations),('참고',papers)]:
                for upload in uploads:
                    if upload.name.lower().endswith('.pdf'):
                        blob=upload.getvalue()
                        extracted,_=extract(upload.name,blob,role)
                        for page in extracted:
                            s.pdf_sources[page.id]=blob
            s.pages, s.notices, s.runs = pages, notices, []
            s.data_label = "직접 업로드한 자료"
            s.active = 0
            s.revision += 1
            st.success("자료 적용 완료! 아래에서 질문을 시작해 주세요.")
        except Exception as exc:
            notify_error(exc)
with col2:
    if st.button("예제 자료로 체험", use_container_width=True):
        s.pages, s.notices, s.runs, s.pdf_sources = examples(), [], [], {}
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

if s.pdf_sources:
    use_visual=st.checkbox('그래프·이미지가 있는 PDF 페이지도 분석 (전송량·사용량 증가)')
    if use_visual:
        pdf_pages=[p for p in s.pages if p.id in s.pdf_sources]
        by_id={p.id:p for p in pdf_pages}
        selected_ids=st.multiselect('시각 분석할 핵심 페이지 (최대 4)',list(by_id),default=choose(pdf_pages),
            format_func=lambda i:f'{by_id[i].file} · 실제 페이지 {by_id[i].number}')
        try:
            images=render(s.pdf_sources,s.pages,selected_ids)
        except Exception:
            st.warning('페이지 이미지를 준비하지 못했습니다. 페이지 수/문서를 확인해 주세요.')
        st.caption(f'{len(images)}페이지 이미지를 준비했습니다. 해석은 모델의 추론이며 축·범례·해상도가 불명확하면 검토가 필요합니다.')
    else:
        st.caption('현재 텍스트만 분석합니다. 그래프 이미지를 읽었다고 가정하지 않습니다.')
if any(p.file.lower().endswith('.pptx') for p in s.pages):
    st.caption('PPTX는 텍스트만 분석합니다. 그래프 분석은 사용자가 PDF로 내보낸 후 업로드해 주세요.')

st.subheader("2. 꼬리질문 연습")
if st.button("연습 시작 · 첫 질문", disabled=not s.pages or not(connection.ready or s.demo_allowed), type="primary"):
    try:
        with st.spinner("질문을 만들고 있어요…"):
            turn = first_or_follow([])
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
    st.caption("AI가 생성한 질문" if turn["mode"] in ("OpenAI","Gemini") else "규칙 기반 체험 질문 · 실제 AI 생성이 아닙니다")
    for citation in question["citations"]:
        p = next((x for x in s.pages if x.id == citation["page_id"]), None)
        if p:
            with st.expander(f"관련 자료: {p.file}, {p.kind} {p.number}"):
                st.write(citation.get('detail') if citation.get('kind')=='visual' else citation['quote'])
                if citation.get('kind')=='visual':
                    st.warning('모델의 시각 해석 · 원문 문자열 대조로 검증한 근거가 아닙니다.')
                if citation.get("kind","text")=="text":
                    st.caption("인용 텍스트 확인만 수행했습니다. 질문의 과학적 타당성이 검증된 것은 아닙니다.")
    if not turn.get("answer") or turn.get("revise"):
        with st.form(f"answer_{s.revision}_{run_index}_{len(turns)}"):
            answer = st.text_area("내 답변",value=turn.get("answer",''), height=170)
            if st.form_submit_button("답변 제출"):
                if len(answer) > 12000:
                    st.warning("답변은 12,000자 이하로 입력해 주세요.")
                elif answer.strip():
                    turn['answer']=answer.strip();turn['revise']=False
                    for name in ('feedback','answer_review','review_fingerprint'):
                        turn.pop(name,None)
                    try:
                        turn['feedback'],turn['feedback_mode']=analyze(turn)
                        st.rerun()
                    except Exception as exc:
                        show_error(exc)
                else:
                    st.warning("답변을 먼저 입력해 주세요.")
    else:
        st.markdown("**내가 제출한 답변**")
        st.write(turn["answer"])
        if st.button("답변 피드백 보기"):
            try:
                with st.spinner("답변을 확인하고 있어요…"):
                    result, feedback_mode = (turn['feedback'],turn.get('feedback_mode',turn['mode'])) if turn.get('feedback') else analyze(turn)
                    turn["feedback"], turn["feedback_mode"] = result, feedback_mode
            except Exception as exc:
                show_error(exc)
        if turn.get("feedback"):
            st.caption("AI 보조 피드백" if turn.get("feedback_mode",turn["mode"]) in ("OpenAI","Gemini") else "규칙 기반 체험 피드백 · 답변의 정확성을 판정하지 않습니다")
            for key, title in [("accuracy","정확성"), ("logic","논리성"), ("evidence","근거 충분성"), ("next_step","개선할 점")]:
                st.write(f"**{title}**: {turn['feedback'][key]}")
        if turn.get('answer_review'):
            review=turn['answer_review']
            st.write('답변 관련성: '+{'direct':'직접 답변','partial':'부분 답변','off_topic':'주제 이탈'}[review['relevance']]+' · '+review['explanation'])
            if review['relevance']=='off_topic' or review['accuracy']=='incorrect':
                st.warning('원래 질문의 핵심을 확인하고 답변을 수정해 보세요.')
        if st.button('답변 수정하기'):
            turn['revise']=True
            st.rerun()
        new_topic = st.text_input("주제 변경 (선택사항, 비우면 현재 답변에 이어서)", max_chars=500)
        if st.button("꼬리질문 받기"):
            try:
                with st.spinner("다음 질문을 만들고 있어요…"):
                    turns.append(first_or_follow(turns,new_topic))
                st.rerun()
            except Exception as exc:
                show_error(exc)

if s.runs:
    st.subheader("3. 기록 저장")
    st.download_button("질문·답변 기록 다운로드", markdown(s.pages, s.runs),
                       file_name="kkokkomoo_qa.md", mime="text/markdown")
    st.download_button("전체 Q&A JSON", json.dumps(snapshot(s.pages,s.runs),ensure_ascii=False,indent=2),
                       file_name="qa_session.json", mime="application/json")

st.caption("문서·대화는 현재 연습 세션에서 처리됩니다. AI 기능이 제공되는 경우 선택된 문서 내용과 답변이 Google Gemini로 전송됩니다. 연결 종료 전에 기록을 저장해 주세요.")
