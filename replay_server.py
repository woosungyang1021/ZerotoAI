"""
재생 서버 — 젯슨 인터페이스(/ws, /stream)를 그대로 흉내낸다.

demo_export/results.json 만 있으면 동작한다.
viz.mp4 가 같이 있으면 영상도 함께 재생하고, 없으면 데이터만 내보낸다.

준비:
    py -m pip install fastapi uvicorn websockets
    (영상까지 쓸 때만)  py -m pip install opencv-python

배치:
    replay_server.py
    index.html
    demo_export/
        results.json      ← 필수
        viz.mp4           ← 선택

실행:
    py -m uvicorn replay_server:app --port 8000
접속:
    http://localhost:8000
"""

import asyncio, json, threading, time
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse, Response
from fastapi.staticfiles import StaticFiles

# ─── 설정 ───────────────────────────────────────────────
EXPORT_DIR   = Path("demo_export")
LOOP         = True    # 끝나면 처음부터 다시
SPEED        = 4.0     # 1.0 = 원래 속도. 판정이 뜸하면 올려서 빨리 감기
JPEG_QUALITY = 72
# ────────────────────────────────────────────────────────

app = FastAPI()

data   = json.loads((EXPORT_DIR / "results.json").read_text(encoding="utf-8"))
FPS    = data["fps"]
FRAMES = data["frames"]
N      = len(FRAMES)

VIDEO = EXPORT_DIR / "viz.mp4"
HAS_VIDEO = VIDEO.exists()

play = {"jpeg": None, "idx": 0, "lap": 0, "running": True}


def clock():
    """재생 위치를 원래 속도로 진행시킨다. 영상이 있으면 프레임도 함께 읽는다."""
    cap = None
    if HAS_VIDEO:
        import cv2
        cap = cv2.VideoCapture(str(VIDEO))

    interval = 1.0 / (FPS * SPEED)
    next_at = time.perf_counter()
    idx = 0

    while play["running"]:
        if cap is not None:
            import cv2
            ok, frame = cap.read()
            if ok:
                ok2, buf = cv2.imencode(".jpg", frame,
                                        [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
                if ok2:
                    play["jpeg"] = buf.tobytes()

        play["idx"] = idx
        idx += 1

        if idx >= N:
            if not LOOP:
                play["running"] = False
                break
            idx = 0
            play["lap"] += 1
            if cap is not None:
                import cv2
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

        next_at += interval
        time.sleep(max(0.0, next_at - time.perf_counter()))

    if cap is not None:
        cap.release()


threading.Thread(target=clock, daemon=True).start()


@app.websocket("/ws")
async def ws(sock: WebSocket):
    await sock.accept()
    sent_upto, sent_lap = -1, play["lap"]

    await sock.send_json({
        "zone_poly": data["zone_poly"],
        "zone_name": data.get("zone_name", ""),
        "bounds_m":  data.get("bounds_m"),
        "has_video": HAS_VIDEO,
    })

    try:
        while True:
            idx, lap = play["idx"], play["lap"]

            if lap != sent_lap:            # 되감김: 이번 회차를 새 것으로
                sent_upto, sent_lap = -1, lap

            # 지난 전송 이후 지나간 프레임의 판정을 하나도 빠뜨리지 않는다
            events = []
            for f in FRAMES[sent_upto + 1: idx + 1]:
                for e in f["events"]:
                    events.append({**e, "id": f"{e['id']}@{lap}"})
            sent_upto = idx

            await sock.send_json({
                "fps": round(FPS * SPEED, 1),
                "latency_ms": 0,
                "active": len(FRAMES[idx]["live"]),
                "live": FRAMES[idx]["live"],
                "events": events,
                "progress": round(idx / N, 3),
            })
            await asyncio.sleep(0.3)
    except WebSocketDisconnect:
        pass


def mjpeg():
    last = None
    while play["running"]:
        jpg = play["jpeg"]
        if jpg is None or jpg is last:
            time.sleep(0.01)
            continue
        last = jpg
        yield b"--f\r\nContent-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"


@app.get("/stream")
def stream():
    if not HAS_VIDEO:
        return Response(status_code=204)   # 영상 없음 — 프론트가 안내를 띄운다
    return StreamingResponse(mjpeg(),
                             media_type="multipart/x-mixed-replace; boundary=f")


app.mount("/", StaticFiles(directory=".", html=True), name="web")

print(f"[replay] 세션 {data.get('session', '?')} · {N}프레임 · "
      f"{N / FPS:.0f}초 · 영상 {'있음' if HAS_VIDEO else '없음'} · 속도 x{SPEED}")
