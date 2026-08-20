"""
make_results.py — 젯슨 판정 로그를 대시보드용 데이터로 변환

입력:  judgments.jsonl, judge_calib.json   (젯슨 산출물)
출력:  demo_export/results.json            (대시보드가 읽는 파일)

judgments.jsonl 은 여러 번의 실행이 누적된 파일이라 세션으로 나눈 뒤
하나를 골라 쓴다. 실행하면 세션 목록을 출력한다.

사용:
    py make_results.py            # 세션 목록 보고 자동 선택
    py make_results.py 6          # 6번 세션으로 생성
"""

import json, os, sys
from pathlib import Path

# ─── 설정 ───────────────────────────────────────────────
SRC_JSONL = Path("judgments.jsonl")
SRC_CALIB = Path("judge_calib.json")
OUT_DIR   = Path("demo_export")
FPS       = 30.0     # 젯슨 입력 영상의 fps
MARGIN_M  = 1.0      # 구역 바깥 여유(m) — BEV 화면에 보일 범위
ZONE_NAME = "유억겸 기념관 앞"
# ────────────────────────────────────────────────────────

BRAND_KO = {"SOCAR": "쏘카", "SWING": "스윙", "GCOO": "지쿠"}
STATUS_KO = {
    "RETURN_VALID":        ("정상 반납", "정상 주차", False),
    "INVALID_OUT_OF_ZONE": ("구역 이탈", "선 넘음",   True),
    "INVALID_FALLEN":      ("전도",     "쓰러짐",     True),
}


def load_sessions(records):
    """프레임 번호가 되감기는 지점에서 세션을 자른다."""
    sessions, cur = [], [records[0]]
    for prev, rec in zip(records, records[1:]):
        if rec["frame"] < prev["frame"]:
            sessions.append(cur)
            cur = [rec]
        else:
            cur.append(rec)
    sessions.append(cur)
    return sessions


def main():
    records = [json.loads(l) for l in SRC_JSONL.open(encoding="utf-8") if l.strip()]
    calib = json.loads(SRC_CALIB.read_text(encoding="utf-8"))
    zone = [list(map(float, p)) for p in calib["zone_ground"]]

    sessions = load_sessions(records)

    print(f"\n{'':<4}{'시각':<18}{'프레임':>12}{'판정':>7}{'트랙':>7}")
    print("─" * 50)
    for i, s in enumerate(sessions, 1):
        fr = [r["frame"] for r in s]
        span = f"{min(fr)}~{max(fr)}"
        print(f"{i:<4}{s[0]['time']}~{s[-1]['time']:<8}{span:>12}"
              f"{len(s):>7}{len({r['track_id'] for r in s}):>7}")
    print("─" * 50)

    if len(sys.argv) > 1:
        pick = int(sys.argv[1])
    else:  # 트랙이 가장 많은 세션 = 볼거리가 가장 많은 세션
        pick = 1 + max(range(len(sessions)),
                       key=lambda i: len({r["track_id"] for r in sessions[i]}))
        print(f"세션 지정이 없어 트랙이 가장 많은 {pick}번을 선택합니다.")
    sess = sessions[pick - 1]
    print(f"→ 세션 {pick} 사용 ({len(sess)}건)\n")

    # ── 정규화: ground(m) → 0~1. y는 뒤집는다(먼 쪽이 위) ──
    xs = [p[0] for p in zone]
    ys = [p[1] for p in zone]
    x0, x1 = min(xs) - MARGIN_M, max(xs) + MARGIN_M
    y0, y1 = min(ys) - MARGIN_M, max(ys) + MARGIN_M

    def nrm(p):
        return [(p[0] - x0) / (x1 - x0), (y1 - p[1]) / (y1 - y0)]

    # ── 프레임 타임라인 구성 ──
    # 판정은 띄엄띄엄 발생하므로, 한 번 나타난 트랙은 이후 프레임에도
    # 계속 남겨둔다(최신 판정으로 갱신). 그래야 맵이 채워져 간다.
    n_frames = max(r["frame"] for r in sess) + 1
    by_frame = {}
    for r in sess:
        by_frame.setdefault(r["frame"], []).append(r)

    frames, live_state, first_seen = [], {}, {}

    for fi in range(n_frames):
        events = []
        for r in by_frame.get(fi, []):
            tid = r["track_id"]
            first_seen.setdefault(tid, fi)
            state, verdict, bad = STATUS_KO.get(
                r["status"], (r["status"], "판정", bool(r.get("events"))))
            brand = BRAND_KO.get(r.get("brand"), "알 수 없음")
            entry = {
                "track_id": tid, "cls": "PM", "brand": brand,
                "state": state, "verdict": verdict, "violation": bad,
                "contact": {"x": nrm(r["ground_xy"])[0],
                            "y": nrm(r["ground_xy"])[1]},
                "overlap": r.get("zone_overlap", 0.0),
                "conf": r.get("zone_overlap", 0.0),
                "dwell": round((fi - first_seen[tid]) / FPS),
            }
            live_state[tid] = entry
            events.append({**entry,
                           "id": f"{pick}-{tid}-{fi}",
                           "ts": r.get("ts"), "time": r.get("time"),
                           "zone": ZONE_NAME})

        # 살아있는 트랙의 체류 시간만 갱신
        live = []
        for tid, e in live_state.items():
            live.append({**e, "dwell": round((fi - first_seen[tid]) / FPS)})

        frames.append({"live": live, "events": events})

    OUT_DIR.mkdir(exist_ok=True)
    out = OUT_DIR / "results.json"
    json.dump({
        "fps": FPS,
        "zone_name": ZONE_NAME,
        "zone_poly": [nrm(p) for p in zone],
        "bounds_m": {"x": [x0, x1], "y": [y0, y1]},
        "session": pick,
        "frames": frames,
    }, out.open("w", encoding="utf-8"), ensure_ascii=False)

    n_ev = sum(len(f["events"]) for f in frames)
    tracks = len({r["track_id"] for r in sess})
    bad = sum(1 for r in sess if r["status"] != "RETURN_VALID")
    print(f"{out}  ({out.stat().st_size/1024:.0f} KB)")
    print(f"{n_frames}프레임 · {n_ev}건 판정 · 트랙 {tracks}개 "
          f"· 위반 {bad} / 정상 {len(sess)-bad}")
    print(f"재생 길이 약 {n_frames/FPS:.0f}초")


if __name__ == "__main__":
    main()
