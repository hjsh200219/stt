---
name: apple
description: Apple 음성 메모 또는 내보낸 녹음 파일을 로컬 전사하여 Markdown vault에 저장한다. 음성 메모 가져오기·전사·목록·동기화 요청에 사용한다.
---

# Apple 음성 메모 → Vault

플러그인 루트의 `scripts/sources/apple/import.py`를 호출한다. Python 3, ffmpeg,
whisper.cpp의 `whisper-cli`와 로컬 GGML 모델이 필요하다. 최초 설치는 루트 README의
Apple 섹션을 참고한다. `list`에는 전사 엔진이 필요하지 않다.

```bash
python3 <plugin-root>/scripts/sources/apple/import.py doctor
python3 <plugin-root>/scripts/sources/apple/import.py list
python3 <plugin-root>/scripts/sources/apple/import.py import --all
python3 <plugin-root>/scripts/sources/apple/import.py import --file /absolute/path/recording.m4a
python3 <plugin-root>/scripts/sources/apple/import.py import --input-dir /absolute/path/exports --all
```

- 출력: `APPLE_STT_OUT` 또는 `--out`. 기본 `~/.stt/apple/notes`.
- 입력: `APPLE_STT_INPUT` 또는 `--file`/`--input-dir`. 미지정 시 Mac의 알려진 Voice Memos 경로 탐색.
- 모델: `APPLE_STT_MODEL` 또는 `--model`. 기본 `~/.local/share/stt/models/ggml-small.bin`.
- 한국어 기본. 다국어는 `--language auto`. 자동 화자 분리 및 요약은 제공하지 않는다.
- 녹음이 없으면 동기화 완료라고 보고하지 않는다. Mac 음성 메모에 다운로드되어 있어야 하며,
  없으면 iCloud 동기화 또는 파일 내보내기를 안내한다. 접근 거부는 실행 앱의 권한 문제로 구분한다.
- 원본/Apple DB/동기화 설정을 수정하지 않는다. 녹음을 외부로 업로드하지 않는다.
- 최근 60초 내 변경 파일은 보류한다. `--settle-seconds 0`은 완료된 내보내기/테스트 파일에만 사용한다.
- 파일 내용 해시로 중복을 건너뛰고 변경된 음성만 재전사한다. `--force`도 수동 수정된 노트를 덮어쓰지 않는다.
- 제목은 파일명, 시간은 파일 수정 시각이다. Apple 앱의 실제 제목·녹음일을 복원했다고 주장하지 않는다.
- `.apple-stt-state.json`과 `.apple-stt.lock`은 출력 폴더의 처리 이력/동시 실행 보호용이므로 유지한다.
- 출력은 파일명과 성공·건너뜀·실패 수를 보고한다. 대화에 전체 전사 내용을 자동 출력하지 않는다.

macOS의 비공개 저장 경로는 버전에 따라 바뀔 수 있다. 자동 발견이 안 되면 명시적 파일 경로를 사용한다.
