import json
from .history import csv_bytes

def markdown(pages, runs, references, comparison):
    lines = ['# 꼬리에 꼬리 물기 — Q&A 기록', '',
             'AI 평가는 보조 결과이며 확정적인 정답이 아닙니다. 데모 결과는 실제 검증 결과가 아닙니다.', '',
             '## 자료', *[f'- {p.file} / {p.role} / {p.kind} {p.number}' for p in pages]]
    page_map = {p.id:p for p in pages}
    for run in runs:
        lines.extend(['', f'## {run["name"]}', f'실험 자료: {run["data_label"]}'])
        for i,t in enumerate(run['turns'],1):
            lines.extend(['', f'### Q{i}. {t["question"]["question"]}',
                          f'모드: {t["mode"]} / 모델: {t["model"]} / 프롬프트: {t["prompt_version"]}',
                          f'근거 구분: {t["question"]["basis"]}'])
            for c in t['question']['citations']:
                p = page_map.get(c['page_id'])
                lines.append(f'> {p.file if p else c["page_id"]} {p.kind if p else ""} {p.number if p else ""}: {c["quote"]}')
            lines.extend([f'연결 이유: {t["question"]["connection"]}',
                          f'답변: {t.get("answer") or "미답변"}'])
            for label in ('feedback','quality','human_scores','human_feedback','errors'):
                if t.get(label):
                    lines.extend([f'{label}:', '```json', json.dumps(t[label],ensure_ascii=False,indent=2), '```'])
    if comparison:
        lines.extend(['', '## 과거 질문 비교', '```json', json.dumps(comparison,ensure_ascii=False,indent=2), '```'])
    lines.extend(['', '## 과거 질문 목록', *[f'- {r.question}' for r in references]])
    return '\n'.join(lines)

def evaluation_csv(runs):
    rows = []
    for run in runs:
        for i,t in enumerate(run['turns'],1):
            ai = {g['metric']:g for g in (t.get('quality') or {}).get('grades',[])}
            for metric in ['자료 관련성', '기술적 정확성', '논리적 연결성', '질문 다양성', '근거 신뢰성']:
                grade = ai.get(metric,{})
                rows.append({'run':run['name'], 'data':run['data_label'], 'question_no':i,
                             'question':t['question']['question'], 'answer':t.get('answer',''),
                             'prompt_version':t['prompt_version'], 'mode':t['mode'], 'metric':metric,
                             'ai_score':grade.get('score') or '검토 필요', 'reason':grade.get('reason','미평가'),
                             'human_score':t.get('human_scores',{}).get(metric,'검토 필요'),
                             'human_feedback':t.get('human_feedback',''), 'errors':'; '.join(t.get('errors',[]))})
    return csv_bytes(rows)
