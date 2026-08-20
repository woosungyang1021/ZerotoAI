"""
extract_live.py — 시연 영상에 그려진 모든 기체를 구역 맵 좌표로 뽑아낸다

젯슨은 확정 판정만 judgments.jsonl 에 남긴다. 지나가는 중이거나 멈춤을
확인하는 중인 기체는 기록이 없다. 하지만 영상에는 그 기체들도 박스가
그려져 있으므로, 영상에서 직접 읽어낸다.

방법
----
1. 오버레이 박스는 상태별로 색이 정해져 있다.
      초록 = 반납 승인   주황 = 반납 거부   흰색 = 판정 보류
   H.264 로 압축돼 정확한 색은 아니므로 HSV 범위로 잡는다.
2. 박스가 서로 겹쳐 하나로 뭉치므로, 열마다 가장 아래 화소를 훑어
   같은 높이로 이어지는 구간(= 박스 밑변)을 따로 끊는다.
3. 글자도 같은 색이라 밑변처럼 보인다. 양 끝에 세로변이 있는지 확인해
   글자를 걸러낸다.
4. 밑변 가운데 = 접지점. judge_calib.json 의 호모그래피로 지면(m)으로
   옮기고, 구역 맵과 같은 0~1 좌표로 정규화한다.

입력:  *.mp4 (1080p 원본 — 호모그래피가 1920x1080 기준이다)
       judge_calib.json
출력:  docs/live/<slug>.json   프레임별 기체 목록

사용:
    py extract_live.py            # 7편 전부
    py extract_live.py case3      # 이름에 case3 이 들어간 것만
"""

import json, sys
from pathlib import Path

import cv2
import numpy as np

from make_cases import CASES, MARGIN_M

CALIB   = Path("judge_calib.json")
OUT_DIR = Path("docs/live")

CODE = {"ok": 0, "bad": 1, "wait": 2}

# 촬영 조건별 설정.
# 야간 영상은 (1) 박스 색이 어두워 채도·명도 하한을 낮춰야 하고,
# (2) 화면 아래 돌난간이 흰 박스로 오인돼 허용 좌표를 구역 근처로 좁혀야 한다.
PROFILE = {
    "day": dict(
        hsv={"ok":  ((45, 150, 170), (75, 255, 255)),    # 초록 — 반납 승인
             "bad": ((8,  150, 190), (24, 255, 255))},   # 주황 — 반납 거부
        gray=((0, 0, 185), (180, 45, 255)),              # 흰색 — 판정 보류
        range_x=(-2.5, 6.5), range_y=(-2.5, 8.5),
    ),
    "night": dict(
        hsv={"ok":  ((40, 90, 110), (85, 255, 255)),
             "bad": ((5,  90, 120), (26, 255, 255))},
        # 야간에는 흰색 박스를 쓰지 않는다. 돌난간·노면 균열이 같은 밝기라
        # 정밀도가 50% 수준이었다. 확실한 초록·주황만 남긴다.
        gray=None,
        range_x=(-0.5, 4.2), range_y=(-0.3, 4.6),        # 구역(0~3.2, 0~3.8) 근처만
    ),
}

MIN_W    = 45      # 밑변 최소 길이(px, 1080p 기준)
TOL_Y    = 5       # 같은 밑변으로 볼 높이 차(px)
MIN_SIDE = 32      # 양 끝 세로변 최소 길이(px) — 글자 걸러내기
MERGE_M  = 0.3     # 이 거리 안이면 같은 기체로 본다(m)


def white_line_mask(hsv, gray):
    """흰색 박스는 도로·콘크리트와 색이 겹친다. 굵은 덩어리를 빼고 얇은 선만
    남긴 뒤, 압축으로 끊긴 밑변을 가로로 이어 붙인다."""
    raw = cv2.inRange(hsv, np.array(gray[0]), np.array(gray[1]))
    solid = cv2.morphologyEx(raw, cv2.MORPH_OPEN, np.ones((9, 9), np.uint8))
    thin = cv2.subtract(raw, solid)
    bridged = cv2.morphologyEx(thin, cv2.MORPH_CLOSE, np.ones((1, 11), np.uint8))
    return cv2.morphologyEx(bridged, cv2.MORPH_OPEN, np.ones((1, 25), np.uint8)), thin


def bottom_edges(mask, side_mask=None, min_w=MIN_W, min_side=MIN_SIDE):
    """열별 최하단 화소를 훑어 박스 밑변 구간 [(x0, x1, y), ...] 을 찾는다."""
    nz = mask > 0
    snz = nz if side_mask is None else side_mask > 0
    w = mask.shape[1]
    ys = np.full(w, -1, dtype=int)
    idx = np.arange(mask.shape[0])[:, None]
    hit = np.where(nz, idx, -1)
    ys = hit.max(axis=0)

    segs, x = [], 0
    while x < w:
        if ys[x] < 0:
            x += 1; continue
        x0, base = x, int(ys[x])
        while x + 1 < w and ys[x + 1] >= 0 and abs(int(ys[x + 1]) - base) <= TOL_Y:
            x += 1; base = (base + int(ys[x])) // 2
        if x - x0 + 1 >= min_w:
            y = int(np.median(ys[x0:x + 1]))
            top = max(0, y - 320)
            left  = snz[top:y, x0:x0 + 8].any(axis=1).sum()
            right = snz[top:y, max(0, x - 7):x + 1].any(axis=1).sum()
            if left >= min_side and right >= min_side:      # 박스에는 세로변이 있다
                segs.append((x0, x, y))
        x += 1
    return segs


def main():
    pick = sys.argv[1] if len(sys.argv) > 1 else None
    calib = json.loads(CALIB.read_text(encoding="utf-8"))
    H = np.array(calib["homography"])
    zone = [[float(p[0]), float(p[1])] for p in calib["zone_ground"]]

    xs = [p[0] for p in zone]; ys_ = [p[1] for p in zone]
    x0m, x1m = min(xs) - MARGIN_M, max(xs) + MARGIN_M
    y0m, y1m = min(ys_) - MARGIN_M, max(ys_) + MARGIN_M

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for c in CASES:
        if pick and pick not in c["slug"]:
            continue
        prof = PROFILE[c.get("enc", "day")]
        cap = cv2.VideoCapture(c["src"])
        fps = cap.get(cv2.CAP_PROP_FPS)
        # 호모그래피는 1920x1080 기준이다. 야간 영상은 960x540 이라 좌표를 맞춘다.
        scale = 1920.0 / max(1, cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        frames, k = [], 0
        while True:
            ok, img = cap.read()
            if not ok:
                break
            hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
            units = []
            for name in ("ok", "bad", "wait"):
                if name == "wait" and not prof["gray"]:
                    continue
                if name == "wait":
                    m, side = white_line_mask(hsv, prof["gray"])
                    segs = bottom_edges(m, side, min_w=int(100 / scale), min_side=int(45 / scale))
                else:
                    lo, hi = prof["hsv"][name]
                    m = cv2.inRange(hsv, np.array(lo), np.array(hi))
                    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
                    segs = bottom_edges(m, min_w=int(MIN_W / scale), min_side=int(MIN_SIDE / scale))
                for sx0, sx1, sy in segs:
                    v = H @ np.array([(sx0 + sx1) / 2 * scale, sy * scale, 1.0]); v = v / v[2]
                    gx, gy = float(v[0]), float(v[1])
                    if not (prof["range_x"][0] < gx < prof["range_x"][1]
                            and prof["range_y"][0] < gy < prof["range_y"][1]):
                        continue
                    if any(abs(gx - u[3]) < MERGE_M and abs(gy - u[4]) < MERGE_M
                           for u in units):                 # 같은 기체 중복
                        continue
                    units.append([round((gx - x0m) / (x1m - x0m), 3),
                                  round((y1m - gy) / (y1m - y0m), 3),
                                  CODE[name], gx, gy,
                                  (sx0 // 8, sx1 // 8, sy // 8) if name == "wait" else None])
            frames.append(units)
            k += 1
        cap.release()

        # 흰색은 에어컨 실외기·간판 같은 정지 구조물도 잡힌다.
        # 클립 내내 같은 자리에 있는 것은 오버레이가 아니므로 버린다.
        sig = {}
        for f in frames:
            for u in f:
                if u[5]:
                    sig[u[5]] = sig.get(u[5], 0) + 1
        static = {s_ for s_, v in sig.items() if v > 0.85 * len(frames)}
        frames = [[u[:3] for u in f if not (u[5] and u[5] in static)] for f in frames]
        total = sum(len(f) for f in frames)

        out = OUT_DIR / f"{c['slug']}.json"
        json.dump({"fps": round(fps, 3), "frames": frames},
                  out.open("w", encoding="utf-8"), ensure_ascii=False,
                  separators=(",", ":"))
        avg = total / max(1, k)
        print(f"  {c['slug']:<22} {k:>4}프레임  평균 {avg:4.1f}대  "
              f"최대 {max(len(f) for f in frames)}대  ({out.stat().st_size/1024:.0f} KB)")


if __name__ == "__main__":
    main()
