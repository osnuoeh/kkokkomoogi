"""Opt-in real API smoke check. No key in command line, environment fallback or files."""
import sys
from pathlib import Path
from getpass import getpass
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from qa.gemini_access import Connection,generate
from qa.gemini_provider import GeminiFailure
from qa.models import Page


def main():
    print('실제 Gemini API를 호출합니다. 키가 속한 프로젝트의 할당량/비용이 발생할 수 있습니다.')
    if input('실제 호출에 동의하면 YES 입력: ').strip()!='YES':
        return 0
    key=getpass('개인 Gemini API 키 (표시/저장 안 함): ')
    connection=Connection()
    try:
        if not connection.attach(key):
            print('기본 모델을 조회하지 못했습니다. 자동 대체 호출은 하지 않습니다.')
            for item in connection.alternatives:
                print('조회 대안:',item['label'])
            return 2
        pages=[Page(id='safe-example:1',file='synthetic_example.pdf',role='발표',number=1,kind='페이지',
            text='Silicon expands during lithiation. Volume expansion causes particle fracture and SEI cracking. This is a synthetic example, not a real experimental result.')]
        turn=generate(connection,pages,[],'전공 교수','보통')
        print('실제 응답:',turn['question']['question'])
        print('이 실행은 연결 확인이며 질문의 과학적 정확성을 검증한 결과는 아닙니다.')
        return 0
    except GeminiFailure as exc:
        print(str(exc));return 1
    except Exception:
        print('응답을 검증하지 못했습니다. 키/요청 원문은 출력하지 않습니다.');return 1
    finally:
        connection.disconnect();key=''


if __name__=='__main__':
    try:
        raise SystemExit(main())
    except (KeyboardInterrupt,EOFError):
        print('중단했습니다.')
