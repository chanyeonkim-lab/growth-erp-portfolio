import hashlib
import os
import unittest
from contextlib import contextmanager
from unittest.mock import patch

import httpx
from storage3 import SyncStorageClient
import private_storage as storage


class PrivateStorageTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'SUPABASE_URL': 'https://test-project.supabase.co',
                                          'SUPABASE_SERVICE_ROLE_KEY': 'test-server-only-key',
                                          'GROWTH_STORAGE_BUCKET': 'growth-documents'}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.ident = 'doc-' + 'a' * 32
        self.scope = 'test:demo'
        self.content = b'%PDF-1.4\nsynthetic test data'
        self.calls = []
        self.public = False
        self.downloaded = self.content
        self.upload_status = 200

        def transport(request):
            self.calls.append(request)
            if request.url.path == '/storage/v1/bucket/growth-documents':
                return httpx.Response(200, json={'id': 'growth-documents', 'name': 'growth-documents',
                    'owner': '', 'public': self.public, 'created_at': '2026-10-03T00:00:00Z',
                    'updated_at': '2026-10-03T00:00:00Z', 'file_size_limit': storage.MAX_FILE,
                    'allowed_mime_types': list(storage.MIMES.values())})
            if request.method == 'POST':
                if self.upload_status != 200:
                    return httpx.Response(self.upload_status, json={'statusCode': '403', 'error': 'Forbidden', 'message': 'test failure'})
                return httpx.Response(200, json={'Key': request.url.path})
            if request.method == 'DELETE':
                return httpx.Response(200, json=[])
            return httpx.Response(200, content=self.downloaded)

        @contextmanager
        def client():
            headers = {'apikey': 'test-server-only-key', 'Authorization': 'Bearer test-server-only-key'}
            with httpx.Client(transport=httpx.MockTransport(transport), headers=headers, follow_redirects=False) as http:
                yield SyncStorageClient('https://test-project.supabase.co/storage/v1/', headers, http_client=http)

        self.mock_client = patch.object(storage, 'storage_client', client)
        self.mock_client.start()
        self.addCleanup(self.mock_client.stop)

    def test_private_upload_uses_opaque_path_and_does_not_overwrite(self):
        path = storage.upload(self.scope, self.ident, '.pdf', self.content)
        self.assertNotIn(self.scope, path)
        request = self.calls[-1]
        self.assertEqual(request.headers['x-upsert'], 'false')
        self.assertIn(b'application/pdf', request.content)
        self.assertTrue(path.endswith(self.ident + '.pdf'))

    def test_public_bucket_is_rejected_before_any_file_transfer(self):
        self.public = True
        with self.assertRaises(storage.StorageUnavailable):
            storage.upload(self.scope, self.ident, '.pdf', self.content)
        self.assertEqual(len(self.calls), 1)

    def test_download_checks_digest_and_returns_bytes_only(self):
        path = storage.object_path(self.scope, self.ident, '.pdf')
        digest = hashlib.sha256(self.content).hexdigest()
        self.assertEqual(storage.download(self.scope, self.ident, path, digest), self.content)
        self.downloaded = b'changed data'
        with self.assertRaises(storage.StorageUnavailable):
            storage.download(self.scope, self.ident, path, digest)

    def test_delete_uses_the_verified_private_object_path(self):
        path = storage.object_path(self.scope, self.ident, '.pdf')
        storage.delete(self.scope, self.ident, path)
        self.assertEqual(self.calls[-1].method, 'DELETE')
        with self.assertRaises(storage.StorageUnavailable):
            storage.delete(self.scope, self.ident, '../../other.pdf')

    def test_other_workspace_and_traversal_paths_never_reach_storage(self):
        for path in (storage.object_path('other:live', self.ident, '.pdf'), '../../private.pdf', 'https://attacker.example/a.pdf'):
            with self.assertRaises(storage.StorageUnavailable):
                storage.download(self.scope, self.ident, path, 'digest')
        self.assertEqual(self.calls, [])

    def test_upload_failure_is_not_reported_as_success(self):
        self.upload_status = 403
        with self.assertRaises(Exception):
            storage.upload(self.scope, self.ident, '.pdf', self.content)

    def test_missing_credentials_and_non_supabase_hosts_fail_closed(self):
        with patch.dict(os.environ, {'SUPABASE_URL': 'https://attacker.example'}):
            with self.assertRaises(storage.StorageUnavailable): storage.settings()
        with patch.dict(os.environ, {'SUPABASE_SERVICE_ROLE_KEY': ''}):
            with self.assertRaises(storage.StorageUnavailable): storage.settings()

    def test_native_integration_aliases_do_not_read_neon_credentials(self):
        with patch.dict(os.environ, {'FILESTORE_SUPABASE_URL': 'https://test-project.supabase.co',
                                    'FILESTORE_SUPABASE_SERVICE_ROLE_KEY': 'test-server-only-key',
                                    'DATABASE_URL': 'postgresql://not-a-storage-key'}, clear=True):
            self.assertEqual(storage.settings()[1], 'test-server-only-key')

    def test_size_rejection_does_not_contact_storage(self):
        with self.assertRaises(storage.StorageUnavailable):
            storage.upload(self.scope, self.ident, '.pdf', b'x' * (storage.MAX_FILE + 1))
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
