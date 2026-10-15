"""Statement-based sales and reviewed supplier documents; no external calls."""
import copy
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from workflow import day, get, ident, number, text


def now():
    return datetime.now(ZoneInfo('Asia/Seoul')).isoformat(timespec='seconds')


def amount(value):
    if type(value) is not int or not 0 <= value <= 10**13:
        raise ValueError('금액은 0 이상의 원 단위 정수로 입력해 주세요.')
    return value


def sale_for(state, delivery_id):
    return next((i for i in state['invoices'] if i.get('sourceDeliveryId') == delivery_id and i['status'] != 'void'), None)


def hometax_done(invoice):
    # Keep earlier issuance confirmations visible without rewriting old records.
    return invoice.get('hometaxTask', {}).get('done', bool(invoice.get('manualTax')))


def set_hometax_task(invoice, done):
    previous = hometax_done(invoice)
    if previous == done:
        return False
    stamp = now()
    invoice['hometaxTask'] = dict(done=done, updatedAt=stamp, completedAt=stamp if done else '')
    invoice.setdefault('hometaxTaskHistory', []).append(dict(previous=previous, done=done, at=stamp))
    return True


def guard(previous, proposed):
    for kind in ('invoices', 'expenses', 'documents'):
        old = {r['id']: r for r in previous[kind]}
        new = {r['id']: r for r in proposed[kind]}
        for key in old.keys() | new.keys():
            if (old.get(key, {}).get('statementManaged') or new.get(key, {}).get('statementManaged')) and old.get(key) != new.get(key):
                raise ValueError('거래명세서 연결 기록은 해당 상세 화면에서 변경해 주세요.')


def validate(state):
    seen = set()
    confirmations = {}
    for inv in state['invoices']:
        if not inv.get('sourceDeliveryId'):
            continue
        task = inv.get('hometaxTask')
        if task is not None and (not isinstance(task, dict) or type(task.get('done')) is not bool):
            raise ValueError('홈택스 발급 할 일의 완료 여부를 확인해 주세요.')
        d = get(state, 'deliveries', inv['sourceDeliveryId'])
        if not inv.get('statementManaged') or (inv['net'], inv['vat'], inv['date'], inv['accountId'], inv.get('orderId')) != (d['net'], d['vat'], d['date'], d['accountId'], d['orderId']):
            raise ValueError('확정 매출은 원본 거래명세서의 금액·납품일·고객과 일치해야 합니다.')
        if inv['status'] != 'void':
            if d['status'] == 'void' or d['id'] in seen:
                raise ValueError('취소되었거나 이미 매출에 반영한 거래명세서입니다.')
            seen.add(d['id'])
        c = inv.get('manualTax')
        if c:
            if inv['id'] not in c['invoiceIds']:
                raise ValueError('수동 발급 확인 연결을 확인해 주세요.')
            key = c.get('approval') or c['id']
            if key in confirmations and confirmations[key] != c:
                raise ValueError('동일 승인번호는 한 번에 함께 발급 확인해 주세요.')
            confirmations[key] = c
    for c in confirmations.values():
        rows = [get(state, 'invoices', key) for key in c['invoiceIds']]
        if len({i['accountId'] for i in rows}) != 1 or any(i.get('manualTax') != c for i in rows):
            raise ValueError('같은 세금계산서로 묶인 매출 기록을 확인해 주세요.')
        if (sum(i['net'] for i in rows), sum(i['vat'] for i in rows)) != (c['net'], c['vat']):
            raise ValueError('발급 확인 금액과 거래명세서 합계가 다릅니다.')
    for doc in state['documents']:
        if doc.get('statementManaged'):
            e = get(state, 'expenses', doc.get('expenseId'))
            if doc.get('accountId') != e.get('accountId') or e.get('category') == '개인 인출':
                raise ValueError('매입 문서와 비용의 거래처 연결을 확인해 주세요.')


def attach(state, doc, expense, kind, reviewed):
    if doc.get('generated') or doc.get('quoteId') or doc.get('deliveryId'):
        raise ValueError('고객에게 작성한 문서는 매입 증빙으로 등록할 수 없습니다.')
    if doc.get('expenseId') and doc['expenseId'] != expense['id']:
        raise ValueError('이미 다른 비용에 연결된 문서입니다.')
    doc.update(statementManaged=True, expenseId=expense['id'], accountId=expense['accountId'],
               orderId=expense.get('orderId', ''), kind=kind, status='등록 완료', reviewedAt=now(),
               reviewedAmounts=reviewed, source=doc.get('source', 'upload'))


def apply(state, action, data):
    if action == 'statement.confirm':
        d = get(state, 'deliveries', data.get('id'))
        existing = sale_for(state, d['id'])
        if existing:
            return dict(recordId=existing['id'], duplicate=True)
        if data.get('confirmed') is not True:
            raise ValueError('납품 내용과 금액을 확인한 뒤 확정해 주세요.')
        if d['status'] == 'void' or not d.get('pdfDocumentId'):
            raise ValueError('유효한 거래명세서 PDF를 먼저 생성해 주세요.')
        if any(t['status'] != 'discarded' and (t.get('deliveryId') == d['id'] or (t.get('orderId') == d['orderId'] and not t.get('deliveryId'))) for t in state['taxDrafts']):
            raise ValueError('기존 전자 발급 기록에 연결된 주문입니다. 중복 매출 여부를 먼저 확인해 주세요.')
        linked = data.get('existingInvoiceId')
        candidates = [i for i in state['invoices'] if i['status'] != 'void' and not i.get('sourceDeliveryId') and i['accountId'] == d['accountId'] and (i.get('orderId') == d['orderId'] or (i['date'], i['net'], i['vat']) == (d['date'], d['net'], d['vat']))]
        if linked:
            inv = get(state, 'invoices', linked)
            if inv.get('taxRecordId') or inv.get('sourceDeliveryId') or inv['status'] == 'void' or (inv['accountId'], inv['date'], inv['net'], inv['vat']) != (d['accountId'], d['date'], d['net'], d['vat']):
                raise ValueError('기존 매출의 고객·매출일·공급가액·부가세가 거래명세서와 같아야 합니다.')
        else:
            if candidates and not (data.get('separateConfirmed') is True and text(data.get('separateReason', ''), 200)):
                raise ValueError('같은 주문 또는 금액의 기존 매출이 있습니다. 기존 기록을 연결하거나 별개 거래 사유를 입력해 주세요.')
            inv = dict(id=ident('sale'), number=number(state, 'invoices', 'S', d['date']), date=d['date'],
                       accountId=d['accountId'], orderId=d['orderId'], net=d['net'], vat=d['vat'], status='issued')
            state['invoices'].append(inv)
        inv.update(statementManaged=True, sourceDeliveryId=d['id'], orderId=d['orderId'],
                   basis='delivery', confirmedAt=now(), dueDate=day(data.get('dueDate'), True),
                   separateReason=text(data.get('separateReason', ''), 200))
        if inv['dueDate'] and inv['dueDate'] < inv['date']:
            raise ValueError('지급기한은 매출일 이후로 입력해 주세요.')
        if data.get('useCredit') is True:
            remaining = inv['net'] + inv['vat'] - sum(a['amount'] for a in state['allocations'] if a['invoiceId'] == inv['id'])
            for receipt in sorted(state['receipts'], key=lambda r: r['date']):
                if receipt.get('orderId') != d['orderId'] or receipt['accountId'] != d['accountId'] or receipt['date'] > now()[:10]:
                    continue
                available = receipt['amount'] - sum(a['amount'] for a in state['allocations'] if a['receiptId'] == receipt['id'])
                value = min(remaining, available)
                if value > 0:
                    state['allocations'].append(dict(id=ident('allocation'), receiptId=receipt['id'], invoiceId=inv['id'],
                                                     date=max(receipt['date'], inv['date']), amount=value))
                    remaining -= value
        return dict(recordId=inv['id'])
    if action == 'statement.hometax-task':
        inv = get(state, 'invoices', data.get('id'))
        if not inv.get('sourceDeliveryId') or inv['status'] == 'void':
            raise ValueError('매출 확정한 거래명세서의 할 일만 변경할 수 있습니다.')
        if type(data.get('done')) is not bool:
            raise ValueError('할 일의 완료 여부를 선택해 주세요.')
        changed = set_hometax_task(inv, data['done'])
        return dict(recordId=inv['id'], duplicate=not changed)
    if action == 'statement.tax-confirm':
        keys = data.get('invoiceIds')
        if not isinstance(keys, list) or not keys or len(keys) > 150 or len(set(keys)) != len(keys):
            raise ValueError('발급 확인할 매출을 선택해 주세요.')
        rows = [get(state, 'invoices', key) for key in keys]
        if data.get('confirmed') is not True or any(not i.get('sourceDeliveryId') or i['status'] == 'void' for i in rows):
            raise ValueError('홈택스에서 수동 발급한 내용을 확인해 주세요.')
        if len({i['accountId'] for i in rows}) != 1:
            raise ValueError('같은 고객사의 거래명세서만 함께 발급 확인할 수 있습니다.')
        if any(i.get('manualTax') for i in rows):
            if all(i.get('manualTax') == rows[0].get('manualTax') for i in rows) and set(keys) == set(rows[0].get('manualTax', {}).get('invoiceIds', [])):
                return dict(recordId=rows[0]['id'], duplicate=True)
            raise ValueError('이미 발급 확인한 매출이 포함되어 있습니다.')
        approval = re.sub(r'[-\s]', '', text(data.get('approval', ''), 80)).upper()
        if approval and not re.fullmatch(r'[A-Z0-9]{24}', approval):
            raise ValueError('승인번호는 하이픈을 제외한 24자리로 입력하거나 비워두세요.')
        if approval and any((i.get('manualTax', {}).get('approval') == approval or re.sub(r'[-\s]', '', i.get('number', '')).upper() == approval) for i in state['invoices']):
            raise ValueError('이미 기록한 승인번호입니다. 기존 발급 내역을 확인해 주세요.')
        c = dict(id=ident('manual-tax'), invoiceIds=keys, date=day(data.get('date')), approval=approval,
                 net=sum(i['net'] for i in rows), vat=sum(i['vat'] for i in rows), confirmedAt=now(), source='user')
        for i in rows:
            i['manualTax'] = copy.deepcopy(c)
            set_hometax_task(i, True)
        return dict(recordId=rows[0]['id'])
    if action == 'statement.tax-reset':
        inv = get(state, 'invoices', data.get('id'))
        c = inv.get('manualTax')
        reason = text(data.get('reason', ''), 200)
        if not c or not reason:
            raise ValueError('잘못 표시한 발급 확인과 정정 사유를 확인해 주세요.')
        for key in c['invoiceIds']:
            row = get(state, 'invoices', key)
            row.setdefault('taxHistory', []).append(dict(confirmation=copy.deepcopy(c), reason=reason, resetAt=now()))
            set_hometax_task(row, False)
            row.pop('manualTax', None)
        return dict(recordId=inv['id'])
    if action == 'statement.cancel':
        inv = get(state, 'invoices', data.get('id'))
        reason = text(data.get('reason', ''), 200)
        if not inv.get('sourceDeliveryId') or not reason:
            raise ValueError('정정할 매출과 사유를 확인해 주세요.')
        if any(a['invoiceId'] == inv['id'] for a in state['allocations']):
            raise ValueError('입금 내역에서 연결을 먼저 해제해 주세요. 실제 입금 기록은 유지됩니다.')
        inv.update(status='void', voidReason=reason, voidedAt=now())
        return dict(recordId=inv['id'])
    if action == 'statement.purchase':
        doc = get(state, 'documents', data.get('documentId'))
        if doc.get('expenseId'):
            return dict(recordId=doc['expenseId'], duplicate=True)
        if data.get('confirmed') is not True:
            raise ValueError('원본의 거래처·금액을 확인한 뒤 등록해 주세요.')
        account = get(state, 'accounts', data.get('accountId'))
        net, vat = amount(data.get('net')), amount(data.get('vat'))
        if net + vat <= 0:
            raise ValueError('원본을 확인하고 매입 금액을 입력해 주세요.')
        kind = data.get('kind')
        if kind not in ('거래명세서', '세금계산서', '기타'):
            raise ValueError('매입 증빙 종류를 선택해 주세요.')
        dt = day(data.get('date'))
        target = data.get('existingExpenseId')
        if target:
            expense = get(state, 'expenses', target)
            if expense.get('category') == '개인 인출' or (expense.get('accountId'), expense['net'], expense['vat']) != (account['id'], net, vat):
                raise ValueError('기존 비용과 매입처·공급가액·부가세가 일치해야 합니다. 다르면 새 비용을 만들기 전에 원본을 대조해 주세요.')
        else:
            # Equal amounts are candidates, never proof that two documents are one deal.
            candidates = [e for e in state['expenses'] if e.get('category') != '개인 인출' and (e.get('accountId'), e['net'], e['vat']) == (account['id'], net, vat)]
            if candidates and not (data.get('separateConfirmed') is True and text(data.get('separateReason', ''), 200)):
                raise ValueError('같은 매입처·금액의 비용이 있습니다. 기존 비용에 연결하거나 별개 거래 사유를 입력해 주세요.')
            category = data.get('category')
            if category not in ('자재비', '외주비', '월세', '차량비', '통신비', '배송비', '기타 사업 비용'):
                raise ValueError('사업 비용 항목을 선택해 주세요.')
            order_id = data.get('orderId', '')
            if order_id:
                get(state, 'orders', order_id)
            expense = dict(id=ident('expense'), statementManaged=True, accountId=account['id'],
                           date=dt, paidDate=day(data.get('paidDate'), True), net=net, vat=vat,
                           vatDeductible=data.get('vatDeductible') is True, category=category,
                           description=text(data.get('description', doc['name']), 500), orderId=order_id,
                           documentId=doc['id'], createdAt=now(), separateReason=text(data.get('separateReason', ''), 200))
            state['expenses'].append(expense)
        if not expense.get('taxRecordId'):
            expense['statementManaged'] = True
        attach(state, doc, expense, kind, dict(net=net, vat=vat, date=dt))
        return dict(recordId=expense['id'])
    if action == 'statement.expense':
        e = get(state, 'expenses', data.get('id'))
        if not e.get('statementManaged') or e.get('taxRecordId'):
            raise ValueError('문서에서 등록한 매입 비용을 선택해 주세요.')
        if 'net' in data:
            values = dict(net=amount(data['net']), vat=amount(data['vat']), date=day(data['date']),
                          description=text(data['description'], 500))
            if values['net'] + values['vat'] <= 0:
                raise ValueError('매입 합계는 0보다 커야 합니다.')
            if any(values[k] != e.get(k) for k in values):
                reason = text(data.get('reason', ''), 200)
                if not reason:
                    raise ValueError('원본 문서 대조 후 금액·발생일·내용 정정 사유를 입력해 주세요.')
                e.setdefault('history', []).append(dict(previous={k:e.get(k) for k in values}, reason=reason, changedAt=now()))
                e.update(values)
        e['paidDate'] = day(data.get('paidDate'), True)
        e['vatDeductible'] = data.get('vatDeductible') is True
        e['orderId'] = text(data.get('orderId', ''), 160)
        if e['orderId']:
            get(state, 'orders', e['orderId'])
        if e['orderId'] and any(c['expenseId'] == e['id'] for c in state['costAllocations']):
            raise ValueError('여러 주문에 배분한 비용입니다. 원가 배분에서 수정해 주세요.')
        return dict(recordId=e['id'])
    if action == 'statement.cost':
        e = get(state, 'expenses', data.get('id'))
        if not e.get('statementManaged') or e.get('taxRecordId'):
            raise ValueError('문서에서 등록한 매입을 선택해 주세요.')
        order = get(state, 'orders', data.get('orderId'))
        value = amount(data.get('amount'))
        others = [c for c in state['costAllocations'] if c['expenseId'] == e['id'] and c['orderId'] != order['id']]
        if sum(c['amount'] for c in others) + value > e['net'] + (0 if e.get('vatDeductible') else e['vat']):
            raise ValueError('매입 비용을 초과하여 주문 원가에 배분할 수 없습니다.')
        state['costAllocations'] = [c for c in state['costAllocations'] if not(c['expenseId'] == e['id'] and c['orderId'] == order['id'])]
        if value:
            state['costAllocations'].append(dict(id=ident('cost'), expenseId=e['id'], orderId=order['id'], amount=value, lineId='total', taxRecordId=''))
        e['orderId'] = ''
        return dict(recordId=e['id'])
    raise ValueError('지원하지 않는 거래명세서 업무입니다.')
