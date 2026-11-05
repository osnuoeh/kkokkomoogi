"""Synthetic PDF regression: no private user documents are checked in."""
from io import BytesIO
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
from qa.documents import extract
from qa import engine


def synthetic_pdf():
    texts = [
        'Silicon anodes\nPresenter: Jane Example\nDepartment of Materials Science\nExample University\n1',
        'Contents\nIntroduction 3\nMechanism 4\nResults 5\nReferences 6\n2',
        'Introduction\nSilicon expands during lithiation. Volume expansion causes particle fracture and loss of electrical contact.\n3',
        'Mechanism\nElectrolyte decomposition forms the SEI layer. Repeated volume expansion cracks the SEI and consumes lithium.\n4',
        'Results\nThe coated sample retained 85% capacity after 100 cycles compared with 60% for the control. The experiment did not test high loading.\n5',
        'References\n[1] A. Author, Battery journal, 2024, 1, 12-19.\n[2] B. Author, Materials journal, 2023, 5, 45-50.\n6',
        'Thank you\nQuestions\n7']
    writer = PdfWriter()
    font = DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    font_ref=writer._add_object(font)
    for text in texts:
        page=writer.add_blank_page(width=600,height=800)
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):font_ref})})
        stream=DecodedStreamObject()
        lines=['BT /F1 12 Tf 20 760 Td']
        for line in text.splitlines():
            lines.extend(['('+line.replace('(','\\(').replace(')','\\)')+') Tj','0 -20 Td'])
        lines.append('ET'); stream.set_data('\n'.join(lines).encode())
        page[NameObject('/Contents')]=writer._add_object(stream)
    out=BytesIO();writer.write(out)
    return out.getvalue()


def test_cover_and_contents_do_not_drive_first_question():
    pages,_=extract('synthetic_anode.pdf',synthetic_pdf(),'발표')
    first=engine.generate(pages,[])
    assert first['question']['citations'][0]['page_id'] in {p.id for p in pages[2:5]}
    assert 'Jane Example' not in first['question']['question']
    assert 'Department' not in first['question']['question']


def test_introduction_retained_metadata_demoted_and_physical_pages_preserved():
    from qa.content import profile
    pages,_=extract('synthetic_anode.pdf',synthetic_pdf(),'발표')
    assert profile(pages[2])['score'] > 0
    assert all(profile(pages[2])['score'] > profile(pages[i])['score'] for i in [0,1,5,6])
    supplied=engine.context(pages)
    assert supplied[0]['number'] in (3,4,5)
    assert [p.number for p in pages] == list(range(1,8))
    assert all(x['number'] not in (1,2,6,7) for x in supplied)


def test_numbers_are_not_concepts_but_scientific_numbers_and_formulas_remain():
    from qa.content import substantive_lines
    assert engine.tokens('1 2 2024 12/45') == set()
    assert not substantive_lines('123\nPage 2/17\n2024\n감사합니다\n목차')
    assert 'li2o' in engine.tokens('Li2O')
    pages,_=extract('synthetic_anode.pdf',synthetic_pdf(),'발표')
    supplied=engine.context(pages)
    assert any('85%' in p['text'] and '100 cycles' in p['text'] for p in supplied)


def test_llm_receives_bounded_map_before_question_and_valid_reference_citations(monkeypatch):
    from qa.models import Question, Citation
    from qa.documents import Page
    pages,_=extract('synthetic_anode.pdf',synthetic_pdf(),'발표')
    paper=Page(id='paper:9',file='synthetic_paper.pdf',role='참고',number=9,kind='페이지',
               text='Lithiation causes volume expansion and SEI fracture in silicon particles.')
    def fake(system,payload,schema,api_key=None):
        m=payload['presentation_map']
        assert m['mechanisms'] and m['results_and_limits'] and m['topics']
        assert any('원인' in system or '인과' in system for _ in [0])
        assert not payload['conversation']
        return Question(question='부피 팽창이 계면층 손상으로 이어지는 메커니즘을 어떻게 설명하나요?',focus='계면층 손상',
                        connection='첫 기술 질문',basis='원문 확인',citations=[Citation(page_id=paper.id,quote=paper.text)])
    monkeypatch.setattr(engine,'llm',fake)
    turn=engine.generate(pages+[paper],[],demo=False)
    assert turn['question']['citations'][0]['page_id']=='paper:9'
    assert paper.number==9 and turn['question']['basis']=='원문 확인'


def test_anchor_uses_concrete_claim_beyond_first_120_characters_and_rejects_unlinked_llm(monkeypatch):
    import pytest
    from qa.models import Question
    pages,_=extract('synthetic_anode.pdf',synthetic_pdf(),'발표')
    first=engine.generate(pages,[])
    first['answer']='소개를 먼저 말씀드리겠습니다. '*20+'\n부피 팽창이 SEI 균열을 유발해 리튬을 소모합니다.'
    follow=engine.generate(pages,[first])
    assert '부피 팽창이 SEI 균열을 유발해 리튬을 소모합니다.' in follow['question']['question']
    def fake(*args,**kwargs):
        return Question(question='발표자의 소속은 어디인가요?',focus='소속',connection='관련 없는 질문',basis='근거 부족',citations=[])
    monkeypatch.setattr(engine,'llm',fake)
    with pytest.raises(ValueError,match='연결되지 않은'):
        engine.generate(pages,[first],demo=False)


def test_cover_only_document_does_not_fabricate_technical_question():
    import pytest
    from qa.models import Page
    p=Page(id='cover',file='cover.pdf',role='발표',number=1,kind='페이지',text='Presenter: Jane Example\nExample University\n1')
    with pytest.raises(ValueError,match='기술적 본문'):
        engine.generate([p],[])



def test_short_scientific_introduction_is_content_not_a_cover():
    from qa.models import Page
    from qa.content import profile
    intro=Page(id='intro',file='short.pdf',role='발표',number=2,kind='페이지',text='Introduction\nSEI forms.\n2')
    assert profile(intro)['score'] > 0
    assert engine.context([intro])[0]['number'] == 2
