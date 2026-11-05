import hashlib
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile, BadZipFile
from .models import Page

MAX_BYTES = 25 * 1024 * 1024

def validate_size(data):
    if len(data) > MAX_BYTES:
        raise ValueError('파일당 최대 25MB입니다.')

def check_zip(data):
    try:
        with ZipFile(BytesIO(data)) as z:
            if sum(i.file_size for i in z.infolist()) > 120 * 1024 * 1024 or len(z.infolist()) > 5000:
                raise ValueError('압축 해제 크기 또는 항목 수가 너무 큽니다.')
    except BadZipFile as e:
        raise ValueError('올바른 Office 문서가 아닙니다.') from e

def shape_text(shape):
    parts = []
    if getattr(shape, 'has_text_frame', False):
        parts.append(shape.text_frame.text)
    if getattr(shape, 'has_table', False):
        parts.extend(' | '.join(c.text for c in row.cells) for row in shape.table.rows)
    if hasattr(shape, 'shapes'):
        parts.extend(shape_text(s) for s in shape.shapes)
    return '\n'.join(parts)

def extract(name: str, data: bytes, role: str):
    validate_size(data)
    suffix = Path(name).suffix.lower()
    if role == '참고' and suffix != '.pdf':
        raise ValueError('참고 문헌은 PDF를 업로드해 주세요.')
    digest = hashlib.sha256(data + name.encode() + role.encode()).hexdigest()[:16]
    if suffix == '.pdf':
        from pypdf import PdfReader
        doc = PdfReader(BytesIO(data))
        if doc.is_encrypted:
            raise ValueError('암호화된 PDF는 암호를 해제한 뒤 업로드해 주세요.')
        if len(doc.pages) > 500:
            raise ValueError('문서는 500페이지 이하로 나눠 주세요.')
        texts = [(p.extract_text() or '') for p in doc.pages]
        kind = '페이지'
    elif suffix == '.pptx':
        from pptx import Presentation
        check_zip(data)
        doc = Presentation(BytesIO(data))
        if len(doc.slides) > 500:
            raise ValueError('문서는 500슬라이드 이하로 나눠 주세요.')
        texts = ['\n'.join(shape_text(s) for s in slide.shapes) for slide in doc.slides]
        kind = '슬라이드'
    else:
        raise ValueError('발표 자료는 PPTX 또는 PDF만 지원합니다.')
    pages = [Page(id=f'{digest}:{i}', file=Path(name).name, role=role,
                  number=i, kind=kind, text=t.strip()) for i, t in enumerate(texts, 1)]
    warnings = [f'{p.file} {p.kind} {p.number}: 텍스트가 없습니다. 이미지/스캔 문서는 OCR 후 다시 업로드해 주세요.'
                for p in pages if not p.text]
    return pages, warnings

def examples():
    return [Page(id='sample:1', file='예제_발표.pdf', role='발표', number=1, kind='페이지',
                 text='과적합은 학습 데이터에 지나치게 맞추어 새로운 데이터의 성능이 떨어지는 현상이다. 학습 손실은 감소했지만 검증 손실은 증가했다.'),
            Page(id='sample:2', file='예제_발표.pdf', role='발표', number=2, kind='페이지',
                 text='실험에서는 훈련 70%, 검증 15%, 테스트 15%로 데이터를 분리했다. 조기 종료와 정규화를 사용했다. 테스트 표본은 100개이며 외부 데이터에 대한 검증은 수행하지 않았다.'),
            Page(id='sample:3', file='예제_참고.pdf', role='참고', number=1, kind='페이지',
                 text='조기 종료는 검증 손실이 더 이상 개선되지 않을 때 훈련을 중단하는 방법이다. 정규화는 모델의 복잡도를 제한한다. 이 문서는 기능 시연용 가상 문헌이며 실제 논문이 아니다.')]
