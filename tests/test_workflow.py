import copy
import io
import unittest
from pypdf import PdfReader
import server
import workflow
from pdf_documents import render_pdf


class SalesWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.state = server.demo_state()
        self.initial = copy.deepcopy(self.state)

    def run_action(self, action, **data):
        # Mirror endpoint rollback: failed commands never change the persisted state.
        candidate = copy.deepcopy(self.state)
        result, content = workflow.apply(candidate, dict(action=action, data=data), 'demo')
        server.validate_state(candidate)
        self.state = candidate
        return result, content

    def quote(self, **overrides):
        data = dict(accountId='ac-0', title='교육관 분전반 제작', date='2026-10-03',
                    items=[dict(name='철함', spec='600 × 800', section='자재', quantity=3, unit='개', unitPrice=150000),
                           dict(name='조립 공임', section='공임', quantity=1, unit='식', unitPrice=200000)])
        data.update(overrides)
        result, _ = self.run_action('quote.save', **data)
        return workflow.get(self.state, 'quotes', result['recordId'])

    def order(self, q):
        result, _ = self.run_action('quote.convert', quoteId=q['id'], date='2026-10-03')
        return workflow.get(self.state, 'orders', result['recordId'])

    def delivery(self, o, quantities):
        result, _ = self.run_action('delivery.create', orderId=o['id'], date='2026-10-04',
                                   items=[dict(orderLineId=r['id'], quantity=qty) for r, qty in zip(o['items'], quantities)])
        return workflow.get(self.state, 'deliveries', result['recordId'])

    def test_server_computes_totals_and_keeps_financial_records_unchanged(self):
        q=self.quote(net=1,vat=1,amount=1)
        self.assertEqual((q['net'],q['vat'],q['amount']),(650000,65000,715000))
        o=self.order(q);self.delivery(o,[1,0])
        for kind in ('invoices','expenses','receipts','allocations'):
            self.assertEqual(self.state[kind],self.initial[kind])

    def test_revision_copy_and_order_freeze_are_distinct(self):
        q=self.quote(); original=copy.deepcopy(q); o=self.order(q)
        revised=self.quote(mode='revise',sourceId=q['id'],items=[dict(name='변경 철함',quantity=4,unitPrice=250000)])
        self.assertEqual(revised['version'],2)
        self.assertEqual(revised['familyId'],q['id'])
        self.assertEqual(revised['number'],q['number'])
        self.assertEqual(workflow.get(self.state,'quotes',q['id']),original)
        self.assertEqual(workflow.get(self.state,'orders',o['id'])['items'],original['items'])
        with self.assertRaises(ValueError):self.quote(mode='revise',sourceId=q['id'])
        with self.assertRaises(ValueError):self.order(revised)
        copied=self.quote(mode='copy',sourceId=revised['id'])
        self.assertNotEqual(copied['familyId'],revised['familyId'])
        self.assertEqual(copied['version'],1);self.assertNotEqual(copied['number'],q['number'])
        self.order(copied)

    def test_same_version_save_and_version_delete_are_managed_safely(self):
        q=self.quote()
        created=q['createdAt']
        _,pdf=self.run_action('document.generate',kind='quote',id=q['id'])
        self.assertIsNotNone(pdf)
        old_document=workflow.get(self.state,'quotes',q['id'])['pdfDocumentId']
        updated=self.quote(mode='update',sourceId=q['id'],title='작성 중 저장',
                           changeNote='중간 저장',items=[dict(name='변경 철함',quantity=4,unitPrice=200000)])
        self.assertEqual(updated['id'],q['id'])
        self.assertEqual(updated['version'],1)
        self.assertEqual(updated['createdAt'],created)
        self.assertEqual(updated['amount'],880000)
        self.assertNotIn('pdfDocumentId',updated)
        self.assertEqual(len(self.state['quotes']),len(self.initial['quotes'])+1)
        self.assertEqual(workflow.get(self.state,'documents',old_document)['status'],'등록 완료')
        _,regenerated=self.run_action('document.generate',kind='quote',id=updated['id'])
        self.assertIsNotNone(regenerated)
        revised=self.quote(mode='revise',sourceId=updated['id'],items=[dict(name='최종 철함',quantity=5,unitPrice=200000)])
        self.run_action('quote.delete',id=updated['id'])
        self.assertIsNone(next((record for record in self.state['quotes'] if record['id']==updated['id']),None))
        self.assertEqual(workflow.get(self.state,'quotes',revised['id'])['version'],2)
        self.assertTrue(all(d.get('quoteId')!=updated['id'] for d in self.state['documents']))

    def test_quote_families_can_be_deleted_in_bulk_while_orders_and_files_are_preserved(self):
        first=self.quote();revised=self.quote(mode='revise',sourceId=first['id'])
        second=self.quote(title='두 번째 견적');kept=self.quote(title='남길 견적')
        order=self.order(revised)
        self.state['documents'].append(dict(id='quote-family-file',name='견적서.pdf',quoteId=first['id'],status='등록 완료'))
        groups=[workflow.family(first),workflow.family(second)]
        result,_=self.run_action('quote.family.delete',ids=groups)
        self.assertEqual(result['familyCount'],2);self.assertEqual(result['versionCount'],3)
        self.assertFalse(any(workflow.family(q) in groups for q in self.state['quotes']))
        self.assertIsNotNone(workflow.get(self.state,'quotes',kept['id']))
        saved_order=workflow.get(self.state,'orders',order['id'])
        self.assertEqual(saved_order.get('quoteId'),'');self.assertEqual(saved_order.get('quoteFamilyId'),'')
        self.assertNotIn('quoteSnapshot',saved_order);self.assertNotIn('quoteVersion',saved_order)
        saved_file=workflow.get(self.state,'documents','quote-family-file')
        self.assertEqual(saved_file.get('quoteId'),'');self.assertEqual(saved_file['status'],'견적 삭제됨 · 파일 보존')
        before=copy.deepcopy(self.state)
        with self.assertRaises(ValueError):self.run_action('quote.family.delete',ids=[workflow.family(kept),'missing-family'])
        self.assertEqual(self.state,before)

    def test_order_linked_quote_can_be_updated_or_deleted_without_deleting_order(self):
        q=self.quote();o=self.order(q)
        updated=self.quote(mode='update',sourceId=q['id'])
        self.assertEqual(updated['id'],q['id'])
        self.run_action('quote.delete',id=q['id'])
        kept=workflow.get(self.state,'orders',o['id']);self.assertEqual(kept.get('quoteId'),'')

    def test_order_can_be_edited_and_deleted_until_downstream_records_exist(self):
        q=self.quote();o=self.order(q);original_items=copy.deepcopy(o['items'])
        result,_=self.run_action('order.save',id=o['id'],accountId=o['accountId'],orderType=o['orderType'],
                                 title='수정한 주문명',date=o['date'],dueDate='2026-10-10',notes='일정 확인',items=o['items'])
        edited=workflow.get(self.state,'orders',result['recordId'])
        self.assertEqual(edited['title'],'수정한 주문명')
        self.assertEqual(edited['items'],original_items)
        self.run_action('order.save',id=o['id'],accountId=o['accountId'],orderType=o['orderType'],
                        title=edited['title'],date=o['date'],items=[dict(name='다른 품목',quantity=1,unitPrice=1)])
        self.assertEqual(workflow.get(self.state,'orders',o['id'])['items'][0]['name'],'다른 품목')
        self.run_action('order.delete',id=o['id'])
        self.assertFalse(any(record['id']==o['id'] for record in self.state['orders']))
        self.assertTrue(any(record['id']==q['id'] for record in self.state['quotes']))

    def test_order_delete_cascades_legacy_delivery_and_keeps_quote(self):
        q=self.quote();o=self.order(q);d=self.delivery(o,[1,0])
        result,_=self.run_action('order.delete',id=o['id'])
        self.assertEqual(result['deleted']['fulfillments'],1)
        self.assertFalse(any(record['id']==o['id'] for record in self.state['orders']))
        self.assertFalse(any(record['id']==d['id'] for record in self.state['deliveries']))
        self.assertTrue(any(record['id']==q['id'] for record in self.state['quotes']))

    def test_partials_zero_lines_overdelivery_and_reverse_cancellation(self):
        o=self.order(self.quote());d1=self.delivery(o,[1,0])
        self.assertEqual(d1['amount'],165000)
        self.assertEqual(workflow.get(self.state,'orders',o['id'])['status'],'일부 납품')
        with self.assertRaises(ValueError):self.delivery(o,[3,0])
        with self.assertRaises(ValueError):self.delivery(o,[0,0])
        d2=self.delivery(o,[2,1])
        self.assertEqual(d1['amount']+d2['amount'],o['amount'])
        self.assertEqual(workflow.get(self.state,'orders',o['id'])['status'],'납품 완료')
        with self.assertRaises(ValueError):self.run_action('delivery.void',id=d1['id'],reason='순서 확인')
        self.run_action('delivery.void',id=d2['id'],reason='납품 수량 정정')
        self.assertEqual(workflow.get(self.state,'orders',o['id'])['status'],'일부 납품')
        self.delivery(o,[2,1])
        self.assertEqual(workflow.get(self.state,'orders',o['id'])['status'],'납품 완료')

    def test_fractional_quantity_rounding_reconciles_to_full_order(self):
        o=self.order(self.quote(items=[dict(name='케이블',quantity=1,unit='m',unitPrice=7)]))
        ds=[self.delivery(o,[q]) for q in (.1,.2,.7)]
        self.assertEqual(sum(d['net'] for d in ds),o['net'])
        self.assertEqual(sum(d['vat'] for d in ds),o['vat'])

    def test_pdf_is_frozen_private_document_metadata_and_idempotent(self):
        q=self.quote()
        self.state['settings']['name']='회사명 변경'
        self.state['accounts'][0]['name']='고객명 변경'
        result,content=self.run_action('document.generate',kind='quote',id=q['id'])
        extracted='\n'.join(p.extract_text() for p in PdfReader(io.BytesIO(content)).pages)
        for word in ('견적서','루미나 캠퍼스','그로스 일렉트릭','715,000'):
            self.assertIn(word,extracted)
        self.assertNotIn('회사명 변경',extracted)
        self.assertNotIn('고객명 변경',extracted)
        repeat,again=self.run_action('document.generate',kind='quote',id=q['id'])
        self.assertEqual(result,repeat);self.assertIsNone(again)
        doc=workflow.get(self.state,'documents',result['documentId'])
        self.assertTrue(doc['generated']);self.assertEqual(doc['quoteId'],q['id'])
        changed=copy.deepcopy(self.state);changed['quotes'][-1]['title']='tamper'
        with self.assertRaises(ValueError):workflow.guard(self.state,changed)
        changed=copy.deepcopy(self.state);changed['documents'][-1]['snapshot']['net']=1
        with self.assertRaises(ValueError):workflow.guard(self.state,changed)

    def test_statement_uses_actual_delivery_quantities_and_not_quote_total(self):
        q=self.quote();o=self.order(q);d=self.delivery(o,[1,0])
        _,content=self.run_action('document.generate',kind='delivery',id=d['id'])
        extracted='\n'.join(p.extract_text() for p in PdfReader(io.BytesIO(content)).pages)
        self.assertIn('거래명세서',extracted);self.assertIn('165,000',extracted)
        self.assertNotIn('조립 공임',extracted);self.assertNotIn('715,000',extracted)

    def test_input_limits_dates_cross_customer_and_duplicate_lines(self):
        for qty in (-1,0,.0001,1.5,'NaN','Infinity',True):
            with self.subTest(qty=qty),self.assertRaises(ValueError):self.quote(items=[dict(name='품목',quantity=qty,unitPrice=1)])
        with self.assertRaises(ValueError):self.quote(validUntil='2026-10-01')
        q=self.quote()
        with self.assertRaises(ValueError):self.quote(mode='revise',sourceId=q['id'],accountId='ac-1')
        o=self.order(q)
        row=dict(orderLineId=o['items'][0]['id'],quantity=1)
        with self.assertRaises(ValueError):self.run_action('delivery.create',orderId=o['id'],date='2026-10-04',items=[row,row])
        with self.assertRaises(ValueError):self.run_action('delivery.create',orderId=o['id'],date='2026-10-01',items=[row])
        changed=copy.deepcopy(self.state);changed['deliveries'].append(dict(id='forged'))
        with self.assertRaises(ValueError):workflow.guard(self.state,changed)

    def test_historical_deduction_keeps_pdf_but_requires_review_before_order(self):
        q=self.quote(items=[dict(name='주문함',quantity=4,unitPrice=600000)])
        q['items'].append(dict(id='historical-deduction',name='기존품 차감',spec='',section='',unit='EA',
                               quantity=-5,unitPrice=300000,net=-1500000,vat=-150000,vatRate=10))
        workflow.total(q)
        before=copy.deepcopy(q)
        _,content=self.run_action('document.generate',kind='quote',id=q['id'])
        extracted='\n'.join(p.extract_text() for p in PdfReader(io.BytesIO(content)).pages)
        self.assertIn('-1,500,000',extracted)
        self.assertIn('990,000',extracted)
        orders=copy.deepcopy(self.state['orders'])
        with self.assertRaisesRegex(ValueError,'실제 납품 품목'):
            self.order(q)
        self.assertEqual(self.state['orders'],orders)
        revised=self.quote(mode='revise',sourceId=q['id'],items=[dict(name='확인된 납품 품목',quantity=1,unitPrice=900000)])
        self.assertEqual(self.order(revised)['amount'],990000)
        self.assertEqual(q['items'],before['items'])

    def test_legacy_samples_are_preserved_and_can_be_revised(self):
        legacy=self.state['quotes'][0];original=copy.deepcopy(legacy)
        revised=self.quote(mode='revise',sourceId=legacy['id'],number=legacy['number'])
        self.assertEqual(revised['version'],2)
        self.assertEqual(self.state['quotes'][0],original)
        changed=copy.deepcopy(self.state);changed['quotes'][0]['title']='tamper'
        with self.assertRaises(ValueError):workflow.guard(self.state,changed)
        old=server.demo_state();del old['deliveries'];workflow.normalize(old)
        self.assertEqual(old['orders'],self.initial['orders'])

    def test_many_korean_items_paginate_and_preserve_all_text(self):
        q=self.quote(items=[dict(name=f'교육관 분전반 상세품목 {i:03}',spec='스테인리스 함 / 상세 규격 '*4,quantity=1,unitPrice=10000) for i in range(80)])
        data=render_pdf('quote',q,True)
        pages=PdfReader(io.BytesIO(data)).pages
        self.assertGreater(len(pages),2);self.assertLess(len(data),3*1024*1024)
        extracted='\n'.join(p.extract_text() for p in pages)
        for i in range(80):self.assertIn(f'상세품목 {i:03}',extracted)
        self.assertIn('880,000',extracted)

    def test_old_converted_order_accepts_item_entry_without_losing_quote_reference(self):
        old=self.state['orders'][0]
        old['quoteId']=self.state['quotes'][0]['id']
        result,_=self.run_action('order.save',id=old['id'],accountId=old['accountId'],date=old['date'],title=old['title'],items=[dict(name='기존 주문 품목',quantity=1,unitPrice=100000)])
        restored=workflow.get(self.state,'orders',result['recordId'])
        self.assertEqual(restored['id'],old['id'])
        self.assertEqual(restored['quoteId'],old['quoteId'])
        self.delivery(restored,[1])
