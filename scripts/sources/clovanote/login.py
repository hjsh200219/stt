#!/usr/bin/env python3
"""
login.py — clovanote 소스: 네이버 ID/PW 무인 자동 로그인으로 세션 확보.

브라우저 로그인 없이 ID/PW 로 직접 로그인해 세션 파일
(CLOVANOTE_HOME/clovanote-session.json)을 만든다 → 이후 import.py 가 그대로 사용.
HJ/naverplace 의 검증된 무인 재로그인 패턴(클립보드 붙여넣기·stealth·가드)을
Python(playwright)으로 이식.

⚠️ 계정 정지 위험. 네이버 봇 탐지 강함. 무리한 반복 로그인은 계정 잠금 위험. 그래서:
   - 가드: 쿨다운 30분 / 일 3회 / 연속일 에스컬레이션 경고.
   - CAPTCHA·2FA·아이디보호조치 감지 시 즉시 중단(무인 재시도 안 함).
   - 콜드 프로필은 보호조치로 튕길 확률 큼. **첫 로그인은 `--seed`**(사람이 1회 헤디드로
     보호조치 해제 → 전용 프로필에 신뢰 시딩), 이후 `--auto`(무인)가 그 신뢰를 유지.

stealth: 전용 persistent 프로필 + AutomationControlled 숨김 + locale ko-KR +
timezone Asia/Seoul + navigator.webdriver 은닉.

--- 사용 ---
  python login.py --seed   # 첫 로그인(헤디드, 사람이 보호조치 해제)
  python login.py --auto   # 이후 무인(가드·챌린지중단)
  (playwright 필요: pip install playwright && playwright install chromium)

설정: 크리덴셜 NAVER_ID + NAVER_PW (환경변수 또는 CLOVANOTE_ENV 가 가리키는 설정 .env).
      CLOVANOTE_HOME(세션·프로필·상태 저장), CLOVANOTE_ENV(설정 파일 경로).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
# HERE = <plugin>/scripts/sources/clovanote → 플러그인 루트는 3단계 위
PLUGIN_ROOT = os.environ.get(
    "CLAUDE_PLUGIN_ROOT", os.path.abspath(os.path.join(HERE, "..", "..", "..")))


def _load_env():
    """설정 .env 를 os.environ 에 로드(존재하는 것 전부, 앞선 파일 우선).
    우선순위: CLOVANOTE_ENV > $CLOVANOTE_HOME/.env(기본 ~/.clovanote/.env) > 플러그인 config/.env."""
    home = os.path.expanduser(os.environ.get("CLOVANOTE_HOME", "~/.clovanote"))
    for path in (os.environ.get("CLOVANOTE_ENV"),
                 os.path.join(home, ".env"),
                 os.path.join(PLUGIN_ROOT, "config", ".env")):
        if not path:
            continue
        try:
            with open(os.path.expanduser(path), encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())  # 앞선 파일 우선
        except FileNotFoundError:
            continue


_load_env()

CLOVANOTE_HOME = os.path.expanduser(os.environ.get("CLOVANOTE_HOME", "~/.clovanote"))
SESSION_FILE = os.path.join(CLOVANOTE_HOME, "clovanote-session.json")
PROFILE_DIR = os.path.join(CLOVANOTE_HOME, "naver-clova")  # 전용 신뢰 프로필
STATE_FILE = os.path.join(CLOVANOTE_HOME, "relogin-state.json")

API = "https://clovanote-api.naver.com"
LOGIN_URL = "https://nid.naver.com/nidlogin.login"
NOTE_DEVICE_ID = "28e8fd37-e2a7-4522-bf0a-18ece49825ab"
NOTE_SESSION_ID = "ef72ae87-c5eb-4c56-8399-024d4b1b74ebs"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36")

SEED_WAIT_S = 360  # --seed: 사람이 보호조치 해제할 때까지 로그인 성공 폴링 최대 대기(초)

# 가드 기본값 (naverplace relogin-guard 와 동일)
COOLDOWN_MS = 30 * 60 * 1000
MAX_PER_DAY = 3
ESCALATE_DAYS = 3

# 챌린지(로그인 실패·추가인증) 신호 URL 조각
CHALLENGE_FRAGMENTS = ("nidlogin", "idSafetyRelease", "/login", "captcha", "otp", "deviceConfirm")


# ------------------------------ 크리덴셜 ------------------------------

def load_credentials() -> tuple[str, str]:
    # _load_env() 가 CLOVANOTE_ENV/config .env 를 os.environ 에 이미 로드함.
    nid = os.environ.get("NAVER_ID") or os.environ.get("c")  # `c` = 기존 워크스페이스 호환
    npw = os.environ.get("NAVER_PW")
    if not nid or not npw:
        sys.exit("크리덴셜 없음: NAVER_ID + NAVER_PW 를 환경변수나 "
                 "CLOVANOTE_ENV(설정 .env) 또는 플러그인 config/.env 에 지정")
    return nid, npw


# ------------------------------ 가드 (순수) ------------------------------

def kst_today() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=9)).strftime("%Y-%m-%d")


def _empty_state() -> dict:
    return {"lastAttemptMs": 0, "attemptsToday": 0, "day": "",
            "lastResult": "", "consecutiveDays": 0, "lastFiredDay": ""}


def read_state() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            s = json.load(f)
        e = _empty_state()
        e.update({k: s.get(k, e[k]) for k in e})
        return e
    except (FileNotFoundError, json.JSONDecodeError):
        return _empty_state()


def write_state(state: dict):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)  # atomic


def _day_diff(a: str, b: str):
    try:
        da = datetime.strptime(a, "%Y-%m-%d")
        db = datetime.strptime(b, "%Y-%m-%d")
    except (ValueError, TypeError):
        return None
    return (db - da).days


def should_attempt(state: dict, now_ms: int, today: str) -> tuple[bool, str | None]:
    if state["lastAttemptMs"] > 0 and now_ms - state["lastAttemptMs"] < COOLDOWN_MS:
        return False, "cooldown"
    attempts = state["attemptsToday"] if state["day"] == today else 0
    if attempts >= MAX_PER_DAY:
        return False, "cap"
    return True, None


def record_attempt(state: dict, now_ms: int, today: str) -> dict:
    attempts = (state["attemptsToday"] if state["day"] == today else 0) + 1
    if state["lastFiredDay"] == today:
        streak = state["consecutiveDays"] or 1
    elif _day_diff(state["lastFiredDay"], today) == 1:
        streak = (state["consecutiveDays"] or 0) + 1
    else:
        streak = 1
    return {**state, "lastAttemptMs": now_ms, "attemptsToday": attempts,
            "day": today, "consecutiveDays": streak, "lastFiredDay": today}


def is_escalating(state: dict) -> bool:
    return state["consecutiveDays"] >= ESCALATE_DAYS


# ------------------------------ 클립보드 (macOS) ------------------------------

def clip_read() -> str | None:
    try:
        return subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return None


def clip_write(value: str):
    subprocess.run(["pbcopy"], input=value, text=True, timeout=5)


# ------------------------------ 세션 검증/저장 ------------------------------

def _cookie_header(jar: dict) -> str:
    return "; ".join(f"{k}={v}" for k, v in jar.items())


def validate_and_name(jar: dict) -> dict | None:
    """쿠키로 /v2/user 호출. 성공 시 유저정보 반환, 아니면 None."""
    if "NID_AUT" not in jar:
        return None
    rid = f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:4]}_{uuid.uuid4().hex[:8]}"
    req = urllib.request.Request(f"{API}/v2/user", headers={
        "accept": "application/json", "note-client-type": "WEB",
        "note-client-version": "26.5.2", "note-device-id": NOTE_DEVICE_ID,
        "note-session-id": NOTE_SESSION_ID, "note-request-id": rid,
        "referer": "https://clovanote.naver.com/", "user-agent": UA,
        "cookie": _cookie_header(jar)})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            body = json.loads(r.read().decode())
        return body.get("contents") if body.get("code") == 0 else None
    except (urllib.error.URLError, TimeoutError):
        return None


def save_session(jar: dict, cdp_url: str = "id-pw-login") -> dict:
    session = {"cookies": jar, "device_id": NOTE_DEVICE_ID, "session_id": NOTE_SESSION_ID,
               "harvested_at": datetime.now(timezone.utc).isoformat(), "cdp_url": cdp_url}
    os.makedirs(os.path.dirname(SESSION_FILE), exist_ok=True)
    with open(SESSION_FILE, "w", encoding="utf-8") as f:
        json.dump(session, f, ensure_ascii=False, indent=2)
    return session


# ------------------------------ 로그인 (playwright) ------------------------------

def _open_context(pw, headless: bool):
    return pw.chromium.launch_persistent_context(
        PROFILE_DIR, headless=headless, locale="ko-KR", timezone_id="Asia/Seoul",
        viewport={"width": 1920, "height": 1080}, user_agent=UA,
        args=["--disable-blink-features=AutomationControlled",
              "--no-first-run", "--no-default-browser-check"])


def _paste_into(page, selector: str, value: str):
    clip_write(value)
    page.click(selector)
    page.keyboard.press("Meta+V")  # macOS


def _click_login(page):
    # 현재 네이버 로그인 DOM(2026): 반응형 이중 레이아웃 #loginBtn_column(PC)/#loginBtn_row.
    # 둘 다 button.btn_done 이고 텍스트 "로그인"(패스키 버튼과 구분). 보이는 쪽을 클릭.
    for sel in ("#loginBtn_column", "#loginBtn_row"):
        el = page.query_selector(sel)
        if el and el.is_visible():
            el.click()
            return
    for el in page.query_selector_all("button.btn_done"):
        if el.is_visible() and (el.inner_text() or "").strip() == "로그인":
            el.click()
            return
    page.keyboard.press("Enter")  # 폴백


def _is_challenge(url: str) -> bool:
    return any(frag in url for frag in CHALLENGE_FRAGMENTS)


def _harvest_cookies(context) -> dict:
    return {c["name"]: c["value"] for c in context.cookies() if "naver" in c.get("domain", "")}


def do_login(seed: bool, headless: bool, on_attempt=None) -> str:
    """로그인 실행. 반환: 'success' | 'already' | 'challenge' | 'fail:...'

    on_attempt: 실제 로그인 직전에 1회 호출(이미 로그인/'already' 시에는 호출 안 함).
    가드가 'already'에 일일 캡을 소모하지 않게 하는 훅.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("playwright 없음. 설치: pip install playwright && playwright install chromium")

    nid, npw = load_credentials()
    print(f"로그인 대상: {nid} (프로필 {PROFILE_DIR})")
    prev_clip = clip_read()
    with sync_playwright() as pw:
        ctx = _open_context(pw, headless=headless)
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            # 이미 로그인 상태면(프로필 신뢰 유지 중) 시도 안 함
            jar = _harvest_cookies(ctx)
            info = validate_and_name(jar)
            if info:
                save_session(jar)
                print(f"이미 로그인됨 — {info.get('userName')} ({info.get('email')})")
                return "already"

            if on_attempt:  # 실제 로그인 시도 직전에만 가드 카운트
                on_attempt()
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
            _paste_into(page, "#id", nid)
            _paste_into(page, "#pw", npw)
            _click_login(page)
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass

            if seed:
                # 사람이 창에서 보호조치/CAPTCHA/2FA 를 직접 해제할 때까지 로그인 성공을 폴링.
                # TTY 여부와 무관하게 동작(Enter 불필요). 완료 즉시 감지·저장.
                deadline = time.time() + SEED_WAIT_S
                print(f"\n[SEED] 열린 창에서 보호조치/CAPTCHA/2FA 를 해제하고 로그인 완료하세요.")
                print(f"       최대 {SEED_WAIT_S // 60}분 대기하며 완료를 자동 감지합니다...", flush=True)
                while time.time() < deadline:
                    jar = _harvest_cookies(ctx)
                    info = validate_and_name(jar)
                    if info:
                        save_session(jar)
                        print(f"로그인 성공 — {info.get('userName')} ({info.get('email')})  쿠키 {len(jar)}개")
                        return "success"
                    time.sleep(3)
                print("대기 시간 초과 — 로그인 미완료.", file=sys.stderr)
                return "fail:seed-timeout"

            time.sleep(2)
            jar = _harvest_cookies(ctx)
            info = validate_and_name(jar)
            if info:
                save_session(jar)
                print(f"로그인 성공 — {info.get('userName')} ({info.get('email')})  쿠키 {len(jar)}개")
                return "success"
            if _is_challenge(page.url):
                print(f"챌린지/보호조치 감지 — URL {page.url[:70]}", file=sys.stderr)
                return "challenge"
            return "fail:no-session"
        finally:
            ctx.close()
            if prev_clip is not None:
                clip_write(prev_clip)  # 사용자 클립보드 복원
            else:
                clip_write("")  # 비번 잔류 제거


# ------------------------------ CLI ------------------------------

def main():
    ap = argparse.ArgumentParser(description="네이버 ID/PW 자동 로그인 → clovanote 세션")
    ap.add_argument("--auto", action="store_true", help="무인(가드·챌린지중단)")
    ap.add_argument("--seed", action="store_true", help="헤디드·사람 개입(첫 로그인/보호조치 해제)")
    ap.add_argument("--headless", action="store_true", help="headless 강제(권장 안 함 — fingerprint 거부 가능)")
    args = ap.parse_args()
    if not (args.auto or args.seed):
        sys.exit("--auto 또는 --seed 중 하나를 지정하세요.")

    if args.seed:
        # 시드는 가드 없이 사람 감독 하에 실행 (헤디드)
        do_login(seed=True, headless=False)
        return

    # --auto: 가드 통과 후에만 시도
    now = int(time.time() * 1000)
    today = kst_today()
    state = read_state()
    ok, reason = should_attempt(state, now, today)
    if not ok:
        state["lastResult"] = f"skipped:{reason}"
        write_state(state)
        sys.exit(f"가드 차단: {reason} (쿨다운 30분 / 일 {MAX_PER_DAY}회). 재시도 보류.")

    def on_attempt():  # 'already'가 아니라 실제 로그인 시도할 때만 캡 소모
        nonlocal state
        state = record_attempt(state, now, today)
        write_state(state)

    result = do_login(seed=False, headless=args.headless, on_attempt=on_attempt)
    state["lastResult"] = result
    write_state(state)

    if is_escalating(state):
        print(f"[경고] {state['consecutiveDays']}일 연속 자동로그인 — 세션 고정수명 의심, "
              "계정 잠금 위험 누적. --seed 로 사람 개입 권장.", file=sys.stderr)
    if result == "challenge":
        sys.exit("보호조치/CAPTCHA 로 무인 로그인 실패. `--seed` 로 사람이 1회 해제 후 재시도.")
    if result.startswith("fail"):
        sys.exit(f"로그인 실패: {result}")


if __name__ == "__main__":
    main()
