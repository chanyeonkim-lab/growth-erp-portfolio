"""SDK contract tests: mocks never contact Popbill or send an invoice/email."""
import os
import unittest
from types import SimpleNamespace as Object
from unittest.mock import Mock, patch
import popbill
import tax_provider


class ProviderContractTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,{'POPBILL_LINK_ID':'test-link','POPBILL_SECRET_KEY':'test-secret','POPBILL_CORP_NUM':'8888800001','POPBILL_USER_ID':'test-user'})
        self.env.start();self.addCleanup(self.env.stop)
        self.socket=patch('socket.setdefaulttimeout');self.socket.start();self.addCleanup(self.socket.stop)
        self.service=Mock()
        self.ht=patch('popbill.HTTaxinvoiceService',return_value=self.service);self.ht.start();self.addCleanup(self.ht.stop)
        self.invoice=patch('popbill.TaxinvoiceService',return_value=self.service);self.invoice.start();self.addCleanup(self.invoice.stop)

    def test_job_success_requires_completed_and_positive_error_code(self):
        op=dict(kind='collect.poll',mode='sandbox',jobId='job',page=2)
        self.service.getJobState.return_value=Object(jobState=3,errorCode=-1)
        result=tax_provider.real(op)
        self.assertIn('error',result);self.service.search.assert_not_called()
        self.service.getJobState.return_value=Object(jobState=2,errorCode=0)
        self.assertFalse(tax_provider.real(op)['done']);self.service.search.assert_not_called()
        self.service.getJobState.return_value=Object(jobState=3,errorCode=1)
        self.service.search.return_value=Object(code=1,pageNum=2,pageCount=3,total=101,list=[Object(ntsconfirmNum='example')])
        result=tax_provider.real(op)
        self.assertEqual(result['pageCount'],3);self.assertEqual(result['rows'],[dict(ntsconfirmNum='example')])
        self.service.search.assert_called_once_with('8888800001','job',[],[],[],'','','',2,50,'A','test-user')
        self.assertTrue(self.service.IsTest)

    def test_request_uses_write_date_and_explicit_sales_purchase_direction(self):
        self.service.requestJob.return_value='job-id'
        result=tax_provider.real(dict(kind='collect.start',mode='sandbox',direction='BUY',start='2026-10-01',end='2026-10-03'))
        self.assertEqual(result['jobId'],'job-id')
        self.service.requestJob.assert_called_once_with('8888800001','BUY','W','20261001','20261003','test-user')

    def test_existing_remote_key_is_queried_and_never_issued_again(self):
        self.service.checkMgtKeyInUse.return_value=True
        self.service.getInfo.return_value=Object(stateCode=304,ntsconfirmNum='approval')
        result=tax_provider.real(dict(kind='draft.issue',mode='sandbox',key='stable-key'))
        self.service.registIssue.assert_not_called();self.assertEqual(result['info']['stateCode'],304)

    def test_new_issue_uses_sdk_document_and_preserves_stable_key(self):
        self.service.checkMgtKeyInUse.return_value=False
        self.service.getInfo.return_value=Object(stateCode=300)
        tax_provider.real(dict(kind='draft.issue',mode='sandbox',key='stable-key',payload=dict(invoicerMgtKey='stable-key',detailList=[dict(serialNum=1,itemName='test',supplyCost='100',tax='10')])))
        args=self.service.registIssue.call_args.args
        self.assertIsInstance(args[1],popbill.Taxinvoice)
        self.assertEqual(args[1].invoicerMgtKey,'stable-key');self.assertIsInstance(args[1].detailList[0],popbill.TaxinvoiceDetail)
        self.assertEqual(args[2:4],(False,False));self.assertEqual(args[-1],'test-user')

    def test_missing_status_never_triggers_a_write(self):
        self.service.checkMgtKeyInUse.return_value=False
        result=tax_provider.real(dict(kind='draft.status',mode='sandbox',key='stable-key'))
        self.assertTrue(result['notFound']);self.service.registIssue.assert_not_called()

    def test_provider_error_message_cannot_leak_credentials(self):
        self.service.checkMgtKeyInUse.side_effect=popbill.PopbillException(-11000005,'secret-credential-text')
        result=tax_provider.real(dict(kind='draft.issue',mode='sandbox',key='stable-key'))
        self.assertTrue(result['uncertain']);self.assertNotIn('secret-credential-text',str(result))


if __name__=='__main__':unittest.main()
