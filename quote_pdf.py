"""Shared SG quotation and delivery statement layout from frozen record snapshots."""
import io
from pathlib import Path
from decimal import Decimal
from xml.sax.saxutils import escape
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
from company_profile import use_sg_seal

INK = colors.HexColor('#202020')
RULE = colors.HexColor('#444444')
PALE = colors.HexColor('#f3f4f5')
SEAL = Path(__file__).parent / 'assets/branding/sg-seal.png'


def korean_won(amount):
    """Whole-won wording, matching the amount rather than copying a template total."""
    if not amount:
        return '영원정'
    digits, units, groups = '영일이삼사오육칠팔구', ('', '십', '백', '천'), ('', '만', '억', '조')
    result = []
    for group, unit in enumerate(groups):
        block = (amount // (10000 ** group)) % 10000
        text = ''.join(digits[(block // (10 ** i)) % 10] + units[i]
                       for i in range(3, -1, -1) if (block // (10 ** i)) % 10)
        if text:
            result.insert(0, text + unit)
    return ''.join(result) + '원정'


def vat_caption(vat):
    return ' (부가세 포함)' if vat > 0 else ''


def render_sales_document(kind, record, demo=False):
    out = io.BytesIO()
    width = A4[0] - 56
    company, customer = record['company'], record['customer']
    is_quote = kind == 'quote'
    blank = record.get('isTemplate', False)
    label = '견적서' if is_quote else '거래명세서'
    reference = record['number'] + (f" / v{record['version']}" if is_quote and not blank else '')
    styles = {}
    for name, size, leading, align in (
        ('body', 9, 13, TA_LEFT), ('small', 8, 11, TA_LEFT), ('center', 9, 13, TA_CENTER),
        ('right', 8.5, 12, TA_RIGHT), ('title', 24, 32, TA_CENTER), ('total', 11, 16, TA_LEFT),
    ):
        styles[name] = ParagraphStyle(name, fontName='GrowthKorean', fontSize=size,
                                     leading=leading, textColor=INK, wordWrap='CJK', alignment=align)

    def p(value='', style='body'):
        return Paragraph(escape(str(value if value is not None else '')).replace('\n', '<br/>'), styles[style])

    def cash(value):
        return f'{int(value):,}'

    def qty(value):
        return format(Decimal(str(value)).normalize(), 'f')

    def table(rows, widths, extra=(), **kwargs):
        t = Table(rows, colWidths=widths, hAlign='LEFT', **kwargs)
        t.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 5), ('RIGHTPADDING', (0, 0), (-1, -1), 5),
            ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            *extra,
        ]))
        return t

    grid = [('GRID', (0, 0), (-1, -1), .55, RULE), ('BOX', (0, 0), (-1, -1), 1.1, INK)]
    doc = SimpleDocTemplate(out, pagesize=A4, leftMargin=22, rightMargin=22, topMargin=30, bottomMargin=34,
                            title=label + ' ' + record['number'], author=company['name'],
                            subject=record['id'], invariant=1)

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont('GrowthKorean', 7)
        canvas.setFillColor(colors.HexColor('#666666'))
        canvas.drawString(28, 24, ('기본 양식 | ' if blank else '') + label + ' | ' + reference[:65])
        canvas.drawRightString(A4[0] - 28, 24, str(document.page))
        if document.page > 1:
            canvas.drawString(28, A4[1] - 25, label + ' · 상세 내역 (계속)')
        canvas.restoreState()

    content = [p('견 적 서' if is_quote else '거 래 명 세 서', 'title'), Spacer(1, 14)]
    left_width, gap = width * .425, 10
    right_width = width - left_width - gap
    date_text = record['date'].replace('-', '년 ', 1).replace('-', '월 ', 1) + '일' if record['date'] else ''
    left_rows = [(label, value) for label, value in (
        ('견적서 No' if is_quote else '명세서 No', reference), ('견적일' if is_quote else record.get('documentDateLabel', '납품일'), date_text),
        ('상 호', customer['name'] + ' 귀하'), ('건 명', record['title']),
        ('참 조', record.get('attention', '')),
    )]
    left = table([[p(label, 'small'), p(value)] for label, value in left_rows], [53, left_width - 53],
                 grid)
    left_stack = [left, Spacer(1, 8), p('아래와 같이 견적합니다.' if is_quote else '아래와 같이 거래 내역을 확인합니다.', 'center')]

    seal_width = 29 if use_sg_seal(company) else 0
    representative_width = 75
    identity_widths = [representative_width - 10 - seal_width] + ([seal_width] if seal_width else [])
    identity_cells = [p(company.get('representative', ''), 'small')]
    if seal_width:
        # Original embedded PNG from the user's Excel; no redraw, alteration or public URL.
        identity_cells.append(Image(str(SEAL), width=27, height=29))
    identity = table([identity_cells], identity_widths,
                     [('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 1),
                      ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0)])
    rows = [
        [p('공\n\n급\n\n자', 'center'), p('등록번호', 'small'), p(company.get('businessNumber', '')), '', ''],
        ['', p('상 호 명', 'small'), p(company['name'], 'small'), p('성명', 'small'), identity],
        ['', p('주 소', 'small'), p(company.get('address', ''), 'small'), '', ''],
        ['', p('업 태', 'small'), p(company.get('businessType', ''), 'small'), p('종목', 'small'), p(company.get('businessItem', ''), 'small')],
        ['', p('전화번호', 'small'), p(company.get('phone', ''), 'small'), p('팩스', 'small'), p(company.get('fax', ''), 'small')],
        ['', p('이 메 일', 'small'), p(company.get('email', ''), 'small'), '', ''],
    ]
    supplier = table(rows, [16, 47, right_width - 16 - 47 - 28 - representative_width, 28, representative_width], grid + [('SPAN', (0, 0), (0, -1)),
                     ('SPAN', (2, 0), (4, 0)), ('SPAN', (2, 2), (4, 2)), ('SPAN', (2, 5), (4, 5)),
                     ('LEFTPADDING', (0, 0), (0, -1), 2), ('RIGHTPADDING', (0, 0), (0, -1), 2),
                     ('LEFTPADDING', (4, 1), (4, 1), 0), ('RIGHTPADDING', (4, 1), (4, 1), 0)])
    header = table([[left_stack, '', supplier]], [left_width, gap, right_width], [
        ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0), ('TOPPADDING', (0, 0), (-1, -1), 0), ('BOTTOMPADDING', (0, 0), (-1, -1), 0)])
    content += [header, Spacer(1, 12)]
    total = record['net'] + record['vat']
    condition_rows = []
    if is_quote:
        condition_rows.append([p('납 기', 'center'), p(record.get('deliveryTerms', ''), 'small'), p('지불조건', 'center'), p(record.get('paymentTerms', ''), 'small')])
    elif record.get('paymentTerms'):
        condition_rows.append([p('지불조건', 'center'), p(record['paymentTerms'], 'small'), '', ''])
    amount_row = len(condition_rows)
    condition_rows.append([p('합계금액', 'center'), p('' if blank else '금 ' + korean_won(total) + vat_caption(record['vat']) + '\n₩ ' + cash(total), 'total'), '', ''])
    spans = [('SPAN', (1, amount_row), (-1, amount_row))]
    if not is_quote and amount_row:
        spans.append(('SPAN', (1, 0), (-1, 0)))
    conditions = table(condition_rows, [59, width * .47 - 59, 58, width * .53 - 58], grid + spans)
    content += [conditions, Spacer(1, 9)]

    # Keep amounts on one line even at the application's maximum supported value.
    def numeric_width(key, minimum, formatter=cash):
        return max(minimum, max(stringWidth(formatter(item[key]), 'GrowthKorean', 8.5)
                                for item in record['items']) + 11)
    numeric_widths = [numeric_width('quantity', 40, qty), numeric_width('unitPrice', 65),
                      max(numeric_width('net', 70), stringWidth(cash(record['net']), 'GrowthKorean', 8.5) + 11),
                      max(numeric_width('vat', 53), stringWidth(cash(record['vat']), 'GrowthKorean', 8.5) + 11)]
    widths = [24, width - 51 - sum(numeric_widths), 27, *numeric_widths]
    rows = [[p(t, 'center') for t in ('No', '품 명 & 규 격', '단위', '수량', '단가', '공급가액', '부가세')]]
    extra = [('BACKGROUND', (0, 0), (-1, 0), PALE)]
    for number, item in enumerate(record['items'], 1):
        description = item['name'] + ('\n' + item['spec'] if item.get('spec') else '')
        rows.append([p('' if blank else number, 'center'), p(description), p(item.get('unit', ''), 'center'),
                     p('' if blank else qty(item['quantity']), 'right'), p('' if blank else cash(item['unitPrice']), 'right'),
                     p('' if blank else cash(item['net']), 'right'), p('' if blank else cash(item['vat']), 'right')])
    end = len(rows)
    rows.append(['', p('- 이하 여백 -', 'center'), '', '', '', '', ''])
    rows.append(['', p('[ 합계 ]'), '', '', '', p('' if blank else cash(record['net']), 'right'), p('' if blank else cash(record['vat']), 'right')])
    # Mixed units have no meaningful aggregate quantity; only the amounts are totalled.
    extra += [('SPAN', (1, end + 1), (4, end + 1)), ('BACKGROUND', (0, end + 1), (-1, end + 1), PALE),
              ('NOSPLIT', (0, end - 1), (-1, end + 1))]
    content += [table(rows, widths, grid + extra, repeatRows=1, minRowHeights=[24] * len(rows)), Spacer(1, 10)]

    # Notes may span pages; avoid an oversized KeepTogether that would lose long input.
    memo = table([[p('MEMO', 'small')], [p(record.get('notes') or ' ', 'body')]], [width], grid,
                 repeatRows=1, splitInRow=1, minRowHeights=[21, 58])
    memo.setStyle(TableStyle([('VALIGN', (0, 1), (-1, -1), 'TOP')]))
    content += [memo]
    extras = []
    if is_quote and record.get('validUntil'):
        extras.append(p('견적 유효일  ' + record['validUntil'], 'small'))
    if company.get('bankAccount'):
        extras.append(p('입금 계좌  ' + ' / '.join(company.get(k, '') for k in ('bank', 'bankAccount', 'bankHolder') if company.get(k)), 'small'))
    content += [Spacer(1, 8), *extras]
    doc.build(content, onFirstPage=footer, onLaterPages=footer)
    return out.getvalue()
