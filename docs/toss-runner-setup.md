# 토스 연동 러너 설정 — 오라클 무료 VM

## 왜 필요한가

토스 오픈API는 **등록된 IP에서 온 요청만 받습니다.** 다른 IP에서 오면 자격증명이 아무리 정확해도 이렇게 거부합니다.

```
{"error":"access_denied","error_description":"IP address not allowed"}
```

2026-07-16에 워크플로를 자택 PC에서 GitHub 클라우드로 옮겼습니다(커밋 `40b1790`). 클라우드 러너는 실행할 때마다 IP가 바뀌기 때문에, 그날 이후 토스 호출이 전부 막혔습니다. 일별 P&L 리포트가 07-17부터 12일간 한 번도 안 나갔습니다.

**해결 방향**: IP가 절대 안 바뀌고 24시간 켜져 있는 서버를 하나 두고, 그 IP를 토스에 등록합니다. 오라클 클라우드가 이런 서버를 평생 무료로 줍니다.

---

## 1단계 — 오라클 클라우드 계정 만들기 (사장님)

1. https://www.oracle.com/kr/cloud/free/ 접속 → "무료로 시작하기"
2. 국가는 **대한민국**, 홈 리전은 **South Korea Central (Seoul)** 선택
   - 홈 리전은 **나중에 못 바꿉니다.** 서울로 하세요.
3. 신용/체크카드 등록 (해외결제 가능 카드)
   - 본인 확인용입니다. Always Free 범위 안에서는 청구되지 않습니다.
   - 가입 직후 30일 무료 크레딧이 붙는데, 그게 끝나도 Always Free 자원은 계속 무료입니다.

> **주의**: 30일이 지나면 계정이 자동으로 "Always Free" 등급으로 내려갑니다. 이때 Always Free가 아닌 자원(고사양 VM 등)을 만들어 뒀다면 정지됩니다. 아래 2단계 사양만 지키면 문제없습니다.

## 2단계 — VM 만들기 (사장님)

콘솔에서 **Compute → Instances → Create instance**

| 항목 | 값 | 이유 |
|---|---|---|
| Image | **Canonical Ubuntu 24.04** | 러너 설치가 가장 쉬움 |
| Shape | **VM.Standard.A1.Flex** (ARM) — OCPU 1, 메모리 6GB | Always Free 범위. 넉넉함 |
| Public IP | **할당함(Assign a public IPv4 address)** | 필수 |
| SSH 키 | **Save private key** 눌러 다운로드 | 접속에 필요. 잃어버리면 재발급 불가 |

> ARM(A1) 재고가 없다고 나오면 **VM.Standard.E2.1.Micro** (AMD)를 고르세요. 이것도 Always Free이고 이 용도에는 충분합니다.

### 2-1. IP를 고정으로 바꾸기 (중요)

기본으로 주는 공인 IP는 **임시(Ephemeral)** 라서 인스턴스를 재시작하면 바뀔 수 있습니다. 반드시 예약(Reserved)으로 바꾸세요.

인스턴스 상세 → **Attached VNICs** → VNIC 클릭 → **IPv4 Addresses** → 오른쪽 점 세 개 → **Edit** → Public IP Type을 **Reserved IP address**로 변경 → 저장

이렇게 나온 IP 주소를 적어두세요. 이게 토스에 등록할 값입니다.

## 3단계 — 토스에 IP 등록 (사장님)

토스증권 오픈API 개발자센터 로그인 → 해당 앱 설정 → **허용 IP 목록**에 2-1에서 받은 IP 추가 → 저장

> 기존에 등록돼 있던 자택 IP는 지우지 마세요. 로컬에서 직접 테스트할 때 필요합니다.

## 4단계 — VM에 러너 설치 (제가 안내, 사장님이 붙여넣기)

VM에 SSH로 접속한 뒤 아래를 순서대로 실행합니다. `<...>` 부분은 실제 값으로 바꿉니다.

```bash
# 파이썬과 기본 도구
sudo apt update && sudo apt install -y python3-pip python3-venv git curl

# 러너 설치 (ARM VM 기준. AMD면 arm64 → x64로 바꿀 것)
mkdir -p ~/actions-runner && cd ~/actions-runner
curl -o runner.tar.gz -L https://github.com/actions/runner/releases/download/v2.330.0/actions-runner-linux-arm64-2.330.0.tar.gz
tar xzf runner.tar.gz
```

등록 토큰은 GitHub에서 발급받습니다:
저장소 → **Settings → Actions → Runners → New self-hosted runner** → 화면에 나오는 `--token` 값 복사

```bash
# 러너 등록 (토큰은 1시간만 유효)
./config.sh --url https://github.com/YEJUN-0716/stock-analyzer --token <복사한_토큰> --labels toss --unattended

# 항상 켜져 있도록 서비스로 등록
sudo ./svc.sh install
sudo ./svc.sh start
sudo ./svc.sh status
```

`Active: active (running)` 이 보이면 성공입니다.

## 5단계 — 워크플로 연결 (제가 함)

토스를 호출하는 워크플로만 이 러너로 보냅니다.

| 워크플로 | 러너 | 이유 |
|---|---|---|
| `daily-report.yml` | `[self-hosted, toss]` | 토스 호출 |
| `paper-trade-us.yml` | `[self-hosted, toss]` | 토스 호출 |
| `signal-alerts.yml` | `ubuntu-latest` (유지) | 토스 안 씀 |

## 6단계 — 검증

```
gh workflow run daily-report.yml -R YEJUN-0716/stock-analyzer
```

텔레그램으로 P&L 리포트가 오면 완료입니다.

---

## 막혔을 때

| 증상 | 원인 | 조치 |
|---|---|---|
| 여전히 `IP address not allowed` | 토스에 등록한 IP와 VM 실제 IP가 다름 | VM에서 `curl https://api.ipify.org` 실행해 실제 나가는 IP 확인 후 재등록 |
| 워크플로가 대기만 함 | 러너가 죽었거나 라벨 불일치 | VM에서 `sudo ./svc.sh status`, GitHub Runners 화면에서 초록불 확인 |
| ARM 재고 없음 | 서울 리전 A1 포화 | E2.1.Micro(AMD)로 생성, 러너 다운로드 URL을 `x64`로 변경 |
| 30일 후 인스턴스 정지 | Always Free 밖 사양 선택 | 2단계 사양표대로 재생성 |

## 재발 방지

- **러너를 옮기기 전에 외부 API의 IP 제한을 확인할 것.** 이번 건은 러너 이전이 원인이었는데 증상은 인증 오류로 나타나 원인 파악이 늦어졌습니다.
- 토스 호출 실패 시 응답 본문이 예외 메시지에 포함됩니다(PR #41). 다음에 같은 일이 나면 로그 한 줄로 바로 원인이 보입니다.
