"""jig_preprocess 검증 — 전부 layer 0 인 도면에서 역할을 형상으로 구분한다."""

import math
import sys
import tempfile
import unittest
from pathlib import Path

import ezdxf
from shapely.ops import unary_union

import jig_preprocess as jp

ROOT = Path(__file__).resolve().parent
FRAMED = jp.Options(strip_frame=True)   # 도면 테두리가 있는 그림에서만 쓴다
APP_DIR = ROOT   # app.py 와 같은 폴더
TAPE_JIG_1 = APP_DIR / "base_jig_templates" / "tape_jig_1.dxf"


def to_bytes(doc) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "m.dxf"
        doc.saveas(path)
        return path.read_bytes()


def read(data: bytes):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "m.dxf"
        path.write_bytes(data)
        return ezdxf.readfile(path)


def rect_lines(msp, x0, y0, x1, y1):
    """낱개 LINE 4개로 그린 직사각형 — 실도면처럼 닫힌 폴리라인이 아니다."""
    pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    return [msp.add_line(pts[i], pts[(i + 1) % 4]) for i in range(4)]


def layer_zero_drawing():
    """외곽·홀·부품·테두리·주서가 전부 layer 0 에 있는 도면."""
    doc = ezdxf.new()
    msp = doc.modelspace()
    made = {
        "border": rect_lines(msp, 0, 0, 300, 200),
        "outline": rect_lines(msp, 50, 50, 150, 110),
        "hole": [msp.add_circle((60, 60), 1.5)],
        "component": [msp.add_lwpolyline(
            [(100, 70), (110, 70), (110, 75), (100, 75)], close=True)],
        # 주서·중심선·파편: 어디에도 이어지지 않는 선
        "noise": [msp.add_line((200, 150), (200.1, 150)),
                  msp.add_line((20, 20), (40, 20)),
                  msp.add_line((70, 80), (130, 80))],
    }
    msp.add_text("TOP", dxfattribs={"insert": (55, 115)})
    return doc, {k: {e.dxf.handle for e in v} for k, v in made.items()}


class RoleClassificationTests(unittest.TestCase):
    def test_roles_are_found_without_layer_names(self):
        doc, made = layer_zero_drawing()
        result = jp.classify(doc, FRAMED)
        self.assertEqual(result.handles(jp.OUTLINE), made["outline"])
        self.assertEqual(result.handles(jp.HOLE), made["hole"])
        self.assertEqual(result.handles(jp.COMPONENT), made["component"])
        self.assertEqual(result.handles(jp.BORDER), made["border"])

    def test_enclosing_rectangle_is_kept_as_outline_by_default(self):
        # 테두리는 추정하지 않는다. "자식이 있으면 테두리" 추정은 핀 표시가 있는 IC 하나로도
        # 제품 외곽을 버렸다. 기본값에서는 외곽으로 두고, 판단을 사용자에게 알린다.
        doc, made = layer_zero_drawing()
        result = jp.classify(doc)
        self.assertFalse(result.by_role(jp.BORDER))
        self.assertTrue(made["border"] <= result.handles(jp.OUTLINE))
        self.assertTrue(any("300x200" in n and "strip_frame" in n for n in result.notes),
                        result.notes)

    def test_outline_geometry_and_hole_diameter_are_reported(self):
        doc, _ = layer_zero_drawing()
        result = jp.classify(doc, FRAMED)
        outline = result.by_role(jp.OUTLINE)[0]
        self.assertEqual(outline.polygon.bounds, (50.0, 50.0, 150.0, 110.0))
        self.assertEqual(outline.depth, 0)
        hole = result.by_role(jp.HOLE)[0]
        self.assertAlmostEqual(hole.diameter, 3.0)
        self.assertEqual(hole.depth, 1)

    def test_unconnected_lines_stay_unclassified(self):
        doc, made = layer_zero_drawing()
        result = jp.classify(doc)
        classified = set().union(*(result.handles(r) for r in jp.ROLES))
        self.assertFalse(classified & made["noise"])

    def test_shape_made_of_sub_millimetre_segments_is_kept(self):
        # 단품 TEST.dxf 는 선분 길이 중앙값이 0.052mm 다(곡선을 미세 선분으로 근사).
        # 선분 길이로 노이즈를 거르면 실제 제품 형상이 지워진다.
        doc = ezdxf.new()
        msp = doc.modelspace()
        plate = {e.dxf.handle for e in rect_lines(msp, 0, 0, 40, 40)}
        n = 400
        ring = [(20 + 5 * math.cos(2 * math.pi * i / n), 20 + 5 * math.sin(2 * math.pi * i / n))
                for i in range(n)]
        fine = {msp.add_line(ring[i], ring[(i + 1) % n]).dxf.handle for i in range(n)}
        self.assertLess(math.dist(ring[0], ring[1]), 0.1)
        result = jp.classify(doc)
        self.assertEqual(result.handles(jp.COMPONENT), fine)
        self.assertEqual(result.handles(jp.OUTLINE), plate)

    def test_rectangular_product_without_frame_is_not_taken_for_a_border(self):
        # 테두리 없이 그린 직사각형 제품: 외곽이 모든 형상을 감싸지만 도면 테두리가 아니다.
        doc = ezdxf.new()
        msp = doc.modelspace()
        plate = {e.dxf.handle for e in rect_lines(msp, 0, 0, 80, 50)}
        msp.add_circle((10, 10), 1.5)
        msp.add_lwpolyline([(30, 20), (40, 20), (40, 26), (30, 26)], close=True)
        result = jp.classify(doc)
        self.assertEqual(result.handles(jp.OUTLINE), plate)
        self.assertFalse(result.by_role(jp.BORDER))

    def test_counterbore_circles_are_both_holes(self):
        # 관통홀 3.5 를 감싸는 카운터보어 6.5 — 둘 다 홀이다.
        doc = ezdxf.new()
        msp = doc.modelspace()
        rect_lines(msp, 0, 0, 80, 50)
        msp.add_circle((20, 20), 3.25)
        msp.add_circle((20, 20), 1.75)
        result = jp.classify(doc)
        self.assertEqual(sorted(l.diameter for l in result.by_role(jp.HOLE)), [3.5, 6.5])

    def test_endpoints_that_differ_within_tolerance_still_close(self):
        # FPCB TEST-2.dwg 실측: 호·스플라인 끝점이 공차 안에서만 일치해 면이 만들어지지
        # 않았고, 35개 선으로 된 제품 외곽이 통째로 빠졌다.
        doc = ezdxf.new()
        msp = doc.modelspace()
        e = 0.0004
        pts = [(0, 0), (50, 0), (50, 30), (0, 30)]
        plate = set()
        for i in range(4):
            x, y = pts[(i + 1) % 4]
            plate.add(msp.add_line(pts[i], (x + e, y - e)).dxf.handle)
        msp.add_circle((10, 10), 1.5)
        result = jp.classify(doc)
        self.assertEqual(result.handles(jp.OUTLINE), plate)

    def test_edge_drawn_as_two_overlapping_lines_still_closes(self):
        # SMT 수대 도면 실측: 판 왼쪽 변이 2.98mm 겹친 두 선으로 그려져 끝점이 매달렸고,
        # 240x175 판이 통째로 외곽에서 빠졌다.
        doc = ezdxf.new()
        msp = doc.modelspace()
        plate = {msp.add_line((0, 0), (80, 0)).dxf.handle,
                 msp.add_line((80, 0), (80, 50)).dxf.handle,
                 msp.add_line((80, 50), (0, 50)).dxf.handle,
                 msp.add_line((0, 50), (0, 21)).dxf.handle,     # 위쪽 조각
                 msp.add_line((0, 24), (0, 0)).dxf.handle}      # 아래쪽 조각 (3mm 겹침)
        msp.add_circle((10, 10), 1.5)
        result = jp.classify(doc)
        self.assertEqual(result.handles(jp.OUTLINE), plate)
        outline = result.by_role(jp.OUTLINE)[0]
        self.assertEqual(outline.polygon.bounds, (0.0, 0.0, 80.0, 50.0))

    def test_touching_shapes_with_overlapping_edges_are_not_merged(self):
        # 맞붙은 두 부품의 변이 겹쳐도 각 변의 끝은 매달려 있지 않다 → 합치지 않는다.
        # 합치면 꼭짓점이 변 중간에 놓여 두 부품 모두 고리가 깨진다.
        doc = ezdxf.new()
        msp = doc.modelspace()
        rect_lines(msp, 0, 0, 100, 100)
        a = {e.dxf.handle for e in rect_lines(msp, 20, 20, 40, 30)}
        b = {e.dxf.handle for e in rect_lines(msp, 30, 30, 50, 40)}   # 아래 변이 a 의 위 변과 겹침
        result = jp.classify(doc)
        parts = sorted(p.polygon.bounds for p in result.by_role(jp.COMPONENT))
        self.assertIn((20.0, 20.0, 40.0, 30.0), parts)
        self.assertIn((30.0, 30.0, 50.0, 40.0), parts)
        self.assertTrue(a | b <= result.handles(jp.COMPONENT))

    def test_every_unclassified_closed_group_is_reported(self):
        # 닫힌 덩어리인데 면을 못 만든 경우가 조용히 사라지면 안 된다.
        # 8자 모양: 모든 끝점 차수가 2 지만 선이 서로 교차한다.
        doc = ezdxf.new()
        msp = doc.modelspace()
        rect_lines(msp, 0, 0, 100, 100)
        bow = [(10, 10), (40, 40), (40, 10), (10, 40)]
        for i in range(4):
            msp.add_line(bow[i], bow[(i + 1) % 4])
        result = jp.classify(doc)
        classified = sum(len(l.handles) for l in result.loops)
        accounted = classified + result.unclassified
        self.assertEqual(accounted, 8)

    def test_frame_is_stripped_even_with_zone_marks_drawn_outside(self):
        # SMT 수대 도면 실측: 틀 바깥에 선으로 그린 구역 표시 문자(높이 4mm)가 있어
        # strip_frame 을 켜도 틀을 빼지 못했다. 사용자가 틀이 있다고 했으면 닫힌 형상 기준으로 뺀다.
        doc = ezdxf.new()
        msp = doc.modelspace()
        frame = {e.dxf.handle for e in rect_lines(msp, 0, 0, 300, 200)}
        plate = {e.dxf.handle for e in rect_lines(msp, 50, 50, 150, 110)}
        msp.add_circle((60, 60), 1.5)
        for x in (40, 90, 140):                                   # 틀 밖 구역 표시 획
            msp.add_line((x, 203), (x + 1.4, 207))
            msp.add_line((x + 1.4, 207), (x + 2.8, 203))
        result = jp.classify(doc, FRAMED)
        self.assertEqual(result.handles(jp.BORDER), frame)
        self.assertEqual(result.handles(jp.OUTLINE), plate)

    def test_double_border_is_stripped_as_one_frame(self):
        # SMT 수대 도면 실측: 0.26mm 간격의 이중 테두리(면적비 0.998). 바깥만 빼면 안쪽 테두리가
        # 외곽 1순위가 되어 240x175 판이 부품으로 밀려났다.
        doc = ezdxf.new()
        msp = doc.modelspace()
        outer = {e.dxf.handle for e in rect_lines(msp, 0, 0, 300, 200)}
        inner = {e.dxf.handle for e in rect_lines(msp, 0.3, 0.3, 299.7, 199.7)}
        plate = {e.dxf.handle for e in rect_lines(msp, 50, 50, 150, 110)}
        msp.add_circle((60, 60), 1.5)
        result = jp.classify(doc, FRAMED)
        self.assertEqual(result.handles(jp.BORDER), outer | inner)
        self.assertEqual(result.handles(jp.OUTLINE), plate)

    def test_frame_stripping_keeps_the_rectangular_product_inside(self):
        # 틀을 뺀 뒤 남은 직사각형 제품이 다시 "가장 큰 직사각형"이 되어도 빼지 않는다.
        doc = ezdxf.new()
        msp = doc.modelspace()
        frame = {e.dxf.handle for e in rect_lines(msp, 0, 0, 300, 200)}
        plate = {e.dxf.handle for e in rect_lines(msp, 50, 50, 150, 110)}
        msp.add_circle((60, 60), 1.5)
        result = jp.classify(doc, FRAMED)
        self.assertEqual(result.handles(jp.BORDER), frame)
        self.assertEqual(result.handles(jp.OUTLINE), plate)

    def test_border_decision_is_always_reported(self):
        doc, _ = layer_zero_drawing()
        notes = jp.classify(doc, FRAMED).notes
        self.assertTrue(any("300x200" in n and jp.BORDER in n for n in notes), notes)

    def test_drawing_without_closed_shape_stops(self):
        doc = ezdxf.new()
        doc.modelspace().add_line((0, 0), (10, 0))
        with self.assertRaisesRegex(jp.PreprocessError, "닫힌"):
            jp.classify(doc)

    def test_component_touched_by_a_line_is_still_recovered(self):
        # 보조선이 부품 꼭짓점에 닿으면 끝점 차수가 3 이 되어 단순 루프 규칙에서 빠진다.
        # 외곽 안에 있는 덩어리는 그 덩어리 안에서만 면을 찾아 부품으로 살린다.
        doc = ezdxf.new()
        msp = doc.modelspace()
        rect_lines(msp, 0, 0, 100, 100)
        body = {e.dxf.handle for e in rect_lines(msp, 20, 20, 60, 50)}
        witness = msp.add_line((60, 50), (90, 80)).dxf.handle
        result = jp.classify(doc)
        parts = result.by_role(jp.COMPONENT)
        self.assertEqual([p.polygon.bounds for p in parts], [(20.0, 20.0, 60.0, 50.0)])
        self.assertEqual(result.handles(jp.COMPONENT), body)
        self.assertNotIn(witness, result.handles(jp.COMPONENT))

    def test_component_drawn_as_body_and_pins_is_one_footprint(self):
        # FPCB TEST-2.dwg 실측: 부품 610개 선 중 534개가 몸체+핀처럼 서로 맞닿아 있어
        # 단순 루프 규칙으로는 분류되지 않았다. 부품 누락은 충돌이다.
        doc = ezdxf.new()
        msp = doc.modelspace()
        rect_lines(msp, 0, 0, 100, 100)
        body = rect_lines(msp, 40, 40, 50, 46)                  # 몸체
        divider = msp.add_line((43, 40), (43, 46))              # 몸체 안 구분선
        pin = [msp.add_line((50, 42), (52, 42)),                # 오른쪽 핀 (ㄷ자, 변 중간에 닿음)
               msp.add_line((52, 42), (52, 44)),
               msp.add_line((52, 44), (50, 44))]
        result = jp.classify(doc)
        footprint = unary_union([p.polygon for p in result.by_role(jp.COMPONENT)])
        self.assertEqual(footprint.bounds, (40.0, 40.0, 52.0, 46.0))
        self.assertAlmostEqual(footprint.area, 10 * 6 + 2 * 2)
        handles = result.handles(jp.COMPONENT)
        self.assertTrue({e.dxf.handle for e in body + pin} <= handles)
        # 구분선은 몸체 안이라 분류되든 안 되든 피난홀 형상은 같다.
        self.assertTrue(handles <= {e.dxf.handle for e in body + pin + [divider]})

    def test_connected_lines_outside_any_outline_never_become_an_outline(self):
        # 표제란 같은 칸 묶음은 면을 이루지만 외곽 후보로 올리면 안 된다.
        # (전체 노딩 방식이 tape_jig_1.dxf 에서 허위 외곽을 만든 것과 같은 위험)
        doc = ezdxf.new()
        msp = doc.modelspace()
        plate = {e.dxf.handle for e in rect_lines(msp, 0, 0, 50, 30)}
        msp.add_circle((10, 10), 1.5)
        # 표제란: 칸마다 끝점을 공유하는 조각 선으로 그린다 (실도면 방식, 차수 3~4 노드).
        grid = [(100, 0), (200, 0), (300, 0), (300, 30), (300, 60), (200, 60), (100, 60),
                (100, 30)]
        title = [msp.add_line(grid[i], grid[(i + 1) % 8]).dxf.handle for i in range(8)]
        title += [msp.add_line(a, (200, 30)).dxf.handle
                  for a in ((200, 0), (300, 30), (200, 60), (100, 30))]
        result = jp.classify(doc)
        self.assertEqual(result.handles(jp.OUTLINE), plate)
        classified = set().union(*(result.handles(r) for r in jp.ROLES))
        self.assertFalse(classified & set(title))
        self.assertTrue(any("이어진 선 덩어리 1개" in n for n in result.notes), result.notes)

    def test_circle_inside_a_component_stays_a_hole_and_is_reported(self):
        # 부품 안의 원을 부품으로 바꾸면 보강판 안의 진짜 홀이 조용히 사라진다.
        # 홀로 두고 개수를 알린다 — app.py 가 홀 직경 하한과 핀-THRU 겹침 경고로 거른다.
        doc = ezdxf.new()
        msp = doc.modelspace()
        rect_lines(msp, 0, 0, 100, 100)
        hole = msp.add_circle((10, 10), 1.5).dxf.handle
        msp.add_lwpolyline([(40, 40), (60, 40), (60, 52), (40, 52)], close=True)
        marker = msp.add_circle((43, 43), 0.5).dxf.handle
        result = jp.classify(doc)
        self.assertEqual(result.handles(jp.HOLE), {hole, marker})
        self.assertTrue(any("1개는 부품 안에" in n for n in result.notes), result.notes)

    def test_outline_candidates_are_listed_with_their_inner_shape_count(self):
        # 빈 A3 사각형 같은 후보를 사용자가 가려낼 수 있어야 한다.
        doc = ezdxf.new()
        msp = doc.modelspace()
        rect_lines(msp, 0, 0, 50, 30)
        msp.add_circle((10, 10), 1.5)
        rect_lines(msp, 1000, 0, 1420, 297)                      # 내부가 빈 큰 사각형
        notes = jp.classify(doc).notes
        self.assertTrue(any("420x297" in n and "내부 형상 0개" in n for n in notes), notes)
        self.assertTrue(any("50x30" in n and "내부 형상 1개" in n for n in notes), notes)


class RoleLayerOutputTests(unittest.TestCase):
    def test_output_dxf_carries_role_layers_and_input_is_untouched(self):
        doc, made = layer_zero_drawing()
        source = to_bytes(doc)
        output, result = jp.preprocess(source, FRAMED)
        self.assertEqual({e.dxf.layer for e in read(source).modelspace()}, {"0"})
        out = read(output)
        layer_of = {e.dxf.handle: e.dxf.layer for e in out.modelspace()}
        for role, key in ((jp.OUTLINE, "outline"), (jp.HOLE, "hole"),
                          (jp.COMPONENT, "component"), (jp.BORDER, "border")):
            self.assertEqual({layer_of[h] for h in made[key]}, {role})
            self.assertIn(role, out.layers)
        self.assertEqual({layer_of[h] for h in made["noise"]}, {"0"})
        self.assertTrue(result.by_role(jp.OUTLINE))

    def test_layer_zero_share_counts_shapes_inside_blocks_and_ignores_text(self):
        doc = ezdxf.new()
        block = doc.blocks.new("CHIP")
        block.add_lwpolyline([(0, 0), (4, 0), (4, 2), (0, 2)], close=True,
                             dxfattribs={"layer": "부품"})
        msp = doc.modelspace()
        rect_lines(msp, 0, 0, 80, 50)                              # layer 0 선 4개
        msp.add_blockref("CHIP", (20, 20))                        # 블록 안 '부품' 1개
        msp.add_text("TOP", dxfattribs={"insert": (5, 55)})       # 문자는 세지 않는다
        self.assertAlmostEqual(jp.layer_zero_share(doc), 4 / 5)
        self.assertEqual(jp.layer_zero_share(ezdxf.new()), 0.0)

    def test_component_inside_a_block_reference_is_classified(self):
        # FPCB TEST-2.dwg 는 부품 18개가 블록 참조(INSERT)다. 블록을 건너뛰면 부품이 빠진다.
        doc = ezdxf.new()
        block = doc.blocks.new("CHIP")
        block.add_lwpolyline([(0, 0), (4, 0), (4, 2), (0, 2)], close=True)
        msp = doc.modelspace()
        rect_lines(msp, 0, 0, 80, 50)
        msp.add_blockref("CHIP", (20, 20))
        msp.add_blockref("CHIP", (40, 20), dxfattribs={"rotation": 90})
        output, result = jp.preprocess(to_bytes(doc))
        parts = result.by_role(jp.COMPONENT)
        self.assertEqual(len(parts), 2)
        self.assertEqual(sorted(p.polygon.bounds for p in parts),
                         [(20.0, 20.0, 24.0, 22.0), (38.0, 20.0, 40.0, 24.0)])
        out = read(output)
        self.assertEqual(len(out.modelspace().query(f'LWPOLYLINE[layer=="{jp.COMPONENT}"]')), 2)
        self.assertFalse(out.modelspace().query("INSERT"))


class RealDrawingTests(unittest.TestCase):
    """실제 한국성전 지그 도면을 전부 layer 0 으로 평탄화해 돌린다."""

    @classmethod
    def setUpClass(cls):
        doc = ezdxf.readfile(TAPE_JIG_1)
        cls.truth = {e.dxf.handle: e.dxf.layer for e in doc.modelspace()}
        cls.circles = [(e.dxf.center.x, e.dxf.center.y, 2 * e.dxf.radius)
                       for e in doc.modelspace() if e.dxftype() == "CIRCLE"]
        for entity in doc.modelspace():
            entity.dxf.layer = "0"
        cls.flat = to_bytes(doc)
        cls.output, cls.result = jp.preprocess(cls.flat)

    def test_main_plate_outline_is_recovered(self):
        outlines = sorted(self.result.by_role(jp.OUTLINE), key=lambda l: -l.polygon.area)
        x0, y0, x1, y1 = outlines[0].polygon.bounds
        self.assertAlmostEqual(x1 - x0, 90.0, places=3)
        self.assertAlmostEqual(y1 - y0, 120.0, places=3)
        # 평탄화 전 정답: 이 외곽을 이루는 개체는 전부 'JIG 외곽' 레이어였다.
        self.assertEqual({self.truth[h] for h in outlines[0].handles}, {"JIG 외곽"})

    def test_holes_inside_main_plate_are_recovered(self):
        outlines = sorted(self.result.by_role(jp.OUTLINE), key=lambda l: -l.polygon.area)
        plate = outlines[0].polygon
        inside = sorted(round(l.diameter, 2) for l in self.result.by_role(jp.HOLE)
                        if plate.contains(l.polygon))
        # 정답은 모듈과 무관하게 원본 CIRCLE 개체에서 직접 센다.
        from shapely.geometry import Point
        expected = sorted(round(d, 2) for x, y, d in self.circles
                          if plate.contains(Point(x, y).buffer(d / 2)))
        self.assertEqual(inside, expected)
        self.assertEqual(inside, [3.0, 3.0, 3.5, 3.5, 3.5, 3.5, 6.5, 6.5, 6.5, 6.5,
                                  13.0, 13.0, 13.0, 13.0])

    def test_annotation_layers_are_not_classified(self):
        classified = set().union(*(self.result.handles(r) for r in jp.ROLES))
        hit = {self.truth[h] for h in classified}
        self.assertFalse(hit & {"HIDDEN", "Center", "BATANG"}, hit)

    def test_app_reads_clean_outline_from_the_role_layer(self):
        # 전처리 결과를 app.py 의 기존 파서에 그대로 넣는다 (app.py 변경 없음).
        sys.path.insert(0, str(APP_DIR))
        import app

        parsed = app.parse_geometry(app.read_doc(self.output), {jp.OUTLINE})
        x0, y0, x1, y1 = parsed.outline.bounds
        self.assertAlmostEqual(x1 - x0, 90.0, places=2)
        self.assertAlmostEqual(y1 - y0, 120.0, places=2)


class RealProductDrawingTests(unittest.TestCase):
    """실제 FPCB 제품 도면(DWG)을 전부 layer 0 으로 평탄화해 돌린다. ODA 가 필요하다.

    합격 기준은 "분류된 개체 수"가 아니라 **피난홀이 실제 부품을 덮는가**다.
    부품 내부 상세선(IC 핀 표시 등)은 미분류여도 피난홀 형상이 같다.
    """

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(APP_DIR))
        import app

        exe = app.find_oda_converter()
        if not exe:
            raise unittest.SkipTest("실제 DWG 검증에는 ODA File Converter 가 필요합니다 "
                                    "(ODA_CONVERTER 환경변수).")
        from ezdxf import path as ezpath
        from shapely.geometry import LineString
        from shapely.ops import polygonize

        with tempfile.TemporaryDirectory() as wd:
            dxf = app.oda_convert(exe, (APP_DIR / "CAD/FPCB TEST-2.dwg").read_bytes(),
                                  "dwg", "DXF", "ACAD2018", wd)
        doc = app.read_doc(dxf)
        # 부품을 블록 안 layer 0 으로 그린 도면 — 저장값 기준 0.60 이라 앱에서 스위치가 뜬다.
        cls.share = jp.layer_zero_share(doc)
        jp._explode_blocks(doc)
        msp = doc.modelspace()
        truth_lines, cls.hole_truth = [], set()
        for e in msp:
            if e.dxftype() in jp.IGNORED:
                continue
            if e.dxf.layer == "내부홀":
                cls.hole_truth.add(e.dxf.handle)
            if e.dxf.layer != "부품":
                continue
            pts = [(v.x, v.y) for v in ezpath.make_path(e).flattening(0.01)]
            if len(pts) >= 2:
                truth_lines.append(LineString(pts))
        # 정답 부품 발자국: 원래 '부품' 레이어 선만으로 면을 만든 것
        cls.truth = unary_union(list(polygonize(unary_union(truth_lines))))
        for e in msp:
            e.dxf.layer = "0"
        cls.result = jp.classify(doc)

    def test_relief_at_company_minimum_covers_every_real_component(self):
        # app.py 는 부품 외곽을 bot_relief_offset(사내 하한 1.0mm, DSG-005-02) 만큼 키워
        # THRU 를 뚫는다. 그 THRU 가 정답 부품 발자국을 빈틈없이 덮어야 한다 (누락 = 충돌).
        got = unary_union([l.polygon for l in self.result.by_role(jp.COMPONENT)])
        self.assertGreater(self.truth.area, 10.0)
        self.assertAlmostEqual(self.truth.difference(got.buffer(1.0, join_style=2)).area,
                               0.0, places=6)
        # 옵셋 없이도 대부분을 직접 잡는다 (2026-11-17 실측 94.9%).
        self.assertGreater(self.truth.intersection(got).area / self.truth.area, 0.9)

    def test_app_offers_the_switch_for_this_drawing(self):
        # AutoCAD 화면으로는 layer 0 이 1% 지만 파일에는 60% 가 layer 0 으로 저장돼 있다.
        # 앱 레이어 목록도 저장값을 보므로 스위치를 제안하는 것이 맞다.
        self.assertGreater(self.share, jp.LAYER_ZERO_MAJORITY)
        self.assertAlmostEqual(self.share, 0.60, places=2)

    def test_all_real_inner_holes_are_found(self):
        self.assertEqual(len(self.hole_truth), 4)
        self.assertTrue(self.hole_truth <= self.result.handles(jp.HOLE))

    def test_both_product_views_are_outline_candidates(self):
        sizes = sorted((round(l.polygon.bounds[2] - l.polygon.bounds[0], 2),
                        round(l.polygon.bounds[3] - l.polygon.bounds[1], 2))
                       for l in self.result.by_role(jp.OUTLINE))
        self.assertEqual(sizes.count((17.74, 20.7)), 2)


class RealLayerZeroJigDrawingTests(unittest.TestCase):
    """SMT 수대 도면: 형상이 layer 0 / HIDDEN / thru 뿐인 실제 도면. 평탄화하지 않고 그대로 쓴다."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(APP_DIR))
        import app

        exe = app.find_oda_converter()
        if not exe:
            raise unittest.SkipTest("실제 DWG 검증에는 ODA File Converter 가 필요합니다 "
                                    "(ODA_CONVERTER 환경변수).")
        with tempfile.TemporaryDirectory() as wd:
            cls.dxf = app.oda_convert(
                exe, (APP_DIR / "CAD/JIG 도면/SMT 수대 도면 (240 x 175 x 3).dwg").read_bytes(),
                "dwg", "DXF", "ACAD2018", wd)
        doc = read(cls.dxf)
        # 정답: 도면 제목의 240 x 175 판 (평면도, R4 모서리). 위치는 도면에서 잰 값.
        from shapely.geometry import box, Point
        cls.plate_box = box(92.16, 148.65, 332.16, 323.65)
        cls.plate_circles = sorted(
            round(2 * e.dxf.radius, 3) for e in doc.modelspace().query("CIRCLE")
            if cls.plate_box.contains(Point(e.dxf.center.x, e.dxf.center.y)))
        cls.result = jp.classify(doc, FRAMED)

    def test_plate_is_the_main_outline(self):
        main = max(self.result.by_role(jp.OUTLINE), key=lambda l: l.polygon.area)
        x0, y0, x1, y1 = main.polygon.bounds
        self.assertAlmostEqual(x1 - x0, 240.0, places=2)
        self.assertAlmostEqual(y1 - y0, 175.0, places=2)
        self.assertLess(main.polygon.area, 240 * 175)        # R4 모서리라 직사각형보다 작다

    def test_every_hole_on_the_plate_is_found(self):
        main = max(self.result.by_role(jp.OUTLINE), key=lambda l: l.polygon.area)
        got = sorted(round(l.diameter, 3) for l in self.result.by_role(jp.HOLE)
                     if main.polygon.contains(l.polygon))
        self.assertTrue(self.plate_circles)
        self.assertEqual(got, self.plate_circles)

    def test_frame_is_reported_as_border(self):
        self.assertTrue(self.result.by_role(jp.BORDER))
        self.assertTrue(any(jp.BORDER in n for n in self.result.notes), self.result.notes)


if __name__ == "__main__":
    unittest.main()
