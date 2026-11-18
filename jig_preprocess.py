"""레이어 구분이 없는 도면(전부 layer 0)에서 역할을 형상으로 구분하는 전처리기.

고객 도면에는 외곽·부품·홀이 레이어로 나뉘지 않은 것이 있다. app.py 는 역할을
**레이어 이름**으로 고르므로, 이 모듈이 역할 레이어를 입힌 DXF 를 만들어 주면
app.py 는 고치지 않고 기존 화면에서 `AUTO_OUTLINE` 등을 고르면 된다.

    고객 DXF  →  preprocess()  →  역할 레이어가 입혀진 DXF  →  app.py

jigauto/dxf_input.py 의 형상 분류를 shapely 로 옮긴 것이다 (cadquery 불필요).

## 판정 규칙

1. 문자·치수·해치 등은 형상에서 뺀다.
2. 낱개 선은 **끝점 차수가 모두 2 인 덩어리만** 닫힌 루프로 잇는다. 전부 한꺼번에
   노딩하면 치수선·중심선·표제란이 엉뚱한 고리를 만든다 — tape_jig_1.dxf 실측에서
   허위 외곽(면적 45,622)이 나왔고, 이 규칙은 실제 외곽(90x120)을 정확히 냈다.
3. 다른 모든 형상을 감싸는 직사각형은 도면 테두리로 보고 뺀다.
4. 포함관계로 역할을 정한다. 최상위 = 외곽, 그 안의 원 = 홀, 그 안의 나머지 = 부품.

## 하지 않는 것 (측정으로 기각한 방법)

- **선분 길이로 노이즈를 거르지 않는다.** 단품 TEST.dxf 는 선분 길이 중앙값이 0.052mm,
  90% 가 1mm 미만이다 (곡선을 미세 선분으로 근사). 길이로 거르면 실제 형상이 지워진다.
  이어지지 않는 파편은 2번 규칙에서 저절로 빠진다.
- **끝점 공차를 키우지 않는다.** 0.0001~0.05mm 에서 결과가 같고 0.1mm 이상에서는
  노드가 뭉쳐 오히려 루프가 줄어든다.
- **작은 닫힌 형상을 버리지 않는다.** 작은 부품을 빠뜨리면 충돌이고 주서 기호를 부품으로
  잡으면 피난홀이 하나 늘 뿐이다. `Options.min_area` 로 올릴 수는 있다.

## 한계

- 보조선이 형상의 꼭짓점에 닿아 있으면 그 덩어리는 닫힌 루프로 인정되지 않는다.
  조용히 빠지지 않도록 `notes` 에 개수를 남긴다.
- 원은 전부 홀로 분류한다. 원형 부품과 구분하지 않는다 (app.py 의 홀 직경 범위로 거른다).
"""

from __future__ import annotations

import math
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import ezdxf
from ezdxf import path as ezpath
from shapely import STRtree
from shapely.geometry import LineString, Polygon
from shapely.ops import polygonize, unary_union

OUTLINE = "AUTO_OUTLINE"
HOLE = "AUTO_HOLE"
COMPONENT = "AUTO_COMPONENT"
BORDER = "AUTO_BORDER"
ROLES = (OUTLINE, HOLE, COMPONENT, BORDER)
ROLE_COLORS = {OUTLINE: 3, HOLE: 1, COMPONENT: 5, BORDER: 8}

# 형상에서 빼는 개체 (jigauto/dxf_input.py 와 같은 목록)
IGNORED = {
    "TEXT", "MTEXT", "DIMENSION", "LEADER", "MULTILEADER", "HATCH", "ATTRIB", "ATTDEF",
    "POINT", "SOLID", "WIPEOUT", "IMAGE", "VIEWPORT", "MLINE", "XLINE", "RAY", "TOLERANCE",
}


class PreprocessError(Exception):
    """역할을 정할 수 없다. 조용히 넘기지 않고 호출자에게 알린다."""


@dataclass(frozen=True)
class Options:
    join_tol: float = 1e-3   # 끝점을 같은 점으로 보는 거리 (mm)
    flatten: float = 0.01    # 호·스플라인을 직선으로 나누는 오차 (mm)
    min_area: float = 1e-6   # 이보다 작은 닫힌 형상은 버린다 (mm²). 기본은 퇴화 형상만
    strip_frame: bool = False  # 다른 모든 형상을 감싸는 직사각형을 도면 테두리로 뺀다
    # 틀 바로 안의 직사각형이 틀 면적의 이 비율 이상이면 이중 테두리로 보고 함께 뺀다.
    # 근거: SMT 수대 도면 이중 테두리 0.998 (간격 0.26mm). 틀 안 제품은 보통 틀의 일부다.
    double_border_ratio: float = 0.95


@dataclass
class Loop:
    polygon: Polygon
    handles: tuple[str, ...]
    diameter: float | None = None
    role: str = ""
    depth: int = 0
    parent: int | None = None


@dataclass
class Classification:
    loops: list[Loop]
    notes: list[str] = field(default_factory=list)
    unclassified: int = 0    # 형상으로 읽었지만 어느 루프에도 들지 못한 개체 수

    def by_role(self, role: str) -> list[Loop]:
        return [loop for loop in self.loops if loop.role == role]

    def handles(self, role: str) -> set[str]:
        return {h for loop in self.by_role(role) for h in loop.handles}


# --- 읽기 -------------------------------------------------------------------

def _read(msp, opt: Options):
    """(닫힌 단일 개체 루프, 열린 선 [(LineString, handle)], 블록 참조 수, 형상 개체 수)"""
    rings, edges, blocks, seen = [], [], 0, 0
    for entity in msp:
        kind = entity.dxftype()
        if kind == "INSERT":
            blocks += 1      # preprocess() 는 미리 분해해서 넘기므로 여기 오지 않는다
            continue
        if kind in IGNORED:
            continue
        seen += 1
        try:
            pts = [(v.x, v.y) for v in ezpath.make_path(entity).flattening(opt.flatten)]
        except Exception:
            continue
        if len(pts) < 2:
            continue
        handle = entity.dxf.handle
        if len(pts) >= 4 and math.dist(pts[0], pts[-1]) <= opt.join_tol:
            polygon = _valid(Polygon(pts))
            if polygon is not None and polygon.area > opt.min_area:
                diameter = 2 * entity.dxf.radius if kind == "CIRCLE" else None
                rings.append(Loop(polygon, (handle,), diameter))
            continue
        edges.append((LineString(pts), (handle,)))
    return rings, edges, blocks, seen


def _merge_overlaps(edges, tol: float):
    """한 변을 겹쳐 그린 두 직선(끝점이 서로 상대 선 안에 매달림)을 한 선으로 합친다.

    SMT 수대 도면 실측: 판 왼쪽 변이 2.98mm 겹친 두 선이라 고리가 닫히지 않아 240x175 판이
    외곽에서 빠졌다. **겹친 쪽 안쪽 끝이 둘 다 매달린(차수 1) 경우에만** 합친다 — 맞붙은 두
    부품처럼 각 변의 끝이 이미 다른 선에 이어진 경우를 합치면 꼭짓점이 변 중간에 놓여 둘 다 깨진다.
    """
    cells: dict = {}
    degree: dict = {}

    def key(p):
        return (round(p[0] / tol), round(p[1] / tol))

    def node(p):
        k = key(p)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                hit = cells.get((k[0] + dx, k[1] + dy))
                if hit is not None:
                    return hit
        cells[k] = k
        return k

    ends = []
    for line, _h in edges:
        a, b = node(line.coords[0]), node(line.coords[-1])
        degree[a] = degree.get(a, 0) + 1
        degree[b] = degree.get(b, 0) + 1
        ends.append((a, b))

    straight = [i for i, (line, _h) in enumerate(edges) if len(line.coords) == 2 and line.length > tol]
    if not straight:
        return edges
    tree = STRtree([edges[i][0] for i in straight])
    parent = {i: i for i in straight}

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def inside(p, line) -> bool:
        """p 가 line 위에 있되 line 의 끝점은 아니다."""
        from shapely.geometry import Point
        pt = Point(p)
        return (line.distance(pt) <= tol and pt.distance(Point(line.coords[0])) > tol
                and pt.distance(Point(line.coords[-1])) > tol)

    for i in straight:
        line_i = edges[i][0]
        for end, p in ((0, line_i.coords[0]), (1, line_i.coords[-1])):
            if degree[ends[i][end]] != 1:
                continue
            for k in tree.query(line_i.buffer(tol)):
                j = straight[int(k)]
                if j == i:
                    continue
                line_j = edges[j][0]
                # 같은 직선 위여야 한다 (i 의 두 끝이 모두 j 의 연장선 위)
                (x0, y0), (x1, y1) = line_j.coords
                length = line_j.length
                cross = [abs((x1 - x0) * (q[1] - y0) - (y1 - y0) * (q[0] - x0)) / length
                         for q in line_i.coords]
                if max(cross) > tol or not inside(p, line_j):
                    continue
                # j 의 매달린 끝도 i 안에 있어야 한다 (서로 겹친 한 변)
                if any(degree[ends[j][e]] == 1 and inside(q, line_i)
                       for e, q in ((0, line_j.coords[0]), (1, line_j.coords[-1]))):
                    parent[find(i)] = find(j)

    groups: dict = {}
    for i in straight:
        groups.setdefault(find(i), []).append(i)
    merged_away = set()
    out = []
    for members in groups.values():
        if len(members) < 2:
            continue
        pts = [c for i in members for c in edges[i][0].coords]
        (x0, y0), (x1, y1) = edges[members[0]][0].coords
        ux, uy = x1 - x0, y1 - y0
        pts.sort(key=lambda q: (q[0] - x0) * ux + (q[1] - y0) * uy)
        out.append((LineString([pts[0], pts[-1]]),
                    tuple(h for i in members for h in edges[i][1])))
        merged_away.update(members)
    return [e for i, e in enumerate(edges) if i not in merged_away] + out


def _valid(polygon: Polygon) -> Polygon | None:
    if not polygon.is_valid:
        polygon = polygon.buffer(0)
    if polygon.is_empty or polygon.geom_type != "Polygon":
        return None
    return polygon


# --- 선 잇기 ----------------------------------------------------------------

def _chains(edges, tol: float):
    """끝점 차수가 모두 2 인 덩어리만 닫힌 루프로 인정한다 (jigauto `_chains`).

    (닫힌 덩어리, 열린 덩어리, {선 번호: 끝점을 노드 좌표로 맞춘 선}) 을 돌려준다.
    공차 안에서만 일치하는 끝점을 그대로 두면 고리가 닫히지 않는다 — FPCB TEST-2.dwg
    에서 호·스플라인 35개로 된 제품 외곽이 이 때문에 빠졌다 (jigauto `_heal`).
    """
    cells: dict = {}
    points: list[tuple[float, float]] = []

    def node(p) -> int:
        key = (round(p[0] / tol), round(p[1] / tol))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                hit = cells.get((key[0] + dx, key[1] + dy))
                if hit is not None:
                    return hit
        cells[key] = len(points)
        points.append((p[0], p[1]))
        return len(points) - 1

    seen, pairs = set(), []
    for index, (line, _handle) in enumerate(edges):
        coords = line.coords
        a, b = node(coords[0]), node(coords[-1])
        mid = line.interpolate(0.5, normalized=True)
        key = (min(a, b), max(a, b), round(mid.x / tol), round(mid.y / tol))
        if key in seen:      # 겹쳐 그린 같은 선
            continue
        seen.add(key)
        pairs.append((index, a, b))

    parent = list(range(len(points)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for _index, a, b in pairs:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    degree: dict = {}
    groups: dict = {}
    nodes: dict = {}
    healed: dict = {}
    for index, a, b in pairs:
        degree[a] = degree.get(a, 0) + 1
        degree[b] = degree.get(b, 0) + 1
        root = find(a)
        groups.setdefault(root, []).append(index)
        nodes.setdefault(root, set()).update((a, b))
        coords = list(edges[index][0].coords)
        coords[0], coords[-1] = points[a], points[b]
        healed[index] = LineString(coords)

    closed, opened = [], []
    for root, group in groups.items():
        if len(group) >= 2 and all(degree[n] == 2 for n in nodes[root]):
            closed.append(group)
        else:
            opened.append(group)
    return closed, opened, healed


def _loop_from_chain(edges, group, healed, opt: Options) -> Loop | None:
    faces = list(polygonize([healed[index] for index in group]))
    if not faces:
        return None
    polygon = _valid(Polygon(max(faces, key=lambda f: f.area).exterior))
    if polygon is None or polygon.area <= opt.min_area:
        return None
    return Loop(polygon, tuple(h for index in group for h in edges[index][1]))


# --- 테두리·포함관계 --------------------------------------------------------

def _holds(outer, inner, tol: float = 1e-6) -> bool:
    return (outer[0] - tol <= inner[0] and outer[1] - tol <= inner[1]
            and outer[2] + tol >= inner[2] and outer[3] + tol >= inner[3])


def _is_rect(polygon: Polygon) -> bool:
    x0, y0, x1, y1 = polygon.bounds
    return abs(polygon.area - (x1 - x0) * (y1 - y0)) <= max(1e-6, 1e-6 * polygon.area)


def _frame_candidate(loops: list[Loop]) -> tuple[Loop, int] | None:
    """도면에서 가장 큰 형상이 직사각형이고 다른 형상을 품고 있으면 (그 형상, 품은 개수).

    "다른 모든 형상을 감싸야 틀" 조건은 쓰지 않는다. SMT 수대 도면은 틀 바깥에 선으로 그린
    구역 표시 문자(닫힌 글자 획 포함)가 있어 그 조건으로는 틀을 찾지 못했다.
    틀인지 제품인지는 이 함수가 정하지 않는다 — `strip_frame` 으로 사용자가 정한다.
    """
    if len(loops) < 2:
        return None
    biggest = max(loops, key=lambda l: l.polygon.area)
    if not _is_rect(biggest.polygon):
        return None
    box = biggest.polygon.bounds
    held = sum(1 for o in loops if o is not biggest and _holds(box, o.polygon.bounds))
    return (biggest, held) if held else None


def _assign_parents(loops: list[Loop]) -> None:
    """각 루프를 감싸는 가장 작은 루프."""
    tree = STRtree([loop.polygon for loop in loops])
    for index, loop in enumerate(loops):
        best = None
        for other in tree.query(loop.polygon, predicate="covered_by"):
            other = int(other)
            if other == index or loops[other].polygon.area <= loop.polygon.area * (1 + 1e-9):
                continue
            if best is None or loops[other].polygon.area < loops[best].polygon.area:
                best = other
        loop.parent = best
    for loop in loops:
        depth, cursor = 0, loop.parent
        while cursor is not None:
            depth += 1
            cursor = loops[cursor].parent
        loop.depth = depth


def _root(loops: list[Loop], index: int) -> int:
    while loops[index].parent is not None:
        index = loops[index].parent
    return index


def _size(polygon: Polygon) -> str:
    x0, y0, x1, y1 = polygon.bounds
    return f"{x1 - x0:.4g}x{y1 - y0:.4g}"


# --- 본체 -------------------------------------------------------------------

def classify(doc, options: Options | None = None) -> Classification:
    """`doc` 의 모델공간 개체에 역할을 매긴다. `doc` 은 바꾸지 않는다."""
    opt = options or Options()
    loops, edges, blocks, seen = _read(doc.modelspace(), opt)
    edges = _merge_overlaps(edges, opt.join_tol)
    closed, opened, healed = _chains(edges, opt.join_tol)
    failed = 0
    for group in closed:
        loop = _loop_from_chain(edges, group, healed, opt)
        if loop is not None:
            loops.append(loop)
        else:
            failed += 1
    if not loops:
        raise PreprocessError(
            "닫힌 형상을 찾지 못했습니다. 외곽선이 끊겨 있거나 보조선이 꼭짓점에 "
            "닿아 있는지 확인하세요.")

    notes = []
    shapes = list(loops)
    # 틀은 한 겹(이중 테두리면 그 안쪽 테두리까지)만 뺀다. 무조건 연달아 빼면 틀 안의 직사각형
    # 제품까지 "가장 큰 직사각형"이 되어 사라진다 (이전 버전에서 외곽이 하나도 남지 않았다).
    found = _frame_candidate(shapes)
    if found is not None and opt.strip_frame:
        frame, held = found
        frame.role = BORDER
        shapes = [loop for loop in shapes if loop is not frame]
        notes.append(f"가장 큰 직사각형 {_size(frame.polygon)}mm(안에 형상 {held}개)를 도면 테두리로 "
                     f"보고 {BORDER} 로 분리했습니다.")
        while True:
            nxt = _frame_candidate(shapes)
            if nxt is None:
                break
            inner, _held = nxt
            ratio = inner.polygon.area / frame.polygon.area
            if ratio < opt.double_border_ratio or not _holds(frame.polygon.bounds,
                                                             inner.polygon.bounds):
                break
            inner.role = BORDER
            shapes = [loop for loop in shapes if loop is not inner]
            notes.append(f"그 안쪽 직사각형 {_size(inner.polygon)}mm(면적비 {ratio:.3f})도 이중 "
                         f"테두리로 보고 {BORDER} 로 분리했습니다.")
            frame = inner
    elif found is not None:
        frame, held = found
        notes.append(
            f"가장 큰 직사각형 {_size(frame.polygon)}mm 가 형상 {held}개를 품고 있습니다. 제품 외곽으로 "
            "분류했습니다. 도면 테두리라면 strip_frame 옵션을 켜고 다시 실행하세요.")
    _assign_parents(shapes)

    has_child = {loop.parent for loop in shapes if loop.parent is not None}
    for index, loop in enumerate(shapes):
        if loop.diameter is not None and (loop.depth > 0 or index not in has_child):
            loop.role = HOLE      # 다른 형상 안의 원은 안에 원이 또 있어도 홀 (카운터보어)
        elif loop.depth == 0:
            loop.role = OUTLINE
        else:
            loop.role = COMPONENT
    outlines = sorted((i for i, loop in enumerate(shapes) if loop.role == OUTLINE),
                      key=lambda i: -shapes[i].polygon.area)
    if not outlines:
        raise PreprocessError("닫힌 외곽 후보가 없습니다 (원만 있습니다).")

    # 외곽 안의 맞닿은 선 → 부품. 부품은 몸체와 핀처럼 선이 서로 닿게 그려져 단순 루프
    # 규칙에서 빠진다 (FPCB TEST-2.dwg: 부품 선 610개 중 534개). 도면 전체를 노딩하면
    # 치수선·중심선이 허위 외곽을 만들지만, **확정된 외곽의 내부로 범위를 좁히면** 생길 수
    # 있는 것은 여분의 부품뿐이다 (부품 누락 = 충돌, 부품 추가 = 피난홀 하나).
    taken = {h for loop in loops for h in loop.handles}
    order = sorted(healed)
    lines = [healed[i] for i in order]
    recovered = []
    if lines:
        tree = STRtree(lines)
        roots = [_root(shapes, i) for i in range(len(shapes))]
        for oi in outlines:
            inner = [int(k) for k in tree.query(shapes[oi].polygon, predicate="contains")]
            if not inner:
                continue
            known = [shapes[j].polygon for j in range(len(shapes))
                     if shapes[j].role == COMPONENT and roots[j] == oi]
            faces = list(polygonize(unary_union(
                [lines[k] for k in inner] + [p.exterior for p in known])))
            if not faces:
                continue
            extra = unary_union(faces)
            if known:
                extra = extra.difference(unary_union(known))
            for part in getattr(extra, "geoms", [extra]):
                if part.geom_type != "Polygon" or part.area <= max(opt.min_area, opt.join_tol ** 2):
                    continue
                cover = part.buffer(opt.join_tol)
                handles = tuple(h for k in tree.query(cover, predicate="covers")
                                for h in edges[order[int(k)]][1] if h not in taken)
                if handles:
                    taken.update(handles)
                    recovered.append(Loop(part, handles, role=COMPONENT, depth=1, parent=oi))
    loops.extend(recovered)

    inner_count = {i: 0 for i in outlines}
    for index, loop in enumerate(shapes):
        if loop.depth > 0:
            root = _root(shapes, index)
            inner_count[root] = inner_count.get(root, 0) + 1
    for loop in recovered:
        inner_count[loop.parent] += 1
    listed = ", ".join(f"{_size(shapes[i].polygon)}mm(내부 형상 {inner_count[i]}개)"
                       for i in outlines[:6])
    more = f" 외 {len(outlines) - 6}개" if len(outlines) > 6 else ""
    notes.append(f"외곽 후보 {len(outlines)}개: {listed}{more}")
    if recovered:
        notes.append(f"외곽 안의 맞닿은 선에서 부품 형상 {len(recovered)}개를 더 찾았습니다.")
    loose = sum(1 for group in opened
                if len(group) >= 2 and not any(h in taken for i in group for h in edges[i][1]))
    if loose:
        notes.append(
            f"이어진 선 덩어리 {loose}개는 분류하지 않았습니다 (표제란·치수선, 또는 끊긴 외곽). "
            "제품 외곽이 빠졌다면 선이 끊기거나 맞닿은 곳을 확인하세요.")
    lone = sum(1 for group in opened
               if len(group) == 1 and not any(h in taken for h in edges[group[0]][1]))
    if lone:
        notes.append(f"어디에도 이어지지 않는 선 {lone}개는 분류하지 않았습니다 (주서·중심선·파편).")
    if failed:
        notes.append(f"닫힌 덩어리 {failed}개는 면을 만들지 못해 분류하지 않았습니다 (선이 서로 교차).")
    if blocks:
        notes.append(f"블록 참조 {blocks}개의 내부 형상은 분류하지 않았습니다.")
    holes = [loop for loop in shapes if loop.role == HOLE]
    if holes:
        notes.append("원은 전부 홀로 분류했습니다. 원형 부품과 구분하지 않습니다.")
        nested = sum(1 for loop in holes
                     if loop.parent is not None and shapes[loop.parent].role == COMPONENT)
        if nested:
            notes.append(f"그중 {nested}개는 부품 안에 있습니다 (핀 표시일 수 있음).")
    return Classification(loops, notes, unclassified=seen - len(taken))


def apply_role_layers(doc, result: Classification) -> None:
    """분류된 개체의 레이어를 역할 레이어로 바꾼다. `doc` 을 직접 고친다."""
    for role in ROLES:
        if result.by_role(role) and role not in doc.layers:
            doc.layers.add(role, color=ROLE_COLORS[role])
    for loop in result.loops:
        for handle in loop.handles:
            entity = doc.entitydb.get(handle)
            if entity is not None:
                entity.dxf.layer = loop.role


# 이 비율을 넘는 도면에만 전처리를 제안한다. 실측 비율은 layer_zero_share 설명 참조.
LAYER_ZERO_MAJORITY = 0.5


def layer_zero_share(doc, depth: int = 4) -> float:
    """형상 개체(블록 안 포함, 문자·치수 제외) 중 layer 0 에 **저장된** 비율. 형상이 없으면 0.

    전처리를 제안할 도면인지 가르는 데 쓴다. 블록 안 개체는 저장된 레이어를 그대로 센다 —
    app.py 의 레이어 목록도 같은 값을 보기 때문이다. AutoCAD 화면은 블록 안 layer 0 개체를
    블록이 놓인 레이어로 보여 주므로 화면 기준과 다를 수 있다.

    실측(저장값 / 화면값, 2026-11-18): SMT 수대 0.75/0.75, TAPE JIG 0.70~0.72/같음,
    **FPCB TEST-2 0.60/0.01** (부품을 블록 안 layer 0 으로 그림 → 스위치 표시, 전처리 결과 검증됨),
    대형 JIG-1.bak·AR.dwg·치공구 0.40/0.21, 단품.dwg 0.22, 단품 TEST 0.00 (3D 메쉬를 펼친 도면).
    """
    total = zero = 0

    def walk(entities, level):
        nonlocal total, zero
        for entity in entities:
            kind = entity.dxftype()
            if kind == "INSERT":
                if level < depth:
                    try:
                        walk(entity.virtual_entities(), level + 1)
                    except Exception:
                        pass
                continue
            if kind in IGNORED:
                continue
            total += 1
            zero += entity.dxf.layer == "0"

    walk(doc.modelspace(), 0)
    return zero / total if total else 0.0


def _explode_blocks(doc, depth: int = 4) -> int:
    """블록 참조를 낱개 개체로 푼다 (중첩은 `depth` 단계까지). 못 푼 개수를 돌려준다.

    부품이 블록으로 들어 있는 도면이 있다 (FPCB TEST-2.dwg: 부품 18개가 INSERT).
    역할 레이어는 개체마다 입히므로 사본에서 블록을 풀어야 한다.
    """
    msp = doc.modelspace()
    for _ in range(depth):
        inserts = list(msp.query("INSERT"))
        if not inserts:
            return 0
        for insert in inserts:
            try:
                insert.explode()
            except Exception:
                pass
        if len(msp.query("INSERT")) == len(inserts):
            break
    return len(msp.query("INSERT"))


def preprocess(dxf: bytes, options: Options | None = None) -> tuple[bytes, Classification]:
    """DXF 바이트 → (역할 레이어가 입혀진 DXF 바이트, 분류 결과). 입력은 바꾸지 않는다."""
    with tempfile.TemporaryDirectory(prefix="jigpre_") as tmp:
        source = Path(tmp) / "in.dxf"
        source.write_bytes(dxf)
        doc = ezdxf.readfile(source)
        stuck = _explode_blocks(doc)
        result = classify(doc, options)
        if stuck:
            result.notes.append(f"블록 참조 {stuck}개는 분해하지 못해 분류하지 않았습니다.")
        apply_role_layers(doc, result)
        target = Path(tmp) / "out.dxf"
        doc.saveas(target)
        return target.read_bytes(), result
