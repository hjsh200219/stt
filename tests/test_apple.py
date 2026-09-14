import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
APPLE_PATH = ROOT / "scripts" / "sources" / "apple" / "import.py"


def load_apple():
    spec = importlib.util.spec_from_file_location("apple_import", APPLE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


apple = load_apple()


class AppleTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def touch_audio(self, relative, data=b"audio"):
        path = self.base / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        old = time.time() - 120
        os.utime(path, (old, old))
        return path

    def make_model(self):
        model = self.base / "ggml-small.bin"
        model.write_bytes(b"model")
        return model

    def sync(self, files, out=None, model=None, settle=0, **kwargs):
        out = out or (self.base / "out")
        model = model or self.make_model()
        with mock.patch.object(apple.shutil, "which", return_value="/usr/local/bin/tool"):
            return apple.sync(files, out, model, settle=settle, **kwargs)


class DiscoverTests(AppleTestCase):
    def test_discover_finds_audio_and_skips_deleted_dirs_symlinks_and_duplicates(self):
        keep = self.touch_audio("Recordings/keep.m4a")
        self.touch_audio("Recordings/Deleted/old.m4a")
        self.touch_audio("Recordings/Trash/bin.wav")
        self.touch_audio("Recordings/.hidden/hidden.m4a")
        duplicate_link = self.base / "Recordings" / "link.m4a"
        duplicate_link.symlink_to(keep)

        found = apple.discover([self.base / "Recordings"], explicit=True)

        self.assertEqual(found, [keep.resolve()])

    def test_discover_accepts_file_and_rejects_bad_explicit_inputs(self):
        audio = self.touch_audio("one.wav")
        self.assertEqual(apple.discover([audio], explicit=True), [audio.resolve()])

        (self.base / "not-audio.txt").write_text("not audio", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "지원하지 않는 음성 형식"):
            apple.discover([self.base / "not-audio.txt"], explicit=True)
        with self.assertRaisesRegex(ValueError, "입력 경로 없음"):
            apple.discover([self.base / "missing"], explicit=True)

        link = self.base / "root-link"
        link.symlink_to(self.base)
        with self.assertRaisesRegex(ValueError, "심볼릭 링크 입력"):
            apple.discover([link], explicit=True)


class TranscribeTests(AppleTestCase):
    def test_transcribe_parses_whisper_json_segments(self):
        audio = self.touch_audio("source.m4a")
        model = self.make_model()
        work = self.base / "work"
        work.mkdir()

        def fake_run(command, timeout):
            if command[0] == "whisper-cli":
                output = Path(command[command.index("-of") + 1]).with_suffix(".json")
                output.write_text(
                    json.dumps(
                        {
                            "transcription": [
                                {"offsets": {"from": 0}, "text": " hello "},
                                {"offsets": {"from": 1250}, "text": "world"},
                                {"offsets": {"from": 2000}, "text": "   "},
                            ]
                        }
                    ),
                    encoding="utf-8",
                )

        with mock.patch.object(apple, "run", side_effect=fake_run):
            self.assertEqual(
                apple.transcribe(audio, work, model, "ko", 30),
                [(0, "hello"), (1250, "world")],
            )

    def test_transcribe_rejects_invalid_json_shape_and_timestamps(self):
        audio = self.touch_audio("source.m4a")
        model = self.make_model()
        work = self.base / "work"
        work.mkdir()

        def fake_run_bad_shape(command, timeout):
            if command[0] == "whisper-cli":
                Path(command[command.index("-of") + 1]).with_suffix(".json").write_text(
                    json.dumps({"text": "no segments"}),
                    encoding="utf-8",
                )

        with mock.patch.object(apple, "run", side_effect=fake_run_bad_shape):
            with self.assertRaisesRegex(ValueError, "전사 결과 형식 오류"):
                apple.transcribe(audio, work, model, "ko", 30)

        def fake_run_bad_timestamp(command, timeout):
            if command[0] == "whisper-cli":
                Path(command[command.index("-of") + 1]).with_suffix(".json").write_text(
                    json.dumps({"transcription": [{"offsets": {"from": -1}, "text": "bad"}]}),
                    encoding="utf-8",
                )

        with mock.patch.object(apple, "run", side_effect=fake_run_bad_timestamp):
            with self.assertRaisesRegex(ValueError, "전사 타임스탬프 오류"):
                apple.transcribe(audio, work, model, "ko", 30)

    def test_transcribe_rejects_malformed_engine_json_segments(self):
        audio = self.touch_audio("source.m4a")
        model = self.make_model()

        for payload in (
            [],
            {"transcription": [None]},
            {"transcription": [{"offsets": {}, "text": 123}]},
        ):
            with self.subTest(payload=payload):
                work = self.base / ("work-" + str(abs(hash(json.dumps(payload, default=str)))))
                work.mkdir()

                def fake_run(command, timeout, payload=payload):
                    if command[0] == "whisper-cli":
                        Path(command[command.index("-of") + 1]).with_suffix(".json").write_text(
                            json.dumps(payload),
                            encoding="utf-8",
                        )

                with mock.patch.object(apple, "run", side_effect=fake_run):
                    with self.assertRaisesRegex(ValueError, "전사 결과 형식 오류"):
                        apple.transcribe(audio, work, model, "ko", 30)


class SyncTests(AppleTestCase):
    def test_sync_imports_then_skips_unchanged_audio(self):
        audio = self.touch_audio("memo.m4a", b"first")
        out = self.base / "out"
        model = self.make_model()

        with mock.patch.object(apple, "transcribe", return_value=[(0, "안녕하세요")]) as transcribe:
            first = self.sync([audio], out=out, model=model)
            second = self.sync([audio], out=out, model=model)

        self.assertEqual(len(first["imported"]), 1)
        self.assertEqual(second["skipped"], [str(audio)])
        self.assertEqual(transcribe.call_count, 1)
        note = Path(first["imported"][0])
        self.assertIn("안녕하세요", note.read_text(encoding="utf-8"))

    def test_sync_retranscribes_changed_audio(self):
        audio = self.touch_audio("memo.m4a", b"first")
        out = self.base / "out"

        with mock.patch.object(apple, "transcribe", side_effect=[[(0, "처음")], [(0, "변경")]]):
            first = self.sync([audio], out=out)
            audio.write_bytes(b"second")
            old = time.time() - 120
            os.utime(audio, (old, old))
            second = self.sync([audio], out=out)

        self.assertEqual(Path(first["imported"][0]), Path(second["imported"][0]))
        self.assertIn("변경", Path(second["imported"][0]).read_text(encoding="utf-8"))

    def test_sync_retries_after_failed_transcription_without_state_poisoning(self):
        audio = self.touch_audio("memo.m4a")
        out = self.base / "out"

        with mock.patch.object(apple, "transcribe", side_effect=ValueError("engine down")):
            failed = self.sync([audio], out=out)
        self.assertEqual(len(failed["errors"]), 1)
        self.assertFalse((out / ".apple-stt-state.json").exists())

        with mock.patch.object(apple, "transcribe", return_value=[(0, "복구")]):
            recovered = self.sync([audio], out=out)
        self.assertEqual(len(recovered["imported"]), 1)

    def test_sync_protects_manually_edited_notes(self):
        audio = self.touch_audio("memo.m4a")
        out = self.base / "out"

        with mock.patch.object(apple, "transcribe", return_value=[(0, "원본")]):
            imported = self.sync([audio], out=out)
        note = Path(imported["imported"][0])
        note.write_text(note.read_text(encoding="utf-8") + "\nmanual edit\n", encoding="utf-8")

        with mock.patch.object(apple, "transcribe", return_value=[(0, "덮어쓰기")]):
            result = self.sync([audio], out=out, force=True)
        self.assertEqual(result["imported"], [])
        self.assertRegex(result["errors"][0]["error"], "수동 수정")
        self.assertIn("manual edit", note.read_text(encoding="utf-8"))

    def test_sync_protects_note_edited_during_transcription(self):
        audio = self.touch_audio("memo.m4a", b"first")
        out = self.base / "out"
        model = self.make_model()

        with mock.patch.object(apple, "transcribe", return_value=[(0, "원본")]):
            imported = self.sync([audio], out=out, model=model)
        note = Path(imported["imported"][0])

        audio.write_bytes(b"second")
        old = time.time() - 120
        os.utime(audio, (old, old))

        def edit_note_during_transcribe(*args):
            note.write_text(note.read_text(encoding="utf-8") + "\nmid-flight edit\n", encoding="utf-8")
            return [(0, "새 전사")]

        with mock.patch.object(apple, "transcribe", side_effect=edit_note_during_transcribe):
            result = self.sync([audio], out=out, model=model)

        self.assertEqual(result["imported"], [])
        self.assertRegex(result["errors"][0]["error"], "전사 중 기존 노트가 수정")
        text = note.read_text(encoding="utf-8")
        self.assertIn("원본", text)
        self.assertIn("mid-flight edit", text)
        self.assertNotIn("새 전사", text)

    def test_sync_defers_empty_and_unsettled_files(self):
        empty = self.touch_audio("empty.m4a", b"")
        fresh = self.touch_audio("fresh.m4a", b"audio")
        now = time.time()
        os.utime(fresh, (now, now))

        with mock.patch.object(apple, "transcribe", return_value=[(0, "ignored")]) as transcribe:
            result = self.sync([empty, fresh], settle=60)

        self.assertEqual(result["imported"], [])
        self.assertEqual(len(result["errors"]), 2)
        self.assertTrue(all("빈 파일" in item["error"] for item in result["errors"]))
        transcribe.assert_not_called()

    def test_sync_continues_after_one_file_fails(self):
        first = self.touch_audio("first.m4a")
        second = self.touch_audio("second.m4a")

        with mock.patch.object(
            apple,
            "transcribe",
            side_effect=[ValueError("engine failed"), [(0, "둘째 성공")]],
        ):
            result = self.sync([first, second])

        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(result["errors"][0]["file"], str(first))
        self.assertEqual(len(result["imported"]), 1)
        self.assertIn("둘째 성공", Path(result["imported"][0]).read_text(encoding="utf-8"))

    def test_sync_rejects_corrupt_state_without_resetting_it(self):
        audio = self.touch_audio("memo.m4a")
        out = self.base / "out"
        out.mkdir()
        state = out / ".apple-stt-state.json"
        state.write_text(json.dumps({"version": 2, "items": {}}), encoding="utf-8")

        with mock.patch.object(apple, "transcribe", return_value=[(0, "ignored")]):
            with self.assertRaisesRegex(ValueError, "처리 이력 형식 오류"):
                self.sync([audio], out=out)
        self.assertEqual(json.loads(state.read_text(encoding="utf-8"))["version"], 2)

    def test_sync_rejects_missing_model_and_tools(self):
        audio = self.touch_audio("memo.m4a")
        with self.assertRaisesRegex(ValueError, "로컬 모델 없음"):
            apple.sync([audio], self.base / "out", self.base / "missing-model", settle=0)

        model = self.make_model()
        with mock.patch.object(apple.shutil, "which", return_value=None):
            with self.assertRaisesRegex(ValueError, "ffmpeg 설치 필요"):
                apple.sync([audio], self.base / "out", model, settle=0)

    def test_sync_lock_prevents_concurrent_runs(self):
        audio = self.touch_audio("memo.m4a")
        out = self.base / "out"
        out.mkdir()
        lock_file = (out / ".apple-stt.lock").open("a")
        self.addCleanup(lock_file.close)
        apple.fcntl.flock(lock_file, apple.fcntl.LOCK_EX | apple.fcntl.LOCK_NB)

        with mock.patch.object(apple, "transcribe", return_value=[(0, "ignored")]):
            with self.assertRaisesRegex(ValueError, "이미 실행 중"):
                self.sync([audio], out=out)

    def test_sync_skips_duplicate_exports_after_first_import(self):
        first = self.touch_audio("A/memo.m4a", b"same")
        second = self.touch_audio("B/memo copy.m4a", b"same")
        out = self.base / "out"

        with mock.patch.object(apple, "transcribe", return_value=[(0, "한 번만")]) as transcribe:
            result = self.sync([first, second], out=out)

        self.assertEqual(len(result["imported"]), 1)
        self.assertEqual(result["skipped"], [str(second)])
        self.assertEqual(transcribe.call_count, 1)


class MainTests(AppleTestCase):
    def test_main_list_prints_metadata_json(self):
        audio = self.touch_audio("memo.m4a")
        argv = ["import.py", "list", "--file", str(audio)]

        with mock.patch.object(sys, "argv", argv), mock.patch("builtins.print") as printed:
            self.assertEqual(apple.main(), 0)

        rows = json.loads(printed.call_args.args[0])
        self.assertEqual(rows[0]["file"], str(audio.resolve()))
        self.assertEqual(rows[0]["title"], "memo")
        self.assertEqual(rows[0]["bytes"], audio.stat().st_size)


if __name__ == "__main__":
    unittest.main()
