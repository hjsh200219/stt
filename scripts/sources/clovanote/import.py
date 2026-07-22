#!/usr/bin/env python3
"""
import.py — clovanote 소스 어댑터. 네이버 클로바노트 노트를 API로 내려받아
CLOVANOTE_OUT(마크다운 vault)에 적재. stt 플러그인의 소스 중 하나.

클로바노트(clovanote.naver.com)는 공개 API가 없다. 이 스크립트는 웹 SPA가
쓰는 내부 REST(https://clovanote-api.naver.com/v2)를 로그인 세션 쿠키로 재현한다.
발굴 후 브라우저 없이 stdlib urllib 만으로 요청을 재현한다.

로그인은 login.py(ID/PW) 또는 login(CDP harvest)로 확보한 세션 파일을 재사용한다.
네이버는 순수 REST 로그인이 없어(RSA+봇탐지+CAPTCHA) 세션 재사용/스텔스 로그인만 가능.

--- 엔드포인트 맵 (전부 200 검증) ---
  auth      : NID_AUT/NID_SES 쿠키 + note-* 헤더 (Authorization/CSRF 없음)
  user      : GET /v2/user                              -> contents.workspaces[]
  folders   : GET /v2/w/{ws}/folders                    -> contents.myFolders[]
  list      : GET /v2/w/{ws}/notes?category=ALL&folderId={fid}
                 &sortKey=UPDATED-DATE&sortOrder=DESC&limit=N
                 -> contents.noteInfoSetList[]           (noteId 열거)
  detail    : GET /v2/w/{ws}/notes/{noteId}             -> 메타(제목/일시/참석자/길이)
  script    : GET /v2/w/{ws}/notes/{noteId}/script      -> contents.script.blockList[]
                 (세그먼트 text/speakerId/start/end) + keywordList[]

--- 사용 ---
  python login.py --seed        # 첫 로그인(사람이 보호조치 해제)
  python import.py list         # 노트 목록 (브라우저 불필요)
  python import.py import --all # CLOVANOTE_OUT 에 전체 적재

설정: CLOVANOTE_OUT(출력), CLOVANOTE_HOME(세션·프로필), CLOVANOTE_ENV(설정 파일).
세션 만료(HTTP 401) 시 login.py --auto 재실행.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone

API = "https://clovanote-api.naver.com"
AUTH_GATE = f"{API}/v2/user"  # 200+code0 이면 로그인 상태
DEFAULT_CDP = "http://localhost:9224"  # 사람이 네이버 로그인해 둔 EDBMac 디버그 Chrome

HERE = os.path.dirname(os.path.abspath(__file__))
# HERE = <plugin>/scripts/sources/clovanote → 플러그인 루트는 3단계 위
PLUGIN_ROOT = os.environ.get(
    "CLAUDE_PLUGIN_ROOT", os.path.abspath(os.path.join(HERE, "..", "..", "..")))


def _load_env():
    """config .env(선택) 를 os.environ 에 로드. CLOVANOTE_ENV 또는 플러그인 config/.env."""
    path = os.environ.get("CLOVANOTE_ENV") or os.path.join(PLUGIN_ROOT, "config", ".env")
    try:
        with open(os.path.expanduser(path), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    except FileNotFoundError:
        pass


_load_env()

# 데이터 홈(세션·프로필·상태) 과 노트 출력 경로 — 환경변수로 설정.
CLOVANOTE_HOME = os.path.expanduser(os.environ.get("CLOVANOTE_HOME", "~/.clovanote"))
SESSION_FILE = os.path.join(CLOVANOTE_HOME, "clovanote-session.json")
VAULT_OUT = os.path.expanduser(
    os.environ.get("CLOVANOTE_OUT", os.path.join(CLOVANOTE_HOME, "notes")))

# note-* 클라이언트 헤더. device/session-id 는 클라이언트 식별자(비밀 아님) —
# 발굴 시 캡처한 값을 재사용한다. request-id 는 매 요청 nonce 로 새로 만든다.
NOTE_DEVICE_ID = "28e8fd37-e2a7-4522-bf0a-18ece49825ab"
NOTE_SESSION_ID = "ef72ae87-c5eb-4c56-8399-024d4b1b74ebs"
NOTE_CLIENT_VERSION = "26.5.2"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36")


class SessionExpired(RuntimeError):
    pass


# ------------------------------- 세션 -------------------------------

def load_session() -> dict | None:
    try:
        with open(SESSION_FILE, encoding="utf-8") as f:
            s = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    if not s.get("cookies", {}).get("NID_AUT"):
        return None
    return s


def harvest_session(cdp_url: str) -> dict:
    """디버그 Chrome(CDP)에서 네이버 세션 쿠키를 harvest → 검증 → 영속화.

    playwright 필요. 브라우저를 새로 띄우지 않고 이미 사람이 로그인해 둔
    디버그 Chrome 프로필의 쿠키만 읽는다.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("playwright 없음. 설치: pip install playwright && playwright install chromium")

    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(cdp_url)
        if not browser.contexts:
            sys.exit(f"CDP {cdp_url} 에 컨텍스트 없음. 디버그 Chrome 이 떠 있는지 확인.")
        cookies = browser.contexts[0].cookies(
            ["https://clovanote-api.naver.com", "https://clovanote.naver.com", "https://naver.com"])

    jar = {c["name"]: c["value"] for c in cookies if "naver" in c.get("domain", "")}
    if "NID_AUT" not in jar:
        sys.exit(f"NID_AUT 쿠키 없음 — {cdp_url} 프로필이 네이버 로그아웃 상태.\n"
                 "해당 Chrome 에서 사람이 네이버 계정으로 로그인 후 다시 실행.")

    session = {
        "cookies": jar,
        "device_id": NOTE_DEVICE_ID,
        "session_id": NOTE_SESSION_ID,
        "harvested_at": datetime.now(timezone.utc).isoformat(),
        "cdp_url": cdp_url,
    }
    # 검증: /v2/user 200 + code 0
    user = api_get(session, "/v2/user")
    name = user.get("userName", "?")
    email = user.get("email", "?")
    os.makedirs(os.path.dirname(SESSION_FILE), exist_ok=True)
    with open(SESSION_FILE, "w", encoding="utf-8") as f:
        json.dump(session, f, ensure_ascii=False, indent=2)
    print(f"세션 저장 OK — {name} ({email})  쿠키 {len(jar)}개 -> {SESSION_FILE}")
    return session


# ------------------------------- HTTP -------------------------------

def _headers(session: dict) -> dict:
    ck = "; ".join(f"{k}={v}" for k, v in session["cookies"].items())
    rid = f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:4]}_{uuid.uuid4().hex[:8]}"
    return {
        "accept": "application/json, text/plain, */*",
        "accept-language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "note-client-type": "WEB",
        "note-client-version": NOTE_CLIENT_VERSION,
        "note-device-id": session.get("device_id", NOTE_DEVICE_ID),
        "note-session-id": session.get("session_id", NOTE_SESSION_ID),
        "note-request-id": rid,
        "referer": "https://clovanote.naver.com/",
        "user-agent": UA,
        "cookie": ck,
    }


def api_get(session: dict, path: str, *, retries: int = 3) -> dict:
    """GET {API}{path} → contents. 실패 시 재시도, 인증 실패는 SessionExpired."""
    url = API + path
    last = None
    for attempt in range(retries):
        req = urllib.request.Request(url, headers=_headers(session))
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                body = json.loads(r.read().decode("utf-8", "replace"))
            if body.get("code") not in (0, None):
                raise RuntimeError(f"API code {body.get('code')}: {body.get('message')} ({path})")
            return body.get("contents", body)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise SessionExpired(
                    "세션 만료(HTTP %d). 다시 로그인: python login.py --auto" % e.code)
            last = f"HTTP {e.code}"
        except (urllib.error.URLError, TimeoutError) as e:
            last = str(e)
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"GET 실패 {path}: {last}")


# ------------------------------- 도메인 -------------------------------

def resolve_workspace(session: dict) -> str:
    user = api_get(session, "/v2/user")
    wss = user.get("workspaces") or []
    for w in wss:
        wid = w.get("workspaceId") or w.get("id") or w.get("wsId")
        if wid:
            return wid
    raise RuntimeError("워크스페이스를 찾지 못함 (/v2/user.workspaces 비어있음)")


def list_note_ids(session: dict, wsid: str) -> list[str]:
    """모든 폴더를 순회해 noteId 를 union(중복 제거). 폴더리스 노트 누락 방지."""
    folders = api_get(session, f"/v2/w/{wsid}/folders").get("myFolders", [])
    seen: dict[str, None] = {}
    for fol in folders:
        fid = fol.get("folderId")
        if not fid:
            continue
        got = 0
        limit = 100
        base = (f"/v2/w/{wsid}/notes?category=ALL&folderId={fid}"
                f"&sortKey=UPDATED-DATE&sortOrder=DESC&limit={limit}")
        lst = api_get(session, base).get("noteInfoSetList", [])
        for item in lst:
            nid = item.get("noteId") or item.get("note", {}).get("noteId")
            if nid and nid not in seen:
                seen[nid] = None
                got += 1
        if len(lst) == limit:
            print(f"  [주의] 폴더 {fol.get('folderName')} 가 {limit}건 상한에 도달 — "
                  "페이징 미구현으로 이후 노트가 누락됐을 수 있음", file=sys.stderr)
    return list(seen)


def fetch_note(session: dict, wsid: str, note_id: str) -> dict:
    """노트 1건. 상세 응답이 메타(noteInfo) + 전사(script)를 함께 담으므로 1콜이면 충분."""
    detail = api_get(session, f"/v2/w/{wsid}/notes/{note_id}")
    info = detail.get("noteInfo", {})
    script = detail.get("script")
    if not (isinstance(script, dict) and script.get("blockList")):  # 폴백: 별도 전사 엔드포인트
        script = api_get(session, f"/v2/w/{wsid}/notes/{note_id}/script").get("script", {})
    return {"info": info, "script": script or {}}


# ------------------------------- 렌더링 -------------------------------

def _slug(name: str) -> str:
    name = (name or "무제").strip()
    name = re.sub(r"[\s/\\]+", "-", name)
    name = re.sub(r"[^\w가-힣.\-]", "", name)
    return name[:50] or "무제"


def _ms_to_clock(ms) -> str:
    try:
        s = int(ms) // 1000
    except (TypeError, ValueError):
        return "00:00"
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"


def _date_only(iso: str) -> str:
    return (iso or "")[:10] or datetime.now().strftime("%Y-%m-%d")


def render_markdown(note_id: str, info: dict, script: dict) -> tuple[str, str]:
    """(파일명, 마크다운 본문) 반환. info=상세.noteInfo, script=상세.script."""
    name = info.get("noteName") or "무제"
    created = info.get("createdDate") or info.get("audioStartDate") or ""
    date = _date_only(created)
    attendees = [a.get("attendeeName", "") for a in info.get("attendeeList", []) if a.get("attendeeName")]
    duration = _ms_to_clock(info.get("audioDuration"))
    keywords = [k.get("text", "") for k in script.get("keywordList", []) if k.get("text")]
    blocks = script.get("blockList", [])

    fm = [
        "---",
        f"title: {name}",
        f"date: {date}",
        "type: clova-note",
        "source: 네이버 클로바노트 (clovanote.naver.com, API export)",
        f"noteId: {note_id}",
        f"created: {created}",
        f"duration: {duration}",
    ]
    if attendees:
        fm.append(f"attendees: [{', '.join(attendees)}]")
    if keywords:
        fm.append("tags: [" + ", ".join(keywords[:15]) + "]")
    fm.append("---")

    body = [f"# {name}", "", f"녹음일 {date} · 길이 {duration}"
            + (f" · 참석자 {', '.join(attendees)}" if attendees else ""), ""]
    if keywords:
        body += ["**키워드**: " + ", ".join(keywords), ""]

    # abstractBlock 은 클로바가 만드는 잘린 미리보기(요약 아님, summaryStatus 무관하게 첫 문장 발췌).
    preview = (info.get("abstractBlock") or "").strip()
    if preview:
        body += ["## 미리보기", "", preview, ""]

    body += ["## 전사", ""]
    prev_spk = None
    for b in blocks:
        text = (b.get("text") or "").strip()
        if not text:
            continue
        spk = b.get("speakerId") or b.get("sttLabel") or ""
        clock = _ms_to_clock(b.get("start"))
        if spk != prev_spk:
            body.append(f"\n**화자 {spk}**" if spk else "")
            prev_spk = spk
        body.append(f"- `{clock}` {text}")
    body.append("")

    filename = f"{date}-{_slug(name)}-{note_id[:8]}.md"
    return filename, "\n".join(fm) + "\n\n" + "\n".join(body) + "\n"


def write_note(note_id: str, info: dict, script: dict) -> str:
    os.makedirs(VAULT_OUT, exist_ok=True)
    filename, md = render_markdown(note_id, info, script)
    path = os.path.join(VAULT_OUT, filename)
    with open(path, "w", encoding="utf-8") as f:  # upsert: 동일 노트 재실행 시 갱신
        f.write(md)
    return path


# ------------------------------- CLI -------------------------------

def _require_session() -> dict:
    s = load_session()
    if not s:
        sys.exit("세션 없음. 먼저 로그인:\n"
                 "  python login.py --seed   # 첫 로그인(사람이 보호조치 해제)\n"
                 "  python login.py --auto   # 이후 무인\n"
                 "  (playwright 필요: pip install playwright && playwright install chromium)")
    return s


def cmd_login(args):
    harvest_session(args.cdp)


def cmd_list(args):
    s = _require_session()
    wsid = resolve_workspace(s)
    ids = list_note_ids(s, wsid)
    print(f"워크스페이스 {wsid} — 노트 {len(ids)}건")
    for nid in ids:
        info = api_get(s, f"/v2/w/{wsid}/notes/{nid}").get("noteInfo", {})
        print(f"  {nid[:8]}  {_date_only(info.get('createdDate',''))}  "
              f"{info.get('noteName') or '무제'}  ({_ms_to_clock(info.get('audioDuration'))})")


def cmd_import(args):
    s = _require_session()
    wsid = resolve_workspace(s)
    ids = list_note_ids(s, wsid)
    if not args.all:
        ids = ids[: args.limit]
    print(f"적재 대상 {len(ids)}건 -> {VAULT_OUT}")
    for i, nid in enumerate(ids, 1):
        try:
            nd = fetch_note(s, wsid, nid)
            path = write_note(nid, nd["info"], nd["script"])
            print(f"  [{i}/{len(ids)}] {os.path.basename(path)}")
        except SessionExpired:
            raise
        except Exception as e:  # noqa: BLE001 — 개별 노트 실패는 건너뛰고 계속
            print(f"  [{i}/{len(ids)}] {nid[:8]} 실패: {e}", file=sys.stderr)
        time.sleep(0.8)  # 계정 보호: 저속 요청
    print("완료.")


def main():
    ap = argparse.ArgumentParser(description="네이버 클로바노트 → _vault 적재")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("login", help="디버그 Chrome(CDP)에서 세션 쿠키 harvest+영속화")
    p.add_argument("--cdp", default=DEFAULT_CDP, help=f"CDP 엔드포인트 (기본 {DEFAULT_CDP})")
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("list", help="노트 목록 출력")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("import", help="노트를 _vault/sources/clova-note/ 에 적재")
    p.add_argument("--all", action="store_true", help="전체 노트 (기본은 최신 --limit 건)")
    p.add_argument("--limit", type=int, default=1, help="--all 아닐 때 적재할 최신 노트 수 (기본 1)")
    p.set_defaults(func=cmd_import)

    args = ap.parse_args()
    try:
        args.func(args)
    except SessionExpired as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
