"""Optional accountant review metadata, separate from posted operating amounts."""
import re

TEXT_FIELDS = ('evidenceType','evidenceStatus','taxCategory','deductionStatus','nondeductionReason',
 'originalApprovalNumber','originalVoucherId','correctionReason','taxAccountCode','accountantMemo',
 'externalId','provider','paymentMethod','bankAccountLabel','bankTransactionId','cashflowType',
 'costAllocationMemo','taxpayerType','entityType')
DATE_FIELDS = ('supplyDate','accountingDate','writtenDate','issuedDate','receivedDate','transmittedDate','correctionDate')
MONEY_FIELDS = ('sourceNet','sourceVat','sourceGross','deductibleVat','nondeductibleVat','roundingAdjustment')

def parse(raw):
    import ledger as L
    L.need(isinstance(raw,dict),'세무사 증빙 입력 형식을 확인해 주세요.')
    out={}
    for key in TEXT_FIELDS:
        value=L.text(raw.get(key,''),2000 if key.endswith('Memo') else 500)
        if value:out[key]=value
    for key in DATE_FIELDS:
        if raw.get(key):out[key]=L.day(raw[key])
    for key in MONEY_FIELDS:
        value=raw.get(key)
        if value is not None and value!='':
            L.need(type(value) is int and abs(value)<=10**13,'증빙 금액은 원 단위 정수로 입력해 주세요.')
            out[key]=value
    period=L.text(raw.get('taxPeriod',''),7)
    if period:
        L.need(re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])',period),'귀속월을 확인해 주세요.');out['taxPeriod']=period
    ratio=raw.get('businessUseRatio')
    if ratio is not None and ratio!='':
        L.need(type(ratio) in (int,float) and 0<=ratio<=100,'업무 사용 비율은 0~100으로 입력해 주세요.');out['businessUseRatio']=ratio
    return out
