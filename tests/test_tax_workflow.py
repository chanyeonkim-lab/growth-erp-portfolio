import copy
import json
import os
import time
import unittest
from unittest.mock import patch

import server
import workflow
import tax_provider
import tax_workflow as tax


class TaxWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.s = server.empty_state()
        self.s['settings'].update(name='가상 공급사',businessNumber='8888800001',representative='테스트',email='sender@example.invalid')
        self.s['accounts'] = [dict(id='a',name='가상 고객',kind='고객사',businessNumber='8888800003',representative='고객',email='recipient@example.invalid')]
        self.env = patch.dict(os.environ,{'GROWTH_TAX_MODE':'simulation'});self.env.start();self.addCleanup(self.env.stop)

    def command(self,action,**data):
        candidate=copy.deepcopy(self.s)
        result,_=workflow.apply(candidate,dict(action=action,data=data),'demo')
        server.validate_state(candidate);self.s=candidate
        return result

    def external(self,action,**data):
        candidate=copy.deepcopy(self.s)
        op=tax.prepare(candidate,dict(action=action,data=data),'demo')
        server.validate_state(candidate);self.s=candidate
        self.s,result=tax.complete(self.s,op,tax_provider.execute(op),server.validate_state)
        self.assertNotIn('notice',result,result)
        return result

    def order(self):
        result=self.command('order.save',accountId='a',title='가상 분전반',date='2026-10-03',items=[dict(name='철함',quantity=3,unitPrice=150000),dict(name='조립',quantity=1,unitPrice=200000)])
        return workflow.get(self.s,'orders',result['recordId'])

    def draft(self,order=None,deliveryId=''):
        order=order or self.order()
        result=self.command('tax.draft',orderId=order['id'],deliveryId=deliveryId,date='2026-10-03')
        return workflow.get(self.s,'taxDrafts',result['recordId'])

    def issued(self):
        draft=self.draft()
        self.external('draft.issue',id=draft['id'],confirmed=True)
        self.external('draft.status',id=draft['id'])
        return workflow.get(self.s,'taxDrafts',draft['id'])

    def collect(self,direction='BUY'):
        j=self.external('collect.start',direction=direction,start='2026-10-01',end='2026-10-31')
        self.external('collect.poll',id=j['recordId'])
        return workflow.get(self.s,'taxJobs',j['recordId'])

    def test_simulation_never_calls_network_and_does_not_book_collection(self):
        with patch('tax_provider.subprocess.run',side_effect=AssertionError('network path')):
            job=self.collect()
        self.assertEqual((job['status'],job['added']),('complete',2))
        self.assertEqual(self.s['expenses'],[])
        self.assertEqual(self.s['invoices'],[])
        self.assertTrue(all(r['approval'].startswith('SIM') for r in self.s['taxRecords']))

    def test_repeated_collection_deduplicates_and_conflicts_preserve_original(self):
        self.collect();original=copy.deepcopy(self.s['taxRecords'][0]);j=self.collect()
        self.assertEqual(len(self.s['taxRecords']),2);self.assertEqual(j['duplicates'],2)
        changed=dict(original,net=1)
        row,status=tax.upsert(self.s,changed)
        self.assertEqual(status,'conflict');self.assertEqual(row['net'],original['net'])
        with self.assertRaises(ValueError):self.command('tax.book',id=row['id'],createAccount=True)

    def test_book_purchase_dedup_expense_and_allocate_without_counting_twice(self):
        o1=self.order();o2=self.order();self.collect();r=self.s['taxRecords'][0]
        self.command('tax.book',id=r['id'],createAccount=True,vatDeductible=True)
        self.command('tax.book',id=r['id'],createAccount=True)
        self.assertEqual(len(self.s['expenses']),1)
        self.command('tax.cost',id=r['id'],rows=[dict(lineId='1',orderId=o1['id'],amount=100000),dict(lineId='1',orderId=o2['id'],amount=200000)])
        self.assertEqual(sum(c['amount'] for c in self.s['costAllocations']),300000)
        self.assertEqual(len(self.s['expenses']),1)
        before=copy.deepcopy(self.s)
        with self.assertRaises(ValueError):self.command('tax.cost',id=r['id'],rows=[dict(lineId='1',orderId=o1['id'],amount=300001)])
        self.assertEqual(self.s,before)
        with self.assertRaises(ValueError):self.command('tax.expense',id=self.s['expenses'][0]['id'],vatDeductible=False)

    def test_existing_manual_ledger_links_without_duplicate_and_signed_forgery_denied(self):
        self.collect('SELL');r=self.s['taxRecords'][0]
        self.s['invoices'].append(dict(id='manual',accountId='a',date=r['date'],number=r['approval'][:8]+'-'+r['approval'][8:].lower(),net=r['net'],vat=r['vat'],status='issued'))
        self.command('tax.book',id=r['id'],accountId='a')
        self.assertEqual(len(self.s['invoices']),1)
        self.assertEqual(self.s['taxRecords'][0]['ledgerId'],'manual')
        forged=copy.deepcopy(self.s);forged['invoices'][0]['net']=0
        with self.assertRaises(ValueError):workflow.guard(self.s,forged)
        forged=copy.deepcopy(self.s);forged['taxRecords']=[]
        with self.assertRaises(ValueError):workflow.guard(self.s,forged)
        forged=copy.deepcopy(self.s);forged['expenses'].append(dict(id='fake',date='2026-10-03',net=-1,vat=0,category='자재비'))
        with self.assertRaises(ValueError):server.validate_state(forged)

    def test_matching_amount_is_only_candidate_and_mismatch_is_rejected(self):
        self.collect('SELL');r=self.s['taxRecords'][0]
        self.s['invoices'].append(dict(id='manual',accountId='a',date=r['date'],number='local-1',net=r['net'],vat=r['vat'],status='issued'))
        with self.assertRaises(ValueError):self.command('tax.book',id=r['id'],accountId='a')
        self.s['invoices'][0]['net']+=1
        with self.assertRaises(ValueError):self.command('tax.book',id=r['id'],accountId='a',existingId='manual')
        self.assertEqual(len(self.s['invoices']),1)

    def test_issuance_books_once_and_repeated_status_never_duplicates(self):
        d=self.issued();self.assertEqual(d['stateCode'],304)
        self.assertEqual(len(self.s['invoices']),1);self.assertEqual(self.s['invoices'][0]['net'],650000)
        self.external('draft.status',id=d['id']);self.assertEqual(len(self.s['invoices']),1)
        with self.assertRaises(ValueError):self.external('draft.issue',id=d['id'],confirmed=True)
        with self.assertRaises(ValueError):self.command('tax.save',id=d['id'],date='2026-10-04')
        with self.assertRaises(ValueError):self.draft(workflow.get(self.s,'orders',d['orderId']))

    def test_partial_deliveries_can_be_billed_separately_but_not_as_whole_order(self):
        o=self.order()
        def delivery(qty):
            r=self.command('delivery.create',orderId=o['id'],date='2026-10-03',items=[dict(orderLineId=o['items'][0]['id'],quantity=qty)])
            return r['recordId']
        first=delivery(1);d=self.draft(o,first)
        self.external('draft.issue',id=d['id'],confirmed=True)
        self.assertEqual(self.s['invoices'][0]['net'],150000)
        second=delivery(2);self.draft(o,second)
        with self.assertRaises(ValueError):self.draft(o,first)
        with self.assertRaises(ValueError):self.draft(o)
        with self.assertRaises(ValueError):self.command('delivery.void',id=second,reason='test')

    def test_timeout_keeps_frozen_request_and_unknown_outcome_can_be_recovered(self):
        d=self.draft();op=tax.prepare(self.s,dict(action='draft.issue',data=dict(id=d['id'],confirmed=True)),'demo')
        key=d['key'];frozen=copy.deepcopy(d['payload'])
        self.s,result=tax.complete(self.s,op,dict(error='timeout',uncertain=True),server.validate_state)
        self.assertIn('notice',result)
        with self.assertRaises(ValueError):self.external('draft.issue',id=d['id'],confirmed=True)
        self.external('draft.status',id=d['id'])
        d=workflow.get(self.s,'taxDrafts',d['id'])
        self.assertEqual(d['key'],key);self.assertEqual(d['payload'],frozen);self.assertEqual(len(self.s['invoices']),1)

    def test_interrupted_attempt_blocks_concurrent_calls_and_late_results(self):
        d=self.draft();op=tax.prepare(self.s,dict(action='draft.issue',data=dict(id=d['id'],confirmed=True)),'demo')
        with self.assertRaises(ValueError):tax.prepare(self.s,dict(action='draft.status',data=dict(id=d['id'])),'demo')
        d['operation']['started']=time.time()-101
        newer=tax.prepare(self.s,dict(action='draft.status',data=dict(id=d['id'])),'demo')
        result=tax.finish(self.s,op,tax_provider.simulate(op))
        self.assertIn('notice',result);self.assertEqual(len(self.s['invoices']),0)
        self.s,result=tax.complete(self.s,newer,tax_provider.simulate(newer),server.validate_state)
        self.assertEqual(len(self.s['invoices']),1)

    def test_return_correction_preserves_original_and_reduces_receivable(self):
        d=self.issued();original=copy.deepcopy(self.s['invoices'][0])
        r=self.command('tax.correct',id=d['id'],modifyCode=3,date='2026-10-04',net=-100000,vat=-10000)
        self.external('draft.issue',id=r['recordId'],confirmed=True)
        self.assertEqual(self.s['invoices'][0],original)
        self.assertEqual(tax.adjusted_gross(self.s,original),605000)
        self.assertEqual(tax.adjusted_gross(self.s,original,'2026-10-03'),715000)
        self.assertEqual(sum(i['net']+i['vat'] for i in self.s['invoices']),605000)
        self.assertEqual(self.s['receipts'],[])

    def test_paid_then_cancelled_preserves_cash_and_blocks_new_overallocation(self):
        d=self.issued();inv=self.s['invoices'][0]
        self.s['receipts'].append(dict(id='paid',accountId='a',date='2026-10-03',amount=715000))
        self.s['allocations'].append(dict(id='alloc',receiptId='paid',invoiceId=inv['id'],date='2026-10-03',amount=715000))
        r=self.command('tax.correct',id=d['id'],modifyCode=4,date='2026-10-04')
        self.external('draft.issue',id=r['recordId'],confirmed=True)
        server.validate_state(self.s)
        self.assertEqual(tax.adjusted_gross(self.s,inv),0)
        self.assertEqual(self.s['receipts'][0]['amount'],715000)
        modified=copy.deepcopy(self.s);modified['allocations'][0]['date']='2026-10-05'
        with self.assertRaises(ValueError):workflow.guard(self.s,modified)
        modified['allocations']=[];workflow.guard(self.s,modified)

    def test_typo_correction_requires_two_separate_documents_in_order(self):
        d=self.issued();r=self.command('tax.correct',id=d['id'],modifyCode=1,net=700000,vat=70000,itemName='정정 분전반')
        cancel,plus=[x for x in self.s['taxDrafts'] if x.get('groupId')==r['groupId']]
        with self.assertRaises(ValueError):self.external('draft.issue',id=plus['id'],confirmed=True)
        self.external('draft.issue',id=cancel['id'],confirmed=True)
        with self.assertRaises(ValueError):self.external('draft.issue',id=plus['id'],confirmed=True)
        self.external('draft.status',id=cancel['id'])
        self.external('draft.issue',id=plus['id'],confirmed=True)
        self.assertEqual(len(self.s['invoices']),3)
        self.assertEqual(sum(i['net']+i['vat'] for i in self.s['invoices']),770000)

    def test_malformed_provider_detail_is_atomic_and_recoverable(self):
        self.collect();r=self.s['taxRecords'][0];before=copy.deepcopy(self.s['taxRecords'])
        op=dict(kind='record.detail',recordId=r['id'],id='bad',mode='simulation');r['operation']=dict(id='bad',started=0)
        self.s,result=tax.complete(self.s,op,dict(record={'ntsconfirmNum':'bad'}),server.validate_state)
        self.assertIn('notice',result);self.assertEqual(self.s['taxRecords'][0]['net'],before[0]['net'])

    def test_collected_sales_can_be_used_as_correction_original_without_reissuing(self):
        self.collect('SELL');record=self.s['taxRecords'][0]
        self.command('tax.book',id=record['id'],accountId='a')
        r=self.command('tax.reference',id=record['id']);d=workflow.get(self.s,'taxDrafts',r['recordId'])
        self.assertTrue(d['referenceOnly']);self.assertEqual(len(self.s['invoices']),1)
        self.assertEqual(self.command('tax.reference',id=record['id'])['recordId'],d['id'])
        corrected=self.command('tax.correct',id=d['id'],modifyCode=2,date='2026-10-03',net=-50000,vat=-5000)
        self.external('draft.issue',id=corrected['recordId'],confirmed=True)
        self.assertEqual(sum(i['net']+i['vat'] for i in self.s['invoices']),275000)

    def test_negative_correction_can_reduce_original_and_positive_adjustment(self):
        d=self.issued()
        plus=self.command('tax.correct',id=d['id'],modifyCode=2,date='2026-10-03',net=100000,vat=10000)
        self.external('draft.issue',id=plus['recordId'],confirmed=True);self.external('draft.status',id=plus['recordId'])
        minus=self.command('tax.correct',id=d['id'],modifyCode=2,date='2026-10-03',net=-700000,vat=-70000)
        self.external('draft.issue',id=minus['recordId'],confirmed=True)
        self.assertEqual(sum(i['net']+i['vat'] for i in self.s['invoices']),55000)
        self.assertEqual(len(self.s['invoices'][-1]['creditApplications']),2)
        self.assertEqual(tax.adjusted_gross(self.s,self.s['invoices'][0]),0)
        self.assertEqual(tax.adjusted_gross(self.s,self.s['invoices'][1]),55000)

    def test_paging_and_error_do_not_book_partial_collection(self):
        result=self.external('collect.start',direction='BUY',start='2026-10-01',end='2026-10-31')
        j=workflow.get(self.s,'taxJobs',result['recordId'])
        op=tax.prepare(self.s,dict(action='collect.poll',data=dict(id=j['id'])),'demo')
        self.s,_=tax.complete(self.s,op,dict(done=True,rows=op['fixtures'][:1],page=1,pageCount=2,total=2),server.validate_state)
        j=workflow.get(self.s,'taxJobs',j['id']);self.assertEqual((j['page'],j['status']),(2,'paging'))
        op=tax.prepare(self.s,dict(action='collect.poll',data=dict(id=j['id'])),'demo')
        self.s,_=tax.complete(self.s,op,dict(error='expired',uncertain=False),server.validate_state)
        self.assertEqual(len(self.s['taxRecords']),1);self.assertEqual(self.s['expenses'],[])
        self.collect();self.assertEqual(len(self.s['taxRecords']),2)

    def test_real_and_simulated_environments_cannot_mix(self):
        self.assertFalse(tax_provider.settings('live')['ready'])
        with patch.dict(os.environ,{'GROWTH_TAX_MODE':'live','POPBILL_LINK_ID':'fake','POPBILL_SECRET_KEY':'fake','POPBILL_CORP_NUM':'8888800001','POPBILL_USER_ID':'fake'},clear=True):
            self.assertFalse(tax_provider.settings('live')['ready'])
            with patch.dict(os.environ,{'GROWTH_TAX_ALLOW_LIVE':'1'}):
                self.assertTrue(tax_provider.settings('live')['ready'])
                self.assertFalse(tax_provider.settings('demo')['ready'])
            self.assertNotIn('fake',json.dumps(tax_provider.settings('demo')))

    def test_provider_process_timeout_returns_unknown_without_secret_stderr(self):
        import subprocess
        with patch('tax_provider.subprocess.run',side_effect=subprocess.TimeoutExpired('worker',75,stderr='secret')):
            result=tax_provider.execute(dict(mode='sandbox',kind='draft.issue'))
        self.assertTrue(result['uncertain']);self.assertNotIn('secret',str(result))

    def test_amounts_and_source_approval_validation(self):
        for v in (True,1.1,float('nan'),'Infinity',10**14):
            with self.assertRaises(ValueError):tax.amount(v)
        self.assertEqual(tax.approval('20260929-10260929-79663401'),'202609291026092979663401')
        self.assertEqual(tax.approval('20260930410000220738b014'),'20260930410000220738B014')
        self.assertEqual(tax.amount('-1,000'),-1000)


if __name__=='__main__':unittest.main()
