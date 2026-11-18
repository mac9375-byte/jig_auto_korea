"""jig_standards 검증 — 실제 rules.json 으로 돈다 (합성 데이터 아님)."""

import json
import tempfile
import unittest
from pathlib import Path

import jig_standards as js


class UniformStandardTests(unittest.TestCase):
    def test_clearance_is_uniform_and_applies_without_jig_type(self):
        # clearance 17건이 17개 치구 유형에 전부 >=1.0mm ERROR 라 매핑 없이 쓸 수 있다.
        lim = js.limit("clearance")
        self.assertTrue(lim.uniform)
        self.assertTrue(lim.cross_type)
        self.assertEqual(lim.min, 1.0)
        self.assertEqual(lim.operator, ">=")
        self.assertEqual(lim.unit, "mm")
        self.assertEqual(lim.severity, "ERROR")
        self.assertIn("DSG-005-02", lim.rule_ids)
        self.assertGreater(len(lim.jig_types), 1)

    def test_describe_names_the_rule_basis(self):
        self.assertTrue(js.limit("clearance").describe().startswith("DSG-"))

    def test_unknown_parameter_is_rejected(self):
        with self.assertRaisesRegex(js.StandardsError, "없습니다"):
            js.limit("정의되지_않은_파라미터")

    def test_single_type_standard_is_not_applied_without_jig_type(self):
        # 룰이 1건이면 값 충돌은 없지만 특정 치구 전용이다. 라우터 비트 간격 46mm 를
        # TAPE 부착 JIG 에 적용하면 틀린다 → 유형 없이는 중단해야 한다.
        with self.assertRaisesRegex(js.AmbiguousStandard, "전용입니다"):
            js.limit("router_bit_spacing")
        self.assertEqual(js.limit("router_bit_spacing", "라우터 JIG").min, 46.0)

    def test_common_process_all_jigs_standard_applies_without_jig_type(self):
        # [공통] 모든 치구 는 공정·유형과 무관하게 적용된다.
        lim = js.limit("bridge_profile_extra_clearance")
        self.assertTrue(lim.cross_type)
        self.assertEqual(lim.min, 0.2)
        self.assertEqual(lim.jig_types, ("모든 치구",))

    def test_process_scoped_all_jigs_standard_needs_the_process(self):
        # [자동화] 모든 치구 는 그 공정 기준이라 일반 치구에 적용하면 안 된다.
        with self.assertRaises(js.AmbiguousStandard):
            js.limit("combined_thickness")
        self.assertEqual(js.limit("combined_thickness", process="자동화").max, 5.0)


class AmbiguousStandardTests(unittest.TestCase):
    """값이 유형별로 갈리면 조용히 고르지 않고 중단해야 한다."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "rules.json")
        rules = [
            {"rule_id": "T-1", "jig_type": "A형", "parameter": "corner_radius",
             "operator": ">=", "min_value": 2.0, "max_value": None,
             "nominal_value": None, "unit": "mm", "severity": "ERROR"},
            {"rule_id": "T-2", "jig_type": "B형", "parameter": "corner_radius",
             "operator": ">=", "min_value": 5.0, "max_value": None,
             "nominal_value": None, "unit": "mm", "severity": "WARNING"},
        ]
        Path(self.path).write_text(json.dumps(rules, ensure_ascii=False),
                                   encoding="utf-8")
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(js.load_rules.cache_clear)

    def test_split_values_without_jig_type_stop_instead_of_guessing(self):
        with self.assertRaises(js.AmbiguousStandard) as ctx:
            js.limit("corner_radius", path=self.path)
        self.assertIn("A형", str(ctx.exception))
        self.assertIn("B형", str(ctx.exception))

    def test_split_values_resolve_once_jig_type_is_given(self):
        self.assertEqual(js.limit("corner_radius", "A형", path=self.path).min, 2.0)
        self.assertEqual(js.limit("corner_radius", "B형", path=self.path).min, 5.0)
        self.assertFalse(js.limit("corner_radius", "A형", path=self.path).uniform)

    def test_unmapped_jig_type_stops_and_lists_candidates(self):
        with self.assertRaisesRegex(js.AmbiguousStandard, "기준이 없습니다"):
            js.limit("corner_radius", "없는유형", path=self.path)


class SourceTextValueTests(unittest.TestCase):
    """rules.json 수치 칸이 비어 원문에만 있는 값 — 쓰기 전에 원문 대조한다."""

    def test_every_source_value_appears_in_its_rule_text(self):
        rules = {r["rule_id"]: r for r in js.load_rules()}
        self.assertTrue(js.SOURCE_TEXT_VALUES)
        for rule_id, value in js.SOURCE_TEXT_VALUES.items():
            with self.subTest(rule_id=rule_id):
                rule = rules[rule_id]
                self.assertTrue(all(rule.get(k) is None
                                    for k in ("min_value", "max_value", "nominal_value")))
                self.assertIn(f"{value:g}mm", rule["source_text"].replace(" ", ""))

    def test_source_value_returns_value_and_rule_basis(self):
        value, basis = js.source_value("DSG-011-03")
        self.assertEqual(value, 6.0)
        self.assertEqual(basis, "DSG-011-03")
        with self.assertRaises(js.StandardsError):
            js.source_value("DSG-999-99")


class RulesFileTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(js.load_rules.cache_clear)

    def test_missing_rules_file_fails_loudly(self):
        with self.assertRaisesRegex(js.StandardsError, "찾을 수 없습니다"):
            js.load_rules(str(Path(tempfile.gettempdir()) / "없는파일_rules.json"))

    def test_malformed_rules_file_fails_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "rules.json"
            bad.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(js.StandardsError, "형식이 올바르지 않습니다"):
                js.load_rules(str(bad))

    def test_real_rules_file_loads(self):
        self.assertGreaterEqual(len(js.load_rules()), 200)


if __name__ == "__main__":
    unittest.main()
