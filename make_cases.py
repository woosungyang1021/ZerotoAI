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
MARGIN_M  = 2.1                       # BEV 그림 여백 — 구역 밖 기체까지 담는다
ZONE_NAME = "유억겸 기념관 앞"

# 녹화를 시작하면 젯슨은 그 자리에 이미 서 있던 기체들을 한꺼번에 판정한다.
# 사람이 와서 반납한 건과 성격이 다르고(추적이 자리잡기 전이라 접지점이 흔들린다)
# 세션이 바뀔 때마다 같은 기체가 다시 계수되므로 구분해 둔다.
# 실측: 시작부 판정은 frame 69~75 에 몰려 있고, 그 다음 판정은 201 부터다.
ONSTART_FRAME = 100

BRAND_KO = {"SOCAR": "쏘카일레클", "SWING": "스윙", "GCOO": "지쿠"}
STATUS_KO = {
    "RETURN_VALID":        ("정상 반납", "반납 승인", False),
    "INVALID_OUT_OF_ZONE": ("구역 이탈", "반납 거부", True),
    "INVALID_FALLEN":      ("전도",      "반납 거부", True),
}

# ── 케이스 정의 ──────────────────────────────────────────────────────────
# anchors: [영상 프레임 번호, 그 프레임부터 배너에 뜬 판정의 (시각, track_id)]
#          영상 배너를 직접 읽어 확인한 값이다.
#          4번째 값이 "carry" 면 그 클립에서 새로 일어난 반납이 아니라,
#          녹화(장비) 재시작 때 이미 서 있던 기체가 다시 받은 허가 표시다.
# standing: 클립 영상에 승인(초록) 표시로 보이는 기존 주차 기체 수.
#           carry 이벤트 + 이전 판정의 박스가 그대로 남은 것까지, 프레임을 떠서 셌다.
# standing_bad: 같은 방식으로 센 기존 주차 기체의 불허가(주황) 표시 수.
#           세션 5~6 내내 보도에 서 있던 자전거가 구역 이탈로 찍혀 있다. 세션 7 에서는 치워짐.
CASES = [
    dict(
        slug="case1-normal-return", src="쏘카일레클 정상 대여, 정상 반납.mp4",
        title="정상 대여 · 정상 반납", session=5,
        summary="쏘카일레클을 빌려 타고 와 구역 안에 세운 기준 사례.",
        expect="반납 승인", kind="ok", group="주차 판정",
        anchors=[(0, "17:14:58", 5, "carry"), (211, "17:16:54", 14)],
        standing=4, standing_bad=1,       # 시작 프레임: #1 #2 #3 + carry #5 · 보도 자전거 #4
    ),
    dict(
        slug="case2-on-the-line", src="쏘카일레클 타고와서 경계선 걸쳐 반납(반납거부).mp4",
        title="경계선에 걸쳐 반납", session=6,
        summary="앞바퀴만 구역 안. 발자국이 구역과 55.8%만 겹쳐 이탈로 판정.",
        expect="반납 거부", kind="bad", group="주차 판정",
        anchors=[(43, "17:25:54", 33)],
        standing=3, standing_bad=1,       # 시작 프레임: #1 #3 #4 · 보도 자전거 #2
    ),
    dict(
        slug="case3-out-of-zone", src="쏘카일레클 타고와서 범위 밖 반납(반납거부).mp4",
        title="구역 밖에 반납", session=6,
        summary="구역 경계 바깥 보도에 그대로 세운 경우. 겹침 0%.",
        expect="반납 거부", kind="bad", group="주차 판정",
        anchors=[(0, "17:27:59", 38, "carry"), (50, "17:28:26", 37), (175, "17:29:29", 46)],
        standing=2, standing_bad=1,       # carry #38 + 중간에 다시 초록이 되는 #1 · 보도 자전거 #2
    ),
    dict(
        slug="case4-fallen-in-zone", src="쏘카일레클 정상범위 내 넘어짐 반납(반납거부).mp4",
        title="구역 안이지만 넘어짐", session=6,
        summary="위치는 구역 안(겹침 95.3%)이지만 기체가 누워 전도로 판정.",
        expect="반납 거부", kind="bad", group="주차 판정",
        anchors=[(11, "17:22:35", 10)],
        standing=3, standing_bad=1,       # 시작 프레임: #1 #3 #4 · 보도 자전거 #2 (#10 은 주인공)
    ),
    dict(
        slug="case5-fallen-pending", src="쏘카일레클 타고와서 정상범위 내 넘어짐(반납보류).mp4",
        title="넘어짐 · 판정 보류", session=6,
        summary="전도로 보이지만 확정 전 상태(PENDING). 확정 판정이 아니라 로그에 남지 않는다.",
        expect="반납 보류", kind="pending", group="주차 판정",
        anchors=[],                       # 확정 판정 없음 — 배너에 시각이 뜨지 않는다
        approx_time="17:23~17:25",        # tracks 12~14 로 앞뒤 클립 사이임을 확인
        standing=3, standing_bad=1,       # 시작 프레임: #1 #3 #4 · 보도 자전거 #2
    ),
    dict(
        slug="case6-gcoo-walk", src="지쿠 걸어서 정상반납(3브랜드).mp4",
        title="지쿠 · 끌고 와서 정상 반납", session=7,
        summary="세 브랜드가 섞인 구역에서 지쿠를 끌고 와 반납. 브랜드 구분이 함께 확인된다.",
        expect="반납 승인", kind="ok", group="주차 판정",
        anchors=[(7, "17:41:30", 4, "carry"), (70, "17:42:00", 9), (91, "17:42:08", 5, "carry")],
        standing=3,                       # #1 스윙 + carry #4 #5 (지쿠 #9 만 실제 반납) · 보도 자전거 없음
    ),
    dict(
        slug="case7-gcoo-ride", src="지쿠 타고와서 정상반납(3브랜드).mp4",
        title="지쿠 · 타고 와서 정상 반납", session=7,
        summary="같은 구역에 지쿠를 타고 와서 반납. 앞 사례와 접근 방식만 다르다.",
        expect="반납 승인", kind="ok", group="주차 판정",
        anchors=[(48, "17:45:07", 23)],
        standing=2,                       # 시작 프레임: #1 #4 (#5 는 보류 표시)
    ),

    # ── 야간 인식 비교 ────────────────────────────────────────────────
    # 판정 로그가 없는 촬영분이다. 확정 판정 대신 영상에서 뽑아낸
    # 인식 결과(extract_live.py)로만 구역 맵을 채운다.
    dict(
        # 야간 영상에는 시각 배너가 없어 프레임을 못박을 수 없다. 대신 클립의
        # 박스 라벨(#4 SOCAR RETURN_VALID)이 로그의 S11 19:32 / S12 19:38 둘로
        # 좁혀지는데, S11 이라면 19:29:05 에 승인된 윗자리 스윙이 초록으로
        # 보여야 한다. 클립(같은 녹화인 night2 포함)에서 그 자리가 비어 있어
        # S12 로 확정. 판정은 클립 시작 전에 이미 확정된 상태라 t=0 에 붙인다.
        slug="night1-return", src="추가/m 모델 야간반납(pending).avi", enc="night",
        title="야간 반납 판정", group="야간 인식", session=12, clock="야간 19:38 무렵",
        summary="가로등만 있는 야간에 확정된 반납 승인(겹침 100%)이 유지되고, 옆 기체는 아직 보류로 남는다.",
        expect="반납 승인", kind="ok",
        anchors=[(0, "19:38:32", 4, "carry")],
        standing=1,                       # 왼쪽 쏘카 — 클립 시작부터 승인 표시
    ),
    dict(
        slug="night2-m-model", src="추가/m모델 야간인식개선됨(동일조건).avi", enc="night",
        title="m 모델 · 야간 인식", group="야간 인식", session=None, clock="야간",
        report=False,                     # 반납 판정이 아닌 비교 영상 — 판정 기록 섹션에서 뺀다
        summary="아래 s 모델과 같은 조건에서 m 모델로 바꾼 결과. 어두워도 기체를 계속 잡아낸다.",
        expect="인식 성공", kind="ok", anchors=[],
    ),
    dict(
        slug="night3-s-model", src="추가/s모델 야간인식불량(동일조건).avi", enc="night",
        title="s 모델 · 야간 인식", group="야간 인식", session=None, clock="야간",
        report=False,
        summary="동일 조건, 더 가벼운 s 모델. 같은 자리에 세워진 기체를 대부분 놓친다.",
        expect="인식 한계", kind="bad", anchors=[],
    ),
    dict(
        slug="night4-light", src="추가/야간 조명 유무 인식률 차이.avi", enc="night",
        title="조명 유무에 따른 차이", group="야간 인식", session=None, clock="야간",
        report=False,
        summary="같은 자리에서 조명이 없을 때와 켜졌을 때를 이어 붙인 영상. "
                "인근에 움직임이 감지되면 GPIO 로 조명이 자동 점등된다.",
        expect="조명 비교", kind="pending", anchors=[],
    ),
]


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


# 인코딩 프로파일 — 야간 영상은 센서 노이즈가 심해 그대로 넣으면 40배 커진다
ENC = {
    "day":   ("scale=1280:-2", "30"),
    "night": ("hqdn3d=12:8:12:14,scale=800:-2", "36"),
}


def transcode(pick=None):
    """원본 영상을 웹용으로 재인코딩한다."""
    import imageio_ffmpeg
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    OUT_MEDIA.mkdir(parents=True, exist_ok=True)
    for c in CASES:
        if pick and pick not in c["slug"]:
            continue
        vf, crf = ENC[c.get("enc", "day")]
        dst = OUT_MEDIA / f"{c['slug']}.mp4"
        subprocess.run([ff, "-y", "-loglevel", "error", "-i", c["src"],
                        "-vf", vf, "-c:v", "libx264", "-crf", crf,
                        "-preset", "slow", "-pix_fmt", "yuv420p", "-an",
                        "-movflags", "+faststart", str(dst)], check=True)
        print(f"  {c['slug']:<22} {Path(c['src']).stat().st_size/1e6:>6.1f}MB "
              f"-> {dst.stat().st_size/1e6:>5.2f}MB")


def main():
    if "--video" in sys.argv:
        i = sys.argv.index("--video")
        only = sys.argv[i + 1] if len(sys.argv) > i + 1 else None
        print("영상 재인코딩")
        transcode(only)

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
            "rec_frame": r["frame"],                     # 젯슨이 기록한 프레임
            "onstart": r["frame"] <= ONSTART_FRAME,      # 녹화 시작 직후 일괄 판정인가
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
        sess = sessions[c["session"] - 1] if c.get("session") else []
        events = []
        for a in c["anchors"]:
            frame, tm, track = a[0], a[1], a[2]
            hit = next((r for r in sess if r["time"] == tm and r["track_id"] == track), None)
            if hit is None:
                print(f"  ! {c['slug']}: {tm} #{track} 판정을 로그에서 못 찾음")
                continue
            e = shape(hit, c["session"])
            e["t"] = round(frame / fps, 2)
            e["frame"] = frame
            e["carry"] = len(a) > 3 and a[3] == "carry"
            events.append(e)
        events.sort(key=lambda e: e["t"])

        # 대표 장면 — 판정이 뜬 프레임, 없으면 후반부
        key_frame = events[-1]["frame"] if events else int(n * 0.75)
        poster(OUT_MEDIA / f"{c['slug']}.mp4", key_frame, OUT_MEDIA / f"{c['slug']}.jpg")

        cases.append({
            "id": c["slug"], "title": c["title"], "summary": c["summary"],
            "expect": c["expect"], "kind": c["kind"], "session": c.get("session"),
            "group": c.get("group", "주차 판정"),
            "video": f"media/{c['slug']}.mp4",
            "poster": f"media/{c['slug']}.jpg",
            "live": f"live/{c['slug']}.json",
            "duration": dur, "fps": round(fps, 3), "frames": n, "width": w, "height": h,
            "clock": c.get("clock") or c.get("approx_time") or (events[0]["time"] if events else ""),
            "approx": bool(c.get("approx_time")),
            "standing": c.get("standing", 0),
            "standing_bad": c.get("standing_bad", 0),
            "report": c.get("report", True),
            "events": events,
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
        sess_txt = f"세션{c['session']}" if c["session"] else "기록없음"
        print(f"  {c['id']:<18} {c['group']:<8} {c['duration']:>6.1f}s  {sess_txt:<8} "
              f"{c['clock']:<12} 판정 {len(c['events'])}건")


if __name__ == "__main__":
    main()
