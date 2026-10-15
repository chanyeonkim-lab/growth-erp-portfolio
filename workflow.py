"""Versioned sales documents. Commands are applied under the workspace row lock."""
import copy
import hashlib
import re
import uuid
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from zoneinfo import ZoneInfo
from company_profile import initialize_company

PROFILE = ('name', 'businessNumber', 'representative', 'address', 'businessType', 'businessItem', 'phone', 'fax', 'email', 'bank', 'bankAccount', 'bankHolder')

def ident(prefix):
    return prefix + '-' + uuid.uuid4().hex


def normalize(state):
    initialize_company(state['settings'])
    for kind in ('deliveries','taxRecords','taxJobs','taxDrafts','costAllocations'):
        state.setdefault(kind, [])
    return state


def text(value, limit=160):
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f'입력 내용은 {limit}자 이내로 작성해 주세요.')
    return value.strip()


def day(value, optional=False):
    if optional and not value:
        return ''
    try:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError('날짜를 확인해 주세요.')
    return value


def decimal(value, maximum=10**9, zero=False):
    try:
        if isinstance(value, bool):
            raise ValueError()
        n = Decimal(str(value))
        if not n.is_finite() or n < 0 or (not zero and n == 0) or n > maximum or n != n.quantize(Decimal('.001')):
            raise ValueError()
        return n
    except (InvalidOperation, ValueError):
        raise ValueError('수량은 0보다 큰 숫자, 소수점 셋째 자리까지 입력해 주세요.')


def won(value):
    return int(Decimal(value).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def numeric(value):
    return int(value) if value == int(value) else float(value)


def items(rows):
    if not isinstance(rows, list) or not 1 <= len(rows) <= 150:
        raise ValueError('품목을 1개 이상, 150개 이하로 입력해 주세요.')
    result = []
    for r in rows:
        name = text(r.get('name', ''), 140)
        if not name:
            raise ValueError('품목명을 입력해 주세요.')
        quantity = decimal(r.get('quantity'))
        price = decimal(r.get('unitPrice'), maximum=10**10, zero=True)
        if price != int(price):
            raise ValueError('단가는 원 단위 정수로 입력해 주세요.')
        rate = r.get('vatRate', 10)
        if isinstance(rate, bool) or rate not in (0, 10):
            raise ValueError('부가세율은 0% 또는 10%로 선택해 주세요.')
        net = won(quantity * price)
        if net > 10**13:
            raise ValueError('품목 금액이 너무 큽니다.')
        result.append(dict(id=ident('line'), section=text(r.get('section', ''), 40), name=name,
                           spec=text(r.get('spec', ''), 160), unit=text(r.get('unit', '개'), 12),
                           quantity=numeric(quantity), unitPrice=int(price), net=net, vat=won(Decimal(net)*rate/100), vatRate=rate))
    if sum(r['net'] + r['vat'] for r in result) > 10**13:
        raise ValueError('총금액이 너무 큽니다.')
    return result


def quote_items(state, rows):
    if state.get('ledgerVersion') == 2:
        import ledger
        return ledger.item_rows(state, rows)
    return items(rows)


def whole_quote_items(state, rows):
    result = quote_items(state, rows)
    if any(Decimal(str(row['quantity'])) != Decimal(str(row['quantity'])).to_integral_value() for row in result):
        raise ValueError('견적 수량은 1 이상의 정수로 입력해 주세요.')
    return result


def get(state, kind, key):
    found = next((r for r in state[kind] if r['id'] == key), None)
    if not found:
        raise ValueError('연결할 기록을 찾을 수 없습니다. 새로고침해 주세요.')
    return found


def family(q):
    return q.get('familyId', q['id'])


def profile(record):
    return {k: text(record.get(k, ''), 500 if k == 'address' else 160) for k in PROFILE}


def snapshots(state, account_id):
    return dict(company=profile(state['settings']), customer=profile(get(state, 'accounts', account_id)))


def total(record):
    record['net'] = sum(i['net'] for i in record['items'])
    record['vat'] = sum(i['vat'] for i in record['items'])
    record['amount'] = record['net'] + record['vat']
    return record


def number(state, kind, prefix, dt):
    base = prefix + '-' + dt.replace('-', '') + '-'
    used = {r.get('number') for r in state[kind]}
    n = 1
    while f'{base}{n:03}' in used:
        n += 1
    return f'{base}{n:03}'


def guard(previous, proposed):
    """The generic ledger endpoint cannot forge or rewrite managed records."""
    normalize(previous); normalize(proposed)
    import ledger
    ledger.guard(previous, proposed)
    import tax_workflow
    tax_workflow.guard(previous, proposed)
    import statement_workflow
    statement_workflow.guard(previous, proposed)
    for kind in ('quotes', 'orders', 'deliveries', 'documents'):
        old = {r['id']: r for r in previous[kind]}
        new = {r['id']: r for r in proposed[kind]}
        protected = {key for key, r in old.items() if kind in ('quotes','orders','deliveries') or r.get('generated')}
        added = {key for key, r in new.items() if (kind in ('quotes','orders','deliveries') or r.get('generated')) and key not in old}
        if added or any(new.get(key) != old[key] for key in protected):
            raise ValueError('견적·납품·생성 문서는 해당 화면에서 수정해 주세요. 이전 버전은 보존됩니다.')
    for q in previous['quotes']:
        if any(family(r) == q['id'] and r.get('workflowVersion') == 2 for r in previous['quotes']) or any(o.get('quoteId') == q['id'] and o.get('workflowVersion') == 2 for o in previous['orders']):
            if next((r for r in proposed['quotes'] if r['id'] == q['id']), None) != q:
                raise ValueError('연결된 원본 견적은 새 버전으로 수정해 주세요.')


def delivery_status(state, order):
    active = [d for d in state['deliveries'] if d['orderId'] == order['id'] and d['status'] != 'void']
    if not active:
        order['status'] = '주문 접수'
    else:
        delivered = {}
        for d in active:
            for r in d['items']:
                delivered[r['orderLineId']] = delivered.get(r['orderLineId'], Decimal(0)) + Decimal(str(r['quantity']))
        order['status'] = '납품 완료' if all(delivered.get(r['id'], 0) == Decimal(str(r['quantity'])) for r in order['items']) else '일부 납품'


def apply(state, command, workspace, actor=None):
    normalize(state)
    action = command.get('action')
    data = command.get('data', {})
    if not isinstance(data, dict):
        raise ValueError('입력 형식을 확인해 주세요.')
    now = datetime.now(ZoneInfo('Asia/Seoul')).isoformat(timespec='seconds')
    result = {}
    file_content = None
    if isinstance(action, str) and action.startswith('erp.'):
        import ledger
        start=len(state.get('erpAudit',[]))
        result=ledger.apply(state, action, data, workspace)
        for event in state['erpAudit'][start:]:event['actorUserId']=actor or 'local'
        return result
    if state.get('ledgerVersion') == 2 and action == 'document.generate' and data.get('kind') == 'quote':
        from erp_documents import generate
        return generate(state,dict(kind='quote',id=data.get('id'),format='pdf'),workspace)
    if state.get('ledgerVersion') == 2 and (str(action).startswith(('statement.', 'tax.', 'delivery.'))):
        raise ValueError('전체 거래 화면에서 전표·납품·수금·지급을 관리해 주세요.')
    if isinstance(action, str) and action.startswith('statement.'):
        import statement_workflow
        return statement_workflow.apply(state, action, data), None
    if isinstance(action, str) and action.startswith('tax.'):
        import tax_workflow
        return tax_workflow.apply(state, action, data, workspace), None
    if action == 'quote.save':
        mode = data.get('mode', 'new')
        if mode not in ('new', 'copy', 'revise', 'update'):
            raise ValueError('견적 저장 방식을 확인해 주세요.')
        source = get(state, 'quotes', data.get('sourceId')) if mode != 'new' else None
        account = get(state, 'accounts', data.get('accountId'))
        dt = day(data.get('date'))
        title = text(data.get('title', ''))
        if not title:
            raise ValueError('견적명을 입력해 주세요.')
        key = ident('quote')
        group = family(source) if mode in ('revise', 'update') else key
        versions = [q for q in state['quotes'] if family(q) == group]
        if mode == 'revise' and max(versions, key=lambda q: q.get('version', 1))['id'] != source['id']:
            raise ValueError('최신 견적 버전에서 수정해 주세요.')
        if mode == 'revise' and account['id'] != source['accountId']:
            raise ValueError('고객사를 바꾸려면 새 견적으로 복사해 주세요.')
        num = source['number'] if mode in ('revise', 'update') else text(data.get('number', '')) or number(state, 'quotes', 'Q', dt)
        if any(q.get('number') == num and q['accountId'] == account['id'] and family(q) != group for q in state['quotes']):
            raise ValueError('이 고객사의 견적번호가 이미 있어요. 다른 번호를 사용해 주세요.')
        q = total(dict(id=source['id'] if mode == 'update' else key, workflowVersion=2, familyId=group,
                       version=source.get('version', 1) if mode == 'update' else max([r.get('version', 1) for r in versions], default=0)+1,
                       sourceId=source.get('sourceId', '') if mode == 'update' else source['id'] if source else '', accountId=account['id'], number=num, date=dt, title=title,
                       validUntil=day(data.get('validUntil'), True), deliveryTerms=text(data.get('deliveryTerms', ''), 500),
                       attention=text(data.get('attention', ''), 160),
                       paymentTerms=text(data.get('paymentTerms', ''), 500), notes=text(data.get('notes', ''), 2000),
                       changeNote=text(data.get('changeNote', ''), 200), items=whole_quote_items(state, data.get('items')),
                       status='작성 완료', createdAt=source.get('createdAt', now) if mode == 'update' else now,
                       updatedAt=now, **snapshots(state, account['id'])))
        if q['validUntil'] and q['validUntil'] < dt:
            raise ValueError('견적 유효일은 견적일 이후로 입력해 주세요.')
        if mode == 'new' and data.get('documentId'):
            d = get(state, 'documents', data['documentId'])
            if d.get('generated') or d.get('quoteId') or d.get('expenseId'):
                raise ValueError('이미 연결한 문서입니다.')
            q['documentId'] = d['id']
            d.update(quoteId=key, accountId=account['id'], status='등록 완료')
        if mode == 'update':
            for field in ('documentId', 'importSource'):
                if field in source:
                    q[field] = copy.deepcopy(source[field])
            source.clear()
            source.update(q)
        else:
            state['quotes'].append(q)
        result['recordId'] = q['id']
    elif action == 'quote.delete':
        q = get(state, 'quotes', data.get('id'))
        for order in state['orders']:
            if order.get('quoteId') == q['id']:
                order['quoteId']='';order['quoteFamilyId']='';order.pop('quoteSnapshot',None)
        for document in state['documents']:
            if document.get('quoteId') == q['id']:
                document['quoteId'] = ''
                document['status'] = '견적 삭제됨 · 파일 보존'
        state['quotes'] = [record for record in state['quotes'] if record['id'] != q['id']]
        result['recordId'] = q['id']
    elif action == 'quote.family.delete':
        groups = data.get('ids')
        if not isinstance(groups, list) or not 1 <= len(groups) <= 500:
            raise ValueError('삭제할 견적을 1건 이상 선택해 주세요.')
        groups = [text(group) for group in groups]
        if not all(groups) or len(groups) != len(set(groups)):
            raise ValueError('선택한 견적 목록을 확인해 주세요.')
        known = {family(q) for q in state['quotes']}
        if any(group not in known for group in groups):
            raise ValueError('삭제할 견적을 찾을 수 없습니다. 새로고침해 주세요.')
        selected_groups = set(groups)
        quote_ids = {q['id'] for q in state['quotes'] if family(q) in selected_groups}
        for order in state['orders']:
            if order.get('quoteFamilyId') in selected_groups or order.get('quoteId') in quote_ids:
                order['quoteId'] = ''
                order['quoteFamilyId'] = ''
                order.pop('quoteVersion', None)
                order.pop('quoteSnapshot', None)
        for document in state['documents']:
            if document.get('quoteId') in quote_ids:
                document['quoteId'] = ''
                document['status'] = '견적 삭제됨 · 파일 보존'
        state['quotes'] = [q for q in state['quotes'] if q['id'] not in quote_ids]
        result.update(familyIds=groups, familyCount=len(groups), versionCount=len(quote_ids))
    elif action == 'quote.convert':
        q = get(state, 'quotes', data.get('quoteId'))
        if any(Decimal(str(r.get('quantity', 0))) <= 0 or r.get('net', 0) < 0 for r in q.get('items', [])):
            raise ValueError('차감 품목이 있는 과거 견적입니다. 실제 납품 품목·수량을 확인해 새 버전으로 작성한 뒤 주문으로 전환해 주세요.')
        if any(o.get('quoteFamilyId', o.get('quoteId')) == family(q) for o in state['orders']):
            raise ValueError('이미 주문에 연결한 견적입니다. 새 주문은 견적 복사로 시작해 주세요.')
        line_items = copy.deepcopy(q['items']) if q.get('workflowVersion') == 2 else items(q.get('items'))
        if sum(i['net'] for i in line_items) != q['net'] or sum(i['vat'] for i in line_items) != q['vat']:
            raise ValueError('품목 합계와 견적 총액이 다릅니다. 견적을 수정해 주세요.')
        dt = day(data.get('date'))
        order_kind = data.get('orderType', 'DIS')
        if order_kind not in ('BOX', 'DIS'):
            raise ValueError('박스 유통 또는 분전반을 선택해 주세요.')
        o = total(dict(id=ident('order'), workflowVersion=2, orderType=order_kind, number=number(state, 'orders', order_kind, dt), accountId=q['accountId'],
                       quoteId=q['id'], quoteFamilyId=family(q), quoteVersion=q.get('version', 1), title=q['title'],
                       date=dt, dueDate=day(data.get('dueDate'), True), notes=text(data.get('notes', ''), 2000),
                       items=line_items, status='주문 접수', createdAt=now, quoteSnapshot=copy.deepcopy(q)))
        if o['dueDate'] and o['dueDate'] < dt:
            raise ValueError('납품 예정일을 확인해 주세요.')
        state['orders'].append(o)
        result['recordId'] = o['id']
    elif action == 'order.save':
        old = get(state, 'orders', data['id']) if data.get('id') else None
        account = get(state, 'accounts', data.get('accountId'))
        dt = day(data.get('date'))
        new_items = quote_items(state, data.get('items'))
        def item_signature(rows):
            keys=('productId','name','spec','unit','quantity','unitPrice','vatRate')
            return [tuple(str(r.get(k,'')) for k in keys) for r in rows]
        item_changed = bool(old and item_signature(new_items) != item_signature(old.get('items', [])))
        if old and item_changed:
            delivery_ids={r['id'] for r in state.get('deliveries',[]) if r.get('orderId')==old['id']}
            state['deliveries']=[r for r in state.get('deliveries',[]) if r['id'] not in delivery_ids]
            state['taxDrafts']=[r for r in state.get('taxDrafts',[]) if r.get('orderId')!=old['id']]
            state['documents']=[r for r in state.get('documents',[]) if not (r.get('generated') and r.get('deliveryId') in delivery_ids)]
        if old and account['id'] != old['accountId']:
            for kind in ('invoices','receipts','vouchers','payments','deliveries','taxDrafts','taxRecords'):
                for record in state.get(kind,[]):
                    if record.get('orderId')==old['id']:record['accountId']=account['id']
        if old and not item_changed:
            new_items=copy.deepcopy(old.get('items', []))
        o = total(dict(id=old['id'] if old else ident('order'), workflowVersion=2,
                       orderType=data.get('orderType', (old or {}).get('orderType', 'DIS')),
                       number=(old or {}).get('number') or number(state, 'orders', data.get('orderType', 'DIS'), dt),
                       accountId=account['id'], title=text(data.get('title', '')), quoteId=(old or {}).get('quoteId',''), date=dt,
                       dueDate=day(data.get('dueDate'), True), notes=text(data.get('notes', ''), 2000),
                       items=new_items, status=(old or {}).get('status','주문 접수'), createdAt=(old or {}).get('createdAt', now)))
        if o.get('orderType') not in ('BOX', 'DIS'):
            raise ValueError('박스 유통 또는 분전반을 선택해 주세요.')
        if not o['title'] or (o['dueDate'] and o['dueDate'] < dt):
            raise ValueError('주문명과 납품 예정일을 확인해 주세요.')
        if old and old.get('quoteId'):
            q = get(state, 'quotes', old['quoteId'])
            if account['id'] != q['accountId']:
                raise ValueError('원본 견적과 같은 고객사를 선택해 주세요.')
            o.update(quoteFamilyId=family(q), quoteVersion=q.get('version',1), quoteSnapshot=copy.deepcopy(q))
        if old:
            old.clear(); old.update(o)
        else:
            state['orders'].append(o)
        result['recordId'] = o['id']
    elif action == 'order.delete':
        o = get(state, 'orders', data.get('id'))
        order_id=o['id']
        voucher_ids={r['id'] for r in state.get('vouchers',[]) if r.get('orderId')==order_id}
        fulfillment_ids={r['id'] for r in state.get('fulfillments',[]) if r.get('orderId')==order_id or r.get('voucherId') in voucher_ids}
        delivery_ids={r['id'] for r in state.get('deliveries',[]) if r.get('orderId')==order_id}
        invoice_ids={r['id'] for r in state.get('invoices',[]) if r.get('orderId')==order_id or r.get('sourceDeliveryId') in delivery_ids}
        allocations=state.get('allocations',[])
        receipt_ids={r['id'] for r in state.get('receipts',[]) if r.get('orderId')==order_id}
        receipt_ids|={a.get('receiptId') for a in allocations if a.get('invoiceId') in invoice_ids}
        receipt_ids.discard(None)
        payments=[p for p in state.get('payments',[]) if p.get('orderId')==order_id or any(a.get('voucherId') in voucher_ids for a in p.get('allocations',[]))]
        payment_ids={p['id'] for p in payments}

        blockers=[]
        shared_payments=[p for p in payments if any(a.get('voucherId') not in voucher_ids for a in p.get('allocations',[]))]
        shared_receipts=[rid for rid in receipt_ids if any(a.get('receiptId')==rid and a.get('invoiceId') not in invoice_ids for a in allocations)]
        if shared_payments or shared_receipts:
            blockers.append(f'다른 거래에도 나누어 연결한 수금·지급 {len(shared_payments)+len(shared_receipts)}건')
        nonlive_modes=('simulation','sandbox')
        nonlive_tax_records={r['id'] for r in state.get('taxRecords',[]) if r.get('mode') in nonlive_modes}
        nonlive_approvals={str(r.get(key,'')) for r in state.get('taxRecords',[]) if r.get('mode') in nonlive_modes for key in ('approval','providerApproval') if r.get(key)}
        issued=[v for v in state.get('vouchers',[]) if v['id'] in voucher_ids and v.get('approvalNumber') and
                str(v['approvalNumber']) not in nonlive_approvals and not str(v['approvalNumber']).startswith('SIM')]
        issued += [i for i in state.get('invoices',[]) if i['id'] in invoice_ids and
                   ((i.get('taxRecordId') and i.get('taxRecordId') not in nonlive_tax_records) or
                    ((i.get('manualTax') or {}).get('approval') and str((i.get('manualTax') or {}).get('approval')) not in nonlive_approvals))]
        if issued:blockers.append(f'승인번호가 입력된 세금계산서 증빙 {len(issued)}건')
        removable_tax_links={kind:set() for kind in ('taxRecords','taxDrafts','taxJobs')}
        protected_tax_links=[]
        for kind in ('taxRecords','taxDrafts','taxJobs'):
            for record in state.get(kind,[]):
                if (record.get('orderId')==order_id or record.get('voucherId') in voucher_ids or
                    record.get('erpSourceId') in voucher_ids or record.get('sourceId') in voucher_ids or
                    record.get('deliveryId') in delivery_ids or record.get('invoiceId') in invoice_ids or
                    record.get('ledgerId') in invoice_ids):
                    unissued_draft=(kind=='taxDrafts' and record.get('status') in ('draft','discarded') and
                                    not record.get('payload') and not record.get('approval') and
                                    not record.get('providerApproval') and record.get('stateCode') is None and
                                    not record.get('taxRecordId'))
                    if record.get('mode') in nonlive_modes or unissued_draft:
                        removable_tax_links[kind].add(record['id'])
                    else:
                        protected_tax_links.append(record)
        if protected_tax_links:
            blockers.append(f'실제 발급·수집 또는 결과 확인이 필요한 전자세금계산서 {len(protected_tax_links)}건')
        credits=[v for v in state.get('vouchers',[]) if v['id'] not in voucher_ids and any(c.get('voucherId') in voucher_ids for c in v.get('credits',[]))]
        if credits:blockers.append(f'다른 전표의 차감 연결 {len(credits)}건')
        if blockers:
            guidance=[]
            if issued:guidance.append('승인번호는 전표 상세의 증빙·세무 정보에서 확인해 주세요')
            if protected_tax_links:guidance.append('전자세금계산서는 해당 메뉴에서 전송 상태를 확인해 주세요')
            raise ValueError(', '.join(blockers)+'이(가) 있어 일괄 삭제할 수 없습니다. '+(' '.join(guidance) if guidance else '해당 기록의 연결을 먼저 확인해 주세요.'))

        operation_sources=fulfillment_ids|{'reverse-'+source for source in fulfillment_ids}
        state['stockMoves']=[m for m in state.get('stockMoves',[]) if m.get('sourceId') not in operation_sources]
        state['fulfillments']=[r for r in state.get('fulfillments',[]) if r['id'] not in fulfillment_ids]
        state['vouchers']=[r for r in state.get('vouchers',[]) if r['id'] not in voucher_ids]
        state['payments']=[r for r in state.get('payments',[]) if r['id'] not in payment_ids]

        state['allocations']=[a for a in allocations if a.get('invoiceId') not in invoice_ids and a.get('receiptId') not in receipt_ids]
        state['receipts']=[r for r in state.get('receipts',[]) if r['id'] not in receipt_ids]
        state['invoices']=[r for r in state.get('invoices',[]) if r['id'] not in invoice_ids]
        state['deliveries']=[r for r in state.get('deliveries',[]) if r['id'] not in delivery_ids]
        for kind,ids in removable_tax_links.items():
            state[kind]=[r for r in state.get(kind,[]) if r['id'] not in ids]

        state['costAllocations']=[r for r in state.get('costAllocations',[]) if r.get('orderId')!=order_id]
        for expense in state.get('expenses',[]):
            if expense.get('orderId')==order_id:expense['orderId']=''

        related=lambda d:(d.get('orderId')==order_id or d.get('voucherId') in voucher_ids or
                          d.get('erpSourceId') in voucher_ids or d.get('sourceId') in voucher_ids or
                          d.get('deliveryId') in delivery_ids or d.get('sourceDeliveryId') in delivery_ids or
                          d.get('invoiceId') in invoice_ids)
        generated_ids=[d['id'] for d in state.get('documents',[]) if d.get('generated') and related(d)]
        for document in state.get('documents',[]):
            if document['id'] not in generated_ids and related(document):
                for key in ('orderId','voucherId','deliveryId','invoiceId'):document[key]=''
                document['status']='주문 삭제됨 · 파일 보존'
        state['documents']=[d for d in state.get('documents',[]) if d['id'] not in generated_ids]

        deleted_sources=(voucher_ids|{'reverse-'+source for source in voucher_ids}|operation_sources|
                         payment_ids|{'reverse-'+source for source in payment_ids})
        state['journalEntries']=[j for j in state.get('journalEntries',[]) if j.get('sourceId') not in deleted_sources]
        state['orders']=[record for record in state['orders'] if record['id']!=order_id]
        if state.get('ledgerVersion')==2:
            import ledger
            ledger.audit(state,'주문·연결 기록 일괄 삭제',order_id,o.get('number',order_id))
            state['erpAudit'][-1]['actorUserId']=actor or 'local'
            from ledger_replay import rebuild
            rebuild(state)
        result.update(recordId=order_id,deletedDocumentIds=generated_ids,
                      deleted=dict(vouchers=len(voucher_ids),fulfillments=len(fulfillment_ids)+len(delivery_ids),
                                   payments=len(payment_ids)+len(receipt_ids),
                                   taxLinks=sum(len(ids) for ids in removable_tax_links.values())))
    elif action == 'order.schedule':
        o = get(state, 'orders', data.get('id'))
        o['dueDate'] = day(data.get('dueDate'), True)
        if o['dueDate'] and o['dueDate'] < o['date']:
            raise ValueError('납품 예정일을 확인해 주세요.')
        o['notes'] = text(data.get('notes', ''), 2000)
        result['recordId'] = o['id']
    elif action == 'delivery.create':
        o = get(state, 'orders', data.get('orderId'))
        if o.get('workflowVersion') != 2 or not o.get('items'):
            raise ValueError('먼저 주문의 상세 품목을 입력해 주세요.')
        if o['status'] == '취소':
            raise ValueError('취소한 주문에는 납품을 등록할 수 없습니다.')
        dt = day(data.get('date'))
        if dt < o['date']:
            raise ValueError('납품일은 주문일 이후로 입력해 주세요.')
        requested = data.get('items')
        if not isinstance(requested, list) or len(requested) > 150:
            raise ValueError('납품 품목을 확인해 주세요.')
        rows = []; seen = set()
        for r in requested:
            key = r.get('orderLineId')
            line = next((i for i in o['items'] if i['id'] == key), None)
            if not line or key in seen:
                raise ValueError('주문 품목 연결을 확인해 주세요.')
            seen.add(key)
            qty = decimal(r.get('quantity'), zero=True)
            if not qty:
                continue
            prior = [i for d in state['deliveries'] if d['orderId'] == o['id'] and d['status'] != 'void' for i in d['items'] if i['orderLineId'] == key]
            delivered = sum((Decimal(str(i['quantity'])) for i in prior), Decimal(0))
            cumulative = delivered + qty
            if cumulative > Decimal(str(line['quantity'])):
                raise ValueError(f"{line['name']}: 남은 주문 수량을 초과했습니다.")
            net = won(cumulative * line['unitPrice'])
            vat = won(Decimal(net)*line['vatRate']/100)
            rows.append(dict(line, id=ident('del-line'), orderLineId=key, quantity=numeric(qty),
                             net=net-sum(i['net'] for i in prior), vat=vat-sum(i['vat'] for i in prior)))
        if not rows:
            raise ValueError('이번에 납품한 수량을 1개 이상 입력해 주세요.')
        quote = o.get('quoteSnapshot', {})
        d = total(dict(id=ident('delivery'), workflowVersion=2, number=number(state, 'deliveries', 'D', dt),
                       date=dt, accountId=o['accountId'], orderId=o['id'], orderNumber=o.get('number', o['id']),
                       title=o['title'], items=rows, notes=text(data.get('notes', ''), 2000), status='completed',
                       attention=text(data.get('attention', quote.get('attention', '')), 160),
                       paymentTerms=text(quote.get('paymentTerms', ''), 500),
                       createdAt=now, **snapshots(state, o['accountId'])))
        state['deliveries'].append(d); delivery_status(state, o)
        result['recordId'] = d['id']
    elif action == 'delivery.void':
        d = get(state, 'deliveries', data.get('id'))
        import statement_workflow
        if statement_workflow.sale_for(state, d['id']):
            raise ValueError('매출에 확정된 거래명세서입니다. 발급 기록 관리에서 잘못 등록한 매출을 먼저 정정해 주세요.')
        if any(t.get('deliveryId') == d['id'] and t['status'] != 'discarded' for t in state['taxDrafts']):
            raise ValueError('세금계산서에 연결된 납품입니다. 초안 폐기 또는 수정세금계산서 처리를 먼저 확인해 주세요.')
        active = [r for r in state['deliveries'] if r['orderId'] == d['orderId'] and r['status'] != 'void']
        if active and active[-1]['id'] != d['id']:
            raise ValueError('같은 주문의 최근 납품부터 취소해 주세요. 분할 납품 금액을 정확히 보존하기 위한 순서입니다.')
        reason = text(data.get('reason', ''), 200)
        if not reason or d['status'] == 'void':
            raise ValueError('취소 사유를 입력하거나 납품 상태를 확인해 주세요.')
        d.update(status='void', voidReason=reason, voidedAt=now)
        delivery_status(state, get(state, 'orders', d['orderId']))
        result['recordId'] = d['id']
    elif action == 'document.generate':
        kind = data.get('kind')
        if kind not in ('quote', 'delivery'):
            raise ValueError('문서 종류를 확인해 주세요.')
        record = get(state, 'quotes' if kind == 'quote' else 'deliveries', data.get('id'))
        if record.get('pdfDocumentId'):
            return {'recordId': record['id'], 'documentId': record['pdfDocumentId']}, None
        if record.get('status') == 'void':
            raise ValueError('취소된 납품은 PDF를 생성할 수 없습니다.')
        if record.get('workflowVersion') != 2:
            raise ValueError('이전 견적은 품목 수정으로 새 버전을 저장한 뒤 PDF를 만들어 주세요.')
        from pdf_documents import render_pdf
        file_content = render_pdf(kind, record, workspace == 'demo')
        key = ident('doc')
        label = '견적서' if kind == 'quote' else '거래명세서'
        suffix = f"-v{record['version']}" if kind == 'quote' else ''
        safe_number = re.sub(r'[^\w가-힣-]', '_', record['number'])[:100]
        doc = dict(id=key, generated=True, name=f'{label}-{safe_number}{suffix}.pdf', storedName=key+'.pdf',
                   sha256=hashlib.sha256(file_content).hexdigest(), date=now[:10], kind=label, status='등록 완료',
                   accountId=record['accountId'], orderId=record.get('orderId', ''),
                   quoteId=record['id'] if kind == 'quote' else '', deliveryId=record['id'] if kind == 'delivery' else '',
                   createdAt=now, snapshot=copy.deepcopy(record))
        state['documents'].append(doc)
        record['pdfDocumentId'] = key
        result.update(recordId=record['id'], documentId=key)
    else:
        raise ValueError('지원하지 않는 업무 요청입니다.')
    return result, file_content
