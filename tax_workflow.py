"""Reviewed tax inbox, immutable issuance outbox and purchase cost allocations.

External writes use prepare/commit -> network -> finish/commit. A timed-out issue
is never resubmitted automatically, including after a process or database crash.
"""
import copy
import hashlib
import json
import re
import time
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

import tax_provider
from workflow import day, get, ident, text

KINDS = ('taxRecords', 'taxJobs', 'taxDrafts', 'costAllocations')
REASONS = {1:'기재사항 착오정정', 2:'공급가액 변동', 3:'환입', 4:'계약의 해제', 6:'착오에 의한 이중발급'}
STATES = {100:'임시 저장', 300:'발급 완료', 301:'전송 대기', 302:'전송 접수', 303:'전송 중', 304:'전송 성공', 305:'전송 실패', 600:'발급 취소'}


def now():
    return datetime.now(ZoneInfo('Asia/Seoul')).isoformat(timespec='seconds')


def digits(value):
    return re.sub(r'\D', '', str(value or ''))


def approval(value, required=True):
    value = re.sub(r'[-\s]', '', str(value or '')).upper()
    if not value and not required:
        return ''
    if not re.fullmatch(r'[A-Z0-9]{24}', value):
        raise ValueError('국세청 승인번호 24자리를 확인해 주세요.')
    return value


def amount(value):
    try:
        if isinstance(value, bool):
            raise ValueError()
        number = Decimal(str(value).replace(',', ''))
        if not number.is_finite() or number != int(number) or abs(number) > 10**13:
            raise ValueError()
        return int(number)
    except (ValueError, TypeError, InvalidOperation):
        raise ValueError('세금계산서 금액은 원 단위 정수여야 합니다.')


def source_day(value):
    value = str(value or '')
    if re.fullmatch(r'\d{8}', value):
        value = value[:4] + '-' + value[4:6] + '-' + value[6:]
    return day(value)


def source_record(raw, direction, mode):
    """Only retain bounded, known business fields from an untrusted API response."""
    if raw.get('invoiceType') in ('매입','매출') and raw['invoiceType'] != ('매입' if direction=='BUY' else '매출'):
        raise ValueError('수집 자료의 매출·매입 구분이 요청과 다릅니다.')
    result = dict(approval=approval(raw.get('ntsconfirmNum')), direction=direction, mode=mode,
                  providerApproval=re.sub(r'[-\s]','',str(raw.get('ntsconfirmNum'))),
                  date=source_day(raw.get('writeDate')), net=amount(raw.get('supplyCostTotal')),
                  vat=amount(raw.get('taxTotal')), originalApproval=approval(raw.get('orgNTSConfirmNum'), False),
                  modifyCode=int(raw.get('modifyCode') or 0), taxType=text(raw.get('taxType') or '과세', 10),
                  supplierNumber=digits(raw.get('invoicerCorpNum')), supplierName=text(raw.get('invoicerCorpName') or '', 160),
                  customerNumber=digits(raw.get('invoiceeCorpNum')), customerName=text(raw.get('invoiceeCorpName') or '', 160),
                  description=text(raw.get('itemName') or '', 160), items=[])
    if amount(raw.get('totalAmount')) != result['net'] + result['vat']:
        raise ValueError('수집 자료의 공급가액·세액·합계가 일치하지 않습니다.')
    if result['net'] * result['vat'] < 0 or (result['net'] + result['vat'] < 0 and not result['originalApproval']):
        raise ValueError('수정세금계산서의 원본 승인번호와 금액을 확인해 주세요.')
    rows = raw.get('detailList')
    result['detailLoaded'] = isinstance(rows, list)
    if rows is not None:
        if not isinstance(rows, list) or len(rows) > 99:
            raise ValueError('수집 품목을 확인해 주세요.')
        for n, r in enumerate(rows):
            result['items'].append(dict(id=str(n+1), name=text(r.get('itemName') or '', 160), spec=text(r.get('spec') or '', 160),
                                        quantity=text(str(r.get('qty') or ''), 24), unitPrice=text(str(r.get('unitCost') or ''), 24),
                                        net=amount(r.get('supplyCost') or 0), vat=amount(r.get('tax') or 0)))
        if sum(r['net'] for r in result['items']) != result['net'] or sum(r['vat'] for r in result['items']) != result['vat']:
            result['detailWarning'] = '품목 합계와 문서 총액이 다릅니다. 전체 금액 기준 원가 연결만 가능합니다.'
    if mode != 'simulation' and result['approval'].startswith('SIM'):
        raise ValueError('모의 승인번호를 실제 자료로 사용할 수 없습니다.')
    return result


def fingerprint(record):
    keys = ('approval','direction','mode','date','net','vat','originalApproval','supplierNumber','customerNumber','taxType')
    return hashlib.sha256(json.dumps({k:record[k] for k in keys}, sort_keys=True).encode()).hexdigest()


def upsert(state, source):
    found = next((r for r in state['taxRecords'] if (r['mode'], r['direction'], r['approval']) ==
                  (source['mode'], source['direction'], source['approval'])), None)
    if found:
        if fingerprint(found) != fingerprint(source):
            found['conflict'] = True
            found['conflictNote'] = '동일 승인번호의 금액·거래처·작성일이 달라졌습니다. 원본과 연동 업체에서 확인해 주세요.'
            return found, 'conflict'
        if source['detailLoaded']:
            if found.get('detailLoaded') and source['items'] != found['items'] and any(c['taxRecordId']==found['id'] for c in state['costAllocations']):
                found['detailWarning'] = '기존 원가 연결이 있어 변경된 품목을 덮어쓰지 않았습니다. 연결을 비운 후 상세를 다시 조회해 주세요.'
                return found, 'conflict'
            found.pop('detailWarning',None)
            for key in ('items', 'detailLoaded', 'detailWarning', 'modifyCode'):
                if key in source:
                    found[key] = source[key]
        found['lastSeenAt'] = now()
        return found, 'duplicate'
    source.update(id=ident('tax'), collectedAt=now(), lastSeenAt=now(), ledgerId='', conflict=False)
    state['taxRecords'].append(source)
    return source, 'new'


def adjusted_gross(state, invoice, cutoff='9999-12-31'):
    credit = 0
    for row in state['invoices']:
        if row.get('status') == 'void' or row['date'] > cutoff:
            continue
        if row.get('creditApplications'):
            credit += sum(c['amount'] for c in row['creditApplications'] if c['invoiceId'] == invoice['id'])
        elif row.get('creditForId') == invoice['id']:
            credit -= row['net'] + row['vat']
    return invoice['net'] + invoice['vat'] - credit


def guard(previous, proposed):
    for kind in KINDS:
        if previous.get(kind, []) != proposed.get(kind, []):
            raise ValueError('세금계산서와 원가 연결은 전용 화면에서 변경해 주세요.')
    for kind in ('invoices', 'expenses'):
        old = {r['id']:r for r in previous[kind]}
        new = {r['id']:r for r in proposed[kind]}
        for key, r in new.items():
            if (r.get('taxRecordId') or r.get('creditForId') or r.get('creditApplications')) and r != old.get(key):
                raise ValueError('수집·발급한 세금계산서의 원본 기록은 변경할 수 없습니다.')
        if any(new.get(key) != r for key, r in old.items() if r.get('taxRecordId')):
            raise ValueError('수집·발급한 원본 기록은 삭제할 수 없습니다.')
    old_allocations = {r['id']:r for r in previous['allocations']}
    for a in proposed['allocations']:
        if a != old_allocations.get(a['id']):
            inv = get(proposed, 'invoices', a['invoiceId'])
            total = sum(r['amount'] for r in proposed['allocations'] if r['invoiceId'] == inv['id'])
            if total > adjusted_gross(proposed, inv):
                raise ValueError('수정세금계산서를 반영한 청구금액을 초과합니다. 기존 초과 입금은 먼저 연결을 해제해 주세요.')


def validate(state):
    seen = set()
    for record in state['taxRecords']:
        key = (record['mode'], record['direction'], record['approval'])
        if key in seen:
            raise ValueError('승인번호가 중복되었습니다.')
        seen.add(key)
        if record.get('ledgerId'):
            kind = 'invoices' if record['direction'] == 'SELL' else 'expenses'
            ledger = get(state, kind, record['ledgerId'])
            if (ledger.get('taxRecordId'), ledger['net'], ledger['vat'], ledger['date']) != (record['id'], record['net'], record['vat'], record['date']):
                raise ValueError('수집 원본과 장부의 금액·작성일이 일치해야 합니다.')
    for kind in ('invoices','expenses'):
        for row in state[kind]:
            if row.get('taxRecordId'):
                rec = get(state, 'taxRecords', row['taxRecordId'])
                if rec.get('ledgerId') != row['id'] or rec['direction'] != ('SELL' if kind == 'invoices' else 'BUY'):
                    raise ValueError('수집 자료 연결이 올바르지 않습니다.')
            if row['net'] < 0 or row['vat'] < 0:
                if not row.get('taxRecordId'):
                    raise ValueError('음수 금액은 확인된 수정세금계산서로만 등록할 수 있습니다.')
            if row.get('creditForId'):
                original = get(state, 'invoices', row['creditForId'])
                if row['accountId'] != original['accountId'] or row['net'] + row['vat'] >= 0 or original['net'] + original['vat'] <= 0:
                    raise ValueError('수정세금계산서와 원본 매출 연결을 확인해 주세요.')
                if adjusted_gross(state, original) < 0:
                    raise ValueError('원본 금액을 초과하는 음수 수정입니다. 원본 연결을 확인해 주세요.')
            if row.get('creditApplications'):
                applications = row['creditApplications']
                if not row.get('taxRecordId') or sum(c['amount'] for c in applications) != -(row['net']+row['vat']):
                    raise ValueError('수정 금액과 청구 차감액이 다릅니다.')
                if len({c['invoiceId'] for c in applications}) != len(applications):
                    raise ValueError('청구 차감 연결이 중복되었습니다.')
                for c in applications:
                    target = get(state,'invoices',c['invoiceId'])
                    if c['amount'] <= 0 or target['net']+target['vat'] <= 0 or target['accountId'] != row['accountId'] or adjusted_gross(state,target)<0:
                        raise ValueError('수정 후 청구금액을 확인해 주세요.')
    for c in state['costAllocations']:
        expense = get(state, 'expenses', c['expenseId'])
        get(state, 'orders', c['orderId'])
        if not expense.get('taxRecordId'):
            if not expense.get('statementManaged') or type(c.get('amount')) is not int or c['amount'] <= 0:
                raise ValueError('확인한 매입 비용과 양수 원가 배분 금액이 필요합니다.')
            entries = [r for r in state['costAllocations'] if r['expenseId'] == expense['id']]
            if len({r['orderId'] for r in entries}) != len(entries) or sum(r['amount'] for r in entries) > expense['net'] + (0 if expense.get('vatDeductible') else expense['vat']):
                raise ValueError('매입 비용을 초과하거나 같은 주문에 중복 배분할 수 없습니다.')


def party_account(state, record, data):
    number = record['customerNumber' if record['direction'] == 'SELL' else 'supplierNumber']
    name = record['customerName' if record['direction'] == 'SELL' else 'supplierName']
    matches = [a for a in state['accounts'] if number and digits(a.get('businessNumber')) == number]
    if data.get('accountId'):
        account = get(state, 'accounts', data['accountId'])
        if digits(account.get('businessNumber')) not in ('', number):
            raise ValueError('선택한 거래처의 사업자등록번호가 수집 자료와 다릅니다.')
        if not account.get('businessNumber'):
            if data.get('confirmParty') is not True:
                raise ValueError('수집 자료와 같은 거래처인지 확인해 주세요.')
            account['businessNumber'] = number
        return account
    if len(matches) == 1:
        return matches[0]
    if matches:
        raise ValueError('같은 사업자등록번호의 거래처가 여러 개입니다. 거래처를 선택해 주세요.')
    if data.get('createAccount') is not True or not number or not name:
        raise ValueError('거래처를 선택하거나 신규 등록을 선택해 주세요.')
    account = dict(id=ident('ac'), name=name, businessNumber=number, kind='고객사' if record['direction'] == 'SELL' else '매입처', contact='', phone='', email='', notes='세금계산서 수집 자료에서 등록')
    state['accounts'].append(account)
    return account


def book(state, record, data):
    if record.get('conflict'):
        raise ValueError('대조 불일치를 먼저 확인해 주세요. 이 자료는 장부에 반영할 수 없습니다.')
    if record.get('ledgerId'):
        return record['ledgerId']
    account = party_account(state, record, data)
    kind = 'invoices' if record['direction'] == 'SELL' else 'expenses'
    exact = [r for r in state[kind] if re.sub(r'[-\s]', '', str(r.get('number', ''))).upper() == record['approval']]
    existing = get(state, kind, data['existingId']) if data.get('existingId') else exact[0] if len(exact) == 1 else None
    if len(exact) > 1 or (exact and existing and exact[0]['id'] != existing['id']):
        raise ValueError('동일 승인번호의 기존 기록을 연결해 주세요.')
    if existing:
        if existing.get('taxRecordId') or existing.get('status') == 'void' or existing.get('category') == '개인 인출':
            raise ValueError('이 기록은 연결할 수 없습니다.')
        if any(existing.get(k) != v for k, v in dict(accountId=account['id'], net=record['net'], vat=record['vat'], date=record['date']).items()):
            raise ValueError('기존 장부와 거래처·금액·작성일이 다릅니다. 기존 기록을 확인해 주세요.')
        row = existing
    else:
        # Same date/party/amount is a candidate, not a proof of identity.
        candidates = [r for r in state[kind] if not r.get('taxRecordId') and r.get('status') != 'void' and
                      all(r.get(k) == v for k,v in dict(accountId=account['id'], net=record['net'], vat=record['vat'], date=record['date']).items())]
        if candidates and data.get('confirmDistinct') is not True:
            raise ValueError('동일 날짜·거래처·금액의 장부가 있습니다. 기존 기록 연결 또는 별도 거래 확인을 선택해 주세요.')
        row = dict(id=ident('invoice' if kind == 'invoices' else 'expense'), accountId=account['id'], orderId='',
                   date=record['date'], net=record['net'], vat=record['vat'], number=record['approval'])
        if kind == 'invoices':
            row.update(status='issued', dueDate=day(data.get('dueDate'), True))
        else:
            category = data.get('category', '자재비')
            if category not in ('자재비','외주비','월세','차량비','통신비','배송비','기타 사업 비용'):
                raise ValueError('사업 비용 항목을 확인해 주세요.')
            row.update(category=category, description=record['description'] or '전자세금계산서 매입', paidDate='', vatDeductible=data.get('vatDeductible') is True)
        state[kind].append(row)
    order_id = data.get('orderId') or row.get('orderId', '')
    if order_id:
        order = get(state, 'orders', order_id)
        if kind == 'invoices' and order['accountId'] != account['id']:
            raise ValueError('다른 고객의 주문을 연결할 수 없습니다.')
        row['orderId'] = order_id
    if record['net'] + record['vat'] < 0 and kind == 'invoices':
        original = next((r for r in state['taxRecords'] if r['mode'] == record['mode'] and r['direction'] == 'SELL' and
                         r['approval'] == record['originalApproval'] and r.get('ledgerId')), None)
        if not original:
            raise ValueError('원본 세금계산서를 먼저 수집·대조하여 장부에 연결해 주세요.')
        family_ids = {r['ledgerId'] for r in state['taxRecords'] if r.get('ledgerId') and r['mode']==record['mode'] and
                      r['direction']=='SELL' and (r['approval']==record['originalApproval'] or r['originalApproval']==record['originalApproval'])}
        remaining = -(row['net']+row['vat']); applications=[]
        for target in sorted((i for i in state['invoices'] if i['id'] in family_ids and i['accountId']==account['id'] and i['net']+i['vat']>0),key=lambda i:i['date']):
            value = min(remaining,max(0,adjusted_gross(state,target)))
            if value:
                applications.append(dict(invoiceId=target['id'],amount=value));remaining-=value
        if remaining:
            raise ValueError('원본과 양수 수정 기록을 초과합니다. 관련 세금계산서를 먼저 장부에 연결해 주세요.')
        row['creditApplications'] = applications
    row.update(taxRecordId=record['id'], number=record['approval'])
    record['ledgerId'] = row['id']
    record['bookedAt'] = now()
    return row['id']


def profile(record):
    return {k:text(record.get(k) or '', 300 if k == 'address' else 160) for k in
            ('name','businessNumber','representative','address','businessType','businessItem','email','phone')}


def add_draft(state, account_id, rows, data, **extra):
    draft = dict(id=ident('td'), key='G' + ident('')[-23:], mode='', status='draft', stateCode=None,
                 accountId=account_id, company=profile(state['settings']), customer=profile(get(state,'accounts',account_id)),
                 date=day(data.get('date')), items=copy.deepcopy(rows), purpose='청구', memo='',
                 net=sum(r['net'] for r in rows), vat=sum(r['vat'] for r in rows), events=[], createdAt=now(), **extra)
    draft['events'].append(dict(at=now(), label='발급 초안 생성'))
    state['taxDrafts'].append(draft)
    return draft


def apply(state, action, data, workspace):
    if action == 'tax.reference':
        record = get(state,'taxRecords',data.get('id'))
        existing = next((d for d in state['taxDrafts'] if d.get('taxRecordId')==record['id']),None)
        if existing:
            return dict(recordId=existing['id'])
        if record['direction']!='SELL' or not record.get('ledgerId') or record.get('conflict') or record['net']<=0 or record['taxType']!='과세':
            raise ValueError('장부에 대조한 양수 과세 매출만 수정 원본으로 가져올 수 있습니다.')
        if not record.get('detailLoaded') or record.get('detailWarning') or not record['items']:
            raise ValueError('합계가 일치하는 상세 품목을 먼저 조회해 주세요.')
        invoice = get(state,'invoices',record['ledgerId'])
        rows=[]
        for r in record['items']:
            try:
                quantity=Decimal(r['quantity'] or '0');price=Decimal(r['unitPrice'] or '0')
                if not quantity.is_finite() or not price.is_finite():raise ValueError()
            except (InvalidOperation,ValueError):
                raise ValueError('수집 품목의 수량·단가를 확인해 주세요.')
            rows.append(dict(name=r['name'],spec=r['spec'],quantity=float(quantity),unitPrice=float(price),net=r['net'],vat=r['vat']))
        d=add_draft(state,invoice['accountId'],rows,{'date':record['date']},orderId=invoice.get('orderId',''),deliveryId='',
                    title=record['description'] or '홈택스 수집 원본',modifyCode=record.get('modifyCode',0),originalApproval=record['originalApproval'],
                    referenceOnly=True,taxRecordId=record['id'],approval=record['approval'],providerApproval=record.get('providerApproval',record['approval']))
        d.update(mode=record['mode'],status='issued',stateCode=304,key='')
        d['company'].update(name=record['supplierName'],businessNumber=record['supplierNumber'])
        d['customer'].update(name=record['customerName'],businessNumber=record['customerNumber'])
        d['events']=[dict(at=now(),label='홈택스 수집·대조 자료를 수정 원본으로 연결 · 신규 발급 없음')]
        return dict(recordId=d['id'])
    if action == 'tax.book':
        return dict(recordId=book(state, get(state,'taxRecords',data.get('id')), data))
    if action == 'tax.cost':
        record = get(state,'taxRecords',data.get('id'))
        if record['direction'] != 'BUY' or not record.get('ledgerId') or record.get('conflict'):
            raise ValueError('대조를 마친 매입 기록이 필요합니다.')
        expense = get(state,'expenses',record['ledgerId'])
        # A single expense.orderId cannot coexist with split allocations.
        expense['orderId'] = ''
        rows = data.get('rows')
        if not isinstance(rows,list) or len(rows) > 100:
            raise ValueError('원가 연결 내역을 확인해 주세요.')
        total_basis = any(r.get('lineId') == 'total' for r in rows)
        if total_basis and any(r.get('lineId') != 'total' for r in rows):
            raise ValueError('전체 금액과 품목 금액을 동시에 연결할 수 없습니다.')
        basis = record['items'] if record.get('detailLoaded') and record['items'] and not record.get('detailWarning') and not total_basis else [dict(id='total',net=record['net'],vat=record['vat'])]
        caps = {r['id']:r['net'] + (0 if expense.get('vatDeductible') else r['vat']) for r in basis}
        allocated = {}; entries = []
        for r in rows:
            order = get(state,'orders',r.get('orderId')); line = str(r.get('lineId')); value = amount(r.get('amount'))
            if line not in caps or not value or value * caps[line] <= 0:
                raise ValueError('품목별 원가와 같은 부호의 연결 금액을 입력해 주세요.')
            allocated[line] = allocated.get(line, 0) + value
            if abs(allocated[line]) > abs(caps[line]):
                raise ValueError('품목 원가를 초과하여 연결할 수 없습니다.')
            entries.append(dict(id=ident('cost'), expenseId=expense['id'], taxRecordId=record['id'], orderId=order['id'], lineId=line, amount=value))
        state['costAllocations'][:] = [c for c in state['costAllocations'] if c['expenseId'] != expense['id']] + entries
        return dict(recordId=record['id'])
    if action == 'tax.expense':
        expense = get(state,'expenses',data.get('id'))
        if not expense.get('taxRecordId'):
            raise ValueError('연결된 매입이 아닙니다.')
        if data.get('vatDeductible') is not expense.get('vatDeductible') and any(c['expenseId'] == expense['id'] for c in state['costAllocations']):
            raise ValueError('부가세 공제 여부를 바꾸려면 먼저 원가 연결을 비워 주세요.')
        expense['paidDate'] = day(data.get('paidDate'), True)
        if expense['net'] + expense['vat'] < 0 and expense['paidDate']:
            raise ValueError('매입 취소에 따른 환불은 이 지급일 화면에서 처리하지 않습니다. 지급일은 비워 주세요.')
        expense['vatDeductible'] = data.get('vatDeductible') is True
        return dict(recordId=expense['id'])
    if action == 'tax.draft':
        order = get(state,'orders',data.get('orderId'))
        if order.get('status') == '취소' or not order.get('items'):
            raise ValueError('유효한 주문과 상세 품목이 필요합니다.')
        delivery_id = data.get('deliveryId', '')
        delivery = get(state,'deliveries',delivery_id) if delivery_id else None
        if delivery and (delivery['orderId'] != order['id'] or delivery['status'] == 'void'):
            raise ValueError('이 주문의 유효한 납품을 선택해 주세요.')
        for d in state['taxDrafts']:
            if d.get('orderId') == order['id'] and d['status'] != 'discarded' and not d.get('modifyCode'):
                if not delivery_id or not d.get('deliveryId') or delivery_id == d['deliveryId']:
                    raise ValueError('이 주문·납품의 발급 초안이 이미 있습니다. 기존 초안을 확인해 주세요.')
        if any(i.get('orderId') == order['id'] and i['status'] != 'void' and
               (not delivery_id or not i.get('deliveryId') or i['deliveryId'] == delivery_id) for i in state['invoices']):
            raise ValueError('이미 매출이 연결된 주문·납품입니다. 홈택스 수집 자료와 기존 장부를 먼저 대조해 주세요.')
        draft = add_draft(state,order['accountId'],(delivery or order)['items'],data,orderId=order['id'],deliveryId=delivery_id,
                          title=order['title'],modifyCode=0,originalApproval='')
        return dict(recordId=draft['id'])
    if action == 'tax.save':
        d = get(state,'taxDrafts',data.get('id'))
        if d['status'] != 'draft' or d.get('payload'):
            raise ValueError('발급 요청 전 초안만 수정할 수 있습니다.')
        d['date'] = day(data.get('date'))
        if d.get('modifyCode') in (1,6) and d['date'] != d['originalDate']:
            raise ValueError('이 수정 사유는 원본 작성일을 사용합니다.')
        d['purpose'] = data.get('purpose','청구')
        if d['purpose'] not in ('청구','영수'):
            raise ValueError('청구·영수 구분을 확인해 주세요.')
        d['memo'] = text(data.get('memo',''),100)
        d['company'] = profile(data.get('company',d['company']))
        customer = profile(data.get('customer',d['customer']))
        account_number = digits(get(state,'accounts',d['accountId']).get('businessNumber'))
        if account_number and digits(customer['businessNumber']) != account_number:
            raise ValueError('발급 대상과 연결된 고객사의 사업자등록번호가 다릅니다. 거래처 정보를 확인해 주세요.')
        if d.get('modifyCode') and digits(customer['businessNumber']) != digits(d['customer']['businessNumber']):
            raise ValueError('이 화면에서 수정세금계산서의 공급받는 사업자는 변경할 수 없습니다.')
        d['customer'] = customer
        return dict(recordId=d['id'])
    if action == 'tax.discard':
        d = get(state,'taxDrafts',data.get('id'))
        if d['status'] != 'draft' or d.get('payload') or d.get('groupId'):
            raise ValueError('요청 전 일반 초안만 폐기할 수 있습니다. 수정 묶음은 발급 이력을 확인해 주세요.')
        d['status'] = 'discarded'; d['events'].append(dict(at=now(),label='초안 폐기'))
        return dict(recordId=d['id'])
    if action == 'tax.correct':
        original = get(state,'taxDrafts',data.get('id')); code = data.get('modifyCode')
        if original.get('stateCode') != 304 or not original.get('approval') or original['net'] <= 0:
            raise ValueError('전송 성공한 양수 세금계산서에서 수정해 주세요.')
        if type(code) is not int or code not in REASONS:
            raise ValueError('지원하는 수정 사유를 선택해 주세요. 내국신용장 사후개설은 연동 업체에서 처리해 주세요.')
        related = [d for d in state['taxDrafts'] if d.get('originalApproval') == original['approval'] and d['status'] != 'discarded']
        source_related = [r for r in state['taxRecords'] if r['mode']==original['mode'] and r['direction']=='SELL' and r['originalApproval']==original['approval']]
        if any(d.get('stateCode') != 304 or d.get('ledgerError') for d in related) or any(not r.get('ledgerId') or r.get('conflict') for r in source_related) or ((related or source_related) and code in (1,4,6)):
            raise ValueError('이전 수정 내역이 있습니다. 진행 중인 전송을 확인하고, 전액 수정은 팝빌에서 원본 관계를 확인해 주세요.')
        dt = original['date'] if code in (1,6) else day(data.get('date'))
        if dt < original['date']:
            raise ValueError('변동·반품·해제 발생일은 원본 작성일 이후여야 합니다.')
        rows = copy.deepcopy(original['items'])
        if code in (1,4,6):
            for r in rows:
                r['net'] *= -1; r['vat'] *= -1
                if isinstance(r.get('quantity'), (int,float)):
                    r['quantity'] *= -1
        else:
            net, vat = amount(data.get('net')), amount(data.get('vat'))
            if not net or net * vat < 0 or (code == 3 and net >= 0):
                raise ValueError('변동액 또는 반품 음수 금액을 확인해 주세요.')
            rows = [dict(name=REASONS[code], spec='', quantity=1,unitPrice=net,net=net,vat=vat)]
        remaining_net = original['net'] + sum(d['net'] for d in source_related)
        remaining_vat = original['vat'] + sum(d['vat'] for d in source_related)
        if remaining_net + sum(r['net'] for r in rows) < 0 or remaining_vat + sum(r['vat'] for r in rows) < 0:
            raise ValueError('원본과 이전 수정 금액을 초과하는 감소입니다.')
        if code == 2 and remaining_net + sum(r['net'] for r in rows) == 0:
            raise ValueError('전액 취소는 상황에 맞는 계약 해제 또는 이중발급 사유를 사용해 주세요.')
        group = ident('correction')
        d = add_draft(state,original['accountId'],rows,{'date':dt},orderId=original.get('orderId',''),deliveryId=original.get('deliveryId',''),
                      title=REASONS[code],modifyCode=code,originalApproval=original['approval'],originalDate=original['date'],groupId=group)
        d['company'] = copy.deepcopy(original['company']); d['customer'] = copy.deepcopy(original['customer'])
        d['originalProviderApproval'] = original.get('providerApproval',original['approval'])
        if code == 1:
            net, vat = amount(data.get('net')), amount(data.get('vat'))
            if net <= 0 or vat < 0:
                raise ValueError('정정 후 양수 공급가액과 세액을 입력해 주세요.')
            plus = add_draft(state,original['accountId'],[dict(name=text(data.get('itemName') or original['title'],100),spec='',quantity=1,unitPrice=net,net=net,vat=vat)],
                             {'date':dt},orderId=original.get('orderId',''),deliveryId=original.get('deliveryId',''),title='기재사항 착오정정 · 수정분',modifyCode=1,
                             originalApproval=original['approval'],originalDate=original['date'],groupId=group,requiresDraftId=d['id'])
            plus['company'] = copy.deepcopy(d['company']); plus['customer'] = copy.deepcopy(d['customer'])
            plus['originalProviderApproval'] = d['originalProviderApproval']
        return dict(recordId=d['id'], groupId=group)
    raise ValueError('지원하지 않는 세금계산서 업무입니다.')


def payload(draft, mode):
    for p in (draft['company'], draft['customer']):
        if not p['name'] or not p['representative'] or not re.fullmatch(r'\d{10}',digits(p['businessNumber'])):
            raise ValueError('공급자·공급받는자의 상호, 대표자, 사업자등록번호 10자리를 입력해 주세요.')
    if mode != 'simulation' and (not draft['customer']['email'] or '@' not in draft['customer']['email']):
        raise ValueError('수신 이메일을 확인해 주세요. 팝빌 연동 확인 및 실제 발급 시 이메일이 발송됩니다.')
    if not 1 <= len(draft['items']) <= 99:
        raise ValueError('전자세금계산서는 품목 1~99개를 지원합니다.')
    if any(abs(Decimal(str(r.get('quantity',1)))) != abs(Decimal(str(r.get('quantity',1)))).quantize(Decimal('.01')) for r in draft['items']):
        raise ValueError('팝빌 발급 수량은 소수점 둘째 자리까지 지원합니다. 주문 품목 수량을 확인해 주세요.')
    if not draft['vat']:
        raise ValueError('이번 발급 기능은 부가세가 있는 과세 거래를 지원합니다. 영세율·면세 발급은 연동 업체에서 처리해 주세요.')
    result = dict(issueType='정발행', taxType='과세', chargeDirection='정과금', writeDate=draft['date'].replace('-',''), purposeType=draft['purpose'],
                  supplyCostTotal=str(draft['net']),taxTotal=str(draft['vat']),totalAmount=str(draft['net']+draft['vat']),invoicerMgtKey=draft['key'],
                  invoiceeType='사업자', remark1=draft['memo'],businessLicenseYN=False,bankBookYN=False,
                  invoicerSMSSendYN=False,invoiceeSMSSendYN=False)
    for prefix, p in (('invoicer',draft['company']),('invoicee',draft['customer'])):
        for suffix, key in (('CorpNum','businessNumber'),('CorpName','name'),('CEOName','representative'),('Addr','address'),('BizType','businessType'),('BizClass','businessItem')):
            result[prefix+suffix] = digits(p[key]) if key == 'businessNumber' else p[key]
        result[prefix + ('Email' if prefix == 'invoicer' else 'Email1')] = p['email']
    if draft.get('modifyCode'):
        result.update(modifyCode=draft['modifyCode'], orgNTSConfirmNum=draft.get('originalProviderApproval',draft['originalApproval']), remark2='당초 작성일: ' + draft['originalDate'])
    result['detailList'] = [dict(serialNum=n+1, purchaseDT=result['writeDate'],itemName=text(r['name'],100),spec=text(r.get('spec',''),60),
                                 qty=str(r.get('quantity',1)),unitCost=str(r.get('unitPrice',r['net'])),supplyCost=str(r['net']),tax=str(r['vat'])) for n,r in enumerate(draft['items'])]
    return result


def fixtures(state, job):
    """Synthetic, repeatable month fixtures; never derived from uploaded files."""
    account = next((a for a in state['accounts'] if a.get('kind') == '고객사'), None)
    result = []
    for n, (net, qty, name) in enumerate(((300000,2,'확인용 제어함'), (120000,3,'확인용 배선 부품'))):
        supplier = '8888800002' if job['direction'] == 'BUY' else digits(state['settings'].get('businessNumber')) or '8888800001'
        customer = (digits(state['settings'].get('businessNumber')) or '8888800001') if job['direction'] == 'BUY' else (digits((account or {}).get('businessNumber')) or '8888800003')
        result.append(dict(ntsconfirmNum=tax_provider.approval(job['direction']+job['start']+str(n)),writeDate=job['start'].replace('-',''),
                           supplyCostTotal=str(net),taxTotal=str(net//10),totalAmount=str(net+net//10),taxType='과세',
                           invoicerCorpNum=supplier,invoicerCorpName='가상 자재상사' if job['direction']=='BUY' else state['settings']['name'],
                           invoiceeCorpNum=customer,invoiceeCorpName=state['settings']['name'] if job['direction']=='BUY' else (account or {}).get('name','가상 고객'),
                           itemName=name,detailList=[dict(itemName=name,spec='표준',qty=str(qty),unitCost=str(net//qty),supplyCost=str(net),tax=str(net//10))]))
    return result


def prepare(state, body, workspace):
    kind = body.get('action'); data = body.get('data', {})
    mode = tax_provider.check(workspace,state['settings'])
    op = dict(id=ident('op'),kind=kind,mode=mode,started=time.time(),companyNumber=digits(state['settings'].get('businessNumber')))
    if kind == 'collect.start':
        start,end = day(data.get('start')),day(data.get('end'))
        if start > end or (date.fromisoformat(end)-date.fromisoformat(start)).days > 89:
            raise ValueError('수집 기간은 시작일 이후, 최대 90일로 선택해 주세요.')
        if data.get('direction') not in ('SELL','BUY'):
            raise ValueError('매출 또는 매입을 선택해 주세요.')
        if len(state['taxJobs']) >= 200:
            raise ValueError('수집 작업 보관 한도에 도달했습니다. 관리자에게 문의해 주세요.')
        row = dict(id=ident('job'),mode=mode,start=start,end=end,direction=data['direction'],status='requesting',page=1,
                   added=0,duplicates=0,conflicts=0,createdAt=now())
        state['taxJobs'].append(row); op.update(start=start,end=end,direction=row['direction'])
    elif kind == 'collect.poll':
        row = get(state,'taxJobs',data.get('id'))
        if row['mode'] != mode or not row.get('jobId') or row['status'] in ('complete','failed'):
            raise ValueError('조회 가능한 수집 작업이 아닙니다. 필요하면 기간을 지정해 다시 수집해 주세요.')
        op.update(jobId=row['jobId'],page=row['page'],direction=row['direction'],fixtures=fixtures(state,row) if mode=='simulation' else [])
    elif kind == 'record.detail':
        row = get(state,'taxRecords',data.get('id'))
        if row['mode'] != mode:
            raise ValueError('연결 모드가 다릅니다.')
        if mode == 'simulation':
            raise ValueError('모의 수집 자료에는 상세 품목이 이미 포함되어 있습니다.')
        op.update(approval=row.get('providerApproval',row['approval']),direction=row['direction'])
    elif kind in ('draft.issue','draft.status'):
        row = get(state,'taxDrafts',data.get('id'))
        if row.get('mode') and row['mode'] != mode:
            raise ValueError('발급 요청과 같은 연결 환경에서 상태를 조회해 주세요.')
        if kind == 'draft.issue':
            if row['status'] != 'draft' or row.get('payload'):
                raise ValueError('이미 요청한 초안입니다. 중복 발급하지 않고 상태 조회로 확인해 주세요.')
            if data.get('confirmed') is not True:
                raise ValueError('작성일·거래처·금액과 발급 안내를 확인해 주세요.')
            if mode != 'simulation' and digits(row['company']['businessNumber']) != digits(state['settings'].get('businessNumber')):
                raise ValueError('초안 공급자의 사업자등록번호가 현재 연동 사업자와 다릅니다.')
            if row.get('requiresDraftId') and get(state,'taxDrafts',row['requiresDraftId']).get('stateCode') != 304:
                raise ValueError('같은 수정 묶음의 취소분이 전송 성공한 뒤 수정분을 발급해 주세요.')
            row['payload'] = payload(row,mode); row['mode'] = mode; row['status'] = 'submitting'
            row['events'].append(dict(at=now(),label='발급 요청 저장 · 동일 관리번호로 추적'))
        elif not row.get('payload') or row['status'] == 'discarded':
            raise ValueError('발급을 요청한 기록만 조회할 수 있습니다.')
        op.update(key=row['key'],payload=copy.deepcopy(row['payload']))
    else:
        raise ValueError('지원하지 않는 연동 요청입니다.')
    if row.get('operation') and time.time()-row['operation']['started'] < 100:
        raise ValueError('연동 요청을 처리 중입니다. 잠시 후 상태를 조회해 주세요.')
    row['operation'] = dict(id=op['id'],started=op['started'])
    op['recordId'] = row['id']
    return op


def finish(state, op, response):
    kind = 'taxJobs' if op['kind'].startswith('collect.') else 'taxRecords' if op['kind']=='record.detail' else 'taxDrafts'
    row = get(state,kind,op['recordId'])
    if row.get('operation',{}).get('id') != op['id']:
        return dict(recordId=row['id'],notice='새로운 조회가 진행 중입니다.')
    row.pop('operation',None)
    row['checkedAt'] = now()
    if response.get('error'):
        row['error'] = text(response['error'],300)
        if kind == 'taxDrafts':
            row['status'] = 'unknown' if row.get('stateCode') is None else row['status']
            row['events'].append(dict(at=now(),label=row['error']))
        elif kind == 'taxJobs':
            row['status'] = 'unknown' if response.get('uncertain') else 'failed'
        return dict(recordId=row['id'],notice=row['error'])
    row.pop('error',None)
    if op['kind'] == 'collect.start':
        row.update(jobId=text(str(response['jobId']),100),status='waiting')
    elif op['kind'] == 'collect.poll':
        if not response['done']:
            row['status'] = 'waiting'
        else:
            for raw in response['rows']:
                source = source_record(raw,row['direction'],row['mode'])
                check_source_owner(source,op)
                _, status = upsert(state,source)
                counter = {'new':'added','duplicate':'duplicates','conflict':'conflicts'}[status]
                row[counter] += 1
            page = int(response['page']); pages = int(response['pageCount'])
            if page != row['page'] or pages < 0:
                raise ValueError('수집 페이지 응답을 확인해 주세요.')
            row.update(page=page+1,pageCount=pages,total=int(response['total']),status='complete' if page>=pages else 'paging')
    elif op['kind'] == 'record.detail':
        source = source_record(response['record'],row['direction'],row['mode'])
        check_source_owner(source,op)
        if source['approval'] != row['approval']:
            raise ValueError('조회한 승인번호가 요청과 다릅니다.')
        upsert(state,source)
    elif response.get('notFound'):
        row['status'] = 'unknown'
        row['error'] = '팝빌에서 관리번호를 찾지 못했습니다. 지연 접수 여부를 팝빌에서 확인해 주세요. 자동 재발급은 하지 않습니다.'
        row['events'].append(dict(at=now(),label=row['error']))
    else:
        info = response['info']; code = int(info.get('stateCode',0))
        # A remote management key is trusted only if the invoice identity matches.
        if row['mode'] != 'simulation':
            expected = dict(invoicerCorpNum=digits(row['company']['businessNumber']),invoiceeCorpNum=digits(row['customer']['businessNumber']),
                            supplyCostTotal=str(row['net']),taxTotal=str(row['vat']),writeDate=row['date'].replace('-',''))
            if any(str(info.get(k,'')) != str(v) for k,v in expected.items()):
                row.update(status='unknown',error='연동 자료의 사업자·금액·작성일이 초안과 다릅니다. 팝빌에서 확인해 주세요.')
                row['events'].append(dict(at=now(),label=row['error']))
                return dict(recordId=row['id'],notice=row['error'])
        if row.get('stateCode') == 304 and code != 304:
            row['error'] = '전송 성공 후 상태가 변경되었습니다. 원본 장부를 보존했습니다. 팝빌에서 확인해 주세요.'
            return dict(recordId=row['id'],notice=row['error'])
        row.update(stateCode=code,status='issued' if code in (300,301,302,303,304,305) else 'attention',
                   ntsResult=text(str(info.get('ntsresult') or ''),100),ntsError=text(str(info.get('ntssendErrCode') or ''),100),
                   sentAt=text(str(info.get('ntssendDT') or ''),30),resultAt=text(str(info.get('ntsresultDT') or ''),30))
        row['events'].append(dict(at=now(),label=('모의 ' if row['mode']=='simulation' else '')+STATES.get(code,'상태 확인 필요')+' ('+str(code)+')'))
        row['events'] = row['events'][-100:]
        if code in (300,301,302,303,304,305) and info.get('ntsconfirmNum'):
            number = approval(info['ntsconfirmNum'])
            if row.get('approval') and row['approval'] != number:
                raise ValueError('승인번호가 달라졌습니다. 연동 업체에서 확인해 주세요.')
            row['approval'] = number
            row['providerApproval'] = re.sub(r'[-\s]','',str(info['ntsconfirmNum']))
            raw = dict(row['payload'],ntsconfirmNum=number)
            record, _ = upsert(state,source_record(raw,'SELL',row['mode']))
            row['taxRecordId'] = record['id']
            try:
                candidate = copy.deepcopy(state)
                ledger_id = book(candidate,get(candidate,'taxRecords',record['id']),dict(accountId=row['accountId'],confirmParty=True,orderId=row.get('orderId','')))
                get(candidate,'invoices',ledger_id)['deliveryId'] = row.get('deliveryId','')
                for name in ('invoices','accounts','taxRecords'):
                    state[name] = candidate[name]
                row.pop('ledgerError',None)
            except ValueError as error:
                # Issuance already happened. Preserve status and expose manual reconciliation.
                row['ledgerError'] = str(error)
    return dict(recordId=row['id'])


def check_source_owner(source,op):
    key='supplierNumber' if source['direction']=='SELL' else 'customerNumber'
    if op['mode']!='simulation' and source[key]!=op['companyNumber']:
        raise ValueError('수집 자료가 현재 회사의 매출·매입인지 확인해 주세요.')


def complete(state, op, response, validator):
    candidate = copy.deepcopy(state)
    try:
        result = finish(candidate,op,response)
        validator(candidate)
        return candidate,result
    except (ValueError,KeyError,TypeError,InvalidOperation):
        result = finish(state,op,dict(error='연동 응답과 장부를 대조하지 못했습니다. 원본·거래처·수정 관계를 확인한 후 다시 조회해 주세요.',uncertain=True))
        return state,result
