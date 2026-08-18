"""
<도구 이름> — 이 스크립트가 하는 일 한 줄 설명.

사용 예:
    python tools/_template.py --input foo
"""

from __future__ import annotations

import argparse

from dotenv import load_dotenv

load_dotenv()


def run(input_value: str) -> str:
    """실제 작업을 수행하고 결과를 반환한다."""
    raise NotImplementedError("이 템플릿을 복사한 뒤 실제 로직을 채워 넣으세요.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="처리할 입력 값")
    args = parser.parse_args()

    result = run(args.input)
    print(result)


if __name__ == "__main__":
    main()
