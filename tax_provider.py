"""Popbill adapter. The SDK runs in an isolated, time-bounded process.

Simulation never opens a network connection. Provider keys stay in server env.
"""
import copy
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path


def settings(workspace):
    mode = os.environ.get('GROWTH_TAX_MODE', 'disabled')
    if mode not in ('simulation', 'sandbox', 'live', 'disabled'):
        mode = 'disabled'
    required = ('POPBILL_LINK_ID', 'POPBILL_SECRET_KEY', 'POPBILL_CORP_NUM', 'POPBILL_USER_ID')
    configured = all(os.environ.get(k) for k in required)
    allowed = (mode == 'simulation' and workspace == 'demo') or (
        configured and mode == 'sandbox' and workspace == 'demo') or (
        configured and mode == 'live' and workspace == 'live' and os.environ.get('GROWTH_TAX_ALLOW_LIVE') == '1')
    return dict(mode=mode, ready=bool(allowed), provider='popbill',
                label={'simulation':'시뮬레이션 · 국세청 미연결', 'sandbox':'팝빌 연동 확인 · 국세청 미전송',
                       'live':'팝빌 운영 연동', 'disabled':'홈택스 수동 발급 · API 사용 안 함'}[mode])


def check(workspace, company):
    config = settings(workspace)
    if not config['ready']:
        raise ValueError('세금계산서 연동 설정이 필요합니다. 설정 화면의 연결 안내를 확인해 주세요.')
    if config['mode'] != 'simulation' and re.sub(r'\D', '', company.get('businessNumber', '')) != os.environ['POPBILL_CORP_NUM']:
        raise ValueError('회사 정보의 사업자등록번호와 팝빌 연동 사업자를 확인해 주세요.')
    return config['mode']


def approval(key):
    # Deliberately distinguish mock approval numbers from actual NTS numbers.
    return 'SIM' + hashlib.sha256(key.encode()).hexdigest()[:21].upper()


def simulate(op):
    kind = op['kind']
    if kind == 'collect.start':
        return dict(jobId='SIM-' + op['id'])
    if kind == 'collect.poll':
        return dict(done=True, rows=op['fixtures'], page=1, pageCount=1, total=len(op['fixtures']))
    if kind == 'record.detail':
        return dict(record=op['record'])
    if kind in ('draft.issue', 'draft.status'):
        return dict(info=dict(stateCode=300 if kind == 'draft.issue' else 304,
                              ntsconfirmNum=approval(op['key']), ntsresult='' if kind == 'draft.issue' else 'SUC001',
                              ntssendErrCode='', stateMemo='모의 발급' if kind == 'draft.issue' else '모의 전송 성공'))
    raise ValueError('지원하지 않는 연동 요청입니다.')


def execute(op):
    if op['mode'] == 'simulation':
        return simulate(op)
    # Do not retry issuance on a timeout: the durable outbox must be queried first.
    try:
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--worker'],
                                input=json.dumps(op, ensure_ascii=False), text=True, capture_output=True, timeout=75)
        if result.returncode != 0:
            return {'error': '연동 응답을 확인하지 못했습니다. 상태 조회로 확인해 주세요.', 'uncertain': True}
        data = json.loads(result.stdout)
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except (subprocess.TimeoutExpired, ValueError, OSError):
        return {'error': '연동 응답을 확인하지 못했습니다. 상태 조회로 확인해 주세요.', 'uncertain': True}


def plain(value):
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if hasattr(value, '__dict__'):
        return plain(vars(value))
    return value


def real(op):
    import socket
    import popbill
    socket.setdefaulttimeout(20)  # This process handles just one provider operation.
    cls = popbill.HTTaxinvoiceService if op['kind'].startswith(('collect.', 'record.')) else popbill.TaxinvoiceService
    service = cls(os.environ['POPBILL_LINK_ID'], os.environ['POPBILL_SECRET_KEY'])
    service.IsTest = op['mode'] == 'sandbox'
    # Leave SDK IP restriction enabled; configure a supported egress strategy before live use.
    corp, user = os.environ['POPBILL_CORP_NUM'], os.environ['POPBILL_USER_ID']
    try:
        if op['kind'] == 'collect.start':
            return dict(jobId=service.requestJob(corp, op['direction'], 'W', op['start'].replace('-', ''), op['end'].replace('-', ''), user))
        if op['kind'] == 'collect.poll':
            job = plain(service.getJobState(corp, op['jobId'], user))
            if int(job.get('jobState', 0)) != 3:
                return dict(done=False, jobState=job.get('jobState'))
            if int(job.get('errorCode', 0)) != 1:
                return dict(error='수집 실패 · 팝빌 오류 코드 ' + str(job.get('errorCode', '')), uncertain=False)
            result = plain(service.search(corp, op['jobId'], [], [], [], '', '', '', op['page'], 50, 'A', user))
            if int(result.get('code', 1)) != 1:
                return dict(error='수집 조회 실패 · 팝빌 오류 코드 ' + str(result.get('code', '')), uncertain=False)
            return dict(done=True, rows=result.get('list', []), page=result['pageNum'], pageCount=result['pageCount'], total=result['total'])
        if op['kind'] == 'record.detail':
            return dict(record=plain(service.getTaxinvoice(corp, op['approval'], user)))
        if op['kind'] == 'draft.issue':
            # The stable management key is also checked remotely before any write.
            if service.checkMgtKeyInUse(corp, 'SELL', op['key']):
                return dict(info=plain(service.getInfo(corp, 'SELL', op['key'])))
            payload = copy.deepcopy(op['payload'])
            payload['detailList'] = [popbill.TaxinvoiceDetail(**r) for r in payload['detailList']]
            service.registIssue(corp, popbill.Taxinvoice(**payload), False, False, None, 'Growth ERP', None, user)
            return dict(info=plain(service.getInfo(corp, 'SELL', op['key'])))
        if op['kind'] == 'draft.status':
            if not service.checkMgtKeyInUse(corp, 'SELL', op['key']):
                return dict(notFound=True)
            return dict(info=plain(service.getInfo(corp, 'SELL', op['key'])))
    except popbill.PopbillException as error:
        # An API failure after a write can still have an unknown outcome.
        return dict(error='팝빌 오류 코드 ' + str(error.code) + ' · 연동 서비스에서 상세 사유를 확인해 주세요.',
                    uncertain=op['kind'] == 'draft.issue')
    return dict(error='지원하지 않는 연동 요청입니다.', uncertain=False)


if __name__ == '__main__':
    try:
        request = json.load(sys.stdin)
        print(json.dumps(real(request), ensure_ascii=False))
    except Exception:
        print(json.dumps({'error':'연동 응답을 확인하지 못했습니다. 상태 조회로 확인해 주세요.', 'uncertain':True}))
