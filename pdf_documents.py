"""Deterministic Korean PDFs from frozen sales-record snapshots."""
from pathlib import Path
from functools import lru_cache
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


@lru_cache(maxsize=1)
def register_font():
    pdfmetrics.registerFont(TTFont('GrowthKorean', str(Path(__file__).parent / 'assets/fonts/NanumGothic-Regular.ttf')))


def render_pdf(kind, record, demo=False):
    if kind not in ('quote', 'delivery'):
        raise ValueError('지원하지 않는 PDF 문서 종류입니다.')
    register_font()
    from quote_pdf import render_sales_document
    return render_sales_document(kind, record, demo)
