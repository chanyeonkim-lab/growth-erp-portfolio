"""Unified trade ledger with reversible manual corrections and independent fulfillment and cash.
Adapted to Growth's locked workspace transactions from dadeum-erp's accounting model.
Money is integer KRW, quantities Decimal(3); VAT and cash never become revenue twice.
"""
import copy, hashlib, json, uuid
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

KINDS=('products','vouchers','payments','fulfillments','stockMoves','journalEntries','erpAudit')
ACCOUNTS={'cash':'보통예금·현금','ar':'외상매출금','ap':'외상매입금','inventory':'재고자산',
 'inputVat':'부가세대급금','outputVat':'부가세예수금','revenue':'매출','cogs':'매출원가','expense':'경비',
 'deferredSales':'선청구 매출','unbilledSales':'미청구 매출','unreceived':'미입고 매입',
 'accruedPurchase':'미청구 매입','advanceIn':'선수금','advanceOut':'선급금','equity':'기초자본','drawings':'사업주 인출'}
LABELS={'sale':'매출','purchase':'매입','expense':'경비','withdrawal':'개인 인출'}

def uid(prefix):return prefix+'-'+uuid.uuid4().hex

def now():return datetime.now(ZoneInfo('Asia/Seoul')).isoformat(timespec='seconds')

def need(ok,message):
 if not ok:raise ValueError(message)

def text(v='',limit=200):
 need(isinstance(v,str) and len(v)<=limit,f'문자 입력은 {limit}자 이내로 작성해 주세요.')
 return v.strip()

def day(v):
 try:need(isinstance(v,str) and date.fromisoformat(v).isoformat()==v,'날짜를 확인해 주세요.')
 except (ValueError,TypeError):raise ValueError('날짜를 확인해 주세요.')
 return v

def dec(v,positive=False):
 try:
  need(not isinstance(v,bool),'숫자를 확인해 주세요.');n=Decimal(str(v))
  need(n.is_finite() and n>=0 and (not positive or n>0) and n<=10**9 and n==n.quantize(Decimal('.001')),'수량은 소수 셋째 자리까지의 유효한 양수로 입력해 주세요.')
  return n
 except Exception:raise ValueError('수량 또는 숫자를 확인해 주세요.')

def won(v):return int(Decimal(str(v)).quantize(Decimal('1'),rounding=ROUND_HALF_UP))

def money(v,positive=False):
 need(type(v) is int and (v>0 if positive else v>=0) and v<=10**13,'금액은 원 단위 정수로 입력해 주세요.')
 return v

def numeric(v):return int(v) if v==int(v) else float(v)

def get(s,kind,key):
 r=next((r for r in s[kind] if r['id']==key),None);need(r is not None,'연결된 기록을 찾을 수 없습니다.');return r

def audit(s,action,entity,detail=''):
 s['erpAudit'].append(dict(id=uid('audit'),at=now(),action=action,entityId=entity,detail=detail))

def entry(s,source,dt,rows,memo='',key=None):
 rows=[dict(account=a,debit=max(0,won(dr-cr)),credit=max(0,won(cr-dr))) for a,dr,cr in rows if dr!=cr]
 need(all(r['account'] in ACCOUNTS for r in rows),'계정 과목을 확인해 주세요.')
 need(sum(r['debit'] for r in rows)==sum(r['credit'] for r in rows),'차변과 대변 합계가 다릅니다.')
 if not rows:return
 s['journalEntries'].append(dict(id=key or uid('journal'),sourceId=source,date=dt,memo=memo,rows=rows))

def terms(s,account,dt):
 a=next((a for a in s['accounts'] if a['id']==account),{})
 return (date.fromisoformat(dt)+timedelta(days=int(a.get('paymentTermsDays',30)))).isoformat()

def totals(rows):
 net=sum(r['net'] for r in rows);vat=sum(r['vat'] for r in rows)
 return dict(net=net,vat=vat,amount=net+vat)

def stock(s,pid,cutoff='9999-12-31'):
 moves=[m for m in s['stockMoves'] if m['productId']==pid and m['date']<=cutoff]
 return dict(quantity=numeric(sum((Decimal(str(m['quantity'])) for m in moves),Decimal(0))),value=sum(m['value'] for m in moves))

def provisional_stock(s,pid):
 b=stock(s,pid)
 if b['quantity']<0 or b['value']<0 or (b['quantity']==0 and b['value']!=0):return True
 reversals={m['sourceId'][8:] for m in s['stockMoves'] if m['sourceId'].startswith('reverse-')}
 return any(m.get('costProvisional') and m['productId']==pid and not m['sourceId'].startswith('reverse-') and m['sourceId'] not in reversals for m in s['stockMoves'])

def move(s,pid,qty,value,dt,source,lineid=''):
 p=get(s,'products',pid);need(p['trackStock'],'서비스 품목은 재고를 이동하지 않습니다.')
 # Inventory quantities are advisory; dated replay derives cost projections.
 m=dict(id=uid('stock'),productId=pid,quantity=numeric(qty),value=value,date=dt,sourceId=source,lineId=lineid)
 s['stockMoves'].append(m);return m

def item_rows(s,rows,kind='sale'):
 import workflow
 result=workflow.items(rows)
 for out,raw in zip(result,rows):
  pid=text(raw.get('productId',''),160);out['productId']=pid
  out['orderLineId']=text(raw.get('orderLineId',''),160)
  out['costUnit']=money(raw.get('costUnit') or 0)
  out['costKnown']=raw.get('costKnown',out['costUnit']>0) is True
  if pid:
   p=get(s,'products',pid);need(p.get('active',True),'비활성 품목입니다.')
   out.update(name=p['name'],spec=p.get('spec',''),unit=p['unit'])
   b=stock(s,pid)
   out['costUnit']=won(Decimal(b['value'])/Decimal(str(b['quantity']))) if b['quantity']>0 and b['value']>=0 and not provisional_stock(s,pid) else p.get('purchasePrice',0)
   out['costKnown']=(b['quantity']>0 and b['value']>=0 and not provisional_stock(s,pid)) or p.get('purchasePrice',0)>0
  out['estimatedCost']=won(Decimal(str(out['quantity']))*out['costUnit'])
 return result

def paid(s,voucher,cutoff='9999-12-31'):
 return sum(a['amount'] for p in s['payments'] if (p['status']=='confirmed' or p.get('voidDate','')>cutoff) and p['date']<=cutoff for a in p.get('allocations',[]) if a['voucherId']==voucher['id'] and a.get('date',p['date'])<=cutoff)

def balance(s,v,cutoff='9999-12-31'):
 credits=sum(c['amount'] for x in s['vouchers'] if x['status']=='confirmed' and x['date']<=cutoff for c in x.get('credits',[]) if c['voucherId']==v['id'])
 return max(0,v['amount']-credits-paid(s,v,cutoff))

def fulfilled(s,v,lineid):
 return sum((Decimal(str(r['quantity'])) for f in s['fulfillments'] if f['voucherId']==v['id'] and f['status']=='completed' for r in f['items'] if r['voucherLineId']==lineid),Decimal(0))

def realized(s,v):
 fs=[f for f in s['fulfillments'] if f['voucherId']==v['id'] and f['status']=='completed']
 return sum(f['net'] for f in fs),sum(f['cost'] for f in fs)

def normalized(s):
 for k in KINDS:s.setdefault(k,[])
 return s

def remove_product(s,p):
 moves=[m for m in s['stockMoves'] if m['productId']==p['id']]
 sources={m['sourceId'] for m in moves}
 s['stockMoves']=[m for m in s['stockMoves'] if m['productId']!=p['id']]
 s['journalEntries']=[j for j in s['journalEntries'] if not (j.get('sourceId') in sources and (j.get('sourceId','').startswith('opening-') or j.get('sourceId','').startswith('usage-')))]
 # Quotations and transaction lines keep their own name/specification/price snapshots.
 # Removing the catalog link therefore preserves historical documents without an orphan ID.
 for kind in ('quotes','orders','vouchers'):
  for record in s.get(kind,[]):
   for row in record.get('items',[]):
    if row.get('productId')==p['id']:row['productId']=''
 for fulfillment in s['fulfillments']:
  for row in fulfillment.get('items',[]):
   if row.get('productId')==p['id']:row['productId']='';row['tracked']=False;row['cost']=0;row['costProvisional']=False
 s['products']=[r for r in s['products'] if r['id']!=p['id']]
 audit(s,'품목·재고 이력 삭제',p['id'],p['code']+' · '+p['name'])
 return p['id']

def migrate(s):
 normalized(s)
 if s.get('ledgerVersion')==2:return
 # Existing arrays stay intact. No historical product stock or missing line cost is invented.
 for i in s['invoices']:
  v=dict(id='legacy-sale-'+i['id'],number=i.get('number') or i['id'],kind='sale',date=i['date'],dueDate=i.get('dueDate') or terms(s,i['accountId'],i['date']),confirmationDueDate=i['date'],accountId=i['accountId'],orderId=i.get('orderId',''),title=i.get('description','기존 매출'),status='void' if i.get('status')=='void' else 'confirmed',net=i['net'],vat=i['vat'],amount=i['net']+i['vat'],items=[],legacy=True,legacyId=i['id'],recognition='legacy',approvalNumber=i.get('approvalNumber',''),notes='이전 장부에서 보존한 매출. 실제 재고 수량은 별도로 등록합니다.',confirmedAt=i['date'])
  v['credits']=[dict(voucherId='legacy-sale-'+c['invoiceId'],amount=c['amount']) for c in i.get('creditApplications',[])]
  if i.get('creditForId') and not v['credits']:v['credits']=[dict(voucherId='legacy-sale-'+i['creditForId'],amount=-v['amount'])]
  v['sourceDeliveryId']=i.get('sourceDeliveryId','')
  v['sourceDocumentId']=i.get('documentId','')
  delivery=next((d for d in s.get('deliveries',[]) if d['id']==v['sourceDeliveryId']),None)
  if delivery and totals(delivery.get('items',[]))=={k:v[k] for k in ('net','vat','amount')}:
   v['items']=copy.deepcopy(delivery['items'])
   for key in ('company','customer'):v[key]=copy.deepcopy(delivery[key])
  if i.get('sourceDeliveryId'):
   v['legacyReviewNeeded']=not i.get('hometaxTask',{}).get('done',bool(i.get('manualTax')))
   if isinstance(i.get('manualTax'),dict):v['approvalNumber']=i['manualTax'].get('approval','') or v['approvalNumber']
  s['vouchers'].append(v)
  if v['status']=='confirmed':entry(s,v['id'],v['date'],[('ar',v['amount'],0),('revenue',0,v['net']),('outputVat',0,v['vat'])],'기존 매출 이관',key='j-'+v['id'])
 for e in s['expenses']:
  kind='withdrawal' if e['category']=='개인 인출' else 'purchase' if e['category'] in ('자재비','외주비') else 'expense'
  v=dict(id='legacy-cost-'+e['id'],number='OLD-'+e['id'],kind=kind,date=e['date'],dueDate=e.get('paidDate') or terms(s,e.get('accountId',''),e['date']),confirmationDueDate=e['date'],accountId=e.get('accountId',''),orderId=e.get('orderId',''),title=e.get('description',e['category']),category=e['category'],status='confirmed',net=e['net'],vat=e['vat'],amount=e['net']+e['vat'],vatDeductible=e.get('vatDeductible',False),items=[],legacy=True,legacyId=e['id'],recognition='legacy',approvalNumber='',notes='이전 비용 합계 보존. 기초재고·과거 매입 품목은 자동 추정하지 않습니다.',confirmedAt=e['date'])
  s['vouchers'].append(v);ded=v['vat'] if v['vatDeductible'] else 0
  entry(s,v['id'],v['date'],[('drawings' if kind=='withdrawal' else 'expense',v['amount']-ded,0),('inputVat',ded,0),('ap',0,v['amount'])],'기존 비용 이관',key='j-'+v['id'])
  if e.get('paidDate'):
   p=dict(id='legacy-pay-'+e['id'],direction='out',accountId=v['accountId'],date=e['paidDate'],amount=v['amount'],status='confirmed',memo='이전 지급 이관',allocations=[dict(voucherId=v['id'],amount=v['amount'],date=max(e['date'],e['paidDate']))],legacy=True)
   s['payments'].append(p)
   entry(s,p['id'],p['date'],[('advanceOut',p['amount'],0),('cash',0,p['amount'])],'기존 지급',key='j-'+p['id'])
   entry(s,p['id'],max(p['date'],v['date']),[('ap',p['amount'],0),('advanceOut',0,p['amount'])],'지급 연결',key='ja-'+p['id'])
 for r in s['receipts']:
  allocs=[dict(voucherId='legacy-sale-'+a['invoiceId'],amount=a['amount'],date=a['date']) for a in s['allocations'] if a['receiptId']==r['id']]
  p=dict(id='legacy-receipt-'+r['id'],direction='in',accountId=r['accountId'],orderId=r.get('orderId',''),date=r['date'],amount=r['amount'],status='confirmed',memo=r.get('memo',''),allocations=allocs,legacy=True)
  s['payments'].append(p)
  entry(s,p['id'],p['date'],[('cash',p['amount'],0),('advanceIn',0,p['amount'])],'기존 수금',key='j-'+p['id'])
  for ix,a in enumerate(allocs):entry(s,p['id'],a['date'],[('advanceIn',a['amount'],0),('ar',0,a['amount'])],'수금 연결',key='ja-'+p['id']+'-'+str(ix))
 s['ledgerVersion']=2
 s['migrationInfo']=dict(version=2,legacyInvoices=len(s['invoices']),legacyExpenses=len(s['expenses']),legacyReceipts=len(s['receipts']),inventoryOpeningRequired=True)

def number(s,kind,prefix,dt):
 base=prefix+'-'+dt.replace('-','')+'-';n=1;used={r.get('number') for r in s[kind]}
 while base+f'{n:03}' in used:n+=1
 return base+f'{n:03}'

def validate(s):
 if s.get('ledgerVersion')!=2:return
 normalized(s)
 for k in KINDS:
  need(isinstance(s[k],list) and len(s[k])<=20000,'레코드 수를 확인해 주세요.')
  need(len({r['id'] for r in s[k]})==len(s[k]),'중복 레코드 ID입니다.')
 for v in s['vouchers']:
  need(v['kind'] in LABELS and v['status'] in ('draft','confirmed','void'),'전표 상태를 확인해 주세요.')
  if v.get('accountId'):get(s,'accounts',v['accountId'])
  day(v['date']);day(v['dueDate']);need(v['amount']==v['net']+v['vat'],'전표 합계를 확인해 주세요.')
  if not v.get('legacy'):
   need(totals(v['items'])=={k:v[k] for k in ('net','vat','amount')},'품목 합계를 확인해 주세요.')
   for r in v['items']:
    dec(r['quantity'],True);money(r['unitPrice']);money(r['net']);money(r['vat'])
    if r.get('productId'):get(s,'products',r['productId'])
 for p in s['payments']:
  money(p['amount'],True);day(p['date']);need(p['direction'] in ('in','out'),'수금·지급 구분을 확인해 주세요.')
  need(sum(a['amount'] for a in p.get('allocations',[]))<=p['amount'],'결제액을 초과한 연결입니다.')
  for a in p.get('allocations',[]):
   v=get(s,'vouchers',a['voucherId']);money(a['amount'],True)
   need(p['accountId']==v['accountId'] and (p['direction']=='in')==(v['kind']=='sale'),'다른 거래처 또는 다른 방향의 결제를 연결할 수 없습니다.')
 for j in s['journalEntries']:
  need(sum(r['debit'] for r in j['rows'])==sum(r['credit'] for r in j['rows']),'회계 분개 불균형입니다.')
  for r in j['rows']:money(r['debit']);money(r['credit']);need(r['account'] in ACCOUNTS,'계정 오류입니다.')
 # Signed stock balances are intentional when receipt records are incomplete.
 for m in s['stockMoves']:
  get(s,'products',m['productId']);day(m['date'])
  dec(abs(Decimal(str(m['quantity']))),True);money(abs(m['value']))

def guard(old,new):
 if old.get('ledgerVersion')!=2:
  need(new.get('ledgerVersion')!=2,'장부 전환은 전용 기능을 사용해 주세요.');return
 need(new.get('ledgerVersion')==2,'장부 버전을 되돌릴 수 없습니다.')
 for k in KINDS+('invoices','expenses','receipts','allocations','migrationInfo','taxRecords','taxDrafts','taxJobs','costAllocations'):
  need(old.get(k)==new.get(k),'거래·결제·재고는 해당 업무 화면에서 수정해 주세요.')

def confirmation_rows(s,v):
 n,c=realized(s,v);ded=v['vat'] if v.get('vatDeductible') else 0
 if v['kind']=='sale':rows=[('ar',v['amount'],0),('unbilledSales',0,n),('deferredSales',0,v['net']-n),('outputVat',0,v['vat'])]
 elif v['kind']=='purchase':rows=[('accruedPurchase',c,0),('unreceived',v['amount']-ded-c,0),('inputVat',ded,0),('ap',0,v['amount'])]
 else:rows=[('drawings' if v['kind']=='withdrawal' else 'expense',v['amount']-ded,0),('inputVat',ded,0),('ap',0,v['amount'])]
 return rows

def confirm(s,v,dt):
 need(v['status']!='void','취소된 전표입니다.')
 if v['status']=='confirmed':return
 need(dt>=v['date'],'확정일은 거래일보다 빠를 수 없습니다.')
 v.update(status='confirmed',confirmedAt=now(),confirmedDate=dt);audit(s,'전표 확정',v['id'])
 from ledger_replay import rebuild
 rebuild(s)

def fulfill(s,v,rows,dt):
 need(v['status']!='void' and not v.get('legacy'),'기존 전표는 실제 수량을 확인해 새 전표 또는 기초재고로 기록해 주세요.')
 need(v['kind'] in ('sale','purchase'),'매출·매입 전표만 입출고할 수 있습니다.')
 need(isinstance(rows,list) and rows and len({r.get('lineId') for r in rows})==len(rows),'입출고 품목을 확인해 주세요.')
 f=dict(id=uid('fulfillment'),voucherId=v['id'],orderId=v.get('orderId',''),date=dt,status='completed',confirmedAtCreation=v['status']=='confirmed',items=[],net=0,vat=0,cost=0)
 jr=[]
 for raw in rows:
  line=next((r for r in v['items'] if r['id']==raw['lineId']),None);need(line is not None,'전표 품목을 확인해 주세요.')
  qty=dec(raw['quantity'],True);previous=fulfilled(s,v,line['id']);totalqty=Decimal(str(line['quantity']))
  need(previous+qty<=totalqty,'주문 또는 매입 수량을 초과할 수 없습니다.')
  net=won(Decimal(line['net'])*(previous+qty)/totalqty)-won(Decimal(line['net'])*previous/totalqty)
  vat=won(Decimal(line['vat'])*(previous+qty)/totalqty)-won(Decimal(line['vat'])*previous/totalqty)
  cost=0;pid=line.get('productId');p=get(s,'products',pid) if pid else None
  tracked=p and p['trackStock']
  if v['kind']=='sale':
   if tracked:
    # Replay prices the movement. Freeze the fallback so later catalog edits
    # cannot change an already recorded provisional delivery cost.
    m=move(s,pid,-qty,0,dt,f['id'],line['id'])
    m['fallbackCostUnit']=line.get('costUnit',0)
   jr.extend([('deferredSales' if v['status']=='confirmed' else 'unbilledSales',net,0),('revenue',0,net)])
  else:
   cost=net+(0 if v.get('vatDeductible') else vat)
   if tracked:move(s,pid,qty,cost,dt,f['id'],line['id'])
   jr.extend([('inventory' if tracked else 'expense',cost,0),('unreceived' if v['status']=='confirmed' else 'accruedPurchase',0,cost)])
   if p:p['purchasePrice']=line['unitPrice']
  f['items'].append(dict(voucherLineId=line['id'],productId=pid or '',quantity=numeric(qty),net=net,vat=vat,cost=cost,tracked=bool(tracked)))
  f['net']+=net;f['vat']+=vat;f['cost']+=cost
 s['fulfillments'].append(f);entry(s,f['id'],dt,jr,'실제 납품·작업 완료' if v['kind']=='sale' else '실제 입고')
 from ledger_replay import rebuild
 rebuild(s)
 if v.get('orderId') and v['kind']=='sale':
  o=get(s,'orders',v['orderId']);o['status']='납품 완료' if all(fulfilled(s,v,r['id'])==Decimal(str(r['quantity'])) for r in v['items']) else '일부 납품'
 audit(s,'납품 완료 기록' if v['kind']=='sale' else '입고 기록',v['id'],f['id']);return f

def reverse_entries(s,source,dt,reason):
 for j in list(s['journalEntries']):
  if j['sourceId']==source:entry(s,'reverse-'+source,dt,[(r['account'],r['credit'],r['debit']) for r in j['rows']],reason)

def repost_payment(s,p):
 """Rebuild a manually entered cash record after an edit or allocation change."""
 sources={p['id'],'reverse-'+p['id']}
 s['journalEntries']=[j for j in s.get('journalEntries',[]) if j.get('sourceId') not in sources]
 if p.get('status')!='confirmed':return
 amount=p['amount'];direction=p['direction']
 entry(s,p['id'],p['date'],[('cash',amount,0),('advanceIn',0,amount)] if direction=='in' else [('advanceOut',amount,0),('cash',0,amount)],'수금' if direction=='in' else '지급')
 for allocation in p.get('allocations',[]):
  amount=allocation['amount'];dt=allocation.get('date') or p['date']
  entry(s,p['id'],dt,[('advanceIn',amount,0),('ar',0,amount)] if direction=='in' else [('ap',amount,0),('advanceOut',0,amount)],'결제 연결')

def remove_fulfillment(s,f):
 sources={f['id'],'reverse-'+f['id']}
 s['fulfillments']=[record for record in s.get('fulfillments',[]) if record['id']!=f['id']]
 s['stockMoves']=[move for move in s.get('stockMoves',[]) if move.get('sourceId') not in sources]
 s['journalEntries']=[j for j in s.get('journalEntries',[]) if j.get('sourceId') not in sources]

def remove_payment(s,p):
 sources={p['id'],'reverse-'+p['id']}
 if p.get('legacy') and p['id'].startswith('legacy-receipt-'):
  legacy_id=p['id'][len('legacy-receipt-'):]
  s['allocations']=[a for a in s.get('allocations',[]) if a.get('receiptId')!=legacy_id]
  s['receipts']=[r for r in s.get('receipts',[]) if r['id']!=legacy_id]
 elif p.get('legacy') and p['id'].startswith('legacy-pay-'):
  legacy_id=p['id'][len('legacy-pay-'):]
  for expense in s.get('expenses',[]):
   if expense['id']==legacy_id:expense.pop('paidDate',None)
 s['payments']=[record for record in s.get('payments',[]) if record['id']!=p['id']]
 s['journalEntries']=[j for j in s.get('journalEntries',[]) if j.get('sourceId') not in sources]

def refresh_order_status(s,order_id):
 order=next((o for o in s.get('orders',[]) if o['id']==order_id),None)
 if not order:return
 vouchers=[v for v in s.get('vouchers',[]) if v.get('orderId')==order_id and v.get('kind')=='sale' and v.get('status')!='void']
 if not vouchers:order['status']='주문 접수';return
 v=vouchers[-1]
 order['status']='납품 완료' if v.get('items') and all(fulfilled(s,v,row['id'])==Decimal(str(row['quantity'])) for row in v['items']) else '일부 납품' if any(fulfilled(s,v,row['id']) for row in v.get('items',[])) else '주문 접수'

def apply(s,action,d,workspace):
 # Keep every failed command atomic for local and cloud callers.
 before=copy.deepcopy(s)
 try:return _apply(s,action,d,workspace)
 except Exception:
  s.clear();s.update(before)
  raise

def _apply(s,action,d,workspace):
 import workflow
 if action=='erp.initialize':
  need(s.get('ledgerVersion')==2 or d.get('confirmed') is True,'기존 자료를 보존하는 장부 구조 전환을 먼저 확인해 주세요.')
  migrate(s);return {'version':2},None
 need(s.get('ledgerVersion')==2,'통합 장부를 먼저 준비해 주세요.');normalized(s)
 result={};content=None
 if action=='erp.account.save':
  old=get(s,'accounts',d['id']) if d.get('id') else None
  name=text(d.get('name',''),160);need(name,'거래처명을 입력해 주세요.')
  a=copy.deepcopy(old) if old else dict(id=uid('ac'))
  for key in ('name','kind','contact','phone','phone2','email','businessNumber','representative','address','businessType','businessItem','notes'):a[key]=text(d.get(key,a.get(key,'')),2000 if key=='notes' else 500)
  a['active']=d.get('active',True) is True;a['paymentTermsDays']=money(d.get('paymentTermsDays',30));need(a['paymentTermsDays']<=365,'결제 기한은 365일 이내로 설정해 주세요.')
  if old:old.update(a)
  else:s['accounts'].append(a)
  result['recordId']=a['id']
 elif action=='erp.account.delete':
  a=get(s,'accounts',d.get('id'))
  quote_ids={r['id'] for r in s.get('quotes',[]) if r.get('accountId')==a['id']}
  order_ids={r['id'] for r in s.get('orders',[]) if r.get('accountId')==a['id']}
  voucher_ids={r['id'] for r in s.get('vouchers',[]) if r.get('accountId')==a['id'] or r.get('orderId') in order_ids}
  fulfillment_ids={r['id'] for r in s.get('fulfillments',[]) if r.get('voucherId') in voucher_ids or r.get('orderId') in order_ids}
  payment_ids={r['id'] for r in s.get('payments',[]) if r.get('accountId')==a['id'] or any(x.get('voucherId') in voucher_ids for x in r.get('allocations',[]))}
  delivery_ids={r['id'] for r in s.get('deliveries',[]) if r.get('accountId')==a['id'] or r.get('orderId') in order_ids or r.get('customer',{}).get('id')==a['id']}
  invoice_ids={r['id'] for r in s.get('invoices',[]) if r.get('accountId')==a['id'] or r.get('orderId') in order_ids or r.get('sourceDeliveryId') in delivery_ids}
  expense_ids={r['id'] for r in s.get('expenses',[]) if r.get('accountId')==a['id'] or r.get('orderId') in order_ids}
  receipt_ids={r['id'] for r in s.get('receipts',[]) if r.get('accountId')==a['id'] or r.get('orderId') in order_ids}
  receipt_ids|={x.get('receiptId') for x in s.get('allocations',[]) if x.get('invoiceId') in invoice_ids};receipt_ids.discard(None)
  operation_sources=fulfillment_ids|{'reverse-'+x for x in fulfillment_ids}
  deleted_sources=voucher_ids|{'reverse-'+x for x in voucher_ids}|payment_ids|{'reverse-'+x for x in payment_ids}|operation_sources
  s['stockMoves']=[r for r in s.get('stockMoves',[]) if r.get('sourceId') not in operation_sources]
  s['fulfillments']=[r for r in s.get('fulfillments',[]) if r['id'] not in fulfillment_ids]
  s['payments']=[r for r in s.get('payments',[]) if r['id'] not in payment_ids]
  s['vouchers']=[r for r in s.get('vouchers',[]) if r['id'] not in voucher_ids]
  s['allocations']=[r for r in s.get('allocations',[]) if r.get('invoiceId') not in invoice_ids and r.get('receiptId') not in receipt_ids]
  s['receipts']=[r for r in s.get('receipts',[]) if r['id'] not in receipt_ids]
  s['invoices']=[r for r in s.get('invoices',[]) if r['id'] not in invoice_ids]
  s['expenses']=[r for r in s.get('expenses',[]) if r['id'] not in expense_ids]
  s['deliveries']=[r for r in s.get('deliveries',[]) if r['id'] not in delivery_ids]
  s['orders']=[r for r in s.get('orders',[]) if r['id'] not in order_ids]
  s['quotes']=[r for r in s.get('quotes',[]) if r['id'] not in quote_ids]
  s['journalEntries']=[r for r in s.get('journalEntries',[]) if r.get('sourceId') not in deleted_sources]
  for kind in ('taxRecords','taxDrafts','taxJobs'):
   s[kind]=[r for r in s.get(kind,[]) if r.get('accountId')!=a['id'] and r.get('orderId') not in order_ids and r.get('voucherId') not in voucher_ids and r.get('sourceId') not in voucher_ids and r.get('erpSourceId') not in voucher_ids and r.get('invoiceId') not in invoice_ids and r.get('deliveryId') not in delivery_ids]
  s['costAllocations']=[r for r in s.get('costAllocations',[]) if r.get('orderId') not in order_ids and r.get('expenseId') not in expense_ids]
  s['tasks']=[r for r in s.get('tasks',[]) if r.get('accountId')!=a['id']]
  s['notes']=[r for r in s.get('notes',[]) if r.get('accountId')!=a['id']]
  for p in s['products']:
   if p.get('supplierId')==a['id']:p['supplierId']=''
  for document in s.get('documents',[]):
   if document.get('accountId')==a['id'] or document.get('quoteId') in quote_ids or document.get('orderId') in order_ids or document.get('voucherId') in voucher_ids:
    document['accountId']='';document['status']='거래처 삭제됨 · 파일 보존'
    for key in ('quoteId','orderId','voucherId','invoiceId','expenseId','deliveryId'):document[key]=''
  s['accounts']=[r for r in s['accounts'] if r['id']!=a['id']]
  audit(s,'거래처 삭제',a['id'],a['name']);result['recordId']=a['id']
 elif action=='erp.product.save':
  old=get(s,'products',d['id']) if d.get('id') else None
  p=dict(id=old['id'] if old else uid('product'),code=text(d.get('code',''),80),name=text(d.get('name',''),140),spec=text(d.get('spec',''),160),unit=text(d.get('unit','개'),12),salePrice=money(d.get('salePrice',0)),purchasePrice=money(d.get('purchasePrice',0)),supplierId=text(d.get('supplierId',''),160),trackStock=d.get('trackStock',True) is True,active=d.get('active',True) is True,minStock=numeric(dec(d.get('minStock',0))),notes=text(d.get('notes',''),1000))
  need(p['code'] and p['name'],'품목 코드와 품목명을 입력해 주세요.')
  need(not any(x['code'].casefold()==p['code'].casefold() and x['id']!=p['id'] for x in s['products']),'이미 등록된 품목 코드입니다.')
  if p['supplierId']:get(s,'accounts',p['supplierId'])
  if old:
   if old['trackStock']!=p['trackStock'] and any(m['productId']==p['id'] for m in s['stockMoves']):
    sources={m['sourceId'] for m in s['stockMoves'] if m['productId']==p['id']}
    s['stockMoves']=[m for m in s['stockMoves'] if m['productId']!=p['id']]
    for fulfillment in s['fulfillments']:
     for row in fulfillment.get('items',[]):
      if row.get('productId')==p['id']:row['tracked']=False;row['cost']=0;row['costProvisional']=False
    s['journalEntries']=[j for j in s['journalEntries'] if not (j.get('sourceId') in sources and (j.get('sourceId','').startswith('opening-') or j.get('sourceId','').startswith('usage-')))]
   old.update(p)
  else:s['products'].append(p)
  audit(s,'품목 저장',p['id']);result['recordId']=p['id']
 elif action=='erp.product.delete':
  p=get(s,'products',d.get('id'))
  result['recordId']=remove_product(s,p)
 elif action=='erp.product.bulk-delete':
  ids=d.get('ids');need(isinstance(ids,list) and 0<len(ids)<=500,'삭제할 품목을 1개 이상 500개 이하로 선택해 주세요.')
  ids=[text(product_id,160) for product_id in ids];need(all(ids) and len(ids)==len(set(ids)),'선택한 품목 목록을 확인해 주세요.')
  selected=[get(s,'products',product_id) for product_id in ids]
  for product in selected:remove_product(s,product)
  result.update(recordIds=ids,count=len(ids))
 elif action=='erp.stock.opening':
  p=get(s,'products',d['productId']);dt=day(d['date']);qty=dec(d['quantity'],True);value=money(d['value'])
  need(not any(m['productId']==p['id'] for m in s['stockMoves']),'기초재고는 첫 이동 전에만 등록할 수 있습니다.')
  source=uid('opening');move(s,p['id'],qty,value,dt,source);entry(s,source,dt,[('inventory',value,0),('equity',0,value)],'기초재고');audit(s,'기초재고',p['id']);result['recordId']=p['id']
 elif action=='erp.stock.use':
  p=get(s,'products',d['productId']);dt=day(d['date']);qty=dec(d['quantity'],True);reason=text(d.get('reason',''),500);need(reason,'사용 목적을 입력해 주세요.')
  b=stock(s,p['id'],dt);need(Decimal(str(b['quantity']))>=qty,'해당 날짜의 재고가 부족합니다.');value=b['value'] if qty==Decimal(str(b['quantity'])) else won(Decimal(b['value'])*qty/Decimal(str(b['quantity'])))
  source=uid('usage');move(s,p['id'],-qty,-value,dt,source);entry(s,source,dt,[('expense',value,0),('inventory',0,value)],reason);audit(s,'재고 사용',p['id'],reason);result['recordId']=p['id']
 elif action in ('erp.voucher.save','erp.order.voucher'):
  old=get(s,'vouchers',d['id']) if d.get('id') else None
  if old:
   # During manual-entry operation, a full correction intentionally resets the
   # dependent operational records so users never get trapped by circular locks.
   for fulfillment in list(s['fulfillments']):
    if fulfillment.get('voucherId')==old['id']:remove_fulfillment(s,fulfillment)
   for payment in s['payments']:
    before=len(payment.get('allocations',[]))
    payment['allocations']=[a for a in payment.get('allocations',[]) if a.get('voucherId')!=old['id']]
    if len(payment['allocations'])!=before:repost_payment(s,payment)
   references={old['id'],old.get('legacyId','')}-{''}
   for kind_name in ('taxRecords','taxDrafts','taxJobs'):
    s[kind_name]=[r for r in s.get(kind_name,[]) if not any(r.get(key) in references for key in ('voucherId','erpSourceId','sourceId','ledgerId','invoiceId','expenseId'))]
   generated_ids={doc['id'] for doc in s.get('documents',[]) if doc.get('generated') and (doc.get('voucherId')==old['id'] or doc.get('erpSourceId')==old['id'])}
   s['documents']=[doc for doc in s.get('documents',[]) if doc['id'] not in generated_ids]
   legacy_id=old.get('legacyId','')
   if legacy_id:
    s['invoices']=[r for r in s.get('invoices',[]) if r['id']!=legacy_id]
    s['expenses']=[r for r in s.get('expenses',[]) if r['id']!=legacy_id]
    s['allocations']=[r for r in s.get('allocations',[]) if r.get('invoiceId')!=legacy_id]
   s['journalEntries']=[j for j in s.get('journalEntries',[]) if j.get('sourceId') not in {old['id'],'reverse-'+old['id']}]
  order=get(s,'orders',d['orderId']) if d.get('orderId') else None
  if action=='erp.order.voucher':
   need(order and order.get('status')!='취소','유효한 주문을 선택해 주세요.')
   existing=next((v for v in s['vouchers'] if v.get('orderId')==order['id'] and v['kind']=='sale' and v['status']!='void'),None)
   if existing:return {'recordId':existing['id']},None
   need(order.get('items'),'기존 합계 주문은 품목을 먼저 등록해 주세요.')
   d={**d,'kind':'sale','accountId':order['accountId'],'title':order['title'],'items':[{**r,'orderLineId':r['id']} for r in order['items']]}
  kind=d.get('kind');need(kind in LABELS,'거래 종류를 확인해 주세요.')
  aid=text(d.get('accountId',''),160)
  if aid:get(s,'accounts',aid)
  need(aid or kind in ('expense','withdrawal'),'거래처를 선택해 주세요.')
  if order and kind=='sale':need(aid==order['accountId'],'주문과 매출의 고객사가 다릅니다.')
  if order and kind=='sale':
   need(order.get('status')!='취소','취소한 주문에는 매출을 연결할 수 없습니다.')
   duplicate=next((v for v in s['vouchers'] if v.get('orderId')==order['id'] and v['kind']=='sale' and v['status']!='void' and v['id']!=d.get('id')),None)
   need(not duplicate,'이 주문에는 이미 매출 전표가 있습니다. 연결된 전표에서 수정·납품을 기록해 주세요: '+(duplicate or {}).get('number',''))
  dt=day(d['date']);due=day(d.get('dueDate') or terms(s,aid,dt));need(due>=dt,'만기일은 거래일 이후로 입력해 주세요.')
  rows=item_rows(s,d.get('items'),kind);need(totals(rows)['amount']>0,'합계는 0원보다 커야 합니다.')
  if kind=='withdrawal':need(all(r['vat']==0 for r in rows),'개인 인출은 부가세 없이 입력해 주세요.')
  v=dict(id=old['id'] if old else uid('voucher'),number=old['number'] if old else number(s,'vouchers',{'sale':'S','purchase':'P','expense':'E','withdrawal':'W'}[kind],dt),kind=kind,date=dt,dueDate=due,confirmationDueDate=day(d.get('confirmationDueDate') or dt),accountId=aid,orderId=order['id'] if order else '',title=text(d.get('title',''),160),category=text(d.get('category','기타 사업 비용'),80),status='draft',items=rows,vatDeductible=d.get('vatDeductible',kind=='purchase') is True,notes=text(d.get('notes',''),2000),approvalNumber='',documentId=text(d.get('documentId',''),160),createdAt=old.get('createdAt',now()) if old else now(),**totals(rows))
  quote=next((q for q in s['quotes'] if order and q['id']==order.get('quoteId')), {})
  for key in ('attention','deliveryTerms','paymentTerms','orderDifferenceReason'):v[key]=text(d.get(key,(old or {}).get(key,quote.get(key,''))),500)
  v['taxEvidence']=copy.deepcopy((old or {}).get('taxEvidence',{}))
  v['approvalNumber']=(old or {}).get('approvalNumber','')
  need(v['title'],'거래 내용을 입력해 주세요.')
  if old:need(old['kind']==kind,'전표 종류는 바꿀 수 없습니다.');old.clear();old.update(v)
  else:s['vouchers'].append(v)
  if v['documentId']:
   doc=get(s,'documents',v['documentId']);need(not doc.get('expenseId') and not doc.get('quoteId') and not doc.get('generated'),'이미 사용한 문서입니다. 기존 전표에 첨부해 주세요.');need(not doc.get('voucherId') or doc['voucherId']==v['id'],'이미 연결된 문서입니다.');doc.update(voucherId=v['id'],accountId=aid,status='등록 완료')
  v.update(workflow.snapshots(s,aid) if aid else dict(company=workflow.profile(s['settings']),customer=workflow.profile({})))
  audit(s,'전표 전체 수정 · 연결 이력 초기화' if old else '전표 초안 저장',v['id']);result['recordId']=v['id']
 elif action=='erp.voucher.info':
  v=get(s,'vouchers',d.get('id'))
  title=text(d.get('title',''),160);need(title,'거래 내용을 입력해 주세요.')
  before={key:v.get(key,'') for key in ('title','attention','deliveryTerms','paymentTerms','notes')}
  v.update(title=title,attention=text(d.get('attention',''),500),deliveryTerms=text(d.get('deliveryTerms',''),500),paymentTerms=text(d.get('paymentTerms',''),500),notes=text(d.get('notes',''),2000))
  for document in s.get('documents',[]):
   if document.get('generated') and document.get('voucherId')==v['id']:
    document['status']='이전 생성본 · 전표 수정됨'
  audit(s,'전표 표시 정보 수정',v['id'],json.dumps({'before':before,'after':{key:v.get(key,'') for key in before}},ensure_ascii=False));result['recordId']=v['id']
 elif action=='erp.voucher.confirm':
  v=get(s,'vouchers',d['id']);confirm(s,v,day(d.get('date',v['date'])))
  if v.get('legacyReviewNeeded'):v['legacyReviewNeeded']=False;audit(s,'이관 전표 증빙 확인',v['id'])
  result['recordId']=v['id']
 elif action=='erp.voucher.approval':
  v=get(s,'vouchers',d['id']);need(v['status']=='confirmed','전표 확정 후 승인번호를 메모할 수 있습니다.')
  approval=text(d.get('approvalNumber',''),100);check_approval(s,v,approval)
  previous=v.get('approvalNumber','');v['approvalNumber']=approval
  audit(s,'국세청 승인번호 메모',v['id'],previous+' → '+v['approvalNumber']);result['recordId']=v['id']
 elif action=='erp.voucher.evidence':
  from tax_evidence import parse
  v=get(s,'vouchers',d['id']);previous=dict(taxEvidence=copy.deepcopy(v.get('taxEvidence',{})),approvalNumber=v.get('approvalNumber',''))
  v['taxEvidence']=parse(d.get('taxEvidence',{}))
  approval=text(d.get('approvalNumber',v.get('approvalNumber','')),100);check_approval(s,v,approval)
  v['approvalNumber']=approval
  audit(s,'세무사 증빙 정보 수정',v['id'],json.dumps({'before':previous,'after':dict(taxEvidence=v['taxEvidence'],approvalNumber=approval)},ensure_ascii=False));result['recordId']=v['id']
 elif action=='erp.voucher.difference':
  v=get(s,'vouchers',d['id']);v['orderDifferenceReason']=text(d.get('reason',''),500)
  audit(s,'주문·전표 금액 차이 사유',v['id'],v['orderDifferenceReason']);result['recordId']=v['id']
 elif action=='erp.voucher.schedule':
  v=get(s,'vouchers',d['id']);need(v['status']!='void','취소한 전표입니다.')
  due=day(d['dueDate']);need(due>=v['date'],'결제 만기일은 거래일 이후여야 합니다.')
  v.update(dueDate=due,confirmationDueDate=day(d['confirmationDueDate']))
  audit(s,'전표 기한 수정',v['id']);result['recordId']=v['id']
 elif action=='erp.order.schedule':
  o=get(s,'orders',d['id']);status=d.get('status')
  need(status in ('주문 접수','제작 중'),'진행 상태를 확인해 주세요.')
  need(o['status'] not in ('납품 완료','일부 납품','취소'),'이미 납품한 주문은 전표의 입출고 기록에서 관리해 주세요.')
  due=day(d['dueDate']) if d.get('dueDate') else ''
  need(not due or due>=o['date'],'납품 예정일을 확인해 주세요.')
  o.update(status=status,dueDate=due,notes=text(d.get('notes',''),2000));result['recordId']=o['id']
 elif action=='erp.voucher.void':
  v=get(s,'vouchers',d['id']);need(v['status']!='void','이미 취소된 전표입니다.');reason=text(d.get('reason',''),500);need(reason,'취소 사유를 입력해 주세요.')
  need(not v.get('legacy'),'이관한 과거 전표는 원본 보존을 위해 관리자 검토가 필요합니다.')
  need(not any(f['voucherId']==v['id'] and f['status']=='completed' for f in s['fulfillments']) and paid(s,v)==0,'입출고 또는 결제를 먼저 취소해 주세요.')
  dt=day(d['date']);need(dt>=v.get('confirmedDate',v['date']),'취소일을 확인해 주세요.')
  need(all(dt>=f.get('voidDate',f['date']) for f in s['fulfillments'] if f['voucherId']==v['id']),'전표 취소일은 입출고 취소일 이후로 입력해 주세요.')
  need(all(dt>=p.get('voidDate',p['date']) for p in s['payments'] if any(a['voucherId']==v['id'] for a in p['allocations'])),'전표 취소일은 결제 취소일 이후로 입력해 주세요.')
  if v['status']=='confirmed':entry(s,'reverse-'+v['id'],dt,[(a,cr,dr) for a,dr,cr in confirmation_rows(s,v)],reason)
  v.update(status='void',voidReason=reason,voidDate=dt);audit(s,'전표 취소',v['id'],reason);result['recordId']=v['id']
 elif action=='erp.voucher.delete':
  v=get(s,'vouchers',d.get('id'));legacy_id=v.get('legacyId','')
  active_fulfillments=[f for f in s.get('fulfillments',[]) if f.get('voucherId')==v['id'] and f.get('status')=='completed']
  linked_payments=[p for p in s.get('payments',[]) if any(a.get('voucherId')==v['id'] for a in p.get('allocations',[]))]
  shared_payments=[p for p in linked_payments if any(a.get('voucherId')!=v['id'] for a in p.get('allocations',[]))]
  dedicated_payments=[p for p in linked_payments if p not in shared_payments]
  raw_allocations=[a for a in s.get('allocations',[]) if v.get('legacy') and v['kind']=='sale' and a.get('invoiceId')==legacy_id]
  raw_receipt_ids={a.get('receiptId') for a in raw_allocations}
  shared_raw_receipts={receipt_id for receipt_id in raw_receipt_ids if any(a.get('receiptId')==receipt_id and a.get('invoiceId')!=legacy_id for a in s.get('allocations',[]))}
  references={v['id'],legacy_id}-{''}
  # Voided operations are audit trails with a net-zero effect. Once every active
  # dependency has been cancelled, remove those orphanable trails with the voucher
  # so a user does not get trapped in a cancel-then-still-cannot-delete loop.
  void_fulfillments=[f for f in s.get('fulfillments',[]) if f.get('voucherId')==v['id']]
  void_sources={f['id'] for f in void_fulfillments}
  operation_sources=void_sources|{'reverse-'+source for source in void_sources}
  s['fulfillments']=[f for f in s.get('fulfillments',[]) if f.get('voucherId')!=v['id']]
  s['stockMoves']=[m for m in s.get('stockMoves',[]) if m.get('sourceId') not in operation_sources]
  payment_ids={p['id'] for p in dedicated_payments}
  for payment in dedicated_payments:remove_payment(s,payment)
  for payment in shared_payments:
   payment['allocations']=[a for a in payment.get('allocations',[]) if a.get('voucherId')!=v['id']]
   repost_payment(s,payment)
  if v.get('legacy') and v['kind']=='sale':
   s['allocations']=[a for a in s.get('allocations',[]) if a.get('invoiceId')!=legacy_id]
   s['receipts']=[r for r in s.get('receipts',[]) if r['id'] not in raw_receipt_ids or r['id'] in shared_raw_receipts]
   s['invoices']=[record for record in s.get('invoices',[]) if record['id']!=legacy_id]
  elif v.get('legacy'):
   s['expenses']=[record for record in s.get('expenses',[]) if record['id']!=legacy_id]
  for kind_name in ('taxRecords','taxDrafts','taxJobs'):
   s[kind_name]=[r for r in s.get(kind_name,[]) if not any(r.get(key) in references for key in ('voucherId','erpSourceId','sourceId','ledgerId','invoiceId','expenseId'))]
  for other in s.get('vouchers',[]):
   other['credits']=[credit for credit in other.get('credits',[]) if credit.get('voucherId')!=v['id']]
  def related_document(document):
   return document.get('voucherId')==v['id'] or document.get('erpSourceId')==v['id'] or (legacy_id and (document.get('invoiceId')==legacy_id or document.get('expenseId')==legacy_id))
  generated=[document for document in s.get('documents',[]) if document.get('generated') and related_document(document)]
  generated_ids=[document['id'] for document in generated]
  for document in s.get('documents',[]):
   if related_document(document) and document['id'] not in generated_ids:
    for key in ('voucherId','orderId','invoiceId','expenseId'):document[key]=''
    document['status']='전표 삭제됨 · 파일 보존'
  s['documents']=[document for document in s.get('documents',[]) if document['id'] not in generated_ids]
  deleted_sources=operation_sources|{v['id'],'reverse-'+v['id']}|payment_ids|{'reverse-'+source for source in payment_ids}
  s['journalEntries']=[j for j in s.get('journalEntries',[]) if j.get('sourceId') not in deleted_sources]
  s['vouchers']=[record for record in s['vouchers'] if record['id']!=v['id']]
  if v.get('legacy') and s.get('migrationInfo'):
   s['migrationInfo'].update(legacyInvoices=len(s.get('invoices',[])),legacyExpenses=len(s.get('expenses',[])),legacyReceipts=len(s.get('receipts',[])))
  if v.get('orderId'):
   order=next((record for record in s.get('orders',[]) if record['id']==v['orderId']),None)
   if order and order.get('status')!='취소':order['status']='주문 접수'
  audit(s,'전표 삭제',v['id'],v.get('number','')+' · '+v.get('title',''));result.update(recordId=v['id'],deletedDocumentIds=generated_ids,deletedPaymentIds=list(payment_ids))
 elif action=='erp.fulfill':
  v=get(s,'vouchers',d['id']);f=fulfill(s,v,d['items'],day(d['date']));result.update(recordId=v['id'],fulfillmentId=f['id'])
 elif action=='erp.fulfillment.void':
  f=get(s,'fulfillments',d['id']);v=get(s,'vouchers',f['voucherId']);need(f['status']=='completed','이미 취소된 입출고입니다.')
  reason=text(d.get('reason',''),500);need(reason,'취소 사유가 필요합니다.');dt=day(d['date']);need(dt>=f['date'],'취소일을 확인해 주세요.')
  active=[x for x in s['fulfillments'] if x['voucherId']==v['id'] and x['status']=='completed']
  need(active[-1]['id']==f['id'],'같은 전표의 최근 입출고부터 취소해 주세요.')
  if v['status']=='confirmed':need(dt>=v.get('confirmedDate',v['date']),'취소일은 전표 확정일 이후여야 합니다.')
  void_sources={x['id'] for x in s['fulfillments'] if x['status']=='void'}
  ignored=void_sources|{'reverse-'+x for x in void_sources}|{f['id'],'reverse-'+f['id']}
  moves=[m for m in s['stockMoves'] if m['sourceId']==f['id']]
  for m in reversed(moves):
   later=[x for x in s['stockMoves'] if x['productId']==m['productId'] and x['sourceId'] not in ignored]
   need(not later or s['stockMoves'].index(later[-1])<s['stockMoves'].index(m),'후속 입출고가 있어 최근 이동부터 취소해야 합니다.')
   move(s,m['productId'],-Decimal(str(m['quantity'])),-m['value'],dt,'reverse-'+f['id'],m['lineId'])
  reverse_entries(s,f['id'],dt,reason)
  if v['status']=='confirmed' and not f.get('confirmedAtCreation'):
   bridge=[('unbilledSales',f['net'],0),('deferredSales',0,f['net'])] if v['kind']=='sale' else [('unreceived',f['cost'],0),('accruedPurchase',0,f['cost'])]
   entry(s,'reverse-'+f['id'],dt,bridge,'확정 후 입출고 취소 조정')
  f.update(status='void',voidReason=reason,voidDate=dt)
  if v.get('orderId') and v['kind']=='sale':get(s,'orders',v['orderId'])['status']='일부 납품' if any(fulfilled(s,v,r['id']) for r in v['items']) else '주문 접수'
  audit(s,'입출고 취소',v['id'],reason);result['recordId']=v['id']
 elif action=='erp.fulfillment.delete':
  f=get(s,'fulfillments',d['id']);voucher_id=f['voucherId'];order_id=f.get('orderId','')
  remove_fulfillment(s,f)
  from ledger_replay import rebuild
  rebuild(s);refresh_order_status(s,order_id)
  audit(s,'입출고 기록 삭제',voucher_id,f['id']);result['recordId']=voucher_id
 elif action=='erp.fulfillment.update':
  f=get(s,'fulfillments',d['id']);v=get(s,'vouchers',f['voucherId']);order_id=f.get('orderId','')
  need(f.get('status')=='completed','취소된 입출고는 삭제 후 다시 입력해 주세요.')
  remove_fulfillment(s,f)
  from ledger_replay import rebuild
  rebuild(s)
  replacement=fulfill(s,v,d['items'],day(d['date']));refresh_order_status(s,order_id)
  audit(s,'입출고 기록 수정',v['id'],f['id']+' → '+replacement['id']);result.update(recordId=v['id'],fulfillmentId=replacement['id'])
 elif action=='erp.payment.save':
  direction=d.get('direction');need(direction in ('in','out'),'수금 또는 지급을 선택해 주세요.')
  old=get(s,'payments',d['id']) if d.get('id') else None
  aid=text(d.get('accountId',''),160)
  if aid:get(s,'accounts',aid)
  dt=day(d['date']);amount=money(d['amount'],True)
  p=dict(id=old['id'] if old else uid('payment'),direction=direction,accountId=aid,date=dt,amount=amount,status='confirmed',memo=text(d.get('memo',''),500),orderId=text(d.get('orderId',(old or {}).get('orderId','')),160),allocations=copy.deepcopy((old or {}).get('allocations',[])))
  if p['orderId']:need(get(s,'orders',p['orderId'])['accountId']==aid,'주문 거래처를 확인해 주세요.')
  voucher=get(s,'vouchers',d['voucherId']) if d.get('voucherId') else None
  allocation_date=max(dt,voucher.get('confirmedDate',voucher['date'])) if voucher else ''
  if old:
   need(sum(a['amount'] for a in p['allocations'])<=amount,'이미 전표에 연결한 금액보다 작게 수정할 수 없습니다. 연결 기록을 먼저 수정하거나 삭제해 주세요.')
   for allocation in p['allocations']:
    linked=get(s,'vouchers',allocation['voucherId'])
    need(linked['accountId']==aid and (direction=='in')==(linked['kind']=='sale'),'연결된 전표와 같은 거래처·수금지급 구분을 사용해 주세요.')
    allocation['date']=max(dt,linked.get('confirmedDate',linked['date']))
   old.clear();old.update(p);repost_payment(s,old)
  else:
   if voucher:check_allocation(s,p,voucher,amount,allocation_date)
   s['payments'].append(p);repost_payment(s,p)
   if voucher:allocate(s,p,voucher,amount,allocation_date)
  audit(s,('수금' if direction=='in' else '지급')+(' 기록 수정' if old else ' 기록'),p['id']);result['recordId']=p['id']
 elif action=='erp.payment.allocate':
  p=get(s,'payments',d['id']);v=get(s,'vouchers',d['voucherId']);allocate(s,p,v,money(d['amount'],True),day(d['date']));result['recordId']=v['id']
 elif action=='erp.payment.void':
  p=get(s,'payments',d['id']);need(p['status']=='confirmed','이미 취소된 결제입니다.')
  reason=text(d.get('reason',''),500);need(reason,'취소 사유가 필요합니다.');dt=day(d['date']);need(dt>=max([p['date']]+[a['date'] for a in p['allocations']]),'취소일을 확인해 주세요.')
  reverse_entries(s,p['id'],dt,reason);p.update(status='void',voidReason=reason,voidDate=dt);audit(s,'결제 취소',p['id'],reason);result['recordId']=p['id']
 elif action=='erp.payment.delete':
  p=get(s,'payments',d['id']);record_id=p['id'];remove_payment(s,p)
  audit(s,'수금 기록 삭제' if p['direction']=='in' else '지급 기록 삭제',record_id);result['recordId']=record_id
 elif action=='erp.task.save':
  old=get(s,'tasks',d['id']) if d.get('id') else None
  aid=text(d.get('accountId',''),160)
  if aid:get(s,'accounts',aid)
  status=d.get('status')
  if status is None:
   if 'done' in d:need(type(d['done']) is bool,'할 일의 완료 여부를 확인해 주세요.')
   status=('completed' if d['done'] else 'in_progress') if 'done' in d else (old.get('status', 'completed' if old.get('done') else 'in_progress') if old else 'not_started')
  need(status in ('not_started','in_progress','completed'),'할 일의 진행 상태를 선택해 주세요.')
  t=dict(id=old['id'] if old else uid('task'),accountId=aid,title=text(d.get('title',''),200),date=day(d['date']),dueDate=day(d['date']),notes=text(d.get('notes',''),2000),status=status,done=status=='completed',createdAt=old.get('createdAt',now()) if old else now())
  need(t['title'],'할 일을 입력해 주세요.')
  if old:old.update(t)
  else:s['tasks'].append(t)
  result['recordId']=t['id']
 elif action=='erp.task.delete':
  t=get(s,'tasks',d['id']);s['tasks']=[r for r in s['tasks'] if r['id']!=t['id']];result['recordId']=t['id']
 elif action=='erp.task.bulk':
  operation=d.get('operation');need(operation in ('complete','delete'),'할 일 일괄 작업을 선택해 주세요.')
  ids=d.get('ids');need(isinstance(ids,list) and 0<len(ids)<=500,'처리할 할 일을 1개 이상 선택해 주세요.')
  ids=[text(task_id,160) for task_id in ids];need(all(ids) and len(ids)==len(set(ids)),'선택한 할 일 목록을 확인해 주세요.')
  selected=[get(s,'tasks',task_id) for task_id in ids]
  if operation=='complete':
   for task in selected:task.update(status='completed',done=True)
  else:
   selected_ids=set(ids);s['tasks']=[task for task in s['tasks'] if task['id'] not in selected_ids]
  result.update(recordIds=ids,count=len(ids))
 elif action=='erp.note.save':
  aid=text(d.get('accountId',''),160);get(s,'accounts',aid);body=text(d.get('body',''),2000);need(body,'메모를 입력해 주세요.')
  n=dict(id=uid('note'),accountId=aid,date=day(d['date']),body=body,type='메모');s['notes'].append(n);result['recordId']=n['id']
 elif action=='erp.note.delete':
  n=get(s,'notes',d['id']);s['notes']=[r for r in s['notes'] if r['id']!=n['id']];result['recordId']=n['id']
 elif action=='erp.document.attach':
  v=get(s,'vouchers',d['id']);doc=get(s,'documents',d['documentId'])
  need(not doc.get('generated'),'생성 문서는 원래 기록에 보존됩니다.')
  need(not doc.get('voucherId') or doc['voucherId']==v['id'],'다른 전표에 이미 연결된 문서입니다.')
  need(not doc.get('expenseId') or v.get('legacyId')==doc['expenseId'],'이전 비용에 연결된 문서입니다. 해당 전표를 선택해 주세요.')
  need(not doc.get('quoteId'),'견적에 이미 연결된 문서입니다.')
  doc.update(voucherId=v['id'],accountId=v['accountId'],orderId=v.get('orderId',''),status='등록 완료')
  audit(s,'문서 연결',v['id'],doc['id']);result['recordId']=v['id']
 elif action=='erp.document':
  from erp_documents import generate
  return generate(s,d,workspace)
 else:raise ValueError('지원하지 않는 통합 업무입니다.')
 if action in ('erp.account.delete','erp.product.save','erp.product.delete','erp.product.bulk-delete','erp.stock.opening','erp.stock.use','erp.fulfillment.void','erp.voucher.void','erp.voucher.delete'):
  from ledger_replay import rebuild
  rebuild(s)
 validate(s);return result,content

def check_approval(s,v,approval):
 normalized_number=''.join(c for c in approval.casefold() if c.isalnum())
 if not normalized_number:return
 duplicate=next((x for x in s['vouchers'] if x['id']!=v['id'] and x['status']!='void' and (x['kind']=='sale')==(v['kind']=='sale') and (v['kind']=='sale' or x['accountId']==v['accountId']) and ''.join(c for c in x.get('approvalNumber','').casefold() if c.isalnum())==normalized_number),None)
 need(not duplicate,'이미 등록된 승인번호입니다. 기존 전표를 확인해 주세요: '+(duplicate or {}).get('number',''))

def check_allocation(s,p,v,amount,dt):
 need(p['status']=='confirmed' and v['status']=='confirmed','확정된 전표와 결제만 연결할 수 있습니다.')
 need(p['accountId']==v['accountId'] and (p['direction']=='in')==(v['kind']=='sale'),'같은 거래처의 수금·지급을 선택해 주세요.')
 need(dt>=max(p['date'],v.get('confirmedDate',v['date'])),'연결일은 결제일과 전표 확정일 이후여야 합니다.')
 payment_left=p['amount']-sum(a['amount'] for a in p['allocations'])
 voucher_left=balance(s,v)
 need(amount<=payment_left,f'이 결제 기록에서 연결할 수 있는 남은 금액은 {payment_left:,}원입니다.')
 need(amount<=voucher_left,f'{v.get("number","전표")}의 실제 남은 금액은 {voucher_left:,}원입니다. 이미 연결된 수금·지급 이력을 확인한 뒤 화면을 새로고침해 주세요.')

def allocate(s,p,v,amount,dt):
 check_allocation(s,p,v,amount,dt)
 p['allocations'].append(dict(voucherId=v['id'],amount=amount,date=dt))
 entry(s,p['id'],dt,[('advanceIn',amount,0),('ar',0,amount)] if p['direction']=='in' else [('ap',amount,0),('advanceOut',0,amount)],'결제 연결')
