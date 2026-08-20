"""
make_cases.py — 판정 로그 + 시연 영상 7개를 웹용 데이터로 묶는다

입력:  judgments.jsonl        젯슨이 남긴 판정 기록 (7개 세션, 49건)
       judge_calib.json       주차구역 폴리곤 (지면 좌표계)
       *.mp4                  1080p 원본 시연 영상 7개
출력:  docs/cases.json         케이스 메타 + 판정 이벤트 + 전체 로그
       docs/media/*.mp4        1280px 로 재인코딩한 웹용 영상

영상과 판정을 어떻게 맞췄나
---------------------------
영상에는 판정 오버레이가 이미 구워져 있고, 좌상단에 마지막 판정이
"17:28:26  #37 SWING  INVALID_OUT_OF_ZONE" 형태로 찍힌다. 이 배너가 바뀌는
프레임을 찾아 judgments.jsonl 의 (시각, track_id) 와 대조하면 영상 몇 초
지점이 어느 판정인지 정확히 떨어진다. 아래 ANCHORS 가 그렇게 확인한 표다.

영상은 파이프라인 출력(초당 2~3회 추론)을 고정 fps 로 인코딩한 것이라
영상 시간과 실제 시계가 완전한 정비례는 아니다(90초에 약 3초 밀림).
그래서 이벤트 위치는 비례식으로 계산하지 않고 확인된 프레임을 그대로 쓴다.

사용:
    py make_cases.py            # 데이터만 다시 만듦
    py make_cases.py --video    # 영상 재인코딩까지 (ffmpeg 필요, 수 분)
"""

import json, subprocess, sys, datetime
from pathlib import Path

SRC_JSONL = Path("judgments.jsonl")
SRC_CALIB = Path("judge_calib.json")
OUT_JSON  = Path("docs/cases.json")
OUT_MEDIA = Path("docs/media")
MARGIN_M  = 1.0                       # BEV 그림 여백
ZONE_NAME = "유억겸 기념관 앞"

BRAND_KO = {"SOCAR": "쏘카일레클", "SWING": "스윙", "GCOO": "지쿠"}
STATUS_KO = {
    "RETURN_VALID":        ("정상 반납", "반납 승인", False),
    "INVALID_OUT_OF_ZONE": ("구역 이탈", "반납 거부", True),
    "INVALID_FALLEN":      ("전도",      "반납 거부", True),
}

# ── 케이스 정의 ──────────────────────────────────────────────────────────
# anchors: [영상 프레임 번호, 그 프레임부터 배너에 뜬 판정의 (시각, track_id)]
#          영상 배너를 직접 읽어 확인한 값이다.
CASES = [
    dict(
        slug="case1-normal-return", src="쏘카일레클 정상 대여, 정상 반납.mp4",
        title="정상 대여 · 정상 반납", session=5,
        summary="쏘카일레클을 빌려 타고 와 구역 안에 세운 기준 사례.",
        expect="반납 승인", kind="ok",
        anchors=[(0, "17:14:58", 5), (211, "17:16:54", 14)],
    ),
    dict(
        slug="case2-on-the-line", src="쏘카일레클 타고와서 경계선 걸쳐 반납(반납거부).mp4",
        title="경계선에 걸쳐 반납", session=6,
        summary="앞바퀴만 구역 안. 발자국이 구역과 55.8%만 겹쳐 이탈로 판정.",
        expect="반납 거부", kind="bad",
        anchors=[(43, "17:25:54", 33)],
    ),
    dict(
        slug="case3-out-of-zone", src="쏘카일레클 타고와서 범위 밖 반납(반납거부).mp4",
        title="구역 밖에 반납", session=6,
        summary="구역 경계 바깥 보도에 그대로 세운 경우. 겹침 0%.",
        expect="반납 거부", kind="bad",
        anchors=[(0, "17:27:59", 38), (50, "17:28:26", 37), (175, "17:29:29", 46)],
    ),
    dict(
        slug="case4-fallen-in-zone", src="쏘카일레클 정상범위 내 넘어짐 반납(반납거부).mp4",
        title="구역 안이지만 넘어짐", session=6,
        summary="위치는 구역 안(겹침 95.3%)이지만 기체가 누워 전도로 판정.",
        expect="반납 거부", kind="bad",
        anchors=[(11, "17:22:35", 10)],
    ),
    dict(
        slug="case5-fallen-pending", src="쏘카일레클 타고와서 정상범위 내 넘어짐(반납보류).mp4",
        title="넘어짐 · 판정 보류", session=6,
        summary="전도로 보이지만 확정 전 상태(PENDING). 확정 판정이 아니라 로그에 남지 않는다.",
        expect="반납 보류", kind="pending",
        anchors=[],                       # 확정 판정 없음 — 배너에 시각이 뜨지 않는다
        approx_time="17:23~17:25",        # tracks 12~14 로 앞뒤 클립 사이임을 확인
        approx_start="17:23:30",          # 구역 맵 누적 기준으로만 쓰는 값
    ),
    dict(
        slug="case6-gcoo-walk", src="지쿠 걸어서 정상반납(3브랜드).mp4",
        title="지쿠 · 끌고 와서 정상 반납", session=7,
        summary="세 브랜드가 섞인 구역에서 지쿠를 끌고 와 반납. 브랜드 구분이 함께 확인된다.",
        expect="반납 승인", kind="ok",
        anchors=[(7, "17:41:30", 4), (70, "17:42:00", 9), (91, "17:42:08", 5)],
    ),
    dict(
        slug="case7-gcoo-ride", src="지쿠 타고와서 정상반납(3브랜드).mp4",
        title="지쿠 · 타고 와서 정상 반납", session=7,
        summary="같은 구역에 지쿠를 타고 와서 반납. 앞 사례와 접근 방식만 다르다.",
        expect="반납 승인", kind="ok",
        anchors=[(48, "17:45:07", 23)],
    ),
]


def secs(hms):
    """'17:28:26' → 자정 기준 초"""
    h, m, sec = (int(v) for v in hms.split(":"))
    return h * 3600 + m * 60 + sec


def split_sessions(records):
    """프레임 번호가 되감기는 지점에서 세션을 자른다."""
    out, cur = [], [records[0]]
    for prev, rec in zip(records, records[1:]):
        if rec["frame"] < prev["frame"]:
            out.append(cur); cur = [rec]
        else:
            cur.append(rec)
    out.append(cur)
    return out


def probe(path):
    """mp4 헤더에서 길이·프레임수를 읽는다 (외부 도구 없이)."""
    import cv2
    c = cv2.VideoCapture(str(path))
    n   = int(c.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = c.get(cv2.CAP_PROP_FPS)
    w, h = int(c.get(cv2.CAP_PROP_FRAME_WIDTH)), int(c.get(cv2.CAP_PROP_FRAME_HEIGHT))
    c.release()
    return n, fps, round(n / fps, 2), w, h


def poster(video, frame, dst):
    """갤러리 카드에 쓸 대표 장면을 뽑는다. (cv2 는 한글 경로에 쓰기가 안 돼
    imencode 로 버퍼를 만들어 직접 저장한다)"""
    import cv2
    c = cv2.VideoCapture(str(video))
    c.set(cv2.CAP_PROP_POS_FRAMES, frame)
    ok, img = c.read()
    c.release()
    if not ok:
        return False
    img = cv2.resize(img, (640, 360), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 74])
    dst.write_bytes(buf.tobytes())
    return True


def transcode():
    """원본 1080p 영상을 1280px 웹용으로 재인코딩한다."""
    import imageio_ffmpeg
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    OUT_MEDIA.mkdir(parents=True, exist_ok=True)
    for c in CASES:
        dst = OUT_MEDIA / f"{c['slug']}.mp4"
        subprocess.run([ff, "-y", "-loglevel", "error", "-i", c["src"],
                        "-vf", "scale=1280:-2", "-c:v", "libx264", "-crf", "30",
                        "-preset", "slow", "-pix_fmt", "yuv420p", "-an",
                        "-movflags", "+faststart", str(dst)], check=True)
        print(f"  {c['slug']:<22} {Path(c['src']).stat().st_size/1e6:>6.1f}MB "
              f"-> {dst.stat().st_size/1e6:>5.2f}MB")


def main():
    if "--video" in sys.argv:
        print("영상 재인코딩")
        transcode()

    records = [json.loads(l) for l in SRC_JSONL.open(encoding="utf-8") if l.strip()]
    calib = json.loads(SRC_CALIB.read_text(encoding="utf-8"))
    zone = [[float(p[0]), float(p[1])] for p in calib["zone_ground"]]
    sessions = split_sessions(records)

    xs = [p[0] for p in zone]; ys = [p[1] for p in zone]
    x0, x1 = min(xs) - MARGIN_M, max(xs) + MARGIN_M
    y0, y1 = min(ys) - MARGIN_M, max(ys) + MARGIN_M

    def nrm(p):                                   # 지면 좌표 → 0~1 (y 뒤집음)
        return [round((p[0] - x0) / (x1 - x0), 4),
                round((y1 - p[1]) / (y1 - y0), 4)]

    def shape(r, sess_no):
        state, verdict, bad = STATUS_KO.get(r["status"], (r["status"], "판정", True))
        xy = nrm(r["ground_xy"])
        return {
            "session": sess_no, "time": r["time"], "track": r["track_id"],
            "brand": BRAND_KO.get(r.get("brand"), "미확인"),
            "state": state, "verdict": verdict, "violation": bad,
            "overlap": round(r.get("zone_overlap", 0.0), 3),
            "ground": [round(v, 2) for v in r["ground_xy"]],
            "x": xy[0], "y": xy[1],
        }

    # 전체 판정 로그
    all_j = []
    for i, s in enumerate(sessions, 1):
        for r in s:
            all_j.append(shape(r, i))

    # 케이스별 이벤트 — 영상에서 확인한 프레임에 판정을 붙인다
    cases = []
    for c in CASES:
        n, fps, dur, w, h = probe(OUT_MEDIA / f"{c['slug']}.mp4")
        sess = sessions[c["session"] - 1]
        events = []
        for frame, tm, track in c["anchors"]:
            hit = next((r for r in sess if r["time"] == tm and r["track_id"] == track), None)
            if hit is None:
                print(f"  ! {c['slug']}: {tm} #{track} 판정을 로그에서 못 찾음")
                continue
            e = shape(hit, c["session"])
            e["t"] = round(frame / fps, 2)
            e["frame"] = frame
            events.append(e)
        events.sort(key=lambda e: e["t"])

        # 클립이 시작된 실제 시각 — 마지막 앵커에서 역산한다.
        # (첫 프레임 배너는 클립 전에 내려진 판정이 남아 있는 것이라 기준으로 못 쓴다)
        if events:
            last = events[-1]
            start_wall = secs(last["time"]) - last["t"]
        else:
            start_wall = secs(c["approx_start"])

        # 구역 맵 배경 — 같은 세션에서 판정된 다른 기체들.
        # 세워둔 기체는 그대로 있으므로, 클립 시작 전에 판정된 것도 화면에 남는다.
        own = {(a[1], a[2]) for a in c["anchors"]}
        context = []
        for r in sess:
            if (r["time"], r["track_id"]) in own:
                continue
            e = shape(r, c["session"])
            e["t"] = round(secs(r["time"]) - start_wall, 1)
            if e["t"] > dur:                  # 클립이 끝난 뒤 판정 — 보여줄 수 없다
                continue
            context.append(e)
        context.sort(key=lambda e: e["t"])

        # 대표 장면 — 판정이 뜬 프레임, 없으면 후반부
        key_frame = events[-1]["frame"] if events else int(n * 0.75)
        poster(OUT_MEDIA / f"{c['slug']}.mp4", key_frame, OUT_MEDIA / f"{c['slug']}.jpg")

        cases.append({
            "id": c["slug"], "title": c["title"], "summary": c["summary"],
            "expect": c["expect"], "kind": c["kind"], "session": c["session"],
            "video": f"media/{c['slug']}.mp4",
            "poster": f"media/{c['slug']}.jpg",
            "duration": dur, "fps": round(fps, 3), "frames": n, "width": w, "height": h,
            "clock": c.get("approx_time") or (events[0]["time"] if events else ""),
            "approx": bool(c.get("approx_time")),
            "events": events,
            "context": context,
        })

    viol = sum(1 for j in all_j if j["violation"])
    brands, reasons = {}, {}
    for j in all_j:
        brands[j["brand"]] = brands.get(j["brand"], 0) + 1
        if j["violation"]:
            reasons[j["state"]] = reasons.get(j["state"], 0) + 1

    OUT_JSON.parent.mkdir(exist_ok=True)
    json.dump({
        "built": datetime.date.today().isoformat(),
        "zone_name": ZONE_NAME,
        "zone_poly": [nrm(p) for p in zone],
        "zone_poly_m": [[round(v, 2) for v in p] for p in zone],
        "bounds_m": {"x": [x0, x1], "y": [y0, y1]},
        "totals": {
            "sessions": len(sessions), "cases": len(cases),
            "judgments": len(all_j), "violations": viol, "valid": len(all_j) - viol,
            "brands": brands, "reasons": reasons,
        },
        "cases": cases,
        "judgments": all_j,
    }, OUT_JSON.open("w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print(f"\n{OUT_JSON}  ({OUT_JSON.stat().st_size/1024:.1f} KB)")
    print(f"케이스 {len(cases)}개 · 케이스 내 판정 "
          f"{sum(len(c['events']) for c in cases)}건 · 전체 판정 {len(all_j)}건 "
          f"(위반 {viol} / 정상 {len(all_j)-viol})")
    for c in cases:
        print(f"  {c['id']:<22} {c['duration']:>6.1f}s  세션{c['session']}  "
              f"{c['clock']:<12} 판정 {len(c['events'])}건  "
              f"맵 배경 {len(c['context'])}대")


if __name__ == "__main__":
    main()
