"""사내 설계기준(rules.json)을 코드에 주입하는 단일 출처.

app.py 와 jigauto 가 같은 수치를 쓰게 하려고 통합 루트에 둔다.
수치를 코드 상수로 박지 않고 여기서 받는다 (CLAUDE.md 코딩 규칙).

## 왜 "치구 유형 매핑표"가 아닌가

rules.json 의 `jig_type` 은 31종인데 app.py 의 BASE JIG 유형·분류와 이름이 다르다
("SMT수대" vs "AL 수대(TOP/BOT)"). 손으로 매핑표를 쓰면 틀린 기준을 조용히 적용하게 된다.

대신 **파라미터별 적용 범위를 데이터에서 계산**한다. 유형을 몰라도 쓸 수 있는 기준은
다음 두 조건을 **모두** 만족할 때뿐이다.

1. `uniform` — 룰들이 서로 다른 값을 말하지 않는다.
2. `cross_type` — 적용 범위가 한 치구 유형을 넘는다. 즉 공정 `공통`의 `모든 치구` 룰이거나,
   서로 다른 치구 유형 2종 이상에 같은 값으로 걸려 있다.

`uniform` 만으로는 안 된다. 룰이 1건뿐인 파라미터는 값 충돌이 없어 자동으로 uniform 이
되지만 그 1건은 특정 치구 전용이다 — `router_bit_spacing >= 46mm` 는 라우터 JIG 기준이라
TAPE 부착 JIG 에 적용하면 틀린다. `clearance` 는 17종에 같은 값으로 걸려 있어 통과한다.

둘 중 하나라도 어긋나면 `AmbiguousStandard` 로 **중단**한다. 후보 중 아무거나 고르는
폴백은 넣지 않는다.

공정 `자동화` 의 `모든 치구` 룰은 그 공정 전체 기준이므로 `process="자동화"` 를 명시해야
쓸 수 있다. 일반 치구에 자동화 기준을 적용하지 않기 위한 구분이다.

## 사용

    import jig_standards
    jig_standards.limit("clearance").min            # 1.0
    jig_standards.limit("clearance").rule_ids       # ('DSG-005-02', ...)
    jig_standards.limit("corner_radius", jig_type="누름 JIG")

rules.json 경로는 `JIG_RULES` 환경변수로 덮어쓸 수 있다.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DEFAULT_RULES = (Path(__file__).resolve().parent / "CAD" / "CAD_Validator" / "CAD_Validator"
                 / "rules" / "rules.json")

# rules.json 에서 "모든 치구" 는 그 공정의 치구 전체를 뜻한다.
ALL_JIGS = "모든 치구"
# 공정 "공통" 의 "모든 치구" 만 공정과 무관하게 적용된다.
UNIVERSAL_PROCESS = "공통"

# app.py 의 BASE JIG 분류 → rules.json 의 jig_type. 이름이 달라 손으로 대응시킨 것이므로
# 근거가 분명한 것만 둔다. [가정] 관리 Sheet r5 "SMT / AL 수대(TOP/BOT) / AL" 을 app.py 의
# "SMT수대" 와 같은 치구로 본다 — 사용자 확인 대상. 분류를 늘릴 때는 여기에 명시적으로 추가한다.
APP_CATEGORY_JIG_TYPES = {"SMT수대": "AL 수대(TOP/BOT)"}

# rules.json 의 수치 칸이 비어 있고 원문(source_text)에만 있는 값. 코드 상수로 박지 않고 여기 모으며,
# test_jig_standards 가 각 값이 해당 룰 원문에 문자 그대로 있는지 대조한다 (원문 대조 규칙).
SOURCE_TEXT_VALUES = {
    "DSG-011-03": 6.0,   # "KIT 외곽으로 부터 3mm 살대 적용 (6mm이내 부품 간섭 여부 체크)" — 관리 Sheet r11
}


def source_value(rule_id: str) -> tuple[float, str]:
    """원문에만 있는 기준값과 근거 룰 ID."""
    if rule_id not in SOURCE_TEXT_VALUES:
        raise StandardsError(f"원문 기준값 목록에 '{rule_id}' 가 없습니다.")
    return SOURCE_TEXT_VALUES[rule_id], rule_id


class StandardsError(Exception):
    """기준을 적용할 수 없는 상태. 조용히 넘기지 않고 호출자에게 알린다."""


class AmbiguousStandard(StandardsError):
    """치구 유형에 따라 값이 갈리는데 유형이 확정되지 않았다."""


@dataclass(frozen=True)
class Limit:
    parameter: str
    operator: str | None
    min: float | None
    max: float | None
    nominal: float | None
    unit: str | None
    severity: str
    uniform: bool
    cross_type: bool
    rule_ids: tuple[str, ...]
    jig_types: tuple[str, ...]

    def describe(self) -> str:
        """오류 문구에 쓸 근거 표기 — 룰 ID 를 남긴다 (설명 가능성)."""
        head = ", ".join(self.rule_ids[:2])
        rest = f" 외 {len(self.rule_ids) - 2}건" if len(self.rule_ids) > 2 else ""
        return f"{head}{rest}"


def rules_path() -> Path:
    return Path(os.environ.get("JIG_RULES") or DEFAULT_RULES)


@lru_cache(maxsize=4)
def load_rules(path: str | None = None) -> tuple[dict, ...]:
    target = Path(path) if path else rules_path()
    if not target.is_file():
        raise StandardsError(
            f"사내 설계기준 파일을 찾을 수 없습니다: {target}. "
            "JIG_RULES 환경변수로 rules.json 경로를 지정하세요.")
    data = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise StandardsError(f"설계기준 파일 형식이 올바르지 않습니다: {target}")
    return tuple(data)


def _key(rule: dict) -> tuple:
    return (rule.get("operator"), rule.get("min_value"),
            rule.get("max_value"), rule.get("nominal_value"), rule.get("unit"))


def limit(parameter: str, jig_type: str | None = None,
          process: str | None = None, path: str | None = None) -> Limit:
    """`parameter` 의 기준 한계.

    `jig_type` 없이 쓸 수 있는 것은 `uniform` 이면서 `cross_type` 인 기준뿐이다.
    그 밖에는 AmbiguousStandard 로 중단한다 (모듈 docstring 참조).
    """
    rules = [r for r in load_rules(path) if r.get("parameter") == parameter]
    if not rules:
        raise StandardsError(f"설계기준에 '{parameter}' 항목이 없습니다.")

    universal = [r for r in rules
                 if r.get("jig_type") == ALL_JIGS
                 and r.get("process") == UNIVERSAL_PROCESS]
    process_wide = [r for r in rules
                    if r.get("jig_type") == ALL_JIGS and r not in universal]
    typed = [r for r in rules if r.get("jig_type") != ALL_JIGS]

    pool = universal or typed or process_wide
    distinct = {_key(r) for r in pool}
    scope = {r.get("jig_type") for r in pool}
    uniform = len(distinct) == 1
    cross_type = bool(universal) or len(scope) > 1

    if jig_type is None and not universal:
        if process and process_wide and all(
                r.get("process") == process for r in process_wide) and not typed:
            pool = process_wide
            uniform = len({_key(r) for r in pool}) == 1
            cross_type = True
        elif not uniform:
            raise AmbiguousStandard(
                f"'{parameter}' 기준이 치구 유형에 따라 다릅니다({len(distinct)}가지). "
                f"치구 유형을 지정하세요. 후보: {sorted(scope)}")
        elif not cross_type:
            only = sorted(scope)[0]
            raise AmbiguousStandard(
                f"'{parameter}' 기준은 치구 유형 '{only}' 전용입니다"
                f"({', '.join(sorted(r['rule_id'] for r in pool))}). "
                "치구 유형을 지정하세요.")

    if jig_type is not None:
        chosen = [r for r in typed if r.get("jig_type") == jig_type] or universal
        if not chosen:
            raise AmbiguousStandard(
                f"치구 유형 '{jig_type}' 에 대한 '{parameter}' 기준이 없습니다. "
                f"후보: {sorted(scope)}")
        if len({_key(r) for r in chosen}) != 1:
            raise AmbiguousStandard(
                f"치구 유형 '{jig_type}' 의 '{parameter}' 기준이 서로 다릅니다: "
                f"{sorted(r['rule_id'] for r in chosen)}")
    else:
        chosen = pool

    first = chosen[0]
    severities = {r.get("severity") for r in chosen}
    return Limit(
        parameter=parameter,
        operator=first.get("operator"),
        min=first.get("min_value"),
        max=first.get("max_value"),
        nominal=first.get("nominal_value"),
        unit=first.get("unit"),
        severity="ERROR" if "ERROR" in severities else sorted(severities)[0],
        uniform=uniform,
        cross_type=cross_type,
        rule_ids=tuple(sorted(r["rule_id"] for r in chosen)),
        jig_types=tuple(sorted({r.get("jig_type") for r in chosen})),
    )


def parameters(path: str | None = None) -> dict[str, str]:
    """{파라미터: 상태}. 어떤 기준을 바로 주입할 수 있는지 보는 용도.

    상태는 `ready`(유형 없이 적용 가능) 또는 중단 사유 문구다.
    """
    out: dict[str, str] = {}
    for rule in load_rules(path):
        name = rule.get("parameter")
        if not name or name in out:
            continue
        try:
            limit(name, path=path)
            out[name] = "ready"
        except AmbiguousStandard as error:
            out[name] = str(error)
    return out
