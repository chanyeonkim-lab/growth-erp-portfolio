"""Synthetic, date-relative showcase built through the actual ERP commands."""
import calendar
import copy
from datetime import date, timedelta
from functools import lru_cache
import ledger
import server
import workflow


def shift_month(d, offset):
    n = d.year * 12 + d.month - 1 + offset
    return date(n // 12, n % 12 + 1, 1)


@lru_cache(maxsize=4)
def _seed(as_of):
    today = date.fromisoformat(as_of)
    first = today.replace(day=1)
    s = server.empty_state()
    s['settings'].update(name='그로스 일렉트릭', representative='김데모',
        businessNumber='000-00-00000', address='가상시 데모구 그로스길 10',
        phone='010-0000-0000', email='hello@growth.example', bank='데모은행',
        bankAccount='DEMO-0000', bankHolder='그로스 일렉트릭',
        businessType='제조,도소매', businessItem='전기콘트롤박스')
    def cmd(action, **data):
        result, _ = workflow.apply(s, dict(action=action, data=data), 'demo', actor='demo')
        return result.get('recordId')
    cmd('erp.initialize', confirmed=True)
    names = ['루미나 캠퍼스', '스튜디오 폼', '노바 일렉트로', '오르빗 머신',
             '아크 모빌리티', '솔라 데모', '테라 파츠', '이온 워크스',
             '볼트 서플라이', '모노 인더스트리', '에코 그리드', '오브제 랩']
    accounts = []
    for i, name in enumerate(names):
        accounts.append(cmd('erp.account.save', name=name, kind='고객사' if i < 8 else '매입처',
            contact=['한데모', '윤샘플', '정가상', '이예시'][i % 4],
            phone='02-000-0000', phone2='010-0000-0000', email=f'team{i+1}@example.com',
            businessNumber='000-00-00000', address=f'가상시 데모구 샘플로 {i+1}',
            paymentTermsDays=30, notes='포트폴리오를 위해 생성한 가상 거래처입니다.'))
    definitions = [
        ('스틸 분전함', '600 × 800 · IP54', 480000, 295000),
        ('스테인리스 제어함', 'SUS304 · 500 × 600', 690000, 420000),
        ('산업용 차단기', '4P · 100A', 85000, 49000),
        ('태양광 접속반', 'DC 1000V · 4채널', 1250000, 745000),
        ('스마트 제어 모듈', 'IoT · RS485', 320000, 184000),
        ('모터 보호 계전기', '3상 · 디지털', 185000, 104000),
        ('방수 전기함', 'FRP · IP65', 235000, 138000),
        ('배전반 조립 공임', '표준 조립 · 1식', 420000, 230000),
        ('전원 공급 장치', '24V · 10A', 96000, 54000),
        ('동 부스바', '30 × 5 · 1M', 78000, 44000),
        ('산업용 타이머', '디지털 · 220V', 54000, 29000),
        ('케이블 덕트', '60 × 60 · 2M', 18000, 9500),
    ]
    products = []
    beginning = shift_month(first, -5).isoformat()
    for i, (name, spec, sale, cost) in enumerate(definitions):
        pid = cmd('erp.product.save', code=f'GR-{i+1:03}', name=name, spec=spec,
            unit='식' if i == 7 else 'EA', salePrice=sale, purchasePrice=cost,
            supplierId=accounts[8+i%4], trackStock=i != 7, minStock=12,
            notes='가상 품목 · 가격은 데모용 임의 금액입니다.')
        products.append(pid)
        if i != 7:
            qty = 180 if i < 5 else (8 if i == 10 else 75)
            cmd('erp.stock.opening', productId=pid, date=beginning, quantity=qty, value=qty*cost)
    titles = ['캠퍼스 에너지 리뉴얼', '성수 스튜디오 전기 설비', '스마트팩토리 제어반',
              '생산라인 배전함 증설', 'EV 충전소 전력 설비', '루프탑 태양광 접속반']
    for m in range(-5, 1):
        dt = shift_month(first, m)
        s['settings']['taxReserves'][dt.strftime('%Y-%m')] = 350000 + (m+5)*45000
        for i in range(6):
            # Increasing quantities make the synthetic growth story visible.
            p = i % 5
            qty = 3 + (m+5)*2 + i
            items = [dict(productId=products[p], name=definitions[p][0], quantity=qty,
                unit='EA', unitPrice=definitions[p][2], vatRate=10),
                dict(productId=products[7], name=definitions[7][0], quantity=1,
                    unit='식', unitPrice=definitions[7][2], vatRate=10)]
            qid = cmd('quote.save', accountId=accounts[i], title=titles[i],
                date=dt.isoformat(), items=items, paymentTerms='착수금 30% · 납품 후 잔금',
                deliveryTerms='발주 후 2주 이내 · 가상 프로젝트')
            oid = cmd('quote.convert', quoteId=qid, date=dt.isoformat(),
                dueDate=max(first,today+timedelta(days=i-2)).isoformat() if m == 0 else
                    (dt+timedelta(days=8)).isoformat(), orderType='DIS' if i%2 else 'BOX')
            vid = cmd('erp.order.voucher', orderId=oid, date=dt.isoformat(),
                dueDate=max(first,today+timedelta(days=i-2)).isoformat() if m == 0 else
                    (dt+timedelta(days=20)).isoformat())
            v = ledger.get(s, 'vouchers', vid)
            if m < 0 or i != 5:
                cmd('erp.voucher.confirm', id=vid, date=dt.isoformat())
            if m < 0 or i < 4:
                cmd('erp.fulfill', id=vid, date=dt.isoformat(), items=[dict(lineId=r['id'],
                    quantity=r['quantity'] if m < 0 or i < 3 else r['quantity']/2) for r in v['items']])
            if m < 0 or i < 3:
                amount = v['amount'] if m < 0 or i < 2 else round(v['amount']*.3)
                cmd('erp.payment.save', direction='in', accountId=accounts[i], date=dt.isoformat(),
                    amount=amount, memo='가상 결제 · 완납' if amount == v['amount'] else '가상 착수금 30%',
                    voucherId=vid)
            if m == 0 and i in (4, 5):
                ledger.get(s, 'orders', oid)['status'] = '제작 중'
        for i, (title, amount) in enumerate([('작업실 임차료', 950000), ('물류·배송비', 280000), ('통신·클라우드', 120000)]):
            vid = cmd('erp.voucher.save', kind='expense', title=title, category=['월세','배송비','통신비'][i],
                date=dt.isoformat(), items=[dict(name=title, quantity=1, unitPrice=amount, vatRate=10)])
            cmd('erp.voucher.confirm', id=vid, date=dt.isoformat())
            v=ledger.get(s, 'vouchers', vid)
            cmd('erp.payment.save', direction='out', accountId='', date=dt.isoformat(),
                amount=v['amount'], voucherId=vid)
    for i in range(4):
        cmd('quote.save', accountId=accounts[i+2], title=['물류센터 제어반 제안', '리테일 조명 분전함',
            '전기차 충전 인프라 2차', '연구동 전력 모니터링'][i], date=today.isoformat(),
            items=[dict(productId=products[i], name=definitions[i][0], quantity=8+i,
                unit='EA', unitPrice=definitions[i][2], vatRate=10)])
    # A genuine purchase / partial receipt demonstrates independent stock and payment.
    vid=cmd('erp.voucher.save', kind='purchase', accountId=accounts[8], title='분전함 자재 정기 발주',
        date=first.isoformat(), dueDate=(today+timedelta(days=7)).isoformat(),
        items=[dict(productId=products[0], name=definitions[0][0], quantity=30,
            unit='EA', unitPrice=definitions[0][3], vatRate=10)])
    cmd('erp.voucher.confirm', id=vid, date=first.isoformat())
    v=ledger.get(s,'vouchers',vid)
    cmd('erp.fulfill', id=vid, date=first.isoformat(), items=[dict(lineId=v['items'][0]['id'], quantity=20)])
    for i, title in enumerate(['루미나 캠퍼스 · 최종 도면 확인', '노바 일렉트로 · 납품 일정 조율',
        '스튜디오 폼 · 착수금 확인', '솔라 데모 · 접속반 사양 검토', '월간 거래 증빙 정리']):
        cmd('erp.task.save', title=title, accountId=accounts[i], date=(today+timedelta(days=i-1)).isoformat(),
            dueDate=(today+timedelta(days=i-1)).isoformat(), status='in_progress' if i<2 else 'not_started',
            notes='직접 수정하거나 완료해 보세요. 초기화하면 원래 데모로 돌아옵니다.')
    s['demoMeta'] = dict(asOf=as_of, version=1, synthetic=True)
    server.validate_state(s)
    return s


def demo_state(as_of=None):
    return copy.deepcopy(_seed(as_of or date.today().isoformat()))
