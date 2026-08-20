"""
make_static.py — 젯슨 판정 로그를 GitHub Pages용 경량 데이터로 변환

입력:  judgments.jsonl, judge_calib.json
출력:  docs/data.json          (수 KB — 판정 이벤트만 저장)

프레임마다 상태를 통째로 저장하던 방식(3.6MB)과 달리, 판정 이벤트만 담고
프레임별 상태는 브라우저가 재구성한다.

사용:
    py make_static.py          # 트랙이 가장 많은 세션 자동 선택
    py make_static.py 6        # 6번 세션
"""

import json, sys
from pathlib import Path

SRC_JSONL = Path("judgments.jsonl")
SRC_CALIB = Path("judge_calib.json")
OUT_DIR   = Path("docs")
FPS       = 30.0
MARGIN_M  = 1.0
ZONE_NAME = "공학관 앞 보도"

BRAND_KO = {"SOCAR": "쏘카", "SWING": "스윙", "GCOO": "지쿠"}
STATUS_KO = {
    "RETURN_VALID":        ("정상 반납", "정상 주차", False),
    "INVALID_OUT_OF_ZONE": ("구역 이탈", "선 넘음",   True),
    "INVALID_FALLEN":      ("전도",      "쓰러짐",    True),
}


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


def main():
    records = [json.loads(l) for l in SRC_JSONL.open(encoding="utf-8") if l.strip()]
    calib = json.loads(SRC_CALIB.read_text(encoding="utf-8"))
    zone = [[float(p[0]), float(p[1])] for p in calib["zone_ground"]]
    sessions = split_sessions(records)

    print(f"\n{'':<4}{'시각':<20}{'프레임':>11}{'판정':>7}{'트랙':>7}")
    print("─" * 50)
    for i, s in enumerate(sessions, 1):
        fr = [r["frame"] for r in s]
        print(f"{i:<4}{s[0]['time']}~{s[-1]['time']:<10}"
              f"{f'{min(fr)}~{max(fr)}':>11}{len(s):>7}"
              f"{len({r['track_id'] for r in s}):>7}")
    print("─" * 50)

    if len(sys.argv) > 1:
        pick = int(sys.argv[1])
    else:
        pick = 1 + max(range(len(sessions)),
                       key=lambda i: len({r["track_id"] for r in sessions[i]}))
        print(f"세션 지정이 없어 트랙이 가장 많은 {pick}번을 선택합니다.")
    sess = sessions[pick - 1]

    xs = [p[0] for p in zone]; ys = [p[1] for p in zone]
    x0, x1 = min(xs) - MARGIN_M, max(xs) + MARGIN_M
    y0, y1 = min(ys) - MARGIN_M, max(ys) + MARGIN_M

    def nrm(p):
        return [round((p[0] - x0) / (x1 - x0), 4),
                round((y1 - p[1]) / (y1 - y0), 4)]

    judgments = []
    for r in sess:
        state, verdict, bad = STATUS_KO.get(
            r["status"], (r["status"], "판정", bool(r.get("events"))))
        xy = nrm(r["ground_xy"])
        judgments.append({
            "frame": r["frame"],
            "t": round(r["frame"] / FPS, 2),
            "track_id": r["track_id"],
            "brand": BRAND_KO.get(r.get("brand"), "알 수 없음"),
            "state": state, "verdict": verdict, "violation": bad,
            "x": xy[0], "y": xy[1],
            "overlap": r.get("zone_overlap", 0.0),
            "time": r.get("time", ""),
        })
    judgments.sort(key=lambda j: j["frame"])

    n_frames = max(r["frame"] for r in sess) + 1
    OUT_DIR.mkdir(exist_ok=True)
    out = OUT_DIR / "data.json"
    json.dump({
        "fps": FPS, "zone_name": ZONE_NAME, "session": pick,
        "n_frames": n_frames, "duration": round(n_frames / FPS, 1),
        "zone_poly": [nrm(p) for p in zone],
        "bounds_m": {"x": [x0, x1], "y": [y0, y1]},
        "judgments": judgments,
    }, out.open("w", encoding="utf-8"), ensure_ascii=False, indent=1)

    bad = sum(1 for j in judgments if j["violation"])
    print(f"\n{out}  ({out.stat().st_size / 1024:.1f} KB)")
    print(f"세션 {pick} · {len(judgments)}건 · 트랙 "
          f"{len({j['track_id'] for j in judgments})}개 · "
          f"위반 {bad} / 정상 {len(judgments) - bad} · {n_frames / FPS:.0f}초")
    print(f"\ndocs/index.html 과 함께 커밋하면 GitHub Pages에서 바로 열립니다.")
    print(f"영상을 넣으려면 docs/viz.mp4 로 복사하세요 (100MB 미만).")


if __name__ == "__main__":
    main()
