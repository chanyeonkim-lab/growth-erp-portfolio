"""Customer-specific document output through the actual record creation workflow."""
import copy
import io
import unittest
from pypdf import PdfReader
import server
import workflow
from pdf_documents import render_pdf


class DynamicDocumentFieldsTests(unittest.TestCase):
    def setUp(self):
        self.state = server.demo_state()

    def command(self, action, **data):
        return workflow.apply(self.state, dict(action=action, data=data), 'demo')

    def quote(self, account='ac-0', **changes):
        data = dict(accountId=account, date='2026-10-03', title='교육관 분전반 제작',
                    attention='학교 시설 담당', paymentTerms='납품 후 30일',
                    items=[dict(name='철함', spec='600×800', quantity=3, unitPrice=150000),
                           dict(name='조립 공임', quantity=1, unit='식', unitPrice=200000)])
        data.update(changes)
        result, _ = self.command('quote.save', **data)
        return workflow.get(self.state, 'quotes', result['recordId'])

    def order(self, q):
        result, _ = self.command('quote.convert', quoteId=q['id'], date='2026-10-03')
        return workflow.get(self.state, 'orders', result['recordId'])

    def delivery(self, o, **overrides):
        data = dict(orderId=o['id'], date='2026-10-04',
                    items=[dict(orderLineId=o['items'][0]['id'], quantity=1)])
        data.update(overrides)
        result, _ = self.command('delivery.create', **data)
        return workflow.get(self.state, 'deliveries', result['recordId'])

    def document(self, kind, record):
        before = copy.deepcopy(record)
        pdf = PdfReader(io.BytesIO(render_pdf(kind, record, True)))
        self.assertEqual(record, before)
        return pdf, '\n'.join(p.extract_text() for p in pdf.pages)

    def test_two_customers_and_copied_quote_use_their_own_fields(self):
        first = self.quote()
        copied = self.quote('ac-1', mode='copy', sourceId=first['id'], title='스튜디오 폼 공장 제어반',
                            attention='스튜디오 폼 구매 담당', date='2026-10-05')
        for quote, expected, absent in (
            (first, ('루미나 캠퍼스', '교육관 분전반 제작', '학교 시설 담당', '2026년 10월 03일'), ('스튜디오 폼 구매 담당', '스튜디오 폼 공장 제어반')),
            (copied, ('스튜디오 폼', '스튜디오 폼 공장 제어반', '스튜디오 폼 구매 담당', '2026년 10월 05일'), ('루미나 캠퍼스', '학교 시설 담당')),
        ):
            _, text = self.document('quote', quote)
            for value in (*expected, quote['number']): self.assertIn(value, text)
            for value in (*absent, '납품장소'): self.assertNotIn(value, text)
        self.assertNotEqual(first['number'], copied['number'])

    def test_revision_updates_new_pdf_and_leaves_prior_fields_frozen(self):
        first = self.quote()
        _, old_pdf = self.command('document.generate', kind='quote', id=first['id'])
        original = copy.deepcopy(first)
        revised = self.quote(mode='revise', sourceId=first['id'], title='교육관 변경 규격', attention='새 담당자')
        _, text = self.document('quote', revised)
        self.assertIn('교육관 변경 규격', text); self.assertIn('새 담당자', text)
        self.assertNotIn('학교 시설 담당', text)
        self.state['accounts'][0]['name'] = '저장 후 변경된 고객명'
        self.state['settings']['representative'] = '저장 후 변경된 대표'
        _, frozen_text = self.document('quote', first)
        self.assertIn('루미나 캠퍼스', frozen_text); self.assertNotIn('저장 후 변경', frozen_text)
        self.assertEqual(first, original)
        _, repeated = self.command('document.generate', kind='quote', id=first['id'])
        self.assertIsNone(repeated)
        self.assertIn('학교 시설 담당', PdfReader(io.BytesIO(old_pdf)).pages[0].extract_text())

    def test_delivery_branding_reference_and_partial_amount_are_dynamic(self):
        for account, title, attention in (('ac-0', '교육관 분전반', '학교 담당'), ('ac-1', '공장 제어반', '스튜디오 폼 담당')):
            q = self.quote(account, title=title, attention=attention)
            o = self.order(q)
            # A newer quote must not replace the quote version attached to this order.
            self.quote(account, mode='revise', sourceId=q['id'], title='다음 버전 건명', attention='다음 버전 담당')
            d = self.delivery(o)
            self.assertEqual(d['attention'], attention)
            self.assertEqual(d['paymentTerms'], '납품 후 30일')
            # Legacy data should also be hidden by the new template, without mutating the record.
            d['deliveryPlace'] = '이전 장소값'
            pdf, text = self.document('delivery', d)
            for value in ('거래명세서', d['customer']['name'], title, attention, d['number'],
                          '납품일', '2026년 10월 04일', '납품 후 30일', '그로스 일렉트릭', '김데모',
                          '000-00-00000', '150,000', '15,000', '165,000'):
                self.assertIn(value, text)
            for value in (o['number'], '연결 주문', '다음 버전 건명', '다음 버전 담당', '715,000', '조립 공임', '납품장소', '이전 장소값', '견적 유효일'):
                self.assertNotIn(value, text)
            self.assertEqual(len(pdf.pages[0].images), 0)
            self.assertTrue(pdf.metadata.title.startswith('거래명세서'))

    def test_direct_order_reference_override_empty_reference_and_legacy_records(self):
        result, _ = self.command('order.save', accountId='ac-2', title='전화 주문', date='2026-10-03',
                                 items=[dict(name='단자대', quantity=3, unitPrice=20000)])
        o = workflow.get(self.state, 'orders', result['recordId'])
        for value in ('현장 담당자', ''):
            d = self.delivery(o, attention=value)
            _, text = self.document('delivery', d)
            self.assertEqual(d['attention'], value)
            self.assertIn('노바 일렉트로', text); self.assertIn('전화 주문', text)
            if value: self.assertIn(value, text)
            else: self.assertNotIn('현장 담당자', text)
        legacy = self.delivery(o)
        legacy.pop('attention'); legacy.pop('paymentTerms')
        self.document('delivery', legacy)
        old_quote = self.quote()
        old_quote['deliveryPlace'] = '삭제된 필드 값'
        _, text = self.document('quote', old_quote)
        self.assertNotIn('납품장소', text); self.assertNotIn('삭제된 필드 값', text)
