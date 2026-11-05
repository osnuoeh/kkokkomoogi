"""Public downloads use an allowlist, independent of developer reports/state."""
import json


def snapshot(pages, runs):
    safe_runs = []
    for run in runs:
        turns = []
        for turn in run['turns']:
            turns.append({k: turn.get(k) for k in (
                'question', 'answer', 'feedback', 'feedback_mode', 'mode', 'model',
                'audience', 'difficulty', 'prompt_version')})
        safe_runs.append({'name': run['name'], 'data_label': run.get('data_label') or '사용자 Q&A 기록', 'turns': turns})
    return {'schema_version': 2, 'purpose': '사용자 Q&A 연습 기록',
            'documents': [{'file': p.file, 'role': p.role, 'number': p.number, 'kind': p.kind}
                          for p in pages], 'sessions': safe_runs}


def markdown(pages, runs):
    data = snapshot(pages, runs)
    page_map = {p.id: p for p in pages}
    lines = ['# 꼬리에 꼬리 물기 — Q&A 연습 기록', '',
             'AI 피드백은 보조 의견입니다. 데모는 규칙 기반 체험입니다.']
    for run in data['sessions']:
        lines.extend(['', f'## {run["name"]}', run['data_label']])
        for i, turn in enumerate(run['turns'], 1):
            q = turn['question']
            lines.extend(['', f'### Q{i}. {q["question"]}',
                          f'방식: {turn["mode"]} / 청중: {turn["audience"]} / 난이도: {turn["difficulty"]}',
                          f'근거 구분: {q["basis"]}'])
            for c in q['citations']:
                p = page_map.get(c['page_id'])
                if p:
                    lines.append(f'> {p.file} · {p.kind} {p.number}: {c["quote"]}')
            lines.append(f'답변: {turn["answer"] or "미답변"}')
            if turn['feedback']:
                lines.extend(['피드백:', '```json', json.dumps(turn['feedback'], ensure_ascii=False, indent=2), '```'])
    return '\n'.join(lines)
