"""Shared presentation UI helpers; no evaluation data or developer routing."""
import hashlib
import streamlit as st
from .documents import extract


def notify_error(exc):
    # Do not echo provider errors, which may contain request data or credentials.
    from .gemini_provider import GeminiFailure
    if isinstance(exc,GeminiFailure):
        st.warning(str(exc))
    elif isinstance(exc, ValueError) and not type(exc).__module__.startswith('pydantic'):
        st.error(str(exc))
    else:
        st.error('처리하지 못했습니다. 파일 형식 또는 API 연결·모델 지원·사용량을 확인한 뒤 다시 시도해 주세요. 기존 기록은 유지됩니다.')


def load_uploads(presentations, papers, allow_visual=False):
    pages, warnings, seen = [], [], set()
    for role, uploads in [('발표', presentations), ('참고', papers)]:
        for upload in uploads:
            data = upload.getvalue()
            fingerprint = (role, upload.name, hashlib.sha256(data).hexdigest())
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            extracted, notices = extract(upload.name, data, role)
            pages.extend(extracted)
            warnings.extend(notices)
    if not any((p.text or (allow_visual and p.file.lower().endswith('.pdf'))) and p.role == '발표' for p in pages):
        raise ValueError('발표 자료에서 텍스트를 추출하지 못했습니다. 스캔 PDF는 OCR 처리 후 다시 업로드해 주세요.')
    return pages, warnings
