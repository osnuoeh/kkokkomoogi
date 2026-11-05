import csv
import re
from io import BytesIO, StringIO
from pathlib import Path
from .documents import validate_size, check_zip
from .models import Historical

ALIASES = {'발표 주제':'topic', '자료 이름':'material', '질문 내용':'question', '질문':'question',
           '질문 출처':'source', '질문 유형':'type', '당시 답변':'answer', '답변':'answer',
           '피드백':'feedback', '관련 참고 문헌':'reference', '페이지':'page'}
FIELDS = list(Historical.model_fields)

def load_history(name, data):
    validate_size(data)
    suffix = Path(name).suffix.lower()
    if suffix == '.xlsx':
        from openpyxl import load_workbook
        check_zip(data)
        wb = load_workbook(BytesIO(data), read_only=True, data_only=True)
        try:
            rows = iter(wb.active.values)
            headers = [str(x or '').strip() for x in next(rows)]
            raw = [dict(zip(headers, r)) for r in rows if any(v is not None for v in r)]
        finally:
            wb.close()
    elif suffix == '.csv':
        raw = list(csv.DictReader(StringIO(data.decode('utf-8-sig'))))
    elif suffix in ('.txt', '.md'):
        lines = data.decode('utf-8-sig').splitlines()
        raw = [{'question':re.sub(r'^\s*(?:[-*+]\s+|\d+[.)]\s*)', '', s).strip()} for s in lines
               if s.strip() and not s.lstrip().startswith('#')]
    else:
        raise ValueError('CSV, XLSX, TXT, Markdown만 지원합니다.')
    if len(raw) > 1000:
        raise ValueError('질문 목록은 1,000개 이하로 나눠 주세요.')
    result = []
    for i, row in enumerate(raw, 2):
        clean = {ALIASES.get(str(k).strip(), str(k).strip()):str(v).strip() if v is not None else ''
                 for k, v in row.items() if k is not None}
        if not clean.get('question'):
            raise ValueError(f'{i}행: question 또는 질문 내용 열이 필요합니다.')
        result.append(Historical(**{k:v for k,v in clean.items() if k in FIELDS}))
    if not result:
        raise ValueError('질문 목록이 비어 있습니다.')
    return result

def csv_bytes(rows, fields=None):
    out = StringIO()
    if rows:
        writer = csv.DictWriter(out, fieldnames=fields or list(rows[0]))
        writer.writeheader()
        for row in rows:
            # Spreadsheet formula injection guard, including user-authored content.
            writer.writerow({k:("'"+str(v) if str(v).lstrip().startswith(('=', '+', '-', '@')) else v)
                             for k,v in row.items()})
    return out.getvalue().encode('utf-8-sig')

def sample_csv():
    return csv_bytes([Historical(topic='과적합과 일반화', material='예제_발표.pdf',
          question='검증 손실 증가가 과적합의 신호인 이유는 무엇인가요?', source='실제 청중 질문', type='원리',
          reference='예제_참고.pdf', page='1').model_dump(),
          Historical(topic='과적합과 일반화', material='예제_발표.pdf',
          question='외부 데이터에서도 성능이 유지된다고 볼 수 있나요?', type='한계').model_dump()])
