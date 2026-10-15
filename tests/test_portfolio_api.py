"""Public demo isolation, accounting commands, export, and request boundaries."""
import base64
import copy
import io
import unittest
from unittest.mock import patch
from openpyxl import load_workbook
from pypdf import PdfReader
from demo_cloud import app
from demo_seed import demo_state


class PortfolioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.seed=demo_state('2026-10-16')
    def setUp(self):self.client=app.test_client();self.state=copy.deepcopy(self.seed)
    def command(self,action,**data):
        return self.client.post('/api/demo/command',json=dict(state=self.state,action=action,data=data))
    def test_seed_is_date_relative_and_contains_connected_records(self):
        for as_of in ('2026-10-01','2026-10-16','2027-01-01'):
            s=demo_state(as_of)
            self.assertEqual(s['demoMeta']['asOf'],as_of)
            self.assertGreaterEqual(len(s['quotes']),40)
            self.assertTrue(all(p['allocations'] for p in s['payments']))
            self.assertTrue(all(sum(r['debit'] for r in j['rows'])==sum(r['credit'] for r in j['rows']) for j in s['journalEntries']))
            self.assertTrue(any(o['status']=='일부 납품' for o in s['orders']))
    def test_visitors_are_isolated_and_requests_do_not_persist(self):
        original=copy.deepcopy(self.state)
        r=self.command('erp.account.save',name='새 가상 고객',kind='고객사')
        self.assertEqual(r.status_code,200)
        self.assertEqual(len(r.json['state']['accounts']),len(original['accounts'])+1)
        self.assertEqual(demo_state('2026-10-16'),original)
        self.assertIsNone(r.headers.get('Set-Cookie'))
    def test_payment_reduces_the_actual_ledger_balance(self):
        v=next(v for v in reversed(self.state['vouchers']) if v['kind']=='sale' and v['status']=='confirmed')
        r=self.command('erp.payment.save',direction='in',accountId=v['accountId'],date='2026-10-16',amount=v['amount'],voucherId=v['id'])
        self.assertEqual(r.status_code,200,r.json)
        p=r.json['state']['payments'][-1]
        self.assertEqual(p['allocations'][0]['voucherId'],v['id'])
        self.assertEqual(p['allocations'][0]['amount'],v['amount'])
    def test_failed_command_is_atomic(self):
        before=copy.deepcopy(self.state)
        r=self.command('erp.payment.save',direction='in',accountId='missing',date='2026-10-16',amount=100)
        self.assertEqual(r.status_code,400)
        self.assertEqual(self.state,before)
    def test_real_integrations_and_production_routes_are_unavailable(self):
        for action in ('tax.issue','tax.collect','statement.confirm','../../cloud.py'):
            self.assertEqual(self.command(action).status_code,400)
        for path in ('/api/state','/api/auth/login','/api/tax','/.env','/cloud.py','/data/growth.sqlite3','/../company_profile.py'):
            self.assertEqual(self.client.get(path).status_code,404,path)
        self.assertEqual(self.client.get('/api/health').json['externalServices'],False)
    def test_exports_contain_synthetic_identity_without_internal_costs_or_seal(self):
        q=self.state['quotes'][-1]
        for fmt in ('pdf','xlsx'):
            r=self.command('erp.document',kind='quote',id=q['id'],format=fmt)
            self.assertEqual(r.status_code,200,r.json)
            content=base64.b64decode(r.json['file']['content'])
            if fmt=='pdf':
                reader=PdfReader(io.BytesIO(content));text=' '.join(p.extract_text() for p in reader.pages)
                self.assertFalse(reader.pages[0].images)
            else:
                ws=load_workbook(io.BytesIO(content)).active
                text=' '.join(str(c.value) for row in ws for c in row if c.value is not None)
                self.assertFalse(ws._images)
            self.assertIn('그로스 일렉트릭',text)
            self.assertIn('000-00-00000',text)
            self.assertNotIn('estimatedCost',text)
            self.assertNotIn('내부 원가',text)
    def test_guide_never_calls_external_models(self):
        with patch('ai_guide.call_claude',side_effect=AssertionError('External AI called')):
            r=self.client.post('/api/guide/ask',json=dict(question='미수금은 어디에서 확인해?',page='payments'))
        self.assertEqual(r.status_code,200)
        self.assertTrue(r.json['fallback'])
        self.assertTrue(r.json['sources'])
        self.assertIn('생성형 AI 호출 없이',r.json['answer'])
    def test_seed_ships_downloadable_synthetic_documents(self):
        r=self.client.get('/api/demo/seed')
        self.assertEqual(r.status_code,200)
        self.assertEqual(len(r.json['files']),3)
        for d in r.json['state']['documents']:self.assertIn(d['id'],r.json['files'])
    def test_security_headers_and_body_limit(self):
        r=self.client.get('/')
        self.assertIn("frame-ancestors 'none'",r.headers['Content-Security-Policy'])
        self.assertEqual(r.headers['X-Content-Type-Options'],'nosniff')
        r=self.client.post('/api/demo/command',data=b'x'*(3*1024*1024+1),content_type='application/json')
        self.assertEqual(r.status_code,413)


if __name__=='__main__':unittest.main()
