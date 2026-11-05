import json
import os
from datetime import datetime, timezone
import pandas as pd
import streamlit as st
from qa.documents import examples
from qa.prompts import PRODUCTION_INSTRUCTIONS
from qa.ui_common import notify_error, load_uploads
from qa.dev_guard import require_local_development
from qa.experiments import execute_suite, parse_scenario, score_rows, comparison_rows
from qa.experiment_reports import suite_markdown, suite_csv, coverage_csv
from qa.history import load_history, sample_csv, csv_bytes
from qa.engine import generate, feedback, quality, version, BASE_PROMPT
from qa.evaluation import compare
from qa.models import METRICS
from qa.reports import markdown, evaluation_csv

st.set_page_config(page_title='개발자 검증 도구', page_icon='🧪', layout='wide')
require_local_development()
s = st.session_state
for key, value in {'pages':[], 'references':[], 'runs':[], 'active':0, 'prompt':PRODUCTION_INSTRUCTIONS,
                   'data_label':'사용자 업로드 자료 (검증 전)', 'comparison':None, 'revision':0}.items():
    if key not in s:
        s[key] = value

def current_run():
    return s.runs[s.active] if s.runs else None

def new_run(name):
    s.runs.append({'name':name or f'실험 {len(s.runs)+1}', 'turns':[], 'data_label':s.data_label,
                   'created_at':datetime.now(timezone.utc).isoformat()})
    s.active = len(s.runs)-1
    s.comparison = None

def first_question(name, demo, audience, difficulty, topic=''):
    t = generate(s.pages, [], audience, difficulty, topic, s.prompt, demo)
    new_run(name)
    current_run()['turns'].append(t)

st.title('개발자 전용 Evaluation & Debugging')
st.warning('개발용 실험 결과입니다. 실제 사용자 앱의 품질을 입증한 결과가 아닙니다.')
st.caption('발표의 주장, 근거, 빈틈을 한 질문씩 확인하는 Q&A 연습실')
with st.sidebar:
    st.subheader('연습 설정')
    mode = st.radio('질문 생성 방식', ['데모 (API 키 불필요)', 'OpenAI API'])
    demo = mode.startswith('데모')
    if demo:
        st.info('규칙 기반 체험입니다. 의미 이해·정확성·성능을 검증한 결과가 아닙니다. 수정 프롬프트는 기록되지만 데모 생성에는 적용되지 않습니다.')
    else:
        st.caption('문서의 선택된 텍스트와 답변이 OpenAI API로 전송됩니다. 과거 질문은 비교 실행 시 독립 평가 요청에만 전송됩니다.')
        if not os.getenv('OPENAI_API_KEY'):
            st.warning('OPENAI_API_KEY가 없습니다. 환경변수 설정 후 앱을 재시작해 주세요.')
    audience = st.selectbox('청중 유형', ['일반 학생', '전공 교수', '심사위원'])
    difficulty = st.select_slider('난이도', ['쉬움', '보통', '어려움'], value='보통')
    st.caption('자료·답변·평가는 현재 세션 메모리에만 보관합니다. 종료 전 기록을 다운로드하세요.')
    if st.button('전체 자료 및 기록 초기화'):
        s.clear()
        st.rerun()

tabs = st.tabs(['자료 관리', 'Q&A 연습', '질문 검증 및 디버깅', '기록 다운로드', '자동 반복 평가'])
with tabs[0]:
    st.subheader('1. 발표와 참고 문헌 등록')
    presentations = st.file_uploader('발표 자료 PPTX / PDF', type=['pptx','pdf'], accept_multiple_files=True)
    papers = st.file_uploader('참고 논문 PDF', type=['pdf'], accept_multiple_files=True)
    st.caption('파일당 25MB·500페이지 이하. 표 텍스트는 추출하지만 이미지·수식·그래프의 시각적 의미는 분석하지 않습니다.')
    if st.button('업로드한 자료 적용', disabled=not presentations):
        try:
            pages, warnings = load_uploads(presentations, papers)
            s.pages = pages
            s.runs, s.references, s.comparison, s.active = [], [], None, 0
            s.data_label = '사용자 업로드 자료 (AI 보조 평가, 실제 타당성 검증 전)'
            s.revision += 1
            s.pop('suite', None)
            for w in warnings:
                st.warning(w)
            st.success('자료를 적용했습니다. 자료 혼합을 방지하기 위해 이전 실험과 과거 질문 목록을 초기화했습니다.')
        except Exception as exc:
            notify_error(exc)
    if st.button('예제 자료로 체험하기'):
        s.pages = examples()
        s.references = load_history('example.csv', sample_csv())
        s.runs, s.comparison, s.active = [], None, 0
        s.data_label = '가상 예제 데이터 — 실제 발표 검증 결과 아님'
        s.revision += 1
        s.pop('suite', None)
        st.success('가상의 과적합 발표·문헌·질문 예제를 불러왔습니다.')
    if s.pages:
        st.info(s.data_label)
        st.dataframe(pd.DataFrame([{'파일':p.file, '구분':p.role, '위치':f'{p.kind} {p.number}',
                                    '글자 수':len(p.text)} for p in s.pages]), hide_index=True)
        with st.expander('추출 원문 확인'):
            for p in s.pages:
                st.text(f'{p.file} · {p.kind} {p.number} [{p.id}]')
                st.text(p.text or '(텍스트 없음: OCR 필요)')
    st.subheader('2. 과거 질문 등록 — 생성기에는 전달되지 않음')
    st.download_button('예시 CSV 다운로드', sample_csv(), 'example_questions.csv', 'text/csv')
    st.caption('CSV/XLSX: question(또는 질문 내용) 필수. topic, material, source, type, answer, feedback, reference, page 선택. UTF-8 CSV 권장. TXT/MD는 제목을 제외한 한 줄을 한 질문으로 읽습니다.')
    files = st.file_uploader('과거 예상질문·실제 질문·당시 답변과 피드백', type=['csv','xlsx','txt','md'], accept_multiple_files=True)
    if st.button('과거 질문 목록 적용', disabled=not files):
        try:
            refs = [r for f in files for r in load_history(f.name, f.getvalue())]
            s.references, s.comparison = refs, None
            s.pop('suite', None)
            st.success(f'{len(refs)}개 질문을 평가 전용 목록으로 저장했습니다.')
        except Exception as exc:
            notify_error(exc)
    if s.references:
        with st.expander('평가 전용 과거 질문 확인'):
            st.dataframe(pd.DataFrame([r.model_dump() for r in s.references]), hide_index=True)

with tabs[1]:
    st.subheader('한 번에 하나의 질문')
    st.caption('기존 질문을 채점용으로 등록해도 첫 질문 생성에는 사용하지 않습니다. 답변 제출 전에는 피드백을 표시하지 않습니다.')
    run_name = st.text_input('새 실험 이름', value=f'실험 {len(s.runs)+1}')
    if st.button('새 연습 시작 / 첫 질문 생성', disabled=not s.pages):
        try:
            with st.spinner('자료를 읽고 질문을 생성합니다…'):
                first_question(run_name, demo, audience, difficulty)
            st.rerun()
        except Exception as exc:
            notify_error(exc)
    if s.runs:
        run_labels = [f'{i+1}. {r["name"]}' for i,r in enumerate(s.runs)]
        selected_run = st.selectbox('진행할 실험', run_labels, index=s.active)
        chosen = run_labels.index(selected_run)
        if chosen != s.active:
            s.active, s.comparison = chosen, None
            st.rerun()
        run = current_run()
        turns = run['turns']
        st.caption(run['data_label'])
        with st.expander('이전 Q&A 기록'):
            for i,t in enumerate(turns[:-1],1):
                st.markdown(f'**Q{i}.** {t["question"]["question"]}')
                st.text(t.get('answer',''))
                if t.get('feedback'):
                    st.json(t['feedback'])
        t = turns[-1]
        q = t['question']
        st.markdown(f'### Q{len(turns)}. {q["question"]}')
        st.caption(f'{t["mode"]} · {t["audience"]} · {t["difficulty"]} · {t["prompt_version"]}')
        st.write(f'근거 구분: {q["basis"]}')
        if q['basis'] == '근거 부족':
            st.warning('유효한 직접 인용이 부족합니다. 질문의 전제와 원문을 확인해 주세요.')
        page_map = {p.id:p for p in s.pages}
        for c in q['citations']:
            p = page_map[c['page_id']]
            with st.expander(f'근거: {p.file} · {p.kind} {p.number}'):
                st.text(c['quote'])
                st.caption('원문에 인용문이 존재하는지 검사했습니다. 질문 전체의 과학적 정확성을 보장하지 않습니다.')
        if not t.get('answer'):
            with st.form(f'answer_{s.revision}_{s.active}_{len(turns)}'):
                answer = st.text_area('발표자로서 직접 답변해 주세요', height=170)
                submitted = st.form_submit_button('답변 제출')
                if submitted:
                    if not answer.strip():
                        st.warning('답변을 입력해 주세요.')
                    elif len(answer) > 12000:
                        st.warning('답변은 12,000자 이하로 입력해 주세요.')
                    else:
                        t['answer'] = answer.strip()
                        s.comparison = None
                        st.rerun()
        else:
            st.markdown('**제출한 답변**')
            st.text(t['answer'])
            st.caption(q['connection'])
            if st.button('답변 피드백 받기 / 다시 평가'):
                try:
                    with st.spinner('답변을 분석합니다…'):
                        t['feedback'] = feedback(s.pages, t, demo)
                        t['feedback_mode'] = '데모' if demo else 'OpenAI'
                except Exception as exc:
                    notify_error(exc)
            if t.get('feedback'):
                st.caption('AI 보조 피드백 · ' + t.get('feedback_mode',t['mode']))
                for key, label in [('accuracy','정확성'),('logic','논리성'),('evidence','근거 충분성'),('next_step','개선 제안')]:
                    st.write(f'{label}: {t["feedback"][key]}')
            new_topic = st.text_input('새 주제로 전환하려면 주제 입력 (비우면 현재 답변의 꼬리질문)')
            if st.button('다음 질문 생성'):
                try:
                    with st.spinner('실제 답변과 연결되는 질문을 생성합니다…'):
                        turns.append(generate(s.pages, turns, audience, difficulty, new_topic, s.prompt, demo))
                        s.comparison = None
                    st.rerun()
                except Exception as exc:
                    notify_error(exc)
        st.caption(f'질문 생성에 사용한 문서 발췌: {len(t["context_ids"])}개 · {t["context_chars"]:,}자. 긴 자료는 일부만 사용하므로 전체 개념을 다루지 못할 수 있습니다.')

with tabs[2]:
    st.subheader('질문 검증 및 디버깅')
    st.warning('AI 점수와 의미 비교는 보조 평가입니다. 객관적 판단이 어려우면 검토 필요로 표시하고 사람이 수정해 주세요.')
    run = current_run()
    if not run:
        st.info('자료를 등록한 뒤 Q&A 연습에서 첫 질문을 생성하세요.')
    else:
        turns = run['turns']
        st.write(f'비교 대상 실험: {run["name"]}')
        if st.button('기존 질문과 생성 질문 비교', disabled=not s.references):
            try:
                with st.spinner('생성이 끝난 질문을 독립 평가 경로에서 비교합니다…'):
                    s.comparison = {'run':run['name'], 'mode':'데모' if demo else 'OpenAI',
                                    'result':compare(s.references, turns, demo)}
            except Exception as exc:
                notify_error(exc)
        if s.comparison:
            result = s.comparison['result']
            st.caption(s.comparison['mode'] + ' · ' + result['limitations'])
            rows = [{'과거 질문':s.references[m['reference_index']].question,
                     '개념':m['concept'], '판정':m['status'],
                     '생성 질문':' / '.join(f'Q{j+1}: {turns[j]["question"]["question"]}' for j in m['generated_indices']),
                     '이유':m['reason']} for m in result['matches']]
            st.dataframe(pd.DataFrame(rows), hide_index=True)
            st.bar_chart(pd.Series([m['status'] for m in result['matches']]).value_counts())
            st.download_button('질문 비교 CSV 다운로드',csv_bytes(rows),'comparison.csv','text/csv')
        idx = st.selectbox('검토할 생성 질문', range(len(turns)), format_func=lambda i:f'Q{i+1}. {turns[i]["question"]["question"][:80]}')
        target = turns[idx]
        st.text(target['question']['question'])
        if st.button('선택 질문 품질 평가'):
            try:
                with st.spinner('원문과 이전 답변을 바탕으로 평가합니다…'):
                    target['quality'] = quality(s.pages, turns, idx, demo)
                    target['quality_mode'] = '데모' if demo else 'OpenAI'
            except Exception as exc:
                notify_error(exc)
        if target.get('quality'):
            st.caption('평가 모드: ' + target.get('quality_mode',target['mode']))
            st.dataframe(pd.DataFrame(target['quality']['grades']).rename(columns={'metric':'평가 항목','score':'AI 점수 (빈 값=검토 필요)','reason':'판정 근거'}),hide_index=True)
            st.write('발견 문제: ' + (' / '.join(target['quality']['problems']) or '별도 지적 없음 (무오류 보장 아님)'))
            st.write('개선 제안: ' + target['quality']['suggestion'])
        with st.form(f'review_{s.revision}_{s.active}_{idx}'):
            scores = {}
            for metric in METRICS:
                choices = ['검토 필요',1,2,3,4,5]
                old = target.get('human_scores',{}).get(metric,'검토 필요')
                scores[metric] = st.selectbox('사람 평가 · '+metric,choices,index=choices.index(old))
            error_types = ['중복 질문','근거 부족','논리적 비약','과학적 오류','자료 무관','지나치게 단순','기타']
            errors = st.multiselect('오류 유형',error_types,default=target.get('errors',[]))
            note = st.text_area('사람 피드백 / 개선 방향',value=target.get('human_feedback',''))
            if st.form_submit_button('사람 평가 저장'):
                target['human_scores'], target['errors'], target['human_feedback'] = scores, errors, note
                st.success('AI 원점수와 별도로 사람 평가를 저장했습니다.')
    st.divider()
    st.subheader('생성 프롬프트 수정 후 재테스트')
    with st.expander('고정 핵심 규칙 확인'):
        st.code(BASE_PROMPT,language=None)
    prompt = st.text_area('추가 생성 지침 (과거 질문 정답을 붙여 넣지 마세요)', value=s.prompt,
                          help='예: 답변의 주장 하나를 고르고, 그 주장의 적용 조건을 묻는 단일 질문을 생성한다.',max_chars=6000)
    if st.button('프롬프트 버전 저장'):
        s.prompt = prompt
        st.success('저장: ' + version(s.prompt))
    st.caption('현재 저장 버전: ' + version(s.prompt))
    if st.button('저장한 프롬프트로 새 실험 생성',disabled=not s.pages):
        try:
            with st.spinner('새 프롬프트로 첫 질문을 생성합니다…'):
                first_question(f'재테스트 {len(s.runs)+1} · {version(s.prompt)}',demo,audience,difficulty)
            st.rerun()
        except Exception as exc:
            notify_error(exc)
    if len(s.runs) >= 2:
        st.caption('서로 다른 실험의 점수 평균입니다. 같은 자료·청중·난이도·답변 조건을 맞추고 해석하세요. 통계적 성능 개선을 입증하지 않습니다.')
        summary = []
        for r in s.runs:
            for metric in METRICS:
                values = [g['score'] for t in r['turns'] for g in (t.get('quality') or {}).get('grades',[])
                          if g['metric'] == metric and g['score'] is not None]
                human = [t.get('human_scores',{}).get(metric) for t in r['turns']]
                human = [v for v in human if isinstance(v,int)]
                summary.append({'실험':r['name'],'평가 항목':metric,'AI 평균':sum(values)/len(values) if values else None,
                                'AI 표본 수':len(values), '사람 평균':sum(human)/len(human) if human else None,
                                '사람 표본 수':len(human), '질문 수':len(r['turns'])})
        df = pd.DataFrame(summary)
        st.dataframe(df,hide_index=True)
        plot = df.dropna(subset=['AI 평균'])
        if not plot.empty:
            st.bar_chart(plot.pivot(index='평가 항목',columns='실험',values='AI 평균'))

with tabs[3]:
    st.subheader('기록을 내 컴퓨터에 저장')
    st.caption('세션이 종료되면 서버 메모리 기록이 사라집니다. 다운로드 파일에도 발표·답변 정보가 포함되므로 공개 저장소에 올리지 마세요.')
    report = markdown(s.pages,s.runs,s.references,s.comparison)
    st.download_button('전체 Q&A·평가 Markdown',report,'qa_report.md','text/markdown')
    st.download_button('질문 품질 평가 CSV',evaluation_csv(s.runs),'evaluation.csv','text/csv')
    snapshot = {'schema_version':1,'data_label':s.data_label,'pages':[p.model_dump() for p in s.pages],
                'runs':s.runs,'references':[r.model_dump() for r in s.references],
                'comparison':s.comparison,'prompt':s.prompt}
    st.download_button('전체 원문·질문·답변·평가 JSON',json.dumps(snapshot,ensure_ascii=False,indent=2),
                       'session.json','application/json')
    st.caption('JSON은 분석 및 보관용입니다. 현재 버전에서는 세션 복원 업로드를 지원하지 않습니다.')
    st.download_button('예시 질문 CSV',sample_csv(),'example_questions.csv','text/csv',key='template_again')

with tabs[4]:
    st.subheader('같은 조건으로 수정 전후 자동 반복 평가')
    st.caption('답변 없이 독립 첫 질문을 반복 생성하거나, 준비한 답변을 순서대로 재생합니다. 모든 생성이 끝난 뒤 독립 평가합니다.')
    dataset_name = st.text_input('데이터셋 이름', value='로컬 검증 데이터셋')
    repeats = st.number_input('버전별 반복 횟수', min_value=1, max_value=5, value=2)
    baseline = st.text_area('기준 생성 지침', value=PRODUCTION_INSTRUCTIONS, max_chars=6000)
    candidate = st.text_area('후보 생성 지침', value=s.prompt, max_chars=6000)
    st.caption(f'기준 버전: {version(baseline)} / 후보 버전: {version(candidate)}')
    replay = st.checkbox('준비된 발표자 답변 시나리오 재생')
    scenario_file = st.file_uploader('발표자 답변 시나리오 JSON (평가 질문과 별도)', type=['json'])
    st.caption('형식: [{"answer":"첫 질문 후 재생할 답변", "note":"개발자 메모"}]. 1~5개 답변만 지원합니다. note와 아직 재생하지 않은 답변은 생성기에 전달하지 않습니다.')
    scenario_example = json.dumps([{'answer':'검증 손실이 증가했기 때문에 과적합을 의심합니다.', 'note':'가상의 시연 답변'},
                                   {'answer':'외부 데이터 검증이 없어 일반화를 단정할 수 없습니다.', 'note':'가상의 시연 답변'}],ensure_ascii=False,indent=2)
    st.download_button('가상 답변 시나리오 예시 다운로드',scenario_example,'example_scenario.json','application/json')
    steps = []
    scenario_valid = not replay
    if replay:
        st.warning('시나리오 답변은 고정 재생입니다. 생성된 질문과 답변이 맞는지 검토하세요. 실제 사용자 답변 기반 품질과 동일하게 해석하지 마세요.')
        if scenario_file:
            try:
                steps = parse_scenario(scenario_file.getvalue())
                scenario_valid = True
            except Exception as exc:
                notify_error(exc)
    calls = 2 * int(repeats) * (2*(len(steps)+1)+1)
    st.caption(f'최대 {calls}회 모델 요청: 생성·질문별 품질 평가·실험별 기존 질문 비교 포함. 데모는 외부 요청이 없습니다.')
    if st.button('자동 반복 평가 실행',disabled=not s.pages or not s.references or not scenario_valid):
        try:
            bar = st.progress(0.0)
            s.suite = execute_suite(s.pages,s.references,baseline,candidate,int(repeats),audience,difficulty,
                                    demo,steps,dataset_name,s.data_label,bar.progress)
            st.success('개발용 반복 평가를 완료했습니다. 오류와 미채점도 결과에 포함합니다.')
        except Exception as exc:
            notify_error(exc)
    if s.get('suite'):
        suite = s.suite
        st.warning(suite['purpose'])
        st.caption(f'결과 데이터셋: {suite["dataset"]} / 고정 조건: {suite["condition_id"][:16]}')
        if suite['errors']:
            st.json(suite['errors'])
        df = pd.DataFrame(score_rows(suite))
        st.dataframe(df,hide_index=True)
        plot = df.dropna(subset=['점수 평균'])
        if not plot.empty:
            st.bar_chart(plot.pivot_table(index='항목',columns='구분',values='점수 평균',aggfunc='mean'))
        if suite.get('exact_duplicates'):
            st.write('문장 동일 중복 후보 (의미적 중복 판정은 별도 검토)')
            st.dataframe(pd.DataFrame(suite['exact_duplicates']),hide_index=True)
        st.caption('데모의 점수는 모두 검토 필요입니다. 같은 조건이어도 모델 출력은 달라질 수 있으며 통계적 개선을 입증하지 않습니다.')
        comparisons = comparison_rows(suite,s.references)
        if comparisons:
            st.dataframe(pd.DataFrame(comparisons),hide_index=True)
        for r in suite['runs']:
            with st.expander(f'{r["name"]} · {r["answer_source"]} · {r["status"]}'):
                for i,t in enumerate(r['turns'],1):
                    st.write(f'Q{i}. {t["question"]["question"]}')
                    st.text('재생 답변: '+(t['answer'] or '(답변 없음)'))
                    st.caption('근거 구분: '+t['question']['basis'])
        st.download_button('자동 평가 Markdown',suite_markdown(s.pages,s.references,suite),'dev_evaluation.md','text/markdown')
        st.download_button('자동 평가 CSV',suite_csv(suite),'dev_evaluation.csv','text/csv')
        st.download_button('개념 비교 CSV',coverage_csv(suite,s.references),'dev_coverage.csv','text/csv')
