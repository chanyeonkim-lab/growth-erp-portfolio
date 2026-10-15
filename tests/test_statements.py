import copy
import os
import unittest
from unittest.mock import patch
import server
import workflow
import tax_provider
import statement_workflow


class StatementTests(unittest.TestCase):
    def setUp(self):
        self.s = server.empty_state()
        self.s['accounts'] = [dict(id='a', name='가상 고객', kind='고객사'), dict(id='b', name='다른 고객', kind='고객사'), dict(id='v', name='가상 매입처', kind='매입처')]

    def run_command(self, action, **data):
        s = copy.deepcopy(self.s)
        result, content = workflow.apply(s, dict(action=action, data=data), 'demo')
        server.validate_state(s)
        self.s = s
        return result

    def delivery(self, account='a', date='2026-10-01', quantity=1):
        r = self.run_command('order.save', accountId=account, title='가상 분전반', date=date, items=[dict(name='분전반', quantity=quantity, unitPrice=500000)])
        order = workflow.get(self.s, 'orders', r['recordId'])
        r = self.run_command('delivery.create', orderId=order['id'], date=date, items=[dict(orderLineId=order['items'][0]['id'], quantity=quantity)])
        return workflow.get(self.s, 'deliveries', r['recordId'])

    def confirm(self, delivery=None, **extra):
        d = delivery or self.delivery()
        self.run_command('document.generate', kind='delivery', id=d['id'])
        r = self.run_command('statement.confirm', id=d['id'], confirmed=True, **extra)
        return workflow.get(self.s, 'invoices', r['recordId'])

    def doc(self, key):
        self.s['documents'].append(dict(id=key, name=key+'.pdf', date='2026-10-01', kind='거래명세서', status='확인 필요'))

    def purchase(self, doc='doc', **extra):
        if not any(d['id'] == doc for d in self.s['documents']): self.doc(doc)
        data = dict(documentId=doc, accountId='v', date='2026-10-01', kind='거래명세서', category='자재비', net=300000, vat=30000, confirmed=True)
        data.update(extra)
        return self.run_command('statement.purchase', **data)

    def test_pdf_is_not_a_sale_confirmation_is_idempotent_and_uses_source_amounts(self):
        d = self.delivery()
        with self.assertRaises(ValueError): self.run_command('statement.confirm', id=d['id'], confirmed=True)
        self.run_command('document.generate', kind='delivery', id=d['id'])
        self.assertEqual(self.s['invoices'], [])
        with self.assertRaises(ValueError): self.run_command('statement.confirm', id=d['id'])
        r = self.run_command('statement.confirm', id=d['id'], confirmed=True, net=1, vat=1, date='2026-10-03')
        sale = self.s['invoices'][0]
        self.assertEqual((sale['net'], sale['vat'], sale['date']), (500000, 50000, '2026-10-01'))
        self.assertEqual(self.run_command('statement.confirm', id=d['id'], confirmed=True)['recordId'], r['recordId'])
        self.assertEqual(len(self.s['invoices']), 1)
        self.assertNotIn('manualTax', sale)

    def test_advance_is_allocated_once_without_changing_received_money(self):
        d = self.delivery()
        self.s['receipts'] = [dict(id='r', accountId='a', orderId=d['orderId'], date='2026-09-30', amount=200000)]
        original = copy.deepcopy(self.s['receipts'])
        sale = self.confirm(d, useCredit=True)
        self.assertEqual(sum(a['amount'] for a in self.s['allocations']), 200000)
        self.assertEqual(self.s['allocations'][0]['date'], sale['date'])
        self.run_command('statement.confirm', id=d['id'], confirmed=True, useCredit=True)
        self.assertEqual(len(self.s['allocations']), 1)
        self.assertEqual(self.s['receipts'], original)
        with self.assertRaises(ValueError): self.run_command('statement.cancel', id=sale['id'], reason='오입력')

    def test_manual_tax_groups_statements_without_changing_revenue_or_dates(self):
        first = self.confirm(self.delivery(date='2026-09-30'))
        second = self.confirm(self.delivery(date='2026-10-01'))
        before = [(i['id'], i['net'], i['vat'], i['date']) for i in self.s['invoices']]
        keys = [first['id'], second['id']]
        self.run_command('statement.tax-confirm', invoiceIds=keys, date='2026-10-02', approval='20260929-10260929-79663401', confirmed=True)
        self.assertEqual(before, [(i['id'], i['net'], i['vat'], i['date']) for i in self.s['invoices']])
        self.assertEqual(self.s['invoices'][0]['manualTax']['net'], 1000000)
        self.assertTrue(self.run_command('statement.tax-confirm', invoiceIds=keys, date='2026-10-02', confirmed=True)['duplicate'])
        self.run_command('statement.tax-reset', id=first['id'], reason='표시 정정')
        self.assertTrue(all(not i.get('manualTax') and len(i['taxHistory']) == 1 for i in self.s['invoices']))

    def test_hometax_task_toggle_only_changes_task_metadata_and_retries_are_idempotent(self):
        d = self.delivery()
        self.s['receipts'] = [dict(id='r', accountId='a', orderId=d['orderId'], date='2026-09-30', amount=200000)]
        sale = self.confirm(d, useCredit=True)
        before = copy.deepcopy(self.s)
        for done in (True, False, True):
            self.run_command('statement.hometax-task', id=sale['id'], done=done)
            inv = workflow.get(self.s, 'invoices', sale['id'])
            self.assertEqual(statement_workflow.hometax_done(inv), done)
            self.assertEqual(bool(inv['hometaxTask']['completedAt']), done)
            current = copy.deepcopy(self.s)
            unchanged = workflow.get(current, 'invoices', sale['id'])
            unchanged.pop('hometaxTask'); unchanged.pop('hometaxTaskHistory')
            self.assertEqual(current, before)
            unchanged_retry = copy.deepcopy(self.s)
            self.assertTrue(self.run_command('statement.hometax-task', id=sale['id'], done=done)['duplicate'])
            self.assertEqual(self.s, unchanged_retry)
        self.assertEqual(len(self.s['invoices'][0]['hometaxTaskHistory']), 3)
        for invalid in ('true', 1, None):
            with self.assertRaises(ValueError): self.run_command('statement.hometax-task', id=sale['id'], done=invalid)
        forged = copy.deepcopy(self.s); forged['invoices'][0]['hometaxTask']['done'] = False
        with self.assertRaises(ValueError): workflow.guard(self.s, forged)

    def test_legacy_group_tasks_can_be_reopened_individually_and_do_not_block_record_cancellation(self):
        a = self.confirm(); b = self.confirm()
        self.run_command('statement.tax-confirm', invoiceIds=[a['id'], b['id']], date='2026-10-02', confirmed=True)
        original = copy.deepcopy(self.s['invoices'][0]['manualTax'])
        self.assertTrue(all(statement_workflow.hometax_done(i) for i in self.s['invoices']))
        self.run_command('statement.hometax-task', id=a['id'], done=False)
        self.assertFalse(statement_workflow.hometax_done(self.s['invoices'][0]))
        self.assertTrue(statement_workflow.hometax_done(self.s['invoices'][1]))
        self.assertTrue(all(i['manualTax'] == original for i in self.s['invoices']))
        self.run_command('statement.cancel', id=b['id'], reason='잘못 확정한 기록')
        self.assertEqual(self.s['invoices'][1]['status'], 'void')
        with self.assertRaises(ValueError): self.run_command('statement.hometax-task', id=b['id'], done=False)
        self.s['invoices'].append(dict(id='old', accountId='a', date='2026-10-01', number='old', net=1, vat=0, status='issued'))
        with self.assertRaises(ValueError): self.run_command('statement.hometax-task', id='old', done=True)

    def test_tax_confirmation_rejects_cross_customer_and_duplicate_approval(self):
        a = self.confirm(); b = self.confirm(self.delivery(account='b'))
        with self.assertRaises(ValueError): self.run_command('statement.tax-confirm', invoiceIds=[a['id'], b['id']], date='2026-10-02', confirmed=True)
        approval = '202609291026092979663401'
        self.run_command('statement.tax-confirm', invoiceIds=[a['id']], date='2026-10-02', approval=approval, confirmed=True)
        with self.assertRaises(ValueError): self.run_command('statement.tax-confirm', invoiceIds=[b['id']], date='2026-10-02', approval=approval, confirmed=True)

    def test_existing_sales_candidate_requires_review_and_links_without_adding(self):
        d = self.delivery()
        self.s['invoices'].append(dict(id='old', accountId='a', orderId=d['orderId'], date=d['date'], number='old-1', net=d['net'], vat=d['vat'], status='issued'))
        with self.assertRaises(ValueError): self.confirm(d)
        self.confirm(d, existingInvoiceId='old')
        self.assertEqual(len(self.s['invoices']), 1)
        self.assertEqual(self.s['invoices'][0]['id'], 'old')

    def test_confirmed_delivery_and_managed_records_cannot_be_rewritten(self):
        sale = self.confirm()
        with self.assertRaises(ValueError): self.run_command('delivery.void', id=sale['sourceDeliveryId'], reason='수량 수정')
        changed = copy.deepcopy(self.s); changed['invoices'][0]['net'] = 1
        with self.assertRaises(ValueError): workflow.guard(self.s, changed)
        with self.assertRaises(ValueError): server.validate_state(changed)
        self.run_command('statement.cancel', id=sale['id'], reason='잘못 확정')
        self.run_command('delivery.void', id=sale['sourceDeliveryId'], reason='수량 정정')
        self.assertEqual(self.s['invoices'][0]['status'], 'void')

    def test_two_supplier_documents_share_one_cost_without_double_count(self):
        result = self.purchase()
        self.doc('tax')
        with self.assertRaises(ValueError): self.purchase('tax', kind='세금계산서')
        self.purchase('tax', kind='세금계산서', existingExpenseId=result['recordId'], date='2026-10-02')
        self.assertEqual(len(self.s['expenses']), 1)
        self.assertEqual(len([d for d in self.s['documents'] if d.get('expenseId') == result['recordId']]), 2)
        self.assertTrue(self.purchase('tax')['duplicate'])
        self.assertEqual(self.s['expenses'][0]['paidDate'], '')
        self.assertFalse(self.s['expenses'][0]['vatDeductible'])

    def test_equal_amount_separate_transactions_require_reason(self):
        self.purchase()
        with self.assertRaises(ValueError): self.purchase('second', separateConfirmed=True)
        self.purchase('second', separateConfirmed=True, separateReason='다른 주문의 동일 규격 부품')
        self.assertEqual(len(self.s['expenses']), 2)
        with self.assertRaises(ValueError): self.purchase('third', confirmed=False)

    def test_document_link_checks_supplier_and_both_amounts_not_only_total(self):
        e = self.purchase()['recordId']
        for fields in (dict(net=300001, vat=29999), dict(accountId='a')):
            with self.subTest(fields=fields), self.assertRaises(ValueError): self.purchase('other', existingExpenseId=e, **fields)
        d = self.delivery(); self.run_command('document.generate', kind='delivery', id=d['id'])
        document = self.s['documents'][-1]
        with self.assertRaises(ValueError): self.purchase(document['id'])

    def test_cost_split_changes_order_costs_not_total_expense_and_rejects_overallocation(self):
        first = self.delivery(); second = self.delivery()
        e = self.purchase(vatDeductible=True)['recordId']
        self.run_command('statement.cost', id=e, orderId=first['orderId'], amount=200000)
        self.run_command('statement.cost', id=e, orderId=second['orderId'], amount=100000)
        with self.assertRaises(ValueError): self.run_command('statement.cost', id=e, orderId=second['orderId'], amount=100001)
        self.assertEqual(len(self.s['expenses']), 1)
        self.assertEqual(sum(c['amount'] for c in self.s['costAllocations']), 300000)
        self.run_command('statement.cost', id=e, orderId=first['orderId'], amount=0)
        self.assertEqual(len(self.s['costAllocations']), 1)

    def test_expense_edits_keep_history_and_source_documents(self):
        e = self.purchase()['recordId']
        with self.assertRaises(ValueError): self.run_command('statement.expense', id=e, net=290000, vat=29000, date='2026-10-01', description='정정')
        self.run_command('statement.expense', id=e, net=290000, vat=29000, date='2026-10-01', description='정정', reason='원본 금액 재확인', paidDate='2026-10-02')
        expense = self.s['expenses'][0]
        self.assertEqual(expense['history'][0]['previous']['net'], 300000)
        self.assertEqual(self.s['documents'][0]['reviewedAmounts']['net'], 300000)
        forged = copy.deepcopy(self.s); forged['documents'][0]['expenseId'] = 'another'
        with self.assertRaises(ValueError): workflow.guard(self.s, forged)

    def test_provider_is_off_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            for workspace in ('demo', 'live'):
                self.assertEqual(tax_provider.settings(workspace)['mode'], 'disabled')
                with self.assertRaises(ValueError): tax_provider.check(workspace, self.s['settings'])
