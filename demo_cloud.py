"""Stateless public demo. No database, production credentials, or external AI calls."""
import base64
import io
import json
import tempfile
import hashlib
import uuid
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from flask import Flask, jsonify, request, send_file, send_from_directory
from werkzeug.exceptions import HTTPException
import ai_guide
import server as core
import workflow
from demo_seed import demo_state

app = Flask(__name__, static_folder=None)
app.config['MAX_CONTENT_LENGTH'] = 3 * 1024 * 1024
ROOT = Path(__file__).parent


@app.after_request
def headers(r):
    r.headers['X-Content-Type-Options']='nosniff'
    r.headers['Referrer-Policy']='strict-origin-when-cross-origin'
    r.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    if request.path.startswith('/api/'):
        r.headers['Cache-Control']='no-store'
    return r


@app.errorhandler(Exception)
def error(e):
    if isinstance(e, HTTPException):return jsonify(error=e.description), e.code
    if isinstance(e, (ValueError, KeyError, TypeError, StopIteration)):
        return jsonify(error=str(e) if isinstance(e, ValueError) else '입력 형식을 확인해 주세요.'), 400
    app.logger.exception('Demo request failed')
    return jsonify(error='요청을 완료하지 못했습니다. 다시 시도해 주세요.'), 500


@app.get('/api/health')
def health():return jsonify(ok=True, mode='portfolio', persistence='browser', externalServices=False)


@app.get('/api/demo/seed')
def seed():
    import erp_documents
    state=demo_state(datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat())
    files={}
    for kind,ident,fmt in [('quote',state['quotes'][-1]['id'],'pdf'),
            ('quote',state['quotes'][-1]['id'],'xlsx'),
            ('voucher',state['vouchers'][-2]['id'],'pdf')]:
        # Select a sales voucher for the statement, independently of list position.
        if kind=='voucher':ident=next(v['id'] for v in reversed(state['vouchers']) if v['kind']=='sale')
        result,content=erp_documents.generate(state,dict(kind=kind,id=ident,format=fmt),'demo')
        files[result['documentId']]=base64.b64encode(content).decode()
    return jsonify(state=state, revision=0,files=files)


@app.post('/api/demo/command')
def command():
    body=request.get_json()
    state=body.get('state')
    if not isinstance(state,dict) or state.get('ledgerVersion')!=2:
        raise ValueError('데모 장부를 확인해 주세요.')
    core.validate_state(state)
    action=body.get('action','')
    if not isinstance(action,str) or not (action.startswith('erp.') or action in (
        'quote.save','quote.convert','order.save','order.schedule','document.generate')):
        raise ValueError('공개 데모에서 지원하지 않는 작업입니다.')
    result, content=workflow.apply(state, dict(action=action,data=body.get('data',{})), 'demo', actor='portfolio-visitor')
    core.validate_state(state)
    response=dict(state=state, result=result)
    if content:response['file']=dict(id=result['documentId'], content=base64.b64encode(content).decode())
    return jsonify(response)


@app.post('/api/demo/validate')
def validate():
    body=request.get_json();core.validate_state(body['state'])
    workflow.guard(body['previous'],body['state'])
    return jsonify(ok=True)


@app.get('/api/guide')
def guide():
    return jsonify(enabled=True, articles=ai_guide.KNOWLEDGE['articles'], usage={}, demo=True)


@app.post('/api/demo/upload')
def upload():
    body=request.get_json();name=body.get('name','')
    if not isinstance(name,str) or len(name)>200 or Path(name).suffix.lower() not in ('.pdf','.xlsx'):
        raise ValueError('PDF 또는 XLSX 가상 문서를 선택하세요.')
    try:content=base64.b64decode(body.get('content',''),validate=True)
    except Exception:raise ValueError('파일을 읽지 못했습니다.')
    if len(content)>1024*1024:raise ValueError('1MB 이하의 가상 문서를 선택하세요.')
    with tempfile.TemporaryDirectory(prefix='growth-demo-') as folder:
        path=Path(folder)/('upload'+Path(name).suffix.lower());path.write_bytes(content)
        extraction=core.parse_document(path,name)
    return jsonify(document=dict(id='doc-'+uuid.uuid4().hex,name=Path(name).name,
        kind=extraction.get('kind','견적서'),status='검토 필요',
        date=datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat(),
        sha256=hashlib.sha256(content).hexdigest(),extraction=extraction))


@app.post('/api/guide/ask')
def ask():
    body=request.get_json();question=body.get('question','')
    if not isinstance(question,str) or not 0<len(question)<=1000:raise ValueError('질문은 1,000자 이내로 입력하세요.')
    sources=ai_guide.retrieve(question,body.get('page',''),body.get('history',[]),body.get('contextSources',[]))[:3]
    return jsonify(answer='가이드 검색 데모 · 생성형 AI 호출 없이 문서에서 찾은 안내입니다.\n\n'+
        '\n\n'.join(a['title']+'\n'+a['text'] for a in sources), sources=sources, fallback=True, usage={})


@app.get('/api/templates/<kind>.<fmt>')
def template(kind,fmt):
    from document_templates import export_template
    name,content=export_template(kind,fmt)
    return send_file(io.BytesIO(content),download_name=name,as_attachment=True)


@app.get('/')
def home():return send_from_directory(ROOT/'app','index.html')


@app.get('/<path:name>')
def asset(name):
    if name.startswith('api/'):return jsonify(error='지원하지 않는 데모 요청입니다.'),404
    return send_from_directory(ROOT/'app',name)


if __name__=='__main__':app.run(host='127.0.0.1',port=8791)
