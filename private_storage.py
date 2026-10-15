"""Server-only access to the application's private Supabase document bucket."""
import hashlib
import os
import re
from contextlib import contextmanager
from urllib.parse import urlparse

import httpx
from storage3 import SyncStorageClient

MAX_FILE = 3 * 1024 * 1024
MIMES = {'.pdf': 'application/pdf', '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}


class StorageUnavailable(RuntimeError):
    pass


def provider():
    value = os.environ.get('GROWTH_FILE_STORAGE', 'postgres')
    if value not in ('postgres', 'supabase'):
        raise StorageUnavailable('파일 저장소 설정을 확인해 주세요.')
    return value


def settings():
    url = (os.environ.get('SUPABASE_URL') or os.environ.get('FILESTORE_SUPABASE_URL') or
           os.environ.get('FILESTORE_NEXT_PUBLIC_SUPABASE_URL') or '').rstrip('/')
    key = (os.environ.get('SUPABASE_SECRET_KEY') or os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or
           os.environ.get('FILESTORE_SUPABASE_SECRET_KEY') or os.environ.get('FILESTORE_SUPABASE_SERVICE_ROLE_KEY'))
    bucket = os.environ.get('GROWTH_STORAGE_BUCKET', 'growth-documents')
    parsed = urlparse(url)
    if (parsed.scheme != 'https' or not (parsed.hostname or '').endswith('.supabase.co') or
            parsed.username or parsed.password or parsed.port or parsed.path or parsed.query or parsed.fragment or
            not key or not re.fullmatch(r'[a-z0-9][a-z0-9-]{2,62}', bucket)):
        raise StorageUnavailable('Supabase 파일 저장소 연결을 확인해 주세요.')
    return url, key, bucket


@contextmanager
def storage_client():
    url, key, _ = settings()
    headers = {'apikey': key, 'Authorization': 'Bearer ' + key}
    # Credentials remain on the server and are never forwarded across redirects.
    with httpx.Client(headers=headers, timeout=30, follow_redirects=False) as http:
        yield SyncStorageClient(url + '/storage/v1/', headers, http_client=http)


def assert_private(client):
    _, _, bucket = settings()
    info = client.get_bucket(bucket)
    if info.public is not False:
        raise StorageUnavailable('파일 저장소의 비공개 설정을 확인해 주세요.')
    return bucket


def object_path(scope, ident, extension):
    if not re.fullmatch(r'doc-[a-f0-9]{32}', ident) or extension not in MIMES:
        raise StorageUnavailable('원본 파일 정보를 확인해 주세요.')
    return 'files/' + hashlib.sha256(scope.encode()).hexdigest() + '/' + ident + extension


def verify_path(scope, ident, path):
    extension = '.' + str(path).rsplit('.', 1)[-1]
    if path != object_path(scope, ident, extension):
        raise StorageUnavailable('원본 파일 연결을 확인해 주세요.')


def upload(scope, ident, extension, content):
    if not content or len(content) > MAX_FILE:
        raise StorageUnavailable('3MB 이하 문서를 사용해 주세요.')
    path = object_path(scope, ident, extension)
    with storage_client() as client:
        bucket = assert_private(client)
        client.from_(bucket).upload(path, content, file_options={
            'content-type': MIMES[extension], 'cache-control': '0', 'upsert': 'false',
        })
    return path


def download(scope, ident, path, digest):
    verify_path(scope, ident, path)
    with storage_client() as client:
        bucket = assert_private(client)
        content = client.from_(bucket).download(path)
    if not content or len(content) > MAX_FILE or hashlib.sha256(content).hexdigest() != digest:
        raise StorageUnavailable('원본 파일의 무결성을 확인하지 못했습니다.')
    return content


def delete(scope, ident, path):
    verify_path(scope, ident, path)
    with storage_client() as client:
        bucket = assert_private(client)
        client.from_(bucket).remove([path])
