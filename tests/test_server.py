import copy, importlib.util, io, json, os, tempfile, unittest
from types import SimpleNamespace
from pathlib import Path
spec=importlib.util.spec_from_file_location('growth_server',Path(__file__).resolve().parents[1]/'server.py')
server=importlib.util.module_from_spec(spec);spec.loader.exec_module(server)

class StorageTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.original=server.DATA;server.DATA=Path(self.temp.name)
 def tearDown(self):server.DATA=self.original;self.temp.cleanup()
 def test_demo_and_live_are_separate(self):
  self.assertEqual(len(server.read_state('live')['state']['invoices']),0)
  self.assertGreater(len(server.read_state('demo')['state']['invoices']),0)
 def test_persistence_and_stale_revision(self):
  s=server.read_state('live');s['state']['settings']['name']='테스트'
  self.assertEqual(server.save_state('live',s['state'],s['revision']),1)
  self.assertEqual(server.read_state('live')['state']['settings']['name'],'테스트')
  with self.assertRaises(RuntimeError):server.save_state('live',s['state'],0)
 def test_demo_validates(self):server.validate_state(server.demo_state())
 def test_cross_account_and_overallocation_rejected(self):
  s=server.demo_state();a=s['allocations'][0];a['amount']=10**10
  with self.assertRaises(ValueError):server.validate_state(s)
  s=server.demo_state();s['receipts'][0]['accountId']='ac-4'
  with self.assertRaises(ValueError):server.validate_state(s)
 def test_invalid_reference_rejected(self):
  s=server.demo_state();s['invoices'][0]['accountId']='missing'
  with self.assertRaises(ValueError):server.validate_state(s)
 def test_individual_withdrawal_cannot_deduct_vat(self):
  s=server.demo_state();s['expenses'][-1]['vatDeductible']=True
  with self.assertRaises(ValueError):server.validate_state(s)
 def request(self,path,body=None,origin='http://127.0.0.1:8787',host='127.0.0.1:8787'):
  handler=object.__new__(server.Handler)
  handler.path=path;handler.server=SimpleNamespace(server_port=8787)
  raw=json.dumps(body).encode() if body is not None else b''
  handler.headers={'Host':host,'Origin':origin,'Content-Type':'application/json','Content-Length':str(len(raw))}
  handler.rfile=io.BytesIO(raw);results=[]
  handler.send=lambda status,data,*args,**kwargs:results.append((status,data))
  if body is None:handler.do_GET()
  else:handler.do_POST()
  return results[0]
 def test_api_state_get_and_save(self):
  status,record=self.request('/api/state?workspace=live');self.assertEqual(status,200)
  record['state']['accounts'].append({'id':'a','name':'검증용 고객','kind':'고객사'})
  status,result=self.request('/api/state?workspace=live',record);self.assertEqual((status,result['revision']),(200,1))
  self.assertEqual(self.request('/api/state?workspace=live')[1]['state']['accounts'][0]['name'],'검증용 고객')
 def test_cross_origin_and_host_requests_are_rejected(self):
  self.assertEqual(self.request('/api/state',{},origin='https://example.com')[0],403)
  self.assertEqual(self.request('/api/state',host='example.com:8787')[0],403)
 def test_ai_upload_requires_explicit_send_and_missing_key_fails_cleanly(self):
  self.assertEqual(self.request('/api/documents/x/analyze',{'allowExternal':False})[0],400)

class RealDocumentTests(unittest.TestCase):
 def test_provided_excel_quotes(self):
  source=os.environ.get('GROWTH_SAMPLE_DIR')
  if not source:self.skipTest('Set GROWTH_SAMPLE_DIR to run private sample checks locally')
  folder=Path(source)
  cases=[('루미나 캠퍼스 견적서 (26.05.14).xlsx',2367500,236750,22,'2026-05-14'),('루미나 캠퍼스 견적서 (26.07.13).xlsx',2160900,216090,26,'2026-07-13')]
  for filename,net,vat,count,day in cases:
   path=folder/filename
   self.assertTrue(path.is_file(),f'Required quotation fixture is missing: {filename}')
   result=server.parse_document(path,filename)
   self.assertEqual((result['net'],result['vat'],len(result['items']),result['date']),(net,vat,count,day))
   self.assertTrue(result['number']);self.assertTrue(result['accountName'])

class SyntheticDocumentTests(unittest.TestCase):
 def test_sections_repeated_items_serial_date_and_empty_second_sheet(self):
  from openpyxl import Workbook
  from openpyxl.utils.datetime import to_excel
  from datetime import datetime
  with tempfile.TemporaryDirectory() as folder:
   book=Workbook();sheet=book.active;sheet.title='Sheet1'
   sheet['G3']='SYNTHETIC-001';sheet['G4']=to_excel(datetime(2026,7,13));sheet['G5']='테스트 고객'
   sheet['C14']='구역 A';sheet['AQ14']=0
   sheet['C15']='테스트 부품';sheet['AE15']=2;sheet['AJ15']=10000;sheet['AQ15']=20000;sheet['AZ15']=2000
   sheet['C38']='구역 B';sheet['C39']='테스트 부품';sheet['AE39']=3;sheet['AJ39']=10000;sheet['AQ39']=30000;sheet['AZ39']=3000
   sheet['C67']='합계';sheet['AQ67']=50000;sheet['AZ67']=5000
   book.create_sheet('Sheet2')['A1']='빈 다음 페이지 양식'
   path=Path(folder)/'synthetic.xlsx';book.save(path)
   result=server.parse_document(path,path.name)
   self.assertEqual(result['date'],'2026-07-13')
   self.assertEqual((result['net'],result['vat'],len(result['items'])),(50000,5000,2))
   self.assertEqual([x['section'] for x in result['items']],['구역 A','구역 B'])

if __name__=='__main__':unittest.main()
