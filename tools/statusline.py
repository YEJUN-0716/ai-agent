"""상태줄 — 화면 아래에 토큰 사용량을 상시 표시한다.

클로드 코드가 매 갱신마다 이 스크립트를 부르고 stdin 으로 JSON 을 준다.
한 줄만 출력하면 그 줄이 그대로 화면 아래에 붙는다.

받는 값은 클로드 코드가 정한다(`context_window`, `rate_limits` 등).
값이 없을 수 있는 자리가 많아서 — 첫 응답 전, 구독이 아닌 경우 — 전부
없는 셈 치고 건너뛴다. 상태줄이 깨지면 화면이 깨진다.

색은 [단색]/[강조] 두 단계만 쓴다. 사장님 테마가 light-daltonized 라
빨강·초록 대비는 쓰지 않는다.
"""
import json
import sys
from datetime import datetime

강조 = "\033[1;38;5;33m"  # 파랑 — 80% 넘은 자리
흐림 = "\033[2m"
끝 = "\033[0m"


def 짧은수(n: float) -> str:
    return f"{n / 1000:.0f}k" if n >= 1000 else str(int(n))


def 퍼센트(값: float | None) -> str:
    """80% 이상이면 눈에 띄게. 색만으로 알리지 않고 ! 도 같이 붙인다."""
    if 값 is None:
        return "-"
    글 = f"{값:.0f}%"
    return f"{강조}{글}!{끝}" if 값 >= 80 else 글


def 리셋(epoch: float | None) -> str:
    if not epoch:
        return ""
    return datetime.fromtimestamp(epoch).strftime(" %H:%M↺")


def 줄(d: dict) -> str:
    칸 = []

    이름 = (d.get("model") or {}).get("display_name")
    if 이름:
        칸.append(이름)

    ctx = d.get("context_window") or {}
    쓴퍼 = ctx.get("used_percentage")
    if 쓴퍼 is not None:
        쓴토큰 = ctx.get("total_input_tokens") or 0
        크기 = ctx.get("context_window_size") or 0
        칸.append(f"맥락 {퍼센트(쓴퍼)} ({짧은수(쓴토큰)}/{짧은수(크기)})")

    한도 = d.get("rate_limits") or {}
    for 키, 라벨 in (("five_hour", "5시간"), ("seven_day", "주간")):
        창 = 한도.get(키)
        if 창:
            칸.append(f"{라벨} {퍼센트(창.get('used_percentage'))}{리셋(창.get('resets_at'))}")

    return f"{흐림} · {끝}".join(칸)


def main() -> int:
    try:
        print(줄(json.load(sys.stdin)))
    except Exception as e:  # 상태줄은 무슨 일이 있어도 화면을 깨면 안 된다
        print(f"{흐림}상태줄 오류: {type(e).__name__}{끝}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
