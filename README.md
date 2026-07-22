# stt — STT 소스 → 지식 vault 동기화 (Claude Code 플러그인)

음성/STT 서비스의 **전사 노트를 마크다운 vault로 동기화**하는 Claude Code 플러그인.
소스별 어댑터 구조 — 클로바노트는 여러 소스 중 하나다.

```
stt/
├── .claude-plugin/{plugin.json, marketplace.json}
├── skills/sync/SKILL.md          # /stt:sync — 소스-범용 커맨드
├── scripts/sources/
│   └── clovanote/                 # 소스 어댑터 1 — 네이버 클로바노트
│       ├── import.py              # 목록/적재 (stdlib urllib, 브라우저 0)
│       └── login.py               # ID/PW 무인 로그인 (playwright, 가드·stealth)
└── config/.env.example
```

## 설치

Claude Code에서:

```
/plugin marketplace add hjsh200219/stt
/plugin install stt@stt
/reload-plugins
```

설치되면 스킬 목록에 `stt:sync` 가 뜬다. **커맨드는 `/stt:sync`** (플러그인 스킬은
`plugin:skill` 네임스페이스라 bare `/stt` 아님).

> 업데이트: 코드가 바뀌면 `plugin.json` 의 `version` 이 오른다. `/plugin` 으로
> update 하거나, 캐시가 안 잡히면 `/plugin uninstall stt@stt` 후 재설치.

## 사용법

| 커맨드 | 동작 |
|--------|------|
| `/stt:sync` | 설정된 전 소스 동기화 (현재 = clovanote) |
| `/stt:sync clovanote` | 클로바노트 노트를 vault(`CLOVANOTE_OUT`)에 적재 |
| `/stt:sync clovanote list` | 노트 목록만 |
| `/stt:sync clovanote auth` | 세션 로그인/갱신 |

세션이 만료되면(`import` 가 401) 커맨드가 자동으로 `login --auto` 재로그인 후 재시도한다.
보호조치로 막히면 `--seed`(사람 개입)를 안내한다.

## 설정

`~/.clovanote/.env` 를 만들어 값을 채운다(플러그인 업데이트에도 안 지워지는 안정 위치):

```bash
mkdir -p ~/.clovanote
cp "$(경로)/config/.env.example" ~/.clovanote/.env   # 또는 아래 내용 직접 작성
```

```ini
NAVER_ID=your_naver_id
NAVER_PW=your_naver_pw
CLOVANOTE_OUT=~/notes/clova          # 노트 저장 경로(각자 vault)
```

```bash
pip install playwright && playwright install chromium   # login 용 (import/list 는 stdlib 만)
```

설정 탐색 우선순위: `CLOVANOTE_ENV`(파일 경로 강제) > `~/.clovanote/.env` > 플러그인 `config/.env`.

- `CLOVANOTE_OUT` — 노트 마크다운 저장 경로. 기본 `~/.clovanote/notes`.
- `CLOVANOTE_HOME` — 세션·프로필·가드상태 홈. 기본 `~/.clovanote`(repo 밖).
- `CLOVANOTE_ENV` — 설정 .env 경로 강제 지정.

## 첫 로그인 (중요)

네이버는 순수 REST 로그인이 없다(RSA+봇탐지+CAPTCHA). 그래서:

```bash
ROOT=<설치된 플러그인>/scripts/sources/clovanote
python3 "$ROOT/login.py" --seed    # 1) 첫 로그인
python3 "$ROOT/login.py" --auto    # 2) 이후 무인
```

1. **`--seed`** — 헤디드 브라우저가 뜨면 사람이 **아이디 보호조치/CAPTCHA/2FA 를 1회 직접
   해제**한다. 전용 stealth 프로필(`CLOVANOTE_HOME/naver-clova`)에 기기 신뢰가 시딩됨.
2. 이후 **`--auto`** 가 그 신뢰로 무인 로그인 유지(가드: 쿨다운 30분 / 일 3회 / 연속일
   에스컬레이션 경고, challenge 시 즉시 중단).

콜드 프로필(시드 없이) 무인 로그인은 보호조치로 튕길 확률이 크다 — **반드시 seed 먼저.**
로그인 성공 시 세션이 `CLOVANOTE_HOME/clovanote-session.json` 에 저장되고, 이후 `/stt:sync` 는
브라우저 없이 그 세션으로 동작한다.

## 클로바노트 어댑터 — 재현한 내부 API

공개 API 없음. 웹 SPA 의 `clovanote-api.naver.com/v2` 를 로그인 세션 쿠키로 재현:

| 용도 | 엔드포인트 |
|------|-----------|
| auth | NID_AUT/NID_SES 쿠키 + `note-*` 헤더 (Authorization/CSRF 없음) |
| user | `GET /v2/user` → `workspaces[]` |
| list | `GET /v2/w/{ws}/notes?category=ALL&folderId=&sortKey=UPDATED-DATE&sortOrder=DESC&limit=N` |
| note | `GET /v2/w/{ws}/notes/{id}` → `noteInfo`(메타) + `script.blockList`(전사) |

노트는 frontmatter(제목·일시·참석자·키워드) + 미리보기 + **화자분리 타임스탬프 전사**로 저장.

## 보안·주의

- 본인 계정·본인 데이터 export 용. 저속 요청·가드로 계정 보호(과도 호출 = 잠금 위험).
- 크리덴셜(`.env`)·세션·프로필은 커밋 금지(`.gitignore` 처리 + `CLOVANOTE_HOME` 은 repo 밖).
- 새 소스 추가: `scripts/sources/<source>/` 어댑터 + `skills/sync/SKILL.md` 표 1줄.
