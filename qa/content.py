"""Extractive technical-content ranking; never rewrites source or page numbers."""
import re

ADMIN = re.compile(r'presenter|presented by|department|university|affiliation|발표자|소속|대학교|지도교수|copyright|all rights', re.I)
HEADING = re.compile(r'^(?:\d+[.)]?\s*)?(?:introduction|background|contents|outline|agenda|references|bibliography|thank you|acknowledg\w*|questions|q\s*&\s*a|목차|서론|감사\S*|참고\s*문헌|결론|results|mechanism)\s*[:.]?$', re.I)
FOOTER = re.compile(r'^(?:(?:page|slide|페이지|슬라이드)\s*)?[\d\s/.,:()\-–]+$', re.I)
RELATION = re.compile(r'caus|because|due to|lead\w* to|result|increas|decreas|reduc|retain|compar|\bform\w*\b|expand|expansion|consum|limit|fracture|decompos|prevent|improv|손실|감소|증가|때문|원인|형성|반응|메커니즘|기전|한계|실험|검증|과적합|정규화|초래|유발|유지|비교|분해', re.I)
SCIENCE = re.compile(r'silicon|lithium|anode|electro|capacity|SEI\b|diffusion|mechanism|cycle|음극|리튬|실리콘|전해|용량|확산|입자|검증|학습|모델|데이터|loss|training|validation', re.I)
BIB = re.compile(r'^\s*\[\d+\]|doi\s*:|https?://|\b10\.\d{4,}/',re.I)


def words(text):
    # Standalone numbers/page counters cannot become semantic search concepts.
    return set(re.findall(r'[a-zA-Z가-힣][a-zA-Z가-힣0-9_-]*', text.lower()))


def substantive_lines(text):
    out=[]
    for line in text.splitlines():
        line=line.strip()
        if (not line or (ADMIN.search(line) and not RELATION.search(line)) or HEADING.fullmatch(line)
                or FOOTER.fullmatch(line) or BIB.search(line)):
            continue
        letters=len(re.findall(r'[a-zA-Z가-힣]',line))
        relation=bool(RELATION.search(line))
        if letters < 12 and not (letters >= 6 and relation and SCIENCE.search(line)):
            continue
        # Short section labels (even scientific ones) are not assertions.
        if relation or (letters >= 45 and len(words(line)) >= 7):
            out.append(line)
    return out


def profile(page):
    lines=substantive_lines(page.text)
    relations=sum(bool(RELATION.search(s)) for s in lines)
    science=sum(bool(SCIENCE.search(s)) for s in lines)
    score=min(len(lines),8)*2 + min(relations,5)*3 + min(science,5)
    if ADMIN.search(page.text) and len(lines) == 1 and not re.search(r'[.!?。]$', lines[0]):
        score *= .1
    return {'page_id':page.id,'file':page.file,'number':page.number,'kind':page.kind,
            'score':score,'lines':lines}


def excerpt(page, length):
    lines=substantive_lines(page.text)
    start=page.text.find(lines[0]) if lines else 0
    return page.text[max(0,start):max(0,start)+length]


def presentation_map(pages):
    """Bounded evidence candidates from ALL pages, not a fabricated scientific summary."""
    result={'type':'추출 원문 후보 — 의미 이해/과학적 검증 결과 아님',
            'topics':[], 'claims':[], 'mechanisms':[], 'results_and_limits':[]}
    for page in pages:
        if page.role != '발표':
            continue
        for line in profile(page)['lines']:
            item={'page_id':page.id,'quote':line[:300]}
            category=('mechanisms' if re.search(r'mechanism|SEI|lithiat|decompos|반응|기전|메커니즘|형성|분해',line,re.I)
                      else 'results_and_limits' if re.search(r'\d.*(?:%|cycles)|result|experiment|limit|실험|결과|한계|검증',line,re.I)
                      else 'claims')
            if len(result[category])<4:
                result[category].append(item)
            if len(result['topics'])<3:
                result['topics'].append(item)
    return result


def answer_anchor(answer):
    # Prefer an explanatory clause anywhere in the answer, not a 120-char prefix.
    sentences=[s.strip() for s in re.split(r'(?<=[.!?。])\s+|\n+',answer) if s.strip()]
    candidates=[s for s in sentences if RELATION.search(s)]
    selected=max(candidates or sentences, key=lambda s:len(words(s)),default=answer.strip())
    return selected[:240]
