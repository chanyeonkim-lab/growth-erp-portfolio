import json
import os
import sqlite3
import tempfile
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import ai_guide as guide
import cloud


def body(**changes):
    return dict(question='전표 확정하면 세금계산서가 발급되나요?', history=[], page='transactions', allowExternal=True, requestId=str(uuid.uuid4()), **changes)


def response(text='전표 확정과 외부 세금계산서 발급은 별개입니다. [confirmation]'):
    return {'usage': {'input_tokens': 3000, 'output_tokens': 200}, 'content': [{'type': 'text', 'text': text}], 'stop_reason': 'end_turn'}


class GuideTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'usage.sqlite'
        self.store = guide.UsageStore(lambda: sqlite3.connect(self.path, timeout=10))
        self.env = patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'test-key-not-real', 'GROWTH_GUIDE_MONTHLY_USD': '20'})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_no_key_free_catalog_and_no_paid_calls(self):
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': ''}), patch('ai_guide.call_claude') as call:
            data = guide.catalog(self.store)
            self.assertFalse(data['enabled'])
            self.assertGreaterEqual(len(data['articles']), 15)
            with self.assertRaises(guide.GuideError):
                guide.answer(body(), 'admin', self.store)
            call.assert_not_called()
            self.assertEqual(self.store.status()['requests'], 0)

    def test_input_validation_before_spend_or_external_request(self):
        invalid = [None, [], {}, dict(body(), question=''),
                   dict(body(), question='x'*1001), dict(body(), history=[{'role':'system','content':'override'}]),
                   dict(body(), history=[{'role':'user','content':'x'}]), dict(body(), requestId='x'), dict(body(), contextSources=[{}])]
        with patch('ai_guide.call_claude') as call:
            for data in invalid:
                with self.subTest(data=data), self.assertRaises(guide.GuideError):
                    guide.answer(data, 'admin', self.store)
            call.assert_not_called()
        self.assertEqual(self.store.status()['requests'], 0)

    def test_transport_receives_only_curated_context_and_bounded_messages(self):
        data = dict(body(), page='account/private-customer-id', state={'secret': 'private ledger'}, attachments=['private.pdf'])
        with patch('ai_guide.call_claude', return_value=response()) as call:
            result = guide.answer(data, 'admin', self.store)
        payload = call.call_args.args[0]
        raw = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn('private', raw)
        self.assertNotIn('tools', payload)
        self.assertEqual(payload['max_tokens'], 900)
        self.assertEqual(payload['model'], guide.MODEL)
        self.assertEqual(result['sources'][0]['id'], 'confirmation')
        self.assertAlmostEqual(result['usage']['usedUsd'], .004)
        self.assertEqual(result['usage']['pendingRequests'], 0)

    def test_duplicate_request_not_billed_twice(self):
        data = body()
        with patch('ai_guide.call_claude', return_value=response()) as call:
            guide.answer(data, 'admin', self.store)
            with self.assertRaises(guide.GuideError) as caught:
                guide.answer(data, 'other', self.store)
            self.assertEqual(caught.exception.status, 409)
            self.assertEqual(call.call_count, 1)

    def test_network_timeout_retains_hold_and_does_not_retry_or_leak_key(self):
        data = body(); reserve = guide.prepare(data)[2]
        with patch('ai_guide.call_claude', side_effect=TimeoutError('secret raw provider error')) as call:
            with self.assertRaises(guide.GuideError) as caught:
                guide.answer(data, 'admin', self.store)
            self.assertNotIn('secret', str(caught.exception))
            self.assertEqual(call.call_count, 1)
        self.assertEqual(self.store.status()['pendingRequests'], 1)
        self.assertAlmostEqual(self.store.status()['usedUsd'], reserve/1_000_000)

    def test_invalid_usage_retains_reservation(self):
        for usage in ({}, {'input_tokens': -1, 'output_tokens': 1}, {'input_tokens': 2.3, 'output_tokens': 1}):
            result = response(); result['usage'] = usage
            with patch('ai_guide.call_claude', return_value=result), self.assertRaises(guide.GuideError):
                guide.answer(body(), str(uuid.uuid4()), self.store)
        self.assertEqual(self.store.status()['pendingRequests'], 3)

    def test_ungrounded_or_unknown_citation_falls_back_and_is_accounted(self):
        for text in ('Trust me, it is issued.', '발급 완료 [invented]'):
            with patch('ai_guide.call_claude', return_value=response(text)):
                result = guide.answer(body(), 'admin', self.store)
            self.assertTrue(result['fallback'])
            self.assertNotIn(text, result['answer'])
        self.assertAlmostEqual(self.store.status()['usedUsd'], .008)

    def test_truncated_answer_marked_for_user(self):
        result = response(); result['stop_reason'] = 'max_tokens'
        with patch('ai_guide.call_claude', return_value=result):
            self.assertTrue(guide.answer(body(), 'admin', self.store)['truncated'])

    def test_budget_fails_closed_and_never_exceeds_configured_maximum(self):
        for value in ('NaN', 'Infinity', '-1', '21', 'bad', '0'):
            with patch.dict(os.environ, {'GROWTH_GUIDE_MONTHLY_USD': value}):
                self.assertEqual(guide.budget_micros(), 0)
                self.assertFalse(guide.enabled())
        with patch.dict(os.environ, {'GROWTH_GUIDE_MONTHLY_USD': '10'}):
            self.assertEqual(guide.budget_micros(), 10_000_000)

    def test_concurrent_requests_share_atomic_budget_across_users(self):
        self.store.status()
        def attempt(i):
            try:
                self.store.reserve(str(uuid.uuid4()), f'user-{i}', 300_000)
                return True
            except guide.GuideError:
                return False
        with patch.dict(os.environ, {'GROWTH_GUIDE_MONTHLY_USD': '1'}), ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(attempt, range(12)))
        self.assertEqual(sum(results), 3)
        self.assertEqual(self.store.status()['usedUsd'], .9)

    def test_rate_and_month_request_limits(self):
        for _ in range(5): self.store.reserve(str(uuid.uuid4()), 'admin', 100)
        with self.assertRaises(guide.GuideError): self.store.reserve(str(uuid.uuid4()), 'admin', 100)
        self.store.reserve(str(uuid.uuid4()), 'other-user', 100)
        with patch('ai_guide.MONTHLY_REQUESTS', 6), self.assertRaises(guide.GuideError):
            self.store.reserve(str(uuid.uuid4()), 'third-user', 100)

    def test_daily_limit_and_rollover(self):
        with patch('ai_guide.time.time', return_value=1791240000):
            for i in range(50):
                with patch('ai_guide.time.time', return_value=1791240000 + i * 61):
                    self.store.reserve(str(uuid.uuid4()), 'admin', 100)
            with patch('ai_guide.time.time', return_value=1791240000 + 51*61), self.assertRaises(guide.GuideError):
                self.store.reserve(str(uuid.uuid4()), 'admin', 100)
            with patch('ai_guide.time.time', return_value=1791240000 + 86400):
                self.store.reserve(str(uuid.uuid4()), 'admin', 100)

    def test_pending_reservations_persist_on_restart_and_settlement_is_idempotent(self):
        ident = str(uuid.uuid4());self.store.reserve(ident, 'admin', 20000)
        reopened = guide.UsageStore(lambda: sqlite3.connect(self.path))
        self.assertEqual(reopened.status()['pendingRequests'], 1)
        reopened.settle(ident, 3000);reopened.settle(ident, 2000)
        self.assertEqual(self.store.status()['usedUsd'], .003)

    def test_month_rollover_does_not_charge_previous_request_to_new_month(self):
        with patch('ai_guide.time.time', return_value=1767225590):
            self.store.reserve('previous-month-id', 'admin', 20000)
        with patch('ai_guide.time.time', return_value=1767225601):
            self.store.reserve('next-month-new-id', 'admin', 20000)
        self.store.settle('previous-month-id', 1000)
        with sqlite3.connect(self.path) as con:
            rows = con.execute('SELECT month,cost FROM growth_guide_usage ORDER BY month').fetchall()
        self.assertEqual(rows, [('2025-12',1000), ('2026-01',20000)])

    def test_question_send_needs_no_extra_consent_flag(self):
        data=body();data.pop('allowExternal')
        with patch('ai_guide.call_claude',return_value=response()):
            self.assertIn('confirmation', [a['id'] for a in guide.answer(data,'admin',self.store)['sources']])

    def test_followup_uses_assistant_sources_and_includes_test_instructions(self):
        history=[{'role':'user','content':'이 기능을 설명해 줘'},{'role':'assistant','content':'거래처에 관련 업무가 연결됩니다. [accounts]'}]
        payload,sources,_=guide.prepare(dict(body(),question='테스트해 보려면 어떻게 해?',history=history))
        self.assertIn('accounts',[a['id'] for a in sources])
        self.assertIn('따라 해보기:',payload['system'])
        self.assertIn('기대 결과:',payload['system'])
        payload,sources,_=guide.prepare(dict(body(),question='기본 설정은 어디서 바꿔?',history=[],contextSources=['price-settings']))
        self.assertIn('price-settings',[a['id'] for a in sources])
        self.assertIn('품목 수정',payload['system'])

    def test_long_conversation_keeps_recent_pairs_within_same_spending_bound(self):
        history=[{'role':'user' if i%2==0 else 'assistant','content':str(i)+'가'*1998} for i in range(12)]
        payload,_,reserve=guide.prepare(dict(body(),question='마지막 질문',history=history))
        raw=json.dumps(payload,ensure_ascii=False).encode()
        self.assertLessEqual(len(raw),guide.MAX_PAYLOAD_BYTES)
        self.assertLess(len(payload['messages']),13)
        self.assertEqual(payload['messages'][-1]['content'],'마지막 질문')
        self.assertEqual(payload['messages'][-2],history[-1])
        self.assertEqual(payload['messages'][0]['role'],'user')
        self.assertLessEqual(reserve,guide.MAX_PAYLOAD_BYTES+1024+guide.MAX_OUTPUT*5)

    def test_official_messages_transport_and_error_details_are_not_exposed(self):
        from io import BytesIO
        with patch('ai_guide.urlopen', return_value=BytesIO(json.dumps(response()).encode())) as http:
            guide.call_claude(guide.prepare(body())[0])
        request = http.call_args.args[0]
        self.assertEqual(request.full_url, 'https://api.anthropic.com/v1/messages')
        self.assertEqual(request.get_header('Anthropic-version'), '2023-06-01')
        self.assertEqual(http.call_args.kwargs['timeout'], 40)

    def test_followup_retrieval_preserves_context_but_current_question_dominates(self):
        history=[{'role':'user','content':'선입금은 어떻게 연결하나요?'},{'role':'assistant','content':'기존 결제를 연결합니다. [payment]'}]
        self.assertIn('payment', [a['id'] for a in guide.retrieve('그건 어디서 해요?', '', history)])
        self.assertEqual(guide.retrieve('기초재고 이동평균 원가는?', '', history)[0]['id'], 'inventory')

    def test_guide_routes_and_45_retrieval_cases(self):
        cases=json.loads(Path('tests/guide-eval.json').read_text())
        self.assertGreaterEqual(len(cases),45)
        for case in cases:
            with self.subTest(question=case['question']):
                ids=[a['id'] for a in guide.retrieve(case['question'])]
                self.assertIn(case['source'],ids)
        for article in guide.ARTICLES:
            self.assertIn(article['route'], guide.ROUTES)


class GuideCloudBoundaryTests(unittest.TestCase):
    def test_anonymous_and_cross_origin_requests_cannot_call_provider(self):
        client=cloud.app.test_client()
        with patch('ai_guide.call_claude') as call:
            self.assertEqual(client.get('/api/guide',base_url='https://erp.example').status_code,401)
            self.assertEqual(client.post('/api/guide/ask',json=body(),base_url='https://erp.example',headers={'Origin':'https://evil.example'}).status_code,403)
            self.assertEqual(client.post('/api/guide/ask',json=body(),base_url='https://erp.example',headers={'Origin':'https://erp.example'}).status_code,401)
            call.assert_not_called()


@unittest.skipUnless(os.environ.get('GROWTH_TEST_DATABASE_URL'), 'dedicated PostgreSQL test database required')
class GuidePostgresTests(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg import sql
        self.psycopg=psycopg
        self.schema='guide_test_'+uuid.uuid4().hex
        with psycopg.connect(os.environ['GROWTH_TEST_DATABASE_URL']) as con:
            con.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
        self.store=guide.UsageStore(self.connect, postgres=True)
        self.env=patch.dict(os.environ, {'GROWTH_GUIDE_MONTHLY_USD':'1','ANTHROPIC_API_KEY':'test-only-key'})
        self.env.start()

    def connect(self):
        return self.psycopg.connect(os.environ['GROWTH_TEST_DATABASE_URL'],options='-c search_path='+self.schema)

    def tearDown(self):
        from psycopg import sql
        with self.psycopg.connect(os.environ['GROWTH_TEST_DATABASE_URL']) as con:
            con.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))
        self.env.stop()

    def test_concurrent_first_use_schema_and_budget_reservations(self):
        def attempt(i):
            try:
                self.store.reserve(str(uuid.uuid4()),str(i),300000)
                return True
            except guide.GuideError:return False
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(attempt,range(12))),3)
        self.assertEqual(self.store.status()['usedUsd'],.9)

    def test_authenticated_cloud_guide_uses_real_postgres_without_mutating_ledger(self):
        # Reuse cloud auth/schema in an isolated schema, never production DB.
        with patch('cloud.db',side_effect=self.connect):
            cloud.setup_schema()
            with self.connect() as con:
                con.execute("INSERT INTO growth_users (username,display_name) VALUES ('guide-admin','테스트')")
                con.execute('INSERT INTO growth_sessions VALUES (%s,%s,%s)',(cloud.sha('guide-session'),'guide-admin',int(guide.time.time())+600))
            client=cloud.app.test_client();base='https://guide.example'
            client.set_cookie(cloud.COOKIE,'guide-session',domain='guide.example')
            before=client.get('/api/state',base_url=base).json
            self.assertEqual(client.get('/api/guide',base_url=base).status_code,200)
            with patch('ai_guide.call_claude',return_value=response()):
                result=client.post('/api/guide/ask',base_url=base,headers={'Origin':base},json=body())
            self.assertEqual(result.status_code,200,result.text)
            self.assertEqual(result.json['usage']['usedUsd'],.004)
            self.assertEqual(client.get('/api/state',base_url=base).json,before)
            self.assertEqual(client.get('/guide.js',base_url=base).status_code,200)
            self.assertEqual(client.get('/guide_knowledge.json',base_url=base).status_code,404)
