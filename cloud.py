"""Authenticated Vercel app: Postgres records and private Supabase documents."""
import base64
import hashlib
import io
import json
import os
import re
import secrets
import tempfile
import time
import uuid
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
from flask import Flask, Response, g, jsonify, request, send_file, send_from_directory
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash

import server as core
import private_storage
import workflow
import tax_provider
import tax_workflow
import ai_guide
from catalog_20261011 import merge_products

MAX_FILE = 3 * 1024 * 1024  # Base64 plus JSON must stay below Vercel's 4.5 MB limit.
MAX_STATE = 3 * 1024 * 1024
COOKIE = '__Host-growth_session'
SESSION_SECONDS = 7 * 24 * 3600
app = Flask(__name__, static_folder=None)
app.config.update(MAX_CONTENT_LENGTH=4_300_000)
app.json.ensure_ascii = False


def db():
    url = os.environ.get('DATABASE_URL')
    if not url:
        raise RuntimeError('영구 저장소 연결을 준비 중입니다.')
    return psycopg.connect(url, connect_timeout=10, sslmode='require')


def setup_schema():
    with db() as con:
        for sql in (
            'CREATE TABLE IF NOT EXISTS growth_workspaces (scope TEXT PRIMARY KEY, revision INTEGER NOT NULL, body TEXT NOT NULL)',
            'CREATE TABLE IF NOT EXISTS growth_files (scope TEXT NOT NULL, id TEXT NOT NULL, digest TEXT NOT NULL, name TEXT NOT NULL, body BYTEA NOT NULL, PRIMARY KEY(scope,id), UNIQUE(scope,digest))',
            "ALTER TABLE growth_files ADD COLUMN IF NOT EXISTS storage_provider TEXT NOT NULL DEFAULT 'postgres'",
            'ALTER TABLE growth_files ADD COLUMN IF NOT EXISTS storage_path TEXT',
            'CREATE TABLE IF NOT EXISTS growth_users (username TEXT PRIMARY KEY, display_name TEXT NOT NULL, password_hash TEXT, setup_hash TEXT, setup_expires BIGINT)',
            'ALTER TABLE growth_users ADD COLUMN IF NOT EXISTS email TEXT',
            'CREATE TABLE IF NOT EXISTS growth_sessions (token_hash TEXT PRIMARY KEY, username TEXT NOT NULL REFERENCES growth_users(username), expires BIGINT NOT NULL)',
            'CREATE TABLE IF NOT EXISTS growth_limits (key TEXT PRIMARY KEY, hits INTEGER NOT NULL, resets BIGINT NOT NULL)',
        ):
            con.execute(sql)


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def active_workspace():
    return 'live' if os.environ.get('GROWTH_ENABLE_LIVE') == '1' else 'demo'


def scope():
    active = active_workspace()
    workspace = request.args.get('workspace', active)
    if workspace not in ('live', 'demo'):
        raise ValueError('작업 공간을 확인해 주세요.')
    if workspace != active:
        return None
    namespace = os.environ.get('GROWTH_DATA_NAMESPACE', 'business' if active == 'live' else 'learning')
    return namespace + ':' + workspace


def state_row(con, key, lock=False):
    initial = core.demo_state() if key.endswith(':demo') else core.empty_state()
    con.execute('INSERT INTO growth_workspaces VALUES (%s,0,%s) ON CONFLICT(scope) DO NOTHING',
                (key, json.dumps(initial, ensure_ascii=False)))
    return con.execute('SELECT revision,body FROM growth_workspaces WHERE scope=%s' + (' FOR UPDATE' if lock else ''), (key,)).fetchone()


def read_state(key):
    with db() as con:
        revision, body = state_row(con, key)
        state = workflow.normalize(json.loads(body))
        # Import into whichever workspace this deployment actively serves.
        # The production domain can temporarily remain on the legacy ``demo``
        # workspace until its existing business records are migrated to live.
        if key.endswith((':live', ':demo')):
            imported = merge_products(state)
            if imported['added']:
                core.validate_state(state)
                raw = json.dumps(state, ensure_ascii=False)
                if len(raw.encode()) > MAX_STATE:
                    raise ValueError('장부 크기 한도에 도달했습니다. 관리자에게 문의해 주세요.')
                result = con.execute(
                    'UPDATE growth_workspaces SET body=%s, revision=revision+1 WHERE scope=%s AND revision=%s',
                    (raw, key, revision),
                )
                if result.rowcount == 1:
                    revision += 1
                else:
                    revision, body = state_row(con, key)
                    state = workflow.normalize(json.loads(body))
    return {'revision': revision, 'state': state}


def save_state(con, key, state, revision):
    core.validate_state(state)
    # Metadata is editable, but file identity must remain server-owned.
    _, previous = state_row(con, key)
    workflow.guard(json.loads(previous), state)
    old = {d['id']: d for d in json.loads(previous)['documents']}
    if set(old) != {d['id'] for d in state['documents']}:
        raise ValueError('문서는 문서 업로드 기능으로 등록해 주세요.')
    for doc in state['documents']:
        if any(doc.get(k) != old[doc['id']].get(k) for k in ('storedName', 'sha256', 'name')):
            raise ValueError('원본 파일 정보를 변경할 수 없습니다.')
    raw = json.dumps(state, ensure_ascii=False)
    if len(raw.encode()) > MAX_STATE:
        raise ValueError('장부 크기 한도에 도달했습니다. 백업 후 관리자에게 문의해 주세요.')
    result = con.execute('UPDATE growth_workspaces SET body=%s, revision=revision+1 WHERE scope=%s AND revision=%s', (raw, key, revision))
    if result.rowcount != 1:
        return False
    return True


def cookie_token():
    return request.cookies.get(COOKIE, '')


def start_session(username):
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    with db() as con:
        con.execute('DELETE FROM growth_sessions WHERE expires<%s', (now,))
        con.execute('INSERT INTO growth_sessions VALUES (%s,%s,%s)', (sha(token), username, now + SESSION_SECONDS))
    response = jsonify(ok=True)
    response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, secure=True, httponly=True, samesite='Strict', path='/')
    return response


def rate_limit(username):
    now = int(time.time())
    # Username limiter also applies to nonexistent users; per-IP limiter limits scanning.
    ip = request.headers.get('x-vercel-forwarded-for', request.remote_addr or 'unknown').split(',')[0]
    keys = [('user:' + sha(username), 10), ('ip:' + sha(ip), 40)]
    limited = False
    with db() as con:
        con.execute('DELETE FROM growth_limits WHERE resets<%s', (now,))
        for key, maximum in keys:
            row = con.execute('INSERT INTO growth_limits VALUES (%s,1,%s) ON CONFLICT(key) DO UPDATE SET hits=growth_limits.hits+1 RETURNING hits', (key, now + 900)).fetchone()
            limited = limited or row[0] > maximum
    return limited


@app.before_request
def protect():
    if request.path == '/api/health':
        return None
    if request.method not in ('GET', 'HEAD', 'POST'):
        return jsonify(error='지원하지 않는 요청입니다.'), 405
    if request.method == 'POST':
        # Same-origin JSON requests only. No CORS; require origin even for login.
        if request.headers.get('Origin') != 'https://' + request.host:
            return jsonify(error='허용되지 않은 요청입니다.'), 403
        if request.mimetype != 'application/json':
            return jsonify(error='JSON 요청이 필요합니다.'), 415
    if request.path in ('/login', '/login.js', '/style.css'):
        return None
    if request.path in ('/api/auth/login', '/api/auth/activate'):
        return None
    token = cookie_token()
    if not token or len(token) > 128:
        return unauthorized()
    with db() as con:
        user = con.execute('SELECT u.username,u.display_name FROM growth_sessions s JOIN growth_users u ON s.username=u.username WHERE s.token_hash=%s AND s.expires>%s', (sha(token), int(time.time()))).fetchone()
    if not user:
        return unauthorized()
    g.user = {'username': user[0], 'displayName': user[1]}
    if request.path.startswith('/api/') and not request.path.startswith('/api/auth/') and request.path != '/api/config':
        g.scope = scope()
        if g.scope is None:
            message = '현재는 실제 장부를 사용하는 배포입니다. 새로고침해 주세요.' if active_workspace() == 'live' else '운영 장부가 활성화되지 않았습니다. 관리자에게 문의해 주세요.'
            return jsonify(error=message), 403


def unauthorized():
    if request.path.startswith('/api/'):
        return jsonify(error='로그인이 필요합니다.'), 401
    return Response('', 303, {'Location': '/login'})


@app.after_request
def headers(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    return response


@app.errorhandler(Exception)
def failure(error):
    if isinstance(error, HTTPException):
        return jsonify(error='요청 크기 또는 주소를 확인해 주세요.'), error.code
    if isinstance(error, (ValueError, KeyError, TypeError)):
        return jsonify(error=str(error) if isinstance(error, ValueError) else '입력 형식을 확인해 주세요.'), 400
    # Never expose DSNs, SQL statements, or file contents in client errors or logs.
    app.logger.error('Request failed: %s', type(error).__name__)
    return jsonify(error='서버 연결을 확인 중입니다. 잠시 후 다시 시도해 주세요.'), 503


@app.get('/api/health')
def health():
    return jsonify(service='Growth ERP', status='ok', mode='cloud')


@app.post('/api/auth/login')
def login():
    body = request.get_json()
    username = str(body.get('username', '')).strip().lower()[:100]
    password = str(body.get('password', ''))
    if len(password) > 256 or rate_limit(username):
        return jsonify(error='로그인 시도가 많습니다. 15분 뒤 다시 시도해 주세요.'), 429
    with db() as con:
        row = con.execute('SELECT password_hash FROM growth_users WHERE username=%s', (username,)).fetchone()
    valid = check_password_hash(row[0] if row and row[0] else dummy_hash(), password)
    if not row or not row[0] or not valid:
        return jsonify(error='아이디 또는 비밀번호를 확인해 주세요.'), 401
    return start_session(username)


@lru_cache(maxsize=1)
def dummy_hash():
    return generate_password_hash(secrets.token_urlsafe(32))


@app.post('/api/auth/activate')
def activate():
    body = request.get_json()
    username = str(body.get('username', '')).strip().lower()[:100]
    token = str(body.get('token', ''))
    password = str(body.get('password', ''))
    if rate_limit(username):
        return jsonify(error='15분 뒤 다시 시도해 주세요.'), 429
    if not 12 <= len(password) <= 256:
        raise ValueError('비밀번호는 12자 이상으로 입력해 주세요.')
    if not token or len(token) > 128:
        raise ValueError('계정 설정 링크를 확인해 주세요.')
    hashed = generate_password_hash(password)
    with db() as con:
        result = con.execute('UPDATE growth_users SET password_hash=%s,setup_hash=NULL,setup_expires=NULL WHERE username=%s AND setup_hash=%s AND setup_expires>%s', (hashed, username, sha(token), int(time.time())))
        if result.rowcount != 1:
            return jsonify(error='계정 설정 링크가 만료되었거나 이미 사용되었습니다.'), 400
        con.execute('DELETE FROM growth_sessions WHERE username=%s', (username,))
    return start_session(username)


@app.post('/api/auth/logout')
def logout():
    with db() as con:
        con.execute('DELETE FROM growth_sessions WHERE token_hash=%s', (sha(cookie_token()),))
    response = jsonify(ok=True)
    response.delete_cookie(COOKIE, secure=True, httponly=True, samesite='Strict', path='/')
    return response


@app.post('/api/auth/password')
def password_change():
    body = request.get_json()
    password = str(body.get('password', ''))
    if not 12 <= len(password) <= 256:
        raise ValueError('비밀번호는 12자 이상으로 입력해 주세요.')
    if rate_limit(g.user['username']):
        return jsonify(error='15분 뒤 다시 시도해 주세요.'), 429
    with db() as con:
        old = con.execute('SELECT password_hash FROM growth_users WHERE username=%s FOR UPDATE', (g.user['username'],)).fetchone()[0]
        if not check_password_hash(old, str(body.get('currentPassword', ''))[:256]):
            return jsonify(error='현재 비밀번호를 확인해 주세요.'), 400
        con.execute('UPDATE growth_users SET password_hash=%s WHERE username=%s', (generate_password_hash(password), g.user['username']))
        con.execute('DELETE FROM growth_sessions WHERE username=%s', (g.user['username'],))
    return start_session(g.user['username'])


@app.get('/api/config')
def config():
    return jsonify(cloud=True, erpVersion=2, user=g.user, liveEnabled=active_workspace() == 'live', workspace=active_workspace(), maxUploadBytes=MAX_FILE, fileStorage=private_storage.provider(),
                   aiConnected=bool(os.environ.get('OPENAI_API_KEY')), model=os.environ.get('OPENAI_MODEL', 'gpt-4.1-mini'), gmailConnected=False, bankConnected=False,
                   tax=tax_provider.settings(active_workspace()))


@app.get('/api/guide')
def guide_catalog():
    return jsonify(ai_guide.catalog(ai_guide.UsageStore(db, postgres=True)))


@app.post('/api/guide/ask')
def guide_ask():
    try:
        return jsonify(ai_guide.answer(request.get_json(), g.user['username'], ai_guide.UsageStore(db, postgres=True)))
    except ai_guide.GuideError as error:
        return jsonify(error=str(error)), error.status


@app.route('/api/state', methods=['GET', 'POST'])
def state():
    if request.method == 'GET':
        return jsonify(read_state(g.scope))
    body = request.get_json()
    if isinstance(body.get('revision'), bool) or not isinstance(body.get('revision'), int):
        raise ValueError('저장 버전을 확인해 주세요.')
    with db() as con:
        if not save_state(con, g.scope, body['state'], body['revision']):
            return jsonify(error='다른 창에서 데이터가 바뀌었습니다. 새로고침한 뒤 다시 저장해 주세요.'), 409
    return jsonify(revision=body['revision'] + 1)


@app.post('/api/workflow')
def sales_workflow():
    body = request.get_json()
    if type(body.get('revision')) is not int:
        raise ValueError('저장 버전을 확인해 주세요.')
    deleted_files = []
    with db() as con:
        revision, raw = state_row(con, g.scope, lock=True)
        if revision != body['revision']:
            return jsonify(error='다른 창에서 데이터가 바뀌었습니다. 새로고침한 뒤 다시 저장해 주세요.'), 409
        current = workflow.normalize(json.loads(raw))
        result, content = workflow.apply(current, body, active_workspace(), actor=g.user['username'])
        core.validate_state(current)
        raw = json.dumps(current, ensure_ascii=False)
        if len(raw.encode()) > MAX_STATE:
            raise ValueError('장부 크기 한도에 도달했습니다. 관리자에게 문의해 주세요.')
        if content:
            doc = next(d for d in current['documents'] if d['id'] == result['documentId'])
            storage_provider = private_storage.provider()
            storage_path = None
            file_body = content
            if storage_provider == 'supabase':
                storage_path = private_storage.upload(g.scope, doc['id'], Path(doc['name']).suffix.lower(), content)
                file_body = b''
            con.execute('INSERT INTO growth_files (scope,id,digest,name,body,storage_provider,storage_path) VALUES (%s,%s,%s,%s,%s,%s,%s)',
                        (g.scope, doc['id'], doc['sha256'], doc['name'], file_body, storage_provider, storage_path))
        deleted_ids = result.get('deletedDocumentIds', [])
        if deleted_ids:
            deleted_files = con.execute('DELETE FROM growth_files WHERE scope=%s AND id=ANY(%s) RETURNING id,storage_provider,storage_path',
                                        (g.scope, deleted_ids)).fetchall()
        con.execute('UPDATE growth_workspaces SET body=%s,revision=revision+1 WHERE scope=%s', (raw, g.scope))
    for ident, storage_provider, storage_path in deleted_files:
        if storage_provider == 'supabase' and storage_path:
            try:
                private_storage.delete(g.scope, ident, storage_path)
            except Exception:
                app.logger.exception('Deleted document storage cleanup failed')
    return jsonify(**result, revision=revision+1, state=current)


def persist_tax(con, current):
    core.validate_state(current)
    raw = json.dumps(current, ensure_ascii=False)
    if len(raw.encode()) > MAX_STATE - 50000:
        raise ValueError('세금계산서 이력을 저장할 공간이 부족합니다. 관리자에게 문의해 주세요.')
    con.execute('UPDATE growth_workspaces SET body=%s,revision=revision+1 WHERE scope=%s', (raw,g.scope))


@app.post('/api/tax')
def tax_integration():
    body = request.get_json()
    if type(body.get('revision')) is not int or not isinstance(body.get('data',{}),dict):
        raise ValueError('요청 형식과 저장 버전을 확인해 주세요.')
    with db() as con:
        revision,raw = state_row(con,g.scope,lock=True)
        if revision != body['revision']:
            return jsonify(error='다른 창에서 데이터가 바뀌었습니다. 새로고침해 주세요.'),409
        current = workflow.normalize(json.loads(raw))
        if current.get('ledgerVersion') == 2:
            raise ValueError('전체 거래 화면에서 전표를 관리해 주세요.')
        operation = tax_workflow.prepare(current,body,active_workspace())
        persist_tax(con,current)
    # Committed outbox survives client disconnect, process death, or unknown response.
    response = tax_provider.execute(operation)
    with db() as con:
        revision,raw = state_row(con,g.scope,lock=True)
        current = workflow.normalize(json.loads(raw))
        current,result = tax_workflow.complete(current,operation,response,core.validate_state)
        persist_tax(con,current)
    return jsonify(**result, revision=revision+1,state=current)


@app.post('/api/upload')
def upload():
    body = request.get_json()
    name = Path(str(body['name'])).name[:240]
    extension = Path(name).suffix.lower()
    if extension not in ('.xlsx', '.pdf'):
        raise ValueError('XLSX 또는 PDF 파일을 올려 주세요.')
    content = base64.b64decode(body['content'], validate=True)
    if not content or len(content) > MAX_FILE:
        raise ValueError('웹 버전에서는 3MB 이하 문서를 사용해 주세요.')
    digest = hashlib.sha256(content).hexdigest()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / ('document' + extension)
        path.write_bytes(content)
        extraction = core.parse_document(path, name)
    with db() as con:
        revision, raw = state_row(con, g.scope, lock=True)
        current = json.loads(raw)
        existing = next((d for d in current['documents'] if d.get('sha256') == digest), None)
        if existing:
            return jsonify(duplicate=True, document=existing)
        ident = 'doc-' + uuid.uuid4().hex
        doc = dict(id=ident, name=name, storedName=ident + extension, sha256=digest, date=datetime.now(ZoneInfo('Asia/Seoul')).date().isoformat(), kind=extraction['kind'], status='확인 필요', extraction=extraction, accountId='', orderId='')
        current['documents'].append(doc)
        raw = json.dumps(current, ensure_ascii=False)
        if len(raw.encode()) > MAX_STATE:
            raise ValueError('장부 크기 한도에 도달했습니다. 관리자에게 문의해 주세요.')
        storage_provider = private_storage.provider()
        storage_path = None
        file_body = content
        if storage_provider == 'supabase':
            storage_path = private_storage.upload(g.scope, ident, extension, content)
            file_body = b''
        # SQL row and document metadata commit together. Supabase object names are unique.
        # If a DB commit has an unknown outcome, retain the private object for recovery.
        con.execute('INSERT INTO growth_files (scope,id,digest,name,body,storage_provider,storage_path) VALUES (%s,%s,%s,%s,%s,%s,%s)', (g.scope, ident, digest, name, file_body, storage_provider, storage_path))
        con.execute('UPDATE growth_workspaces SET body=%s,revision=revision+1 WHERE scope=%s', (raw, g.scope))
    return jsonify(duplicate=False, document=doc)


def file_row(ident):
    with db() as con:
        row = con.execute('SELECT name,body,storage_provider,storage_path,digest FROM growth_files WHERE scope=%s AND id=%s', (g.scope, ident)).fetchone()
    if not row:
        raise ValueError('문서를 찾을 수 없습니다.')
    if row[2] == 'supabase':
        content = private_storage.download(g.scope, ident, row[3], row[4])
    elif row[2] == 'postgres':
        content = bytes(row[1])
    else:
        raise private_storage.StorageUnavailable('원본 파일 저장소를 확인해 주세요.')
    return row[0], content


@app.get('/api/documents/<ident>/file')
def document_file(ident):
    name, content = file_row(ident)
    return send_file(io.BytesIO(content), download_name=name, as_attachment=request.args.get('download') == '1')


@app.get('/api/templates/<kind>.<fmt>')
def template_file(kind,fmt):
    from document_templates import export_template
    name,content=export_template(kind,fmt,read_state(g.scope)['state']['settings'])
    return send_file(io.BytesIO(content),download_name=name,as_attachment=True)


@app.post('/api/documents/<ident>/analyze')
def analyze(ident):
    if request.get_json().get('allowExternal') is not True:
        raise ValueError('AI 문서 전송을 선택해 주세요.')
    name, content = file_row(ident)
    current = read_state(g.scope)
    doc = next(d for d in current['state']['documents'] if d['id'] == ident)
    if doc.get('generated'):
        raise ValueError('앱에서 생성한 문서는 원본 기록에서 확인해 주세요.')
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / ('document' + Path(name).suffix.lower())
        path.write_bytes(content)
        result = core.analyze_ai(path, doc)
    with db() as con:
        revision, raw = state_row(con, g.scope, lock=True)
        state = json.loads(raw)
        found = next(d for d in state['documents'] if d['id'] == ident)
        found.update(extraction=result, kind=result['kind'])
        save_state(con, g.scope, state, revision)
    return jsonify(document=found)


@app.get('/api/backup-manifest')
def backup_manifest():
    current = read_state(g.scope)
    return jsonify(version=1, workspace=request.args.get('workspace', active_workspace()), **current)


@app.get('/login')
def login_page():
    return send_from_directory(core.ROOT / 'app', 'login.html')


@app.get('/')
def home():
    return send_from_directory(core.ROOT / 'app', 'index.html')


@app.get('/<path:name>')
def assets(name):
    # Only application assets are routable, never deployment source or runtime data.
    if name not in ('guide.js', 'lookup.js', 'drafts.js', 'erp.js', 'evidence.js', 'erp-domain.js', 'statements.js', 'tax.js', 'workflow.js', 'app.js', 'domain.js', 'style.css', 'login.js', 'vendor/fflate.js'):
        return jsonify(error='페이지를 찾을 수 없습니다.'), 404
    return send_from_directory(core.ROOT / 'app', name)
