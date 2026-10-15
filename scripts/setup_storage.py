"""Create or verify a private Supabase bucket; initialize additive SQL columns."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cloud
import private_storage


def main():
    _, _, bucket = private_storage.settings()
    with private_storage.storage_client() as client:
        buckets = client.list_buckets()
        existing = next((item for item in buckets if item.id == bucket), None)
        if existing is None:
            client.create_bucket(bucket, options={'public': False, 'file_size_limit': private_storage.MAX_FILE,
                                                  'allowed_mime_types': list(private_storage.MIMES.values())})
        private_storage.assert_private(client)
    cloud.setup_schema()
    print('Private document bucket and database schema are ready. No credentials printed.')


if __name__ == '__main__':
    main()
