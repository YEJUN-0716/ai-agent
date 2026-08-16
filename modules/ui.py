"""새 화면(레짐 스트립 · [오늘])이 쓰는 얇은 HTML 조각 생성기.

문자열만 반환한다 — `st.markdown(..., unsafe_allow_html=True)` 는 호출부가 부른다.
기존 27개 render_* 패널은 여기로 이관하지 않는다
(설계 `docs/superpowers/specs/2026-08-17-dashboard-rebuild-design.md` §6).

색 규칙(§5.1): 초록·빨강은 **방향**(수익/손실·매수/매도)에만 쓴다.
상태(정상/주의/경고)는 회색·앰버·빨강이고 정상은 색을 쓰지 않는다.
"""

MONO = "'JetBrains Mono',ui-monospace,Menlo,Consolas,monospace"

# 상태 색 — '정상'에 초록을 쓰지 않는다(초록은 방향 전용).
STATUS_COLOR = {
    '정상': 'var(--text-3)',
    '주의': 'var(--amber)',
    '경고': 'var(--red)',
    '대기': 'var(--text-4)',
}


def section(title, sub=''):
    """섹션 표제 — 대문자 모노 라벨(11px) + 보조 설명(13px)."""
    sub_html = (f"<span style='font-size:13px;color:var(--text-4)'>{sub}</span>") if sub else ''
    return (f"<div style='display:flex;align-items:baseline;gap:10px;margin:32px 0 12px 0'>"
            f"<span style=\"font-size:11px;font-weight:600;color:var(--text-2);"
            f"letter-spacing:1.6px;text-transform:uppercase;font-family:{MONO}\">{title}</span>"
            f"{sub_html}</div>")


def card(body, accent=None, pad='16px'):
    """카드 한 장. accent 를 주면 좌측 3px 바로 강조한다."""
    left = f"border-left:3px solid {accent};" if accent else ''
    return (f"<div style='background:var(--surface);border:1px solid var(--border);{left}"
            f"border-radius:6px;padding:{pad}'>{body}</div>")


def kpi(label, value, delta=None, delta_color=None):
    """숫자 하나 — 라벨(11px 모노) + 값(28px) + 증감(13px)."""
    d = ''
    if delta is not None:
        d = (f"<div style='font-size:13px;font-weight:600;margin-top:2px;"
             f"color:{delta_color or 'var(--text-3)'};font-family:{MONO}'>{delta}</div>")
    return card(
        f"<div style=\"font-size:11px;font-weight:600;color:var(--text-3);letter-spacing:1.1px;"
        f"text-transform:uppercase;font-family:{MONO}\">{label}</div>"
        f"<div style=\"font-size:28px;font-weight:700;color:var(--text-1);line-height:1.25;"
        f"margin-top:4px;font-family:{MONO}\">{value}</div>{d}")


def status_dot(level, label=None):
    """상태 점 + 텍스트 라벨. 색만으로 뜻을 전하지 않는다(§5.1)."""
    color = STATUS_COLOR.get(level, 'var(--text-4)')
    text = label if label is not None else level
    return (f"<span style='display:inline-flex;align-items:center;gap:6px'>"
            f"<span style='width:7px;height:7px;border-radius:50%;background:{color};"
            f"flex-shrink:0'></span>"
            f"<span style=\"font-size:11px;font-weight:600;color:{color};"
            f"letter-spacing:.6px;font-family:{MONO}\">{text}</span></span>")


def grid(cards_html, min_px=220, gap=12):
    """카드들을 반응형 그리드로 — 900px 미만에서는 CSS(.qt-grid)가 1열로 접는다."""
    return (f"<div class='qt-grid' style='display:grid;gap:{gap}px;"
            f"grid-template-columns:repeat(auto-fit,minmax({min_px}px,1fr))'>"
            f"{''.join(cards_html)}</div>")
