# ATRsite-pro 배포 절차

2026-08-02 RackNerd VPS(Ubuntu 24.04, KVM)로 실제 배포 완료 -- 이 문서
그대로 따라서 진행했다(오라클 대신 RackNerd를 썼을 뿐 절차는 동일). 아래
내용은 그 실제 배포 경험을 반영해 갱신함.

**중요 -- 시간대(timezone) 필수 확인**: `market_schedule.py`의 장중 판정
로직 전체가 `datetime.now()`가 KST를 반환한다고 가정한다(로컬 개발 PC는
이미 한국시간이라 문제가 없었음). 서버가 기본 UTC로 뜨는 경우가 많으므로
(RackNerd NY 리전도 기본 UTC였음), 배포 직후 반드시:
```bash
timedatectl                       # Time zone이 Asia/Seoul인지 확인
sudo timedatectl set-timezone Asia/Seoul   # 아니면 이걸로 바꾸고
sudo systemctl restart atrsite-worker atrsite-backup.timer  # 재시작
```
이걸 안 하면 워커의 장중/마감 판정이 9시간 밀려서 완전히 틀어지고,
`atrsite-backup.timer`도 16:30 KST가 아니라 16:30 UTC(한국시간 새벽
1시반)에 돈다.

## 0. 시작하기 전에 -- 이 저장소에 있는 위험 파일

`C:\strpro` 루트에 `ssh-key-2026-08-01.key` / `.key.pub` 파일이 있다. 이름 형식이
오라클 콘솔이 인스턴스 생성 시 자동 생성해주는 SSH 키페어와 같다. **이 파일들은
`.gitignore`에 이미 추가해뒀지만, git 저장소 자체를 다른 곳(GitHub 등)에 올리기
전에 정말로 커밋되지 않았는지 `git status`로 반드시 재확인할 것.** 가능하면 이
키 파일들을 저장소 폴더 밖(예: `~/.ssh/`)으로 옮겨두는 편이 안전하다.

## 1. 사전 준비

- Oracle Cloud 인스턴스 (Ampere A1 ARM 또는 AMD, Ubuntu 22.04+ 권장)
- 인스턴스에 Python 3.12, git, (선택) Caddy 또는 Nginx 설치
- 방화벽/보안목록: 22(SSH), 443(HTTPS, 리버스 프록시 쓸 경우)만 외부 개방.
  8000(uvicorn)은 `127.0.0.1`에만 바인딩하므로 외부 개방 불필요

## 2. SSH 접속 → clone

```bash
ssh -i ~/.ssh/ssh-key-2026-08-01.key ubuntu@<서버IP>

sudo mkdir -p /opt/atrsite
sudo chown $USER:$USER /opt/atrsite
git clone <저장소 URL> /opt/atrsite
cd /opt/atrsite
```

## 3. 가상환경 + 의존성

```bash
python3.12 -m venv /opt/atrsite/venv
/opt/atrsite/venv/bin/pip install -r requirements.txt
```

서버를 처음부터 다시 설치하는 상황(재해복구 등)이라 지금 검증된 조합을 정확히
재현하고 싶으면, 간접 의존성(starlette 등)까지 전부 고정한
`requirements-lock.txt`를 대신 써도 된다(2026-08-06 추가, 클린 Python 3.12.3
venv에서 생성):

```bash
/opt/atrsite/venv/bin/pip install -r requirements-lock.txt
```

## 4. 값 채우기

```bash
cp .env.example .env
chmod 600 .env
nano .env   # KIS_APP_KEY, KIS_APP_SECRET, KIS_ACCOUNT_NO, TELEGRAM_BOT_TOKEN,
            # TELEGRAM_CHAT_ID, API_KEY 등 실제 값 채우기 (스펙 14.2 -- git에는 절대 안 올라감)
```

`data/`, `backups/` 디렉터리를 만들고 서비스 계정에 쓰기 권한을 준다.

```bash
mkdir -p /opt/atrsite/data /opt/atrsite/backups
sudo useradd --system --no-create-home atrsite
sudo chown -R atrsite:atrsite /opt/atrsite
```

## 5. systemd 등록

```bash
sudo cp systemd/atrsite-web.service systemd/atrsite-worker.service \
        systemd/atrsite-backup.service systemd/atrsite-backup.timer \
        /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable --now atrsite-web.service
sudo systemctl enable --now atrsite-worker.service
sudo systemctl enable --now atrsite-backup.timer

# 상태 확인
sudo systemctl status atrsite-web atrsite-worker atrsite-backup.timer
sudo journalctl -u atrsite-web -f
```

`atrsite-web.service`는 `127.0.0.1:8000`에서만 대기한다. 외부에 노출하려면
Caddy/Nginx를 리버스 프록시로 앞에 둔다 (스펙 14.1 "API의 동일 출처 사용" --
프런트엔드 정적 파일도 같은 FastAPI 프로세스가 서빙하므로 프록시는 도메인/HTTPS
종단 역할만 하면 된다). 실제 배포에서 쓴 nginx 설정은 `nginx/atrsite.conf`에
있다(도메인 없이 HTTP 80만 프록시 -- HTTPS는 도메인 생기면 certbot으로 추가):
```bash
sudo cp nginx/atrsite.conf /etc/nginx/sites-available/atrsite
sudo ln -sf /etc/nginx/sites-available/atrsite /etc/nginx/sites-enabled/atrsite
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl restart nginx && sudo systemctl enable nginx
```

`API_KEY`는 인터넷에 노출되는 대시보드를 아무나 못 보게 막는 최소한의
방어선이다 -- 반드시 채울 것(`.env`의 다른 KIS/텔레그램 값과 달리 이건
제3자 자격증명이 아니라 이 앱 자체의 접근 인증용이라 `openssl rand -hex 24`
같은 걸로 새로 생성해도 무방하다). 프런트엔드는 처음 접속 시
`window.prompt()`로 물어봐서 브라우저 `localStorage`에 저장해둔다.

journald 로그가 무한정 쌓이지 않도록 상한을 설정해둔다:
```bash
sudo mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nSystemMaxUse=200M\n' | sudo tee /etc/systemd/journald.conf.d/atrsite.conf
sudo systemctl restart systemd-journald
```

## 6. 백업 정책 (스펙 15절)

- `atrsite-backup.timer`가 매일 16:30(KST, 정규장 마감 후)에 `scripts/backup.py`를 실행한다
- 로컬(`/opt/atrsite/backups`)에는 최신 2개만 남긴다 (자동)
- **아직 구현 안 됨**: Oracle Object Storage 업로드, 원격 14개 초과분 삭제
  (`scripts/backup.py`의 TODO 참고 -- 오라클 계정/버킷이 준비되면 `oci` SDK로
  채워 넣을 것. 그때까지는 로컬 백업 2개가 유일한 백업본이라는 점을 인지할 것)
- 매월 한 번 `python scripts/restore_check.py`로 최신 백업이 실제로 복원 가능한지
  확인한다 (지금은 로컬 최신 백업 기준. Object Storage 연동 후에는 원격 다운로드로 교체)

## 7. 배포 후 남은 TODO (이 코드베이스에 이미 표시돼 있음)

- `backend/atrsite/adapters/kis_client.py`의 `get_current_price()` /
  `get_daily_bars()` -- 실제 KIS 시세조회 TR 호출 코드 (현재는 더미 데이터)
- `backend/atrsite/services/market_schedule.py`의 `MANUAL_HOLIDAYS` -- 설날/추석
  등 음력 연휴, 임시공휴일, 대체공휴일, 수능일 개장시간 변경분을 한국거래소
  공지 기준으로 채워 넣을 것
- `scripts/backup.py` / `scripts/restore_check.py`의 Object Storage 업로드/다운로드
- 텔레그램 실제 Bot Token/Chat ID 발급 후 `.env`에 채우면 자동으로 더미 모드가
  풀리고 실제 발송으로 전환됨 (코드 변경 불필요)

## 8. 재배포(코드 업데이트) 절차

```bash
cd /opt/atrsite
git pull
/opt/atrsite/venv/bin/pip install -r requirements.txt
sudo systemctl restart atrsite-web atrsite-worker
```

`atrsite-web`과 `atrsite-worker`를 분리 배포한 이유(스펙 5.2)대로, 웹만 재시작해도
Worker의 장중 시세 감시는 끊기지 않는다 -- 반대의 경우도 마찬가지.
