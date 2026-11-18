"""rules.json 의 룰을 추출 원본 엑셀과 1:1 대조한다.

rules.json 은 엑셀에서 기계 추출된 2차 자료다. 수치를 설계에 쓰기 전에
source_row 로 원문을 찾아 (1) 원문이 실제로 그 행에 있는지 (2) 룰의 수치가
원문에 문자로 등장하는지 확인한다.

사용:
    python tools/verify_rules.py <rules.json> <원본.xlsx> [--md <출력.md>]

openpyxl 없이 stdlib(zipfile+xml)로 읽는다 — 이 검증은 어느 환경에서나 돌아야 한다.
"""

from __future__ import annotations

import argparse
import json
import re
import zipfile
from xml.etree import ElementTree as ET

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def read_sheets(path: str) -> dict[str, dict[int, dict[str, str]]]:
    """{시트명: {행번호: {열문자: 값}}}"""
    z = zipfile.ZipFile(path)
    shared: list[str] = []
    if "xl/sharedStrings.xml" in z.namelist():
        root = ET.fromstring(z.read("xl/sharedStrings.xml"))
        for si in root.findall(f"{NS}si"):
            shared.append("".join(t.text or "" for t in si.iter(f"{NS}t")))

    wb = ET.fromstring(z.read("xl/workbook.xml"))
    names = [s.get("name") for s in wb.iter(f"{NS}sheet")]

    out: dict[str, dict[int, dict[str, str]]] = {}
    for idx, name in enumerate(names, 1):
        member = f"xl/worksheets/sheet{idx}.xml"
        if member not in z.namelist():
            continue
        rows: dict[int, dict[str, str]] = {}
        root = ET.fromstring(z.read(member))
        for r in root.iter(f"{NS}row"):
            cells: dict[str, str] = {}
            for c in r.findall(f"{NS}c"):
                ref, ctype = c.get("r"), c.get("t")
                v, inline = c.find(f"{NS}v"), c.find(f"{NS}is")
                if ctype == "s" and v is not None:
                    val = shared[int(v.text)]
                elif inline is not None:
                    val = "".join(x.text or "" for x in inline.iter(f"{NS}t"))
                elif v is not None:
                    val = v.text or ""
                else:
                    continue
                if val.strip():
                    cells[re.match(r"([A-Z]+)", ref).group(1)] = val.strip()
            if cells:
                rows[int(r.get("r"))] = cells
        out[name] = rows
    return out


def norm(s: str) -> str:
    """공백·개행 차이를 무시하고 비교하기 위한 정규화."""
    return re.sub(r"\s+", "", s or "")


def number_variants(x: float) -> list[str]:
    """1.0 → ['1', '1.0'] / 0.05 → ['0.05'] — 원문 표기 흔들림 흡수."""
    out = {f"{x:g}"}
    if float(x).is_integer():
        out.add(str(int(x)))
        out.add(f"{int(x)}.0")
    return sorted(out)


def verify(rules: list[dict], sheets: dict) -> list[dict]:
    findings = []
    for rule in rules:
        rid = rule.get("rule_id")
        sheet, row = rule.get("source_sheet"), rule.get("source_row")
        src = rule.get("source_text") or ""

        if sheet not in sheets:
            findings.append({"rule_id": rid, "kind": "SHEET_MISSING",
                             "detail": f"시트 {sheet!r} 없음"})
            continue
        cells = sheets[sheet].get(row)
        if not cells:
            findings.append({"rule_id": rid, "kind": "ROW_MISSING",
                             "detail": f"{sheet} r{row} 비어 있음"})
            continue

        joined = norm(" ".join(cells.values()))
        if norm(src) and norm(src) not in joined:
            findings.append({"rule_id": rid, "kind": "TEXT_MISMATCH",
                             "detail": f"{sheet} r{row} 원문에 source_text 없음"})
            continue

        # 수치 룰: 값이 원문에 문자로 등장해야 한다
        for field in ("nominal_value", "min_value", "max_value"):
            val = rule.get(field)
            if val is None:
                continue
            if not any(v in src for v in number_variants(val)):
                findings.append({
                    "rule_id": rid, "kind": "VALUE_NOT_IN_SOURCE",
                    "detail": f"{field}={val} 가 원문에 없음 "
                              f"(검사항목: {rule.get('inspection_item')})"})
    return findings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("rules")
    ap.add_argument("xlsx")
    ap.add_argument("--md")
    args = ap.parse_args()

    rules = json.load(open(args.rules, encoding="utf-8"))
    sheets = read_sheets(args.xlsx)
    findings = verify(rules, sheets)

    numeric = [r for r in rules
               if any(r.get(f) is not None
                      for f in ("nominal_value", "min_value", "max_value"))]
    auto = [r for r in rules if r.get("auto_checkable")]

    lines = [
        f"룰 {len(rules)}건 / 수치 룰 {len(numeric)}건 / 자동검사 {len(auto)}건",
        f"불일치 {len(findings)}건",
        "",
    ]
    by_kind: dict[str, list[dict]] = {}
    for f in findings:
        by_kind.setdefault(f["kind"], []).append(f)
    for kind, items in sorted(by_kind.items()):
        lines.append(f"## {kind} — {len(items)}건")
        for f in items:
            lines.append(f"- `{f['rule_id']}` {f['detail']}")
        lines.append("")

    report = "\n".join(lines)
    print(report)
    if args.md:
        with open(args.md, "w", encoding="utf-8") as fp:
            fp.write("# rules.json 원문 대조 결과\n\n" + report)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
