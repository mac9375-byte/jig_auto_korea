# JIG 자동 배열 · 가공도 (layer 0 도면 전처리 통합본)

로컬 Streamlit 앱. 베이스 지그(DXF/DWG)에 제품을 배열하고 홀·가이드핀을 계산하며, TAPE 부착 JIG 는
좌측 상판 DP·피난홀 THRU / 우측 하판 핀 가공도를 DXF·DWG 로 낸다. 작업 규칙은 `AGENTS.md`.

이 통합본은 담당자 원본에 다음을 더한 것이다 (상세: `AGENTS.md` "통합 추가 기능", 전체 기록: `docs/통합/PLAN.md`).

- **layer 0 도면 전처리** — 레이어 구분 없이 그린 제품·지그 도면을 외곽/부품/홀로 자동 구분 (`jig_preprocess.py`)
- **사내 설계기준 주입** — 피난홀 하한·4배열·AOI 클램프 판정을 `rules.json` 에서 받음 (`jig_standards.py`)
- Auto-Max 배치 결함 수정 — 비대칭 제품에서 첫 행·열이 빠지던 문제 (FPCB TEST-2: 1개 → 4개)

## 새 폴더에 받은 뒤 할 일

1. **도면 자료 복사** — 저장소에는 고객 도면이 없다. 기존 프로젝트 폴더에서 이 폴더로 복사한다.
   - `CAD/` 아래 도면(DWG·DXF·BAK)과 엑셀 — `CAD/CAD_Validator/CAD_Validator/rules/rules.json` 은 저장소에 있다
   - `base_jig_templates/` 폴더 전체 (등록된 BASE JIG — 없으면 앱에서 새로 등록해야 한다)
2. **패키지 설치** — `python -m pip install -r requirements.txt` (Python 3.12)
   - Windows 스마트 앱 제어가 켜진 PC 에서 `DLL load failed ... 애플리케이션 제어 정책` 이 나면, 이미 실행되던
     버전으로 고정한다 (통합 작업 PC 에서는 `numpy==2.5.4` 로 해결)
3. **ODA File Converter** — DWG 입출력에 필요. 기본 경로(`C:\Program Files\ODA\`)가 아니면 환경변수
   `ODA_CONVERTER` 에 exe 경로를 넣거나 앱 사이드바에 경로를 넣는다
4. **실행** — `run_app.bat` 또는 `python -m streamlit run app.py --server.address 127.0.0.1`

## 검증

```
python -m unittest test_jig_standards test_jig_preprocess test_nesting
```

- 86 tests. 1번 도면 복사 후 `CAD/제품 도면/` 폴더와 `CAD/FPCB TEST-2.dxf` 가 없으면 FileNotFoundError 6~7건이 난다
  (원본 폴더에도 없던 파일 — 코드 결함 아님). ODA 가 없으면 실제 DWG 테스트는 SKIP 된다 — SKIP 은 통과가 아니다.
- 사내 기준 원문 대조: `python -I tools/verify_rules.py "CAD/CAD_Validator/CAD_Validator/rules/rules.json" "CAD/치구 설계 관리 Sheet (1).xlsx"`

## 담당자 확인 항목 (AutoCAD 필요)

통합 작업 PC 에는 AutoCAD 가 없어 결과 도면은 그림(PNG)으로만 확인했다. 아래로 결과를 다시 만들어 AutoCAD 로 확인한다.

```
python tools/field_scenarios.py      → 실사용검증/ 에 S1~S4 결과 DXF·DWG·PNG·summary.json
```

| 시나리오 | 확인할 것 |
|---|---|
S1 자동 인식 가공도 (TAPE 부착 JIG 2단 + FPCB TEST-2) | 상판 DP·피난홀 THRU·핀, 하판 핀 위치 / "가이드핀이 THRU 와 겹침"·"Ø1 로 남는 좁은 영역" 경고가 실제 문제인지 |
S2 수동 레이어 가공도 | S1 과 같은 결과인지 |
S3b 기본 배열 + 레이어 자동 구분 | 90x120 판에 2x2=4개, 핀 위치 |
S4 SMT 수대 + 베이스 레이어 자동 구분 | 240x175 판에 4x7=28개, 판·구멍이 원본과 일치하는지, AOI 클램프 경고(6mm 이내 78개)가 타당한지 |

## 확인이 필요한 가정

- "SMT수대" 분류 = 관리 Sheet 의 "AL 수대(TOP/BOT)" (`jig_standards.APP_CATEGORY_JIG_TYPES`)
- `rules.json` DSG-011-03 의 검사 항목 문구는 "수대 외곽 6mm" 이나 원문은 "KIT 외곽으로부터 … 6mm" — 원문(제품 외곽)으로 구현
- 근거 사내 기준이 없는 상수: `THRU_TOOL_DIAMETER`(1.0) · `THRU_MERGE_GAP`(1.5) · `PIN_CLEARANCE_DIAMETER`(0.5)
