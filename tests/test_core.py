from io import BytesIO
import csv
import json
import pytest
from pptx import Presentation
from pypdf import PdfWriter
from openpyxl import Workbook
from qa.documents import extract, examples, validate_size
from qa.history import load_history, sample_csv, csv_bytes
from qa.models import Question, Citation, Historical, Comparison, Match, Quality, Grade, METRICS
from qa import engine, evaluation
from qa.reports import markdown, evaluation_csv

@pytest.fixture
def pages():
    return examples()

def test_pdf_preserves_page_number_and_warns_for_image_or_blank():
    writer = PdfWriter()
    writer.add_blank_page(width=300,height=300)
    writer.add_blank_page(width=300,height=300)
    # A minimal PDF text stream uses a standard font, with no renderer dependency.
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    font = DictionaryObject({NameObject('/Type'):NameObject('/Font'), NameObject('/Subtype'):NameObject('/Type1'), NameObject('/BaseFont'):NameObject('/Helvetica')})
    writer.pages[0][NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(b'BT /F1 12 Tf 10 100 Td (Evidence on page one) Tj ET')
    writer.pages[0][NameObject('/Contents')] = writer._add_object(stream)
    output = BytesIO(); writer.write(output)
    pages,warnings = extract('sample.pdf',output.getvalue(),'발표')
    assert [p.number for p in pages] == [1,2]
    assert 'Evidence on page one' in pages[0].text
    assert len(warnings) == 1 and '2' in warnings[0]

def test_pptx_extracts_text_and_tables_with_slide_numbers():
    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    from pptx.util import Inches
    slide.shapes.add_textbox(Inches(1), Inches(1), Inches(5), Inches(1)).text = '첫 슬라이드'
    table = slide.shapes.add_table(1,2,Inches(1),Inches(3),Inches(5),Inches(1)).table
    table.cell(0,0).text = '표 근거'
    deck.slides.add_slide(deck.slide_layouts[6])
    output = BytesIO(); deck.save(output)
    pages,warnings = extract('sample.pptx',output.getvalue(),'발표')
    assert pages[0].number == 1 and '표 근거' in pages[0].text and '첫 슬라이드' in pages[0].text
    assert len(warnings) == 1 and pages[1].number == 2

def test_encrypted_pdf():
    writer = PdfWriter(); writer.add_blank_page(width=100,height=100); writer.encrypt('secret')
    b=BytesIO(); writer.write(b)
    with pytest.raises(ValueError,match='암호화'):
        extract('encrypted.pdf',b.getvalue(),'발표')

@pytest.mark.parametrize('filename,data', [('bad.pptx',b'not a zip'),('bad.doc',b'unsupported')])
def test_invalid_documents(filename,data):
    with pytest.raises(Exception):
        extract(filename,data,'발표')

def test_oversize_and_reference_type():
    with pytest.raises(ValueError,match='25MB'):
        validate_size(bytes(25*1024*1024+1))
    with pytest.raises(ValueError,match='PDF'):
        extract('paper.pptx',b'','참고')

def test_blank_presentations_rejected(pages):
    for p in pages:
        p.text = ''
    with pytest.raises(ValueError):
        engine.generate(pages,[])

def test_history_csv_and_metadata():
    refs = load_history('questions.csv',sample_csv())
    assert len(refs) == 2 and refs[0].source == '실제 청중 질문'
    refs=load_history('q.csv','질문 내용,당시 답변,피드백\n왜인가요?,기존 답변,피드백'.encode())
    assert refs[0].answer == '기존 답변'

@pytest.mark.parametrize('suffix',['txt','md'])
def test_history_plain(suffix):
    refs = load_history('q.'+suffix,'# 제목\n- 어떤 근거인가요?\n2. 한계는 무엇인가요?'.encode())
    assert [r.question for r in refs] == ['어떤 근거인가요?','한계는 무엇인가요?']

def test_history_xlsx():
    wb = Workbook(); ws=wb.active
    ws.append(['question','answer','feedback','reference','page'])
    ws.append(['원리는 무엇인가요?','당시 답변','피드백','paper.pdf',3])
    out=BytesIO(); wb.save(out)
    refs=load_history('q.xlsx',out.getvalue())
    assert refs[0].page == '3' and refs[0].answer == '당시 답변'

@pytest.mark.parametrize('data',[b'topic\nmissing',b'question\n',b'question,answer\n,empty'])
def test_invalid_history(data):
    with pytest.raises(ValueError):
        load_history('bad.csv',data)

def test_demo_requires_answer_and_uses_actual_claim(pages):
    first=engine.generate(pages,[])
    assert first['feedback'] is None and first['answer'] == ''
    with pytest.raises(ValueError,match='먼저 답변'):
        engine.generate(pages,[first])
    first['answer']='검증 손실의 증가가 과적합의 증거입니다.'
    followup=engine.generate(pages,[first])
    assert first['answer'] in followup['question']['question']
    assert followup['question']['question'] != first['question']['question']
    first['answer']='외부 검증을 하지 않았으므로 일반화를 단정할 수 없습니다.'
    other=engine.generate(pages,[first])
    assert other['question']['question'] != followup['question']['question']

def test_citation_forgery_dropped_and_flagged(pages):
    q=Question(question='검증 결과를 어떻게 설명하나요?',focus='검증',connection='',basis='원문 확인',
               citations=[Citation(page_id=pages[0].id,quote='문서에 없는 내용'),Citation(page_id='fake',quote='a')])
    q=engine.validate_citations(q,[p.model_dump() for p in pages])
    assert q.basis == '근거 부족' and q.citations == []

def test_context_bounded_and_contains_both_roles(pages):
    for p in pages:
        p.text *= 2000
    supplied=engine.context(pages,'검증 손실',limit=5500)
    assert sum(len(p['text']) for p in supplied) <= 5500
    assert {p['role'] for p in supplied} == {'발표','참고'}

def test_generator_allowlist_prevents_evaluation_leakage(monkeypatch,pages):
    marker='HOLDOUT_SECRET_9238'
    previous=engine.generate(pages,[])
    previous['answer']='일반화가 중요합니다.'
    previous['feedback']={'reference_question':marker}
    previous['quality']={'reference_question':marker}
    previous['human_feedback']=marker
    def fake(system,payload,schema):
        serialized=json.dumps(payload,ensure_ascii=False)
        assert marker not in serialized and marker not in system
        assert set(payload)=={'documents','conversation','audience','difficulty','new_topic'}
        assert payload['conversation'][0]['answer'] == previous['answer']
        return Question(question='이 주장을 검증할 근거는 무엇인가요?',focus='검증',connection='답변의 근거 확인',basis='모델 추론',citations=[])
    monkeypatch.setattr(engine,'llm',fake)
    result=engine.generate(pages,[previous],demo=False)
    assert result['question']['basis'] == '근거 부족'

def test_judge_receives_reference_only_after_generation(monkeypatch,pages):
    turns=[engine.generate(pages,[])]
    refs=[Historical(question='외부 검증의 한계는 무엇인가요?')]
    def fake(system,payload,schema):
        assert payload['references'][0]['question']==refs[0].question
        return Comparison(matches=[Match(reference_index=0,generated_indices=[0],status='부분적으로 다룸',concept='일반화',reason='의미 비교')],limitations='보조 평가')
    monkeypatch.setattr(evaluation,'llm',fake)
    result=evaluation.compare(refs,turns,demo=False)
    assert result['matches'][0]['status']=='부분적으로 다룸'

def test_judge_invalid_index_rejected(monkeypatch,pages):
    monkeypatch.setattr(evaluation,'llm',lambda *a:Comparison(matches=[Match(reference_index=0,generated_indices=[8],status='다룸',concept='x',reason='x')],limitations=''))
    with pytest.raises(ValueError,match='존재하지 않는'):
        evaluation.compare([Historical(question='왜인가요?')],[engine.generate(pages,[])],False)

def test_demo_does_not_claim_semantic_scores(pages):
    turns=[engine.generate(pages,[])]
    grades=engine.quality(pages,turns,0)
    assert all(g['score'] is None for g in grades['grades'])
    result=evaluation.compare(load_history('q.csv',sample_csv()),turns)
    assert all(m['status']=='검토 필요' for m in result['matches'])

def test_first_question_logical_connection_unscored(monkeypatch,pages):
    monkeypatch.setattr(engine,'llm',lambda *a:Quality(grades=[Grade(metric=m,score=4,reason='test') for m in METRICS],problems=[],suggestion=''))
    result=engine.quality(pages,[engine.generate(pages,[])],0,False)
    assert next(g for g in result['grades'] if g['metric']=='논리적 연결성')['score'] is None

def test_prompt_versions_stable_and_change(pages):
    assert engine.version('a')==engine.version('a') != engine.version('b')
    a=engine.generate(pages,[],prompt='a')
    b=engine.generate(pages,[],prompt='b')
    assert a['prompt_version']!=b['prompt_version']

def test_feedback_requires_answer_and_demo_is_honest(pages):
    t=engine.generate(pages,[])
    with pytest.raises(ValueError):
        engine.feedback(pages,t)
    t['answer']='검증은 중요합니다.'
    assert '검토 필요' in engine.feedback(pages,t)['accuracy']

def test_reports_and_csv_formula_safety(pages):
    t=engine.generate(pages,[])
    t['answer']='=HYPERLINK("bad")'
    t['human_scores']={'자료 관련성':4}
    runs=[{'name':'before','data_label':'가상 예제', 'turns':[t]}]
    report=markdown(pages,runs,[],None)
    assert '예제_발표.pdf' in report and '실제 검증 결과가 아닙니다' in report
    rows=list(csv.DictReader(evaluation_csv(runs).decode('utf-8-sig').splitlines()))
    assert rows[0]['answer'].startswith("'=") and rows[0]['human_score']=='4'

def test_api_missing_key(monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    with pytest.raises(ValueError,match='OPENAI_API_KEY'):
        engine.llm('s',{},Question)

def test_openai_sdk_structured_adapter_with_mock_transport(monkeypatch):
    """Checks real SDK serialization/parsing, without external API traffic."""
    import httpx
    import openai
    original = openai.OpenAI
    question={'question':'검증 근거는 무엇인가요?','focus':'검증','connection':'첫 질문','basis':'근거 부족','citations':[]}
    def handler(request):
        body=json.loads(request.content)
        assert body['response_format']['type']=='json_schema'
        assert body['response_format']['json_schema']['strict'] is True
        assert body['messages'][0]['role']=='system'
        return httpx.Response(200,json={'id':'mock-response','object':'chat.completion','created':1,
              'model':'gpt-4o-mini','choices':[{'index':0,'finish_reason':'stop',
              'message':{'role':'assistant','content':json.dumps(question,ensure_ascii=False),'refusal':None}}],
              'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}})
    monkeypatch.setenv('OPENAI_API_KEY','test-key-not-real')
    monkeypatch.setattr(openai,'OpenAI',lambda **kwargs:original(**kwargs,http_client=httpx.Client(transport=httpx.MockTransport(handler))))
    result=engine.llm('고정 규칙',{'documents':[]},Question)
    assert result.question==question['question']

def test_citation_must_exist_in_supplied_excerpt_not_only_full_document(pages):
    q=Question(question='추출 내용의 근거는 무엇인가요?',focus='근거',connection='',basis='원문 확인',
               citations=[Citation(page_id=pages[0].id,quote=pages[0].text[-20:])])
    supplied=pages[0].model_dump(); supplied['text']=pages[0].text[:10]
    assert engine.validate_citations(q,[supplied]).basis=='근거 부족'
