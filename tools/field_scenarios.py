"""실사용 검증: 실제 도면으로 현업 흐름을 앱(AppTest)에서 끝까지 돌려 결과 파일과 요약을 남긴다.

결과는 이 프로젝트의 `실사용검증/` 에 저장한다 (결과 DXF·DWG·PNG·summary.json).
SMT 수대 시나리오는 임시 앱 사본에만 지그를 등록한다 (base_jig_templates 를 바꾸지 않는다).
CAD/ 의 실제 도면과 ODA File Converter 가 필요하다.

사용 (이 프로젝트 폴더에서):
    python tools\\field_scenarios.py
"""

from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import traceback
from pathlib import Path
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parent.parent
OUT = APP_DIR / "실사용검증"
sys.path.insert(0, str(APP_DIR))

import app  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402

TAPE_JIG = APP_DIR / "CAD" / "JIG 도면" / "TAPE 부착 JIG-2.dwg"
SMT_JIG = APP_DIR / "base_jig_templates" / "smt_fixture.dxf"
FPCB = APP_DIR / "CAD" / "FPCB TEST-2.dwg"


class Upload(io.BytesIO):
    def __init__(self, path: Path):
        super().__init__(path.read_bytes())
        self.name = path.name


def uploader(product: Path):
    def upload(label, **kwargs):
        return Upload(product) if label.startswith("②") else None
    return upload


def w(widgets, label):
    return next((x for x in widgets if x.label == label), None)


def messages(at):
    return dict(error=[e.value for e in at.error], warning=[x.value for x in at.warning],
                success=[x.value for x in at.success],
                exception=[str(e.value)[:300] for e in at.exception])


def save(name: str, data: bytes | None) -> str | None:
    if not data:
        return None
    path = OUT / name
    path.write_bytes(data)
    return path.name


def render(dxf_name: str) -> str | None:
    """결과 DXF 를 PNG 로 그린다 (AutoCAD 없이 눈으로 보는 용도)."""
    try:
        import ezdxf
        from ezdxf.addons.drawing import matplotlib as ezmpl
        doc = ezdxf.readfile(OUT / dxf_name)
        png = OUT / (Path(dxf_name).stem + ".png")
        ezmpl.qsave(doc.modelspace(), str(png), bg="#FFFFFF", dpi=120, size_inches=(16, 9))
        return png.name
    except Exception as error:   # 그림은 보조 자료라 실패해도 시나리오는 계속한다
        return f"(그림 실패: {type(error).__name__}: {str(error)[:80]})"


def open_app(path: Path, product: Path, mode: str):
    at = AppTest.from_file(str(path), default_timeout=300).run()
    if mode != at.segmented_control[0].value:
        at.segmented_control[0].set_value(mode).run()
    return at


def machining(name: str, mode: str) -> dict:
    with patch("streamlit.delta_generator.DeltaGenerator.file_uploader", side_effect=uploader(FPCB)):
        at = open_app(APP_DIR / "app.py", FPCB, mode)
        before = messages(at)
        button = next((b for b in at.button if b.label == "가공도 계산 및 배열"), None)
        if button is None:
            return dict(name=name, ok=False, stage="계산 버튼 없음", before=before)
        button.click().run()
        result = at.session_state["machining_result"] if "machining_result" in at.session_state else None
        info = dict(name=name, mode=mode, before=before, after=messages(at))
        if not result:
            return dict(info, ok=False, stage="결과 없음")
        plan = result["plan"]
        info.update(
            ok=True, placements=len(result["nesting"]["placements"]),
            dp_depths=sorted({round(op["depth"], 3) for op in plan["operations"] if op["kind"] == "DP"}),
            thru_count=sum(1 for op in plan["operations"] if op["kind"] == "THRU"),
            pins=len(plan["pins"]), relief_offset=plan["cfg"]["bot_relief_offset"],
            plan_warnings=plan.get("warnings", []),
            dxf=save(f"{name}_machining.dxf", result["dxf"]),
            dwg=save(f"{name}_machining.dwg", result["dwg"]))
        if info["dxf"]:
            info["png"] = render(info["dxf"])
        return info


def basic(name: str, app_path: Path, auto_split: bool, strip_frame: bool,
          base_layers=None, base_outline_size=None, base_auto=False) -> dict:
    with patch("streamlit.delta_generator.DeltaGenerator.file_uploader", side_effect=uploader(FPCB)):
        at = open_app(app_path, FPCB, "기본 배열")
        steps = []
        if base_auto:
            switch = w(at.checkbox, "베이스 레이어 자동 구분")
            if switch is None:
                return dict(name=name, ok=False, stage="베이스 레이어 자동 구분 스위치 없음", msgs=messages(at))
            switch.check().run()
            w(at.checkbox, "베이스 도면 테두리 제거").check().run()
            steps.append("베이스 레이어 자동 구분·테두리 제거 켬")
        if auto_split:
            switch = w(at.checkbox, "레이어 자동 구분")
            if switch is None:
                return dict(name=name, ok=False, stage="레이어 자동 구분 스위치 없음", msgs=messages(at))
            switch.check().run()
            steps.append("레이어 자동 구분 켬")
            if strip_frame:
                w(at.checkbox, "도면 테두리 제거").check().run()
                steps.append("도면 테두리 제거 켬")
        if base_layers:
            w(at.multiselect, "베이스 외곽 레이어").set_value(base_layers).run()
            steps.append(f"베이스 외곽 레이어 {base_layers}")
        if base_outline_size:
            box = w(at.selectbox, "베이스 외곽선 후보")
            hit = next((i for i, o in enumerate(box.options) if base_outline_size in o), None)
            if hit is None:
                return dict(name=name, ok=False, stage=f"베이스 외곽선 후보에 {base_outline_size} 없음",
                            candidates=box.options[:8], steps=steps)
            box.set_value(hit).run()
            steps.append(f"베이스 외곽선 후보 {box.options[hit]}")
        selected = {label: (w(at.multiselect, label).value if w(at.multiselect, label) else None)
                    for label in ("베이스 외곽 레이어", "제품 외곽 레이어", "제품 부품 레이어",
                                  "제품 내부 홀 레이어")}
        product_box = w(at.selectbox, "제품 외곽선 후보")
        base_box = w(at.selectbox, "베이스 외곽선 후보")
        info = dict(name=name, steps=steps, selected=selected,
                    product_outline=(product_box.options[product_box.index]
                                     if product_box and product_box.index is not None else None),
                    product_candidates=product_box.options[:5] if product_box else None,
                    base_outline=(base_box.options[base_box.index]
                                  if base_box and base_box.index is not None else None),
                    before=messages(at))
        run = next((b for b in at.button if "배열 실행" in b.label), None)
        if run is None:
            return dict(info, ok=False, stage="배열 실행 버튼 없음")
        run.click().run()
        clamp = w(at.checkbox, "자동화 타입 모델 (AOI 설비 클램프 체결)")
        if clamp is not None:
            clamp.check().run()
            info["steps"].append("자동화 타입 모델 켬")
        res = at.session_state["result"] if "result" in at.session_state else None
        info["after"] = messages(at)
        info["metrics"] = {m.label: m.value for m in at.metric}
        if not res:
            return dict(info, ok=False, stage="결과 없음")
        info.update(ok=True, placements=len(res["placements"]),
                    dxf=save(f"{name}_nesting.dxf", res.get("out_dxf")),
                    dwg=save(f"{name}_nesting.dwg", res.get("out_dwg")))
        if info["dxf"]:
            info["png"] = render(info["dxf"])
        return info


def main() -> int:
    if not app.find_oda_converter():
        print("ODA_CONVERTER 가 필요합니다 (DWG 입력).")
        return 2
    OUT.mkdir(exist_ok=True)
    report = []
    jobs = [
        ("S1_자동인식가공도", lambda: machining("S1_자동인식가공도", "자동 인식 가공도")),
        ("S2_수동레이어가공도", lambda: machining("S2_수동레이어가공도", "수동 레이어 가공도")),
        ("S3a_기본배열_담당자방식", lambda: basic("S3a_기본배열_담당자방식", APP_DIR / "app.py", False, False)),
        ("S3b_기본배열_자동구분", lambda: basic("S3b_기본배열_자동구분", APP_DIR / "app.py", True, True)),
    ]
    for name, job in jobs:
        print("실행:", name, flush=True)
        try:
            report.append(job())
        except Exception as error:
            report.append(dict(name=name, ok=False, stage="예외", error=traceback.format_exc()[-1500:]))

    print("실행: S4_SMT수대", flush=True)
    with tempfile.TemporaryDirectory() as folder:
        try:
            copy = Path(folder) / "app.py"
            shutil.copy(APP_DIR / "app.py", copy)
            app.save_base_jig_template("SMT 수대 240x175 (실도면)", SMT_JIG.read_bytes(),
                                       root=Path(folder) / "base_jig_templates", category="SMT수대")
            report.append(basic("S4_SMT수대", copy, True, True,
                                base_outline_size="240.000 × 175.000", base_auto=True))
        except Exception:
            report.append(dict(name="S4_SMT수대", ok=False, stage="예외", error=traceback.format_exc()[-1500:]))

    (OUT / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str),
                                      encoding="utf-8")
    for r in report:
        print(json.dumps(r, ensure_ascii=False, default=str)[:1500], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
