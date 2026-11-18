"""제공 CAD로 계산/출력 및 Streamlit 화면 흐름을 검증한다."""
import io
import json
import logging
from pathlib import Path
from unittest.mock import patch

import matplotlib.pyplot as plt
from streamlit.testing.v1 import AppTest

import app
from test_nesting import config

ROOT = Path(__file__).parent
base_path = ROOT / 'CAD/JIG 도면/TAPE 부착 JIG-1.dxf'
product_path = ROOT / 'CAD/제품 도면/TEST 제품도면 단품(DX)-2.dxf'


class Upload(io.BytesIO):
    def __init__(self, path):
        super().__init__(path.read_bytes())
        self.name = path.name


def upload(label, **kwargs):
    return Upload(base_path if label.startswith('①') else product_path)


if __name__ == '__main__':
    result = app.run_nesting(base_path.read_bytes(), product_path.read_bytes(), config())
    out = ROOT / '검증결과'
    out.mkdir(exist_ok=True)
    (out / 'nesting_result.dxf').write_bytes(app.build_result_dxf(result))
    logging.getLogger('matplotlib.font_manager').setLevel(logging.ERROR)
    fig = app.make_figure(result)
    fig.savefig(out / 'nesting_preview.png', dpi=140, bbox_inches='tight')
    plt.close(fig)
    print(json.dumps({'placements': len(result['placements']),
                      'holes_per_product': len(result['holes_rel']),
                      'minimum_gap_mm': result['min_gap'],
                      'warnings': result['warnings']}, ensure_ascii=False), flush=True)
    with patch('streamlit.delta_generator.DeltaGenerator.file_uploader', side_effect=upload):
        at = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=60).run()
        at.segmented_control[0].set_value('기본 배열').run()
        assert not at.exception, str(at.exception)
        print('UI initial controls:', [(x.label, x.value) for x in at.multiselect], flush=True)
        for widget in at.multiselect:
            if widget.label == '베이스 외곽 레이어':
                widget.set_value(['JIG 외곽'])
            elif widget.label == '제품 형상 레이어':
                widget.set_value(['0', '부품'])
            elif widget.label == '피날홀 원 레이어':
                widget.set_value(['0'])
        at.run()
        assert not at.exception, str(at.exception)
        next(b for b in at.button if '배열 실행' in b.label).click().run()
        assert not at.exception, str(at.exception)
        assert not at.error, str(at.error)
        print('UI metrics:', [(x.label, x.value) for x in at.metric], flush=True)
        assert any(x.label == '배치 수량' and int(x.value) > 0 for x in at.metric)
        print('Streamlit UI PASS', flush=True)
