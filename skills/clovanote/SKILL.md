---
name: clovanote
description: 네이버 클로바노트 전사 노트를 지식 vault(마크다운)로 동기화. 트리거 — "/stt:clovanote", "클로바노트 동기화", "클로바노트 저장", "클로바노트 노트 가져와". 인자 — 없음=최근 노트 전체 sync, `list`=목록만, `auth`=세션 로그인/갱신. 세션 만료 시 자동 재로그인.
argument-hint: "[list|auth]"
---

# /stt:clovanote — 네이버 클로바노트 → vault 동기화

네이버 클로바노트(clovanote.naver.com) 전사 노트를 API로 내려받아
vault(`CLOVANOTE_OUT`)에 마크다운으로 적재한다. `stt` 플러그인의 소스 어댑터 하나.
(새 소스는 `scripts/sources/<source>/` + `skills/<source>/` 로 추가 → `/stt:<source>`)

## 실행

```bash
ROOT="${CLAUDE_PLUGIN_ROOT}/scripts/sources/clovanote"
python3 "$ROOT/import.py" import --all    # 동기화 (CLOVANOTE_OUT 에 적재, upsert)
python3 "$ROOT/import.py" list            # 노트 목록 (브라우저 0)
python3 "$ROOT/login.py"  --auto          # 무인 로그인 (가드 쿨다운30분·일3회)
python3 "$ROOT/login.py"  --seed          # 첫 로그인 (사람이 보호조치 해제)
```

## 라우팅

| 입력 | 동작 |
|------|------|
| `/stt:clovanote` (인자 없음) | 최근 노트 전체 sync (`import --all`) → 저장 파일 보고 |
| `/stt:clovanote list` | 노트 목록만 |
| `/stt:clovanote auth` | 세션 로그인/갱신 (`login --auto`) |

## 세션 만료 자동 복구

`import`/`list` 가 "세션 없음"·"세션 만료"·HTTP 401 을 내면:
1. `python3 "$ROOT/login.py" --auto` 1회 → 성공 시 원래 명령 재시도.
2. `--auto` 가 challenge(보호조치/CAPTCHA)·가드차단이면 무인 재시도 금지. 사용자에게 안내:
   ```
   ! python3 ${CLAUDE_PLUGIN_ROOT}/scripts/sources/clovanote/login.py --seed
   ```
   (창 뜨면 사람이 보호조치 해제·로그인 완료 → 자동 감지. 이후 `/stt:clovanote` 재실행.)

동기화 후 저장된 파일명을 간결히 보고한다.

## 설정 (첫 사용)

`~/.clovanote/.env` 에 크리덴셜·출력을 둔다(플러그인 업데이트에도 안정):

```ini
NAVER_ID=your_naver_id
NAVER_PW=your_naver_pw
CLOVANOTE_OUT=~/notes/clova
```

```bash
pip install playwright && playwright install chromium   # login 용 (import/list 는 stdlib 만)
```

탐색 우선순위: `CLOVANOTE_ENV` > `~/.clovanote/.env` > 플러그인 `config/.env`.

## 주의

- 본인 계정·본인 데이터 export. 저속 요청(내장 0.8s)·가드로 계정 보호. 과도 호출 금지.
- 크리덴셜(`.env`)·세션(`CLOVANOTE_HOME`)은 커밋 금지.
- 상세: 플러그인 README, 어댑터 소스 docstring, memory [[clovanote_api_export]].
