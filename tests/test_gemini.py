import json
from pathlib import Path
import pytest
from qa import engine, gemini_provider as gp, gemini_access as access
from qa.documents import examples, extract
from qa.models import Page, Historical, Question, Citation
from qa.public_reports import snapshot
from qa.experiments import execute_suite
from qa.media import render
from streamlit.testing.v1 import AppTest

APP=Path(__file__).parents[1]/'app.py'


def connection(key='key-A'):
    c=access.Connection(); assert c.attach(key); return c


def test_separate_keys_and_no_owner_fallback(gemini_backend,monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY','OWNER_KEY');monkeypatch.setenv('GOOGLE_API_KEY','OWNER_KEY')
    a=connection('key-A'); b=connection('key-B')
    ta=access.generate(a,examples(),[],'전공 교수','어려움')
    tb=access.generate(b,examples(),[],'일반 학생','쉬움')
    assert [r['key'] for r in gemini_backend['requests']]==['key-A','key-B']
    assert ta['mode']==tb['mode']=='Gemini'
    assert gp.current.get() is None
    assert gemini_backend['requests'][0]['payload']['audience']=='전공 교수'
    empty=access.Connection()
    with pytest.raises(gp.GeminiFailure):
        access.generate(empty,examples(),[],'일반 학생','보통')
    assert len(gemini_backend['requests'])==2


def test_default_model_and_manual_alternative_only(gemini_backend):
    gemini_backend['missing_model']=True
    c=access.Connection()
    assert not c.attach('key-A')
    assert not c.ready and c.alternatives[0]['id']=='gemini-3.6-flash'
    assert not gemini_backend['requests']
    c.last_connect=0
    assert c.attach(c.pending_key,c.alternatives[0]['id'])
    access.generate(c,examples(),[],'일반 학생','보통')
    assert 'gemini-3.6-flash' in gemini_backend['requests'][0]['body']['contents'][0]['parts'][0]['text'] or c.model=='gemini-3.6-flash'


def test_invalid_key_is_not_stored(gemini_backend):
    c=access.Connection()
    with pytest.raises(gp.GeminiFailure) as e:
        c.attach('invalid-key')
    assert e.value.kind=='auth' and 'secret-details' not in str(e.value)
    assert not c.key and not c.ready


@pytest.mark.parametrize('code,kind',[(429,'quota'),(500,'request'),(403,'auth'),(401,'auth')])
def test_api_errors_do_not_retry_fallback_or_expose_raw_details(gemini_backend,code,kind):
    c=connection();gemini_backend['error']=code
    with pytest.raises(gp.GeminiFailure) as e:
        access.generate(c,examples(),[],'일반 학생','보통')
    assert e.value.kind==kind and 'SECRET' not in str(e.value)
    assert len(gemini_backend['requests'])==1 and gp.current.get() is None
    if code==403: assert not c.key


@pytest.mark.parametrize('status',['off_topic','incorrect','partial','supported'])
def test_review_then_correct_or_deepen_with_actual_answer(gemini_backend,status):
    c=connection();first=access.generate(c,examples(),[],'전공 교수','어려움')
    first['answer']='Li dendrite는 현미경으로 관찰합니다.'
    gemini_backend['review']=status
    feedback=access.feedback(c,examples(),first)
    assert first['answer_review']['claim']==first['answer']
    assert feedback['review']['relevance']==('off_topic' if status=='off_topic' else 'partial' if status=='partial' else 'direct')
    before=len(gemini_backend['requests'])
    follow=access.generate(c,examples(),[first],'전공 교수','어려움')
    if status in ('off_topic','incorrect'):
        assert '구분' in follow['question']['question']
        assert len(gemini_backend['requests'])==before  # Reuse AI-authored repair, no extra API call.
    else:
        assert len(gemini_backend['requests'])==before+1
        assert gemini_backend['requests'][-1]['payload']['answer_review']['action'] in ('clarify','deepen')
    assert first['answer'] in follow['question']['connection']
    assert follow['mode']=='Gemini'


def test_sdk_response_is_structured_and_no_sampling_parameters(gemini_backend):
    c=connection();access.generate(c,examples(),[],'일반 학생','보통')
    config=gemini_backend['requests'][0]['body']['generationConfig']
    assert config['responseMimeType']=='application/json'
    assert not any(x in config for x in ('temperature','topP','topK','candidateCount'))


def test_meter_and_disconnect_do_not_reset_usage(gemini_backend):
    c=connection();c.meter.max_calls=1
    access.generate(c,examples(),[],'일반 학생','보통')
    with pytest.raises(gp.GeminiFailure,match='한도'):
        access.generate(c,examples(),[],'일반 학생','보통')
    c.disconnect();assert not c.key and not c.pending_key and c.meter.calls==1


def test_pdf_images_are_actually_sent_and_visual_citations_not_text_verified(gemini_backend):
    from test_content_selection import synthetic_pdf
    data=synthetic_pdf();pages,_=extract('graph.pdf',data,'발표')
    image=render({pages[4].id:data},pages,[pages[4].id])
    assert image[0].number==5 and image[0].data[:2]==b'\xff\xd8'
    c=connection();turn=access.generate(c,pages,[],'전공 교수','어려움',images=image)
    parts=gemini_backend['requests'][0]['body']['contents'][0]['parts']
    assert any('inlineData' in p for p in parts)
    assert turn['question']['citations'][0]['kind']=='visual'
    assert turn['question']['basis']=='모델 추론'
    assert turn['question']['citations'][0]['page_id']==pages[4].id


def test_visual_page_without_text_can_be_analyzed_and_unknown_image_citation_rejected(gemini_backend):
    from qa.media import PageImage
    p=Page(id='image:2',file='scan.pdf',role='발표',number=2,kind='페이지',text='')
    image=PageImage(p.id,p.file,p.number,b'fake-image-for-mock-only')
    turn=access.generate(connection(),[p],[],'일반 학생','보통',images=(image,))
    assert turn['question']['citations'][0]['page_id']==p.id
    q=Question(question='곡선에서 손실의 원인은 무엇인가요?',focus='곡선',connection='',basis='원문 확인',citations=[Citation(page_id='fake',quote='',kind='visual',detail='추정')])
    assert engine.validate_citations(q,[]) .basis=='근거 부족'


def test_developer_suite_gemini_keeps_holdouts_out_of_generator(gemini_backend):
    refs=[Historical(question='HOLDOUT_PRIVATE_91')]
    with gp.use(gp.Scope('dev-key',meter=gp.Meter(interval=0))):
        suite=execute_suite(examples(),refs,'','엄밀한 반응 메커니즘',repeats=1,demo=False)
    assert suite['conditions']['provider']=='Gemini'
    assert not suite['errors']
    for r in gemini_backend['requests']:
        if r['schema']=='Question':
            assert 'HOLDOUT_PRIVATE_91' not in json.dumps(r['payload'])
    assert any(r['schema']=='Comparison' and 'HOLDOUT_PRIVATE_91' in json.dumps(r['payload']) for r in gemini_backend['requests'])


def press(at,label):
    return next(x for x in at.button if x.label==label)


def ui_connect(at,key):
    next(x for x in at.text_input if x.label=='나의 Gemini API 키').set_value(key)
    next(x for x in at.checkbox if x.label.startswith('키·자료가')).check()
    press(at,'연결하기').click().run()
    return at


def test_ui_connection_answer_review_edit_disconnect_and_download(gemini_backend):
    at=AppTest.from_file(str(APP),default_timeout=20).run()
    ui_connect(at,'key-A')
    assert at.session_state['_gemini_connection'].ready and at.session_state['gemini_key_input']==''
    press(at,'예제 자료로 체험').click().run();press(at,'연습 시작 · 첫 질문').click().run()
    gemini_backend['review']='off_topic'
    next(x for x in at.text_area if x.label=='내 답변').set_value('Li dendrite를 현미경으로 관찰합니다.')
    press(at,'답변 제출').click().run()
    assert at.session_state['runs'][0]['turns'][0]['answer_review']['relevance']=='off_topic'
    assert at.warning
    press(at,'답변 수정하기').click().run()
    gemini_backend['review']='supported'
    next(x for x in at.text_area if x.label=='내 답변').set_value('전해액 환원 반응으로 SEI가 형성됩니다.')
    press(at,'답변 제출').click().run();press(at,'꼬리질문 받기').click().run()
    assert len(at.session_state['runs'][0]['turns'])==2 and not at.exception
    exported=json.dumps(snapshot(at.session_state['pages'],at.session_state['runs']))
    assert 'key-A' not in exported and 'gemini-3.8' not in exported
    press(at,'연결 해제').click().run()
    assert not at.session_state['_gemini_connection'].key and not at.exception


def test_ui_two_visitors_have_isolated_keys_documents_and_histories(gemini_backend):
    a=ui_connect(AppTest.from_file(str(APP),default_timeout=20).run(),'key-A')
    b=ui_connect(AppTest.from_file(str(APP),default_timeout=20).run(),'key-B')
    press(a,'예제 자료로 체험').click().run();press(a,'연습 시작 · 첫 질문').click().run()
    assert b.session_state['pages']==[] and b.session_state['runs']==[]
    press(b,'예제 자료로 체험').click().run();press(b,'연습 시작 · 첫 질문').click().run()
    assert [r['key'] for r in gemini_backend['requests']]==['key-A','key-B']
    press(a,'연결 해제').click().run()
    assert b.session_state['_gemini_connection'].ready and not a.exception and not b.exception


def test_pending_key_is_deleted_on_disconnect(gemini_backend):
    gemini_backend['missing_model']=True
    c=access.Connection();assert not c.attach('key-A')
    c.disconnect();assert not c.pending_key and not c.key and not c.alternatives


def test_no_ambient_credential_constructor_when_key_missing(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY','OWNER_KEY');monkeypatch.setenv('GOOGLE_API_KEY','OWNER_KEY')
    def unexpected(*a,**k): raise AssertionError('SDK constructor must not run')
    monkeypatch.setattr(gp.genai,'Client',unexpected)
    with pytest.raises(gp.GeminiFailure,match='먼저'):
        gp.client('')


def test_public_action_debounce_and_visual_payload_bounds(gemini_backend):
    c=connection();c.meter.interval=60
    access.generate(c,examples(),[],'일반 학생','보통')
    with pytest.raises(gp.GeminiFailure,match='잠시'):
        access.generate(c,examples(),[],'일반 학생','보통')
    assert len(gemini_backend['requests'])==1
    from qa.media import PageImage
    with gp.use(gp.Scope('key-A',images=tuple(PageImage(str(i),'f.pdf',i+1,b'x') for i in range(5)))):
        with pytest.raises(gp.GeminiFailure,match='페이지'):
            engine.generate(examples(),[],demo=False)


def test_graph_pdf_raster_and_reference_number():
    from io import BytesIO
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject,NameObject
    writer=PdfWriter();writer.add_blank_page(400,300)
    graph=writer.add_blank_page(400,300)
    stream=DecodedStreamObject();stream.set_data(b'0 0 0 RG 20 20 m 20 250 l 350 250 l S 1 0 0 RG 20 40 m 80 100 l 160 120 l 240 180 l 340 185 l S')
    graph[NameObject('/Contents')]=writer._add_object(stream)
    out=BytesIO();writer.write(out)
    pages,_=extract('synthetic_voltage.pdf',out.getvalue(),'발표')
    selected=render({p.id:out.getvalue() for p in pages},pages,[pages[1].id])
    assert len(selected)==1 and selected[0].number==2 and selected[0].file=='synthetic_voltage.pdf'
    assert selected[0].data.startswith(b'\xff\xd8')


def test_developer_gemini_ui_uses_same_sdk_engine(gemini_backend,monkeypatch):
    import streamlit as st
    monkeypatch.setenv('KKOKKOMOOGI_DEV_LOCAL','1');monkeypatch.setenv('GEMINI_API_KEY','dev-key')
    original=st.get_option
    monkeypatch.setattr(st,'get_option',lambda key:'127.0.0.1' if key=='server.address' else original(key))
    app=Path(__file__).parents[1]/'dev_app.py'
    at=AppTest.from_file(str(app),default_timeout=20).run()
    next(x for x in at.selectbox if x.label=='검증 제공자').select('Gemini').run()
    next(x for x in at.radio if x.label=='질문 생성 방식').set_value('Gemini API').run()
    press(at,'예제 자료로 체험하기').click().run()
    press(at,'새 연습 시작 / 첫 질문 생성').click().run()
    assert at.session_state['runs'][0]['turns'][0]['mode']=='Gemini'
    assert gemini_backend['requests'][0]['key']=='dev-key' and not at.exception
