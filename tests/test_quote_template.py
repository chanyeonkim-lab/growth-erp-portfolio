import copy
import io
import unittest
from pypdf import PdfReader
import server
import workflow
from pdf_documents import render_pdf
from quote_pdf import korean_won
from company_profile import initialize_company


class QuoteTemplateTests(unittest.TestCase):
    def quote(self, **data):
        state = server.demo_state()
        payload = dict(accountId='ac-0', title='분전반 제작', date='2026-10-03',
                       items=[dict(name='철함', spec='600×800', section='자재', quantity=3, unitPrice=150000)],
                       attention='시설 담당자', deliveryPlace='고객 지정 장소')
        payload.update(data)
        result, _ = workflow.apply(state, dict(action='quote.save', data=payload), 'demo')
        return workflow.get(state, 'quotes', result['recordId'])

    def pdf(self, q, demo=False):
        return PdfReader(io.BytesIO(render_pdf('quote', q, demo)))

    def test_reference_template_has_supplier_seal_conditions_and_exact_amounts(self):
        q = self.quote(); saved = copy.deepcopy(q)
        pdf = self.pdf(q)
        text = '\n'.join(page.extract_text() for page in pdf.pages)
        for term in ('그로스 일렉트릭', '김데모', '000-00-00000', '010-0000-0000', '02-000-0000',
                     '가상시 데모구 그로스길 10', '제조,도소매', '전기콘트롤박스', 'hello@growth.example', '시설 담당자',
                     '450,000', '45,000', '495,000', '사십구만오천원정', '부가세 포함', 'MEMO'):
            self.assertIn(term, text)
        self.assertEqual(len(pdf.pages[0].images), 0)
        self.assertNotIn('테스트용', text)
        self.assertNotIn('납품장소', text)
        self.assertNotIn('고객 지정 장소', text)
        self.assertNotIn('자재', text)
        self.assertEqual(q, saved)  # Rendering never changes a frozen quotation.

    def test_zero_vat_omits_tax_caption_and_categories_are_not_customer_facing(self):
        q = self.quote(items=[dict(name='VAT 0 품목', spec='규격 표시', section='표시금지구분',
                                   quantity=1, unitPrice=150000, vatRate=0)])
        text = '\n'.join(page.extract_text() for page in self.pdf(q).pages)
        self.assertNotIn('부가세 0원', text)
        self.assertNotIn('부가세 포함', text)
        self.assertIn('십오만원정', text)
        self.assertNotIn('표시금지구분', text)
        self.assertIn('규격 표시', text)

    def test_company_profile_respects_overrides_and_does_not_brand_other_business(self):
        custom = dict(name='그로스 일렉트릭', address='변경 주소', phone='', representative='변경 대표자')
        initialize_company(custom)
        self.assertEqual(custom['address'], '변경 주소'); self.assertEqual(custom['phone'], '')
        other = dict(name='다른 기업', businessNumber='123-45-67890')
        self.assertEqual(initialize_company(other), dict(name='다른 기업', businessNumber='123-45-67890'))
        q = self.quote()
        for company in (custom, dict(q['company'], businessNumber='123-45-67890'), other):
            with self.subTest(company=company['name']):
                q['company'] = company
                pdf = self.pdf(q)
                self.assertFalse(pdf.pages[0].images)
                text = '\n'.join(page.extract_text() for page in pdf.pages)
                if company is other or company is custom:
                    self.assertNotIn('부천시', text)

    def test_large_amounts_long_notes_and_multiple_pages_preserve_content(self):
        q = self.quote(items=[dict(name=f'상세품목 {i:03}', spec='긴 규격 설명 '*12,
                                  section=f'구분 {i // 4}', quantity=1, unitPrice=10000000000)
                             for i in range(35)], notes='검토 사항\n' * 230 + '마지막 메모 확인')
        pdf = self.pdf(q, True)
        text = '\n'.join(page.extract_text() for page in pdf.pages)
        self.assertGreater(len(pdf.pages), 2)
        self.assertIn('마지막 메모 확인', text)
        self.assertIn('350,000,000,000', text)
        self.assertIn('35,000,000,000', text)
        self.assertNotIn('테스트용', text)
        for i in range(35):
            self.assertIn(f'상세품목 {i:03}', text)
        self.assertEqual(korean_won(2376990), '이백삼십칠만육천구백구십원정')
        self.assertEqual(korean_won(0), '영원정')
