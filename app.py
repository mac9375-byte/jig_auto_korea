# -*- coding: utf-8 -*-
"""
JIG 제품 자동 배열(Nesting) + 피날홀/가이드핀 연산  -  100% 로컬 Streamlit 앱
=============================================================================

[실행 방법]
    pip install -r requirements.txt
    streamlit run app.py          # .streamlit/config.toml 설정이 자동 적용됨(127.0.0.1 바인딩, 통계 전송 OFF)

[보안/프라이버시 구조 - 외부 유출 방지]
  1) 서버 바인딩: .streamlit/config.toml 에서 address="127.0.0.1" -> 이 PC의 브라우저에서만 접속 가능.
  2) 텔레메트리 OFF: browser.gatherUsageStats=false -> Streamlit 사용 통계도 외부 전송 안 함.
  3) 코드 레벨 차단: install_offline_guard() 가 이 프로세스의 socket.connect 를 후킹하여
     루프백(127.0.0.1/::1) 이외의 모든 '외부 발신 연결'을 PermissionError 로 거부한다.
     (requests/urllib 등 어떤 라이브러리도 외부로 나갈 수 없다.)
  4) 파일 처리: 제품 업로드와 변환 임시 파일은 메모리/TemporaryDirectory에서만 처리한다.
     사용자가 등록한 BASE JIG DXF만 앱 폴더의 base_jig_templates에 로컬 보관한다.
     사이드바의 [세션 데이터 즉시 삭제] 버튼으로 메모리의 업로드/결과물도 즉시 비울 수 있다.
  5) DWG 입출력은 로컬에 설치된 ODA File Converter(오프라인 CLI)로 수행한다. (ezdxf 는 DWG 를 직접 읽지 못함)

[엔지니어링 규칙 요약] (단위: mm 가정)
  - 제품 외곽선 + 0.03mm 옵셋(미터 조인)  -> 배치/간격 검사용 '풋프린트'
  - 베이스 외곽에서 안쪽 20mm 이상 -> 유효 배치 영역(usable) = 베이스.buffer(-20)
  - 인접 제품 간 유격 >= 10mm -> 풋프린트 bbox 피치 = (폭|높이) + 10  (bbox 기반이라 항상 보수적으로 안전)
  - 피날홀: 제품 DWG 내부 CIRCLE -> 홀 직경 D, 가이드핀 = D - 0.1mm
"""
from __future__ import annotations

import ipaddress
import math
import socket
import subprocess
import tempfile
import glob
import hashlib
import io
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

import ezdxf
from ezdxf import disassemble
from ezdxf.addons import Importer
from ezdxf.math import Matrix44
from ezdxf import path as ezpath
from ezdxf import bbox as ezbbox
from ezdxf.enums import TextEntityAlignment
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.path import Path as MatplotlibPath
from matplotlib.patches import Circle, PathPatch
import pandas as pd
import shapely
import streamlit as st
from shapely import affinity
from shapely.geometry import LineString, Point, Polygon, box
from shapely.geometry.polygon import orient
from shapely.ops import polygonize, unary_union
from shapely.prepared import prep
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.page import PageMargins
from openpyxl.worksheet.properties import PageSetupProperties

# layer 0 도면 전처리(jig_preprocess)와 사내 설계기준 주입(jig_standards)은 이 파일 옆 모듈이다.
# 수치를 코드 상수로 박지 않기 위한 단일 출처이며, 찾지 못하면 ImportError 로 즉시 멈춘다
# (기준 없이 도는 폴백을 넣지 않는다).
import jig_preprocess
import jig_standards

plt.rcParams["font.family"] = ["Malgun Gothic", "AppleGothic", "NanumGothic", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

LINE_TYPES = {"LINE", "ARC", "LWPOLYLINE", "POLYLINE", "ELLIPSE", "SPLINE"}
ODA_VERSIONS = ["ACAD2018", "ACAD2013", "ACAD2010", "ACAD2007", "ACAD2004", "ACAD2000"]
BASE_JIG_TYPES = {
    "TAPE 부착 JIG 2단": "tape_jig_2",
}
BASE_JIG_TYPE_CATEGORIES = {
    "TAPE 부착 JIG 2단": "TAPE 부착 JIG",
}
BASE_JIG_CATEGORIES = ("TAPE 부착 JIG", "SMT수대", "기타")
BASE_JIG_LIBRARY_DIR = Path(__file__).resolve().parent / "base_jig_templates"
THRU_TOOL_DIAMETER = 1.0
THRU_TOOL_RADIUS = THRU_TOOL_DIAMETER / 2
THRU_MERGE_GAP = 1.5
PIN_CLEARANCE_DIAMETER = 0.5


def array_standard_check(res: dict, base_category: str):
    """사내 기준 '배열시 4배열 가능 하도록(가로 or 세로)' 판정. 해당 기준이 없는 치구면 None.

    돌려주는 dict: ok(True/False, 수동 배치면 None), need(필요 개수), best(한 줄 최대 개수), basis(근거 룰).
    "가능 하도록" 은 능력 기준이라 최대 수량을 내는 Auto-Max 배치에서만 판정한다.
    """
    jig_type = jig_standards.APP_CATEGORY_JIG_TYPES.get(base_category)
    if jig_type is None:
        return None
    try:
        lim = jig_standards.limit("array_count", jig_type=jig_type)
    except jig_standards.AmbiguousStandard:
        return None
    placements = res["placements"]
    rows = {}
    cols = {}
    for p in placements:
        rows[p["row"]] = rows.get(p["row"], 0) + 1
        cols[p["col"]] = cols.get(p["col"], 0) + 1
    best = max([*rows.values(), *cols.values()], default=0)
    need = int(lim.nominal)
    ok = best >= need if res["cfg"].get("mode") == "auto" else None
    return dict(ok=ok, need=need, best=best, basis=lim.describe())


def clamp_clearance_check(prod, base_category: str):
    """AOI 클램프 체결 모델의 KIT 외곽 살대 간섭 확인. 해당 기준이 없는 치구면 None.

    원문 "KIT 외곽으로 부터 3mm 살대 적용 (6mm이내 부품 간섭 여부 체크)" 를
    "제품 외곽에서 안쪽 6mm 이내에 부품이 있으면 경고" 로 판정한다 (2026-11-18 사용자 확정 해석).
    부품 = 제품 외곽 안의 닫힌 형상. 홀(원)은 세지 않는다. 제품 하나 기준이며 배치마다 같다.
    """
    jig_type = jig_standards.APP_CATEGORY_JIG_TYPES.get(base_category)
    if jig_type is None:
        return None
    try:
        rim = jig_standards.limit("kit_outer_support_width", jig_type=jig_type)
    except jig_standards.AmbiguousStandard:
        return None
    reach, reach_rule = jig_standards.source_value("DSG-011-03")
    edge = prod.outline.exterior
    near = [part.distance(edge) for part in prod.others
            if prod.outline.contains(part) and part.distance(edge) < reach]
    return dict(rim=rim.nominal, reach=reach, count=len(near),
                closest=min(near) if near else None, basis=f"{rim.describe()}, {reach_rule}")


def relief_offset_minimum() -> tuple[float, str]:
    """BOT 부품 피난홀 옵셋의 사내 기준 하한과 근거 룰 표기.

    `clearance` 기준은 치구 유형과 무관하게 전부 같은 값이라(DSG-005-02 외 16건,
    모두 >= 1.0mm ERROR) 치구 유형 매핑 없이 적용할 수 있다. 유형별로 값이 갈리는
    기준은 jig_standards 가 AmbiguousStandard 로 중단시킨다.
    """
    lim = jig_standards.limit("clearance")
    return float(lim.min), lim.describe()


# ---------------------------------------------------------------------------
# 0. 보안: 외부 발신 연결 차단
# ---------------------------------------------------------------------------
def install_offline_guard() -> None:
    """루프백 이외 주소로의 socket.connect 를 모두 차단 (프로세스 전체에 1회 적용)."""
    if getattr(socket, "_jignest_guard", False):
        return

    def _allowed(address) -> bool:
        if not isinstance(address, tuple):  # AF_UNIX 등 로컬 소켓
            return True
        host = str(address[0])
        if host == "localhost":
            return True
        try:
            return ipaddress.ip_address(host.split("%")[0]).is_loopback
        except ValueError:
            return False

    orig_connect, orig_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def guarded_connect(self, address):
        if not _allowed(address):
            raise PermissionError(f"[오프라인 모드] 외부 연결 차단: {address}")
        return orig_connect(self, address)

    def guarded_connect_ex(self, address):
        if not _allowed(address):
            raise PermissionError(f"[오프라인 모드] 외부 연결 차단: {address}")
        return orig_connect_ex(self, address)

    socket.socket.connect = guarded_connect
    socket.socket.connect_ex = guarded_connect_ex
    socket._jignest_guard = True


# ---------------------------------------------------------------------------
# 1. DWG <-> DXF 변환 (로컬 ODA File Converter)
# ---------------------------------------------------------------------------
def find_oda_converter() -> str:
    env = os.environ.get("ODA_CONVERTER", "")
    if env and Path(env).exists():
        return env
    w = shutil.which("ODAFileConverter")
    if w:
        return w
    for pat in (r"C:\Program Files\ODA\*\ODAFileConverter.exe",
                r"C:\Program Files (x86)\ODA\*\ODAFileConverter.exe"):
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[-1]
    return ""


def oda_convert(exe: str, data: bytes, src_ext: str, out_fmt: str, version: str, workdir: str) -> bytes:
    """ODAFileConverter <in_dir> <out_dir> <ver> <DWG|DXF> <recurse 0> <audit 1> <filter>"""
    idir = Path(tempfile.mkdtemp(dir=workdir))
    odir = Path(tempfile.mkdtemp(dir=workdir))
    (idir / f"model.{src_ext}").write_bytes(data)
    cmd = [exe, str(idir), str(odir), version, out_fmt.upper(), "0", "1", f"*.{src_ext}"]
    r = subprocess.run(cmd, capture_output=True, timeout=180)  # shell=False
    outs = [f for f in odir.iterdir() if f.suffix.lower() == f".{out_fmt.lower()}"]
    if not outs:
        raise RuntimeError(f"ODA 변환 실패 (code={r.returncode}) {r.stderr.decode(errors='ignore')[:300]}")
    return outs[0].read_bytes()


def read_doc(dxf_bytes: bytes):
    with tempfile.TemporaryDirectory(prefix="jignest_") as tmp:
        p = Path(tmp) / "m.dxf"
        p.write_bytes(dxf_bytes)
        return ezdxf.readfile(str(p))


def base_jig_index_path(root=None) -> Path:
    return Path(root or BASE_JIG_LIBRARY_DIR) / "index.json"


def base_jig_index_entries(root=None) -> list[dict]:
    path = base_jig_index_path(root)
    if not path.is_file():
        return []
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("BASE JIG 라이브러리 index.json을 읽을 수 없습니다.") from error
    if not isinstance(entries, list):
        raise ValueError("BASE JIG 라이브러리 index.json 형식이 올바르지 않습니다.")
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("BASE JIG 라이브러리 항목이 올바르지 않습니다.")
        filename = entry.get("file")
        if (not isinstance(entry.get("name"), str) or not entry["name"].strip()
                or entry.get("category") not in BASE_JIG_CATEGORIES
                or not isinstance(filename, str) or Path(filename).name != filename
                or Path(filename).suffix.lower() != ".dxf"):
            raise ValueError("BASE JIG 라이브러리 항목이 올바르지 않습니다.")
    return entries


def base_jig_template_path(kind: str, root=None) -> Path:
    library = Path(root or BASE_JIG_LIBRARY_DIR)
    slug = BASE_JIG_TYPES.get(kind)
    if slug:
        return library / f"{slug}.dxf"
    entry = next((item for item in base_jig_index_entries(root) if item["name"] == kind), None)
    if entry is None:
        raise ValueError(f"등록된 BASE JIG 유형이 아닙니다: {kind}")
    return library / entry["file"]


def base_jig_category(kind: str, root=None) -> str:
    if kind in BASE_JIG_TYPE_CATEGORIES:
        return BASE_JIG_TYPE_CATEGORIES[kind]
    entry = next((item for item in base_jig_index_entries(root) if item["name"] == kind), None)
    if entry is None:
        raise ValueError(f"등록된 BASE JIG 유형이 아닙니다: {kind}")
    return entry["category"]


def base_jig_replace_key(kind: str, dxf_data: bytes) -> str:
    base_jig_template_path(kind)
    digest = hashlib.sha256(dxf_data).hexdigest()[:12]
    slug = BASE_JIG_TYPES.get(kind, hashlib.sha256(kind.encode("utf-8")).hexdigest()[:12])
    return f"base_jig_replace_{slug}_{digest}"


def list_base_jig_templates(root=None, category=None) -> list[str]:
    kinds = [kind for kind in BASE_JIG_TYPES if base_jig_template_path(kind, root).is_file()]
    custom = sorted(base_jig_index_entries(root), key=lambda entry: (entry["category"], entry["name"]))
    kinds.extend(entry["name"] for entry in custom if base_jig_template_path(entry["name"], root).is_file())
    return [kind for kind in kinds if category is None or base_jig_category(kind, root) == category]


def load_base_jig_template(kind: str, root=None) -> bytes:
    path = base_jig_template_path(kind, root)
    try:
        data = path.read_bytes()
        doc = read_doc(data)
    except (OSError, ezdxf.DXFError) as error:
        raise ValueError(f"등록된 BASE JIG 도면을 읽을 수 없습니다: {kind}") from error
    if not any(True for _ in iter_entities(doc.modelspace())):
        raise ValueError(f"등록된 BASE JIG 도면이 비어 있습니다: {kind}")
    return data


def save_base_jig_template(kind: str, dxf_data: bytes, root=None, overwrite=False, category=None) -> Path:
    kind = kind.strip()
    entries = base_jig_index_entries(root)
    custom_entry = next((entry for entry in entries if entry["name"] == kind), None)
    builtin_category = BASE_JIG_TYPE_CATEGORIES.get(kind)
    if builtin_category:
        if category and category != builtin_category:
            raise ValueError(f"{kind} 유형의 기본 분류는 {builtin_category}입니다.")
        category = builtin_category
        path = base_jig_template_path(kind, root)
    else:
        if (not kind or len(kind) > 64 or not category or category not in BASE_JIG_CATEGORIES
                or kind in BASE_JIG_TYPES):
            raise ValueError("새 BASE JIG 이름과 유효한 분류를 입력하세요.")
        if custom_entry:
            path = base_jig_template_path(kind, root)
        else:
            digest = hashlib.sha256(f"{category}\0{kind}".encode("utf-8")).hexdigest()[:20]
            path = Path(root or BASE_JIG_LIBRARY_DIR) / f"custom_{digest}.dxf"
    if not dxf_data:
        raise ValueError("등록할 BASE JIG DXF가 비어 있습니다.")
    try:
        doc = read_doc(dxf_data)
    except Exception as error:
        raise ValueError("BASE JIG를 유효한 DXF로 변환한 뒤 등록하세요.") from error
    if not any(True for _ in iter_entities(doc.modelspace())):
        raise ValueError("BASE JIG DXF에 도면 형상이 없습니다.")
    if path.exists() and not overwrite:
        raise FileExistsError(f"{kind} 유형은 이미 등록되어 있습니다. 교체 확인 후 다시 저장하세요.")
    path.parent.mkdir(parents=True, exist_ok=True)
    writes = [(path, dxf_data)]
    if not builtin_category:
        if custom_entry:
            custom_entry["category"] = category
        else:
            entries.append(dict(name=kind, category=category, file=path.name))
        index_data = json.dumps(entries, ensure_ascii=False, indent=2).encode("utf-8")
        writes.append((base_jig_index_path(root), index_data))
    for output_path, data in writes:
        fd, temporary = tempfile.mkstemp(prefix=f"{output_path.stem}_", suffix=".tmp", dir=output_path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, output_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return path


def load_cad(upload, exe: str, version: str) -> bytes:
    """업로드 파일 -> DXF 바이트 (메모리 캐시: 같은 파일은 재변환하지 않음)."""
    data = upload.getvalue()
    key = hashlib.sha256(data).hexdigest()
    cache = st.session_state.setdefault("dxf_cache", {})
    if key in cache:
        return cache[key]
    ext = upload.name.rsplit(".", 1)[-1].lower()
    if ext == "dxf":
        dxf = data
    else:
        if not exe:
            raise RuntimeError("DWG 변환용 ODA File Converter 경로가 필요합니다. (또는 DXF 로 업로드)")
        with tempfile.TemporaryDirectory(prefix="jignest_") as tmp:
            dxf = oda_convert(exe, data, "dwg", "DXF", version, tmp)
    cache[key] = dxf
    return dxf


# ---------------------------------------------------------------------------
# 2. 도면 -> 형상(shapely) 추출
# ---------------------------------------------------------------------------
@dataclass
class Parsed:
    outline: Polygon          # 선택한 외곽선 (기본: 최대 면적)
    others: list              # 나머지 폐곡선 면(베이스의 내부 홀/장애물 후보)
    lines: list               # [(pts, closed)] 플래튼된 선형 요소 전체
    circles: list             # [(cx, cy, r, layer)]
    outlines: list            # 면적순 외곽선 후보


def iter_entities(entities, depth=0):
    for e in entities:
        if e.dxftype() == "INSERT":
            if depth < 4:
                try:
                    yield from iter_entities(e.virtual_entities(), depth + 1)
                except Exception:
                    pass
        else:
            yield e


def list_layers(doc) -> list:
    return sorted({e.dxf.layer for e in iter_entities(doc.modelspace())})


def parse_geometry(doc, layers, tol: float = 0.001, flat: float = 0.005,
                   outline_index: int = 0, circle_layers=None) -> Parsed:
    """선택 레이어의 엔티티를 shapely 로 변환.
    - ARC/LWPOLYLINE(bulge)/SPLINE 은 flat(mm) 오차로 직선 분할(플래트닝)
    - 끝점을 tol(mm) 격자로 스냅하여 미세한 틈이 있는 폐곡선도 닫아준다."""
    lines, circles, outline_circles = [], [], []
    for e in iter_entities(doc.modelspace()):
        selected = not layers or e.dxf.layer in layers
        t = e.dxftype()
        if t == "CIRCLE":
            c = e.dxf.center
            circle = (c.x, c.y, e.dxf.radius, e.dxf.layer)
            if selected:
                outline_circles.append(circle)
            if selected or (circle_layers is not None
                            and (not circle_layers or e.dxf.layer in circle_layers)):
                circles.append(circle)
        elif selected and t in LINE_TYPES:
            try:
                p = ezpath.make_path(e)
            except Exception:
                continue
            for sp in p.sub_paths():
                pts = [(v.x, v.y) for v in sp.flattening(flat)]
                if len(pts) >= 2:
                    lines.append((pts, math.dist(pts[0], pts[-1]) < 1e-6))

    polys = []
    if lines:
        segs = []
        for pts, _ in lines:
            g = LineString(pts)
            try:
                g = shapely.set_precision(g, tol)
            except Exception:
                pass
            if not g.is_empty:
                segs.append(g)
        merged = unary_union(segs)
        parts = list(merged.geoms) if hasattr(merged, "geoms") else [merged]
        polys = [Polygon(g.exterior) for g in polygonize(parts) if g.area > 1e-6]

    if polys:
        # 부품 경계/보조선이 나눈 면을 합쳐 제품 전체 외곽을 복원한다.
        merged = unary_union(polys)
        outlines = sorted(list(merged.geoms) if hasattr(merged, "geoms") else [merged],
                          key=lambda g: -g.area)
        if not 0 <= outline_index < len(outlines):
            raise ValueError("외곽선 후보가 변경되었습니다. 외곽선을 다시 선택하세요.")
        outline = outlines[outline_index]
        # 다른 폐곡선에 엄격히 포함된 면만 내부 장애물로 보존한다.
        tree = shapely.STRtree(polys)
        others = [p for i, p in enumerate(polys)
                  if any(j != i and p.boundary.disjoint(polys[j].boundary)
                         for j in tree.query(p, predicate="within"))]
        others.sort(key=lambda g: -g.area)
        return Parsed(outline, others, lines, circles, outlines)
    if outline_circles:  # 원형 외곽(선형 폐곡선 없음)
        if outline_index != 0:
            raise ValueError("외곽선 후보가 변경되었습니다. 외곽선을 다시 선택하세요.")
        c = max(outline_circles, key=lambda c: c[2])
        rest = [x for x in circles if x is not c]
        outline = Point(c[0], c[1]).buffer(c[2], quad_segs=64)
        return Parsed(outline, [], lines, rest, [outline])
    raise ValueError("선택한 레이어에서 폐곡선(외곽선)을 찾지 못했습니다. 레이어/허용오차를 확인하세요.")


@st.cache_data(max_entries=8, show_spinner=False)
def product_layer_zero_share(dxf: bytes) -> float:
    return jig_preprocess.layer_zero_share(read_doc(dxf))


@st.cache_data(max_entries=8, show_spinner=False)
def split_product_layers(dxf: bytes, strip_frame: bool) -> tuple:
    """layer 0 도면에 역할 레이어(AUTO_OUTLINE 등)를 입힌다. 못 하면 PreprocessError."""
    out, result = jig_preprocess.preprocess(dxf, jig_preprocess.Options(strip_frame=strip_frame))
    return out, list(result.notes)


@st.cache_data(max_entries=8, show_spinner=False)
def parse_cad(dxf: bytes, layers: tuple, tol: float, outline_index: int = 0,
              circle_layers=None) -> Parsed:
    return parse_geometry(read_doc(dxf), layers, tol, outline_index=outline_index,
                          circle_layers=circle_layers)


def outline_label(index, outlines):
    x0, y0, x1, y1 = outlines[index].bounds
    return f"후보 {index + 1}: {x1 - x0:.3f} × {y1 - y0:.3f} mm (X {x0:.3f}, Y {y0:.3f})"


# 다면도는 명시적인 레이어/블록 소스와 기능 레이어로만 분리한다.
def named_entities(entities, blocks=(), inherited_layer="0", depth=0):
    for entity in entities:
        layer = entity.dxf.layer if entity.dxf.layer != "0" else inherited_layer
        if entity.dxftype() == "INSERT":
            if entity.mcount > 1:
                yield from named_entities(entity.multi_insert(), blocks, inherited_layer, depth)
                continue
            if depth >= 8:
                raise ValueError("블록 중첩이 8단계를 넘습니다. 가공할 뷰 블록을 단순화하세요.")
            yield from named_entities(entity.virtual_entities(), blocks + (entity.dxf.name,), layer, depth + 1)
        elif entity.dxftype() in LINE_TYPES | {"CIRCLE"}:
            yield entity, layer, blocks


def machining_sources(doc):
    entries = list(named_entities(doc.modelspace()))
    sources = {f"LAYER:{layer}" for _, layer, _ in entries}
    sources.update(f"BLOCK:{name}" for _, _, blocks in entries for name in blocks)
    return sorted(sources)


def feature_layer_defaults(layers, feature):
    aliases = {"OUTLINE": ("OUTLINE", "외곽"), "HOLE": ("HOLE", "홀"),
               "COMPONENT": ("COMPONENT", "부품"), "TAPE": ("TAPE", "테이프"),
               "PROFILE": ("PROFILE", "단차", "깊이", "프로파일")}
    return [layer for layer in layers
            if any(alias in layer.upper() for alias in aliases.get(feature, (feature,)))]


def base_jig_default_layers(kind, layers):
    known = {
        "TAPE 부착 JIG 1단": ["JIG 외곽"],
        "TAPE 부착 JIG 2단": ["JIG 외곽"],
        "TAPE 부착 JIG AR": ["2", "7"],
        "SMT수대": ["0"],
    }
    selected = known.get(kind, [])
    if not selected:
        try:
            category = base_jig_category(kind)
            selected = {"TAPE 부착 JIG": ["JIG 외곽"], "SMT수대": ["0"]}.get(category, [])
        except ValueError:
            pass
    if selected and all(layer in layers for layer in selected):
        return selected
    return ["JIG 외곽"] if "JIG 외곽" in layers else feature_layer_defaults(layers, "OUTLINE")


def basic_result_signature(base_dxf, product_dxf, cfg):
    settings = tuple((key, tuple(sorted(value)) if isinstance(value, set) else value)
                     for key, value in sorted(cfg.items())
                     if key != "bot_relief_offset")
    return hashlib.sha256(b"basic-array-v1" + base_dxf + product_dxf
                          + repr(settings).encode()).hexdigest()


def source_entities(doc, source):
    if not source or ":" not in source:
        raise ValueError("TOP/SIDE/BOT 뷰의 레이어 또는 블록 소스를 각각 지정하세요.")
    kind, name = source.split(":", 1)
    if kind not in ("LAYER", "BLOCK"):
        raise ValueError("뷰 소스는 LAYER:이름 또는 BLOCK:이름이어야 합니다.")
    selected = [(entity, layer) for entity, layer, blocks in named_entities(doc.modelspace())
                if (kind == "LAYER" and layer == name) or (kind == "BLOCK" and name in blocks)]
    if not selected:
        raise ValueError(f"뷰 소스 {source}에 처리할 형상이 없습니다. MTEXT 표식은 뷰 식별자로 사용하지 않습니다.")
    return selected


def view_rectangle(bounds):
    if (not isinstance(bounds, (list, tuple)) or len(bounds) != 4
            or any(value is None or not isinstance(value, (int, float)) or not math.isfinite(value) for value in bounds)
            or bounds[0] >= bounds[2] or bounds[1] >= bounds[3]):
        raise ValueError("뷰 영역의 X/Y 최소·최대를 모두 입력하고 최소 < 최대인지 확인하세요.")
    return box(*bounds)


def region_entities(doc, bounds):
    view_rectangle(bounds)
    xmin, ymin, xmax, ymax = bounds
    selected = []
    for entity, layer, _ in named_entities(doc.modelspace()):
        extents = ezbbox.extents([entity], fast=False)
        if extents.has_data:
            lo, hi = extents.extmin, extents.extmax
            if xmin <= lo.x and ymin <= lo.y and hi.x <= xmax and hi.y <= ymax:
                selected.append((entity, layer))
    return selected


def validate_view_regions(cfg):
    rectangles = [(view, view_rectangle(cfg.get(f"{view.lower()}_region"))) for view in ("TOP", "SIDE", "BOT")]
    for index, (view, rectangle) in enumerate(rectangles):
        for other_view, other in rectangles[index + 1:]:
            if rectangle.intersects(other):
                raise ValueError(f"{view}/{other_view} 뷰 영역이 겹치거나 경계가 닿습니다. 형상 중복을 막도록 영역 사이에 간격을 두세요.")


def detect_multiview(doc):
    """Infer the supplied horizontal TOP/SIDE/BOT layout only when labels and hole pairs agree."""
    marker_positions = {view: [] for view in ("TOP", "SIDE", "BOT")}
    for entity in doc.modelspace():
        kind = entity.dxftype()
        if kind == "TEXT":
            raw_text = entity.dxf.text
        elif kind == "MTEXT":
            raw_text = entity.plain_text()
        else:
            continue
        label = "".join(char for char in raw_text.upper() if char.isalpha())
        if label in marker_positions:
            point = entity.dxf.insert
            marker_positions[label].append((point.x, point.y))
    if any(len(points) != 1 for points in marker_positions.values()):
        return None
    markers = {view: points[0] for view, points in marker_positions.items()}
    if (markers["TOP"][0] >= markers["SIDE"][0] or markers["SIDE"][0] >= markers["BOT"][0]
            or max(point[1] for point in markers.values()) - min(point[1] for point in markers.values()) > 1.0):
        return None

    # ponytail: support the labelled horizontal view arrangement used by this drawing; fall back to manual selection for other layouts.
    gaps = {view: min(abs(x - markers[other][0]) for other in markers if other != view)
            for view, (x, _) in markers.items()}
    grouped = {view: [] for view in markers}
    for entity, layer, _ in named_entities(doc.modelspace()):
        extents = ezbbox.extents([entity], fast=False)
        if not extents.has_data:
            continue
        lo, hi = extents.extmin, extents.extmax
        cx, cy = (lo.x + hi.x) / 2, (lo.y + hi.y) / 2
        view = min(markers, key=lambda name: abs(cx - markers[name][0]))
        marker_x, marker_y = markers[view]
        if cy <= marker_y or math.hypot(cx - marker_x, cy - marker_y) > gaps[view] * 0.72:
            continue
        grouped[view].append((entity, layer, (lo.x, lo.y, hi.x, hi.y)))
    if any(not grouped[view] for view in grouped):
        return None

    regions = {}
    view_entries = {}
    for view, entries in grouped.items():
        bounds = (min(item[2][0] for item in entries) - 0.01,
                  min(item[2][1] for item in entries) - 0.01,
                  max(item[2][2] for item in entries) + 0.01,
                  max(item[2][3] for item in entries) + 0.01)
        regions[view.lower()] = bounds
        view_entries[view] = region_entities(doc, bounds)
    cfg = dict(view_mode="regions", top_region=regions["top"], side_region=regions["side"],
               bot_region=regions["bot"], tol=0.001, hole_dmin=0.5, hole_dmax=30.0,
               pin_height=1.8, top_outline_index=0)
    try:
        validate_view_regions(cfg)
        top_layers = {layer for _, layer in view_entries["TOP"]}
        side_layers = {layer for _, layer in view_entries["SIDE"]}
        bot_layers = {layer for _, layer in view_entries["BOT"]}

        outline_candidates = []
        for layer in top_layers:
            selected = [(entity, entity_layer) for entity, entity_layer in view_entries["TOP"]
                        if entity_layer == layer]
            try:
                parsed = parse_geometry(feature_doc(selected, [layer]), [layer], cfg["tol"])
            except (ValueError, IndexError):
                continue
            outline_candidates.append((parsed.outline.area, layer, parsed.outline))
        outline_candidates.sort(reverse=True, key=lambda item: item[0])
        if (not outline_candidates or (len(outline_candidates) > 1
                and outline_candidates[0][0] <= outline_candidates[1][0] * 1.03)):
            return None
        _, outline_layer, top_outline = outline_candidates[0]

        def matching_layers(layers, names):
            return [layer for layer in layers if any(name in layer.upper() for name in names)]

        tape_layers = matching_layers(top_layers, ("TAPE", "테이프"))
        component_layers = matching_layers(bot_layers, ("COMPONENT", "부품"))
        profile_layers = matching_layers(side_layers, ("PROFILE", "단차", "깊이", "프로파일"))
        if len(tape_layers) != 1 or len(component_layers) != 1:
            return None
        profile_layer = (profile_layers[0] if len(profile_layers) == 1 else
                         outline_layer if outline_layer in side_layers else None)
        if not profile_layer:
            return None

        hole_candidates = []
        for layer in top_layers & bot_layers:
            if not any(name in layer.upper() for name in ("HOLE", "홀")):
                continue
            circles = {}
            for view in ("TOP", "BOT"):
                circles[view] = [(entity.dxf.center.x, entity.dxf.center.y, entity.dxf.radius)
                                 for entity, entity_layer in view_entries[view]
                                 if entity_layer == layer and entity.dxftype() == "CIRCLE"]
            if (len(circles["TOP"]) == len(circles["BOT"]) == 2
                    and all(top_outline.buffer(cfg["tol"]).covers(Point(x, y).buffer(r))
                            for x, y, r in circles["TOP"])):
                top_pair = sorted(circles["TOP"], key=lambda circle: circle[2], reverse=True)
                bot_pair = sorted(circles["BOT"], key=lambda circle: circle[2], reverse=True)
                if (abs(top_pair[0][2] - bot_pair[0][2]) <= 0.005
                        and abs(top_pair[1][2] - bot_pair[1][2]) <= 0.005
                        and abs(top_pair[0][2] - top_pair[1][2]) > 0.01):
                    hole_candidates.append((layer, top_pair, bot_pair))
        if len(hole_candidates) != 1:
            return None
        hole_layer, top_pair, bot_pair = hole_candidates[0]
        top_anchor, top_other = top_pair
        bot_anchor, bot_other = bot_pair
        top_vector = (top_other[0] - top_anchor[0], top_other[1] - top_anchor[1])
        bot_vector = (bot_other[0] - bot_anchor[0], bot_other[1] - bot_anchor[1])
        if abs(math.dist(top_vector, (0, 0)) - math.dist(bot_vector, (0, 0))) > 0.01:
            return None
        transforms = []
        for mirror_x in (False, True):
            for mirror_y in (False,):
                for rotation in (0, 90, 180, 270):
                    x = -bot_vector[0] if mirror_x else bot_vector[0]
                    y = -bot_vector[1] if mirror_y else bot_vector[1]
                    radians = math.radians(rotation)
                    transformed = (x * math.cos(radians) - y * math.sin(radians),
                                   x * math.sin(radians) + y * math.cos(radians))
                    error = math.dist(transformed, top_vector)
                    if error <= 0.01:
                        transforms.append((error, mirror_x, mirror_y, rotation))
        if len(transforms) != 1:
            return None
        _, mirror_x, mirror_y, rotation = transforms[0]

        profile_entries = [(entity, layer) for entity, layer in view_entries["SIDE"]
                           if layer == profile_layer and entity.dxftype() in LINE_TYPES]
        segments = []
        profile_points = []
        for entity, _ in profile_entries:
            for path in ezpath.make_path(entity).sub_paths():
                points = [(point.x, point.y) for point in path.flattening(0.005)]
                if len(points) >= 2:
                    profile_points.extend(points)
                    segments.extend(zip(points, points[1:]))
        if not profile_points:
            return None
        sx0, sy0 = min(x for x, _ in profile_points), min(y for _, y in profile_points)
        sx1, sy1 = max(x for x, _ in profile_points), max(y for _, y in profile_points)
        side_axis = "X" if sx1 - sx0 >= sy1 - sy0 else "Y"
        side_span = sx1 - sx0 if side_axis == "X" else sy1 - sy0
        bx0, by0, bx1, by1 = top_outline.bounds
        target_spans = {"X": bx1 - bx0, "Y": by1 - by0}
        ranked_axes = sorted((abs(span - side_span) / max(span, side_span), axis)
                             for axis, span in target_spans.items())
        if ranked_axes[0][0] > 0.02 or (len(ranked_axes) > 1 and ranked_axes[1][0] - ranked_axes[0][0] < 0.01):
            return None
        target_axis = ranked_axes[0][1]

        depth_index = 1 if side_axis == "X" else 0
        projection_index = 0 if side_axis == "X" else 1
        extreme_lengths = {}
        for a, b in segments:
            if abs(a[depth_index] - b[depth_index]) <= 0.001:
                depth = round((a[depth_index] + b[depth_index]) / 2, 5)
                extreme_lengths[depth] = extreme_lengths.get(depth, 0.0) + abs(a[projection_index] - b[projection_index])
        depth_values = [round(point[depth_index], 5) for segment in segments for point in segment]
        low, high = min(depth_values), max(depth_values)
        if (low not in extreme_lengths or high not in extreme_lengths
                or max(extreme_lengths[low], extreme_lengths[high]) < min(extreme_lengths[low], extreme_lengths[high]) * 1.1):
            return None
        datum = high if extreme_lengths[high] > extreme_lengths[low] else low
        surface = "min" if datum == high else "max"
        depth_sign = -1 if datum == high else 1
        side_origin = sy0 if side_axis == "Y" else sx0
        side_top_origin = by0 if target_axis == "Y" else bx0
    except (ValueError, IndexError, ZeroDivisionError):
        return None

    cfg.update(top_outline_layers=[outline_layer], top_hole_layers=[hole_layer], tape_layers=tape_layers,
               side_profile_layers=[profile_layer], bot_component_layers=component_layers,
               side_profile_axis=side_axis, side_axis=target_axis, side_reverse=False,
               side_surface=surface, side_depth_sign=depth_sign, side_origin=side_origin,
               side_top_origin=side_top_origin, side_datum=datum,
               bot_mirror_x=mirror_x, bot_mirror_y=mirror_y, bot_rotation=rotation,
               bot_origin=(bot_anchor[0], bot_anchor[1]), bot_target_origin=(top_anchor[0], top_anchor[1]))
    return cfg


@st.cache_data(max_entries=8, show_spinner=False)
def detect_multiview_dxf(product_dxf: bytes):
    return detect_multiview(read_doc(product_dxf))


def feature_doc(entries, layers):
    doc = ezdxf.new()
    for entity, layer in entries:
        if layer in layers:
            copied = entity.copy()
            copied.dxf.layer = layer
            doc.modelspace().add_entity(copied)
    return doc


def doc_bytes(doc):
    stream = io.StringIO()
    doc.write(stream)
    return stream.getvalue().encode("utf-8")


def polygon_features(entries, layers, tol):
    if not layers:
        return Polygon()
    doc = feature_doc(entries, layers)
    if not len(doc.modelspace()):
        raise ValueError(f"선택한 뷰에서 기능 레이어를 찾지 못했습니다: {', '.join(layers)}")
    parsed = parse_geometry(doc, layers, tol)
    polygons = parsed.outlines + [Point(x, y).buffer(r, quad_segs=64) for x, y, r, _ in parsed.circles]
    return unary_union(polygons)


def prepare_thru_geometry(geometry, tol=0.001):
    """1.5mm 미만 홈/간격을 먼저 잇고 피난 영역을 R0.5로 다듬는다."""
    if geometry.is_empty:
        return geometry
    polygons = [geometry] if geometry.geom_type == "Polygon" else [
        part for part in getattr(geometry, "geoms", []) if part.geom_type == "Polygon"]
    geometry = unary_union(polygons)
    merge_radius = max(0.0, (THRU_MERGE_GAP - max(0.0, tol)) / 2)
    if merge_radius:
        geometry = geometry.buffer(merge_radius, quad_segs=16, join_style=1)
        geometry = geometry.buffer(-merge_radius, quad_segs=16, join_style=1)
    polygons = [geometry] if geometry.geom_type == "Polygon" else [
        part for part in getattr(geometry, "geoms", []) if part.geom_type == "Polygon"]
    rounded = []
    for polygon in polygons:
        core = polygon.buffer(-THRU_TOOL_RADIUS, quad_segs=16, join_style=2)
        rounded.append(core.buffer(THRU_TOOL_RADIUS, quad_segs=16, join_style=1)
                       if not core.is_empty else polygon)
    return unary_union(rounded)


def recommend_tools(geometry, tol=0.001):
    """원형 공구의 침식/팽창으로 접근 가능성을 비교한다. NC 경로 계산은 아니다."""
    reachable = geometry.buffer(-1.0, quad_segs=32).buffer(1.0, quad_segs=32)
    tools = [2.0] if not reachable.is_empty else []
    rest = geometry.difference(reachable.buffer(tol))
    warning = ""
    if rest.area > tol * tol:
        reachable_small = geometry.buffer(-0.5, quad_segs=32).buffer(0.5, quad_segs=32)
        if reachable_small.difference(reachable.buffer(tol)).area > tol * tol:
            tools.append(1.0)
        if geometry.difference(reachable_small.buffer(tol)).area > tol * tol:
            warning = "Ø1로도 남는 좁은 영역/내각이 있습니다. 형상·공구 접근성을 검토하세요."
    return tools, warning




def add_preview_polygon(ax, polygon, color, alpha, label=None):
    """Draw a polygon with its interior rings left unfilled in the preview."""
    polygon = orient(polygon, sign=1.0)
    vertices, codes = [], []
    for ring in [polygon.exterior, *polygon.interiors]:
        coords = list(ring.coords)
        vertices.extend(coords)
        codes.extend([MatplotlibPath.MOVETO]
                     + [MatplotlibPath.LINETO] * (len(coords) - 2)
                     + [MatplotlibPath.CLOSEPOLY])
    patch = PathPatch(MatplotlibPath(vertices, codes), facecolor=color, edgecolor="none",
                      alpha=alpha, label=label)
    ax.add_patch(patch)
    return patch


def create_machining_plan(product_dxf: bytes, cfg: dict):
    relief_min, relief_basis = relief_offset_minimum()
    bound_text = (f"BOT 피난홀 옵셋은 {relief_min:g}~10mm 범위의 유한한 값이어야 합니다"
                  f" ({relief_basis} 피난홀 크기 최소 {relief_min:g}mm).")
    try:
        bot_relief_offset = float(cfg.get("bot_relief_offset", relief_min))
    except (TypeError, ValueError):
        raise ValueError(bound_text) from None
    if not math.isfinite(bot_relief_offset) or not relief_min <= bot_relief_offset <= 10:
        raise ValueError(bound_text)
    cfg = dict(cfg, bot_relief_offset=bot_relief_offset)
    if not 1.5 <= cfg["pin_height"] <= 2.0:
        raise ValueError("핀 높이는 1.5~2.0mm 범위에서 선택하세요.")
    if (cfg["side_axis"] not in ("X", "Y") or cfg.get("side_profile_axis", "X") not in ("X", "Y")
            or cfg["side_depth_sign"] not in (-1, 1)):
        raise ValueError("SIDE 투영축과 깊이 증가 방향을 확인하세요.")
    if cfg["side_surface"] not in ("max", "min"):
        raise ValueError("SIDE 높이 경계는 위쪽 또는 아래쪽으로 지정하세요.")
    tol = cfg.get("tol", 0.001)
    doc = read_doc(product_dxf)
    if cfg.get("view_mode", "named") == "regions":
        validate_view_regions(cfg)
        top_entries, side_entries, bot_entries = [region_entities(doc, cfg[f"{name}_region"])
                                                  for name in ("top", "side", "bot")]
        for view, entries in zip(("TOP", "SIDE", "BOT"), (top_entries, side_entries, bot_entries)):
            if not entries:
                raise ValueError(f"{view} 영역에 완전히 포함된 형상이 없습니다. 영역 범위를 확인하세요.")
    elif cfg.get("view_mode", "named") == "named":
        top_entries, side_entries, bot_entries = [source_entities(doc, cfg[f"{name}_source"])
                                                 for name in ("top", "side", "bot")]
        if len({cfg[f"{name}_source"] for name in ("top", "side", "bot")}) != 3:
            raise ValueError("TOP/SIDE/BOT는 서로 다른 이름의 소스로 지정하세요.")
    else:
        raise ValueError("뷰 구분 방식은 이름 또는 수동 영역으로 지정하세요.")
    for key in ("top_outline_layers", "side_profile_layers", "bot_component_layers"):
        if not cfg[key]:
            raise ValueError("제품 외곽·SIDE 프로파일·BOT 부품 기능 레이어를 지정하세요.")
    for view, entries, keys in (
        ("TOP", top_entries, ("top_outline_layers", "top_hole_layers", "tape_layers")),
        ("SIDE", side_entries, ("side_profile_layers",)),
        ("BOT", bot_entries, ("bot_component_layers",)),
    ):
        available = {layer for _, layer in entries}
        for key in keys:
            missing = sorted(set(cfg[key]) - available)
            if missing:
                raise ValueError(f"{view} 선택 뷰 소스/영역에 기능 레이어 {', '.join(missing)}가 없습니다. 소스/영역/역할 레이어를 확인하세요.")
    top_doc = feature_doc(top_entries, cfg["top_outline_layers"] + cfg["top_hole_layers"])
    top = parse_geometry(top_doc, cfg["top_outline_layers"], tol,
                         outline_index=cfg.get("top_outline_index", 0), circle_layers=cfg["top_hole_layers"])
    tape = polygon_features(top_entries, cfg["tape_layers"], tol).intersection(top.outline)
    dp_outline = top.outline.buffer(0.03, join_style=2)
    dp = dp_outline.difference(tape)
    warnings = ["SIDE 단면을 선택 축의 전폭 띠로 투영했습니다. 단면만으로 알 수 없는 평면 위치는 검토해야 합니다."]
    side_doc = feature_doc(side_entries, cfg["side_profile_layers"])
    profile_lines = []
    for entity in side_doc.modelspace():
        if entity.dxftype() in LINE_TYPES:
            for path in ezpath.make_path(entity).sub_paths():
                points = [(p.x, p.y) if cfg.get("side_profile_axis", "X") == "X" else (p.y, p.x)
                          for p in path.flattening(0.005)]
                if len(points) < 2:
                    continue
                if any(abs(a[0] - b[0]) > tol and abs(a[1] - b[1]) > tol
                       for a, b in zip(points, points[1:])):
                    raise ValueError("SIDE 경사/곡선은 고정 깊이 단차로 자동 해석할 수 없습니다. 수평/수직 단차 프로파일을 지정하세요.")
                profile_lines.append(LineString(points))
    if not profile_lines:
        raise ValueError("SIDE 기능 레이어에 단차 프로파일 선이 없습니다.")
    profile = unary_union(profile_lines)
    breaks = sorted({round(x, 6) for line in profile_lines for x, _ in line.coords})
    sx0, sy0, sx1, sy1 = profile.bounds
    bx0, by0, bx1, by1 = dp.bounds
    if dp.is_empty:
        raise ValueError("TAPE를 제외하면 DP 영역이 남지 않습니다.")
    axis_low, axis_high = (bx0, bx1) if cfg["side_axis"] == "X" else (by0, by1)
    by_depth = {}
    display_by_depth = {}
    for left, right in zip(breaks, breaks[1:]):
        if right - left <= tol:
            continue
        cut = profile.intersection(LineString([((left + right) / 2, sy0 - 1), ((left + right) / 2, sy1 + 1)]))
        ys = shapely.get_coordinates(cut)[:, 1]
        if not len(ys):
            continue
        level = max(ys) if cfg["side_surface"] == "max" else min(ys)
        depth = round(cfg["side_depth_sign"] * (level - cfg["side_datum"]), 6)
        if depth < -tol:
            raise ValueError("SIDE 깊이가 음수입니다. 깊이 기준면/증가 방향/높이 경계를 다시 지정하세요.")
        if depth <= tol:
            continue
        direction = -1 if cfg["side_reverse"] else 1
        start, end = sorted(cfg["side_top_origin"] + direction * (x - cfg["side_origin"])
                            for x in (left, right))
        if abs(start - axis_low) <= 0.03 + tol:
            start = axis_low
        if abs(end - axis_high) <= 0.03 + tol:
            end = axis_high
        stripe = box(start, by0, end, by1) if cfg["side_axis"] == "X" else box(bx0, start, bx1, end)
        region = dp.intersection(stripe)
        if not region.is_empty:
            by_depth.setdefault(depth, []).append(region)
            display_region = dp_outline.intersection(stripe)
            if not display_region.is_empty:
                display_by_depth.setdefault(depth, []).append(display_region)
    if by_depth:
        maximum_depth = max(by_depth)
        full_dp = unary_union([region for regions in by_depth.values() for region in regions])
        display_dp = unary_union([region for regions in display_by_depth.values() for region in regions])
        operations = [dict(kind="DP", depth=maximum_depth, geometry=full_dp)]
    else:
        display_dp = Polygon()
        operations = []
    if not operations:
        raise ValueError("TOP에 투영되는 양의 SIDE 깊이가 없습니다. 축/기준점/깊이 기준면을 확인하세요.")
    covered = unary_union([op["geometry"] for op in operations])
    if dp.difference(covered.buffer(tol)).area > tol * tol:
        warnings.append("SIDE 깊이가 배정되지 않은 TOP 영역이 있습니다. 해당 부분은 DP 출력에서 제외했으므로 정합을 검토하세요.")
    relief = polygon_features(bot_entries, cfg["bot_component_layers"], tol)
    relief = affinity.translate(relief, -cfg["bot_origin"][0], -cfg["bot_origin"][1])
    relief = affinity.scale(relief, xfact=-1 if cfg["bot_mirror_x"] else 1,
                            yfact=-1 if cfg["bot_mirror_y"] else 1, origin=(0, 0))
    relief = affinity.rotate(relief, cfg["bot_rotation"], origin=(0, 0))
    relief = affinity.translate(relief, *cfg["bot_target_origin"])
    relief = relief.buffer(bot_relief_offset, join_style=2)
    raw_relief = relief
    relief = prepare_thru_geometry(relief, tol)
    pins = [dict(x=x, y=y, hole_diameter=2 * r, diameter=2 * r - 0.1, height=cfg["pin_height"])
            for x, y, r, layer in top.circles
            if layer in cfg["top_hole_layers"] and top.outline.buffer(tol).covers(Point(x, y).buffer(r, quad_segs=64))
            and cfg.get("hole_dmin", 0.5) <= 2 * r <= cfg.get("hole_dmax", 30.0)]
    if not pins:
        warnings.append("TOP 내부의 지정 홀 레이어에서 가이드핀용 원을 찾지 못했습니다.")
    if any(pin["diameter"] <= 0 for pin in pins):
        raise ValueError("홀 직경이 핀 감소량 0.1mm보다 작습니다.")
    pin_clearance_parts = [Point(pin["x"], pin["y"]).buffer(
        (pin["diameter"] + PIN_CLEARANCE_DIAMETER) / 2, quad_segs=64) for pin in pins]
    for pin, clearance in zip(pins, pin_clearance_parts):
        if not top.outline.buffer(tol).covers(clearance):
            raise ValueError(f"GUIDE PIN 위치의 Ø{pin['diameter'] + PIN_CLEARANCE_DIAMETER:.3f}mm 여유 관통공이 "
                             "TOP 제품 외곽을 벗어납니다. 핀 위치/제품 외곽을 확인하세요.")
    pin_clearance = unary_union(pin_clearance_parts) if pin_clearance_parts else Polygon()
    for operation in operations:
        if operation["kind"] == "DP" and not pin_clearance.is_empty:
            operation["geometry"] = operation["geometry"].difference(pin_clearance)
    thru_geometry = unary_union([geometry for geometry in (relief, pin_clearance) if not geometry.is_empty])
    if not thru_geometry.is_empty:
        operations.append(dict(kind="THRU", depth=None, geometry=thru_geometry))
    operations = [operation for operation in operations if not operation["geometry"].is_empty]
    for operation in operations:
        operation["tools"], warning = recommend_tools(operation["geometry"], tol)
        if warning:
            warnings.append(f"{operation['kind']}: {warning}")
    if any(relief.intersects(Point(pin["x"], pin["y"]).buffer(pin["diameter"] / 2)) for pin in pins):
        warnings.append("가이드핀 위치가 THRU 영역과 겹칩니다. 핀 지지/고정 구조를 검토해야 합니다.")
    if raw_relief.is_empty and pin_clearance_parts:
        warnings.append("BOT 부품 THRU는 없고 GUIDE PIN 여유 관통공만 생성했습니다.")
    if doc.header.get("$INSUNITS", 0) not in (0, 4):
        warnings.append("제품 도면 단위가 mm가 아닐 수 있습니다. 이 가공도는 입력 좌표를 mm로 사용하므로 단위를 확인하세요.")
    nesting_doc = ezdxf.new()
    nesting_doc.header["$INSUNITS"] = 4
    nesting_doc.modelspace().add_lwpolyline(list(top.outline.exterior.coords)[:-1], close=True,
                                           dxfattribs={"layer": "PRODUCT_OUTLINE"})
    for pin in pins:
        nesting_doc.modelspace().add_circle((pin["x"], pin["y"]), pin["hole_diameter"] / 2,
                                           dxfattribs={"layer": "PRODUCT_HOLES"})
    return dict(top=top, operations=operations, display_dp=display_dp, component_thru=relief,
                pins=pins, pin_clearance=pin_clearance, tape=tape,
                warnings=list(dict.fromkeys(warnings)), nesting_dxf=doc_bytes(nesting_doc), cfg=cfg)


def jig_plate_indices(outlines):
    candidates = []
    for index, outline in enumerate(outlines):
        x0, y0, x1, y1 = outline.bounds
        if min(x1 - x0, y1 - y0) >= 10.0:
            candidates.append((index, outline))
    if len(candidates) < 2:
        raise ValueError("JIG 외곽 레이어에서 좌측 상판과 우측 하판 외곽을 모두 찾지 못했습니다.")
    main_plates = sorted(candidates, key=lambda item: item[1].area, reverse=True)[:2]
    left_index, left_plate = min(main_plates, key=lambda item: item[1].bounds[0])
    right_index, right_plate = max(main_plates, key=lambda item: item[1].bounds[0])
    left_center_x = (left_plate.bounds[0] + left_plate.bounds[2]) / 2
    right_center_x = (right_plate.bounds[0] + right_plate.bounds[2]) / 2
    if left_index == right_index or right_center_x <= left_center_x:
        raise ValueError("JIG 외곽 후보에서 왼쪽 상판과 오른쪽 하판을 구분할 수 없습니다.")
    return left_index, right_index


def jig_side_view_candidates(doc, outlines, top_index, bottom_index, tol=0.2):
    """선택된 좌우 주판과 치수가 대응하는 얇은 정면/측면 투영 후보를 찾는다."""
    plates = (outlines[top_index], outlines[bottom_index])
    candidates = []

    def add_candidate(geometry):
        if geometry.is_empty or geometry.area <= 0:
            return
        key = tuple(round(value, 3) for value in geometry.bounds)
        if all(tuple(round(value, 3) for value in item.bounds) != key for item in candidates):
            candidates.append(geometry)

    for index, outline in enumerate(outlines):
        if index in (top_index, bottom_index):
            continue
        x0, y0, x1, y1 = outline.bounds
        width, height = x1 - x0, y1 - y0
        if min(width, height) > 10.0:
            continue
        for plate in plates:
            px0, py0, px1, py1 = plate.bounds
            plate_width, plate_height = px1 - px0, py1 - py0
            x_overlap = max(0.0, min(x1, px1) - max(x0, px0))
            y_overlap = max(0.0, min(y1, py1) - max(y0, py0))
            horizontal_match = (abs(width - plate_width) <= max(tol, plate_width * 0.005)
                                and x_overlap >= plate_width * 0.95
                                and (y1 <= py0 or py1 <= y0)
                                and min(abs(py0 - y1), abs(y0 - py1)) <= 60.0)
            vertical_match = (abs(height - plate_height) <= max(tol, plate_height * 0.005)
                              and y_overlap >= plate_height * 0.95
                              and (x1 <= px0 or px1 <= x0)
                              and min(abs(px0 - x1), abs(x0 - px1)) <= 60.0)
            if horizontal_match or vertical_match:
                add_candidate(outline)
                break

    vertical_lines = []
    for entity in iter_entities(doc.modelspace()):
        if entity.dxftype() != "LINE":
            continue
        start, end = entity.dxf.start, entity.dxf.end
        if abs(start.x - end.x) > tol or abs(start.y - end.y) < 20.0:
            continue
        vertical_lines.append((min(start.x, end.x), min(start.y, end.y),
                               max(start.y, end.y)))
    for plate in plates:
        px0, py0, px1, py1 = plate.bounds
        plate_height = py1 - py0
        matching = []
        for x, y0, y1 in vertical_lines:
            if abs(y0 - py0) > tol or abs(y1 - py1) > tol:
                continue
            gap = x - px1 if x >= px1 else px0 - x if x <= px0 else -1.0
            if 0.0 < gap <= 60.0:
                matching.append(x)
        for index, x0 in enumerate(sorted(set(matching))):
            for x1 in sorted(set(matching))[index + 1:]:
                if 1.0 <= x1 - x0 <= 10.0:
                    add_candidate(box(x0, py0, x1, py1))

    return sorted(candidates, key=lambda geometry: geometry.bounds)


def jig_side_view_label(index, outlines):
    x0, y0, x1, y1 = outlines[index].bounds
    width, height = x1 - x0, y1 - y0
    view_name = "측면도" if height > width else "정면도"
    return f"{view_name} 후보 {index + 1}: {width:.3f} × {height:.3f} mm (X {x0:.3f}, Y {y0:.3f})"


def build_machining_dxf(base_dxf, plan, placements, c0, base_outline=None, pin_outline=None,
                        reference_dxf=None, reference_gap=20.0, side_view_outlines=None):
    if not math.isfinite(reference_gap) or reference_gap < 0:
        raise ValueError("제품 원본 참조도 간격은 0mm 이상의 유한한 값이어야 합니다.")
    if plan["pins"] and pin_outline is None:
        raise ValueError("GUIDE PIN을 배치할 JIG 오른쪽 하판 외곽을 선택하세요.")
    doc = read_doc(base_dxf)
    msp = doc.modelspace()
    view_base = base_outline if base_outline is not None else parse_geometry(doc, []).outline
    tol = plan.get("cfg", {}).get("tol", 0.001)
    if pin_outline is not None:
        top_center = ((view_base.bounds[0] + view_base.bounds[2]) / 2,
                      (view_base.bounds[1] + view_base.bounds[3]) / 2)
        bottom_center = ((pin_outline.bounds[0] + pin_outline.bounds[2]) / 2,
                         (pin_outline.bounds[1] + pin_outline.bounds[3]) / 2)
        pin_translation = (bottom_center[0] - top_center[0], bottom_center[1] - top_center[1])
        if pin_translation[0] <= 0:
            raise ValueError("GUIDE PIN 대상 하판은 JIG 왼쪽 상판의 오른쪽에 있어야 합니다.")
        selected_base = unary_union([view_base, pin_outline,
                                     *(side_view_outlines or [])]).buffer(tol)
    else:
        pin_translation = (0.0, 0.0)
        selected_base = unary_union([view_base, *(side_view_outlines or [])]).buffer(tol)

    def source_covered(entity):
        try:
            kind = entity.dxftype()
            if kind == "CIRCLE":
                center = entity.dxf.center
                geometry = Point(center.x, center.y).buffer(entity.dxf.radius, quad_segs=64)
            elif kind in LINE_TYPES:
                paths = []
                for path in ezpath.make_path(entity).sub_paths():
                    points = [(point.x, point.y) for point in path.flattening(0.005)]
                    if len(points) >= 2:
                        paths.append(LineString(points))
                geometry = unary_union(paths)
            else:
                bounds = ezbbox.extents([entity], fast=True)
                if not bounds.has_data:
                    raise ValueError("베이스 요소의 포함 범위를 확인할 수 없습니다.")
                lo, hi = bounds.extmin, bounds.extmax
                geometry = box(lo.x, lo.y, hi.x, hi.y) if lo.x != hi.x and lo.y != hi.y else LineString([(lo.x, lo.y), (hi.x, hi.y)])
            return not geometry.is_empty and selected_base.covers(geometry)
        except Exception:
            return False

    def source_children(entity, inherited_layer="0", depth=0):
        layer = entity.dxf.layer if entity.dxf.layer != "0" else inherited_layer
        if entity.dxftype() != "INSERT":
            yield entity, layer
            return
        if depth >= 8:
            raise ValueError("베이스 블록 중첩이 8단계를 넘습니다. 블록을 단순화하세요.")
        for insert in entity.multi_insert() if entity.mcount > 1 else [entity]:
            for child in [*insert.attribs, *insert.virtual_entities()]:
                yield from source_children(child, layer, depth + 1)

    excluded = 0
    for entity in list(msp):
        children = list(source_children(entity))
        covered = [source_covered(child) for child, _ in children]
        if children and all(covered):
            continue
        if entity.dxftype() == "INSERT":
            for (child, layer), keep in zip(children, covered):
                if keep:
                    if child.dxftype() == "ATTRIB":
                        copied = msp.add_text(child.dxf.text)
                        for name, value in child.dxf.all_existing_dxf_attribs().items():
                            if name not in ("handle", "owner") and copied.dxf.is_supported(name):
                                setattr(copied.dxf, name, value)
                    else:
                        copied = child.copy()
                        msp.add_entity(copied)
                    copied.dxf.layer = layer
        excluded += sum(not keep for keep in covered) if children else 1
        msp.delete_entity(entity)
    if excluded:
        plan["warnings"].append(f"선택 베이스 밖이거나 포함 범위를 확인할 수 없는 원본 요소 {excluded}개를 가공도에서 제외했습니다.")

    reference_bounds = None
    if reference_dxf:
        reference_doc = read_doc(reference_dxf)
        cfg = plan.get("cfg", {})
        if cfg.get("view_mode") == "regions":
            view_bounds = [view_rectangle(cfg.get(f"{view}_region")).bounds
                           for view in ("top", "side", "bot")]
        elif cfg.get("view_mode") == "named":
            view_entries = [source_entities(reference_doc, cfg[f"{view}_source"])
                            for view in ("top", "side", "bot")]
            view_bounds = []
            for entries in view_entries:
                extents = ezbbox.extents([entity for entity, _ in entries], fast=False)
                if extents.has_data:
                    lo, hi = extents.extmin, extents.extmax
                    view_bounds.append((lo.x, lo.y, hi.x, hi.y))
        else:
            view_bounds = []
        if len(view_bounds) == 3:
            xmin = min(bounds[0] for bounds in view_bounds)
            ymin = min(bounds[1] for bounds in view_bounds)
            xmax = max(bounds[2] for bounds in view_bounds)
            ymax = max(bounds[3] for bounds in view_bounds)
            margin = max(5.0, min(xmax - xmin, ymax - ymin))
            selection_bounds = (xmin - margin, ymin - margin, xmax + margin, ymax + margin)
            selected = []
            annotations = {"TEXT", "MTEXT", "DIMENSION", "LEADER", "MLEADER"}
            for entity in reference_doc.modelspace():
                extents = ezbbox.extents([entity], fast=False)
                if not extents.has_data:
                    continue
                lo, hi = extents.extmin, extents.extmax
                inside_view = any(bounds[0] <= lo.x and bounds[1] <= lo.y
                                  and hi.x <= bounds[2] and hi.y <= bounds[3]
                                  for bounds in view_bounds)
                nearby_annotation = (entity.dxftype() in annotations
                                     and hi.x >= selection_bounds[0] and lo.x <= selection_bounds[2]
                                     and hi.y >= selection_bounds[1] and lo.y <= selection_bounds[3])
                if inside_view or nearby_annotation:
                    selected.append(entity)
            if selected:
                existing_handles = {entity.dxf.handle for entity in msp}
                importer = Importer(reference_doc, doc)
                tape_layer_names = {layer.casefold() for layer in cfg.get("tape_layers", [])}
                tape_inserts = [entity for entity in selected
                                if entity.dxftype() == "INSERT"
                                and entity.dxf.layer.casefold() in tape_layer_names]
                regular_entities = [entity for entity in selected
                                    if entity.dxftype() != "INSERT"
                                    or entity.dxf.layer.casefold() not in tape_layer_names]
                decomposed = list(disassemble.recursive_decompose(regular_entities))
                for insert in tape_inserts:
                    for child in disassemble.recursive_decompose([insert]):
                        child = child.copy()
                        if child.dxf.layer == "0":
                            child.dxf.layer = insert.dxf.layer
                        decomposed.append(child)
                importer.import_entities(decomposed, msp)
                importer.finalize()
                imported = [entity for entity in msp if entity.dxf.handle not in existing_handles]
                reference_tape = [entity for entity in imported
                                  if entity.dxf.layer.casefold() in tape_layer_names | {"product_reference_tape"}]
                if reference_tape:
                    if "PRODUCT_REFERENCE_TAPE" not in doc.layers:
                        doc.layers.add("PRODUCT_REFERENCE_TAPE", color=8)
                    doc.layers.get("PRODUCT_REFERENCE_TAPE").off()
                    for entity in reference_tape:
                        entity.dxf.layer = "PRODUCT_REFERENCE_TAPE"
                ref_extents = ezbbox.extents(imported, fast=False)
                if ref_extents.has_data:
                    lo, hi = ref_extents.extmin, ref_extents.extmax
                    lo_x, lo_y, hi_x, hi_y = lo.x, lo.y, hi.x, hi.y
                    dx = view_base.bounds[0] - reference_gap - hi_x
                    dy = (view_base.bounds[1] + view_base.bounds[3] - lo_y - hi_y) / 2
                    transform = Matrix44.translate(dx, dy, 0)
                    failed_transforms = 0
                    for entity in imported:
                        try:
                            entity.transform(transform)
                        except Exception:
                            msp.delete_entity(entity)
                            failed_transforms += 1
                    if failed_transforms:
                        plan["warnings"].append(f"원본 제품 참조도의 요소 {failed_transforms}개를 이동하지 못해 제외했습니다.")
                    if len(imported) < len(decomposed):
                        plan["warnings"].append(f"지원하지 않는 원본 참조도 요소 {len(decomposed) - len(imported)}개를 제외했습니다.")
                    reference_bounds = (lo_x + dx, lo_y + dy, hi_x + dx, hi_y + dy)
                    if failed_transforms == len(imported):
                        reference_bounds = None
                else:
                    for entity in imported:
                        msp.delete_entity(entity)
                    reference_bounds = None
            else:
                reference_bounds = None
        if reference_bounds is None:
            plan["warnings"].append("원본 제품 TOP/SIDE/BOT 참조 형상을 찾지 못해 참조도를 생략했습니다.")

    def transformed(geometry, placement):
        geometry = affinity.translate(geometry, -c0[0], -c0[1])
        return affinity.translate(affinity.rotate(geometry, placement["rot"], origin=(0, 0)),
                                  placement["x"], placement["y"])

    def draw(geometry, layer, color, include_interiors=True):
        if layer not in doc.layers:
            doc.layers.add(layer, color=color)
        for polygon in (list(geometry.geoms) if hasattr(geometry, "geoms") else [geometry]):
            if polygon.geom_type != "Polygon" or polygon.is_empty:
                continue
            msp.add_lwpolyline(list(polygon.exterior.coords)[:-1], close=True,
                               dxfattribs={"layer": layer})
            if include_interiors:
                for ring in polygon.interiors:
                    msp.add_lwpolyline(list(ring.coords)[:-1], close=True,
                                       dxfattribs={"layer": layer})

    global_tape = unary_union([transformed(plan["tape"], placement) for placement in placements])

    def machining_region(operation, placement):
        geometry = transformed(operation["geometry"], placement)
        return geometry.difference(global_tape) if operation["kind"] == "DP" else geometry

    component_thru = plan.get("component_thru")
    if component_thru is None:
        thru_geometry = unary_union([operation["geometry"] for operation in plan["operations"]
                                     if operation["kind"] == "THRU"])
        pin_clearance = plan.get("pin_clearance", Polygon())
        component_thru = thru_geometry.difference(pin_clearance)
    through = [(placement["no"], transformed(component_thru, placement))
               for placement in placements if not component_thru.is_empty]
    if through:
        tree = shapely.STRtree([geometry for _, geometry in through])
        for placement in placements:
            for pin in plan["pins"]:
                x, y = tf((pin["x"], pin["y"]), c0, placement["rot"], placement["x"], placement["y"])
                for index in tree.query(Point(x, y).buffer(pin["diameter"] / 2), predicate="intersects"):
                    through_no = through[index][0]
                    relation = "다른 제품" if through_no != placement["no"] else "동일 제품"
                    warning = (f"{relation} {through_no}의 THRU와 제품 {placement['no']}의 가이드핀이 겹칩니다. "
                               "핀 지지/고정 구조를 검토해야 합니다.")
                    if warning not in plan["warnings"]:
                        plan["warnings"].append(warning)
    pin_obstacles = []
    for placement in placements:
        for pin in plan["pins"]:
            x, y = tf((pin["x"], pin["y"]), c0, placement["rot"], placement["x"], placement["y"])
            x += pin_translation[0]
            y += pin_translation[1]
            pin_obstacles.append(Point(x, y).buffer(pin["diameter"] / 2 + 0.2))
    annotation_bounds = []
    hole_callout_bounds = []
    for placement in placements:
        for operation in plan["operations"]:
            original = transformed(operation["geometry"], placement)
            geometry = machining_region(operation, placement)
            if operation["kind"] == "DP" and original.difference(geometry).area > 1e-6:
                warning = f"제품 {placement['no']} DP에서 배열된 TAPE 금지영역을 차감했습니다."
                if warning not in plan["warnings"]:
                    plan["warnings"].append(warning)
            if geometry.is_empty:
                continue
            if base_outline is not None and not base_outline.buffer(0.001).covers(geometry):
                raise ValueError("가공 영역이 베이스 외곽을 벗어납니다. 배치/THRU 정합을 검토하세요.")
            depth = f"Z{operation['depth']:g}" if operation["depth"] is not None else "FULL"
            recommended, warning = recommend_tools(geometry)
            if warning and warning not in plan["warnings"]:
                plan["warnings"].append(warning)
            tools = "_".join(f"D{tool:g}" for tool in recommended) or "REVIEW"
            layer = f"{operation['kind']}_{depth}_{tools}"
            if operation["kind"] == "DP":
                # DP represents the whole product outline. THRU is a separate,
                # overlapping operation and must not trim the DP display shape.
                display_geometry = transformed(plan.get("display_dp", operation["geometry"]), placement)
                display_parts = list(display_geometry.geoms) if hasattr(display_geometry, "geoms") else [display_geometry]
                if display_parts:
                    display_geometry = display_parts[0]
                    for part in display_parts[1:]:
                        part = shapely.snap(part, display_geometry, tol)
                        display_geometry = unary_union([display_geometry, part])
                draw(display_geometry, layer, 2, include_interiors=False)
            else:
                draw(geometry, layer, 1)
    dp_depths = sorted({operation["depth"] for operation in plan["operations"]
                        if operation["kind"] == "DP" and operation["depth"] is not None})
    if dp_depths:
        if "DP_ANNOTATIONS" not in doc.layers:
            doc.layers.add("DP_ANNOTATIONS", color=6)
        depth_text = "제품 DP 깊이: " + " / ".join(f"Z{depth:.3f} mm" for depth in dp_depths)
        annotation_x = reference_bounds[0] if reference_bounds else view_base.bounds[0]
        annotation_y = reference_bounds[1] - 2.0 if reference_bounds else view_base.bounds[1] - 2.0
        annotation_width = min(120.0, reference_bounds[2] - reference_bounds[0]) if reference_bounds else 120.0
        msp.add_mtext(depth_text, dxfattribs={"layer": "DP_ANNOTATIONS", "char_height": 1.5,
                                             "width": max(1.0, annotation_width),
                                             "insert": (annotation_x, annotation_y)})
    for placement in placements:
        for pin in plan["pins"]:
            left_x, left_y = tf((pin["x"], pin["y"]), c0, placement["rot"],
                                placement["x"], placement["y"])
            clearance_diameter = pin["diameter"] + PIN_CLEARANCE_DIAMETER
            if "PIN_CLEARANCE_REF" not in doc.layers:
                doc.layers.add("PIN_CLEARANCE_REF", color=6)
            msp.add_circle((left_x, left_y), clearance_diameter / 2,
                           dxfattribs={"layer": "PIN_CLEARANCE_REF"})
            if "PIN_HOLE_CALLOUTS" not in doc.layers:
                doc.layers.add("PIN_HOLE_CALLOUTS", color=4)
            callout_text = (f"제품 홀 Ø{pin['hole_diameter']:.3f}\\P"
                            f"GUIDE PIN Ø{pin['diameter']:.3f}\\P"
                            f"JIG THRU Ø{clearance_diameter:.3f} mm")
            callout = msp.add_mtext(callout_text, dxfattribs={
                "layer": "PIN_HOLE_CALLOUTS", "char_height": 1.2,
                "width": 42.0, "insert": (view_base.bounds[2] + 2.0, left_y + 1.0)})
            accepted_callout = None
            right_obstacles = sorted(
                [outline for outline in (side_view_outlines or []) +
                 ([pin_outline] if pin_outline is not None else [])
                 if outline.bounds[0] > view_base.bounds[2]],
                key=lambda outline: (outline.bounds[0], outline.bounds[2]))
            callout_positions = []
            cursor_x = view_base.bounds[2]
            for obstacle in right_obstacles:
                gap_start = cursor_x + 2.0
                gap_width = obstacle.bounds[0] - gap_start - 2.0
                if gap_width >= 10.0:
                    callout_positions.append((gap_start, min(42.0, gap_width)))
                cursor_x = max(cursor_x, obstacle.bounds[2])
            callout_positions.append((cursor_x + 2.0, 42.0))
            for callout_x, callout_width in callout_positions:
                callout.dxf.width = callout_width
                for offset_y in (1.0, 4.0, -3.0, 7.0, -6.0, 10.0, -9.0,
                                 13.0, -12.0, 18.0, -17.0, 24.0, -23.0,
                                 32.0, -31.0, 48.0, -47.0, 64.0, -63.0,
                                 80.0, -79.0):
                    callout.dxf.insert = (callout_x, left_y + offset_y)
                    extents = ezbbox.extents([callout], fast=False)
                    if not extents.has_data:
                        continue
                    lo, hi = extents.extmin, extents.extmax
                    bounds = box(lo.x, lo.y, hi.x, hi.y)
                    if selected_base.intersects(bounds.buffer(0.25)):
                        continue
                    if any(bounds.intersects(previous.buffer(0.5))
                           for previous in hole_callout_bounds):
                        continue
                    leader = LineString([(left_x + clearance_diameter / 2, left_y),
                                         (lo.x, (lo.y + hi.y) / 2)])
                    if any(leader.intersects(outline.buffer(0.25))
                           for outline in side_view_outlines or []):
                        continue
                    accepted_callout = (bounds, lo, hi)
                    break
                if accepted_callout is not None:
                    break
            if accepted_callout is None:
                raise ValueError("내부 홀 직경/THRU 주서를 JIG 외곽·투영과 겹치지 않게 배치할 공간이 없습니다. "
                                 "선택한 측면 투영 후보를 확인하세요.")
            callout_bounds, lo, hi = accepted_callout
            hole_callout_bounds.append(callout_bounds)
            msp.add_line((left_x + clearance_diameter / 2, left_y),
                         (lo.x, (lo.y + hi.y) / 2),
                         dxfattribs={"layer": "PIN_HOLE_CALLOUTS", "color": 1})
            x = left_x + pin_translation[0]
            y = left_y + pin_translation[1]
            if pin_outline is not None and not pin_outline.buffer(tol).covers(
                    Point(x, y).buffer(pin["diameter"] / 2, quad_segs=64)):
                raise ValueError(f"제품 {placement['no']} GUIDE PIN이 JIG 오른쪽 하판 외곽을 벗어납니다. "
                                 "상판/하판 외곽 선택과 제품 배열 위치를 확인하세요.")
            layer = f"GUIDE_PIN_H{pin['height']:.3f}"
            if layer not in doc.layers:
                doc.layers.add(layer, color=4)
            msp.add_circle((x, y), pin["diameter"] / 2, dxfattribs={"layer": layer})
            if "PIN_ANNOTATIONS" not in doc.layers:
                doc.layers.add("PIN_ANNOTATIONS", color=4)
            note_width = 14.0
            note_gap = 0.8
            right_x = x + pin["diameter"] / 2 + note_gap
            left_x = x - pin["diameter"] / 2 - note_gap - note_width
            if pin_outline is not None:
                right_space = pin_outline.bounds[2] - right_x
                left_space = left_x - pin_outline.bounds[0]
                if right_space >= note_width or right_space >= left_space:
                    side_positions = [right_x, left_x]
                else:
                    side_positions = [left_x, right_x]
            else:
                side_positions = [right_x, left_x]
            text = f"Ø{pin['diameter']:.3f} / H={pin['height']:.3f} mm"
            note = msp.add_mtext(text, dxfattribs={"layer": "PIN_ANNOTATIONS", "char_height": 0.8,
                                                  "width": note_width, "insert": (side_positions[0], y)})
            candidate_positions = [(note_x, y + dy) for dy in (0.0, 2.0, -2.0, 4.0, -4.0, 6.0, -6.0)
                                   for note_x in side_positions]
            candidate_positions.extend([
                (x - note_width / 2, y + pin["diameter"] / 2 + note_gap + 2.0),
                (x - note_width / 2, y - pin["diameter"] / 2 - note_gap - 2.0),
                (x + pin["diameter"] / 2 + note_gap, y + pin["diameter"] / 2 + note_gap + 2.0),
                (x - pin["diameter"] / 2 - note_gap - note_width, y + pin["diameter"] / 2 + note_gap + 2.0),
            ])
            accepted_bounds = None
            for note_x, note_y in candidate_positions:
                note.dxf.insert = (note_x, note_y)
                extents = ezbbox.extents([note], fast=False)
                if not extents.has_data:
                    continue
                lo, hi = extents.extmin, extents.extmax
                current_bounds = box(lo.x, lo.y, hi.x, hi.y)
                in_plate = (pin_outline is None
                            or pin_outline.buffer(-0.001).covers(current_bounds))
                clear_of_pins = not any(current_bounds.intersects(obstacle)
                                        for obstacle in pin_obstacles)
                clear_of_notes = not any(current_bounds.intersects(bounds.buffer(0.2))
                                         for bounds in annotation_bounds)
                if in_plate and clear_of_pins and clear_of_notes:
                    accepted_bounds = current_bounds
                    break
            if accepted_bounds is None:
                msp.delete_entity(note)
                raise ValueError(f"제품 {placement['no']} GUIDE PIN의 직경/높이 주서를 하판 안에 겹치지 않게 둘 공간이 없습니다. "
                                 "핀 간격 또는 하판 외곽을 확인하세요.")
            annotation_bounds.append(accepted_bounds)

    if "ISOMETRIC_REVIEW" not in doc.layers:
        doc.layers.add("ISOMETRIC_REVIEW", color=6)

    def iso_polygons(geometry):
        if geometry.geom_type == "Polygon":
            yield geometry
        elif hasattr(geometry, "geoms"):
            for part in geometry.geoms:
                yield from iso_polygons(part)

    def plate_thickness(outline):
        if outline is None or not side_view_outlines:
            return None
        width = outline.bounds[2] - outline.bounds[0]
        height = outline.bounds[3] - outline.bounds[1]
        plate_edges = [edge for edge in (width, height) if edge > 0]
        thicknesses = []
        for candidate in side_view_outlines:
            dx = candidate.bounds[2] - candidate.bounds[0]
            dy = candidate.bounds[3] - candidate.bounds[1]
            long_edge, short_edge = max(dx, dy), min(dx, dy)
            if short_edge <= 0 or short_edge > min(plate_edges) * 0.2:
                continue
            if any(abs(long_edge - edge) <= max(0.05, edge * 0.01) for edge in plate_edges):
                thicknesses.append(short_edge)
        if not thicknesses:
            return None
        thicknesses.sort()
        return thicknesses[len(thicknesses) // 2]

    def add_iso_group(outline, name, board_kind):
        if outline is None or outline.is_empty:
            return False
        origin = (outline.bounds[0], outline.bounds[1])
        segments = []

        def iso_point(x, y, z=0.0):
            local_x, local_y = x - origin[0], y - origin[1]
            return (math.sqrt(3) * 0.5 * (local_x - local_y),
                    0.5 * (local_x + local_y) + z)

        def iso_ring(ring, z, color):
            points = [iso_point(x, y, z) for x, y in list(ring.coords)[:-1]]
            if len(points) >= 3:
                segments.append((points, True, color))

        def iso_geometry(geometry, z, color, include_interiors=True):
            for polygon in iso_polygons(geometry):
                iso_ring(polygon.exterior, z, color)
                if include_interiors:
                    for ring in polygon.interiors:
                        iso_ring(ring, z, color)

        def iso_connector(start, end, color):
            segments.append(([iso_point(*start), iso_point(*end)], False, color))

        def connect_geometry(geometry, top_z, bottom_z, color, include_interiors=True):
            for polygon in iso_polygons(geometry):
                rings = [polygon.exterior]
                if include_interiors:
                    rings.extend(polygon.interiors)
                for ring in rings:
                    coords = list(ring.coords)[:-1]
                    if not coords:
                        continue
                    for index in sorted({round(i * len(coords) / 4) % len(coords)
                                         for i in range(min(4, len(coords)))}):
                        x, y = coords[index]
                        iso_connector((x, y, top_z), (x, y, bottom_z), color)

        thickness = plate_thickness(outline)
        iso_geometry(outline, 0.0, 7)
        if thickness is not None:
            iso_geometry(outline, -thickness, 8)
            connect_geometry(outline, 0.0, -thickness, 8)

        if board_kind == "top":
            for operation in plan["operations"]:
                for placement in placements:
                    geometry = machining_region(operation, placement)
                    if geometry.is_empty:
                        continue
                    if operation["kind"] == "DP":
                        depth = operation["depth"] or 0.0
                        iso_geometry(geometry, 0.0, 2, include_interiors=False)
                        iso_geometry(geometry, -depth, 2, include_interiors=False)
                        connect_geometry(geometry, 0.0, -depth, 2, include_interiors=False)
                    else:
                        iso_geometry(geometry, 0.0, 1)
                        if thickness is not None:
                            iso_geometry(geometry, -thickness, 1)
                            connect_geometry(geometry, 0.0, -thickness, 1)
        else:
            for placement in placements:
                for pin in plan["pins"]:
                    x, y = tf((pin["x"], pin["y"]), c0, placement["rot"],
                              placement["x"], placement["y"])
                    x += pin_translation[0]
                    y += pin_translation[1]
                    pin_ring = Point(x, y).buffer(pin["diameter"] / 2, quad_segs=16)
                    iso_geometry(pin_ring, 0.0, 4)
                    iso_geometry(pin_ring, pin["height"], 4)
                    connect_geometry(pin_ring, 0.0, pin["height"], 4)

        points = [point for line, _, _ in segments for point in line]
        if not points:
            return False
        min_x, max_x = min(point[0] for point in points), max(point[0] for point in points)
        min_y, max_y = min(point[1] for point in points), max(point[1] for point in points)
        center_x = (outline.bounds[0] + outline.bounds[2]) / 2
        shift_x = center_x - (min_x + max_x) / 2
        shift_y = outline.bounds[3] + 15.0 - min_y
        for line, closed, color in segments:
            projected = [(x + shift_x, y + shift_y) for x, y in line]
            if len(projected) >= 2:
                msp.add_lwpolyline(projected, close=closed,
                                   dxfattribs={"layer": "ISOMETRIC_REVIEW", "color": color})

        dp_depths = sorted({operation["depth"] for operation in plan["operations"]
                            if operation["kind"] == "DP" and operation["depth"] is not None})
        pin_heights = sorted({pin["height"] for pin in plan["pins"]})
        thickness_label = (f"두께 {thickness:.3f} mm (측면 투영 기준)"
                           if thickness is not None else "두께 확인 필요 (측면 투영 미확인)")
        detail = ("DP " + ", ".join(f"Z{depth:.3f} mm" for depth in dp_depths)
                  if board_kind == "top" and dp_depths else
                  "GUIDE PIN " + ", ".join(f"H={height:.3f} mm" for height in pin_heights)
                  if board_kind == "bottom" and pin_heights else "가공 형상 없음")
        label = (f"{name} 아이소메트릭 검토\\P{thickness_label} · {detail}\\P"
                 "2D 와이어프레임 · 3D 솔리드 아님")
        msp.add_mtext(label, dxfattribs={
            "layer": "ISOMETRIC_REVIEW", "color": 6, "char_height": 1.5,
            "width": min(180.0, max(60.0, max_x - min_x)),
            "insert": (min_x + shift_x, max_y + shift_y + 4.0)})
        return True

    iso_groups = [add_iso_group(view_base, "상판", "top")]
    if pin_outline is not None:
        iso_groups.append(add_iso_group(pin_outline, "하판", "bottom"))

    if "MACHINING_REVIEW" not in doc.layers:
        doc.layers.add("MACHINING_REVIEW", color=1)
    bot_relief_offset = plan.get("cfg", {}).get("bot_relief_offset", 1.0)
    note_lines = ["2D OPERATION REVIEW - NOT NC/G-CODE",
                  f"LEFT TOP PLATE: TOP DP / BOT COMPONENT +{bot_relief_offset:g}mm THRU / PIN CLEARANCE THRU   "
                  f"RIGHT LOWER PLATE: GUIDE PINS ONLY "
                  f"(DX={pin_translation[0]:.3f}, DY={pin_translation[1]:.3f}mm)"]
    note_lines.append("BOT THRU: Ø1 tool R0.5 rounding; relief gaps under 1.5mm are connected")
    note_lines.append("TAPE: DP KEEP-OUT ONLY; NO TAPE MACHINING GEOMETRY")
    if plan.get("pin_clearance") is not None and not plan["pin_clearance"].is_empty:
        note_lines.append("PIN_CLEARANCE_REF: Ø(pin + 0.5mm) THRU clearance, already included in THRU")
    if dp_depths:
        note_lines.append("DP_ANNOTATIONS: DP depth in mm")
    if reference_bounds and reference_tape:
        note_lines.append("PRODUCT_REFERENCE_TAPE: original reference only; layer OFF by default")
    if plan["pins"]:
        note_lines.append("PIN_ANNOTATIONS: GUIDE PIN diameter and height in mm")
    if any(iso_groups):
        note_lines.append("ISOMETRIC_REVIEW: plate thickness inferred from selected side projections; 2D wireframe, not a 3D solid")
    if reference_bounds:
        note_lines.append("PRODUCT TOP/SIDE/BOT REFERENCE: LEFT OF JIG (1:1); REFERENCE LINES ARE NOT MACHINING")
    note_lines.extend(plan["warnings"])
    notes = "\\P".join(note_lines)
    note_x = reference_bounds[0] if reference_bounds else view_base.bounds[0]
    note_width = (min(120.0, reference_bounds[2] - reference_bounds[0])
                  if reference_bounds else 120.0)
    annotation_extents = ezbbox.extents(list(msp.query('MTEXT[layer=="DP_ANNOTATIONS"]')), fast=False)
    note_y = (annotation_extents.extmin.y - 3.0 if annotation_extents.has_data else
              reference_bounds[1] - 2.0 if reference_bounds else view_base.bounds[1] - 2.0)
    review_note = msp.add_mtext(notes, dxfattribs={"layer": "MACHINING_REVIEW", "char_height": 1.5,
                                                  "width": max(1.0, note_width), "insert": (note_x, note_y)})
    return doc_bytes(doc)


def machining_ui(base_dxf, product_dxf, nesting_cfg, exe, version, manual_layers=False, base_jig_kind=None):
    st.subheader("TOP / SIDE / BOT 가공도 설정")
    st.caption("2D 가공 경계와 Ø1/Ø2 공구 추천을 표시합니다. 툴 이동 동선은 결과 도면에 그리지 않습니다. NC/G-code는 생성하지 않습니다. "
               "TOP +0.03mm, BOT 부품 피난홀 옵셋 THRU, TAPE 가공 금지, 핀 직경=홀−0.1mm를 적용합니다. "
                "선택한 JIG 측면 투영에서 판 두께를 읽어 상·하판별 2D 아이소메트릭 와이어프레임을 표시합니다.")
    doc = read_doc(product_dxf)
    sources = machining_sources(doc)
    document_layers = [source.split(":", 1)[1] for source in sources if source.startswith("LAYER:")]
    cfg = dict(tol=nesting_cfg["tol"], hole_dmin=nesting_cfg["hole_dmin"],
               hole_dmax=nesting_cfg["hole_dmax"],
               bot_relief_offset=nesting_cfg.get("bot_relief_offset", 1.0))
    named_sources = {view: sorted([source for source in sources if view in source.split(":", 1)[1].upper()],
                                  key=lambda source: (not source.startswith("BLOCK:"), source))
                     for view in ("TOP", "SIDE", "BOT")}
    auto_cfg = detect_multiview_dxf(product_dxf) or {}
    methods = (["도면 자동 인식", "레이어/블록 이름", "수동 영역"] if auto_cfg
               else ["레이어/블록 이름", "수동 영역"])
    default_method = (0 if auto_cfg else
                      0 if all(named_sources.values()) else 1)
    method = st.radio("뷰 구분 및 정합 방식", methods, index=default_method, horizontal=True,
                      key="multiview_detection_mode_v2")
    auto_view_mode = method == "도면 자동 인식"
    if auto_view_mode:
        cfg.update(auto_cfg)
        cfg["tol"] = nesting_cfg["tol"]
        cfg["hole_dmin"] = nesting_cfg["hole_dmin"]
        cfg["hole_dmax"] = nesting_cfg["hole_dmax"]
        cfg.update(top_source="", side_source="", bot_source="")
        st.success("TOP/SIDE/BOT 뷰·축·깊이·정합을 도면에서 확인했습니다.")
        if manual_layers:
            st.caption("뷰 위치와 정합은 자동으로 적용합니다. 가공 역할 레이어는 아래에서 직접 선택하세요.")
        else:
            st.caption(f"추천 레이어: TOP 외곽 {', '.join(cfg['top_outline_layers'])} · 홀 {', '.join(cfg['top_hole_layers'])} · "
                       f"TAPE {', '.join(cfg['tape_layers'])} / SIDE 단면 {', '.join(cfg['side_profile_layers'])} / "
                       f"BOT 부품 {', '.join(cfg['bot_component_layers'])}. 레이어는 아래에서 바꿀 수 있습니다.")
        columns = st.columns(3)
        view_entries = {view: region_entities(doc, cfg[f"{view.lower()}_region"])
                        for view in ("TOP", "SIDE", "BOT")}
        for column, view in zip(columns, ("TOP", "SIDE", "BOT")):
            xmin, ymin, xmax, ymax = cfg[f"{view.lower()}_region"]
            column.markdown(f"**{view}**")
            column.caption(f"{xmax - xmin:.3f} × {ymax - ymin:.3f} mm · {len(view_entries[view])}개 형상")
        with st.expander("인식된 영역 미리보기", expanded=False):
            preview = st.checkbox("각 뷰 형상 미리보기 표시", value=False,
                                  key=f"multiview_preview_{hashlib.sha256(product_dxf).hexdigest()[:10]}")
            if preview:
                preview_columns = st.columns(3)
                for column, view in zip(preview_columns, ("TOP", "SIDE", "BOT")):
                    xmin, ymin, xmax, ymax = cfg[f"{view.lower()}_region"]
                    fig, ax = plt.subplots(figsize=(4, 3))
                    for entity, _ in view_entries[view]:
                        if entity.dxftype() == "CIRCLE":
                            center = entity.dxf.center
                            ax.add_patch(Circle((center.x, center.y), entity.dxf.radius,
                                                fill=False, color="tab:red"))
                        else:
                            for path in ezpath.make_path(entity).sub_paths():
                                points = list(path.flattening(0.02))
                                ax.plot([point.x for point in points], [point.y for point in points], lw=0.5)
                    ax.set_xlim(xmin, xmax)
                    ax.set_ylim(ymin, ymax)
                    ax.set_aspect("equal")
                    ax.ticklabel_format(style="plain", useOffset=False)
                    ax.locator_params(nbins=3)
                    ax.tick_params(labelsize=7)
                    column.pyplot(fig)
                    plt.close(fig)
    else:
        cfg["view_mode"] = "regions" if method == "수동 영역" else "named"
        st.caption("블록은 필수가 아닙니다. 레이어/블록 이름이 없거나 도면 자동 인식이 불가능하면 수동 영역을 선택하세요. "
                   "뷰 영역은 서로 닿지 않게 지정해야 합니다.")
        columns = st.columns(3)
        view_entries = {}
        for column, view in zip(columns, ("TOP", "SIDE", "BOT")):
            if cfg["view_mode"] == "named":
                named = named_sources[view]
                default = sources.index(named[0]) + 1 if named else 0
                selected = column.selectbox(f"{view} 뷰 소스", [""] + sources, index=default,
                                            format_func=lambda value: value or "레이어/블록 선택")
                cfg[f"{view.lower()}_source"] = selected
                entries = source_entities(doc, selected) if selected else []
            else:
                cfg[f"{view.lower()}_source"] = ""
                xmin = column.number_input(f"{view} X 최소", value=None, format="%.3f")
                ymin = column.number_input(f"{view} Y 최소", value=None, format="%.3f")
                xmax = column.number_input(f"{view} X 최대", value=None, format="%.3f")
                ymax = column.number_input(f"{view} Y 최대", value=None, format="%.3f")
                bounds = (xmin, ymin, xmax, ymax)
                cfg[f"{view.lower()}_region"] = bounds if all(value is not None for value in bounds) else None
                entries = []
                if cfg[f"{view.lower()}_region"] is not None:
                    try:
                        entries = region_entities(doc, bounds)
                    except ValueError as error:
                        column.warning(str(error))
                column.caption(f"{view} 영역: {len(entries)}개 형상. 영역 안에 완전히 포함된 요소만 사용합니다.")
            view_entries[view] = entries
        if cfg["view_mode"] == "regions" and all(cfg.get(f"{view}_region") is not None for view in ("top", "side", "bot")):
            try:
                validate_view_regions(cfg)
            except ValueError as error:
                st.warning(str(error))
        if not auto_cfg and not any(view in source.split(":", 1)[1].upper()
                                    for view in ("TOP", "SIDE", "BOT") for source in sources):
            st.warning("도면에서 정확한 TOP/SIDE/BOT 표식과 내부 홀 대응을 찾지 못했습니다. "
                       "수동 영역 또는 레이어/블록 이름을 지정하세요.")
        elif cfg["view_mode"] == "named" and not all(cfg[f"{v}_source"] for v in ("top", "side", "bot")):
            st.warning("TOP/SIDE/BOT 이름의 레이어 또는 블록을 각각 선택하세요.")
    view_layers = {view: sorted({layer for _, layer in entries}) for view, entries in view_entries.items()}

    def feature(label, view, token, key):
        layers = view_layers[view] or document_layers
        suggested = [layer for layer in auto_cfg.get(key, []) if layer in layers]
        if auto_view_mode:
            defaults = [layer for layer in cfg[key] if layer in layers]
        elif manual_layers and suggested:
            defaults = suggested
        else:
            defaults = feature_layer_defaults(layers, token)
        mode_key = "manual" if manual_layers else "auto"
        widget_key = f"{key}_multiview_{mode_key}_{hashlib.sha256(product_dxf).hexdigest()[:10]}"
        cfg[key] = st.multiselect(label, layers, default=defaults, key=widget_key)
        if cfg[f"{view.lower()}_source"] or (cfg["view_mode"] == "regions" and cfg[f"{view.lower()}_region"] is not None):
            missing = sorted(set(cfg[key]) - set(view_layers[view]))
            if missing:
                st.warning(f"{view} 선택 뷰 소스/영역에 역할 레이어 {', '.join(missing)}가 없습니다. 소스/영역/역할 레이어를 확인하세요.")

    st.caption(f"BOT 부품 외곽을 왼쪽 설계 파라미터의 {cfg['bot_relief_offset']:.1f}mm 옵셋으로 확장해 피난홀 THRU를 만듭니다. "
               "TOP 내부 홀은 가이드핀 위치로 사용합니다. Ø1 툴 R0.5 라운딩과 간격 1.5mm 미만 연결은 유지됩니다.")

    with st.expander("뷰 안의 기능 레이어 지정", expanded=manual_layers or not auto_view_mode):
        feature("TOP 제품 외곽 레이어", "TOP", "OUTLINE", "top_outline_layers")
        feature("TOP 내부 홀 → 가이드핀 위치", "TOP", "HOLE", "top_hole_layers")
        feature("TOP TAPE 가공 금지 레이어", "TOP", "TAPE", "tape_layers")
        feature("SIDE 단차 프로파일 레이어", "SIDE", "PROFILE", "side_profile_layers")
        feature("BOT 부품 외곽 → 피난홀 THRU", "BOT", "COMPONENT", "bot_component_layers")
        st.caption("TOP 내부 홀은 가이드핀 위치로 사용합니다(핀 직경=홀−0.1mm). "
                   "BOT 부품 피난홀은 선택한 옵셋만큼 확장하고 TAPE와 겹쳐도 줄이지 않습니다. "
                   "TAPE는 DP 금지영역으로만 쓰고 별도 가공형상은 출력하지 않습니다.")
        st.caption("기능 이름이 다르면 직접 지정하세요. TOP 외곽에는 도면틀/표제를, BOT 부품에는 보드 전체 외곽을 포함하지 마세요.")
        if view_entries["TOP"] and cfg["top_outline_layers"]:
            top_doc = feature_doc(view_entries["TOP"], cfg["top_outline_layers"])
            top_candidates = parse_geometry(top_doc, cfg["top_outline_layers"], cfg["tol"]).outlines
            cfg["top_outline_index"] = st.selectbox("TOP 제품 외곽 후보", range(len(top_candidates)),
                                                    format_func=lambda i: outline_label(i, top_candidates))
    with st.expander("SIDE 투영과 깊이 기준", expanded=not auto_view_mode):
        if auto_view_mode:
            st.caption(f"자동 판정: SIDE {cfg['side_profile_axis']}축 → TOP {cfg['side_axis']}축, "
                       f"깊이 {cfg['side_depth_sign']:+d}{'Y' if cfg['side_profile_axis'] == 'X' else 'X'}, "
                       f"기준면 {cfg['side_datum']:.3f} mm. 경사/곡선 단면은 자동 가공하지 않습니다.")
        else:
            cfg["side_profile_axis"] = st.selectbox("SIDE 도면의 투영축", ["X", "Y"])
            cfg["side_axis"] = st.selectbox("SIDE → TOP 투영축", ["X", "Y"])
            cfg["side_reverse"] = st.checkbox("SIDE 투영 방향 반전")
            cfg["side_surface"] = st.selectbox("SIDE 깊이 경계", ["max", "min"],
                                               format_func=lambda value: "최대 좌표 경계" if value == "max" else "최소 좌표 경계")
            depth_axis = "Y" if cfg["side_profile_axis"] == "X" else "X"
            cfg["side_depth_sign"] = st.selectbox("SIDE 깊이 증가 방향", [1, -1],
                                                  format_func=lambda value: ("+" if value == 1 else "−") + depth_axis)
            cfg["side_origin"] = st.number_input("SIDE 투영 기준 좌표", value=None, format="%.3f")
            cfg["side_top_origin"] = st.number_input("TOP 대응 기준 좌표", value=None, format="%.3f")
            cfg["side_datum"] = st.number_input("SIDE 깊이 기준면", value=None, format="%.3f")
            st.caption("SIDE에서 선택한 투영축을 TOP의 선택 축에 1:1 대응합니다. 수평/수직 단차만 처리합니다.")
    with st.expander("BOT → TOP 정합", expanded=not auto_view_mode):
        if auto_view_mode:
            st.caption("TOP/BOT 내부 홀 2쌍을 대조해 X 반전, 회전, 정합점을 자동으로 맞췄습니다.")
        else:
            cfg["bot_mirror_x"] = st.checkbox("BOT X 좌표 반전")
            cfg["bot_mirror_y"] = st.checkbox("BOT Y 좌표 반전")
            cfg["bot_rotation"] = st.selectbox("BOT 회전", [0, 90, 180, 270])
            source_x = st.number_input("BOT 기준 X", value=None, format="%.3f")
            source_y = st.number_input("BOT 기준 Y", value=None, format="%.3f")
            target_x = st.number_input("BOT 정합 X", value=None, format="%.3f")
            target_y = st.number_input("BOT 정합 Y", value=None, format="%.3f")
            cfg["bot_origin"], cfg["bot_target_origin"] = (source_x, source_y), (target_x, target_y)
            st.caption("BOT 기준점을 원점으로 이동 → 좌표 반전/회전 → TOP 정합점으로 이동합니다.")
    cfg["pin_height"] = st.number_input("핀 높이 (mm)", 1.5, 2.0, 1.8, 0.1, format="%.2f")
    base_layers = list_layers(read_doc(base_dxf))
    selected_base = st.multiselect("가공 베이스 외곽 레이어", base_layers,
                                   default=base_jig_default_layers(base_jig_kind, base_layers))
    try:
        base = parse_cad(base_dxf, tuple(sorted(selected_base)), nesting_cfg["tol"])
        top_default, bottom_default = jig_plate_indices(base.outlines)
    except Exception as error:
        st.error(f"JIG 좌측 상판/우측 하판 외곽을 확인하세요: {error}")
        return
    plate_options = list(range(len(base.outlines)))
    top_column, bottom_column = st.columns(2)
    top_index = top_column.selectbox("JIG 왼쪽 상판 외곽", plate_options,
                                    index=plate_options.index(top_default),
                                    format_func=lambda index: outline_label(index, base.outlines),
                                    key=f"jig_top_plate_{hashlib.sha256(base_dxf).hexdigest()[:10]}")
    bottom_index = bottom_column.selectbox("JIG 오른쪽 하판 외곽", plate_options,
                                          index=plate_options.index(bottom_default),
                                          format_func=lambda index: outline_label(index, base.outlines),
                                          key=f"jig_bottom_plate_{hashlib.sha256(base_dxf).hexdigest()[:10]}")
    if top_index == bottom_index:
        st.error("좌측 상판과 우측 하판을 서로 다른 외곽 후보로 지정하세요.")
        return
    top_outline, bottom_outline = base.outlines[top_index], base.outlines[bottom_index]
    side_view_candidates = jig_side_view_candidates(read_doc(base_dxf), base.outlines,
                                                     top_index, bottom_index)
    side_view_indices = st.multiselect(
        "JIG 측면/정면 투영 포함", list(range(len(side_view_candidates))),
        default=list(range(len(side_view_candidates))),
        format_func=lambda index: jig_side_view_label(index, side_view_candidates),
        key=f"jig_side_views_{hashlib.sha256(base_dxf).hexdigest()[:10]}_{top_index}_{bottom_index}")
    selected_side_views = [side_view_candidates[index] for index in side_view_indices]
    st.caption("원본 JIG의 우측 측면 투영은 유지하고, 별도 생성 단면은 결과에서 제외합니다.")
    if not side_view_candidates:
        st.caption("주판 치수와 맞는 얇은 JIG 측면/정면 투영을 찾지 못했습니다. 결과에는 상판·하판 외곽만 포함됩니다.")
    signature = hashlib.sha256(b"component-thru-configurable-offset-r0.5-no-tape-pin-clearance-depth-notes-v8-sideviews-no-generated-sections-dp-union-v17-no-toolpaths-v19"
                               + base_dxf + product_dxf
                               + repr((cfg, selected_base, top_index, bottom_index,
                                       side_view_indices, nesting_cfg)).encode()).hexdigest()
    if st.button("가공도 계산 및 배열", type="primary"):
        try:
            required = [cfg["side_origin"], cfg["side_top_origin"], cfg["side_datum"],
                        *cfg["bot_origin"], *cfg["bot_target_origin"]]
            if any(value is None or not math.isfinite(value) for value in required):
                raise ValueError("SIDE/BOT 기준을 찾지 못했습니다. 자동 인식이 불가하면 수동 영역 모드에서 지정하세요.")
            plan = create_machining_plan(product_dxf, cfg)
            layout_cfg = dict(nesting_cfg, base_layers=selected_base, base_outline_index=top_index,
                              prod_layers=["PRODUCT_OUTLINE"], hole_layers=["PRODUCT_HOLES"],
                              prod_outline_index=0, offset=0.03, pin_clear=0.1)
            result = run_nesting(base_dxf, plan["nesting_dxf"], layout_cfg)
            output = build_machining_dxf(base_dxf, plan, result["placements"], result["c0"],
                                          top_outline, pin_outline=bottom_outline,
                                          reference_dxf=product_dxf,
                                          side_view_outlines=selected_side_views)
            dwg = None
            if exe:
                try:
                    with tempfile.TemporaryDirectory(prefix="jignest_") as tmp:
                        dwg = oda_convert(exe, output, "dxf", "DWG", version, tmp)
                except Exception as error:
                    plan["warnings"].append(f"DWG 변환 실패(DXF 사용 가능): {error}")
            st.session_state["machining_result"] = dict(signature=signature, plan=plan, nesting=result,
                                                         dxf=output, dwg=dwg)
        except Exception as error:
            st.session_state.pop("machining_result", None)
            st.error(f"가공도 검토 필요: {error}")
    result = st.session_state.get("machining_result")
    if not result or result["signature"] != signature:
        return
    plan, nesting = result["plan"], result["nesting"]
    for warning in list(dict.fromkeys(plan["warnings"] + nesting["warnings"])):
        st.warning(warning)
    st.metric("가공도 배치 수량", len(nesting["placements"]))
    st.caption(f"JIG 왼쪽 상판 안에 TOP 외곽 DP와 BOT 부품 외곽 +{plan['cfg']['bot_relief_offset']:.1f}mm THRU를 표시합니다. "
               "TAPE는 DP 금지영역으로만 쓰며 별도 가공형상은 출력하지 않습니다. "
               "원본 3면 참조에 포함된 TAPE 선은 참고용입니다. "
               "오른쪽 하판 안에는 상판 배열과 중심 정합한 GUIDE PIN만 표시합니다.")
    preview_width = st.slider("가공도 미리보기 너비 (px)", 200, 800, 400, step=100, format="%d px")
    fig, ax = plt.subplots(figsize=(preview_width / 100, preview_width * 5 / 9 / 100))
    ax.plot(*plan["top"].outline.exterior.xy, color="black", label="TOP")
    for operation_index, operation in enumerate(plan["operations"]):
        operation_polygons = (list(operation["geometry"].geoms)
                              if hasattr(operation["geometry"], "geoms") else [operation["geometry"]])
        for polygon_index, polygon in enumerate(operation_polygons):
            if polygon.geom_type == "Polygon":
                label = (f"{operation['kind']} depth={operation['depth']}"
                         if polygon_index == 0 else "_nolegend_")
                add_preview_polygon(ax, polygon, color=f"C{operation_index % 10}",
                                    alpha=0.25, label=label)
    if not plan["tape"].is_empty:
        tape_polygons = (list(plan["tape"].geoms) if hasattr(plan["tape"], "geoms") else [plan["tape"]])
        for polygon_index, polygon in enumerate(tape_polygons):
            if polygon.geom_type == "Polygon":
                add_preview_polygon(ax, polygon, color="red", alpha=0.3,
                                    label="TAPE DP KEEP-OUT ONLY" if polygon_index == 0 else "_nolegend_")
    ax.set_aspect("equal")
    ax.legend(fontsize=7)
    st.caption("아래 미리보기와 핀 좌표는 TOP 원본 좌표의 단일 제품 기준입니다. "
               "TAPE는 DP 가공 금지영역으로만 반영합니다. BOT 피난 THRU는 R0.5로 다듬고, "
               "간격 1.5mm 미만인 영역은 연결합니다. GUIDE PIN 위치에는 핀 직경+0.5mm 관통공을 추가합니다.")
    show_preview(fig, preview_width)
    st.dataframe(pd.DataFrame([dict(가공=op["kind"], 깊이=op["depth"],
                                     공구추천=", ".join(f"Ø{tool:g}" for tool in op["tools"]) or "검토 필요")
                               for op in plan["operations"]]))
    st.dataframe(pd.DataFrame(plan["pins"]))
    st.download_button("가공도 DXF", result["dxf"], "machining_operations.dxf", "application/dxf")
    if result["dwg"]:
        st.download_button("가공도 DWG", result["dwg"], "machining_operations.dwg", "application/acad")
    st.caption("Ø2 우선, Ø1은 Ø2로 남는 영역의 기하 기준 추천입니다. 공구 길이·홀더·재질·절삭 조건은 포함하지 않습니다.")


# ---------------------------------------------------------------------------
# 3. 배열(Nesting) 알고리즘
# ---------------------------------------------------------------------------
def _norm_cells(cells):
    mj, mi = min(c[0] for c in cells), min(c[1] for c in cells)
    return sorted(((j - mj, i - mi, cx, cy) for j, i, cx, cy in cells))


def auto_layout(usable, fp0: Polygon, spacing: float):
    """Auto-Max: 유효영역 안에 들어가는 최대 격자 배열.
    - 피치 = 풋프린트 bbox + spacing  (인접 bbox 간 틈 >= spacing 보장)
    - 직사각형 영역이면 1개 위상, 비정형이면 4x4 위상(phase) 탐색 후 최대 수량 선택
    - 결과 격자는 유효영역 중앙으로 정렬(여유 분배)"""
    minx, miny, maxx, maxy = usable.bounds
    x0, y0, x1, y1 = fp0.bounds
    w, h = x1 - x0, y1 - y0
    px, py = w + spacing, h + spacing
    is_rect = abs(usable.area - (maxx - minx) * (maxy - miny)) < 1e-6 * max(usable.area, 1.0)
    phases = [(0.0, 0.0)] if is_rect else [(a / 4, b / 4) for a in range(4) for b in range(4)]
    if is_rect:
        # 남는 폭을 양쪽에 반씩 나눈 위상도 본다. 첫 칸을 경계에 딱 붙이면 발자국 bbox 의
        # 1µm 미만 어긋남으로 첫 행·열이 통째로 빠진다 (FPCB TEST-2: 2x2 → 1개, 2026-11-18).
        fit_x = max(int((maxx - minx + spacing) // px), 1)
        fit_y = max(int((maxy - miny + spacing) // py), 1)
        slack_x = (maxx - minx) - (fit_x * px - spacing)
        slack_y = (maxy - miny) - (fit_y * py - spacing)
        phases.append((slack_x / 2 / px, slack_y / 2 / py))
    pu = prep(usable)
    nx, ny = int((maxx - minx) / px) + 2, int((maxy - miny) / py) + 2
    def phase_cells(fx, fy):
        cells = []
        for j in range(-1, ny):
            for i in range(-1, nx):
                cx = minx + w / 2 + (i + fx) * px
                cy = miny + h / 2 + (j + fy) * py
                if pu.contains(affinity.translate(fp0, cx, cy)):
                    cells.append((j, i, cx, cy))
        return cells

    best = []
    for fx, fy in phases:
        cells = phase_cells(fx, fy)
        if len(cells) > len(best):
            best = cells
    if is_rect and not best:
        for a in range(4):
            for b in range(4):
                if a == 0 and b == 0:
                    continue
                cells = phase_cells(a / 4, b / 4)
                if len(cells) > len(best):
                    best = cells
    if not best:
        center = usable.representative_point()
        if pu.contains(affinity.translate(fp0, center.x, center.y)):
            best = [(0, 0, center.x, center.y)]
        else:
            return []
    xs, ys = [c[2] for c in best], [c[3] for c in best]
    dx = (minx + maxx) / 2 - (min(xs) + max(xs)) / 2
    dy = (miny + maxy) / 2 - (min(ys) + max(ys)) / 2
    shifted = [(j, i, cx + dx, cy + dy) for j, i, cx, cy in best]
    if all(pu.contains(affinity.translate(fp0, cx, cy)) for _, _, cx, cy in shifted):
        best = shifted
    return _norm_cells(best)


def manual_layout(usable, fp0: Polygon, spacing: float, rows: int, cols: int):
    """수동 행x열: 최소 피치로 배열 후 유효영역 중앙 정렬. 영역을 벗어난 셀은 제외하고 개수를 보고."""
    minx, miny, maxx, maxy = usable.bounds
    x0, y0, x1, y1 = fp0.bounds
    w, h = x1 - x0, y1 - y0
    px, py = w + spacing, h + spacing
    sx = (minx + maxx) / 2 - ((cols - 1) * px + w) / 2 + w / 2
    sy = (miny + maxy) / 2 - ((rows - 1) * py + h) / 2 + h / 2
    pu, ok, bad = prep(usable), [], 0
    for j in range(rows):
        for i in range(cols):
            cx, cy = sx + i * px, sy + j * py
            if pu.contains(affinity.translate(fp0, cx, cy)):
                ok.append((j, i, cx, cy))
            else:
                bad += 1
    return sorted(ok), bad


def verify_spacing(fps: list, spacing: float):
    """실제 풋프린트 간 최소 거리 검증 (STRtree)."""
    if len(fps) < 2:
        return None, 0
    tree = shapely.STRtree(fps)
    min_d, viol = float("inf"), 0
    for i, g in enumerate(fps):
        for j in tree.query(g.buffer(spacing + 1.0)):
            if j > i:
                d = g.distance(fps[j])
                min_d = min(min_d, d)
                if d < spacing - 1e-6:
                    viol += 1
    return (None if min_d == float("inf") else min_d), viol


def tf(pt, c0, rot, cx, cy):
    """제품 기준점(c0)->회전(0/90)->배치 중심(cx,cy) 좌표변환."""
    dx, dy = pt[0] - c0[0], pt[1] - c0[1]
    if rot == 90:
        dx, dy = -dy, dx
    return dx + cx, dy + cy


# ---------------------------------------------------------------------------
# 4. 메인 연산
# ---------------------------------------------------------------------------
def run_nesting(base_dxf: bytes, prod_dxf: bytes, cfg: dict) -> dict:
    base_doc, prod_doc = read_doc(base_dxf), read_doc(prod_dxf)
    warnings = []
    for nm, d in (("베이스", base_doc), ("제품", prod_doc)):
        if d.header.get("$INSUNITS", 0) not in (0, 4):
            warnings.append(f"{nm} 도면 단위($INSUNITS)가 mm 가 아닐 수 있습니다. 단위를 확인하세요.")

    base = parse_cad(base_dxf, tuple(sorted(cfg["base_layers"])), cfg["tol"],
                     cfg.get("base_outline_index", 0))
    prod = parse_cad(prod_dxf, tuple(sorted(cfg["prod_layers"])), cfg["tol"],
                     cfg.get("prod_outline_index", 0), tuple(sorted(cfg["hole_layers"])))

    # 선택 외곽 밖의 주석/별도 투상도를 결과 제품에 복제하지 않는다.
    selected_region = prod.outline.buffer(cfg["tol"])
    omitted = sum(not selected_region.covers(LineString(pts)) for pts, _ in prod.lines)
    prod.lines = [(pts, closed) for pts, closed in prod.lines
                  if selected_region.covers(LineString(pts))]
    prod.circles = [(cx, cy, r, ly) for cx, cy, r, ly in prod.circles
                    if selected_region.covers(Point(cx, cy).buffer(r, quad_segs=32))]
    if omitted:
        warnings.append(f"선택 제품 외곽을 벗어난 선형 요소 {omitted}개를 출력에서 제외했습니다. "
                        "돌출 부품까지 포함한 외곽인지 미리보기를 확인하세요.")

    # (1) 유효 배치 영역: 베이스 외곽에서 edge_margin 만큼 안쪽 (미터 조인으로 모서리 유지)
    usable = base.outline.buffer(-cfg["edge_margin"], join_style=2, mitre_limit=5.0)
    if cfg["use_obstacles"]:
        obs = [g for g in base.others if base.outline.intersects(g)]
        obs += [Point(cx, cy).buffer(r, quad_segs=32) for cx, cy, r, _ in base.circles
                if base.outline.contains(Point(cx, cy))]
        if obs:
            usable = usable.difference(unary_union(obs).buffer(cfg["obst_margin"]))
    if usable.is_empty:
        raise ValueError("유효 배치 영역이 없습니다. (베이스가 너무 작거나 마진이 너무 큼)")

    # (2) 제품 풋프린트 = 외곽선 + 옵셋
    c0 = ((prod.outline.bounds[0] + prod.outline.bounds[2]) / 2, (prod.outline.bounds[1] + prod.outline.bounds[3]) / 2)
    fp = prod.outline.buffer(cfg["offset"], join_style=2, mitre_limit=5.0) if cfg["offset"] > 0 else prod.outline
    fp_c = affinity.translate(fp, -c0[0], -c0[1])
    fp0 = {0: fp_c, 90: affinity.rotate(fp_c, 90, origin=(0, 0))}

    # (3) 피날홀 추출 (제품 외곽 내부 원)
    hole_layers = cfg["hole_layers"]
    holes_rel, other_circles = [], []
    for cx, cy, r, ly in prod.circles:
        is_hole = ((not hole_layers or ly in hole_layers)
                   and cfg["hole_dmin"] <= 2 * r <= cfg["hole_dmax"]
                   and prod.outline.contains(Point(cx, cy)))
        (holes_rel if is_hole else other_circles).append((cx - c0[0], cy - c0[1], 2 * r) if is_hole else (cx, cy, r))
    holes_rel.sort(key=lambda h: (round(h[1], 3), round(h[0], 3)))
    for _, _, d in holes_rel:
        if d - cfg["pin_clear"] <= 0:
            raise ValueError(f"홀 직경 {d:.3f} 이 핀 여유({cfg['pin_clear']})보다 작습니다.")
    if not holes_rel:
        selected_holes = ", ".join(sorted(hole_layers)) if hole_layers else "전체"
        warnings.append(f"피날홀을 찾지 못했습니다. 원 레이어: {selected_holes}. "
                        "선택 외곽 내부의 CIRCLE과 직경 범위를 확인하세요.")

    # (4) 배열
    mode, rot_opt = cfg["mode"], cfg["rot"]
    cand_rots = [0, 90] if rot_opt == "auto" else [int(rot_opt)]
    cells, rot = [], cand_rots[0]
    if mode == "rc":
        cells, bad = manual_layout(usable, fp0[rot], cfg["spacing"], cfg["rows"], cfg["cols"])
        if bad:
            warnings.append(f"지정한 {cfg['rows']}x{cfg['cols']} 중 유효영역을 벗어나는 {bad}개는 제외했습니다.")
    else:
        for r in cand_rots:
            c = auto_layout(usable, fp0[r], cfg["spacing"])
            if len(c) > len(cells):
                cells, rot = c, r
        if mode == "qty":
            if cfg["qty"] > len(cells):
                warnings.append(f"요청 수량 {cfg['qty']} > 최대 {len(cells)}. 최대 수량으로 배치했습니다.")
            cells = cells[: cfg["qty"]]
    if not cells:
        ux0, uy0, ux1, uy1 = usable.bounds
        px0, py0, px1, py1 = prod.outline.bounds
        raise ValueError(f"배치 가능한 제품이 없습니다. 인식 제품: {px1 - px0:.1f} × {py1 - py0:.1f} mm, "
                         f"유효영역: {ux1 - ux0:.1f} × {uy1 - uy0:.1f} mm. "
                         "주석 상자를 외곽으로 선택하지 않았는지, 제품 외곽의 모든 레이어를 함께 "
                         "선택했는지 확인한 뒤 외곽선 후보/회전/마진을 확인하세요.")

    placements = [dict(no=n, row=j + 1, col=i + 1, x=cx, y=cy, rot=rot) for n, (j, i, cx, cy) in enumerate(cells, 1)]
    fps = [affinity.translate(fp0[rot], p["x"], p["y"]) for p in placements]
    min_gap, viol = verify_spacing(fps, cfg["spacing"])
    if viol:
        warnings.append(f"간격 위반 {viol}건 감지 (최소 {min_gap:.3f}mm).")

    return dict(base=base, prod=prod, usable=usable, c0=c0, fp0=fp0, rot=rot, placements=placements,
                holes_rel=holes_rel, other_circles=other_circles, min_gap=min_gap, warnings=warnings,
                cfg=cfg, base_dxf=base_dxf)


def hole_rows(res: dict, origin) -> list:
    rows = []
    for p in res["placements"]:
        for k, (hx, hy, d) in enumerate(res["holes_rel"], 1):
            x, y = tf((hx + res["c0"][0], hy + res["c0"][1]), res["c0"], p["rot"], p["x"], p["y"])
            rows.append(dict(제품번호=p["no"], 홀번호=k, X=round(x - origin[0], 3), Y=round(y - origin[1], 3),
                             홀직경=round(d, 3), 핀직경=round(d - res["cfg"]["pin_clear"], 3)))
    return rows


# ---------------------------------------------------------------------------
# 5. 결과 DXF/DWG 작도
# ---------------------------------------------------------------------------
LAYERS = {"NEST_PRODUCT": 3, "NEST_OFFSET": 2, "NEST_GUIDE_HOLE": 4, "NEST_GUIDE_PIN": 1,
          "NEST_CENTERLINE": 6, "NEST_USABLE": 7, "NEST_TEXT": 7}


def build_result_dxf(res: dict) -> bytes:
    with tempfile.TemporaryDirectory(prefix="jignest_") as tmp:
        src = Path(tmp) / "base.dxf"
        src.write_bytes(res["base_dxf"])
        doc = ezdxf.readfile(str(src))
        for name, color in LAYERS.items():
            if name not in doc.layers:
                doc.layers.add(name, color=color)
        msp = doc.modelspace()
        c0, prod = res["c0"], res["prod"]

        # 유효 배치영역 경계
        polys = list(res["usable"].geoms) if hasattr(res["usable"], "geoms") else [res["usable"]]
        for pg in polys:
            for ring in [pg.exterior, *pg.interiors]:
                msp.add_lwpolyline(list(ring.coords)[:-1], close=True, dxfattribs={"layer": "NEST_USABLE"})

        w, h = (res["fp0"][0].bounds[2] - res["fp0"][0].bounds[0]), (res["fp0"][0].bounds[3] - res["fp0"][0].bounds[1])
        th = max(2.0, min(w, h) * 0.08)
        for p in res["placements"]:
            rot, cx, cy = p["rot"], p["x"], p["y"]
            for pts, closed in prod.lines:                       # 제품 원본 형상
                q = [tf(v, c0, rot, cx, cy) for v in pts]
                msp.add_lwpolyline(q[:-1] if closed else q, close=closed, dxfattribs={"layer": "NEST_PRODUCT"})
            for ccx, ccy, r in res["other_circles"]:
                x, y = tf((ccx, ccy), c0, rot, cx, cy)
                msp.add_circle((x, y), r, dxfattribs={"layer": "NEST_PRODUCT"})
            fp = affinity.translate(res["fp0"][rot], cx, cy)      # +옵셋 윤곽
            msp.add_lwpolyline(list(fp.exterior.coords)[:-1], close=True, dxfattribs={"layer": "NEST_OFFSET"})
            for hx, hy, d in res["holes_rel"]:                    # 피날홀 + 핀 + 센터라인
                x, y = tf((hx + c0[0], hy + c0[1]), c0, rot, cx, cy)
                pin_d = d - res["cfg"]["pin_clear"]
                msp.add_circle((x, y), d / 2, dxfattribs={"layer": "NEST_GUIDE_HOLE"})
                msp.add_circle((x, y), pin_d / 2, dxfattribs={"layer": "NEST_GUIDE_PIN"})
                L = d * 0.8
                msp.add_line((x - L, y), (x + L, y), dxfattribs={"layer": "NEST_CENTERLINE"})
                msp.add_line((x, y - L), (x, y + L), dxfattribs={"layer": "NEST_CENTERLINE"})
            msp.add_text(str(p["no"]), height=th, dxfattribs={"layer": "NEST_TEXT"}).set_placement(
                (cx, cy), align=TextEntityAlignment.MIDDLE_CENTER)
        out = Path(tmp) / "result.dxf"
        doc.saveas(str(out))
        return out.read_bytes()


# ---------------------------------------------------------------------------
# 6. 시각화 / 리포트
# ---------------------------------------------------------------------------
def show_preview(fig, width_px):
    try:
        image = io.BytesIO()
        fig.savefig(image, format="png", dpi=100)
        st.image(image.getvalue(), width=int(width_px))
    finally:
        plt.close(fig)


def make_figure(res: dict, preview_width=400):
    fig, ax = plt.subplots(figsize=(preview_width / 100, preview_width * 7 / 11 / 100))
    base = res["base"]
    ax.plot(*base.outline.exterior.xy, color="k", lw=1.5, label="Base outline")
    for g in base.others:
        if base.outline.intersects(g):
            ax.plot(*g.exterior.xy, color="gray", lw=0.6)
    for pg in (list(res["usable"].geoms) if hasattr(res["usable"], "geoms") else [res["usable"]]):
        ax.plot(*pg.exterior.xy, color="tab:blue", ls="--", lw=1, label="Usable area")
    c0, n = res["c0"], len(res["placements"])
    for p in res["placements"]:
        fp = affinity.translate(res["fp0"][p["rot"]], p["x"], p["y"])
        ax.fill(*fp.exterior.xy, color="tab:green", alpha=0.25)
        out = affinity.translate(affinity.translate(res["prod"].outline, -c0[0], -c0[1]), 0, 0)
        out = affinity.translate(affinity.rotate(out, p["rot"], origin=(0, 0)), p["x"], p["y"])
        ax.plot(*out.exterior.xy, color="tab:green", lw=0.8)
        for hx, hy, d in res["holes_rel"]:
            x, y = tf((hx + c0[0], hy + c0[1]), c0, p["rot"], p["x"], p["y"])
            ax.add_patch(Circle((x, y), d / 2, fill=False, color="tab:red", lw=0.8))
            ax.add_patch(Circle((x, y), (d - res["cfg"]["pin_clear"]) / 2, fill=True, color="tab:blue", alpha=0.6))
        if n <= 150:
            ax.text(p["x"], p["y"], str(p["no"]), ha="center", va="center", fontsize=7)
    h, l = ax.get_legend_handles_labels()
    uniq = dict(zip(l, h))
    ax.legend(uniq.values(), uniq.keys(), loc="upper right", fontsize=8)
    ax.set_aspect("equal")
    ax.grid(alpha=0.2)
    ax.set_xlabel("X (mm)")
    ax.set_ylabel("Y (mm)")
    return fig


def build_excel(summary: list, df_prod: pd.DataFrame, df_hole: pd.DataFrame) -> bytes:
    """J 표준: 맑은 고딕, A열/1행 여백(B2 시작), A4 가로 1페이지 폭 맞춤, 좁은 여백."""
    wb = Workbook()
    wb.remove(wb.active)
    thin = Side(style="thin", color="999999")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    ctr = Alignment(horizontal="center", vertical="center")

    def sheet(name, headers, rows):
        ws = wb.create_sheet(name)
        for j, hd in enumerate(headers):
            c = ws.cell(row=2, column=2 + j, value=hd)
            c.font = Font(name="맑은 고딕", bold=True, color="FFFFFF", size=10)
            c.fill = PatternFill("solid", fgColor="1F4E78")
            c.alignment, c.border = ctr, border
        for i, r in enumerate(rows):
            for j, v in enumerate(r):
                c = ws.cell(row=3 + i, column=2 + j, value=v)
                c.font, c.alignment, c.border = Font(name="맑은 고딕", size=10), ctr, border
                if isinstance(v, float):
                    c.number_format = "0.000"
        for j, hd in enumerate(headers):
            mx = max([len(str(hd))] + [len(str(r[j])) for r in rows]) if rows else len(str(hd))
            ws.column_dimensions[chr(ord("B") + j)].width = min(60, mx * 1.6 + 4)
        ws.column_dimensions["A"].width = 2
        ws.page_setup.orientation = "landscape"
        ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
        ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
        ws.page_margins = PageMargins(left=0.25, right=0.25, top=0.4, bottom=0.4)

    sheet("요약", ["항목", "값"], [list(r) for r in summary])
    sheet("제품 좌표", list(df_prod.columns), df_prod.values.tolist())
    sheet("피날홀 좌표", list(df_hole.columns), df_hole.values.tolist())
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


# ---------------------------------------------------------------------------
# 7. Streamlit UI
# ---------------------------------------------------------------------------
def reset_product_layers():
    suffix = st.session_state.get("active_product_layers_hash")
    if suffix:
        st.session_state[f"prod_layers_selection_{suffix}"] = []
        st.session_state[f"product_component_layers_selection_{suffix}"] = []
        st.session_state[f"prod_outline_choice_{suffix}"] = 0
    st.session_state.pop("result", None)


def main():
    st.set_page_config(page_title="JIG 자동 배열 (로컬)", layout="wide")
    install_offline_guard()
    st.title("JIG 제품 자동 배열 · 피날홀/가이드핀 연산")
    st.caption("🔒 제품 도면은 이 PC 메모리/임시폴더에서 처리합니다. 등록한 BASE JIG만 이 앱 폴더에 로컬 저장되며 외부로 전송되지 않습니다.")

    # ---- 사이드바 -------------------------------------------------------
    sb = st.sidebar
    sb.header("🔒 보안 상태")
    addr = st.get_option("server.address")
    if addr in ("127.0.0.1", "localhost", "::1"):
        sb.success(f"서버 바인딩: {addr} (로컬 전용)")
    else:
        sb.warning("서버가 127.0.0.1 로 고정되지 않았습니다. .streamlit/config.toml 을 확인하세요.")
    if st.get_option("browser.gatherUsageStats"):
        sb.warning("Streamlit 사용 통계 전송이 켜져 있습니다. config.toml 에서 끄세요.")
    sb.info("외부 발신 연결 차단 모듈 작동 중")
    if sb.button("🗑️ 세션 데이터 즉시 삭제"):
        st.session_state.clear()
        parse_cad.clear()
        st.rerun()

    sb.header("⚙️ 변환기")
    exe = sb.text_input("ODA File Converter 경로", value=find_oda_converter())
    ver = sb.selectbox("결과 DWG 버전", ODA_VERSIONS)

    sb.header("📐 설계 파라미터 (mm)")
    relief_min, relief_basis = relief_offset_minimum()
    cfg = dict(
        offset=sb.number_input("제품 외곽 옵셋", 0.0, 5.0, 0.03, 0.01, format="%.3f"),
        bot_relief_offset=sb.number_input(
            "BOT 부품 피난홀 옵셋 (mm)", relief_min, 10.0, relief_min, 0.1,
            format="%.1f", help=f"최소 {relief_min:g}mm — 사내 설계기준 {relief_basis}"),
        edge_margin=sb.number_input("베이스 외곽 마진 (≥20)", 20.0, 500.0, 20.0, 1.0),
        spacing=sb.number_input("제품 간 유격 (≥10)", 10.0, 500.0, 10.0, 1.0),
        pin_clear=sb.number_input("핀 직경 감소량 (홀 - 핀)", 0.01, 2.0, 0.10, 0.01, format="%.2f"),
        tol=sb.number_input("폐곡선 병합 허용오차", 0.0001, 0.1, 0.001, 0.0005, format="%.4f"),
        hole_dmin=sb.number_input("피날홀 최소 직경", 0.1, 100.0, 0.5, 0.1),
        hole_dmax=sb.number_input("피날홀 최대 직경", 0.1, 200.0, 30.0, 0.5),
    )
    sb.header("🧩 배치 방식")
    mode_lbl = sb.radio("모드", ["Auto-Max (최대 수량)", "수동: 행 × 열", "수동: 수량"])
    cfg["mode"] = {"Auto-Max (최대 수량)": "auto", "수동: 행 × 열": "rc", "수동: 수량": "qty"}[mode_lbl]
    if cfg["mode"] == "rc":
        cfg["rows"] = int(sb.number_input("행 (Row)", 1, 200, 2, 1))
        cfg["cols"] = int(sb.number_input("열 (Col)", 1, 200, 4, 1))
    if cfg["mode"] == "qty":
        cfg["qty"] = int(sb.number_input("수량", 1, 5000, 8, 1))
    rot_lbl = sb.selectbox("제품 회전", ["0°", "90°", "자동 (0°/90° 중 최대)"])
    cfg["rot"] = {"0°": "0", "90°": "90"}.get(rot_lbl, "auto")
    cfg["use_obstacles"] = sb.checkbox("베이스 내부 홀/폐곡선을 배치 금지영역으로 처리", False)
    cfg["obst_margin"] = sb.number_input("금지영역 이격", 0.0, 200.0, 20.0, 1.0) if cfg["use_obstacles"] else 0.0

    # ---- 업로드 ---------------------------------------------------------
    c1, c2 = st.columns(2)
    saved_base_jigs = list_base_jig_templates()
    def base_jig_label(kind):
        return f"{base_jig_category(kind)} / {kind}"

    base_dxf = None
    base_choice = None
    if saved_base_jigs:
        base_choice = c1.selectbox("① BASE JIG 유형", saved_base_jigs,
                                   key="base_jig_choice", format_func=base_jig_label)
        try:
            base_dxf = load_base_jig_template(base_choice)
            c1.caption(f"저장된 {base_choice} 도면을 사용합니다.")
        except Exception as error:
            c1.error(f"저장된 BASE JIG를 읽지 못했습니다: {error}")
    else:
        c1.info("등록된 BASE JIG가 없습니다. 아래 등록 창에서 도면을 추가하세요.")
    up_prod = c2.file_uploader("② 제품 (DWG/DXF)", type=["dwg", "dxf"])
    prod_dxf = None
    try:
        if up_prod:
            prod_dxf = load_cad(up_prod, exe, ver)
    except Exception as error:
        st.error(f"제품 파일 변환 오류: {error}")
    # layer 0 위주 도면에만 역할 레이어 자동 구분을 제안한다 (기본 꺼짐 — 입력을 조용히 바꾸지 않는다).
    if prod_dxf and product_layer_zero_share(prod_dxf) > jig_preprocess.LAYER_ZERO_MAJORITY:
        if c2.checkbox("레이어 자동 구분", key="product_auto_layers",
                       help="형상 대부분이 layer 0 인 도면입니다. 외곽·부품·홀을 형상으로 구분해 "
                            "AUTO_OUTLINE / AUTO_COMPONENT / AUTO_HOLE 레이어로 나눕니다."):
            strip_frame = c2.checkbox("도면 테두리 제거", key="product_strip_frame",
                                      help="가장 큰 직사각형이 도면 틀이면 켜세요 (이중 테두리 포함).")
            try:
                prod_dxf, notes = split_product_layers(prod_dxf, strip_frame)
                with c2.expander("레이어 자동 구분 결과"):
                    for note in notes:
                        st.write(f"- {note}")
            except jig_preprocess.PreprocessError as error:
                c2.warning(f"레이어 자동 구분을 할 수 없어 원본 레이어를 씁니다: {error}")
    registration_panel = st.expander("BASE JIG 신규 등록 / 교체", expanded=True)
    with registration_panel:
        registration_panel.caption("새 BASE JIG를 등록하면 이 PC에서 다음 작업부터 선택해 사용할 수 있습니다.")
        upload_base_registration = registration_panel.file_uploader(
            "새 BASE JIG 도면 업로드 (등록용)", type=["dwg", "dxf"], key="base_jig_registration_upload")
        registration_dxf = None
        if upload_base_registration:
            try:
                registration_dxf = load_cad(upload_base_registration, exe, ver)
            except Exception as error:
                st.error(f"BASE JIG 파일 변환 오류: {error}")
        if registration_dxf:
            register_modes = ["신규 유형 등록"] + (["기존 유형 교체"] if saved_base_jigs else [])
            register_mode = st.radio("BASE JIG 등록 방식", register_modes,
                                     horizontal=True, key="base_jig_register_mode")
            if register_mode == "신규 유형 등록":
                register_category = st.selectbox("신규 BASE JIG 분류", BASE_JIG_CATEGORIES,
                                                 key="base_jig_new_category")
                register_kind = st.text_input("신규 BASE JIG 유형명", key="base_jig_new_kind").strip()
            else:
                register_kind = st.selectbox("교체할 BASE JIG 유형", saved_base_jigs,
                                             key="base_jig_register_kind")
                register_category = base_jig_category(register_kind)
            already_registered = register_kind in saved_base_jigs
            replace_key = base_jig_replace_key(register_kind, registration_dxf) if already_registered else None
            overwrite = (st.checkbox("기존 등록 도면을 교체합니다", key=replace_key)
                         if replace_key else False)
            category_matches = (register_kind not in BASE_JIG_TYPE_CATEGORIES
                                or BASE_JIG_TYPE_CATEGORIES[register_kind] == register_category)
            if not category_matches:
                st.warning(f"{register_kind} 유형은 {BASE_JIG_TYPE_CATEGORIES[register_kind]} 분류에 등록해야 합니다.")
            if st.button("BASE JIG 등록", type="secondary",
                         disabled=(not register_kind or not category_matches
                                   or (already_registered and not overwrite))):
                try:
                    path = save_base_jig_template(register_kind, registration_dxf, overwrite=overwrite,
                                                  category=register_category)
                    st.success(f"{register_kind}을 로컬 라이브러리에 등록했습니다: {path.name}")
                    st.rerun()
                except Exception as error:
                    st.error(f"BASE JIG 등록 실패: {error}")
            st.caption("분류별 신규 유형을 추가하거나 등록된 BASE JIG를 교체할 수 있습니다. "
                       "베이스 도면만 앱 폴더에 DXF로 저장하며 제품 도면은 저장하지 않습니다.")
    workflow = st.segmented_control("작업 모드", ["수동 레이어 가공도", "자동 인식 가공도", "기본 배열"],
                                    default="수동 레이어 가공도")
    if workflow is None:
        st.info("작업 모드를 선택하세요.")
        return
    machining_enabled = workflow in ("수동 레이어 가공도", "자동 인식 가공도")
    manual_layers = workflow == "수동 레이어 가공도"
    if manual_layers:
        st.info("TOP/SIDE/BOT 뷰와 정합은 도면에서 확인하고, 각 기능 레이어는 직접 선택합니다. "
                "JIG 왼쪽 상판에는 제품 DP·BOT 부품 THRU·GUIDE PIN 여유공, 오른쪽 하판에는 GUIDE PIN만 배치하고 "
                "원본 제품 3면도는 JIG 왼쪽 빈 공간에 참조도로 표시합니다.")
    elif workflow == "자동 인식 가공도":
        st.info("TOP/SIDE/BOT 뷰와 기능 레이어를 도면에서 추천해 DP·부품 THRU·핀 여유공·핀 가공도를 생성합니다. "
                "추천 레이어는 계산 전에 직접 바꿀 수 있습니다.")
    else:
        st.info("기본 배열은 제품 외곽·부품을 JIG에 배치하고 내부 홀의 핀 위치만 표시합니다. "
                "DP와 부품 THRU는 생성하지 않습니다.")
    if machining_enabled and base_choice and base_jig_category(base_choice) == "SMT수대":
        st.warning("등록된 SMT수대는 단일 베이스판입니다. 좌측 상판·우측 하판 분리 가공도는 두 판이 있는 TAPE JIG에서 사용하세요. "
                   "SMT수대는 기본 배열 모드에서 사용할 수 있습니다.")
        return

    if machining_enabled:
        if base_dxf and prod_dxf:
            try:
                machining_ui(base_dxf, prod_dxf, cfg, exe, ver, manual_layers=manual_layers,
                             base_jig_kind=base_choice)
            except Exception as e:
                st.error(f"가공도 형상 확인 오류: {e}")
        else:
            st.info("등록된 BASE JIG를 선택하고 제품 파일을 업로드하면 TOP/SIDE/BOT 가공도 설정이 표시됩니다.")
        return

    if base_dxf and prod_dxf:
        # layer 0 위주 베이스(SMT 수대 실도면 등)는 판 외곽이 도면 틀과 한 면으로 합쳐진다.
        # 제품과 같은 전처리를 베이스에도 제안한다 (기본 꺼짐). 가공도 모드는 SMT수대가 막혀 있어 기본 배열에만 둔다.
        if (product_layer_zero_share(base_dxf) > jig_preprocess.LAYER_ZERO_MAJORITY
                and c1.checkbox("베이스 레이어 자동 구분", key="base_auto_layers",
                                help="베이스 형상 대부분이 layer 0 입니다. 판 외곽·구멍을 형상으로 구분해 "
                                     "AUTO_OUTLINE / AUTO_HOLE 등의 레이어로 나눕니다.")):
            base_strip = c1.checkbox("베이스 도면 테두리 제거", key="base_strip_frame",
                                     help="베이스 도면에 틀(이중 테두리 포함)이 있으면 켜세요.")
            try:
                base_dxf, base_notes = split_product_layers(base_dxf, base_strip)
                with c1.expander("베이스 레이어 자동 구분 결과"):
                    for note in base_notes:
                        st.write(f"- {note}")
            except jig_preprocess.PreprocessError as error:
                c1.warning(f"베이스 레이어 자동 구분을 할 수 없어 원본 레이어를 씁니다: {error}")
        bl, pl = list_layers(read_doc(base_dxf)), list_layers(read_doc(prod_dxf))
        with st.expander("레이어 선택", expanded=True):
            a, b, c, d = st.columns(4)
            base_default = ([jig_preprocess.OUTLINE] if jig_preprocess.OUTLINE in bl
                            else base_jig_default_layers(base_choice, bl))
            outline_default = ([jig_preprocess.OUTLINE] if jig_preprocess.OUTLINE in pl
                               else ["0"] if "0" in pl else feature_layer_defaults(pl, "OUTLINE"))
            component_default = feature_layer_defaults(pl, "COMPONENT")
            hole_default = feature_layer_defaults(pl, "HOLE")
            base_hash = hashlib.sha256(base_dxf).hexdigest()[:10]
            product_hash = hashlib.sha256(prod_dxf).hexdigest()[:10]
            base_layers_key = f"base_layers_selection_{base_hash}"
            outline_layers_key = f"prod_layers_selection_{product_hash}"
            component_layers_key = f"product_component_layers_selection_{product_hash}"
            hole_layers_key = f"hole_layers_selection_{product_hash}"
            outline_choice_key = f"prod_outline_choice_{product_hash}"
            st.session_state["active_product_layers_hash"] = product_hash
            st.session_state.setdefault(base_layers_key, base_default)
            st.session_state.setdefault(outline_layers_key, outline_default)
            st.session_state.setdefault(component_layers_key, component_default)
            st.session_state.setdefault(hole_layers_key, hole_default)
            cfg["base_layers"] = set(a.multiselect("베이스 외곽 레이어", bl, key=base_layers_key))
            outline_layers = set(b.multiselect("제품 외곽 레이어", pl, key=outline_layers_key))
            component_layers = set(c.multiselect("제품 부품 레이어", pl,
                                                 key=component_layers_key))
            cfg["hole_layers"] = set(d.multiselect("제품 내부 홀 레이어", pl, key=hole_layers_key))
            cfg["prod_layers"] = outline_layers | component_layers
            st.caption("제품 외곽 후보를 미리보기에서 고르세요. 선택 외곽 안의 부품 형상만 배열에 포함합니다. "
                       "BOT 뷰처럼 외곽 밖의 부품은 이 모드에서 제외되며, 부품 THRU 피난홀은 자동 인식 가공도에서 생성합니다. "
                       "내부 홀 레이어는 핀 위치 계산에 사용합니다.")
        st.button("제품 외곽 다시 찾기 (전체 레이어)", on_click=reset_product_layers)
        st.caption("제품 외곽이 제대로 인식되지 않으면 전체 레이어로 다시 찾으세요. 베이스와 내부 홀 레이어 선택은 유지됩니다.")
        try:
            with st.expander("외곽선 확인 및 선택", expanded=True):
                st.caption("주석 상자·다른 투상도가 아닌 실제 외곽을 선택하세요. "
                           "제품의 돌출 부품도 외곽에 포함되어야 합니다.")
                preview = st.columns(2)
                for column, prefix, name, data, layers in (
                    (preview[0], "base", "베이스", base_dxf, cfg["base_layers"]),
                    (preview[1], "prod", "제품", prod_dxf, cfg["prod_layers"]),
                ):
                    circles = tuple(sorted(cfg["hole_layers"])) if prefix == "prod" else None
                    parsed = parse_cad(data, tuple(sorted(layers)), cfg["tol"], circle_layers=circles)
                    outlines = parsed.outlines
                    choice = column.selectbox(f"{name} 외곽선 후보", range(len(outlines)),
                                              format_func=lambda i, gs=outlines: outline_label(i, gs),
                                              key=outline_choice_key if prefix == "prod" else None)
                    cfg[f"{prefix}_outline_index"] = choice
                    selected = outlines[choice]
                    fig, ax = plt.subplots(figsize=(5, 3))
                    ax.fill(*selected.exterior.xy, color="tab:green", alpha=0.25)
                    if prefix == "prod":
                        for cx, cy, radius, ly in parsed.circles:
                            if selected.contains(Point(cx, cy)) and (not cfg["hole_layers"] or ly in cfg["hole_layers"]):
                                ax.add_patch(Circle((cx, cy), radius, fill=False, color="tab:red"))
                    ax.set_aspect("equal")
                    ax.set_xlabel("X (mm)")
                    ax.set_ylabel("Y (mm)")
                    column.pyplot(fig)
                    plt.close(fig)
        except Exception as e:
            st.error(f"외곽선 확인 오류: {e}")
            return
        result_signature = basic_result_signature(base_dxf, prod_dxf, cfg)
        origin_lbl = st.radio("좌표 기준", ["도면 원점 (WCS)", "베이스 외곽 좌측하단"], horizontal=True)

        if st.button("▶ 배열 실행", type="primary"):
            try:
                with st.spinner("배열 및 도면 생성 중..."):
                    res = run_nesting(base_dxf, prod_dxf, cfg)
                    res["origin_lbl"] = origin_lbl
                    res["signature"] = result_signature
                    res["out_dxf"] = build_result_dxf(res)
                    res["out_dwg"] = None
                    if exe:
                        with tempfile.TemporaryDirectory(prefix="jignest_") as tmp:
                            try:
                                res["out_dwg"] = oda_convert(exe, res["out_dxf"], "dxf", "DWG", ver, tmp)
                            except Exception as e:
                                res["warnings"].append(f"DWG 변환 실패(DXF 는 사용 가능): {e}")
                    st.session_state["result"] = res
            except Exception as e:
                st.session_state.pop("result", None)
                st.error(f"연산 오류: {e}")
                st.button("오류 복구: 제품 전체 레이어 다시 선택", on_click=reset_product_layers)

    else:
        result_signature = None
    res = st.session_state.get("result")
    if not res or res.get("signature") != result_signature:
        if not (base_dxf and prod_dxf):
            st.info("BASE JIG를 선택/업로드하고 제품 파일을 업로드한 뒤 [배열 실행]을 누르세요.")
        return

    for w in res["warnings"]:
        st.warning(w)
    bx0, by0 = res["base"].outline.bounds[:2]
    origin = (bx0, by0) if res["origin_lbl"].startswith("베이스") else (0.0, 0.0)
    pl = res["placements"]
    rows_n, cols_n = max(p["row"] for p in pl), max(p["col"] for p in pl)
    df_prod = pd.DataFrame([dict(번호=p["no"], 행=p["row"], 열=p["col"], X=round(p["x"] - origin[0], 3),
                                 Y=round(p["y"] - origin[1], 3), 회전=p["rot"]) for p in pl])
    df_hole = pd.DataFrame(hole_rows(res, origin))
    pins = sorted({(h[2], round(h[2] - res["cfg"]["pin_clear"], 3)) for h in res["holes_rel"]})

    m = st.columns(4)
    m[0].metric("배치 수량", len(pl))
    m[1].metric("행 × 열", f"{rows_n} × {cols_n}")
    m[2].metric("피날홀 총 수", len(df_hole))
    m[3].metric("최소 제품 간격", "-" if res["min_gap"] is None else f"{res['min_gap']:.2f} mm")
    array_check = array_standard_check(res, base_jig_category(base_choice)) if base_choice else None
    if array_check is not None:
        if array_check["ok"] is None:
            st.info(f"사내 기준 {array_check['basis']}(가로 또는 세로 {array_check['need']}배열 가능)은 "
                    "Auto-Max 배치에서 판정합니다.")
        elif array_check["ok"]:
            st.success(f"사내 기준 {array_check['basis']} 충족: 한 줄 최대 {array_check['best']}개 "
                       f"(기준 가로 또는 세로 {array_check['need']}배열 가능).")
        else:
            st.error(f"사내 기준 {array_check['basis']} 위반: 가로 또는 세로로 {array_check['need']}개를 "
                     f"한 줄에 놓을 수 없습니다 (현재 한 줄 최대 {array_check['best']}개, "
                     f"{rows_n} × {cols_n}). 베이스 판 크기·회전·마진을 검토하세요.")
    clamp_check = clamp_clearance_check(res["prod"], base_jig_category(base_choice)) if base_choice else None
    if clamp_check is not None and st.checkbox("자동화 타입 모델 (AOI 설비 클램프 체결)",
                                               key="aoi_clamp_model"):
        if clamp_check["count"]:
            st.warning(f"사내 기준 {clamp_check['basis']}: KIT 외곽 {clamp_check['rim']:g}mm 살대 적용 시 "
                       f"제품 외곽에서 {clamp_check['reach']:g}mm 이내 부품 {clamp_check['count']}개 "
                       f"(가장 가까운 거리 {clamp_check['closest']:.2f} mm) — 클램프 간섭을 확인하세요.")
        else:
            st.success(f"사내 기준 {clamp_check['basis']}: 제품 외곽에서 {clamp_check['reach']:g}mm 이내 "
                       "부품이 없습니다.")
    if pins:
        st.write("**핀 규격:** " + ", ".join(f"홀 Ø{d:.3f} → 핀 Ø{p:.3f}" for d, p in pins))

    t1, t2, t3 = st.tabs(["2D 시각화", "좌표 리포트", "다운로드"])
    with t1:
        preview_width = st.slider("배치 미리보기 너비 (px)", 200, 800, 400, step=100, format="%d px")
        show_preview(make_figure(res, preview_width), preview_width)
    with t2:
        st.subheader("제품 중심 좌표")
        st.dataframe(df_prod, hide_index=True)
        st.subheader("피날홀 중심 좌표 / 핀 규격")
        st.dataframe(df_hole, hide_index=True)
    with t3:
        summary = [("좌표 기준", res["origin_lbl"]), ("배치 수량", len(pl)), ("행 x 열", f"{rows_n} x {cols_n}"),
                   ("제품 회전(°)", res["rot"]), ("외곽 옵셋(mm)", res["cfg"]["offset"]),
                   ("베이스 외곽 마진(mm)", res["cfg"]["edge_margin"]), ("제품 간 유격(mm)", res["cfg"]["spacing"]),
                   ("핀 감소량(mm)", res["cfg"]["pin_clear"])]
        summary += [(f"핀 규격 {k}", f"홀 Ø{d:.3f} → 핀 Ø{p:.3f}") for k, (d, p) in enumerate(pins, 1)]
        xlsx = build_excel(summary, df_prod, df_hole)
        csv_df = pd.concat([df_prod.assign(구분="제품"), df_hole.assign(구분="피날홀")], ignore_index=True)
        d1, d2, d3, d4 = st.columns(4)
        if res["out_dwg"]:
            d1.download_button("결과 DWG", res["out_dwg"], "nesting_result.dwg", "application/acad")
        else:
            d1.caption("DWG 변환 불가 (ODA 경로 확인)")
        d2.download_button("결과 DXF", res["out_dxf"], "nesting_result.dxf", "application/dxf")
        d3.download_button("좌표 리포트 (Excel)", xlsx, "nesting_report.xlsx",
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        d4.download_button("좌표 리포트 (CSV)", csv_df.to_csv(index=False).encode("utf-8-sig"),
                           "nesting_report.csv", "text/csv")
        st.caption("결과 도면 레이어: NEST_PRODUCT / NEST_OFFSET / NEST_GUIDE_HOLE / NEST_GUIDE_PIN / "
                   "NEST_CENTERLINE / NEST_USABLE / NEST_TEXT")


if __name__ == "__main__":
    main()
