# stt — 클로바노트 · Apple 음성 메모 → 지식 Vault

클로바노트의 기존 전사를 가져오거나, Apple 음성 메모를 로컬에서 새로 전사해
**Markdown 노트로 저장**하는 Claude Code 플러그인입니다. Obsidian 등 Markdown 기반 Vault와 사용할 수 있습니다.

| 소스 | 처리 방식 | 필요한 준비 |
|---|---|---|
| 클로바노트 | 서비스에 이미 생성된 전사·화자 구분을 가져오기 | 네이버 로그인·클로바노트 노트 |
| Apple 음성 메모 / 내보낸 음성 파일 | whisper.cpp로 이 Mac에서 새로 전사 | 로컬 녹음·ffmpeg·whisper-cli·GGML 모델 |

Apple 전사는 녹음을 외부로 업로드하지 않습니다. **플러그인 설치만으로 전사 엔진과 모델까지 설치되지는 않습니다.**

- [플러그인 설치](#설치)
- [사용 명령](#사용법)
- [Apple 음성 메모 설치·전사](#apple-음성-메모-v030)
- [클로바노트 설정](#클로바노트-설정)

```
stt/
├── .claude-plugin/{plugin.json, marketplace.json}
├── skills/
│   ├── clovanote/SKILL.md         # /stt:clovanote — 소스별 커맨드
│   └── apple/SKILL.md             # /stt:apple — 로컬 음성 전사
├── scripts/sources/
│   ├── apple/import.py            # 발견/로컬 전사/중복 방지
│   └── clovanote/                 # 소스 어댑터 1 — 네이버 클로바노트
│       ├── import.py              # 목록/적재 (stdlib urllib, 브라우저 0)
│       └── login.py               # ID/PW 무인 로그인 (playwright, 가드·stealth)
├── config/.env.example
└── LICENSE                        # MIT
```

소스별로 스킬 1개 — 소스 추가 = `scripts/sources/<source>/` + `skills/<source>/` → `/stt:<source>`.

## 설치

Claude Code에서:

```
/plugin marketplace add hjsh200219/stt
/plugin install stt@stt
/reload-plugins
```

v0.3.0부터 `stt:clovanote`와 `stt:apple` 두 스킬을 제공합니다.
플러그인 명령은 `/stt:clovanote`, `/stt:apple`입니다.
bare `/stt apple`은 별도로 설정한 로컬 alias이며 이 플러그인 설치만으로 생기지 않습니다.

> 업데이트: 코드가 바뀌면 `plugin.json` 의 `version` 이 오른다. `/plugin` 으로
> update 하거나, 캐시가 안 잡히면 `/plugin uninstall stt@stt` 후 재설치.

## 사용법

| 커맨드 | 동작 |
|--------|------|
| `/stt:clovanote` | 클로바노트 노트를 vault(`CLOVANOTE_OUT`)에 적재 (최근 전체) |
| `/stt:clovanote list` | 노트 목록만 |
| `/stt:clovanote auth` | 세션 로그인/갱신 |
| `/stt:apple` | 발견된 로컬 녹음을 전사해 Markdown으로 저장 |
| `/stt:apple list` | 녹음 파일 목록만 조회 (엔진·모델 불필요) |
| `/stt:apple doctor` | 입력 경로·녹음 수·전사 엔진·모델 확인 |

내보낸 파일을 처리할 때는 “`/stt:apple`로 `/절대/경로/회의.m4a`를 전사해줘”처럼
파일 경로를 함께 전달하세요. 아래 CLI 예시는 이 저장소 또는 설치된 플러그인의 루트에서 실행합니다.

클로바노트 세션이 만료되면(`import` 가 401) 스킬이 `login --auto` 재로그인 후 재시도한다.
보호조치로 막히면 `--seed`(사람 개입)를 안내한다.

## 클로바노트 설정

`~/.clovanote/.env` 를 만들어 값을 채운다(플러그인 업데이트에도 안 지워지는 안정 위치):

```bash
mkdir -p ~/.clovanote
cp -n config/.env.example ~/.clovanote/.env   # 저장소/플러그인 루트에서 실행, 기존 설정 보존
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

## 클로바노트 첫 로그인 (중요)

네이버는 순수 REST 로그인이 없다(RSA+봇탐지+CAPTCHA). 그래서:

```bash
python3 scripts/sources/clovanote/login.py --seed    # 1) 첫 로그인
python3 scripts/sources/clovanote/login.py --auto    # 2) 이후 무인
```

1. **`--seed`** — 헤디드 브라우저가 뜨면 사람이 **아이디 보호조치/CAPTCHA/2FA 를 1회 직접
   해제**한다. 전용 stealth 프로필(`CLOVANOTE_HOME/naver-clova`)에 기기 신뢰가 시딩됨.
2. 이후 **`--auto`** 가 그 신뢰로 무인 로그인 유지(가드: 쿨다운 30분 / 일 3회 / 연속일
   에스컬레이션 경고, challenge 시 즉시 중단).

콜드 프로필(시드 없이) 무인 로그인은 보호조치로 튕길 확률이 크다 — **반드시 seed 먼저.**
로그인 성공 시 세션이 `CLOVANOTE_HOME/clovanote-session.json` 에 저장되고, 이후 `/stt:clovanote` 는
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
- 새 소스 추가: `scripts/sources/<source>/` 어댑터 + `skills/<source>/SKILL.md`.

## Apple 음성 메모 (v0.3.0)

`/stt:apple`은 Mac으로 다운로드된 음성 메모 또는 내보낸 음성 파일을
**로컬 whisper.cpp**로 전사한다. 원본/Apple DB는 읽기 전용이며 녹음을 외부로 전송하지 않는다.
별도 Python 패키지는 필요 없다. macOS 대상이며 Python 3.10 이상을 권장한다.

### 최초 설치 (Mac)

```bash
HOMEBREW_NO_INSTALL_CLEANUP=1 brew install whisper.cpp ffmpeg
mkdir -p ~/.local/share/stt/models
curl -fL --retry 2 -o ~/.local/share/stt/models/ggml-small.bin.part \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin
```

모델은 다국어 `small`(약 488MB, 한국어 지원)이다. 다운로드 파일의 SHA-256을 확인한 뒤
`.part`를 제거해 `ggml-small.bin`으로 이름을 바꾼다. 이 릴리스에서 검증한 해시:
`1be3a9b2063867b937e64e2ec7483364a79917e157fa98c5d94b5c1fffea987b`.
확인 명령은 `shasum -a 256 ~/.local/share/stt/models/ggml-small.bin.part`이며,
일치할 때만 `mv -n ~/.local/share/stt/models/ggml-small.bin.part ~/.local/share/stt/models/ggml-small.bin`을 실행한다.
기존 모델 파일은 덮어쓰지 않는다. 설치 시에만 엔진/모델 다운로드에 네트워크가 필요하다.
상위 프로젝트: [whisper.cpp](https://github.com/ggml-org/whisper.cpp).

```bash
python3 scripts/sources/apple/import.py doctor
python3 scripts/sources/apple/import.py list
python3 scripts/sources/apple/import.py import --all --out /path/to/private-vault/apple
python3 scripts/sources/apple/import.py import --file /path/to/meeting.m4a --out /path/to/private-vault/apple
python3 scripts/sources/apple/import.py import --input-dir /path/to/exports --all
```

설정은 환경변수 또는 CLI 옵션으로 전달한다(`.env` 자동 로드는 하지 않음):

| 변수 | 기본값 / 의미 |
|---|---|
| `APPLE_STT_INPUT` | 미지정 시 알려진 Mac Voice Memos `Recordings` 경로 탐색 |
| `APPLE_STT_OUT` | `~/.stt/apple/notes` |
| `APPLE_STT_MODEL` | `~/.local/share/stt/models/ggml-small.bin` |

- 한국어 기본(`--language ko`), 다국어 자동 감지 `--language auto`.
- `.m4a`, `.wav`, `.mp3`, `.aac`, `.aiff`, `.aif`, `.flac`, `.caf` 지원. ffmpeg로 PCM WAV 임시 변환.
- 원본 변경 시 재전사, 동일 내용 건너뜀, 출력 폴더 단위 잠금, 실패 건 재시도 가능.
- 수동 수정한 노트는 `--force`여도 덮어쓰지 않는다. 원본 삭제를 Vault에 전파하지 않는다.
- 처리 이력 `.apple-stt-state.json`과 잠금 파일은 출력 폴더에 둔다. 개인 노트/이력은 public repo에 넣지 않는다.
- 최근 60초 변경 파일은 녹음/다운로드 중일 수 있어 보류. 완료된 테스트 파일만 `--settle-seconds 0` 사용.
- 제목은 파일명이고 `file_modified_at`은 파일 수정 시각이다. 실제 녹음일/앱 제목/화자 이름 복원은 아직 지원하지 않는다.
- 전사에는 타임스탬프가 포함되며 화자 분리·요약은 없다. Whisper 오인식/무음 환각 가능성이 있어 원음 대조 필요.
- Apple 저장 경로는 비공개 구현에 의존한다. 파일이 없으면 음성 메모의 iCloud 동기화/다운로드를 확인하거나 내보낸 파일을 지정한다.
  접근 거부 시에는 실행 앱의 파일 접근 권한을 확인한다. iCloud 계정 로그인이나 설정 변경은 수행하지 않는다.

검증: `python3 -m unittest discover -s tests -v`. 사용자 실제 녹음 없이 fixture로 실행할 수 있다.

### 문제가 생겼을 때

| 증상 | 확인할 사항 |
|---|---|
| 녹음 파일 없음 | Mac 음성 메모에 녹음이 다운로드되었는지 확인. 없으면 내보낸 파일을 `--file`로 지정 |
| 로컬 모델 없음 / 엔진 설치 필요 | 최초 설치 절차 실행 후 `doctor`로 확인 |
| 빈 파일 또는 최근 변경으로 보류 | 녹음·다운로드가 끝난 뒤 최소 60초 후 다시 실행 |
| 기존 노트 수정으로 저장 거부 | 편집 내용을 보존한 상태입니다. 원본 노트를 덮지 말고 다른 `--out` 폴더에 재전사 |
| 권한 오류 | 명시한 파일 경로와 실행 앱의 파일 접근 권한 확인 |

현재 검증 범위는 회귀 테스트 17개와 한국어 합성 M4A의 실제 로컬 전사·중복 건너뛰기입니다.
실제 사용자 음성 메모 라이브러리 수집은 녹음이 동기화된 환경에서 추가 확인이 필요합니다.

## License

MIT — see [LICENSE](./LICENSE).
