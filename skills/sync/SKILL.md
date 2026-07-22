---
name: sync
description: STT/음성 소스의 전사 노트를 지식 vault(마크다운)로 동기화. 트리거 — "/stt", "stt sync", "클로바노트 동기화", "클로바노트 저장", "음성 노트 vault 저장". 인자 — [source] [list|auth]. source 없으면 설정된 전 소스. 현재 소스: clovanote. 세션 만료 시 자동 재로그인.
argument-hint: "[source] [list|auth]"
---

# /stt:sync — STT 소스 → vault 동기화

STT/음성 전사 노트를 **소스별 어댑터**로 내려받아 vault(마크다운)에 적재한다.
소스는 `${CLAUDE_PLUGIN_ROOT}/scripts/sources/<source>/` 에 산다.
새 소스 추가 = 그 폴더 + 아래 표 1줄. (클로바노트는 여러 소스 중 하나)

## 소스

| source | 어댑터 | 설명 |
|--------|--------|------|
| `clovanote` | `scripts/sources/clovanote/` | 네이버 클로바노트 (비공개 API 재현) |

## 라우팅

| 입력 | 동작 |
|------|------|
| `/stt:sync` (인자 없음) | 설정된 전 소스 동기화 (현재 = clovanote) |
| `/stt:sync <source>` | 해당 소스만 동기화 |
| `/stt:sync <source> list` | 해당 소스 노트 목록 |
| `/stt:sync <source> auth` | 해당 소스 세션 로그인/갱신 |

인자에 소스명이 없으면 위 표의 소스를 순서대로 실행한다.

## clovanote 소스 실행

```bash
ROOT="${CLAUDE_PLUGIN_ROOT}/scripts/sources/clovanote"
python3 "$ROOT/import.py" import --all    # 동기화 (CLOVANOTE_OUT 에 적재, upsert)
python3 "$ROOT/import.py" list            # 노트 목록 (브라우저 0)
python3 "$ROOT/login.py"  --auto          # 무인 로그인 (가드 쿨다운30분·일3회)
python3 "$ROOT/login.py"  --seed          # 첫 로그인 (사람이 보호조치 해제)
```

**세션 만료 자동 복구**: `import`/`list` 가 "세션 없음"·"세션 만료"·HTTP 401 을 내면:
1. `python3 "$ROOT/login.py" --auto` 1회 → 성공 시 원래 명령 재시도.
2. `--auto` 가 challenge(보호조치/CAPTCHA)·가드차단이면 무인 재시도 금지. 사용자에게 안내:
   ```
   ! python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sources/clovanote/login.py --seed
   ```
   (창 뜨면 사람이 보호조치 해제·로그인 완료 → 자동 감지. 이후 `/stt:sync` 재실행.)

동기화 후 저장된 파일명을 사용자에게 간결히 보고한다.

## 설정 (첫 사용)

```bash
cp "${CLAUDE_PLUGIN_ROOT}/config/.env.example" "${CLAUDE_PLUGIN_ROOT}/config/.env"
# 편집: NAVER_ID, NAVER_PW, CLOVANOTE_OUT(노트 저장 경로)
pip install playwright && playwright install chromium   # login 용
```

- `CLOVANOTE_OUT` — 노트 마크다운 저장 경로(각자 vault 로 지정). 기본 `~/.clovanote/notes`.
- `CLOVANOTE_HOME` — 세션·프로필·상태 저장 홈(기본 `~/.clovanote`, gitignore 밖).
- `CLOVANOTE_ENV` — 설정 .env 경로 강제 지정(미지정 시 플러그인 `config/.env`).

## 주의

- 본인 계정·본인 데이터 export. 저속 요청(내장 0.8s)·가드로 계정 보호. 과도 호출 금지.
- 크리덴셜(`config/.env`)·세션(`CLOVANOTE_HOME`)은 커밋 금지.
- 상세: 플러그인 README, 어댑터 소스 docstring.
