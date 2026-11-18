# PLAN — JIG 설계 자동화 통합 (app.py 중심)

## 목표

담당자의 `app.py`(2D 배열·가공도)를 메인 제품으로 두고, `jigauto`(3D 검증)와 사내
설계기준(`rules.json` 212건)을 붙여 **사내 기준 → 자동 설계 → 자동 검증**이 한 줄로
이어지게 한다.

## 성공 기준

| # | 기준 | 검증 방법 |
|---|---|---|
| S1 | `rules.json`의 수치 룰이 엑셀 원문과 1:1 대조된다 | 대조 검증기 실행 → 불일치 0건 또는 전건 사유 기록 |
| S2 | app.py의 하드코딩 수치가 기준 소스에서 주입된다 | `THRU_TOOL_DIAMETER`·`THRU_MERGE_GAP`·`PIN_CLEARANCE_DIAMETER`가 코드 상수로 남지 않음 |
| S3 | 생성 결과가 사내 기준을 위반하면 경고가 나온다 | 피난홀 < 1mm 등 위반 케이스를 넣어 WARN/ERROR 재현 |
| S4 | 3D 간섭 검증이 실제 도면에 적용된다 | 실제 FPCB + 실제 BASE JIG → 지그 ∩ 제품 부피 = 0 판정 |
| S5 | 담당자 앱의 기존 66개 unittest가 깨지지 않는다 | `python -m unittest` 전체 통과 |

## 가정 (사용자 미확정 — 확정되면 교체)

- `단품 TEST.dxf`의 단위는 mm다 (`$INSUNITS = 0`이라 확정 불가) — **미확정**
- `설계표준시트.xlsx`의 18개 파라미터 값은 전부 더미다 (시트에 명시) — 실제 값 **미수령**
- `rules.json`의 수치는 엑셀 원문과 같다 — **S1에서 검증 중**

## 단계

| 단계 | 내용 | 검증 방법 | 상태 |
|---|---|---|---|
| 0 | 압축 해제·전수 분석 | 파일 59개, 레이어·엔티티·기준 구조 확인 | ✅ 완료 (2026-11-17) |
| 1 | 통합 CLAUDE.md·PLAN.md 착수 산출물 | 두 파일 존재, 하위 규칙 복제 없음 | ✅ 완료 (2026-11-17) |
| 2 | `rules.json` ↔ 엑셀 원문 대조 검증 | S1 | ✅ 완료 (2026-11-17) |
| 3 | `config.toml` `[가정]` ↔ 사내 기준 매핑표 | 가정 26건 각각 대체 룰 지정 또는 "기준 없음" 명시 | ✅ 완료 (2026-11-17) |
| 4 | 기준 주입: app.py 상수 → 기준 소스 | S2, S5 | 대기 |
| 5 | 생성 결과 기준 위반 검사 연결 | S3, S5 | 대기 |
| 6 | 3D 검증 엔진 연결 (실데이터) | S4 | **차단** (OCP) |
| P1–P3 | layer 0 전처리기 `jig_preprocess.py` | 실데이터 피난홀 커버 100% | ✅ 완료 (2026-11-17), SMT 실도면 대응 (2026-11-18) |
| P4 | 전처리기 app.py 화면 연결 | 기존 테스트 기준선 유지 | ✅ 완료 (2026-11-18) |

## 블로커

| # | 내용 | 영향 | 해소 주체 |
|---|---|---|---|
| B1 | 스마트 앱 제어가 OCP(cadquery) 차단 | 단계 6 불가. jigauto 전체 실행 불가 | 사용자 (보안 설정) |
| B2 | ODA File Converter 미설치 | DWG 입출력 경로 검증 불가. DXF만 가능 | 사용자 (설치) |
| B3 | `단품 TEST.dxf` 단위 미지정 | 치수 전체가 조용히 틀릴 수 있음 | 사용자 확인 |
| B4 | `설계표준시트.xlsx` 값 더미 | 18개 파라미터 실값 없음 | 사용자 (사내 수령) |

단계 2·3은 B1~B4와 무관하게 진행 가능하다 (ezdxf·stdlib만 사용).

## 실행 기록

### 2026-11-17 — 단계 0: 실데이터 접수 및 전수 분석

- 카카오톡 수신 `과제 2번 검토.zip` (170MB, 11,766 엔트리) → `real_data/` 하위 해제.
  핵심 59개 추출, `.venv`·`site-packages`·`__pycache__` 11,687개 제외 (담당자 PC 경로 기준이라 사용 불가).
  압축 내 한글 이름이 CP949라 cp437→cp949 변환으로 복원.
- 들어온 것 세 묶음: ① `app.py` 2,545줄 + unittest 1,609줄 + PLAN.md 80KB (11/16까지 개발,
  결과 서명 `no-toolpaths-v19`) ② `CAD_Validator` (FastAPI, rules.json 212건)
  ③ `JIG_CAD_Local_v1` (2026-10-01 구 프로토타입, ①에 흡수된 세대)
- 실데이터: `단품 TEST.dxf` 142,176 엔티티(FPCB, `FPCB+EPOXY(A)=0.334T`),
  `JIG 도면/TAPE 부착 JIG-1·2·AR`, `base_jig_templates` 4종, `nesting_result.dxf` 26MB(실제 출력).
  `smt_fixture.dxf` 표제란에 (주)한국성전 — 진짜 현업 도면.
- 실제 기준: `치구 설계 관리 Sheet (1).xlsx` REV2.6, 치구 31종 60행.
  `rules.json`으로 212건 구조화 (자동검사 118 / ERROR 118·WARNING 94).
  `설계표준시트.xlsx`는 18항목 템플릿이나 값이 전부 더미.
- **핵심 발견**: `app.py`는 `rules.json`도 `설계표준시트.xlsx`도 읽지 않는다. 수치가 코드
  상수(`THRU_TOOL_DIAMETER=1.0`, `THRU_MERGE_GAP=1.5`, `PIN_CLEARANCE_DIAMETER=0.5`)와
  UI 입력(마진 20/간격 10)에 흩어져 있다 → 통합 1차 접점.
- jigauto와의 관계: 중복이 아니라 상보. app.py에 3D 간섭·받침 검사가 없고, jigauto에
  배열·가공도·실데이터·사내 기준이 없다.

### 2026-11-17 — 단계 1: 착수 산출물

- 루트 `CLAUDE.md`(통합 규칙·Constraints 9건), `PLAN.md`(이 파일) 작성.
- 하위 프로젝트 규칙은 복제하지 않고 각자의 `claude.md`/`AGENTS.md`를 단일 출처로 둔다.
  통합 작업의 기록은 이 PLAN.md 한 곳에만 이어 쓴다.

### 2026-11-17 — 단계 2: rules.json 원문 대조 (S1 충족)

- `tools/verify_rules.py` 작성. openpyxl 없이 stdlib(zipfile+xml)로 엑셀을 읽어 룰마다
  `source_row` 원문을 찾고 ① 원문이 그 행에 실제로 있는지 ② 수치가 원문에 문자로
  등장하는지 확인한다. 어느 환경에서나 돌아야 하므로 의존성을 두지 않았다.
- 결과: **룰 212건 / 수치 룰 66건 / 자동검사 118건 → 불일치 0건.**
- 검증기 자체의 역검증(변이 테스트): `min_value` 변이 **23/23 탐지**,
  `source_row` 변이 → `ROW_MISSING`, `source_text` 변이 → `TEXT_MISMATCH`.
  0건이 "검사가 비어서 통과한 것"이 아님을 확인했다.
- 결론: `rules.json` 212건을 설계·검증 수치의 단일 출처로 쓸 수 있다.
  `설계표준시트.xlsx`(값 전부 더미)는 계속 쓰지 않는다.

### 2026-11-17 — 단계 3: 기준 ↔ 코드 매핑 (`기준매핑.md`)

- **사내 기준이 있는데 코드가 어기는 것 2건**
  - `app.py:2278` `bot_relief_offset` UI 하한이 **0.0** — `DSG-005-02`(피난홀 ≥ 1.0mm,
    severity ERROR)를 위반하는 값을 그대로 받는다. clearance ≥ 1mm는 치구 16종에 반복
    등장하는 최다 기준이다. 같은 UI의 `edge_margin`(≥20)·`spacing`(≥10)은 하한이
    걸려 있으므로 동일 패턴으로 막을 수 있다 → 단계 4 최우선.
  - jigauto `comp_clearance = 0.5` — 같은 기준에 미달. 1.0 이상으로 상향 필요.
- **사내 기준이 있는데 구현이 없는 것 10건**: 4배열(`DSG-005-03`), 가이드핀 3개소
  (`DSG-026-02`), 핀 −0.1/편측 +0.05(`DSG-026-01`·`DSG-024-01`), 살대 폭 3.0
  (`DSG-011-02` — jigauto R6 받침 검사와 직결), 가이드 블럭 3파이(`DSG-027-01`),
  브릿지 내부 도피 1.0(`DSG-036-01`) 등.
- **코드에 있는데 근거가 없는 것 3건**: `THRU_TOOL_DIAMETER`(1.0),
  `THRU_MERGE_GAP`(1.5), `PIN_CLEARANCE_DIAMETER`(0.5). 사내 기준 확인 대기.
- **구조 발견**: 기준이 치구 유형 31종별로 갈린다. 단일 전역 상수로 둘 수 없고 선택된
  BASE JIG 유형에 따라 룰 집합을 고르는 구조가 필요하다. `rules.json`의 `jig_type`
  필드가 이미 그 축을 갖고 있어 추가 설계 없이 쓸 수 있다.
- 미확인: `app.py` `spacing`(10) ↔ `DSG-022-03`(제품 간격 ≥48mm)은 공정이 달라
  (라우터 JIG) 직접 비교할 수 없다. 사용자 확인 항목.

### 2026-11-17 — 단계 4: 피난홀 옵셋 하한 적용 (담당자 코드 변경 사전 기록)

CLAUDE.md Constraint("담당자 산출물을 고치기 전에 변경 사유와 범위를 먼저 적는다")에 따른 사전 기록.

**변경 사유**: `DSG-005-02`(피난홀 ≥ 1.0mm, severity **ERROR**)를 코드가 강제하지 않는다.
`app.py:2278`의 UI 하한이 `0.0`이고 `app.py:785`의 검증도 `0 <= x <= 10`이라, 사용자가
0.3을 넣으면 앱이 사내 기준 위반 지그를 그대로 생성한다. clearance ≥ 1mm는 `rules.json`에서
치구 16종에 반복 등장하는 최다 기준이다.

**변경 범위** (3곳, 전부 `real_data/과제 2번 검토/`):
1. `app.py:785` 검증 하한 `0` → `1.0`
2. `app.py:784·786` 오류 문구에 근거 `DSG-005-02` 표기 (통합 CLAUDE.md "근거 rule_id를 남긴다")
3. `app.py:2278` UI `number_input` 하한 `0.0` → `1.0`
4. `test_nesting.py` 하한 미달 거부 테스트 1개 추가

**범위 밖 (손대지 않음)**: `THRU_TOOL_DIAMETER`·`THRU_MERGE_GAP`·`PIN_CLEARANCE_DIAMETER`는
대응 사내 기준이 없어 확인 전까지 유지한다. jigauto `comp_clearance`(0.5)도 같은 기준
위반이지만 OCP 차단(B1)으로 회귀 검증이 불가해 이번 변경에서 제외한다 — 값만 바꾸면
포켓 형상이 달라져 기존 23개 테스트가 깨질 수 있고 그것을 확인할 방법이 없다.

**기본값은 바꾸지 않는다**: 기존 기본값이 이미 1.0이라 정상 사용 흐름의 결과는 동일하다.
회귀 위험은 하한 미달 값을 쓰던 경우에 한정된다.

#### 실행 환경 구축 (app.py 축)

- 담당자 `.venv`는 압축에서 제외했으므로 통합 루트에 `.venv-app` 을 새로 만들었다.
  담당자 트리를 건드리지 않기 위해 그 폴더 밖에 둔다. app.py 는 `__file__` 기준으로
  `base_jig_templates` 를 찾으므로 venv 위치와 무관하다.
- **SAC(스마트 앱 제어)는 파일 단위 평판 기반이다** (B1 의 정확한 성질). 전면 차단이 아니다:
  - 기존 `.venv`(11/8) 의 numpy 2.5.4 → 통과
  - 신규 설치 shapely 2.2.0 GEOS → 통과
  - 신규 설치 numpy **2.5.5** `_mt19937.pyd` → **차단**
  - → `numpy==2.5.4` 로 고정해 해소. 신규 pip 설치가 막히면 **이미 통과한 버전으로 핀**하는
    것이 우회법이다. OCP 는 어느 버전도 통과한 적이 없어 이 방법으로 풀리지 않는다.
- 검증된 조합: Python 3.12.10 / streamlit 1.67.0 / shapely 2.2.0 / **numpy 2.5.4(고정)** /
  ezdxf 1.4.5 / pandas 3.1.1 / matplotlib 3.11.2

#### 기준선 (수정 전)

`python -m unittest test_nesting` → **66 tests, errors=6, skipped=3**

- errors 6건은 전부 `FileNotFoundError` 로, 누락 입력 파일 **2개** 때문이다:
  `CAD/FPCB TEST-2.dxf`(4건), `CAD/제품 도면/TEST 제품도면 단품(DX)-2.dxf`(2건).
  압축에는 `FPCB TEST-2.dwg`(DWG)만 있고 `CAD/제품 도면/` 폴더는 아예 없다.
  담당자 PC 에서 ODA 로 변환해 만든 중간 산출물로 보인다 → **사용자 수령 대기 항목**
- skipped 3건은 ODA File Converter 부재(B2)
- 코드 결함은 0건이다

#### RED → GREEN

- RED: 추가한 `test_component_thru_offset_rejects_values_below_company_minimum` 가
  0.0 / 0.3 / 0.9 세 값 모두 `ValueError not raised` 로 실패. 경계값 1.0 은 정상 허용되어
  `test_component_thru_offset_accepts_company_minimum` 는 통과 — 과잉 수정 방지 확인.
- 수정 (3곳):
  - `app.py:785` 검증 하한 `0` → `1.0`
  - `app.py:784·786` 오류 문구를 `1~10mm` + `(DSG-005-02 피난홀 크기 최소 1mm)` 로.
    기존 테스트의 `'BOT 피난홀 옵셋'` 정규식과 신규 테스트의 `'DSG-005-02'` 를 둘 다 만족한다
  - `app.py:2278` UI `number_input` 하한 `0.0` → `1.0`, 라벨 `(mm, ≥1)`
    (같은 사이드바의 `edge_margin (≥20)`·`spacing (≥10)` 표기 관행에 맞춤)
- GREEN: `python -m unittest test_nesting` → **68 tests, errors=6, skipped=3**
  - 추가한 2개 통과, 기존 거부 테스트(`-0.1`/nan/inf/`10.1`)도 계속 통과
  - `test_component_thru_uses_full_one_mm_offset_and_does_not_output_tape_shape` 통과 →
    1mm 옵셋 동작 자체는 변하지 않았다
  - errors 6건은 기준선과 동일하며 전부 `FileNotFoundError`. 예외 유형 집계에서
    DSG 관련 실패 **0건** 확인 → **회귀 0건**
  - `py_compile app.py test_nesting.py verify_local.py` 통과

#### 미검증 / 남은 것

- **Streamlit UI 화면에서의 실제 동작은 확인하지 않았다** (하한이 UI 위젯에 걸리는 것은
  `number_input` 인자 변경으로만 확인). 앱을 띄워 사이드바를 눌러본 검증은 없다
- `jigauto` `comp_clearance`(0.5)는 같은 기준 위반이나 B1 로 회귀 검증 불가 → 미수정
- 근거 없는 상수 3건(`THRU_TOOL_DIAMETER`·`THRU_MERGE_GAP`·`PIN_CLEARANCE_DIAMETER`) 미수정

### 2026-11-17 — 단계 4: 기준 주입 구조 (`jig_standards.py`)

#### 조인 키 조사 결과 — 손으로 쓴 매핑표는 쓰지 않는다

- app.py: `BASE_JIG_TYPES` 는 `{"TAPE 부착 JIG 2단": "tape_jig_2"}` **1종**,
  `BASE_JIG_CATEGORIES` 는 `("TAPE 부착 JIG", "SMT수대", "기타")`. `index.json` 은 없다.
- rules.json: (공정, `jig_type`) **32쌍 / 유형 31종**.
- 이름이 어긋난다: app.py `"SMT수대"` ↔ rules.json `"AL 수대(TOP/BOT)"`.
  `"TAPE 부착 JIG"` 만 이름이 같다. → **직접 조인 불가**이며, 손으로 매핑표를 쓰면
  틀린 기준을 조용히 적용하게 된다. 그래서 매핑표를 만들지 않았다.

#### 설계: 적용 범위를 데이터에서 계산한다

유형을 몰라도 쓸 수 있는 기준은 두 조건을 **모두** 만족할 때뿐이다.

1. `uniform` — 룰들이 서로 다른 값을 말하지 않는다
2. `cross_type` — 적용 범위가 한 유형을 넘는다 (공정 `공통`의 `모든 치구` 이거나,
   서로 다른 치구 유형 2종 이상에 같은 값)

어긋나면 `AmbiguousStandard` 로 중단한다. 후보 중 아무거나 고르는 폴백은 없다.
공정 `자동화` 의 `모든 치구` 룰은 `process="자동화"` 를 명시해야 쓸 수 있다
(일반 치구에 자동화 기준을 적용하지 않기 위한 구분).

#### 설계 도중 바로잡은 결함 — `uniform` 만으로는 틀린다

처음에는 게이트를 `uniform` 하나로 두었다. 집계해 보니 148종 중 147종이 `uniform` 으로
나왔는데, 대부분은 **룰이 1건뿐이라 값 충돌이 없어서** 자동으로 통과한 것이었다.
그 1건은 특정 치구 전용이다 — `router_bit_spacing >= 46mm` 는 라우터 JIG 기준인데
TAPE 부착 JIG 에 적용하면 틀린다. `uniform`(값 충돌 없음) ≠ `적용 가능 범위`.
→ `cross_type` 조건을 추가해 단일 유형 전용 기준은 유형 지정 없이는 쓰지 못하게 했다.
`clearance` 는 17종에 같은 값으로 걸려 있어 통과하고, `router_bit_spacing` 은 막힌다.

이 수정으로 내가 쓴 테스트 픽스처 하나가 깨졌다 — clearance 룰을 1개 유형으로만 만들어
둔 비현실적 픽스처였다. 실제 구조(여러 유형, 같은 값)에 맞게 고쳤다. 코드가 아니라
테스트가 틀린 경우였고, 새 게이트가 그걸 잡아낸 것이다.

#### 변경 파일

- 신규 `jig_standards.py` (통합 루트) — `limit(parameter, jig_type, process, path)`,
  `parameters()`, `Limit.describe()`(근거 룰 ID 표기). stdlib 만 쓴다
- 신규 `test_jig_standards.py` — 12개. 실제 rules.json + 합성 룰 양쪽으로 검증
- `app.py`: `import sys` 추가 / 통합 루트 `sys.path` 부트스트랩 + `import jig_standards`
  (찾지 못하면 ImportError 로 즉시 멈춘다 — 기준 없이 도는 폴백을 넣지 않는다) /
  `relief_offset_minimum()` 추가 / 하한 리터럴 `1.0` 제거, 검증·오류문구·UI 위젯이
  모두 기준에서 값을 받는다
- `test_nesting.py`: 3개 추가 (기준 파일에서 값이 오는지, 기준 파일 없으면 중단하는지,
  사이드바 위젯에 하한이 걸리는지)

#### 검증

- `jig_standards` **12/12 통과**
- `test_nesting` **71 tests, errors=6, skipped=3** — 기준선과 동일, 회귀 0건.
  errors 6건 전부 `FileNotFoundError`(누락 입력 파일), Standards 관련 실패 0건
- `py_compile app.py test_nesting.py verify_local.py jig_standards.py` 통과
- **Streamlit 실제 기동 확인**: headless 로 띄워 `/_stcore/health` → `ok`,
  로그에 오류 없음 (import 부트스트랩이 앱 기동을 깨지 않음)
- **UI 위젯 확인(AppTest)**: `BOT 부품 피난홀 옵셋 (mm, ≥1)` / `min=1.0` / `value=1.0` /
  help `사내 설계기준 DSG-005-02, DSG-013-02 외 15건`, 예외 없음.
  지난 단계에서 "미검증"으로 남겼던 UI 항목을 닫았고 회귀 테스트로 고정했다
- 기준 2mm 로 바꾼 임시 rules.json 에서 1.5mm 가 거부되고 2.0mm 가 통과함을 확인 →
  하한이 **코드 상수가 아니라 기준 파일에서 온다**는 것이 증명됨

#### 지금 바로 주입 가능한 기준

`parameters()` 집계: 수치가 있는 유형 무관 기준이 다수 확보됐다. 예:
`kit_outer_support_width`(3.0 — jigauto R6 받침 검사와 직결), `kit_outer_guide_pin_count`(3),
`kit_outer_guide_pin_offset`(0.05), `inner_guide_block_diameter`(3.0),
`bridge_inner_escape`(1.0), `base_jig_thickness`(3T), `array_count`(4).
단일 유형 전용(`router_bit_spacing` 등)은 치구 유형 확정이 먼저다 → 사용자 확인 항목.

### 2026-11-17 — layer 0 전처리 이식 검토 (`이식검토.md`)

사용자 질문("외곽·부품 구분 없이 전부 layer 0 인 제품 도면도 전처리하는가")에 대한 조사.

- **현황**: 뷰 분리(TOP/SIDE/BOT)는 app.py 에 자동 인식이 있다(`detect_multiview` — TEXT/MTEXT
  의 "TOP/SIDE/BOT" 글자 + 홀 패턴). 그러나 **역할 분류(외곽/부품/홀)는 app.py 에 없다** —
  `app.py:2446-2450` 의 `multiselect` 로 사용자가 고른다. 형상 기반 자동 분류는
  `jigauto/dxf_input.py` 에만 있고 cadquery 기반이라 B1 으로 실행 불가.
- **이식 가능성**: cadquery → shapely 대응을 전수 확인한 결과 **전부 1:1**이며 새 의존성이
  0개다(app.py 가 이미 `polygonize`·`affinity`·`ezpath` 를 import). `_chains` union-find 는
  순수 파이썬이라 그대로 복사된다.
- **실데이터 spike**: `tape_jig_1.dxf`(실제 도면)를 전부 layer 0 으로 평탄화해 shapely
  파이프라인을 돌렸다. 외곽을 **90.00 x 120.00mm** 로 정확히 찾아냈다 — 담당자가 화면에서
  손으로 고른 값(PLAN.md "외곽 후보: 90.000 × 120.000 mm")과 일치. 원 지름 3.5 (8개)도
  도면 주서 `4-%%C3.5 THRU` 와 부합.
- **발견한 위험**: 평탄화 전 레이어를 정답으로 복원율을 재니 `JIG 외곽` 100%,
  `4` 88.5%, `Thru`/`DP` 50%, `PIN` 8.3%, **`layer 0` 3.2%** 였다.
  `HIDDEN`·`Center`·`BATANG` 0% 는 올바른 동작이고, `PIN` 은 반홀(관리 Sheet r11
  "양사이드 센터 5.02 반홀")일 수 있어 결함으로 단정하지 않았다. 확실한 문제는
  **layer 0 복원율 3.2%** 다 — 이 도면은 layer 0 이 주로 표제란이라 괜찮지만,
  부품 형상이 layer 0 에 있는 제품 도면에서는 치명적이다.
- **채택 방식**: 전처리기가 역할 레이어(`AUTO_OUTLINE`/`AUTO_COMPONENT`/`AUTO_HOLE`)를 입힌
  DXF 를 내고 app.py 는 기존 흐름 그대로 그 레이어를 고른다 → **app.py 코드 변경 0줄**,
  단독 테스트 가능, 사용자가 AutoCAD 로 분류 결과를 눈으로 검증 가능, jigauto 와 공유 가능.
  app.py 내부를 geometry 주입 가능하게 리팩터하는 대안은 AGENTS.md("단일 파일 구조 유지,
  최소 변경")와 충돌해 채택하지 않았다.
- **미확보**: "전부 layer 0 인 제품 도면" 실물을 아직 못 봤다. 받은 DWG 3개가 전부
  ODA 미설치로 읽히지 않는다 → **B2 해소가 이식 검증(P5)의 전제**다.
- spike 는 scratchpad 의 탐색 코드이며 저장소에 넣지 않았다. P1 에서 모듈로 정리한다.

### 2026-11-17 — P1~P3 측정: "복원율 개선" 전제 기각

`tape_jig_1.dxf` 로 가설 셋을 측정해 모두 기각했다. 모듈은 아직 쓰지 않았다.

- 전체 노딩(`unary_union`+`polygonize`)으로 바꾸면 낫다 → **더 나쁘다.** 면 300개,
  최대 면적 45,622/41,582(허위)이고 경계에 `Center` 224·`HIDDEN` 194·`BATANG` 158 이 낀다.
  차수2 규칙은 외곽 10,750(90×120)을 정확히 낸다 → **차수2 유지**
- 끊긴 틈 때문이다 → **아니다.** 공차 0.0001~0.05 에서 복원 선분 318 로 불변,
  0.1 이상에서는 258→150 으로 줄어든다. 현행 1e-3 유지
- T자 교차로 큰 덩어리가 탈락한다 → **아니다.** 열린 덩어리 1,132개 중 1,107개가 고립 선분
- **실제 원인**: 고립 선분의 길이 중앙값 0.120mm, 83% 가 1mm 미만인 서브밀리미터 파편이다.
  `layer 0` 복원율 3.2% 는 결함이 아니라 올바른 노이즈 거부였다
- → P3 을 **노이즈 억제**로 재정의. 최소 선분 1mm(원은 보존) 필터로 선분 1,501→481,
  외곽 후보 41→26. 면적 1·2위가 `JIG 외곽`(90×120, 70×100)이고 최대 외곽의 직계 자식 11개 중
  원 10개(지름 3.0×2, 6.5×4, 13.0×4)로 포함관계가 올바르게 나온다
- 남은 최상위 26개는 다중 뷰(6×120 등 판 측면 투영)라 결함이 아니다

### 2026-11-17 — B2 해소: ODA File Converter (관리자 권한 없이)

- 사용자가 받은 파일은 `ODADrawingsExplorer_…27.10.msi`(뷰어)로 **다른 제품**이었다
  (MSI Property 로 `ProductName = ODADrawingsExplorer` 확인). 설치하지 않았다
- ODA 공식 페이지에서 `ODAFileConverter_QT6_vc16_amd64dll.msi`(32.09MB)를 받았다.
  `ProductName = ODAFileConverter`, `27.10.0`, 서명 `Valid / CN=OPEN DESIGN ALLIANCE`
- 이 셸은 관리자 권한이 없어(`net session` 액세스 거부) `msiexec /i /qn` 은 실패한다.
  **`msiexec /a`(관리 설치=추출)** 로 `%USERPROFILE%\AppData\Local\ODAFileConverter\` 에
  59개 파일을 풀었다. 중첩 따옴표로 명령이 깨져 배치 파일로 실행해야 했다
- 검증: `ODA_CONVERTER` 를 그 exe 로 주면 `app.find_oda_converter()` 가 인식하고
  `app.oda_convert()` 로 `FPCB TEST-2.dwg` → DXF(191 엔티티) 변환 성공
- **환경변수는 아직 영구 설정하지 않았다** — 명령마다 `ODA_CONVERTER=…` 를 붙여 실행 중

#### 받은 DWG 의 레이어 구조 (ODA 변환 후)

| 파일 | 엔티티 | layer 0 | 비고 |
|---|---|---|---|
`FPCB TEST-2.dwg` | 191 | 13% | `부품` 90, `IGES LEVEL 241` 65, `보강판`, `내부홀`, `TAPE` |
`단품.dwg` | 144,927 | 1% | `BODY1-STL`~ 로 분리 |
`치공구 및 제품 유형별 도면.dwg` | 216,575 | 18% | 레이어 40종 |
`TAPE 부착 JIG-2.dwg` | 1,759 | 74% | JIG 도면 |
`SMT 수대 도면 (240 x 175 x 3).dwg` | 375 | 81% | JIG 도면 |

**"외곽·부품 구분 없이 전부 layer 0 인 제품 도면"은 받은 자료에 없다.** layer 0 비율이
높은 것은 JIG 도면뿐이고 제품 도면은 레이어가 나뉘어 있다 → 실물 샘플 수령 필요.

#### 테스트 기준선 갱신과 내 회귀 1건

- ODA 가 있으면 SKIP 3건이 실행된다. 그중 `test_screenshot_files_default_to_auto_detection…`
  가 **내 변경 때문에 깨져 있었다** — 위젯 라벨을 `(mm, ≥1)` 로 바꿨는데 이 테스트는
  원래 라벨 `'BOT 부품 피난홀 옵셋 (mm)'` 로 위젯을 찾는다. ODA 부재로 SKIP 되어 가려져 있었다.
  라벨을 원래대로 되돌리고 하한·근거는 `help` 에 두었다
- 기준선: **ODA 없음 `errors=6, skipped=3` / ODA 있음 `errors=7, skipped=0`** (71 tests).
  errors 는 전부 누락 입력 파일 3개 때문이다: `CAD/FPCB TEST-2.dxf`,
  `CAD/제품 도면/TEST 제품도면 단품(DX)-2.dxf`, `CAD/제품 도면/TEST 제품도면 단품(DX)-L.dwg`
- 교훈: SKIP 된 테스트는 통과한 테스트가 아니다. 환경 때문에 건너뛴 테스트가 있으면
  내 변경이 그 테스트를 깨는지 알 수 없다

### 2026-11-17 — 단계 P1~P3 완료: 전처리기 `jig_preprocess.py`

중단 지점(초안 미적용, 테스트 3건 실패)에서 재개해 마쳤다. app.py 는 건드리지 않았다.

#### 확정 설계 (측정으로 결정됨 — 다시 논의하지 않는다)

1. 닫힌 루프는 **끝점 차수 2 규칙** 유지. 끝점은 노드 좌표로 맞춘다(heal) — 빠뜨리면
   FPCB TEST-2 의 제품 외곽(호·스플라인 35개)이 사라진다
2. **선분 길이로 거르지 않는다** (단품 TEST.dxf 선분 중앙값 0.052mm)
3. **테두리는 자동 추정하지 않는다.** `Options(strip_frame=True)` 로만 뺀다. 감싸는 직사각형이
   있으면 notes 로 알린다. "내부 형상을 가진 자식이 있으면 테두리" 추정은 핀 표시가 있는 IC
   하나로도 제품 외곽을 버렸다. 실데이터에는 감싸는 테두리가 없었다
4. **부품 복원은 확정된 외곽의 내부 선만 노딩**한다. 기존 COMPONENT 루프는 그대로 두고 새로
   생긴 면만 추가한다. 생길 수 있는 오류가 "여분의 부품"(피난홀 하나)뿐이라 안전한 방향이다
5. **부품 안의 원도 홀로 둔다** (개수만 notes). 부품으로 바꾸면 보강판 안의 진짜 홀이 사라진다
6. 블록은 `preprocess()` 가 사본에서 explode 한다 (FPCB TEST-2: 부품 18개가 INSERT)
7. 최상위의 열린 선 덩어리는 외곽으로 올리지 않는다

#### 실데이터 검증 — FPCB TEST-2.dwg (전부 layer 0 으로 평탄화, 원래 레이어를 정답으로)

| 항목 | 1차 버전 | 완료 버전 |
|---|---|---|
`부품` 레이어 선 분류 | 62개 (534 미분류) | **240개** (356 미분류) |
`내부홀` 4개 | 4/4 | 4/4 |
제품 외곽 17.74x20.7 (두 뷰) | 2/2 | 2/2 |
분류 시간 | — | 0.55s (1,688 개체) |

**남은 356개가 결함인지 따로 확인했다.** 329개는 이미 잡은 부품 형상 **안의 상세선**(IC 핀 표시 등),
25개는 일부 걸침, 2개(합계 0.36mm)는 무관했다. 합격 기준을 개체 수가 아니라 **피난홀이 실제 부품을
덮는가**로 다시 쟀다:

| app.py 피난홀 옵셋 | 정답 부품 발자국 미커버 면적 |
|---|---|
0.0mm (전처리 결과 그대로) | 0.850mm² (23조각, 커버율 94.9%) |
0.1mm | 0.308mm² |
0.5mm | **0** |
**1.0mm (사내 하한, DSG-005-02)** | **0** |

빠진 조각은 최대 0.55x0.18mm 이고 전부 찾은 부품에 맞닿아 있다(최대 거리 0.001mm).
→ **사내 하한 옵셋을 적용한 피난홀이 이 도면의 실제 부품을 100% 덮는다.** 이 기준을 회귀 테스트
(`RealProductDrawingTests`)로 고정했다.

#### 범위 밖으로 확인한 것 — 3D 메쉬를 펼친 도면

`단품 TEST.dxf`(147,667 개체)는 **13.1초**에 끝나지만 외곽 후보가 2,877개(최대 5.91x14.78)로
쓸 수 없는 결과다. `BODY1-STL` 등 3D 메쉬 삼각형을 2D 로 펼친 도면이라 삼각형마다 닫힌 고리가
된다. 이 도면은 원래 레이어가 이미 나뉘어 있어 전처리 대상(전부 layer 0)도 아니다.
→ **app.py 연결 때 "전부 layer 0 인 도면에만 전처리 버튼을 노출"** 하는 것으로 막는다.
메쉬 전용 처리는 만들지 않는다 (그런 layer 0 도면을 받은 적이 없다).

#### 변경 파일

- `jig_preprocess.py` (통합 루트) — 초안 적용: heal 을 `_chains` 로 이동, 테두리 판정을
  `_enclosing_rect` + `strip_frame` 옵션으로, 외곽 내부 노딩으로 부품 복원, 외곽 후보 목록 note
- `test_jig_preprocess.py` — 25개. 확정 설계에 맞춰 4개 수정, 신규:
  `test_enclosing_rectangle_is_kept_as_outline_by_default`, `RealProductDrawingTests` 3개
  (ODA 없으면 클래스째 명시적 SKIP)
- `tools/jig_preprocess_core_draft.py.txt` — 적용 완료로 삭제

#### 검증

- `test_jig_preprocess` **25/25** (ODA 있음) / 22 통과 + 실데이터 클래스 SKIP (ODA 없음)
- `test_jig_standards` 12/12
- `test_nesting` ODA 없음 `errors=6, skipped=3` / ODA 있음 `errors=7` — **기준선과 동일, 회귀 0건**
  (errors 전부 누락 입력 파일 3개 탓)
- `py_compile jig_preprocess.py test_jig_preprocess.py jig_standards.py` 통과

#### 다음 할 일

1. **app.py 화면 연결 — 사용자 확인 후.** 업로드한 제품 도면이 전부 layer 0 일 때만
   "레이어 자동 구분" 버튼을 보이고, 누르면 `preprocess()` 결과 DXF 로 바꿔 기존 레이어 선택
   화면에 `AUTO_OUTLINE`/`AUTO_COMPONENT`/`AUTO_HOLE` 이 뜨게 한다. notes 는 화면에 그대로 보인다.
   담당자 트리를 고치므로 사유·범위를 먼저 이 장부에 적는다
2. "전부 layer 0 인 제품 도면" 실물이 오면 `RealProductDrawingTests` 와 같은 방식으로 고정한다

#### 실행 환경 메모

- 파이썬: `C:\0. project\Jig_automation_final\.venv-app\Scripts\python.exe` (numpy 2.5.4 고정)
- DWG 가 필요한 명령은 앞에 `ODA_CONVERTER="%USERPROFILE%\AppData\Local\ODAFileConverter\ODAFileConverter.exe"`
  (사용자가 `setx` 로 영구 설정했는지 확인)
- 긴 파이썬 코드를 bash heredoc 으로 넘기면 따옴표가 깨진다 — 파일로 써서 실행한다

#### 사용자 대기 항목

- 누락 파일 3개(`CAD/제품 도면/` 폴더), "전부 layer 0 인 제품 도면" 실물 샘플
- 스마트 앱 제어 결정(jigauto/OCP), `단품 TEST.dxf` 단위, 근거 없는 상수 3건의 사내 기준


### 2026-11-18 — 실제 layer 0 도면 대응: SMT 수대 도면

사용자 지적: `CAD/JIG 도면/` 에 layer 0 인 도면이 있다. 폴더를 원본 그대로(평탄화 없이) 전부 돌렸다.

#### 폴더 실측

| 파일 | 형상 개체 | layer 0 | 비고 |
|---|---|---|---|
`SMT 수대 도면 (240 x 175 x 3).dwg` | 293 | **75%** | 레이어가 `0`·`HIDDEN`·`thru` 뿐 — **이번 대상** |
`TAPE 부착 JIG-1.dwg` / `.dxf`, `TAPE 부착 JIG-2.dwg` | 약 1,500 | 70% | 기존 tape_jig 와 같은 도면. 90x120 판 정상 |
`TAPE 부착 JIG-AR.bak` | 2,998 | 71% | 판 285x190·305x190 등 |
`TAPE 부착 JIG-1.bak` | 673,750 | 40% | 제품 도면(`부품`·`BODY*-STL`)까지 든 대형 도면. 전처리 245초 |
`TAPE 부착 JIG-AR.dwg` | 673,750 | 40% | JIG-1.bak 과 같은 대형 도면(제품 도면 포함). 전처리 대상 아님 |

#### SMT 수대 도면에서 틀린 것 → 원인 → 수정

처음 결과: 외곽 1순위가 **550.2x370.7 (도면 틀)**, 240x175 판은 외곽 후보에 없음.

1. **겹쳐 그린 변.** 판(R4 모서리 호 4 + 선 5) 왼쪽 변이 끝점을 공유하지 않고 2.98mm 겹친 두 선
   `(92.16,152.65)→(92.16,210.63)`, `(92.16,207.65)→(92.16,319.65)` 이라 끝점 2개가 매달려
   판 전체가 닫힌 고리가 아니었다.
   → `_merge_overlaps`: 같은 직선 위에서 **겹친 쪽 안쪽 끝이 둘 다 매달린(차수 1)** 두 선만 한 선으로
   합친다. 맞붙은 두 부품처럼 끝이 이미 이어진 변을 합치면 꼭짓점이 변 중간에 놓여 둘 다 깨지므로
   그 경우는 합치지 않는다 (가드 테스트 있음). 합친 선은 원래 핸들 둘을 모두 가진다
2. **틀 감지 실패.** 틀 바깥에 선으로 그린 구역 표시 문자(닫힌 글자 획 2.8x3.9mm 포함)가 있어
   "다른 모든 형상을 감싸는 직사각형" 조건이 깨졌다.
   → `_frame_candidate`: **도면에서 가장 큰 형상이 직사각형이고 다른 형상을 품으면** 틀 후보.
   빼는 것은 여전히 `strip_frame=True` 일 때만(설계 3번 유지), 기본값에서는 notes 로 알린다
3. **다중 테두리.** 0.26mm 간격의 3중 테두리(550.2 → 549.7 → 549.3, 면적비 0.998)였다.
   → 틀을 뺀 뒤 바로 안의 직사각형이 틀 면적의 `Options.double_border_ratio`(0.95) 이상이면
   함께 뺀다. 틀 안의 직사각형 제품(테스트 그림 면적비 0.1)은 남는다
4. **기존 결함 발견.** 이전 버전은 틀을 무조건 연달아 빼서, 틀 안 직사각형 제품까지 빼고
   외곽이 하나도 없어 `PreprocessError` 로 멈췄다 (`test_frame_stripping_keeps_the_rectangular_product_inside`
   가 RED 에서 예외로 확인). 위 3번의 면적비 조건으로 막았다

#### 결과 (`strip_frame=True`, 원래 레이어와 대조)

| 역할 | 원래 레이어 | 내용 |
|---|---|---|
AUTO_OUTLINE | `0` 37 | **240x175 평면도 판(1순위, 내부 형상 20개)**, 240x3·3x175 측면 투영, 34.4x17.4, 글자 2 |
AUTO_HOLE | `thru` 12 + `0` 4 | 판 위 16개: Ø1.0x4, Ø3.8x4, Ø4.0x8 — 판 안 CIRCLE 전수와 일치 |
AUTO_COMPONENT | `HIDDEN` 36 | 판 위 5x5 숨은선 사각형 4개 (지그 도면에서는 "부품"이 아니라 안쪽 형상) |
AUTO_BORDER | `0` 12 | 테두리 3겹 |

#### 변경 파일

- `jig_preprocess.py`: 선 하나가 핸들 튜플을 갖게 바꿈(합친 선용), `_merge_overlaps` 추가,
  `_enclosing_rect` → `_frame_candidate`, 다중 테두리 처리, `Options.double_border_ratio`
- `test_jig_preprocess.py`: 33개. 신규 — 겹친 변 닫힘, 맞붙은 부품 미병합(가드), 틀 밖 글자가
  있어도 틀 제거, 이중 테두리, 틀 안 직사각형 제품 보존, `RealLayerZeroJigDrawingTests` 3개(SMT 실도면)

#### 검증

- `test_jig_preprocess` 33/33 (ODA 있음) / 27 통과 + 실도면 클래스 2개 SKIP (ODA 없음)
- `test_jig_standards` 12/12
- `test_nesting` ODA 없음 `errors=6, skipped=3` / ODA 있음 `errors=7` — 기준선과 동일, 회귀 0건
- 기존 실데이터 기준 유지: tape_jig_1 외곽 90x120·홀 14개, FPCB TEST-2 피난홀 커버 100%

#### 남은 것 / 주의

- 지그 도면에서는 AUTO_COMPONENT 가 "제품 부품"이 아니라 판 안쪽 닫힌 형상(숨은선 포켓 등)이다.
  app.py 에서 **제품 도면**용으로 쓰는 것이 전처리기의 원래 목적이다
- SMT 수대는 `strip_frame=True` 여야 맞다. 기본값에서는 틀이 외곽 1순위이고 notes 가 옵션을 안내한다.
  app.py 연결 시 "도면 테두리 제거" 체크를 함께 둔다


### 2026-11-18 — 단계 P4: 전처리기 app.py 화면 연결 (담당자 코드 변경 사전 기록)

CLAUDE.md Constraint("담당자 산출물을 고치기 전에 변경 사유와 범위를 먼저 적는다")에 따른 사전 기록.
사용자 승인: "화면 연결 진행".

**변경 사유**: layer 0 위주 도면은 지금 사용자가 외곽·부품·홀 모두에 `0` 을 골라야 하고, 그러면
역할 구분이 없어 피난홀·외곽이 뒤섞인다. 전처리기(`jig_preprocess.py`)가 역할 레이어를 입혀 주면
기존 레이어 선택 화면에 `AUTO_OUTLINE`/`AUTO_COMPONENT`/`AUTO_HOLE` 이 뜬다.

**설계**

- 주입 지점은 한 곳: `prod_dxf = load_cad(...)` 직후. 기본 배열·가공도 두 흐름 모두 이 바이트를 쓴다
- **선택 사항(기본 꺼짐)**: 제품 업로드 아래 체크박스 "레이어 자동 구분". 입력을 조용히 바꾸지
  않는다. 기본 꺼짐이라 기존 화면·기존 테스트 흐름은 그대로다
- **layer 0 이 형상의 절반을 넘는 도면에만 표시.** 실측 비율: SMT 수대 75%, TAPE JIG 70%,
  FPCB TEST-2 13%, 단품 TEST 1%, 치공구 및 제품 유형별 18%, 대형 JIG-1.bak/AR.dwg 40%.
  3D 메쉬를 펼친 도면(단품 TEST)처럼 결과가 쓸모없고 느린 도면은 버튼이 안 뜬다
- 켜면 "도면 테두리 제거"(`strip_frame`) 체크박스가 함께 뜬다 — SMT 수대처럼 틀이 있는 도면용
- 결과 notes 는 화면에 그대로 보인다. `PreprocessError` 면 경고만 띄우고 원본을 쓴다
- 전처리 결과는 `st.cache_data` 로 캐시한다 (Streamlit 은 위젯 조작마다 전체 재실행)
- 가공도 모드의 기능 레이어 추천은 이름에 OUTLINE/COMPONENT/HOLE 이 들어가면 고르므로
  `AUTO_*` 를 **코드 변경 없이** 추천한다. 기본 배열의 외곽 기본값만 "`0` 이 있으면 `0`" 이라,
  `AUTO_OUTLINE` 이 있을 때는 그것을 먼저 고르도록 한 줄 바꾼다

**변경 범위** (`real_data/과제 2번 검토/`)

1. `app.py`: 캐시 래퍼 2개, 업로드 직후 체크박스·전처리·notes 표시, 기본 배열 외곽 기본값 1줄
2. `test_nesting.py`: AppTest 추가 (layer 0 도면에서만 표시, 켜면 AUTO 레이어 기본 선택, 실패 시 경고)
3. 통합 루트 `jig_preprocess.py`: `layer_zero_share(doc)` 추가 (표시 조건 계산)

**바꾸지 않는 것**: 기존 위젯 라벨·키, 단일 파일 구조, 가공도 계산, 결과 서명

#### 실행 결과 (2026-11-18)

- RED: 신규 AppTest 3건이 "스위치 없음"으로 실패하는 것을 확인한 뒤 구현했다
- 변경
  - `app.py`: `import jig_preprocess`, 캐시 래퍼 `product_layer_zero_share`·`split_product_layers`,
    업로드 직후 "레이어 자동 구분"(기본 꺼짐)·"도면 테두리 제거" 체크박스와 "레이어 자동 구분 결과"
    안내, 실패 시 경고 후 원본 사용, 기본 배열 외곽 기본값을 `AUTO_OUTLINE` 우선으로 1줄
  - `jig_preprocess.py`: `layer_zero_share(doc)`, `LAYER_ZERO_MAJORITY = 0.5`
  - `test_nesting.py`: `ProductLayerAutoSplitTests` 3개 — layer 0 도면에서만 표시·기본 꺼짐,
    켜면 기존 선택 화면에 AUTO 레이어가 기본 선택되고 **배열 실행까지 성공**, 구분 불가 도면은
    경고 후 원본 유지
  - `test_jig_preprocess.py`: `layer_zero_share` 테스트 1개 (블록 안 포함, 문자 제외)
- 검증
  - `test_nesting` **74 tests** (기존 71 + 신규 3): ODA 없음 `errors=6, skipped=3` /
    ODA 있음 `errors=7` — 기준선과 동일, 회귀 0건 (errors 전부 누락 파일)
  - `test_jig_preprocess` 34/34, `test_jig_standards` 12/12
  - **실도면 화면 확인(AppTest)**: SMT 수대 DWG 를 제품으로 올림 → 스위치 표시·기본 꺼짐,
    자동 구분 전 외곽 레이어 `['0']` → 켜고 테두리 제거 → 외곽/부품/홀 레이어가
    `AUTO_OUTLINE`/`AUTO_COMPONENT`/`AUTO_HOLE`, **제품 외곽선 후보 1 = 240.000 × 175.000 mm**,
    안내 9건 표시, 예외·오류 없음
  - Streamlit headless 기동 `/_stcore/health` = `ok`
- **기준선 갱신**: `test_nesting` 은 이제 74 tests (errors 기준선 6/7 은 그대로)
- 미검증: 사람이 브라우저로 직접 눌러본 확인은 하지 않았다 (AppTest 와 health 로만 확인)


### 2026-11-18 — 사내 기준 미구현 10건: 적용 범위 확인과 4배열(DSG-005-03) 구현 (담당자 코드 변경 사전 기록)

사용자 요청: "사내 기준 미구현 10건 진행". 착수 전에 10건(룰 12개)의 적용 치구를 `rules.json` 으로 확인했다.

| 기준 | 룰 | 적용 치구 | app.py 적용 |
|---|---|---|---|
4배열 가능 | DSG-005-03 | AL 수대 | ✅ SMT수대 기본 배열 결과로 판정 가능 |
KIT 외곽 살대 3mm | DSG-011-02 | AL 수대, 조건 "AOI 설비 클램프 체결 시" | ⚠️ 자동화 모델 여부를 app.py 가 모름 |
가이드핀 편측 +0.05 / −0.1 / 3개소 | DSG-024-01·02, 026-01·02 | 라우터 JIG | ❌ app.py 는 라우터 지그를 설계하지 않음 |
내부 가이드 블럭 3파이 | DSG-027-01 | 라우터 JIG | ❌ |
BASE JIG 뜰채 3T | DSG-028-02 | 라우터 JIG | ❌ |
브릿지 내부 도피 1mm | DSG-036-01 | 라우터 JIG | ❌ 브릿지 개념 없음 |
브릿지 형상 +0.2 | DSG-056-01 | 공통, HARD PCB KIT 형상가공 조건 | ❌ 브릿지 개념 없음 |
탈착 핀 1.5mm | DSG-012-02 | FPCB 부착 JIG | ❌ 해당 치구 없음 |
하단 KIT 살대 3mm | DSG-061-02 | 자동화 하단 인라인 설비 | ❌ 설비 기준 |

라우터 지그 기준을 TAPE 부착·SMT 수대에 적용하면 `jig_standards` 가 막으려던 "엉뚱한 기준 적용"이 된다.
→ **어느 해석에서도 맞는 DSG-005-03 만 먼저 구현**하고 나머지는 사용자 결정을 받는다.

**변경 사유**: AL 수대는 "배열시 4배열 가능 하도록(가로 or 세로)"(관리 Sheet r5, severity ERROR)인데
app.py 는 SMT수대 배열 결과가 이를 만족하는지 알려주지 않는다.

**설계**

- 판정: 배치 결과의 한 행(가로) 또는 한 열(세로)에 제품이 `array_count`(=4) 개 이상이면 통과
- "가능 하도록" 은 능력 기준이므로 **최대 수량을 내는 Auto-Max 에서만 판정**한다. 수동 행×열·수량
  모드에서는 판정하지 않고 Auto-Max 로 확인하라고 안내한다 (일부러 적게 배치한 것을 위반으로 보지 않음)
- 수치(4)와 근거 룰은 `jig_standards.limit("array_count", jig_type=...)` 에서 받는다
- 치구 유형 대응: `jig_standards.APP_CATEGORY_JIG_TYPES = {"SMT수대": "AL 수대(TOP/BOT)"}` 한 건만 둔다
  (관리 Sheet r5 "SMT / AL 수대(TOP/BOT)" ↔ app.py 분류 "SMT수대"). **[가정] 사용자 확인 대상**.
  TAPE 부착 JIG 는 지금 쓰는 기준이 없어 대응을 만들지 않는다
- 위반이면 `st.error`, 통과면 `st.success` 로 룰 ID 와 함께 표시. 출력(다운로드)은 막지 않는다
  (CAD_Validator 의 FAIL 보고와 같은 의미 — 설계자가 보고 판단)

**변경 범위**

1. `jig_standards.py`: `APP_CATEGORY_JIG_TYPES`
2. `app.py`: `array_standard_check(res, base_category)` 함수, 기본 배열 결과 지표 아래 표시
3. `test_nesting.py`: 판정 함수 단위 테스트 + 임시 앱 사본에 SMT수대 지그를 등록한 AppTest

**바꾸지 않는 것**: 배치 계산(Auto-Max·마진·간격), 기존 라벨·키, 결과 서명

#### 실행 결과 (2026-11-18)

- RED: `ArrayStandardTests` 5개 — 판정 함수 없음(6 errors), 화면에 기준 표시 없음(2 failures)
- 변경: `jig_standards.APP_CATEGORY_JIG_TYPES`, `app.array_standard_check()`, 기본 배열 결과 지표 아래
  충족(`st.success`)·위반(`st.error`)·수동 배치 안내(`st.info`) 표시
- GREEN: `ArrayStandardTests` 5/5. 화면 테스트는 임시 앱 사본에 SMT수대 지그를 등록해 확인 —
  300x100 판(한 줄 5개) "충족", 200x150 판(3x3, 총 9개지만 한 줄 3개) "위반"
- 회귀: `test_nesting` **79 tests** — ODA 없음 `errors=6, skipped=3` / ODA 있음 `errors=7`, 기준선과 동일.
  `test_jig_preprocess` 34/34, `test_jig_standards` 12/12
- 미검증: 실제 SMT 수대 지그(`base_jig_templates/smt_fixture.dxf`)는 라이브러리에 등록돼 있지 않아
  실제 지그·실제 제품 조합으로는 판정해 보지 않았다

#### 남은 9건 — 사용자 결정 대기

- DSG-011-02 (KIT 외곽 살대 3mm, AL 수대): 조건 "AOI 설비 클램프 체결 시 / 자동화 타입 선정 모델".
  구현하려면 ① 자동화 모델 여부 입력(체크박스) ② 판정 대상 확정이 필요하다 — 원문
  "KIT 외곽으로부터 3mm 살대 적용 (6mm 이내 부품 간섭 여부 체크)" 를 "제품 외곽에서 6mm 안쪽에
  부품이 있으면 경고" 로 읽을지 확인받아야 한다
- 나머지 8건(라우터 JIG 7, FPCB 부착 JIG 1)은 app.py 가 설계하지 않는 치구의 기준이다.
  적용하려면 해당 치구 설계 기능이 먼저 있어야 한다


### 2026-11-18 — DSG-011-02/03 AOI 클램프 살대 간섭 확인 (담당자 코드 변경 사전 기록)

사용자 승인 해석: **"제품(KIT) 외곽에서 안쪽 6mm 이내에 부품이 있으면 경고"**, 자동화 모델일 때만.

**룰 확인**

- DSG-011-02 `kit_outer_support_width` = 3.0mm (ERROR, 조건 "AOI 설비 클램프 체결 시")
- DSG-011-03 `component_interference_check` (WARNING, `auto_checkable=false`) — **수치 칸이 비어 있고
  6mm 는 원문에만 있다**: "KIT 외곽으로 부터 3mm 살대 적용 (6mm이내 부품 간섭 여부 체크)" (관리 Sheet r11)
- rules.json 의 검사 항목 문구는 "**수대** 외곽 6mm 이내" 인데 원문은 "**KIT** 외곽으로부터".
  추출 시 문구가 바뀐 것으로 보고 원문(=승인 해석)을 따른다. **rules.json 문구 정정은 사용자 판단**

**설계**

- 6mm 는 코드 상수로 박지 않는다. `jig_standards.SOURCE_TEXT_VALUES = {"DSG-011-03": 6.0}` 에 두고
  `test_jig_standards` 가 **그 수가 해당 룰 원문에 문자 그대로 있는지 대조**한다 (원문 대조 규칙 준수)
- 3mm 는 `jig_standards.limit("kit_outer_support_width", jig_type=...)` 에서 받는다
- 판정: 선택한 제품 외곽 안의 닫힌 부품 형상 중 외곽선까지 거리 < 6mm 인 것의 개수와 최소 거리.
  홀(원)은 부품이 아니므로 세지 않는다. 제품 하나 기준(배치마다 같다)
- 화면: SMT수대 기본 배열 결과 아래 체크박스 "자동화 타입 모델 (AOI 설비 클램프 체결)" (기본 꺼짐).
  켜면 위반 시 `st.warning`(DSG-011-03 이 WARNING 등급), 없으면 `st.success`
- 관련 DSG-011-01(양사이드 센터 반홀 5.02)은 같은 조건의 다른 항목이며 이번 범위가 아니다

**변경 범위**: `jig_standards.py`(값 목록·`source_value`), `test_jig_standards.py`(원문 대조 테스트),
`app.py`(`clamp_clearance_check` + 체크박스·표시), `test_nesting.py`(단위 + 화면 테스트)

#### 실행 결과 (2026-11-18)

- RED: 원문 값 테스트 2개, `ClampClearanceStandardTests` 4개 실패 확인
- 변경: `jig_standards.SOURCE_TEXT_VALUES`·`source_value()`, `app.clamp_clearance_check()`,
  기본 배열 결과의 "자동화 타입 모델 (AOI 설비 클램프 체결)" 체크박스(기본 꺼짐)와 경고/통과 표시
- GREEN: `test_jig_standards` 14/14 (6mm 가 DSG-011-03 원문 "6mm이내" 와 문자 그대로 대조됨),
  `ClampClearanceStandardTests` 4/4 — 외곽 1mm 안 부품 → "1개, 1.00 mm" 경고, 정확히 6mm 안쪽은 통과,
  TAPE 지그는 해당 없음, 화면에서 체크 전에는 경고 없음
- 회귀: `test_nesting` **83 tests** — ODA 없음 `errors=6, skipped=3` / ODA 있음 `errors=7`, 기준선과 동일.
  `test_jig_preprocess` 34/34, `tools/verify_rules.py` 불일치 0건
- 미검증: 실제 SMT 수대 지그·실제 제품 조합으로는 판정해 보지 않았다 (실제 SMT 지그가 라이브러리에 미등록)

#### 사내 기준 10건 최종 정리

- 구현 2건(룰 3개): DSG-005-03(4배열), DSG-011-02/03(AOI 클램프 살대·6mm 간섭) — 둘 다 SMT수대 전용
- 적용 안 함 8건: 라우터 JIG 7건, FPCB 부착 JIG 1건 (+조건부 브릿지 1건, 자동화 설비 1건) —
  app.py 가 그 치구를 설계하지 않는다
- 사용자 판단 대기: rules.json DSG-011-03 의 검사 항목 문구("수대 외곽")를 원문("KIT 외곽")에 맞게 정정할지


### 2026-11-18 — ⏸ 재개 지점 (다음 세션은 여기서 시작)

이동으로 잠시 중단. **진행 중인 작업 없음** — 모든 변경은 테스트 통과·기록 완료 상태다. 띄워 둔 앱 서버도 정리했다.

#### 지금까지 완료 (이 장부의 위 절들 참조)

| 단계 | 내용 | 상태 |
|---|---|---|
4 | 기준 주입 구조 `jig_standards.py` + 피난홀 하한(DSG-005-02) | ✅ |
P1–P3 | layer 0 전처리기 `jig_preprocess.py` (+ SMT 수대 실도면: 겹친 변·다중 테두리) | ✅ |
P4 | 전처리기 app.py 화면 연결 ("레이어 자동 구분"·"도면 테두리 제거") | ✅ |
기준 | DSG-005-03 4배열, DSG-011-02/03 AOI 클램프 6mm 간섭 (SMT수대 전용) | ✅ |
6 | jigauto 3D 검증 연결 | ⛔ 스마트 앱 제어(OCP) 차단 |

#### 재개 시 첫 확인 (전부 통과해야 정상)

```
cd "C:\0. project\Jig_automation_final"
.venv-app\Scripts\python.exe -m unittest test_jig_standards        → 14 OK
.venv-app\Scripts\python.exe -m unittest test_jig_preprocess       → 34 OK (ODA 있으면)
cd "real_data\과제 2번 검토"
..\..\.venv-app\Scripts\python.exe -m unittest test_nesting        → 83 tests, errors=6 skipped=3 (ODA 없음)
                                                                     / errors=7 (ODA 있음) — 전부 누락 파일
```

DWG 를 쓰는 명령은 `ODA_CONVERTER=%USERPROFILE%\AppData\Local\ODAFileConverter\ODAFileConverter.exe` 가 필요하다.

#### 다음 작업 후보 (사용자 선택)

1. **실제 SMT 수대 지그로 확인** — `base_jig_templates/smt_fixture.dxf` 는 지그 목록에 미등록이다.
   화면의 "BASE JIG 신규 등록"으로 SMT수대 분류에 등록하면 4배열·클램프 판정을 실제 지그로 볼 수 있다
   (담당자 폴더의 `index.json` 이 생기므로 사용자가 직접 등록하거나 승인 후 진행)
2. **브라우저 직접 확인** — 지금까지는 AppTest·health 로만 확인했다
3. 사용자 대기 항목 처리(아래)가 오면 반영

#### 사용자 대기 항목

- `rules.json` DSG-011-03 검사 항목 문구 "수대 외곽" → 원문 "KIT 외곽" 으로 정정할지 (CAD_Validator 영향)
- `APP_CATEGORY_JIG_TYPES` 의 "SMT수대 = AL 수대(TOP/BOT)" 대응 — **2026-11-18 사용자 판단으로 유지(미확인)**. SMT수대로 AL 이 아닌 지그(예: 베크라이트 FPCB 부착 JIG)를 등록하면 AL 수대 기준이 잘못 적용되므로 담당자 확인 시 재검토
- 누락 파일 3개(`CAD/제품 도면/` 폴더), "전부 layer 0 인 제품 도면" 실물 샘플
- 근거 없는 상수 3건(`THRU_TOOL_DIAMETER`·`THRU_MERGE_GAP`·`PIN_CLEARANCE_DIAMETER`) 사내 기준
- 스마트 앱 제어 결정(jigauto 3D 검증), `단품 TEST.dxf` 단위, `ODA_CONVERTER` 영구 설정(`setx`) 여부


### 2026-11-18 — 정정: layer 0 비율 (담당자 "폴더 내 대부분 도면이 layer 0")

보유 CAD 전부를 **저장값**(파일에 기록된 레이어)과 **화면값**(AutoCAD 표시 — 블록 안 layer 0 개체는 블록이
놓인 레이어로 보임) 두 기준으로 다시 쟀다.

| 도면 | 형상 | 저장 L0 | 화면 L0 | 스위치 |
|---|---|---|---|---|
FPCB TEST-2.dwg (제품) | 1,688 | **60%** | **1%** | 표시 |
SMT 수대 / smt_fixture | 293 | 75% | 75% | 표시 |
TAPE 부착 JIG-1·2·AR (dwg·dxf·.bak 소형) | 약 1,500~3,000 | 70~72% | 같음 | 표시 |
JIG-1.bak · JIG-AR.dwg · 치공구 및 제품 유형별 | 673,750 | 40% | 21% | - |
단품.dwg | 454,125 | 22% | 22% | - |
단품 TEST.dxf | 147,667 | 0% | 0% | - |

- **담당자 말이 맞다.** 부품을 블록 안에서 layer 0 으로 그리는 AutoCAD 표준 작도 때문에, 파일 저장값으로는
  대부분이 layer 0 이다. app.py 레이어 목록도 저장값을 보므로 앱에서는 "대부분 layer 0" 으로 보인다
  (담당자 PLAN.md 의 "제품 형상 레이어: `0`, `부품` 함께 선택" 이 이 현상)
- **정정**: P4 기록의 "FPCB TEST-2 13% → 스위치 안 뜸" 은 틀렸다. 13% 는 블록을 분해한 뒤 값이었다.
  실제 스위치 판정값(저장값)은 60% 라 **FPCB 에서도 스위치가 뜬다**
- 동작은 유지한다: 앱 레이어 목록과 같은 기준이고, FPCB TEST-2 에서 전처리 결과가 검증돼 있다
  (피난홀 커버 100%). `layer_zero_share` 설명의 수치를 고치고, FPCB 에서 스위치가 뜨는 것을
  `RealProductDrawingTests.test_app_offers_the_switch_for_this_drawing` 로 고정했다 (35/35 통과)
- 관찰(범위 밖, 담당자 코드): app.py 기본 배열의 레이어 선택은 블록 안 layer 0 상속을 반영하지 않아
  `부품` 만 고르면 블록 안 부품이 빠진다. 가공도 모드(`named_entities`)는 상속을 반영한다


### 2026-11-18 — 실사용 검증 (실도면 시나리오 5개)

사용자 결정: AutoCAD·뷰어 없이 진행. 결과 도면은 PNG 로 렌더링해 확인하고, 최종본은 GitHub 에 올려
담당자가 내려받아 AutoCAD 로 마무리한다. **최종 범위 = layer 0 도면 전처리 기능까지 완료.**

`tools/field_scenarios.py` 로 앱(AppTest)에서 실제 도면 흐름을 끝까지 돌렸다. 결과는 `실사용검증/`.

| # | 시나리오 | 결과 |
|---|---|---|
S1 | 자동 인식 가공도 (TAPE 부착 JIG 2단 + FPCB TEST-2.dwg) | ✅ 배치 2, DP 0.45, THRU 1, 핀 2. 그림 확인 — 상판 제품 2·DP·THRU·핀, 하판 핀 |
S2 | 수동 레이어 가공도 (같음) | ✅ S1 과 같은 결과 |
S3a | 기본 배열, 담당자 방식 레이어 | ⚠️ 제품 외곽 기본 후보가 26.22x20.7 (실제 17.74x20.7 아님), **배치 1** |
S3b | 기본 배열 + 레이어 자동 구분·테두리 제거 | ⚠️ 제품 외곽 17.74x20.7 정상, 그러나 **배치 1** (2x2=4 가능) |
S4 | SMT 수대 실도면을 베이스로 + FPCB | ❌ 베이스 외곽 후보에 240x175 판이 없음 (틀 550x370 이 판을 흡수) |

공통 경고(담당자 기존 동작, 이번 범위 아님): 가공도의 "가이드핀이 THRU 영역과 겹침"·"Ø1 로도 남는 좁은 영역" —
담당자 AutoCAD 확인 항목으로 넘긴다.

#### 결함 1 — Auto-Max 경계 부동소수점 (담당자 코드 변경 사전 기록)

- 원인(실측): Auto-Max 는 첫 칸을 유효영역 경계에 **딱 붙여** 놓는다 (`cx = minx + w/2 + i*px`).
  제품 발자국 bbox 중심이 원점에서 0.0005mm 어긋나 있어(y −10.380 / +10.379) 첫 행·첫 열이 0.0005mm
  밖으로 나가 통째로 탈락했다. 유효영역 50x80, 발자국 17.80x20.76, 간격 10 → 2x2=4 가 맞는데 1.
  (처음 가설 "무게중심≠bbox 중심" 은 실측으로 기각 — bbox 중심은 원점이었다)
- 직사각형 영역은 위상 (0,0) 하나만 보고, 0개일 때만 다른 위상을 본다 (AGENTS.md 규칙). 1개는 0이 아니라
  다른 위상을 보지 않았다
- 수정: 직사각형 영역에서 **남는 폭을 양쪽에 반씩 나눈 "가운데 정렬" 위상**을 하나 더 보고 최대를 고른다.
  최대를 고르므로 기존보다 수량이 줄 수 없다. 경계에서 떨어지므로 부동소수점 오차에 강하다
- 기대: FPCB TEST-2 + TAPE 부착 JIG 2단 기본 배열 회전 0° → 4, 90° → 3, 자동 → 4.
  가공도 모드(상판 70x100 → 유효 30x60)의 2개는 그대로여야 한다 (담당자 테스트가 2 를 단언)

#### 결함 2 — layer 0 베이스 지그 (SMT 수대) 를 기본 배열에 못 씀 (담당자 코드 변경 사전 기록)

- 원인: 전처리기가 제품에만 붙어 있다. SMT 수대 실도면(형상 75% layer 0)을 베이스로 쓰면 layer 0 을
  고를 수밖에 없고, app.py 는 고른 레이어의 면을 합쳐 외곽을 만들므로 틀(550x370) 하나로 합쳐진다
- 수정: 기본 배열 화면에서 베이스에도 같은 스위치를 둔다 — "베이스 레이어 자동 구분"·"베이스 도면 테두리 제거"
  (기본 꺼짐, layer 0 이 절반 넘는 베이스만). 켜면 베이스 외곽 레이어 기본값이 `AUTO_OUTLINE` 이 된다
  (기존 `base_jig_default_layers` 가 이름에 OUTLINE 이 들어간 레이어를 고르므로 코드 변경 없음)
- 가공도 모드에는 넣지 않는다 — SMT수대는 가공도 모드에서 막혀 있고(담당자 규칙), TAPE 지그는 `JIG 외곽`
  레이어로 이미 동작한다
- 기대: S4 에서 베이스 외곽 후보 1 = 240x175, 배열 성공, 4배열 판정 표시

**변경 범위**: `app.py`(`auto_layout` 위상 1개 추가, 기본 배열 베이스 스위치), `test_nesting.py`(실패 테스트)

#### 실사용 검증 재실행 결과 (수정 후, 2026-11-18)

| # | 수정 전 | 수정 후 |
|---|---|---|
S1·S2 가공도 | 배치 2 | 배치 2 (변화 없음 — 상판 유효 30x60 이라 2 가 맞다) |
S3a 기본 배열, 담당자 방식 | 배치 1 | 배치 2 |
S3b 기본 배열 + 자동 구분 | 배치 1 | **배치 4 (2x2)** |
S4 SMT 수대 실도면 베이스 | 판을 못 찾음 | **240x175 판에 28개 (4x7)**, DSG-005-03 충족, AOI 클램프 경고(6mm 이내 78개, 최소 0.30mm) |

그림(`실사용검증/*.png`) 확인: S4 는 한국성전 SMT 캐리어 도면의 판 안에 FPCB 28개가 번호와 함께 배열됨.
- 테스트: `AutoLayoutBoundaryTests` 2개(합성 0.0005mm 어긋남 → 4, 실도면 회전 0/90/자동 → 4/3/4),
  `BaseLayerAutoSplitTests` 1개 (SMT 실도면 베이스 → 외곽 1순위 240x175, 배치 ≥4, 4배열 충족)
- 담당자 기본 배열 외곽 기본값 함수 `base_jig_default_layers` 는 SMT수대에 `["0"]` 을 주므로,
  호출하는 곳에서 `AUTO_OUTLINE` 이 있으면 그것을 먼저 고르게 했다 (담당자 함수는 그대로)

### 2026-11-18 — 최종본 묶기와 GitHub 인계 (담당자 코드 변경 사전 기록)

사용자 결정:
- GitHub **공개** 저장소 `mac9375-byte/jig_auto_korea` 생성 (빈 저장소, 2026-11-18). 비공개를 권했으나 사용자가 공개로 결정
- 담당자는 ZIP 으로 내려받아 **별도 새 폴더**에 풀고 **Codex** 로 마무리한다

**변경 사유**: 새 모듈(`jig_preprocess.py`·`jig_standards.py`)이 통합 루트에 있어 app.py 가 "두 단계 위 폴더"
에서 찾는다. 담당자가 새 폴더에 풀면 앱이 시작하자마자 멈춘다. → 한 폴더로 완결시킨다.

**변경 범위**
1. `jig_preprocess.py`·`jig_standards.py`·`test_jig_preprocess.py`·`test_jig_standards.py` 를 통합 루트 →
   `real_data/과제 2번 검토/` 로 **이동** (복사 아님 — 단일 출처 유지)
2. `jig_standards.DEFAULT_RULES` 를 모듈 옆 `CAD/CAD_Validator/CAD_Validator/rules/rules.json` 기준으로
3. `app.py` 의 통합 루트 `sys.path` 부트스트랩 제거 → 같은 폴더에서 바로 import (`import sys` 도 제거 — 내가 추가한 것)
4. `test_jig_preprocess.py` 의 `APP_DIR` 을 같은 폴더로, `tools/` 의 두 스크립트를 담당자 폴더 `tools/` 로 이동·경로 수정
5. Codex 가 읽는 `AGENTS.md` 끝에 통합 절 추가, 실행·인계용 `README.md` 추가
6. 통합 루트 `CLAUDE.md` 구조 절 갱신 (모듈 위치가 바뀜)

#### 최종본 묶기 실행 결과 (2026-11-18)

- 이동·경로 정리 후 담당자 폴더 전체 회귀: `test_jig_standards` 14 OK, `test_jig_preprocess` 35/35,
  `test_nesting` **86 tests** (기존 83 + 신규 3) — ODA 없음 `errors=6, skipped=4` / ODA 있음 `errors=7`.
  skipped 가 3→4 인 것은 새 실도면 테스트가 ODA 없을 때 SKIP 되기 때문. `verify_rules` 불일치 0건
- 배포 사본 `배포/jig_auto_korea/`: 허용 목록 20개 파일 712KB (코드·테스트·AGENTS.md·README.md·담당자 PLAN.md·
  `rules.json`·`tools/`·`docs/통합/`·`.gitignore`). 고객 도면·엑셀·base_jig_templates·출력물 제외.
  PC 사용자 경로 1곳(`docs/통합/PLAN.md`)을 `%USERPROFILE%` 로 바꿈. 이메일·실명 검출 없음.
  회사명 "한국성전" 은 문서 2개·테스트 주석 1곳에 남음 (공개는 사용자 결정)
- **담당자 PC 재현**: 배포 사본을 새 폴더에 풀고 ① 도면 없이 → 컴파일·기준 모듈 OK, 앱 기동 health `ok`
  ② README 대로 `CAD/` 도면·`base_jig_templates/` 복사 → 위 담당자 폴더와 같은 결과 (86 tests, errors=7 전부 누락 파일)
- `AGENTS.md`(Codex 지침) 끝에 "통합 추가 기능" 절: 모듈·화면 변경, 확정 설계 7항과 기각한 방법, 검증 명령·기준선
