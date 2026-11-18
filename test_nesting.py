"""Run with: python -m unittest -v test_nesting.py"""
import io
import logging
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import ezdxf
from shapely import affinity
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box
from shapely.ops import unary_union

import app


ROOT = Path(__file__).parent


def config(**changes):
    cfg = dict(base_layers=['JIG 외곽'], prod_layers=['0', '부품'],
               hole_layers=['0'], tol=0.001, edge_margin=20.0,
               offset=0.03, spacing=10.0, pin_clear=0.1,
               hole_dmin=0.5, hole_dmax=30.0, use_obstacles=False,
               obst_margin=0.0, mode='auto', rot='0', prod_outline_index=0)
    cfg.update(changes)
    return cfg


def dxf_bytes(doc):
    stream = io.StringIO()
    doc.write(stream)
    return stream.getvalue().encode('utf-8')


def machining_fixture():
    doc = ezdxf.new()
    top = doc.blocks.new('TOP_VIEW')
    top.add_lwpolyline([(0, 0), (20, 0), (20, 10), (0, 10)], close=True,
                      dxfattribs={'layer': 'TOP_OUTLINE'})
    top.add_circle((4, 4), 1, dxfattribs={'layer': 'TOP_HOLES'})
    top.add_lwpolyline([(9, -1), (11, -1), (11, 11), (9, 11)], close=True,
                      dxfattribs={'layer': 'TAPE'})
    side = doc.blocks.new('SIDE_VIEW')
    side.add_lwpolyline([(0, 0), (20, 0), (20, 5), (10, 5), (10, 3), (0, 3)],
                       close=True, dxfattribs={'layer': 'SIDE_PROFILE'})
    bot = doc.blocks.new('BOT_VIEW')
    bot.add_lwpolyline([(2, 2), (4, 2), (4, 4), (2, 4)], close=True,
                      dxfattribs={'layer': 'BOT_COMPONENT'})
    msp = doc.modelspace()
    for name, position in [('TOP_VIEW', (100, 200)), ('SIDE_VIEW', (300, 400)),
                           ('BOT_VIEW', (500, 600))]:
        msp.add_blockref(name, position)
    msp.add_lwpolyline([(0, 0), (420, 0), (420, 297), (0, 297)], close=True,
                      dxfattribs={'layer': 'FRAME'})
    msp.add_mtext('TOP SIDE BOT', dxfattribs={'layer': 'ANNOTATIONS'})
    return dxf_bytes(doc)


def machining_config(**changes):
    cfg = dict(top_source='BLOCK:TOP_VIEW', side_source='BLOCK:SIDE_VIEW',
               bot_source='BLOCK:BOT_VIEW', top_outline_layers=['TOP_OUTLINE'],
               top_hole_layers=['TOP_HOLES'], side_profile_layers=['SIDE_PROFILE'],
               bot_component_layers=['BOT_COMPONENT'], tape_layers=['TAPE'],
               side_axis='X', side_reverse=False, side_origin=300.0,
               side_top_origin=100.0, side_datum=400.0, side_depth_sign=1,
               side_surface='max', bot_origin=(500.0, 600.0),
               bot_target_origin=(100.0, 200.0), bot_rotation=0,
               bot_mirror_x=False, bot_mirror_y=False, pin_height=1.8,
               bot_relief_offset=1.0, hole_dmin=0.5, hole_dmax=30.0, tol=0.001)
    cfg.update(changes)
    return cfg


def shared_layer_multiview_fixture():
    doc = ezdxf.new()
    for entity, layer, _ in app.named_entities(app.read_doc(machining_fixture()).modelspace()):
        copied = entity.copy()
        copied.dxf.layer = 'TAPE' if layer == 'TAPE' else '0'
        doc.modelspace().add_entity(copied)
    for name, position in [('TOP', (100, 190)), ('SIDE', (300, 390)), ('BOT', (500, 590))]:
        doc.modelspace().add_mtext(name, dxfattribs={'layer': '0', 'insert': position})
    return dxf_bytes(doc)


def region_machining_config(**changes):
    cfg = machining_config(view_mode='regions', top_source='LAYER:0', side_source='LAYER:0',
                           bot_source='LAYER:0', top_region=(99, 198, 121, 212),
                           side_region=(299, 399, 321, 406), bot_region=(501, 601, 505, 605),
                           top_outline_layers=['0'], top_hole_layers=['0'],
                           side_profile_layers=['0'], bot_component_layers=['0'])
    cfg.update(changes)
    return cfg


class MachiningTests(unittest.TestCase):
    def test_base_jig_library_roundtrips_and_requires_confirmation_to_replace(self):
        data = machining_fixture()
        kind = 'TAPE 부착 JIG 2단'
        with TemporaryDirectory() as folder:
            self.assertEqual(app.list_base_jig_templates(folder), [])
            app.save_base_jig_template(kind, data, folder)
            self.assertEqual(app.list_base_jig_templates(folder), [kind])
            self.assertEqual(app.load_base_jig_template(kind, folder), data)
            with self.assertRaises(FileExistsError):
                app.save_base_jig_template(kind, machining_fixture(), folder)
            replacement_doc = app.read_doc(data)
            replacement_doc.modelspace().add_line((0, 0), (1, 1))
            replacement = dxf_bytes(replacement_doc)
            self.assertNotEqual(app.base_jig_replace_key(kind, data),
                                app.base_jig_replace_key(kind, replacement))
            app.save_base_jig_template(kind, replacement, folder, overwrite=True)
            self.assertEqual(app.load_base_jig_template(kind, folder), replacement)
            with self.assertRaisesRegex(ValueError, '분류'):
                app.save_base_jig_template('사용자 입력', data, folder)
        self.assertEqual(app.base_jig_default_layers('TAPE 부착 JIG AR', ['2', '7']), ['2', '7'])
        self.assertEqual(app.base_jig_default_layers('SMT수대', ['0', 'thru']), ['0'])

    def test_base_jig_library_hides_unregistered_files_and_lists_registered_types_by_category(self):
        data = machining_fixture()
        with TemporaryDirectory() as folder:
            for filename in ('tape_jig_1.dxf', 'tape_jig_ar.dxf', 'smt_fixture.dxf'):
                (Path(folder) / filename).write_bytes(data)
            (Path(folder) / 'tape_jig_2.dxf').write_bytes(data)
            self.assertEqual(app.list_base_jig_templates(folder), ['TAPE 부착 JIG 2단'])
            app.save_base_jig_template('TAPE 부착 JIG 1단', data, folder,
                                       category='TAPE 부착 JIG')
            app.save_base_jig_template('TAPE 부착 JIG AR', data, folder,
                                       category='TAPE 부착 JIG')
            app.save_base_jig_template('TAPE 부착 JIG 3단', data, folder,
                                       category='TAPE 부착 JIG')
            app.save_base_jig_template('SMT수대 A', data, folder, category='SMT수대')
            self.assertEqual(app.base_jig_category('TAPE 부착 JIG 3단', folder),
                             'TAPE 부착 JIG')
            self.assertEqual(app.base_jig_category('SMT수대 A', folder), 'SMT수대')
            self.assertCountEqual(app.list_base_jig_templates(folder, category='TAPE 부착 JIG'),
                                  ['TAPE 부착 JIG 1단', 'TAPE 부착 JIG 2단',
                                   'TAPE 부착 JIG AR', 'TAPE 부착 JIG 3단'])
            self.assertEqual(app.load_base_jig_template('TAPE 부착 JIG 3단', folder), data)

    def test_base_jig_registration_ui_saves_and_reloads_new_type(self):
        from streamlit.testing.v1 import AppTest
        data = machining_fixture()

        class Upload(io.BytesIO):
            def __init__(self, content):
                super().__init__(content)
                self.name = 'new_base.dxf'

        with TemporaryDirectory() as folder:
            app_copy = Path(folder) / 'app.py'
            app_copy.write_bytes((ROOT / 'app.py').read_bytes())

            def upload(label, **kwargs):
                return Upload(data) if label == '새 BASE JIG 도면 업로드 (등록용)' else None

            with patch('streamlit.delta_generator.DeltaGenerator.file_uploader', side_effect=upload):
                at = AppTest.from_file(str(app_copy), default_timeout=30).run()
                self.assertFalse(at.exception)
                next(w for w in at.text_input if w.label == '신규 BASE JIG 유형명').set_value(
                    'TAPE 부착 JIG 1단').run()
                next(w for w in at.button if w.label == 'BASE JIG 등록').click().run()
                self.assertFalse(at.exception)
                base_type = next(w for w in at.selectbox if w.label == '① BASE JIG 유형')
                self.assertEqual(base_type.value, 'TAPE 부착 JIG 1단')
                self.assertEqual(base_type.options, ['TAPE 부착 JIG / TAPE 부착 JIG 1단'])
                library = Path(folder) / 'base_jig_templates'
                self.assertEqual(app.list_base_jig_templates(library), ['TAPE 부착 JIG 1단'])
                self.assertEqual(app.load_base_jig_template('TAPE 부착 JIG 1단', library), data)

    def test_basic_result_signature_changes_when_product_or_base_changes(self):
        data = machining_fixture()
        cfg = config(base_layers={'JIG 외곽'}, prod_layers={'0'}, hole_layers={'내부홀'})
        original = app.basic_result_signature(data, data, cfg)
        self.assertNotEqual(original, app.basic_result_signature(data + b'product', data, cfg))
        self.assertNotEqual(original, app.basic_result_signature(data, data + b'base', cfg))
        self.assertEqual(original, app.basic_result_signature(
            data, data, {**cfg, 'bot_relief_offset': 2.0}))

    def test_explicit_regions_isolate_same_layer_views_without_mtext_assignment(self):
        data = shared_layer_multiview_fixture()
        self.assertFalse(any(view in source for source in app.machining_sources(app.read_doc(data))
                             for view in ('TOP', 'SIDE', 'BOT')))
        plan = app.create_machining_plan(data, region_machining_config())
        self.assertEqual(plan['top'].outline.bounds, (100, 200, 120, 210))
        self.assertEqual(sorted(op['depth'] for op in plan['operations'] if op['kind'] == 'DP'), [5])
        self.assertEqual(next(op['geometry'].bounds for op in plan['operations'] if op['kind'] == 'THRU'),
                         (101.0, 201.0, 105.2, 205.2))
        self.assertEqual(len(plan['pins']), 1)
        self.assertAlmostEqual(plan['pins'][0]['diameter'], 1.9)
        self.assertEqual(len(app.region_entities(app.read_doc(data), (99, 198, 121, 212))), 3)

    def test_manual_regions_reject_overlap_missing_bounds_and_absent_role_layers(self):
        cases = [({'side_region': (99, 198, 121, 212)}, '영역'),
                 ({'top_region': None}, '영역'),
                 ({'bot_component_layers': ['TAPE']}, 'BOT.*TAPE')]
        for changes, message in cases:
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, message):
                app.create_machining_plan(shared_layer_multiview_fixture(), region_machining_config(**changes))

    def test_manual_regions_reject_shared_edges_and_corners(self):
        for side in ((10, 0, 20, 10), (10, 10, 20, 20)):
            with self.subTest(side=side), self.assertRaisesRegex(ValueError, '경계'):
                app.validate_view_regions(dict(top_region=(0, 0, 10, 10),
                                                side_region=side, bot_region=(30, 0, 40, 10)))

    def test_vertical_side_profile_uses_user_selected_source_axis(self):
        doc = app.read_doc(machining_fixture())
        side = doc.blocks.get('SIDE_VIEW')
        for entity in list(side):
            side.delete_entity(entity)
        side.add_lwpolyline([(0, 0), (0, 20), (5, 20), (5, 10), (3, 10), (3, 0)],
                           close=True, dxfattribs={'layer': 'SIDE_PROFILE'})
        next(e for e in doc.modelspace().query('INSERT') if e.dxf.name == 'SIDE_VIEW').dxf.insert = (400, 300, 0)
        plan = app.create_machining_plan(dxf_bytes(doc), machining_config(side_profile_axis='Y'))
        self.assertEqual(sorted(op['depth'] for op in plan['operations'] if op['kind'] == 'DP'), [5])

    def test_drawing_labels_and_holes_auto_detect_views_axes_and_alignment(self):
        data = (ROOT / 'CAD/FPCB TEST-2.dxf').read_bytes()
        cfg = app.detect_multiview(app.read_doc(data))
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg['view_mode'], 'regions')
        self.assertEqual(cfg['top_outline_layers'], ['0'])
        self.assertEqual(cfg['top_hole_layers'], ['내부홀'])
        self.assertEqual(cfg['tape_layers'], ['TAPE'])
        self.assertEqual(cfg['side_profile_layers'], ['0'])
        self.assertEqual(cfg['bot_component_layers'], ['부품'])
        self.assertEqual((cfg['side_profile_axis'], cfg['side_axis']), ('Y', 'Y'))
        self.assertEqual((cfg['side_surface'], cfg['side_depth_sign']), ('min', -1))
        self.assertTrue(cfg['bot_mirror_x'])
        self.assertFalse(cfg['bot_mirror_y'])
        self.assertEqual(cfg['bot_rotation'], 0)
        for got, expected in zip(cfg['bot_origin'], (37922.81973051623, -760.7206821738185)):
            self.assertAlmostEqual(got, expected, places=5)
        for got, expected in zip(cfg['bot_target_origin'], (37817.76907464839, -760.7206821737427)):
            self.assertAlmostEqual(got, expected, places=5)
        plan = app.create_machining_plan(data, cfg)
        self.assertEqual(sorted(round(op['depth'], 2) for op in plan['operations'] if op['kind'] == 'DP'),
                         [0.45])
        self.assertEqual(len(plan['pins']), 2)
        thru_geometry = unary_union([op['geometry'] for op in plan['operations'] if op['kind'] == 'THRU'])
        self.assertTrue(thru_geometry.buffer(0.001).covers(plan['pin_clearance']))
        self.assertGreater(thru_geometry.area, 136.545)
        raw_tape = app.polygon_features(
            app.region_entities(app.read_doc(data), cfg['top_region']), cfg['tape_layers'], cfg['tol'])
        self.assertGreater(raw_tape.difference(plan['top'].outline).area, 1e-3)
        self.assertLess(plan['tape'].difference(plan['top'].outline).area, 1e-6)
        dp_geometry = unary_union([op['geometry'] for op in plan['operations'] if op['kind'] == 'DP'])
        self.assertLess(dp_geometry.intersection(plan['tape']).area, 1e-6)

    def test_preview_polygon_path_preserves_tape_holes(self):
        from matplotlib.path import Path as MatplotlibPath
        from matplotlib import pyplot as plt

        geometry = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)],
                           [[(2, 2), (8, 2), (8, 8), (2, 8)]])
        figure, axes = plt.subplots()
        patch = app.add_preview_polygon(axes, geometry, color='red', alpha=0.3)
        plt.close(figure)
        path = patch.get_path()
        closed = [index for index, code in enumerate(path.codes) if code == MatplotlibPath.CLOSEPOLY]
        self.assertEqual(len(closed), 2)
        rings = [path.vertices[:closed[0]], path.vertices[closed[0] + 1:closed[1]]]
        signed_areas = [sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2)
                            in zip(ring, list(ring[1:]) + [ring[0]])) / 2
                        for ring in rings]
        self.assertGreater(signed_areas[0], 0)
        self.assertLess(signed_areas[1], 0)

    def test_auto_detect_requires_unique_labels_and_matching_hole_pattern(self):
        data = (ROOT / 'CAD/FPCB TEST-2.dxf').read_bytes()
        doc = app.read_doc(data)
        self.assertIsNotNone(app.detect_multiview(doc))
        doc.modelspace().add_mtext('TOP', dxfattribs={'insert': (37810.0, -784.35)})
        self.assertIsNone(app.detect_multiview(doc))

        doc = app.read_doc(data)
        for entity in list(doc.modelspace().query('MTEXT')):
            if entity.plain_text().strip().upper() == 'SIDE':
                doc.modelspace().delete_entity(entity)
        self.assertIsNone(app.detect_multiview(doc))

        doc = app.read_doc(data)
        for entity, layer, _ in app.named_entities(doc.modelspace()):
            if layer == '내부홀' and entity.dxftype() == 'CIRCLE' and entity.dxf.center.x > 37900:
                entity.dxf.radius += 0.2
                break
        self.assertIsNone(app.detect_multiview(doc))

    def test_screenshot_files_default_to_auto_detection_without_manual_coordinates(self):
        from streamlit.testing.v1 import AppTest
        logging.getLogger('matplotlib.font_manager').setLevel(logging.ERROR)
        if not app.find_oda_converter():
            self.skipTest('실제 스크린샷 DWG 업로드 검증에는 로컬 ODA File Converter가 필요합니다.')

        class Upload(io.BytesIO):
            def __init__(self, path):
                super().__init__(path.read_bytes())
                self.name = path.name

        def upload(label, **kwargs):
            if label == '새 BASE JIG 도면 업로드 (등록용)':
                return Upload(ROOT / 'CAD/JIG 도면/TAPE 부착 JIG-2.dwg')
            if label.startswith('②'):
                return Upload(ROOT / 'CAD/FPCB TEST-2.dwg')
            return None

        with patch('streamlit.delta_generator.DeltaGenerator.file_uploader', side_effect=upload):
            at = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=90).run()
            self.assertFalse(at.exception)
            self.assertFalse(at.error)
            base_type = next(w for w in at.selectbox if w.label == '① BASE JIG 유형')
            self.assertEqual(base_type.value, 'TAPE 부착 JIG 2단')
            self.assertEqual(len(base_type.options), 1)
            self.assertNotIn('직접 업로드', base_type.options)
            self.assertIn('BASE JIG 신규 등록 / 교체', [w.label for w in at.expander])
            self.assertIn('신규 유형 등록', next(w for w in at.radio
                                                  if w.label == 'BASE JIG 등록 방식').options)
            self.assertIn('TAPE 부착 JIG', next(w for w in at.selectbox
                                                if w.label == '신규 BASE JIG 분류').options)
            self.assertTrue(any(w.label == '신규 BASE JIG 유형명' for w in at.text_input))
            self.assertEqual(at.segmented_control[0].value, '수동 레이어 가공도')
            self.assertEqual(next(w for w in at.selectbox if w.label == 'JIG 왼쪽 상판 외곽').value, 1)
            self.assertEqual(next(w for w in at.selectbox if w.label == 'JIG 오른쪽 하판 외곽').value, 0)
            side_views = next(w for w in at.multiselect if w.label == 'JIG 측면/정면 투영 포함')
            self.assertEqual(len(side_views.options), 4)
            self.assertEqual(len(side_views.value), 4)
            self.assertIn('자동 인식 가공도', at.segmented_control[0].options)
            at.segmented_control[0].set_value('자동 인식 가공도').run()
            self.assertFalse(at.exception)
            self.assertEqual(at.segmented_control[0].value, '자동 인식 가공도')
            self.assertEqual(next(w for w in at.radio if w.label == '뷰 구분 및 정합 방식').value, '도면 자동 인식')
            self.assertIn('인식된 영역 미리보기', [w.label for w in at.expander])
            self.assertFalse({'TOP X 최소', 'TOP X 최대', 'SIDE Y 최소', 'BOT Y 최대',
                              'SIDE 투영 기준 좌표', 'BOT 기준 X', 'BOT 정합 Y'}
                             & {w.label for w in at.number_input})
            self.assertTrue(any('TOP/SIDE/BOT' in message.value and '정합' in message.value
                                for message in at.success))
            relief_offset = next(w for w in at.sidebar.number_input
                                 if w.label == 'BOT 부품 피난홀 옵셋 (mm)')
            self.assertEqual(relief_offset.value, 1.0)
            relief_offset.set_value(2.0).run()
            self.assertFalse(at.exception)
            component = next(w for w in at.multiselect if w.label == 'BOT 부품 외곽 → 피난홀 THRU')
            self.assertIn('부품', component.options)
            self.assertIn('부품', component.value)
            self.assertFalse(any(w.label == '피날홀 원 레이어' for w in at.multiselect))
            next(w for w in at.button if w.label == '가공도 계산 및 배열').click().run()
            self.assertFalse(at.exception)
            self.assertFalse(at.error, [error.value for error in at.error])
            result = at.session_state['machining_result']
            self.assertEqual(result['plan']['cfg']['bot_relief_offset'], 2.0)
            self.assertEqual(sorted(round(op['depth'], 2) for op in result['plan']['operations']
                                     if op['kind'] == 'DP'), [0.45])
            self.assertEqual(len(result['plan']['pins']), 2)
            thru_geometry = unary_union([op['geometry'] for op in result['plan']['operations']
                                         if op['kind'] == 'THRU'])
            self.assertTrue(thru_geometry.buffer(0.001).covers(result['plan']['pin_clearance']))
            self.assertGreater(thru_geometry.area, 136.545)
            self.assertEqual(len(result['nesting']['placements']), 2)
            self.assertTrue(result['dxf'])
            self.assertTrue(result['dwg'])
            outlines = result['nesting']['base'].outlines
            top_index, bottom_index = app.jig_plate_indices(outlines)
            top_plate, bottom_plate = outlines[top_index], outlines[bottom_index]
            self.assertAlmostEqual(top_plate.bounds[0], 3535.022, places=3)
            self.assertAlmostEqual(bottom_plate.bounds[0], 3825.022, places=3)
            output_doc = app.read_doc(result['dxf'])
            toolpaths = [entity for entity in output_doc.modelspace().query('LWPOLYLINE')
                         if entity.dxf.layer.startswith('TOOLPATH_')]
            self.assertFalse(toolpaths)
            machining_notes = output_doc.modelspace().query('MTEXT[layer=="MACHINING_REVIEW"]')
            self.assertTrue(machining_notes)
            self.assertFalse(any('TOOLPATH_' in note.plain_text() for note in machining_notes))
            hole_callouts = list(output_doc.modelspace().query(
                'MTEXT[layer=="PIN_HOLE_CALLOUTS"]'))
            expected_callouts = len(result['plan']['pins']) * len(result['nesting']['placements'])
            self.assertEqual(len(hole_callouts), expected_callouts)
            self.assertTrue(all('제품 홀 Ø' in item.plain_text()
                                and 'GUIDE PIN Ø' in item.plain_text()
                                and 'JIG THRU Ø' in item.plain_text()
                                for item in hole_callouts))
            callout_bounds = [app.ezbbox.extents([item], fast=False) for item in hole_callouts]
            for index, first in enumerate(callout_bounds):
                first_box = box(first.extmin.x, first.extmin.y, first.extmax.x, first.extmax.y)
                self.assertFalse(first_box.intersects(top_plate))
                self.assertFalse(first_box.intersects(bottom_plate))
                for second in callout_bounds[index + 1:]:
                    second_box = box(second.extmin.x, second.extmin.y,
                                     second.extmax.x, second.extmax.y)
                    self.assertFalse(first_box.intersects(second_box))
            section_layers = {"SECTION_TOP_PROFILE", "SECTION_BOTTOM_PROFILE",
                              "SECTION_CUT_MARKS", "SECTION_ANNOTATIONS"}
            self.assertFalse(any(entity.dxf.layer in section_layers
                                 for entity in output_doc.modelspace()))
            self.assertNotIn('DP_KEEP_OUT', {layer.dxf.name for layer in output_doc.layers})
            iso_entities = list(output_doc.modelspace().query('LWPOLYLINE[layer=="ISOMETRIC_REVIEW"]'))
            iso_notes = [entity.plain_text() for entity in output_doc.modelspace().query(
                'MTEXT[layer=="ISOMETRIC_REVIEW"]')]
            self.assertTrue(iso_entities)
            self.assertEqual(len(iso_notes), 2)
            top_iso_note = next(text for text in iso_notes if '상판' in text)
            bottom_iso_note = next(text for text in iso_notes if '하판' in text)
            self.assertIn('3D 솔리드 아님', top_iso_note)
            self.assertIn('두께 6.000 mm', top_iso_note)
            self.assertIn('Z0.450 mm', top_iso_note)
            self.assertIn('두께 6.000 mm', bottom_iso_note)
            self.assertIn('H=1.800 mm', bottom_iso_note)
            dp_notes = [entity.plain_text() for entity in output_doc.modelspace().query(
                'MTEXT[layer=="DP_ANNOTATIONS"]')]
            self.assertTrue(dp_notes)
            self.assertTrue(any('제품 DP 깊이' in text and '0.450 mm' in text
                                and '0.210 mm' not in text for text in dp_notes))
            dp_layers = {entity.dxf.layer for entity in output_doc.modelspace().query('LWPOLYLINE')
                         if entity.dxf.layer.startswith('DP_Z')}
            self.assertEqual(len(dp_layers), 1)
            dp_outlines = [entity for entity in output_doc.modelspace().query('LWPOLYLINE')
                           if entity.dxf.layer.startswith('DP_Z')]
            self.assertEqual(len(dp_outlines), len(result['nesting']['placements']))
            operation_text = [entity for entity in output_doc.modelspace().query('TEXT MTEXT')
                              if entity.dxf.layer.startswith(('DP_Z', 'THRU_'))]
            self.assertFalse(operation_text)
            pin_text = [entity for entity in output_doc.modelspace().query('TEXT MTEXT')
                        if entity.dxf.layer.startswith('GUIDE_PIN')]
            self.assertFalse(pin_text)
            self.assertFalse(any(entity.dxf.layer == 'TAPE_KEEP_OUT'
                                 for entity in output_doc.modelspace()))
            reference_tape = [entity for entity in output_doc.modelspace()
                              if entity.dxf.layer == 'PRODUCT_REFERENCE_TAPE']
            self.assertTrue(reference_tape)
            self.assertLess(output_doc.layers.get('PRODUCT_REFERENCE_TAPE').dxf.color, 0)
            jig_edges = unary_union([LineString([(entity.dxf.start.x, entity.dxf.start.y),
                                                  (entity.dxf.end.x, entity.dxf.end.y)])
                                     for entity in output_doc.modelspace().query('LINE')
                                     if entity.dxf.layer == 'JIG 외곽'])
            self.assertTrue(jig_edges.buffer(0.002).covers(top_plate.boundary))
            self.assertTrue(jig_edges.buffer(0.002).covers(bottom_plate.boundary))
            line_bounds = []
            for entity in output_doc.modelspace().query('LINE'):
                start, end = entity.dxf.start, entity.dxf.end
                line_bounds.append((min(start.x, end.x), min(start.y, end.y),
                                    max(start.x, end.x), max(start.y, end.y)))
            expected_projection_bounds = [(3825.022, 417.755, 3915.022, 423.755),
                                          (3535.022, 427.755, 3605.022, 433.755),
                                          (3635.022, 463.755, 3641.022, 563.755),
                                          (3945.022, 453.755, 3951.022, 573.755)]
            for x0, y0, x1, y1 in expected_projection_bounds:
                edges = [(x0, y0, x0, y1), (x1, y0, x1, y1),
                         (x0, y0, x1, y0), (x0, y1, x1, y1)]
                for edge in edges:
                    self.assertTrue(any(all(abs(actual - target) <= 0.005
                                            for actual, target in zip(bounds, edge))
                                        for bounds in line_bounds), edge)
            pin_circles = [entity for entity in output_doc.modelspace().query('CIRCLE')
                           if entity.dxf.layer.startswith('GUIDE_PIN')]
            self.assertEqual(len(pin_circles), len(result['plan']['pins'])
                             * len(result['nesting']['placements']))
            clearance_circles = list(output_doc.modelspace().query('CIRCLE[layer=="PIN_CLEARANCE_REF"]'))
            self.assertEqual(len(clearance_circles), len(pin_circles))
            expected_clearance_diameters = sorted(
                round(pin['diameter'] + 0.5, 3)
                for pin in result['plan']['pins'] for _ in result['nesting']['placements'])
            self.assertEqual(sorted(round(2 * circle.dxf.radius, 3) for circle in clearance_circles),
                             expected_clearance_diameters)
            for circle in clearance_circles:
                self.assertTrue(top_plate.buffer(0.001).covers(
                    Point(circle.dxf.center.x, circle.dxf.center.y).buffer(circle.dxf.radius)))
            pin_notes = [entity for entity in output_doc.modelspace().query('MTEXT')
                         if entity.dxf.layer == 'PIN_ANNOTATIONS']
            self.assertEqual(len(pin_notes), len(pin_circles))
            note_text = [entity.plain_text() for entity in pin_notes]
            for pin in result['plan']['pins']:
                self.assertTrue(any(f"Ø{pin['diameter']:.3f}" in text
                                    and f"H={pin['height']:.3f} mm" in text for text in note_text))
            for note in pin_notes:
                extents = app.ezbbox.extents([note], fast=False)
                lo, hi = extents.extmin, extents.extmax
                self.assertTrue(bottom_plate.buffer(0.001).covers(box(lo.x, lo.y, hi.x, hi.y)))
            with TemporaryDirectory() as folder:
                roundtrip_dxf = app.oda_convert(app.find_oda_converter(), result['dwg'],
                                                'dwg', 'DXF', 'ACAD2018', folder)
            roundtrip_doc = app.read_doc(roundtrip_dxf)
            self.assertLess(roundtrip_doc.layers.get('PRODUCT_REFERENCE_TAPE').dxf.color, 0)
            self.assertTrue(any(entity.dxf.layer == 'PIN_ANNOTATIONS'
                                for entity in roundtrip_doc.modelspace().query('MTEXT')))
            self.assertTrue(all(bottom_plate.buffer(0.001).covers(
                Point(entity.dxf.center.x, entity.dxf.center.y).buffer(entity.dxf.radius))
                for entity in pin_circles))
            self.assertTrue(all(not top_plate.intersects(
                Point(entity.dxf.center.x, entity.dxf.center.y).buffer(entity.dxf.radius))
                for entity in pin_circles))
            preview_width = next(w for w in at.slider if w.label == '가공도 미리보기 너비 (px)')
            self.assertEqual(preview_width.value, 400)
            operation_shapes = [entity for entity in output_doc.modelspace().query('LWPOLYLINE')
                                if entity.dxf.layer.startswith(('DP_', 'THRU')) and entity.closed]
            thru_shapes = [entity for entity in operation_shapes if entity.dxf.layer.startswith('THRU')]
            self.assertTrue(thru_shapes)
            for entity in operation_shapes:
                polygon = Polygon([(point[0], point[1]) for point in entity.get_points()])
                self.assertTrue(top_plate.buffer(0.001).covers(polygon))
                self.assertFalse(bottom_plate.intersects(polygon))
            preview_width.set_value(200).run()
            self.assertFalse(at.exception)
            self.assertEqual(next(w for w in at.slider if w.label == '가공도 미리보기 너비 (px)').value, 200)
            next(w for w in at.selectbox if w.label == '① BASE JIG 유형').set_value(
                'TAPE 부착 JIG 2단').run()
            self.assertFalse(at.exception)
            self.assertTrue(any('저장된 TAPE 부착 JIG 2단' in w.value for w in at.caption))
            next(w for w in at.button if w.label == '가공도 계산 및 배열').click().run()
            self.assertFalse(at.exception)
            self.assertTrue(at.session_state['machining_result']['dxf'])
            at.segmented_control[0].set_value('기본 배열').run()
            self.assertFalse(at.exception)
            self.assertTrue(any(w.label == '제품 내부 홀 레이어' for w in at.multiselect))
            self.assertTrue(any('부품 THRU' in notice.value and '생성하지 않습니다' in notice.value for notice in at.info))

    def test_role_layer_not_in_selected_view_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'BOT.*TOP_OUTLINE'):
            app.create_machining_plan(machining_fixture(),
                                      machining_config(bot_component_layers=['BOT_COMPONENT', 'TOP_OUTLINE']))

    def test_korean_feature_layer_alias_recommendations(self):
        layers = ['제품 외곽', '제품 내부 홀', '부품', 'TAPE', '테이프',
                  '단차', '깊이', '프로파일', '도면틀', '치수']
        expected = {'OUTLINE': ['제품 외곽'], 'HOLE': ['제품 내부 홀'],
                    'COMPONENT': ['부품'], 'TAPE': ['TAPE', '테이프'],
                    'PROFILE': ['단차', '깊이', '프로파일']}
        for feature, selected in expected.items():
            with self.subTest(feature=feature):
                self.assertEqual(app.feature_layer_defaults(layers, feature), selected)

    def test_ui_recommends_korean_feature_layers_without_removing_manual_selection(self):
        from streamlit.testing.v1 import AppTest
        logging.getLogger('matplotlib.font_manager').setLevel(logging.ERROR)
        doc = app.read_doc(machining_fixture())
        aliases = {'TOP_OUTLINE': '제품 외곽', 'TOP_HOLES': '제품 내부 홀',
                   'SIDE_PROFILE': '단차 프로파일', 'BOT_COMPONENT': '부품', 'TAPE': '테이프'}
        for block in doc.blocks:
            for entity in block:
                if entity.dxf.layer in aliases:
                    entity.dxf.layer = aliases[entity.dxf.layer]
        data = dxf_bytes(doc)
        base = ezdxf.new()
        base.modelspace().add_lwpolyline([(0, 0), (200, 0), (200, 200), (0, 200)], close=True)
        base.modelspace().add_lwpolyline([(300, 0), (500, 0), (500, 200), (300, 200)], close=True)

        class Upload(io.BytesIO):
            def __init__(self, contents):
                super().__init__(contents)
                self.name = 'korean_layers.dxf'

        def upload(label, **kwargs):
            return Upload(data) if label.startswith('②') else None

        with patch('streamlit.delta_generator.DeltaGenerator.file_uploader', side_effect=upload):
            at = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=60).run()
            at.segmented_control[0].set_value('자동 인식 가공도').run()
            self.assertEqual(at.segmented_control[0].value, '자동 인식 가공도')
            self.assertFalse(at.exception)
            actual = {w.label: w.value for w in at.multiselect}
            self.assertEqual(actual['TOP 제품 외곽 레이어'], ['제품 외곽'])
            self.assertEqual(actual['TOP 내부 홀 → 가이드핀 위치'], ['제품 내부 홀'])
            self.assertEqual(actual['TOP TAPE 가공 금지 레이어'], ['테이프'])
            self.assertEqual(actual['SIDE 단차 프로파일 레이어'], ['단차 프로파일'])
            self.assertEqual(actual['BOT 부품 외곽 → 피난홀 THRU'], ['부품'])
            next(w for w in at.multiselect if w.label == 'TOP 제품 외곽 레이어').set_value([]).run()
            self.assertFalse(at.exception)
            self.assertEqual(next(w for w in at.multiselect if w.label == 'TOP 제품 외곽 레이어').value, [])

    def test_rotated_scaled_nested_block_transforms_and_layer_zero_inheritance(self):
        doc = app.read_doc(machining_fixture())
        child = doc.blocks.new('LOCATING_HOLE')
        child.add_circle((0, 0), 0.5)
        doc.blocks.get('TOP_VIEW').add_blockref('LOCATING_HOLE', (6, 4),
                                               dxfattribs={'layer': 'TOP_HOLES'})
        insert = next(e for e in doc.modelspace().query('INSERT') if e.dxf.name == 'TOP_VIEW')
        insert.dxf.rotation = 90
        insert.dxf.xscale = insert.dxf.yscale = 2
        entries = app.source_entities(doc, 'BLOCK:TOP_VIEW')
        selected = app.feature_doc(entries, ['TOP_OUTLINE', 'TOP_HOLES'])
        top = app.parse_geometry(selected, ['TOP_OUTLINE'], circle_layers=['TOP_HOLES'])
        self.assertEqual(top.outline.bounds, (80.0, 200.0, 100.0, 240.0))
        self.assertTrue(any(abs(x - 92) < 1e-6 and abs(y - 212) < 1e-6 and abs(radius - 1) < 1e-6
                            for x, y, radius, layer in top.circles if layer == 'TOP_HOLES'))

    def test_minsert_component_array_preserves_every_instance(self):
        doc = app.read_doc(machining_fixture())
        insert = next(e for e in doc.modelspace().query('INSERT') if e.dxf.name == 'BOT_VIEW')
        insert.dxf.row_count = insert.dxf.column_count = 2
        insert.dxf.row_spacing = insert.dxf.column_spacing = 10
        plan = app.create_machining_plan(dxf_bytes(doc), machining_config())
        thru = next(op['geometry'] for op in plan['operations'] if op['kind'] == 'THRU')
        self.assertEqual(len(list(thru.geoms)) if hasattr(thru, 'geoms') else 1, 4)
        self.assertEqual(thru.bounds, (101.0, 201.0, 115.0, 215.0))

    def test_named_blocks_steps_relief_tape_and_pins(self):
        plan = app.create_machining_plan(machining_fixture(), machining_config())
        self.assertEqual(plan['top'].outline.bounds, (100.0, 200.0, 120.0, 210.0))
        dp = [op for op in plan['operations'] if op['kind'] == 'DP']
        self.assertEqual(sorted(op['depth'] for op in dp), [5.0])
        self.assertTrue(dp[0]['geometry'].covers(Point(102, 205)))
        self.assertTrue(all(op['geometry'].intersection(plan['tape']).area < 1e-8
                            for op in plan['operations']))
        self.assertAlmostEqual(min(op['geometry'].bounds[0] for op in dp), 99.97, places=3)
        thru = next(op for op in plan['operations'] if op['kind'] == 'THRU')
        self.assertEqual(thru['geometry'].bounds, (101.0, 201.0, 105.2, 205.2))
        pin = plan['pins'][0]
        self.assertAlmostEqual(pin['diameter'], 1.9)
        self.assertEqual(pin['height'], 1.8)
        self.assertEqual((pin['x'], pin['y']), (104.0, 204.0))
        self.assertTrue(any('핀' in warning and 'THRU' in warning for warning in plan['warnings']))

    def test_thru_geometry_rounds_to_tool_radius_and_merges_only_close_regions(self):
        close_regions = unary_union([box(0, 0, 10, 10), box(11.4, 0, 21.4, 10)])
        close_result = app.prepare_thru_geometry(close_regions, tol=0.001)
        self.assertEqual(close_result.geom_type, 'Polygon')
        self.assertFalse(close_result.covers(Point(0.1, 0.1)))
        self.assertTrue(close_result.covers(Point(0.15, 0.15)))
        distant_regions = unary_union([box(0, 0, 10, 10), box(11.51, 0, 21.51, 10)])
        distant_result = app.prepare_thru_geometry(distant_regions, tol=0.001)
        self.assertEqual(distant_result.geom_type, 'MultiPolygon')
        self.assertEqual(len(distant_result.geoms), 2)

    def test_thru_geometry_fills_narrow_recess_before_rounding(self):
        def notch(opening):
            return Polygon([(0, 0), (10, 0), (10, 10), (6 + opening, 10),
                            (6 + opening, 8), (6, 8), (6, 10), (0, 10)])

        connected = app.prepare_thru_geometry(notch(1.49), tol=0.001)
        at_limit = app.prepare_thru_geometry(notch(1.5), tol=0.001)
        separate = app.prepare_thru_geometry(notch(1.51), tol=0.001)
        self.assertTrue(connected.covers(Point(6.745, 9.0)))
        self.assertFalse(at_limit.covers(Point(6.75, 9.0)))
        self.assertFalse(separate.covers(Point(6.755, 9.0)))

    def test_tape_stays_out_of_dp_without_outputting_the_gray_helper_layer(self):
        doc = app.read_doc(machining_fixture())
        top = doc.blocks.get('TOP_VIEW')
        for entity in list(top.query('LWPOLYLINE[layer=="TAPE"]')):
            top.delete_entity(entity)
        top.add_lwpolyline([(9, -1), (11, -1), (11, 11), (9, 11)], close=True,
                           dxfattribs={'layer': 'TAPE'})
        plan = app.create_machining_plan(dxf_bytes(doc), machining_config())
        base = ezdxf.new()
        base.modelspace().add_lwpolyline([(0, 0), (120, 0), (120, 100), (0, 100)], close=True)
        base.modelspace().add_lwpolyline([(300, 0), (420, 0), (420, 100), (300, 100)], close=True)
        output = app.read_doc(app.build_machining_dxf(
            dxf_bytes(base), plan, [dict(no=1, x=50, y=50, rot=0)], (110, 205),
            base_outline=box(0, 0, 120, 100), pin_outline=box(300, 0, 420, 100)))
        polylines = list(output.modelspace().query('LWPOLYLINE'))
        dp_entities = [entity for entity in polylines if entity.dxf.layer.startswith('DP_Z')]
        keepout_entities = [entity for entity in polylines if entity.dxf.layer == 'DP_KEEP_OUT']
        self.assertTrue(dp_entities)
        self.assertEqual(len(dp_entities), 1)
        self.assertFalse(keepout_entities)
        self.assertNotIn('DP_KEEP_OUT', {layer.dxf.name for layer in output.layers})
        dp_geometry = unary_union([operation['geometry'] for operation in plan['operations']
                                   if operation['kind'] == 'DP'])
        self.assertLess(dp_geometry.intersection(plan['tape']).area, 1e-8)
        nominal_dp = plan['top'].outline.buffer(0.03, join_style=2)
        self.assertLess(plan['display_dp'].symmetric_difference(nominal_dp).area, 1e-6)
        expected = app.affinity.translate(
            app.affinity.translate(nominal_dp, xoff=-110, yoff=-205),
            xoff=50, yoff=50)
        expected_outline = Polygon(expected.exterior.coords)
        actual_outline = Polygon(dp_entities[0].get_points('xy'))
        self.assertLess(actual_outline.symmetric_difference(expected_outline).area, 1e-6)

    def test_dp_outline_remains_at_bot_thru_without_added_side_section(self):
        plan = app.create_machining_plan(
            machining_fixture(), machining_config(bot_target_origin=(100.0, 198.0)))
        base = ezdxf.new()
        msp = base.modelspace()
        msp.add_lwpolyline([(0, 0), (120, 0), (120, 100), (0, 100)], close=True)
        msp.add_lwpolyline([(300, 0), (420, 0), (420, 100), (300, 100)], close=True)
        projections = [box(0, -10, 120, -4), box(125, 0, 131, 100),
                       box(300, -10, 420, -4), box(425, 0, 431, 100)]
        for projection in projections:
            msp.add_lwpolyline(list(projection.exterior.coords)[:-1], close=True,
                               dxfattribs={'layer': 'JIG_SIDE'})
        output = app.read_doc(app.build_machining_dxf(
            dxf_bytes(base), plan, [dict(no=1, x=50, y=50, rot=0)], (110, 205),
            base_outline=box(0, 0, 120, 100), pin_outline=box(300, 0, 420, 100),
            side_view_outlines=projections))
        msp = output.modelspace()
        dp_entities = [entity for entity in msp.query('LWPOLYLINE')
                       if entity.dxf.layer.startswith('DP_Z')]
        self.assertTrue(dp_entities)
        # DP remains the complete product shape even where the relief crosses
        # the TOP outer edge at y=44.97 after placement.
        self.assertTrue(any(
            abs(start[1] - 44.97) < 1e-4 and abs(end[1] - 44.97) < 1e-4
            and min(start[0], end[0]) < 43.0 < max(start[0], end[0])
            for entity in dp_entities
            for points in [list(entity.get_points('xy'))]
            for start, end in zip(points, points[1:] + (points[:1] if entity.closed else []))))
        section_layers = {"SECTION_TOP_PROFILE", "SECTION_BOTTOM_PROFILE",
                          "SECTION_CUT_MARKS", "SECTION_ANNOTATIONS"}
        self.assertFalse(any(entity.dxf.layer in section_layers for entity in msp))
        self.assertEqual(len(msp.query('LWPOLYLINE[layer=="JIG_SIDE"]')), 4)

    def test_dp_display_unions_touching_outline_parts_without_internal_seam(self):
        plan = app.create_machining_plan(machining_fixture(), machining_config())
        plan["display_dp"] = MultiPolygon([box(0, 0, 10, 10), box(10, 0, 20, 10)])
        machining_dp_area = next(operation["geometry"].area for operation in plan["operations"]
                                 if operation["kind"] == "DP")
        base = ezdxf.new()
        base.modelspace().add_lwpolyline([(0, 0), (120, 0), (120, 100), (0, 100)], close=True)
        base.modelspace().add_lwpolyline([(300, 0), (420, 0), (420, 100), (300, 100)], close=True)
        output = app.read_doc(app.build_machining_dxf(
            dxf_bytes(base), plan, [dict(no=1, x=50, y=50, rot=0)], (110, 205),
            base_outline=box(0, 0, 120, 100), pin_outline=box(300, 0, 420, 100)))
        dp_outlines = [entity for entity in output.modelspace().query('LWPOLYLINE')
                       if entity.dxf.layer.startswith('DP_Z')]
        self.assertEqual(len(dp_outlines), 1)
        points = list(dp_outlines[0].get_points('xy'))
        self.assertAlmostEqual(Polygon(points).area, 200.0)
        min_x = min(x for x, _ in points)
        middle_x = min_x + 10.0
        self.assertFalse(any(abs(x0 - x1) < 1e-6 and abs(x0 - middle_x) < 1e-6
                             and abs(abs(y1 - y0) - 10.0) < 1e-6
                             for (x0, y0), (x1, y1) in zip(points, points[1:] + points[:1])))
        self.assertAlmostEqual(next(operation["geometry"].area for operation in plan["operations"]
                                    if operation["kind"] == "DP"), machining_dp_area)

    def test_internal_hole_callouts_show_hole_pin_and_thru_diameters(self):
        plan = app.create_machining_plan(machining_fixture(), machining_config())
        base = ezdxf.new()
        base.modelspace().add_lwpolyline([(0, 0), (120, 0), (120, 100), (0, 100)], close=True)
        base.modelspace().add_lwpolyline([(300, 0), (420, 0), (420, 100), (300, 100)], close=True)
        output = app.read_doc(app.build_machining_dxf(
            dxf_bytes(base), plan, [dict(no=1, x=50, y=50, rot=0)], (110, 205),
            base_outline=box(0, 0, 120, 100), pin_outline=box(300, 0, 420, 100)))
        callouts = list(output.modelspace().query('MTEXT[layer=="PIN_HOLE_CALLOUTS"]'))
        leaders = list(output.modelspace().query('LINE[layer=="PIN_HOLE_CALLOUTS"]'))
        self.assertEqual(len(callouts), len(plan['pins']))
        self.assertEqual(len(leaders), len(plan['pins']))
        for pin, callout in zip(plan['pins'], callouts):
            label = callout.plain_text()
            self.assertIn(f"제품 홀 Ø{pin['hole_diameter']:.3f}", label)
            self.assertIn(f"GUIDE PIN Ø{pin['diameter']:.3f}", label)
            self.assertIn(f"JIG THRU Ø{pin['diameter'] + 0.5:.3f}", label)

    def test_hole_callouts_avoid_selected_jig_side_projection(self):
        plan = app.create_machining_plan(machining_fixture(), machining_config())
        base = ezdxf.new()
        msp = base.modelspace()
        msp.add_lwpolyline([(0, 0), (120, 0), (120, 100), (0, 100)], close=True)
        msp.add_lwpolyline([(300, 0), (420, 0), (420, 100), (300, 100)], close=True)
        side_projection = box(125, 30, 131, 70)
        msp.add_lwpolyline(list(side_projection.exterior.coords)[:-1], close=True,
                           dxfattribs={'layer': 'JIG_SIDE'})
        output = app.read_doc(app.build_machining_dxf(
            dxf_bytes(base), plan, [dict(no=1, x=50, y=50, rot=0)], (110, 205),
            base_outline=box(0, 0, 120, 100), pin_outline=box(300, 0, 420, 100),
            side_view_outlines=[side_projection]))
        callouts = list(output.modelspace().query('MTEXT[layer=="PIN_HOLE_CALLOUTS"]'))
        self.assertTrue(callouts)
        for callout in callouts:
            extents = app.ezbbox.extents([callout], fast=False)
            bounds = box(extents.extmin.x, extents.extmin.y,
                         extents.extmax.x, extents.extmax.y)
            self.assertFalse(bounds.intersects(side_projection))
            self.assertFalse(bounds.intersects(box(0, 0, 120, 100)))

    def test_original_side_projections_remain_without_generated_sections(self):
        plan = app.create_machining_plan(
            machining_fixture(), machining_config(view_mode='named', pin_height=2.0))
        base = ezdxf.new()
        msp = base.modelspace()
        msp.add_lwpolyline([(0, 0), (120, 0), (120, 100), (0, 100)], close=True)
        msp.add_lwpolyline([(300, 0), (420, 0), (420, 100), (300, 100)], close=True)
        projections = [box(0, -10, 120, -4), box(125, 0, 131, 100),
                       box(300, -10, 420, -4), box(425, 0, 431, 100)]
        for projection in projections:
            msp.add_lwpolyline(list(projection.exterior.coords)[:-1], close=True,
                               dxfattribs={'layer': 'JIG_SIDE'})
        output = app.read_doc(app.build_machining_dxf(
            dxf_bytes(base), plan, [dict(no=1, x=50, y=50, rot=0)], (110, 205),
            base_outline=box(0, 0, 120, 100), pin_outline=box(300, 0, 420, 100),
            side_view_outlines=projections))
        msp = output.modelspace()
        section_layers = {"SECTION_TOP_PROFILE", "SECTION_BOTTOM_PROFILE",
                          "SECTION_CUT_MARKS", "SECTION_ANNOTATIONS"}
        self.assertFalse(any(entity.dxf.layer in section_layers for entity in msp))
        self.assertEqual(len(msp.query('LWPOLYLINE[layer=="JIG_SIDE"]')), 4)

    def test_pin_clearance_holes_are_thru_and_removed_from_dp(self):
        plan = app.create_machining_plan(machining_fixture(), machining_config())
        pin = plan['pins'][0]
        clearance_hole = Point(pin['x'], pin['y']).buffer((pin['diameter'] + 0.5) / 2,
                                                          quad_segs=64)
        thru = unary_union([op['geometry'] for op in plan['operations'] if op['kind'] == 'THRU'])
        dp = unary_union([op['geometry'] for op in plan['operations'] if op['kind'] == 'DP'])
        self.assertTrue(thru.buffer(0.001).covers(clearance_hole))
        self.assertLess(dp.intersection(clearance_hole).area, 1e-6)

    def test_dp_maximum_depth_is_written_as_a_single_annotation(self):
        plan = app.create_machining_plan(machining_fixture(), machining_config())
        base = ezdxf.new()
        base.modelspace().add_lwpolyline([(0, 0), (120, 0), (120, 100), (0, 100)], close=True)
        base.modelspace().add_lwpolyline([(300, 0), (420, 0), (420, 100), (300, 100)], close=True)
        output = app.read_doc(app.build_machining_dxf(
            dxf_bytes(base), plan, [dict(no=1, x=50, y=50, rot=0)], (110, 205),
            base_outline=box(0, 0, 120, 100), pin_outline=box(300, 0, 420, 100)))
        labels = [entity.plain_text() for entity in output.modelspace().query(
            'MTEXT[layer=="DP_ANNOTATIONS"]')]
        self.assertEqual(len(labels), 1)
        self.assertIn('5.000 mm', labels[0])
        self.assertNotIn('3.000 mm', labels[0])
        dp_layers = {entity.dxf.layer for entity in output.modelspace().query('LWPOLYLINE')
                     if entity.dxf.layer.startswith('DP_Z')}
        self.assertEqual(len(dp_layers), 1)

    def test_component_thru_uses_full_one_mm_offset_and_does_not_output_tape_shape(self):
        doc = app.read_doc(machining_fixture())
        top = doc.blocks.get('TOP_VIEW')
        for entity in list(top.query('LWPOLYLINE[layer=="TAPE"]')):
            top.delete_entity(entity)
        top.add_lwpolyline([(2, 2), (4, 2), (4, 4), (2, 4)], close=True,
                           dxfattribs={'layer': 'TAPE'})
        plan = app.create_machining_plan(dxf_bytes(doc), machining_config())
        self.assertEqual(plan['cfg']['bot_relief_offset'], 1.0)
        thru = next(op['geometry'] for op in plan['operations'] if op['kind'] == 'THRU')
        expected_relief = app.prepare_thru_geometry(box(101, 201, 105, 205))
        expected_clearance = Point(104, 204).buffer(1.2, quad_segs=64)
        expected = unary_union([expected_relief, expected_clearance])
        self.assertLess(thru.symmetric_difference(expected).area, 1e-6)
        self.assertGreater(plan['tape'].intersection(thru).area, 0)
        self.assertTrue(all(op['geometry'].intersection(plan['tape']).area < 1e-8
                            for op in plan['operations'] if op['kind'] == 'DP'))

        base = ezdxf.new()
        base.modelspace().add_lwpolyline([(0, 0), (120, 0), (120, 100), (0, 100)], close=True)
        base.modelspace().add_lwpolyline([(300, 0), (420, 0), (420, 100), (300, 100)], close=True)
        output = app.read_doc(app.build_machining_dxf(
            dxf_bytes(base), plan, [dict(no=1, x=50, y=50, rot=0)], (110, 205),
            base_outline=box(0, 0, 120, 100), pin_outline=box(300, 0, 420, 100)))
        self.assertFalse(any(entity.dxf.layer == 'TAPE_KEEP_OUT' for entity in output.modelspace()))

    def test_custom_component_thru_offset_expands_relief_and_keeps_tool_rounding(self):
        plan = app.create_machining_plan(machining_fixture(),
                                         machining_config(bot_relief_offset=2.0))
        component_thru = plan['component_thru']
        self.assertEqual(component_thru.bounds, (100.0, 200.0, 106.0, 206.0))
        expected = app.prepare_thru_geometry(box(100, 200, 106, 206))
        self.assertLess(component_thru.symmetric_difference(expected).area, 1e-6)
        self.assertEqual(plan['cfg']['bot_relief_offset'], 2.0)

    def test_component_thru_offset_rejects_negative_nonfinite_and_over_limit_values(self):
        for value in (-0.1, float('nan'), float('inf'), 10.1):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, 'BOT 피난홀 옵셋'):
                    app.create_machining_plan(machining_fixture(),
                                              machining_config(bot_relief_offset=value))

    def test_component_thru_offset_rejects_values_below_company_minimum(self):
        # DSG-005-02 피난홀 크기 최소 1mm (severity ERROR, 치구 16종 공통).
        # 하한 미달 값으로 사내 기준 위반 가공도가 생성되지 않아야 한다.
        for value in (0.0, 0.3, 0.9):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, 'DSG-005-02'):
                    app.create_machining_plan(machining_fixture(),
                                              machining_config(bot_relief_offset=value))

    def test_component_thru_offset_accepts_company_minimum(self):
        plan = app.create_machining_plan(machining_fixture(),
                                         machining_config(bot_relief_offset=1.0))
        self.assertEqual(plan['cfg']['bot_relief_offset'], 1.0)

    def test_relief_offset_bound_is_read_from_the_standards_file_not_a_constant(self):
        # 하한이 코드 상수가 아니라 rules.json 에서 와야 한다. 기준을 2mm 로 바꾸면
        # 1.5mm 가 거부되고 오류 문구가 그 룰을 근거로 대야 한다.
        import json
        import os

        import jig_standards

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'rules.json'
            # 실제 clearance 처럼 여러 치구 유형에 같은 값으로 걸어 둔다. 한 유형
            # 전용 룰은 jig_standards 가 유형 지정 없이는 쓰지 않는다.
            path.write_text(json.dumps([
                {'rule_id': 'TEST-CLR-01', 'process': 'SMT',
                 'jig_type': 'AL 수대(TOP/BOT)', 'parameter': 'clearance',
                 'operator': '>=', 'min_value': 2.0, 'max_value': None,
                 'nominal_value': None, 'unit': 'mm', 'severity': 'ERROR'},
                {'rule_id': 'TEST-CLR-02', 'process': 'Ass`y',
                 'jig_type': '누름 JIG', 'parameter': 'clearance',
                 'operator': '>=', 'min_value': 2.0, 'max_value': None,
                 'nominal_value': None, 'unit': 'mm', 'severity': 'ERROR'},
            ], ensure_ascii=False), encoding='utf-8')
            previous = os.environ.get('JIG_RULES')
            os.environ['JIG_RULES'] = str(path)
            jig_standards.load_rules.cache_clear()
            try:
                self.assertEqual(app.relief_offset_minimum()[0], 2.0)
                with self.assertRaisesRegex(ValueError, 'TEST-CLR-01'):
                    app.create_machining_plan(machining_fixture(),
                                              machining_config(bot_relief_offset=1.5))
                plan = app.create_machining_plan(machining_fixture(),
                                                 machining_config(bot_relief_offset=2.0))
                self.assertEqual(plan['cfg']['bot_relief_offset'], 2.0)
            finally:
                if previous is None:
                    os.environ.pop('JIG_RULES', None)
                else:
                    os.environ['JIG_RULES'] = previous
                jig_standards.load_rules.cache_clear()

    def test_sidebar_relief_offset_widget_carries_the_standard_lower_bound(self):
        # 화면 위젯에도 사내 기준 하한이 걸려야 한다. 검증 함수만 막으면 사용자는
        # 기준 미달 값을 입력했다가 뒤늦게 오류를 본다.
        from streamlit.testing.v1 import AppTest

        at = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=90).run()
        self.assertFalse(at.exception)
        widget = next(w for w in at.sidebar.number_input if '피난홀 옵셋' in w.label)
        self.assertEqual(widget.min, 1.0)
        self.assertEqual(widget.value, 1.0)
        # 라벨은 기존 화면·기존 테스트가 쓰는 문구를 유지하고 기준은 help 에 적는다.
        self.assertEqual(widget.label, 'BOT 부품 피난홀 옵셋 (mm)')
        self.assertIn('DSG-005-02', widget.help)

    def test_missing_standards_file_stops_instead_of_running_without_rules(self):
        import os

        import jig_standards

        previous = os.environ.get('JIG_RULES')
        os.environ['JIG_RULES'] = str(Path(__file__).parent / '없는_rules.json')
        jig_standards.load_rules.cache_clear()
        try:
            with self.assertRaisesRegex(jig_standards.StandardsError, '찾을 수 없습니다'):
                app.create_machining_plan(machining_fixture(), machining_config())
        finally:
            if previous is None:
                os.environ.pop('JIG_RULES', None)
            else:
                os.environ['JIG_RULES'] = previous
            jig_standards.load_rules.cache_clear()

    def test_review_note_without_product_reference_is_below_jig_content(self):
        plan = app.create_machining_plan(machining_fixture(), machining_config())
        base = ezdxf.new()
        msp = base.modelspace()
        msp.add_lwpolyline([(0, 0), (120, 0), (120, 100), (0, 100)], close=True)
        msp.add_lwpolyline([(300, 0), (420, 0), (420, 100), (300, 100)], close=True)
        msp.add_lwpolyline([(500, 250), (800, 250), (800, 320), (500, 320)], close=True,
                           dxfattribs={'layer': 'TITLE_BLOCK'})
        output = app.build_machining_dxf(
            dxf_bytes(base), plan, [dict(no=1, x=50, y=50, rot=0)], (110, 205),
            base_outline=box(0, 0, 120, 100), pin_outline=box(300, 0, 420, 100),
            side_view_outlines=[box(500, 250, 800, 320)])
        doc = app.read_doc(output)
        msp = doc.modelspace()
        notes = list(msp.query('MTEXT[layer=="MACHINING_REVIEW"]'))
        self.assertEqual(len(notes), 1)
        self.assertTrue(any(entity.dxf.layer == 'TITLE_BLOCK' for entity in msp))
        note_bounds = app.ezbbox.extents(notes, fast=False)
        other_bounds = app.ezbbox.extents([entity for entity in msp if entity not in notes], fast=False)
        self.assertTrue(note_bounds.has_data)
        self.assertTrue(other_bounds.has_data)
        self.assertLess(note_bounds.extmax.y, other_bounds.extmin.y)

    def test_output_has_thickened_plate_projections_above_each_jig_and_notes_below_product(self):
        plan = app.create_machining_plan(machining_fixture(), machining_config(view_mode='named'))
        base = ezdxf.new()
        msp = base.modelspace()
        msp.add_lwpolyline([(0, 0), (120, 0), (120, 100), (0, 100)], close=True)
        msp.add_lwpolyline([(300, 0), (420, 0), (420, 100), (300, 100)], close=True)
        msp.add_lwpolyline([(0, -6), (120, -6), (120, 0), (0, 0)], close=True)
        msp.add_lwpolyline([(300, -6), (420, -6), (420, 0), (300, 0)], close=True)
        msp.add_lwpolyline([(500, 250), (800, 250), (800, 320), (500, 320)], close=True,
                           dxfattribs={'layer': 'TITLE_BLOCK'})
        output = app.read_doc(app.build_machining_dxf(
            dxf_bytes(base), plan, [dict(no=1, x=50, y=50, rot=0)], (110, 205),
            base_outline=box(0, 0, 120, 100), pin_outline=box(300, 0, 420, 100),
            reference_dxf=machining_fixture(),
            side_view_outlines=[box(0, -6, 120, 0), box(300, -6, 420, 0),
                                box(500, 250, 800, 320)]))
        entities = list(output.modelspace())
        isometric = [entity for entity in entities if entity.dxf.layer == 'ISOMETRIC_REVIEW']
        self.assertTrue(any(entity.dxftype() == 'LWPOLYLINE' for entity in isometric))
        iso_labels = [entity for entity in isometric if entity.dxftype() == 'MTEXT']
        self.assertEqual(len(iso_labels), 2)
        top_label = next(entity for entity in iso_labels if '상판' in entity.plain_text())
        bottom_label = next(entity for entity in iso_labels if '하판' in entity.plain_text())
        self.assertIn('3D 솔리드 아님', top_label.plain_text())
        self.assertIn('두께 6.000 mm', top_label.plain_text())
        self.assertIn('Z5.000 mm', top_label.plain_text())
        self.assertIn('두께 6.000 mm', bottom_label.plain_text())
        self.assertIn('H=1.800 mm', bottom_label.plain_text())
        top_bounds = app.ezbbox.extents([top_label], fast=False)
        bottom_bounds = app.ezbbox.extents([bottom_label], fast=False)
        self.assertGreater(top_bounds.extmin.y, 100.0)
        self.assertGreater(bottom_bounds.extmin.y, 100.0)
        self.assertLess((top_bounds.extmin.x + top_bounds.extmax.x) / 2, 200.0)
        self.assertGreater((bottom_bounds.extmin.x + bottom_bounds.extmax.x) / 2, 200.0)
        review = list(output.modelspace().query('MTEXT[layer=="MACHINING_REVIEW"]'))
        self.assertEqual(len(review), 1)
        review_extents = app.ezbbox.extents(review, fast=False)
        dp_notes = list(output.modelspace().query('MTEXT[layer=="DP_ANNOTATIONS"]'))
        self.assertEqual(len(dp_notes), 1)
        dp_note_extents = app.ezbbox.extents(dp_notes, fast=False)
        product_reference = []
        for entity in entities:
            if entity.dxf.layer in ('DP_ANNOTATIONS', 'MACHINING_REVIEW', 'ISOMETRIC_REVIEW'):
                continue
            extents = app.ezbbox.extents([entity], fast=False)
            if extents.has_data and extents.extmax.x <= 0.0:
                product_reference.append(entity)
        reference_extents = app.ezbbox.extents(product_reference, fast=False)
        self.assertTrue(reference_extents.has_data)
        self.assertLess(dp_note_extents.extmax.y, reference_extents.extmin.y)
        self.assertLess(review_extents.extmax.y, dp_note_extents.extmin.y)
        self.assertLess(review_extents.extmax.x, 0.0)

    def test_pin_annotations_stay_inside_concave_plate_and_do_not_overlap(self):
        plan = app.create_machining_plan(machining_fixture(), machining_config())
        pin = dict(plan['pins'][0])
        plan['pins'].append({**pin, 'x': pin['x'] + 10.0})
        base = ezdxf.new()
        base.modelspace().add_lwpolyline([(0, 0), (120, 0), (120, 100), (0, 100)], close=True)
        bottom_plate = box(300, 0, 420, 100)
        output = app.read_doc(app.build_machining_dxf(
            dxf_bytes(base), plan, [dict(no=1, x=50, y=50, rot=0)], (110, 205),
            base_outline=box(0, 0, 120, 100), pin_outline=bottom_plate))
        notes = list(output.modelspace().query('MTEXT[layer=="PIN_ANNOTATIONS"]'))
        self.assertEqual(len(notes), 2)
        note_bounds = []
        for note in notes:
            extents = app.ezbbox.extents([note], fast=False)
            lo, hi = extents.extmin, extents.extmax
            bounds = box(lo.x, lo.y, hi.x, hi.y)
            self.assertTrue(bottom_plate.buffer(0.001).covers(bounds))
            note_bounds.append(bounds)
        self.assertFalse(note_bounds[0].intersects(note_bounds[1]))

        concave_outline = box(300, 0, 420, 100).difference(box(353, 35, 420, 65))
        one_pin_plan = app.create_machining_plan(machining_fixture(), machining_config())
        concave_output = app.read_doc(app.build_machining_dxf(
            dxf_bytes(base), one_pin_plan, [dict(no=1, x=50, y=50, rot=0)], (110, 205),
            base_outline=box(0, 0, 120, 100), pin_outline=concave_outline))
        note = list(concave_output.modelspace().query('MTEXT[layer=="PIN_ANNOTATIONS"]'))[0]
        extents = app.ezbbox.extents([note], fast=False)
        note_bounds = box(extents.extmin.x, extents.extmin.y,
                          extents.extmax.x, extents.extmax.y)
        self.assertTrue(concave_outline.buffer(0.001).covers(note_bounds))

    def test_side_reversal_and_bottom_reflection_are_explicit(self):
        plan = app.create_machining_plan(machining_fixture(),
                                        machining_config(side_reverse=True, side_top_origin=120.0, bot_mirror_x=True,
                                                         bot_target_origin=(120.0, 200.0)))
        deep = next(op for op in plan['operations'] if op['kind'] == 'DP' and op['depth'] == 5)
        self.assertTrue(deep['geometry'].covers(Point(102, 205)))
        thru = next(op for op in plan['operations'] if op['kind'] == 'THRU')
        self.assertTrue(thru['geometry'].covers(Point(117.0, 203.0)))
        self.assertTrue(thru['geometry'].covers(Point(104.0, 204.0).buffer(1.2, quad_segs=64)))

    def test_side_reversal_preserves_the_explicit_alignment_anchor(self):
        plan = app.create_machining_plan(machining_fixture(),
                                        machining_config(side_origin=300.0, side_top_origin=120.0,
                                                         side_reverse=True))
        dp = [op for op in plan['operations'] if op['kind'] == 'DP']
        self.assertAlmostEqual(min(op['geometry'].bounds[0] for op in dp), 99.97)
        self.assertAlmostEqual(max(op['geometry'].bounds[2] for op in dp), 120.03)

    def test_cross_placement_thru_pin_collision_is_reported_in_output(self):
        plan = app.create_machining_plan(machining_fixture(),
                                        machining_config(bot_target_origin=(132.0, 200.0)))
        base = ezdxf.new()
        base.modelspace().add_lwpolyline([(0, 0), (120, 0), (120, 100), (0, 100)], close=True)
        bottom_plate = box(300, 0, 420, 100)
        output = app.build_machining_dxf(dxf_bytes(base), plan,
                                         [dict(no=1, x=50, y=50, rot=0),
                                          dict(no=2, x=80, y=50, rot=0)], (110, 205),
                                         base_outline=box(0, 0, 120, 100), pin_outline=bottom_plate)
        self.assertTrue(any('다른 제품' in warning and 'THRU' in warning for warning in plan['warnings']))
        doc = app.read_doc(output)
        review = list(doc.modelspace().query('MTEXT[layer=="MACHINING_REVIEW"]'))
        self.assertTrue(review)
        self.assertTrue(any('THRU' in entity.plain_text() for entity in review))

    def test_own_pin_clearance_hole_is_not_reported_as_a_thru_collision(self):
        plan = app.create_machining_plan(machining_fixture(),
                                        machining_config(bot_target_origin=(120.0, 200.0)))
        base = ezdxf.new()
        base.modelspace().add_lwpolyline([(0, 0), (120, 0), (120, 100), (0, 100)], close=True)
        base.modelspace().add_lwpolyline([(300, 0), (420, 0), (420, 100), (300, 100)], close=True)
        app.build_machining_dxf(dxf_bytes(base), plan, [dict(no=1, x=50, y=50, rot=0)],
                                (110, 205), base_outline=box(0, 0, 120, 100),
                                pin_outline=box(300, 0, 420, 100))
        self.assertFalse(any('동일 제품' in warning and 'THRU' in warning
                             for warning in plan['warnings']))

    def test_two_views_keep_machining_left_and_pin_only_right(self):
        plan = app.create_machining_plan(machining_fixture(), machining_config())
        base = ezdxf.new()
        msp = base.modelspace()
        msp.add_lwpolyline([(0, 0), (120, 0), (120, 100), (0, 100)], close=True)
        msp.add_lwpolyline([(300, 0), (420, 0), (420, 100), (300, 100)], close=True)
        top_plate, bottom_plate = box(0, 0, 120, 100), box(300, 0, 420, 100)
        output = app.build_machining_dxf(dxf_bytes(base), plan,
                                         [dict(no=1, x=50, y=50, rot=0),
                                          dict(no=2, x=80, y=50, rot=0)], (110, 205),
                                         base_outline=top_plate, pin_outline=bottom_plate)
        doc = app.read_doc(output)
        pins = [e for e in doc.modelspace().query('CIRCLE') if e.dxf.layer.startswith('GUIDE_PIN')]
        self.assertEqual(sorted(round(e.dxf.center.x, 3) for e in pins), [344.0, 374.0])
        self.assertEqual(sorted(round(e.dxf.center.y, 3) for e in pins), [49.0, 49.0])
        self.assertTrue(all(bottom_plate.covers(Point(e.dxf.center.x, e.dxf.center.y)
                                                .buffer(e.dxf.radius)) for e in pins))
        for entity in doc.modelspace().query('LWPOLYLINE'):
            if entity.dxf.layer.startswith(('DP_', 'THRU', 'TAPE_KEEP_OUT')):
                self.assertLessEqual(max(point[0] for point in entity.get_points()), 120.0)

    def test_multiview_export_retains_only_selected_base_and_internal_features(self):
        plan = app.create_machining_plan(machining_fixture(), machining_config())
        base = ezdxf.new()
        msp = base.modelspace()
        msp.add_lwpolyline([(0, 0), (100, 0), (100, 100), (0, 100)], close=True,
                          dxfattribs={'layer': 'BASE_OUTLINE'})
        msp.add_circle((15, 15), 2, dxfattribs={'layer': 'JIG_INTERNAL'})
        msp.add_line((150, 10), (160, 10), dxfattribs={'layer': 'EXTERNAL_VIEW'})
        msp.add_text('EXTERNAL LABEL', dxfattribs={'insert': (150, 20), 'height': 2,
                                                 'layer': 'EXTERNAL_VIEW'})
        data = dxf_bytes(base)
        selected = box(0, 0, 100, 100)
        output = app.read_doc(app.build_machining_dxf(data, plan,
                                                     [dict(no=1, x=50, y=50, rot=0)],
                                                     (110, 205), base_outline=selected,
                                                     pin_outline=box(300, 0, 400, 100)))
        self.assertEqual(len(list(output.modelspace().query('*[layer=="BASE_OUTLINE"]'))), 1)
        self.assertEqual(len(list(output.modelspace().query('*[layer=="JIG_INTERNAL"]'))), 1)
        self.assertEqual(len(list(output.modelspace().query('*[layer=="EXTERNAL_VIEW"]'))), 0)

        legacy = app.run_nesting(data, plan['nesting_dxf'],
                                 config(base_layers=['BASE_OUTLINE'], prod_layers=['PRODUCT_OUTLINE'],
                                        hole_layers=['PRODUCT_HOLES']))
        legacy_output = app.read_doc(app.build_result_dxf(legacy))
        self.assertEqual(len(list(legacy_output.modelspace().query('*[layer=="EXTERNAL_VIEW"]'))), 2)

    def test_selected_base_filter_preserves_chamfer_and_excludes_concave_cutout(self):
        plan = app.create_machining_plan(machining_fixture(), machining_config())
        selected = Polygon([(5, 0), (100, 0), (100, 50), (50, 50), (50, 100), (0, 100), (0, 5)])
        base = ezdxf.new()
        msp = base.modelspace()
        msp.add_lwpolyline(list(selected.exterior.coords)[:-1], close=True,
                          dxfattribs={'layer': 'SELECTED_CHAMFER'})
        msp.add_line((0, 5), (5, 0), dxfattribs={'layer': 'CHAMFER_EDGE'})
        msp.add_circle((75, 75), 2, dxfattribs={'layer': 'OUTSIDE_IN_BBOX'})
        output = app.read_doc(app.build_machining_dxf(dxf_bytes(base), plan,
                                                     [dict(no=1, x=25, y=25, rot=0)],
                                                     (110, 205), base_outline=selected,
                                                     pin_outline=affinity.translate(selected, xoff=150)))
        self.assertEqual(len(list(output.modelspace().query('*[layer=="SELECTED_CHAMFER"]'))), 1)
        self.assertEqual(len(list(output.modelspace().query('*[layer=="CHAMFER_EDGE"]'))), 1)
        self.assertEqual(len(list(output.modelspace().query('*[layer=="OUTSIDE_IN_BBOX"]'))), 0)

    def test_selected_base_insert_preserves_chamfer_internal_features_and_layer_inheritance(self):
        plan = app.create_machining_plan(machining_fixture(), machining_config())
        local = Polygon([(5, 0), (100, 0), (100, 50), (50, 50), (50, 100), (0, 100), (0, 5)])
        selected = affinity.translate(local, 10, 20)
        for partial in (False, True):
            with self.subTest(partial=partial):
                base = ezdxf.new()
                block = base.blocks.new('JIG_SHAPE')
                block.add_lwpolyline(list(local.exterior.coords)[:-1], close=True,
                                     dxfattribs={'layer': 'BLOCK_OUTLINE'})
                block.add_circle((15, 15), 2)
                if partial:
                    block.add_line((150, 10), (160, 10), dxfattribs={'layer': 'BLOCK_EXTERNAL'})
                base.modelspace().add_blockref('JIG_SHAPE', (10, 20), dxfattribs={'layer': 'PARENT_JIG'})
                output = app.read_doc(app.build_machining_dxf(dxf_bytes(base), plan,
                                                             [dict(no=1, x=35, y=45, rot=0)],
                                                             (110, 205), base_outline=selected,
                                                             pin_outline=affinity.translate(selected, xoff=300)))
                entries = list(app.named_entities(output.modelspace()))
                circles = [(entity, layer) for entity, layer, _ in entries
                           if entity.dxftype() == 'CIRCLE' and layer == 'PARENT_JIG']
                self.assertEqual(len(circles), 1)
                self.assertAlmostEqual(circles[0][0].dxf.center.x, 25)
                self.assertAlmostEqual(circles[0][0].dxf.center.y, 35)
                self.assertTrue(any(layer == 'BLOCK_OUTLINE' for _, layer, _ in entries))
                self.assertFalse(any(layer == 'BLOCK_EXTERNAL' for _, layer, _ in entries))
                self.assertEqual(len(list(output.modelspace().query('INSERT[name=="JIG_SHAPE"]'))),
                                 0 if partial else 1)

    def test_partial_base_minsert_keeps_all_contained_array_instances(self):
        plan = app.create_machining_plan(machining_fixture(), machining_config())
        selected = Polygon([(0, 0), (100, 0), (100, 50), (50, 50), (50, 100), (0, 100)])
        base = ezdxf.new()
        base.modelspace().add_lwpolyline(list(selected.exterior.coords)[:-1], close=True)
        base.blocks.new('JIG_ARRAY').add_circle((10, 10), 2)
        base.modelspace().add_blockref('JIG_ARRAY', (0, 0),
                                       dxfattribs={'layer': 'PARENT_ARRAY', 'row_count': 2,
                                                   'column_count': 2, 'row_spacing': 40, 'column_spacing': 40})
        output = app.read_doc(app.build_machining_dxf(dxf_bytes(base), plan,
                                                     [dict(no=1, x=25, y=25, rot=0)],
                                                     (110, 205), base_outline=selected,
                                                     pin_outline=affinity.translate(selected, xoff=150)))
        centers = {(round(e.dxf.center.x), round(e.dxf.center.y))
                   for e, layer, _ in app.named_entities(output.modelspace())
                   if e.dxftype() == 'CIRCLE' and layer == 'PARENT_ARRAY'}
        self.assertEqual(centers, {(10, 10), (50, 10), (10, 50)})

    def test_tape_does_not_clip_component_thru_from_other_placements(self):
        plan = app.create_machining_plan(machining_fixture(),
                                        machining_config(bot_target_origin=(136.0, 200.0)))
        base = ezdxf.new()
        base.modelspace().add_lwpolyline([(0, 0), (120, 0), (120, 100), (0, 100)], close=True)
        bottom_plate = box(300, 0, 420, 100)
        output = app.build_machining_dxf(dxf_bytes(base), plan,
                                         [dict(no=1, x=50, y=50, rot=0),
                                          dict(no=2, x=80, y=50, rot=0)], (110, 205),
                                         base_outline=box(0, 0, 120, 100), pin_outline=bottom_plate)
        doc = app.read_doc(output)
        layers = [layer.dxf.name for layer in doc.layers if layer.dxf.name.startswith('THRU')]
        geometry = unary_union(app.parse_geometry(doc, layers).outlines)
        self.assertTrue(geometry.covers(Point(78, 48)))
        self.assertTrue(geometry.covers(Point(80, 48)))
        self.assertFalse(any(entity.dxf.layer == 'TAPE_KEEP_OUT' for entity in doc.modelspace()))

    def test_tool_recommendations_are_geometric_and_keep_small_features_for_review(self):
        self.assertEqual(app.recommend_tools(Point(0, 0).buffer(4, quad_segs=64))[0], [2.0])
        self.assertEqual(app.recommend_tools(box(0, 0, 1.5, 8))[0], [1.0])
        tools, warning = app.recommend_tools(box(0, 0, 0.7, 8))
        self.assertEqual(tools, [])
        self.assertTrue(warning)
        mixed = Point(0, 0).buffer(4, quad_segs=64).union(box(10, 0, 10.7, 8))
        tools, warning = app.recommend_tools(mixed)
        self.assertEqual(tools, [2.0])
        self.assertTrue(warning)

    def test_machining_output_omits_toolpaths_and_keeps_design_boundaries(self):
        plan = app.create_machining_plan(machining_fixture(), machining_config())
        base = ezdxf.new()
        base.modelspace().add_lwpolyline([(0, 0), (120, 0), (120, 100), (0, 100)], close=True)
        base.modelspace().add_lwpolyline([(300, 0), (420, 0), (420, 100), (300, 100)], close=True)
        output = app.read_doc(app.build_machining_dxf(
            dxf_bytes(base), plan, [dict(no=1, x=50, y=50, rot=0)], (110, 205),
            base_outline=box(0, 0, 120, 100), pin_outline=box(300, 0, 420, 100)))
        paths = [entity for entity in output.modelspace().query('LWPOLYLINE')
                 if entity.dxf.layer.startswith('TOOLPATH_')]
        self.assertFalse(paths)
        target_boundaries = [entity for entity in output.modelspace()
                             if entity.dxf.layer.startswith(('DP_Z', 'THRU_FULL'))]
        self.assertTrue(target_boundaries)
        review = output.modelspace().query('MTEXT[layer=="MACHINING_REVIEW"]')[0].plain_text()
        self.assertNotIn('TOOLPATH_', review)
        self.assertNotIn('stepover', review)
        self.assertTrue(any('_D1' in entity.dxf.layer or '_D2' in entity.dxf.layer
                            for entity in target_boundaries))

    def test_unmapped_views_and_invalid_pin_height_are_rejected(self):
        for changes in ({'side_source': ''}, {'pin_height': 2.1}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                app.create_machining_plan(machining_fixture(), machining_config(**changes))

    def test_y_projection_and_internal_hole_boundary(self):
        doc = app.read_doc(machining_fixture())
        top = doc.blocks.get('TOP_VIEW')
        for entity in list(top.query('LWPOLYLINE[layer=="TOP_OUTLINE"]')):
            top.delete_entity(entity)
        top.add_lwpolyline([(0, 0), (10, 0), (10, 20), (0, 20)], close=True,
                          dxfattribs={'layer': 'TOP_OUTLINE'})
        top.add_circle((0.5, 4), 1, dxfattribs={'layer': 'TOP_HOLES'})
        plan = app.create_machining_plan(dxf_bytes(doc), machining_config(side_axis='Y', side_top_origin=200.0))
        dp_operations = [op for op in plan['operations'] if op['kind'] == 'DP']
        self.assertEqual(len(dp_operations), 1)
        self.assertEqual(dp_operations[0]['depth'], 5)
        self.assertTrue(dp_operations[0]['geometry'].covers(Point(102, 202)))
        self.assertTrue(dp_operations[0]['geometry'].covers(Point(102, 218)))
        self.assertEqual(len(plan['pins']), 1)

    def test_real_text_only_views_are_not_fabricated_as_named_sources(self):
        data = (ROOT / 'CAD/FPCB TEST-2.dxf').read_bytes()
        sources = app.machining_sources(app.read_doc(data))
        self.assertFalse(any(view in source.upper() for source in sources for view in ('TOP', 'SIDE', 'BOT')))
        with self.assertRaisesRegex(ValueError, 'MTEXT'):
            app.create_machining_plan(data, machining_config())

    def test_output_transforms_every_operation_and_records_depth_tool_height(self):
        plan = app.create_machining_plan(machining_fixture(), machining_config())
        base = ezdxf.new()
        base.modelspace().add_lwpolyline([(0, 0), (100, 0), (100, 100), (0, 100)], close=True)
        output = app.build_machining_dxf(dxf_bytes(base), plan,
                                         [dict(no=1, x=50, y=50, rot=90)], (110, 205),
                                         base_outline=box(0, 0, 100, 100),
                                         pin_outline=box(150, 0, 250, 100))
        doc = app.read_doc(output)
        names = [layer.dxf.name for layer in doc.layers]
        self.assertFalse(any(name.startswith('DP_Z3') for name in names))
        self.assertTrue(any(name.startswith('DP_Z5') for name in names))
        self.assertTrue(any(name.startswith('THRU') for name in names))
        self.assertIn('GUIDE_PIN_H1.800', names)
        self.assertTrue(all('_D' in name for name in names if name.startswith(('DP_Z', 'THRU_'))))
        self.assertFalse(any(e.dxftype() in ('TEXT', 'MTEXT')
                             and e.dxf.layer.startswith(('DP_Z', 'THRU_', 'GUIDE_PIN'))
                             for e in doc.modelspace()))
        pin = next(e for e in doc.modelspace().query('CIRCLE') if e.dxf.layer.startswith('GUIDE_PIN'))
        self.assertAlmostEqual(pin.dxf.center.x, 201.0)
        self.assertAlmostEqual(pin.dxf.center.y, 44.0)

    def test_jig_plate_selection_uses_left_main_plate_and_right_main_plate(self):
        outlines = [box(300, 0, 390, 120), box(0, 10, 70, 110),
                    box(300, -20, 390, -14), box(0, -20, 70, -14)]
        top_index, bottom_index = app.jig_plate_indices(outlines)
        self.assertEqual(top_index, 1)
        self.assertEqual(bottom_index, 0)

    def test_jig_side_view_candidates_find_front_and_side_projections(self):
        data = (ROOT / 'base_jig_templates/tape_jig_2.dxf').read_bytes()
        doc = app.read_doc(data)
        parsed = app.parse_cad(data, ('JIG 외곽',), 0.001)
        top_index, bottom_index = app.jig_plate_indices(parsed.outlines)

        candidates = app.jig_side_view_candidates(doc, parsed.outlines,
                                                  top_index, bottom_index)

        expected = [(3535.022, 427.755, 3605.022, 433.755),
                    (3635.022, 463.755, 3641.022, 563.755),
                    (3825.022, 417.755, 3915.022, 423.755),
                    (3945.022, 453.755, 3951.022, 573.755)]
        self.assertEqual(len(candidates), len(expected))
        for bounds in expected:
            self.assertTrue(any(all(abs(actual - target) <= 0.001
                                    for actual, target in zip(candidate.bounds, bounds))
                                for candidate in candidates), bounds)

    def test_machining_output_preserves_only_selected_jig_side_projection(self):
        plan = app.create_machining_plan(machining_fixture(), machining_config())
        top_plate = box(0, 0, 100, 100)
        bottom_plate = box(150, 0, 250, 100)
        selected_projection = box(110, 0, 116, 100)
        base = ezdxf.new()
        msp = base.modelspace()
        for plate in (top_plate, bottom_plate):
            msp.add_lwpolyline(list(plate.exterior.coords)[:-1], close=True,
                              dxfattribs={'layer': 'JIG_OUTLINE'})
        msp.add_line((110, 0), (110, 100), dxfattribs={'layer': 'JIG_SIDE'})
        msp.add_line((116, 0), (116, 100), dxfattribs={'layer': 'JIG_SIDE'})
        msp.add_line((130, 0), (130, 100), dxfattribs={'layer': 'JIG_SIDE'})

        output = app.read_doc(app.build_machining_dxf(
            dxf_bytes(base), plan, [dict(no=1, x=50, y=50, rot=0)], (110, 205),
            base_outline=top_plate, pin_outline=bottom_plate,
            side_view_outlines=[selected_projection]))

        side_lines = [(entity.dxf.start.x, entity.dxf.end.x)
                      for entity in output.modelspace().query('LINE[layer=="JIG_SIDE"]')]
        self.assertCountEqual(side_lines, [(110, 110), (116, 116)])

    def test_preview_is_rendered_to_the_selected_fixed_pixel_width(self):
        logging.getLogger('matplotlib.font_manager').setLevel(logging.ERROR)
        figure, _ = app.plt.subplots(figsize=(4, 2))
        figure_number = figure.number
        with patch('app.st.image') as show_image:
            app.show_preview(figure, 400)
        png = show_image.call_args.args[0]
        self.assertEqual(show_image.call_args.kwargs['width'], 400)
        self.assertEqual(struct.unpack('>II', png[16:24])[0], 400)
        self.assertNotIn(figure_number, app.plt.get_fignums())

    def test_machining_output_maps_left_plate_operations_and_right_plate_pins(self):
        plan = app.create_machining_plan(machining_fixture(), machining_config())
        top_plate = box(0, 5, 70, 105)
        bottom_plate = box(290, -5, 380, 115)
        base = ezdxf.new()
        msp = base.modelspace()
        msp.add_lwpolyline(list(top_plate.exterior.coords)[:-1], close=True,
                          dxfattribs={'layer': 'JIG_OUTLINE'})
        msp.add_lwpolyline(list(bottom_plate.exterior.coords)[:-1], close=True,
                          dxfattribs={'layer': 'JIG_OUTLINE'})
        msp.add_lwpolyline([(0, -20), (70, -20), (70, -14), (0, -14)], close=True,
                          dxfattribs={'layer': 'JIG_OUTLINE'})
        output = app.build_machining_dxf(dxf_bytes(base), plan,
                                         [dict(no=1, x=25, y=25, rot=0)], (110, 205),
                                         base_outline=top_plate, pin_outline=bottom_plate)
        doc = app.read_doc(output)
        pins = [entity for entity in doc.modelspace().query('CIRCLE')
                if entity.dxf.layer.startswith('GUIDE_PIN')]
        self.assertEqual(len(pins), 1)
        self.assertAlmostEqual(pins[0].dxf.center.x, 319.0)
        self.assertAlmostEqual(pins[0].dxf.center.y, 24.0)
        self.assertTrue(bottom_plate.covers(Point(pins[0].dxf.center.x,
                                                  pins[0].dxf.center.y).buffer(pins[0].dxf.radius)))
        operation_polylines = [entity for entity in doc.modelspace().query('LWPOLYLINE')
                               if entity.dxf.layer.startswith(('DP_', 'THRU'))]
        self.assertTrue(operation_polylines)
        for entity in operation_polylines:
            geometry = Polygon([(point[0], point[1]) for point in entity.get_points()])
            if not geometry.is_empty:
                self.assertTrue(top_plate.buffer(0.001).covers(geometry))
                self.assertFalse(bottom_plate.intersects(geometry))

    def test_ui_exposes_explicit_multiview_mapping_and_alignment(self):
        from streamlit.testing.v1 import AppTest
        logging.getLogger('matplotlib.font_manager').setLevel(logging.ERROR)
        base = ezdxf.new()
        msp = base.modelspace()
        msp.add_lwpolyline([(0, 0), (200, 0), (200, 200), (0, 200)], close=True)
        msp.add_lwpolyline([(300, 0), (500, 0), (500, 200), (300, 200)], close=True)

        class Upload(io.BytesIO):
            def __init__(self, data):
                super().__init__(data)
                self.name = 'synthetic.dxf'

        def upload(label, **kwargs):
            return Upload(machining_fixture()) if label.startswith('②') else None

        with patch('streamlit.delta_generator.DeltaGenerator.file_uploader', side_effect=upload):
            at = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=60).run()
            self.assertEqual(at.segmented_control[0].value, '수동 레이어 가공도')
            at.segmented_control[0].set_value('자동 인식 가공도').run()
            self.assertEqual(at.segmented_control[0].value, '자동 인식 가공도')
            self.assertFalse(at.exception)
            self.assertTrue({'TOP 뷰 소스', 'SIDE 뷰 소스', 'BOT 뷰 소스'}
                            .issubset({w.label for w in at.selectbox}))
            self.assertTrue(any(w.label == 'SIDE 도면의 투영축' for w in at.selectbox))
            roles = {w.label for w in at.multiselect}
            self.assertIn('TOP 내부 홀 → 가이드핀 위치', roles)
            self.assertIn('BOT 부품 외곽 → 피난홀 THRU', roles)
            self.assertTrue(any('가이드핀 위치' in w.value and '피난홀 THRU' in w.value for w in at.caption))
            component = next(w for w in at.multiselect if w.label == 'BOT 부품 외곽 → 피난홀 THRU')
            self.assertNotIn('TOP_OUTLINE', component.options)
            component.set_value(['BOT_COMPONENT']).run()
            labels = {w.label for w in at.number_input}
            self.assertTrue({'SIDE 깊이 기준면', 'BOT 기준 X', 'BOT 정합 X', '핀 높이 (mm)'} <= labels)
            self.assertTrue(any('NC/G-code' in w.value for w in at.caption))
            values = {'SIDE 투영 기준 좌표': 300.0, 'TOP 대응 기준 좌표': 100.0,
                      'SIDE 깊이 기준면': 400.0, 'BOT 기준 X': 500.0, 'BOT 기준 Y': 600.0,
                      'BOT 정합 X': 100.0, 'BOT 정합 Y': 200.0}
            for widget in at.number_input:
                if widget.label in values:
                    widget.set_value(values[widget.label])
            at.run()
            next(w for w in at.button if w.label == '가공도 계산 및 배열').click().run()
            self.assertFalse(at.exception)
            self.assertFalse(at.error)
            self.assertTrue(any(w.label == '가공도 배치 수량' and int(w.value) > 0 for w in at.metric))
            self.assertTrue(any('가공도 DXF' == w.label for w in at.get('download_button')))
            result = at.session_state['machining_result']
            pin = result['plan']['pins'][0]
            placement = result['nesting']['placements'][0]
            left_x, left_y = app.tf((pin['x'], pin['y']), result['nesting']['c0'],
                                    placement['rot'], placement['x'], placement['y'])
            doc = app.read_doc(result['dxf'])
            right_pins = [e for e in doc.modelspace().query('CIRCLE') if e.dxf.layer.startswith('GUIDE_PIN')]
            self.assertTrue(any(abs(e.dxf.center.x - left_x - 300.0) < 1e-6
                                and abs(e.dxf.center.y - left_y) < 1e-6 for e in right_pins))
            self.assertTrue(any('TOP 원본 좌표의 단일 제품 기준' in w.value for w in at.caption))


class NestingRegressionTests(unittest.TestCase):
    def test_auto_layout_finds_centered_single_piece_when_grid_origin_touches_edge(self):
        usable = box(0, 0, 30, 60)
        product = (ROOT / 'CAD/FPCB TEST-2.dxf').read_bytes()
        plan = app.create_machining_plan(product, app.detect_multiview(app.read_doc(product)))
        parsed = app.parse_cad(plan['nesting_dxf'], ('PRODUCT_OUTLINE',), 0.001,
                               circle_layers=('PRODUCT_HOLES',))
        center = ((parsed.outline.bounds[0] + parsed.outline.bounds[2]) / 2,
                  (parsed.outline.bounds[1] + parsed.outline.bounds[3]) / 2)
        footprint = affinity.translate(parsed.outline.buffer(0.03, join_style=2, mitre_limit=5.0),
                                       -center[0], -center[1])
        placements = app.auto_layout(usable, footprint, 10.0)
        self.assertEqual(len(placements), 2)
        self.assertTrue(all(usable.contains(affinity.translate(footprint, placement[2], placement[3]))
                            for placement in placements))

    def test_supplied_cad_selects_board_and_preserves_clearances(self):
        base = (ROOT / 'CAD/JIG 도면/TAPE 부착 JIG-1.dxf').read_bytes()
        product = (ROOT / 'CAD/제품 도면/TEST 제품도면 단품(DX)-2.dxf').read_bytes()
        res = app.run_nesting(base, product, config())
        x0, y0, x1, y1 = res['prod'].outline.bounds
        self.assertAlmostEqual(x1 - x0, 43.180, places=3)
        self.assertAlmostEqual(y1 - y0, 23.231, places=3)
        self.assertGreater(len(res['placements']), 0)
        self.assertGreater(len(res['holes_rel']), 0)
        footprints = [affinity.translate(res['fp0'][p['rot']], p['x'], p['y'])
                      for p in res['placements']]
        self.assertTrue(all(res['usable'].covers(g) for g in footprints))
        self.assertEqual(app.verify_spacing(footprints, 10.0)[1], 0)
        self.assertTrue(all(res['prod'].outline.buffer(0.002).covers(LineString(points))
                            for points, _ in res['prod'].lines))
        output = app.read_doc(app.build_result_dxf(res))
        self.assertTrue(list(output.modelspace().query('LWPOLYLINE[layer=="NEST_PRODUCT"]')))

    def test_holes_on_separate_layer_are_collected(self):
        base = ezdxf.new()
        base.modelspace().add_lwpolyline([(0, 0), (100, 0), (100, 100), (0, 100)],
                                       close=True, dxfattribs={'layer': 'BASE'})
        product = ezdxf.new()
        product.modelspace().add_lwpolyline([(0, 0), (20, 0), (20, 10), (0, 10)],
                                          close=True, dxfattribs={'layer': 'OUTLINE'})
        product.modelspace().add_circle((5, 5), 1, dxfattribs={'layer': 'HOLES'})
        res = app.run_nesting(dxf_bytes(base), dxf_bytes(product),
                              config(base_layers=['BASE'], prod_layers=['OUTLINE'],
                                     hole_layers=['HOLES'], prod_outline_index=0))
        self.assertEqual(len(res['holes_rel']), 1)
        self.assertEqual(res['holes_rel'][0][2], 2.0)

    def test_component_layer_inside_selected_outline_is_kept_in_product_geometry(self):
        base = ezdxf.new()
        base.modelspace().add_lwpolyline([(0, 0), (100, 0), (100, 100), (0, 100)], close=True,
                                       dxfattribs={'layer': 'JIG 외곽'})
        product = ezdxf.new()
        msp = product.modelspace()
        msp.add_lwpolyline([(0, 0), (20, 0), (20, 20), (0, 20)], close=True,
                           dxfattribs={'layer': 'OUTLINE'})
        msp.add_lwpolyline([(5, 5), (9, 5), (9, 9), (5, 9)], close=True,
                           dxfattribs={'layer': 'COMPONENT'})
        res = app.run_nesting(dxf_bytes(base), dxf_bytes(product),
                              config(base_layers=['JIG 외곽'], prod_layers=['OUTLINE', 'COMPONENT'],
                                     hole_layers=[], edge_margin=20, mode='qty', qty=1,
                                     prod_outline_index=0))

        self.assertEqual(res['prod'].outline.bounds, (0.0, 0.0, 20.0, 20.0))
        self.assertEqual(len(res['prod'].lines), 2)
        output = app.read_doc(app.build_result_dxf(res))
        product_geometry = list(output.modelspace().query('LWPOLYLINE[layer=="NEST_PRODUCT"]'))
        self.assertEqual(len(product_geometry), 2)

    def test_manual_layer_array_defaults_to_manual_and_places_supplied_top_candidate(self):
        from streamlit.testing.v1 import AppTest
        logging.getLogger('matplotlib.font_manager').setLevel(logging.ERROR)
        if not app.find_oda_converter():
            self.skipTest('실제 DWG 업로드 검증에는 로컬 ODA File Converter가 필요합니다.')

        class Upload(io.BytesIO):
            def __init__(self, path):
                super().__init__(path.read_bytes())
                self.name = path.name

        def upload(label, **kwargs):
            if label.startswith('②'):
                return Upload(ROOT / 'CAD/FPCB TEST-2.dwg')
            return None

        with patch('streamlit.delta_generator.DeltaGenerator.file_uploader', side_effect=upload):
            at = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=90).run()
            self.assertFalse(at.exception)
            self.assertFalse(at.error)
            self.assertEqual(at.segmented_control[0].value, '수동 레이어 가공도')
            at.segmented_control[0].set_value('기본 배열').run()
            self.assertEqual(at.segmented_control[0].value, '기본 배열')
            self.assertIn('자동 인식 가공도', at.segmented_control[0].options)
            labels = {w.label for w in at.multiselect}
            self.assertTrue({'베이스 외곽 레이어', '제품 외곽 레이어',
                             '제품 부품 레이어', '제품 내부 홀 레이어'} <= labels)
            defaults = {w.label: w.value for w in at.multiselect}
            self.assertEqual(defaults['베이스 외곽 레이어'], ['JIG 외곽'])
            self.assertEqual(defaults['제품 외곽 레이어'], ['0'])
            self.assertEqual(defaults['제품 부품 레이어'], ['부품'])
            self.assertEqual(defaults['제품 내부 홀 레이어'], ['내부홀'])
            self.assertTrue(any('외곽 밖의 부품' in w.value and '자동 인식 가공도' in w.value
                                for w in at.caption))
            self.assertFalse({'SIDE 깊이 기준면', 'BOT 기준 X', 'BOT 정합 X'}
                             & {w.label for w in at.number_input})

            selections = {'베이스 외곽 레이어': ['JIG 외곽'],
                          '제품 외곽 레이어': ['0'],
                          '제품 부품 레이어': ['부품'],
                          '제품 내부 홀 레이어': ['내부홀']}
            for widget in at.multiselect:
                if widget.label in selections:
                    widget.set_value(selections[widget.label])
            at.run()
            self.assertFalse(at.exception)
            candidate = next(w for w in at.selectbox if w.label == '제품 외곽선 후보')
            self.assertTrue(any('26.222 × 20.699' in option for option in candidate.options))
            top_index = next(i for i, option in enumerate(candidate.options)
                             if '26.222 × 20.699' in option)
            candidate.set_value(top_index).run()
            self.assertFalse(at.exception)
            next(b for b in at.button if b.label == '▶ 배열 실행').click().run()

            self.assertFalse(at.exception)
            self.assertFalse(at.error)
            result = at.session_state['result']
            x0, y0, x1, y1 = result['prod'].outline.bounds
            self.assertAlmostEqual(x1 - x0, 26.222, places=3)
            self.assertAlmostEqual(y1 - y0, 20.699, places=3)
            self.assertEqual(result['cfg']['prod_layers'], {'0', '부품'})
            self.assertEqual(result['cfg']['hole_layers'], {'내부홀'})
            self.assertGreater(len(result['placements']), 0)
            self.assertTrue(result['out_dxf'])
            self.assertTrue(any(w.label == '결과 DXF' for w in at.get('download_button')))
            preview_width = next(w for w in at.slider if w.label == '배치 미리보기 너비 (px)')
            self.assertEqual(preview_width.value, 400)
            preview_width.set_value(200).run()
            self.assertFalse(at.exception)
            self.assertEqual(next(w for w in at.slider
                                  if w.label == '배치 미리보기 너비 (px)').value, 200)

    def test_wrong_outline_reports_recognized_sizes(self):
        base = (ROOT / 'CAD/JIG 도면/TAPE 부착 JIG-1.dxf').read_bytes()
        product = (ROOT / 'CAD/제품 도면/TEST 제품도면 단품(DX)-2.dxf').read_bytes()
        with self.assertRaisesRegex(ValueError, '82\\.1.*6\\.7'):
            app.run_nesting(base, product, config(prod_layers=['0'], prod_outline_index=0))

    def test_connected_faces_merge_without_losing_internal_obstacles(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_lwpolyline([(0, 0), (100, 0), (100, 100), (0, 100)], close=True)
        msp.add_line((50, 0), (50, 100))
        msp.add_lwpolyline([(10, 10), (20, 10), (20, 20), (10, 20)], close=True)
        parsed = app.parse_geometry(doc, ['0'])
        self.assertEqual(parsed.outline.area, 10000.0)
        self.assertEqual([g.area for g in parsed.others], [100.0])

    def test_grid_partition_does_not_create_internal_obstacles(self):
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_lwpolyline([(0, 0), (90, 0), (90, 90), (0, 90)], close=True)
        for coordinate in (30, 60):
            msp.add_line((coordinate, 0), (coordinate, 90))
            msp.add_line((0, coordinate), (90, coordinate))
        parsed = app.parse_geometry(doc, ['0'])
        self.assertEqual(parsed.outline.area, 8100.0)
        self.assertEqual(parsed.others, [])

    def test_circular_outline_rejects_invalid_candidate(self):
        doc = ezdxf.new()
        doc.modelspace().add_circle((0, 0), 10)
        for index in (-1, 1):
            with self.subTest(index=index), self.assertRaisesRegex(ValueError, '외곽선 후보'):
                app.parse_geometry(doc, ['0'], outline_index=index)

    def test_ui_recovers_from_product_layer_zero_without_changing_base_or_holes(self):
        from streamlit.testing.v1 import AppTest
        logging.getLogger('matplotlib.font_manager').setLevel(logging.ERROR)
        if not app.find_oda_converter():
            self.skipTest('실제 DWG 업로드 검증에는 로컬 ODA File Converter가 필요합니다.')

        class Upload(io.BytesIO):
            def __init__(self, path):
                super().__init__(path.read_bytes())
                self.name = path.name

        def upload(label, **kwargs):
            if label.startswith('②'):
                return Upload(ROOT / 'CAD/제품 도면/TEST 제품도면 단품(DX)-L.dwg')
            return None

        with patch('streamlit.delta_generator.DeltaGenerator.file_uploader', side_effect=upload):
            at = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=60).run()
            self.assertEqual(at.segmented_control[0].value, '수동 레이어 가공도')
            at.segmented_control[0].set_value('기본 배열').run()
            self.assertFalse(at.exception)
            selections = {'베이스 외곽 레이어': ['JIG 외곽'],
                          '제품 외곽 레이어': ['0'],
                          '제품 부품 레이어': [], '제품 내부 홀 레이어': ['0']}
            for widget in at.multiselect:
                widget.set_value(selections[widget.label])
            at.run()
            next(b for b in at.button if '배열 실행' in b.label).click().run()
            self.assertFalse(at.exception)
            self.assertTrue(any('배치 가능한 제품이 없습니다' in e.value for e in at.error))
            self.assertTrue(any(b.label == '오류 복구: 제품 전체 레이어 다시 선택' for b in at.button))
            self.assertFalse(any('파일을 업로드' in notice.value for notice in at.info))

            next(b for b in at.button if b.label == '오류 복구: 제품 전체 레이어 다시 선택').click().run()
            self.assertFalse(at.exception)
            product_hash = at.session_state['active_product_layers_hash']
            self.assertEqual(at.session_state[f'prod_layers_selection_{product_hash}'], [])
            self.assertEqual(at.session_state[f'product_component_layers_selection_{product_hash}'], [])
            self.assertEqual(at.session_state[f'prod_outline_choice_{product_hash}'], 0)
            actual = {w.label: w.value for w in at.multiselect}
            self.assertEqual(actual['베이스 외곽 레이어'], ['JIG 외곽'])
            self.assertEqual(actual['제품 내부 홀 레이어'], ['0'])
            self.assertEqual(actual['제품 외곽 레이어'], [])
            self.assertEqual(actual['제품 부품 레이어'], [])
            candidate = next(w for w in at.selectbox if w.label == '제품 외곽선 후보')
            self.assertIn('43.180 × 23.231', candidate.options[0])

            next(b for b in at.button if '배열 실행' in b.label).click().run()
            self.assertFalse(at.exception)
            self.assertFalse(at.error)
            metrics = {m.label: m.value for m in at.metric}
            self.assertEqual(metrics['배치 수량'], '2')
            self.assertEqual(metrics['최소 제품 간격'], '10.00 mm')
            next(w for w in at.selectbox if w.label == '① BASE JIG 유형').set_value(
                'TAPE 부착 JIG 2단').run()
            self.assertFalse(at.exception)
            self.assertEqual(next(w for w in at.selectbox if w.label == '① BASE JIG 유형').options,
                             ['TAPE 부착 JIG / TAPE 부착 JIG 2단'])
            self.assertTrue(at.get('download_button'))


def layer_zero_product(closed=True):
    """외곽·부품·홀이 전부 layer 0 에 있는 제품 도면 (낱개 LINE 외곽)."""
    doc = ezdxf.new()
    msp = doc.modelspace()
    pts = [(0, 0), (40, 0), (40, 25), (0, 25)]
    for i in range(4 if closed else 3):
        msp.add_line(pts[i], pts[(i + 1) % 4])
    if closed:
        msp.add_circle((5, 5), 1.5)
        msp.add_lwpolyline([(15, 8), (25, 8), (25, 14), (15, 14)], close=True)
    return doc


def grid_result(rows, cols, mode='auto'):
    return dict(placements=[dict(no=r * cols + c + 1, row=r + 1, col=c + 1, x=0, y=0, rot=0)
                            for r in range(rows) for c in range(cols)],
                cfg=dict(mode=mode))


def smt_base(width, height):
    """SMT수대 베이스 판 (JIG 외곽 레이어 직사각형)."""
    doc = ezdxf.new()
    doc.modelspace().add_lwpolyline([(0, 0), (width, 0), (width, height), (0, height)],
                                    close=True, dxfattribs={'layer': 'JIG 외곽'})
    return dxf_bytes(doc)


class ArrayStandardTests(unittest.TestCase):
    """DSG-005-03 AL 수대 '배열시 4배열 가능 하도록(가로 or 세로)'."""

    def test_row_or_column_of_four_passes_with_rule_basis(self):
        for rows, cols in ((2, 5), (4, 1), (1, 4)):
            with self.subTest(rows=rows, cols=cols):
                check = app.array_standard_check(grid_result(rows, cols), 'SMT수대')
                self.assertTrue(check['ok'])
                self.assertEqual(check['need'], 4)
                self.assertIn('DSG-005-03', check['basis'])

    def test_three_by_three_fails_even_though_total_is_nine(self):
        check = app.array_standard_check(grid_result(3, 3), 'SMT수대')
        self.assertFalse(check['ok'])
        self.assertEqual(check['best'], 3)

    def test_manual_layout_is_not_judged(self):
        # "가능 하도록" 은 능력 기준이다. 일부러 적게 놓은 수동 배치를 위반으로 보지 않는다.
        check = app.array_standard_check(grid_result(1, 2, mode='qty'), 'SMT수대')
        self.assertIsNone(check['ok'])

    def test_other_jig_categories_have_no_array_standard(self):
        self.assertIsNone(app.array_standard_check(grid_result(1, 2), 'TAPE 부착 JIG'))
        self.assertIsNone(app.array_standard_check(grid_result(1, 2), '기타'))

    def test_result_screen_reports_the_standard_for_smt_fixture(self):
        from streamlit.testing.v1 import AppTest

        product = dxf_bytes(layer_zero_product())

        class Upload(io.BytesIO):
            def __init__(self):
                super().__init__(product)
                self.name = 'product.dxf'

        def upload(label, **kwargs):
            return Upload() if label.startswith('②') else None

        # 300x100 판 → 유효영역 260x60 → 40x25 제품 한 줄 5개 (통과)
        # 200x150 판 → 유효영역 160x110 → 3x3 (위반)
        for kind, base, expect_ok in (('SMT수대 넓은판', smt_base(300, 100), True),
                                      ('SMT수대 좁은판', smt_base(200, 150), False)):
            with self.subTest(kind=kind), TemporaryDirectory() as folder:
                app_copy = Path(folder) / 'app.py'
                app_copy.write_bytes((ROOT / 'app.py').read_bytes())
                library = Path(folder) / 'base_jig_templates'
                app.save_base_jig_template(kind, base, root=library, category='SMT수대')
                with patch('streamlit.delta_generator.DeltaGenerator.file_uploader',
                           side_effect=upload):
                    at = AppTest.from_file(str(app_copy), default_timeout=60).run()
                    at.segmented_control[0].set_value('기본 배열').run()
                    next(b for b in at.button if '배열 실행' in b.label).click().run()
                    self.assertFalse(at.exception)
                    if expect_ok:
                        self.assertTrue(any('DSG-005-03' in s.value for s in at.success),
                                        [s.value for s in at.success])
                        self.assertFalse(any('DSG-005-03' in e.value for e in at.error))
                    else:
                        self.assertTrue(any('DSG-005-03' in e.value for e in at.error),
                                        [e.value for e in at.error])


class ClampClearanceStandardTests(unittest.TestCase):
    """DSG-011-02/03 AOI 클램프: KIT(제품) 외곽에서 안쪽 6mm 이내 부품 간섭 경고 (3mm 살대)."""

    @staticmethod
    def product(*components):
        outline = box(0, 0, 40, 25)
        return app.Parsed(outline, [box(*c) for c in components], [], [(5, 5, 1.5, '0')], [outline])

    def test_components_within_six_mm_of_the_outline_are_counted(self):
        check = app.clamp_clearance_check(self.product((1, 1, 4, 3), (15, 8, 25, 14)), 'SMT수대')
        self.assertEqual(check['count'], 1)                 # (1,1)-(4,3) 은 외곽까지 1mm
        self.assertAlmostEqual(check['closest'], 1.0)
        self.assertEqual(check['reach'], 6.0)
        self.assertEqual(check['rim'], 3.0)
        self.assertIn('DSG-011-02', check['basis'])
        self.assertIn('DSG-011-03', check['basis'])

    def test_component_exactly_six_mm_inside_is_clear_and_holes_are_not_components(self):
        check = app.clamp_clearance_check(self.product((6, 6, 34, 19)), 'SMT수대')
        self.assertEqual(check['count'], 0)
        self.assertIsNone(check['closest'])

    def test_other_jig_categories_have_no_clamp_standard(self):
        self.assertIsNone(app.clamp_clearance_check(self.product((1, 1, 4, 3)), 'TAPE 부착 JIG'))

    def test_result_screen_warns_only_for_automation_models(self):
        from streamlit.testing.v1 import AppTest

        doc = layer_zero_product()
        doc.modelspace().add_lwpolyline([(1, 1), (4, 1), (4, 3), (1, 3)], close=True)   # 외곽 1mm 안
        product = dxf_bytes(doc)

        class Upload(io.BytesIO):
            def __init__(self):
                super().__init__(product)
                self.name = 'product.dxf'

        def upload(label, **kwargs):
            return Upload() if label.startswith('②') else None

        with TemporaryDirectory() as folder:
            app_copy = Path(folder) / 'app.py'
            app_copy.write_bytes((ROOT / 'app.py').read_bytes())
            app.save_base_jig_template('SMT수대 넓은판', smt_base(300, 100),
                                       root=Path(folder) / 'base_jig_templates', category='SMT수대')
            with patch('streamlit.delta_generator.DeltaGenerator.file_uploader', side_effect=upload):
                at = AppTest.from_file(str(app_copy), default_timeout=60).run()
                at.segmented_control[0].set_value('기본 배열').run()
                next(b for b in at.button if '배열 실행' in b.label).click().run()
                self.assertFalse(at.exception)
                switch = next(c for c in at.checkbox
                              if c.label == '자동화 타입 모델 (AOI 설비 클램프 체결)')
                self.assertFalse(switch.value)
                self.assertFalse(any('DSG-011-03' in w.value for w in at.warning))
                switch.check().run()
                self.assertFalse(at.exception)
                warned = [w.value for w in at.warning if 'DSG-011-03' in w.value]
                self.assertEqual(len(warned), 1, [w.value for w in at.warning])
                self.assertIn('1개', warned[0])
                self.assertIn('1.00 mm', warned[0])


class AutoLayoutBoundaryTests(unittest.TestCase):
    """Auto-Max 가 경계에 딱 붙인 첫 행·열을 부동소수점 오차로 잃지 않아야 한다 (2026-11-18 실사용 검증)."""

    def test_footprint_off_center_by_half_micron_still_fills_the_grid(self):
        # FPCB TEST-2 발자국 실측과 같은 크기·어긋남: y −10.3805 / +10.3795
        footprint = box(-8.9, -10.3805, 8.9, 10.3795)
        cells = app.auto_layout(box(0, 0, 50, 80), footprint, 10.0)
        self.assertEqual(len(cells), 4)                    # 2 x 2 (전에는 1)

    def test_real_fpcb_on_tape_jig_basic_layout_counts(self):
        import jig_preprocess

        exe = app.find_oda_converter()
        if not exe:
            self.skipTest('실제 DWG 검증에는 로컬 ODA File Converter가 필요합니다.')
        with TemporaryDirectory() as work:
            product = app.oda_convert(exe, (ROOT / 'CAD/FPCB TEST-2.dwg').read_bytes(),
                                      'dwg', 'DXF', 'ACAD2018', work)
        product, _ = jig_preprocess.preprocess(product, jig_preprocess.Options(strip_frame=True))
        base = app.load_base_jig_template('TAPE 부착 JIG 2단')
        cfg = config(base_layers={'JIG 외곽'},
                     prod_layers={'AUTO_OUTLINE', 'AUTO_COMPONENT', '부품'},
                     hole_layers={'AUTO_HOLE'}, mode='auto')
        # 유효영역 50x80, 발자국 17.80x20.76, 간격 10
        for rot, expected in (('0', 4), ('90', 3), ('auto', 4)):
            with self.subTest(rot=rot):
                result = app.run_nesting(base, product, dict(cfg, rot=rot))
                self.assertEqual(len(result['placements']), expected)


class BaseLayerAutoSplitTests(unittest.TestCase):
    """layer 0 베이스 지그(SMT 수대 실도면)를 기본 배열에 쓰는 화면 흐름."""

    def test_smt_fixture_plate_becomes_the_base_outline(self):
        from streamlit.testing.v1 import AppTest

        product = dxf_bytes(layer_zero_product())

        class Upload(io.BytesIO):
            def __init__(self):
                super().__init__(product)
                self.name = 'product.dxf'

        def upload(label, **kwargs):
            return Upload() if label.startswith('②') else None

        with TemporaryDirectory() as folder:
            app_copy = Path(folder) / 'app.py'
            app_copy.write_bytes((ROOT / 'app.py').read_bytes())
            app.save_base_jig_template('SMT 수대 240x175', (ROOT / 'base_jig_templates/smt_fixture.dxf')
                                       .read_bytes(), root=Path(folder) / 'base_jig_templates',
                                       category='SMT수대')
            with patch('streamlit.delta_generator.DeltaGenerator.file_uploader', side_effect=upload):
                at = AppTest.from_file(str(app_copy), default_timeout=90).run()
                at.segmented_control[0].set_value('기본 배열').run()
                self.assertFalse(at.exception)
                switch = next((c for c in at.checkbox if c.label == '베이스 레이어 자동 구분'), None)
                self.assertIsNotNone(switch)
                self.assertFalse(switch.value)
                switch.check().run()
                next(c for c in at.checkbox if c.label == '베이스 도면 테두리 제거').check().run()
                self.assertFalse(at.exception)
                layers = next(m for m in at.multiselect if m.label == '베이스 외곽 레이어')
                self.assertEqual(layers.value, ['AUTO_OUTLINE'])
                base_box = next(s for s in at.selectbox if s.label == '베이스 외곽선 후보')
                self.assertIn('240.000 × 175.000', base_box.options[0])
                next(b for b in at.button if '배열 실행' in b.label).click().run()
                self.assertFalse(at.exception)
                self.assertFalse(at.error, [e.value for e in at.error])
                metrics = {m.label: m.value for m in at.metric}
                self.assertGreaterEqual(int(metrics['배치 수량']), 4)
                self.assertTrue(any('DSG-005-03' in s.value for s in at.success),
                                [s.value for s in at.success])


class ProductLayerAutoSplitTests(unittest.TestCase):
    """layer 0 제품 도면을 전처리기(jig_preprocess)로 역할 레이어를 입혀 쓰는 화면 흐름."""

    def open_app(self, product_doc):
        from streamlit.testing.v1 import AppTest

        data = dxf_bytes(product_doc)

        class Upload(io.BytesIO):
            def __init__(self):
                super().__init__(data)
                self.name = 'product.dxf'

        def upload(label, **kwargs):
            return Upload() if label.startswith('②') else None

        patcher = patch('streamlit.delta_generator.DeltaGenerator.file_uploader', side_effect=upload)
        patcher.start()
        self.addCleanup(patcher.stop)
        at = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=60).run()
        at.segmented_control[0].set_value('기본 배열').run()
        self.assertFalse(at.exception)
        return at

    @staticmethod
    def widget(widgets, label):
        return next((w for w in widgets if w.label == label), None)

    def test_switch_is_offered_only_for_layer_zero_drawings(self):
        at = self.open_app(layer_zero_product())
        switch = self.widget(at.checkbox, '레이어 자동 구분')
        self.assertIsNotNone(switch)
        self.assertFalse(switch.value)                    # 입력을 조용히 바꾸지 않는다
        self.assertEqual(self.widget(at.multiselect, '제품 외곽 레이어').options, ['0'])

        layered = layer_zero_product()
        for entity in layered.modelspace():
            entity.dxf.layer = '부품' if entity.dxftype() == 'LWPOLYLINE' else '외곽'
        at = self.open_app(layered)
        self.assertIsNone(self.widget(at.checkbox, '레이어 자동 구분'))

    def test_switch_puts_role_layers_into_the_existing_selectors(self):
        at = self.open_app(layer_zero_product())
        self.widget(at.checkbox, '레이어 자동 구분').check().run()
        self.assertFalse(at.exception)
        self.assertFalse(at.error, [e.value for e in at.error])
        outline = self.widget(at.multiselect, '제품 외곽 레이어')
        self.assertTrue({'AUTO_OUTLINE', 'AUTO_COMPONENT', 'AUTO_HOLE'} <= set(outline.options))
        self.assertEqual(outline.value, ['AUTO_OUTLINE'])
        self.assertIn('AUTO_COMPONENT', self.widget(at.multiselect, '제품 부품 레이어').value)
        self.assertEqual(self.widget(at.multiselect, '제품 내부 홀 레이어').value, ['AUTO_HOLE'])
        self.assertIsNotNone(self.widget(at.checkbox, '도면 테두리 제거'))
        self.assertIn('레이어 자동 구분 결과', [e.label for e in at.expander])

        next(b for b in at.button if '배열 실행' in b.label).click().run()
        self.assertFalse(at.exception)
        self.assertFalse(at.error, [e.value for e in at.error])
        metrics = {m.label: m.value for m in at.metric}
        self.assertGreaterEqual(int(metrics['배치 수량']), 1)

    def test_drawing_that_cannot_be_split_keeps_original_layers_with_warning(self):
        at = self.open_app(layer_zero_product(closed=False))
        self.widget(at.checkbox, '레이어 자동 구분').check().run()
        self.assertFalse(at.exception)
        self.assertTrue(any('닫힌' in w.value for w in at.warning), [w.value for w in at.warning])
        self.assertEqual(self.widget(at.multiselect, '제품 외곽 레이어').options, ['0'])


if __name__ == '__main__':
    unittest.main()
