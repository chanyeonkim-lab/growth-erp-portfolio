import hashlib
import re
import unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo


def product(code, name, spec, unit, sale_price, source):
    return {
        'code': code,
        'name': name,
        'spec': spec,
        'unit': unit,
        'salePrice': sale_price,
        'purchasePrice': 0,
        'trackStock': True,
        'active': True,
        'minStock': 0,
        'notes': f'첨부 견적 추출 · {source}',
    }


P = product
PRODUCTS = [product('DEMO-PART-%02d'%i, '데모 전기 부품 %02d'%i, '가상 규격', 'EA', i*12000, 'synthetic catalog') for i in range(1,25)]
REVIEW = []
EXCLUDED = []

codes = [item['code'].casefold() for item in PRODUCTS]
assert len(codes) == len(set(codes)), 'duplicate code'


def _normalized(value):
    value = unicodedata.normalize('NFC', str(value or '')).casefold().replace('×', 'x')
    return re.sub(r'[^0-9a-z가-힣]+', '', value)


def _fingerprint(item):
    return _normalized(item.get('name')) + ':' + _normalized(item.get('spec'))


def merge_products(state):
    """Add the reviewed attachment catalog without changing existing products."""
    if state.get('ledgerVersion') != 2:
        return {'added': 0, 'skipped': len(PRODUCTS)}
    state.setdefault('products', [])
    state.setdefault('erpAudit', [])
    known_codes = {_normalized(item.get('code')) for item in state['products']}
    known_items = {_fingerprint(item) for item in state['products']}
    known_ids = {item['id'] for item in state['products']}
    imported_ids = {event['id'] for event in state['erpAudit']}
    added = 0
    timestamp = datetime.now(ZoneInfo('Asia/Seoul')).isoformat(timespec='seconds')
    for source in PRODUCTS:
        code_key = _normalized(source['code'])
        item_key = _fingerprint(source)
        digest = hashlib.sha256(source['code'].encode()).hexdigest()[:24]
        product_id = 'product-import-' + digest
        audit_id = 'audit-import-' + digest
        # Stable identity survives edits; retained import history also records
        # that a now-deleted product must not be recreated by a subsequent read.
        if (product_id in known_ids or audit_id in imported_ids
                or code_key in known_codes or item_key in known_items):
            continue
        item = {
            **source,
            'id': product_id,
            'supplierId': '',
        }
        state['products'].append(item)
        state['erpAudit'].append({
            'id': audit_id,
            'at': timestamp,
            'action': '첨부 견적 품목 등록',
            'entityId': item['id'],
            'detail': source['code'],
            'actorUserId': 'system-import',
        })
        known_codes.add(code_key)
        known_items.add(item_key)
        known_ids.add(product_id)
        imported_ids.add(audit_id)
        added += 1
    return {'added': added, 'skipped': len(PRODUCTS) - added}
