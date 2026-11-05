from io import BytesIO
from types import SimpleNamespace
import pytest
from pptx import Presentation
from pptx.util import Inches
from qa import ui_common
from qa.documents import examples


def test_shared_upload_path_preserves_slides_and_deduplicates_files():
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    slide.shapes.add_textbox(Inches(1), Inches(1), Inches(5), Inches(1)).text = '가상의 발표 주장'
    buffer = BytesIO(); presentation.save(buffer)
    upload = SimpleNamespace(name='가상_발표.pptx', getvalue=lambda:buffer.getvalue())
    pages, warnings = ui_common.load_uploads([upload, upload], [])
    assert len(pages) == 1 and pages[0].number == 1
    assert pages[0].text == '가상의 발표 주장' and not warnings


def test_shared_upload_path_requires_readable_presentation(monkeypatch):
    pages = examples()
    for page in pages:
        page.text = ''
    monkeypatch.setattr(ui_common, 'extract', lambda *a:(pages, []))
    upload = SimpleNamespace(name='fake.pdf', getvalue=lambda:b'fake')
    with pytest.raises(ValueError, match='텍스트'):
        ui_common.load_uploads([upload], [])
