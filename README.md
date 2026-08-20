# 선 넘은 PM 단속반

공유 PM(전동킥보드)이 지정 주차구역에 제대로 반납됐는지 자동으로 판독하고,
그 판정 과정을 웹에서 리플레이로 보여주는 프로젝트입니다.

**데모:** https://woosungyang1021.github.io/ZerotoAI/

## 판정 방식

| 판정 | 조건 |
|------|------|
| 정상 주차 | 접지점 발자국이 구역과 70% 이상 겹침 |
| 선 넘음 | 겹침 70% 미만 (구역 이탈) |
| 쓰러짐 | 기체 축이 눕는 전도 상태 |

- 검출/세그먼트: YOLO26s-seg
- 디바이스: Jetson Orin Nano
- 좌표 변환: IPM 호모그래피로 카메라 화면 → 지면(BEV) 좌표

## 구성

```
docs/index.html   판정 리플레이 웹페이지 (GitHub Pages 공개 대상)
docs/data.json    페이지가 읽는 경량 판정 데이터
docs/viz.mp4      (선택) 같은 세션의 시각화 영상 — 있으면 자동 재생
make_static.py    judgments.jsonl → docs/data.json 변환
make_results.py   원본 추론 결과 생성
replay_server.py  로컬 확인용 간이 서버
judgments.jsonl   젯슨에서 기록한 판정 로그 (원본)
judge_calib.json  주차구역 폴리곤 캘리브레이션
```

## 로컬에서 보기

`fetch`를 쓰기 때문에 파일을 더블클릭하면 열리지 않습니다. 서버로 띄우세요.

```bash
python -m http.server 8000 --directory docs
# http://localhost:8000
```

## 데이터 갱신

```bash
python make_static.py      # 트랙이 가장 많은 세션 자동 선택
python make_static.py 6    # 6번 세션 지정
```
