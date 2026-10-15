import copy
import unittest

import ledger
import server
import workflow


class LegacyVoucherDeleteTests(unittest.TestCase):
    def setUp(self):
        self.state = workflow.normalize(server.demo_state())
        ledger.migrate(self.state)

    def delete(self, voucher):
        result, _ = workflow.apply(
            self.state,
            {'action': 'erp.voucher.delete', 'data': {'id': voucher['id']}},
            'demo',
        )
        server.validate_state(self.state)
        return result

    def voucher(self, *, kind=None, legacy_id=None, order_id=None):
        return next(
            voucher for voucher in self.state['vouchers']
            if voucher.get('legacy')
            and (kind is None or voucher['kind'] == kind)
            and (legacy_id is None or voucher.get('legacyId') == legacy_id)
            and (order_id is None or voucher.get('orderId') == order_id)
        )

    def test_legacy_purchase_and_personal_withdrawal_delete_their_source_and_payment(self):
        for legacy_id in ('exp-5-0', 'personal-5'):
            voucher = self.voucher(legacy_id=legacy_id)
            payment_ids = {
                payment['id'] for payment in self.state['payments']
                if any(row['voucherId'] == voucher['id'] for row in payment.get('allocations', []))
            }
            result = self.delete(voucher)
            self.assertEqual(set(result['deletedPaymentIds']), payment_ids)
            self.assertFalse(any(row['id'] == voucher['id'] for row in self.state['vouchers']))
            self.assertFalse(any(row['id'] == legacy_id for row in self.state['expenses']))
            self.assertFalse(any(row['id'] in payment_ids for row in self.state['payments']))
            self.assertFalse(any(entry['sourceId'] in payment_ids | {voucher['id']} for entry in self.state['journalEntries']))

    def test_legacy_sale_deletes_source_invoice_and_dedicated_receipts_but_keeps_order(self):
        voucher = self.voucher(kind='sale', order_id='order-5-0')
        invoice_id = voucher['legacyId']
        receipt_ids = {
            row['receiptId'] for row in self.state['allocations']
            if row['invoiceId'] == invoice_id
        }
        order_id = voucher['orderId']
        result = self.delete(voucher)
        self.assertFalse(any(row['id'] == invoice_id for row in self.state['invoices']))
        self.assertFalse(any(row['invoiceId'] == invoice_id for row in self.state['allocations']))
        self.assertFalse(any(row['id'] in receipt_ids for row in self.state['receipts']))
        self.assertFalse(any(row['id'] == voucher['id'] for row in self.state['vouchers']))
        self.assertTrue(any(row['id'] == order_id for row in self.state['orders']))
        self.assertEqual(len(result['deletedPaymentIds']), len(receipt_ids))

    def test_shared_legacy_payment_keeps_other_allocation_and_unlinks_deleted_voucher(self):
        voucher = self.voucher(kind='sale', order_id='order-5-0')
        other = next(v for v in self.state['vouchers'] if v.get('legacy') and v['kind']=='sale' and v['id']!=voucher['id'] and v['accountId']==voucher['accountId'])
        payment = next(
            payment for payment in self.state['payments']
            if any(row['voucherId'] == voucher['id'] for row in payment.get('allocations', []))
        )
        payment['amount'] += 1
        payment['allocations'].append(
            {'voucherId': other['id'], 'amount': 1, 'date': payment['date']}
        )
        self.delete(voucher)
        kept=next(row for row in self.state['payments'] if row['id']==payment['id'])
        self.assertEqual(kept['allocations'],[{'voucherId':other['id'],'amount':1,'date':payment['date']}])


if __name__ == '__main__':
    unittest.main()
