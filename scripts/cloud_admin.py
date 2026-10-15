"""Create a one-use account setup link. Passwords are chosen by the user in the app."""
import argparse
import html
import os
import re
import secrets
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cloud


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--username', required=True)
    parser.add_argument('--display-name', required=True)
    parser.add_argument('--email', required=True)
    parser.add_argument('--reset', action='store_true')
    args = parser.parse_args()
    if not re.fullmatch(r'[a-z0-9_-]{3,40}', args.username) or not args.base_url.startswith('https://'):
        raise SystemExit('Use an HTTPS URL and a lowercase username.')
    email = args.email.strip().lower()
    if len(email) > 254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
        raise SystemExit('Provide a valid account contact email.')
    cloud.setup_schema()
    token = secrets.token_urlsafe(32)
    expires = int(time.time()) + 86400
    with cloud.db() as con:
        found = con.execute('SELECT password_hash FROM growth_users WHERE username=%s', (args.username,)).fetchone()
        if found and found[0] and not args.reset:
            raise SystemExit('Account already active. Use --reset only to intentionally reset it.')
        con.execute('INSERT INTO growth_users (username,display_name,password_hash,setup_hash,setup_expires,email) VALUES (%s,%s,NULL,%s,%s,%s) ON CONFLICT(username) DO UPDATE SET setup_hash=EXCLUDED.setup_hash,setup_expires=EXCLUDED.setup_expires,email=EXCLUDED.email', (args.username, args.display_name, cloud.sha(token), expires, email))
    url = args.base_url.rstrip('/') + '/login#' + urlencode({'setup': token, 'user': args.username})
    expires_label = datetime.fromtimestamp(expires, ZoneInfo('Asia/Seoul')).strftime('%Y-%m-%d %H:%M')
    page = '<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="referrer" content="no-referrer"><title>Growth ERP 계정 설정</title><body style="margin:0;background:#f3f5f8;color:#243247"><main style="max-width:580px;margin:80px auto;padding:32px;font:17px/1.8 system-ui;background:#fcfdff;border:1px solid #dfe5ed;border-radius:16px"><h1>Growth ERP 계정 설정</h1><p>' + html.escape(args.display_name) + ' · 아이디: <b>' + html.escape(args.username) + '</b><br>등록 이메일: ' + html.escape(email) + '</p><p>아래 버튼에서 12자 이상의 비밀번호를 직접 설정하세요.<br>설정 후에는 이메일 대신 위 아이디로 로그인하세요.</p><p><a style="display:inline-block;padding:12px 22px;background:#315d88;color:white;border-radius:10px;text-decoration:none" href="' + html.escape(url, quote=True) + '">비밀번호 설정하고 시작하기</a></p><p>사용 기한: ' + expires_label + ' (한국 시간)<br>이 링크는 한 번만 사용할 수 있어요. 계정 사용자에게만 전달하고, 설정 후 이 파일을 삭제해 주세요.</p></main></body></html>'
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(page)
    print('One-use setup link saved to local file; token not printed.')


if __name__ == '__main__':
    main()
