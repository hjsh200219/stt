#!/usr/bin/env python3
"""Read-only Apple Voice Memos / exported audio -> local whisper.cpp -> Markdown.

No iCloud API, login, remote transcription or automatic downloads. Apple library
formats are private; only audio files are read. File dates are NOT recording dates.
"""
import argparse
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

AUDIO = {'.m4a', '.wav', '.mp3', '.aac', '.aiff', '.aif', '.flac', '.caf'}
HOME = Path.home()
DEFAULT_MODEL = HOME / '.local/share/stt/models/ggml-small.bin'
DEFAULT_OUT = HOME / '.stt/apple/notes'


def library_roots(home=HOME):
    return [home / p for p in (
        'Library/Group Containers/group.com.apple.VoiceMemos.shared/Recordings',
        'Library/Containers/com.apple.VoiceMemos/Data/Library/Application Support/Recordings',
        'Library/Application Support/com.apple.voicememos/Recordings',
    )]


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def discover(roots, explicit=False):
    files, seen = [], set()
    for root in roots:
        root = root.expanduser().absolute()
        if not root.exists():
            if explicit:
                raise ValueError(f'입력 경로 없음: {root}')
            continue
        if root.is_symlink():
            raise ValueError(f'심볼릭 링크 입력은 실제 경로로 지정하세요: {root}')
        if root.is_file():
            if root.suffix.lower() not in AUDIO:
                raise ValueError(f'지원하지 않는 음성 형식: {root.suffix}')
            candidates = [root]
        else:
            candidates = []
            def walk_error(error):
                raise error
            for directory, dirs, names in os.walk(root, onerror=walk_error):
                dirs[:] = [d for d in dirs if not d.startswith('.')
                           and 'deleted' not in d.lower() and 'trash' not in d.lower()
                           and not (Path(directory) / d).is_symlink()]
                candidates.extend(Path(directory) / n for n in names
                                  if not n.startswith('.') and Path(n).suffix.lower() in AUDIO)
        for path in candidates:
            if path.is_symlink():
                continue
            resolved = path.resolve()
            if resolved not in seen:
                seen.add(resolved)
                files.append(resolved)
    return sorted(files)


def atomic_write(path, text):
    fd, name = tempfile.mkstemp(prefix='.stt-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def run(command, timeout):
    try:
        subprocess.run(command, check=True, capture_output=True, timeout=timeout)
    except subprocess.CalledProcessError as e:
        # Engine logs may contain transcript text: do not echo them into context.
        raise ValueError(f'{Path(command[0]).name} 실패 (종료 코드 {e.returncode}); 원본·기존 노트 유지') from e
    except subprocess.TimeoutExpired as e:
        raise ValueError(f'{Path(command[0]).name} 시간 초과; 원본·기존 노트 유지') from e


def transcribe(audio, work, model, language, timeout):
    wav, output = work / 'audio.wav', work / 'result'
    run(['ffmpeg', '-nostdin', '-v', 'error', '-protocol_whitelist', 'file,pipe',
         '-i', str(audio), '-vn', '-ar', '16000', '-ac', '1', '-c:a', 'pcm_s16le', str(wav)], timeout)
    run(['whisper-cli', '-m', str(model), '-f', str(wav), '-l', language,
         '-oj', '-of', str(output), '-np'], timeout)
    data = json.loads(output.with_suffix('.json').read_text(encoding='utf-8'))
    segments = data.get('transcription') if isinstance(data, dict) else None
    if not isinstance(segments, list):
        raise ValueError('전사 결과 형식 오류')
    usable = []
    for segment in segments:
        if not isinstance(segment, dict) or not isinstance(segment.get('text'), str) or not isinstance(segment.get('offsets'), dict):
            raise ValueError('전사 결과 형식 오류')
        text = segment.get('text', '').strip()
        start = segment.get('offsets', {}).get('from')
        if text:
            if not isinstance(start, (int, float)) or start < 0:
                raise ValueError('전사 타임스탬프 오류')
            usable.append((int(start), text))
    if not usable:
        raise ValueError('인식된 음성이 없습니다; 완료 처리하지 않습니다')
    return usable


def render(path, sha, segments, model, language):
    title = path.stem
    fields = {'title': title, 'type': 'apple-voice-memo', 'source': 'local-audio',
              'source_file': str(path), 'audio_sha256': sha,
              'file_modified_at': datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
              'imported_at': datetime.now(timezone.utc).isoformat(),
              'transcriber': 'whisper.cpp', 'model': model.name, 'language': language}
    lines = ['---'] + [f'{k}: {json.dumps(v, ensure_ascii=False)}' for k, v in fields.items()]
    lines += ['---', '', '# ' + title.replace('\n', ' ').replace('\r', ' '), '',
              '> 로컬 자동 전사입니다. 이름·숫자는 원음과 대조하세요. 화자 분리는 제공하지 않습니다.',
              '> 제목은 파일명 기준이며 파일 수정 시각은 실제 녹음일과 다를 수 있습니다.', '', '## 전사', '']
    for ms, text in segments:
        seconds = ms // 1000
        clock = f'{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}'
        lines.append(f'- `{clock}` {text}')
    return '\n'.join(lines) + '\n'


def load_state(path):
    if not path.exists():
        return {'version': 1, 'items': {}}
    state = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(state, dict) or state.get('version') != 1 or not isinstance(state.get('items'), dict):
        raise ValueError('처리 이력 형식 오류; 자동 초기화하지 않습니다')
    for key, entry in state['items'].items():
        if not re.fullmatch(r'[0-9a-f]{24}', key) or not isinstance(entry, dict) or entry.get('note') != f'apple-{key}.md':
            raise ValueError('처리 이력 노트 경로 오류')
    return state


def sync(files, out, model, language='ko', timeout=7200, settle=60, force=False):
    out = out.expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    model = model.expanduser().resolve()
    if not model.is_file():
        raise ValueError(f'로컬 모델 없음: {model}; README의 최초 설치 안내를 확인하세요')
    for tool in ('ffmpeg', 'whisper-cli'):
        if not shutil.which(tool):
            raise ValueError(f'{tool} 설치 필요; doctor로 확인하세요')
    signature = f'{model}:{model.stat().st_size}:{model.stat().st_mtime_ns}:{language}'
    results = {'imported': [], 'skipped': [], 'errors': []}
    with (out / '.apple-stt.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            raise ValueError('동일 출력 폴더의 동기화가 이미 실행 중입니다') from e
        state_path = out / '.apple-stt-state.json'
        state = load_state(state_path)
        for path in files:
            try:
                stat = path.stat()
                if stat.st_size == 0 or time.time() - stat.st_mtime < settle:
                    raise ValueError('빈 파일 또는 녹음·동기화 중일 수 있어 보류합니다')
                key = hashlib.sha256(str(path).encode()).hexdigest()[:24]
                note = out / f'apple-{key}.md'
                old = state['items'].get(key)
                if note.exists() and (not old or digest(note) != old.get('note_sha256')):
                    raise ValueError('기존 노트가 수동 수정되었거나 이력이 없어 덮어쓰지 않습니다')
                with tempfile.TemporaryDirectory(prefix='stt-apple-') as td:
                    work = Path(td)
                    snapshot = work / ('source' + path.suffix.lower())
                    shutil.copyfile(path, snapshot)
                    sha = digest(snapshot)
                    after = path.stat()
                    if (stat.st_size, stat.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                        raise ValueError('복사 중 원본이 변경되어 보류합니다')
                    if not force and old and old.get('sha256') == sha and old.get('engine') == signature and note.exists():
                        results['skipped'].append(str(path))
                        continue
                    # Identical exports under another name should not be transcribed again.
                    duplicate = next((e for k, e in state['items'].items() if k != key
                                      and e.get('sha256') == sha and e.get('engine') == signature
                                      and (out / e['note']).is_file()), None)
                    if duplicate and not force:
                        results['skipped'].append(str(path))
                        continue
                    segments = transcribe(snapshot, work, model, language, timeout)
                    if (path.stat().st_size, path.stat().st_mtime_ns) != (stat.st_size, stat.st_mtime_ns):
                        raise ValueError('전사 중 원본이 변경되어 보류합니다')
                    if note.exists() and (not old or digest(note) != old.get('note_sha256')):
                        raise ValueError('전사 중 기존 노트가 수정되어 덮어쓰지 않습니다')
                    atomic_write(note, render(path, sha, segments, model, language))
                state['items'][key] = {'sha256': sha, 'engine': signature, 'note': note.name,
                                       'note_sha256': digest(note)}
                atomic_write(state_path, json.dumps(state, ensure_ascii=False, indent=2) + '\n')
                results['imported'].append(str(note))
            except (OSError, ValueError, KeyError, TypeError) as e:
                results['errors'].append({'file': str(path), 'error': str(e)})
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['list', 'import', 'doctor'])
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--input-dir', type=Path, help='내보낸 녹음 폴더 (하위 폴더 포함)')
    source.add_argument('--file', type=Path, help='단건 음성 파일')
    parser.add_argument('--all', action='store_true', help='발견한 전체 파일 (import 시 필수, --file은 제외)')
    parser.add_argument('--out', type=Path, default=Path(os.environ.get('APPLE_STT_OUT', DEFAULT_OUT)))
    parser.add_argument('--model', type=Path, default=Path(os.environ.get('APPLE_STT_MODEL', DEFAULT_MODEL)))
    parser.add_argument('--language', default='ko', help='ko 기본, 다국어 자동 감지는 auto')
    parser.add_argument('--timeout', type=int, default=7200, help='변환/전사 각각 최대 초')
    parser.add_argument('--settle-seconds', type=int, default=60)
    parser.add_argument('--force', action='store_true', help='변경 없는 음성도 재전사 (수동 수정 노트는 보호)')
    args = parser.parse_args()
    if args.timeout <= 0 or args.settle_seconds < 0:
        parser.error('timeout > 0, settle-seconds >= 0 이어야 합니다')
    explicit = args.file or args.input_dir or os.environ.get('APPLE_STT_INPUT')
    roots = [Path(explicit)] if explicit else library_roots()
    if args.command == 'doctor':
        source_error = None
        try:
            recording_count = len(discover(roots, explicit=bool(explicit)))
        except (OSError, ValueError) as exc:
            recording_count, source_error = 0, str(exc)
        report = {'ffmpeg': shutil.which('ffmpeg'), 'whisper_cli': shutil.which('whisper-cli'),
                  'model': str(args.model.expanduser()), 'model_exists': args.model.expanduser().is_file(),
                  'sources': [{'path': str(p), 'exists': p.expanduser().exists()} for p in roots],
                  'recording_count': recording_count, 'source_error': source_error,
                  'out': str(args.out.expanduser()), 'transcription': 'local-only'}
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report['ffmpeg'] and report['whisper_cli'] and report['model_exists'] and recording_count > 0 else 1
    files = discover(roots, explicit=bool(explicit))
    if not files:
        raise ValueError('녹음 파일 없음. Mac 음성 메모의 iCloud 동기화/다운로드를 확인하거나 --file / --input-dir로 내보낸 파일을 지정하세요. 권한 오류라면 실행 앱의 파일 접근 권한을 확인하세요.')
    if args.command == 'list':
        print(json.dumps([{'file': str(p), 'title': p.stem, 'bytes': p.stat().st_size} for p in files], ensure_ascii=False, indent=2))
        return 0
    if not args.all and not args.file:
        parser.error('import는 --all 또는 --file을 명시하세요')
    result = sync(files, args.out, args.model, args.language, args.timeout, args.settle_seconds, args.force)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result['errors'] else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError) as exc:
        print(f'apple-stt: {exc}', file=sys.stderr)
        sys.exit(1)
