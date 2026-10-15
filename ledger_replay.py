"""Revalue stock and derive trade postings in event-date order after late entry.

Source vouchers, fulfillment records and audit events remain intact. Journal rows
are projections of those events; payment and legacy postings are preserved.
"""
from decimal import Decimal


def rebuild(s):
    import ledger as L
    # Insertion order breaks ties on the same date, preserving physical sequence.
    balances, originals, rates, provisional = {}, {}, {}, {}
    for m in sorted(s['stockMoves'], key=lambda x: x['date']):
        qty = Decimal(str(m['quantity']))
        pid = m['productId']
        b = balances.setdefault(pid, [Decimal(0), 0])
        pending = provisional.setdefault(pid, set())
        source = m['sourceId']
        key = (source, pid, m.get('lineId', ''))
        m.pop('costProvisional', None)
        if source.startswith('reverse-'):
            original_key = (source[8:], pid, m.get('lineId', ''))
            original = originals.get(original_key)
            L.need(original is not None, '정정할 재고 원본을 찾을 수 없습니다.')
            m['value'] = -original['value']
            if original.get('costProvisional'):
                m['costProvisional'] = True
            pending.discard(original_key)
        elif qty < 0:
            reliable = b[0] > 0 and b[1] >= 0 and not pending
            unit = Decimal(b[1]) / b[0] if reliable else rates.get(pid, Decimal(m.get('fallbackCostUnit', 0)))
            if reliable:
                rates[pid] = unit
            m['value'] = -b[1] if reliable and -qty == b[0] else -L.won(unit * -qty)
            if pending or b[0] < -qty or b[1] < 0:
                m['costProvisional'] = True
                pending.add(key)
        elif qty > 0:
            # Only recorded receipts set a new fallback rate. Never divide a
            # negative/zero advisory balance or read today's mutable price book.
            rates[pid] = Decimal(m['value']) / qty
        b[0] += qty
        b[1] += m['value']
        if (b[0] < 0 or b[1] < 0 or (b[0] == 0 and b[1] != 0)) and not pending:
            m['costProvisional'] = True
            pending.add(key)
        # Negative quantity/value is a reference-data gap, not a delivery gate.
        originals[key] = m

    fs = {f['id']: f for f in s['fulfillments']}
    for f in fs.values():
        v = L.get(s, 'vouchers', f['voucherId'])
        for r in f['items']:
            moves = [m for m in s['stockMoves'] if m['sourceId'] == f['id'] and m.get('lineId') == r['voucherLineId']]
            if moves:
                r['cost'] = sum(abs(m['value']) for m in moves)
                r['costProvisional'] = any(m.get('costProvisional') for m in moves)
            r.setdefault('tracked', bool(moves))
        f['cost'] = sum(r['cost'] for r in f['items'])
        f['costProvisional'] = any(r.get('costProvisional') for r in f['items'])

    vouchers = [v for v in s['vouchers'] if not v.get('legacy')]
    sources = {v['id'] for v in vouchers} | set(fs)
    managed = sources | {'reverse-' + source for source in sources}
    # A backdated receipt can change later consumptions as well as sales COGS.
    uses = [m for m in s['stockMoves'] if m['sourceId'].startswith('usage-')]
    managed |= {m['sourceId'] for m in uses}
    old_entries = s['journalEntries']
    s['journalEntries'] = [j for j in old_entries if j['sourceId'] not in managed]

    def post(source, dt, rows, memo, suffix):
        L.entry(s, source, dt, rows, memo, key='j2-' + source + '-' + suffix)

    for m in uses:
        memo = next((j['memo'] for j in old_entries if j['sourceId'] == m['sourceId']), '재고 사용')
        post(m['sourceId'], m['date'], [('expense', -m['value'], 0), ('inventory', 0, -m['value'])], memo, 'use')

    for v in vouchers:
        events = []
        confirmed_date = v.get('confirmedDate')
        if confirmed_date:
            events.append((confirmed_date, 0, 'confirm', v))
        for f in fs.values():
            if f['voucherId'] != v['id']:
                continue
            events.append((f['date'], 1, 'fulfill', f))
            if f['status'] == 'void':
                events.append((f['voidDate'], 2, 'undo', f))
        if v.get('voidDate'):
            events.append((v['voidDate'], 3, 'void', v))
        confirmed, net_supplied, cost_supplied = False, 0, 0
        ded = v['vat'] if v.get('vatDeductible') else 0

        def confirmation():
            if v['kind'] == 'sale':
                return [('ar', v['amount'], 0), ('unbilledSales', 0, net_supplied), ('deferredSales', 0, v['net']-net_supplied), ('outputVat', 0, v['vat'])]
            if v['kind'] == 'purchase':
                return [('accruedPurchase', cost_supplied, 0), ('unreceived', v['amount']-ded-cost_supplied, 0), ('inputVat', ded, 0), ('ap', 0, v['amount'])]
            return [('drawings' if v['kind'] == 'withdrawal' else 'expense', v['amount']-ded, 0), ('inputVat', ded, 0), ('ap', 0, v['amount'])]

        for dt, _, event, obj in sorted(events, key=lambda x: (x[0], x[1])):
            if event == 'confirm':
                post(v['id'], dt, confirmation(), L.LABELS[v['kind']]+' 전표 확정', event)
                confirmed = True
            elif event == 'void':
                L.need(net_supplied == 0 and cost_supplied == 0, '전표 취소일은 입출고 취소일 이후로 입력해 주세요.')
                if confirmed:
                    post('reverse-'+v['id'], dt, [(a, cr, dr) for a, dr, cr in confirmation()], v.get('voidReason', '전표 정정'), event)
            else:
                f, rows = obj, []
                undo = event == 'undo'
                if v['kind'] == 'sale':
                    rows.extend([('deferredSales' if confirmed else 'unbilledSales', f['net'], 0), ('revenue', 0, f['net'])])
                    for r in f['items']:
                        if r['tracked']:
                            rows.extend([('cogs', r['cost'], 0), ('inventory', 0, r['cost'])])
                else:
                    for r in f['items']:
                        rows.extend([('inventory' if r['tracked'] else 'expense', r['cost'], 0), ('unreceived' if confirmed else 'accruedPurchase', 0, r['cost'])])
                if undo:
                    rows = [(a, cr, dr) for a, dr, cr in rows]
                post(('reverse-' if undo else '')+f['id'], dt, rows, f.get('voidReason', '입출고 정정') if undo else '실제 납품·입고', event)
                net_supplied += (-1 if undo else 1) * f['net']
                cost_supplied += (-1 if undo else 1) * f['cost']
