import asyncio
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import config


_ORIGINAL_DATABASE_PATH = config.DATABASE_PATH
_ORIGINAL_PRODUCTS_DIR = config.PRODUCTS_DIR


# models 在导入时会初始化数据库，先将其指向临时文件，避免触碰真实数据库。
_IMPORT_TEMP_DIR = tempfile.TemporaryDirectory()
config.DATABASE_PATH = os.path.join(_IMPORT_TEMP_DIR.name, 'import.db')
config.PRODUCTS_DIR = os.path.join(_IMPORT_TEMP_DIR.name, 'products')

import models
import goofish_bot


def tearDownModule():
    models.DATABASE_PATH = _ORIGINAL_DATABASE_PATH
    models.PRODUCTS_DIR = _ORIGINAL_PRODUCTS_DIR
    config.DATABASE_PATH = _ORIGINAL_DATABASE_PATH
    config.PRODUCTS_DIR = _ORIGINAL_PRODUCTS_DIR
    _IMPORT_TEMP_DIR.cleanup()


class DeliverySchemaTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = os.path.join(self.temp_dir.name, 'orders.db')
        self.products_path = os.path.join(self.temp_dir.name, 'products')
        self.database_patch = patch.object(models, 'DATABASE_PATH', self.database_path)
        self.products_patch = patch.object(models, 'PRODUCTS_DIR', self.products_path)
        self.database_patch.start()
        self.products_patch.start()

    def tearDown(self):
        self.products_patch.stop()
        self.database_patch.stop()
        self.temp_dir.cleanup()

    def test_init_db_migrates_legacy_orders_and_preserves_delivery_state(self):
        conn = sqlite3.connect(self.database_path)
        try:
            conn.execute("""
                CREATE TABLE orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    goofish_order_id TEXT DEFAULT '',
                    delivery_sent INTEGER DEFAULT 0
                )
            """)
            conn.execute(
                "INSERT INTO orders (goofish_order_id, delivery_sent) VALUES (?, ?)",
                ('legacy-sent', 1),
            )
            conn.execute(
                "INSERT INTO orders (goofish_order_id, delivery_sent) VALUES (?, ?)",
                ('legacy-pending', 0),
            )
            conn.commit()
        finally:
            conn.close()

        models.init_db()

        conn = sqlite3.connect(self.database_path)
        try:
            columns = {row[1] for row in conn.execute('PRAGMA table_info(orders)')}
            statuses = dict(conn.execute('SELECT goofish_order_id, delivery_status FROM orders'))
        finally:
            conn.close()

        self.assertTrue({
            'delivery_status',
            'delivery_attempts',
            'delivery_error',
            'delivery_started_at',
            'last_delivery_attempt_at',
        }.issubset(columns))
        self.assertEqual(statuses, {
            'legacy-sent': 'sent',
            'legacy-pending': 'pending',
        })

    def test_init_db_allows_empty_order_ids_but_rejects_duplicate_nonempty_ids(self):
        models.init_db()

        conn = sqlite3.connect(self.database_path)
        try:
            conn.execute("INSERT INTO orders (goofish_order_id) VALUES ('')")
            conn.execute("INSERT INTO orders (goofish_order_id) VALUES ('')")
            conn.execute("INSERT INTO orders (goofish_order_id) VALUES ('order-1')")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("INSERT INTO orders (goofish_order_id) VALUES ('order-1')")
        finally:
            conn.close()


    def test_init_db_recovers_partial_migration_without_overwriting_failure_states(self):
        conn = sqlite3.connect(self.database_path)
        try:
            conn.execute("""
                CREATE TABLE orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    goofish_order_id TEXT DEFAULT '',
                    delivery_sent INTEGER DEFAULT 0,
                    delivery_status TEXT DEFAULT 'pending'
                )
            """)
            conn.executemany(
                "INSERT INTO orders (goofish_order_id, delivery_sent, delivery_status) VALUES (?, ?, ?)",
                [
                    ('sent-pending', 1, 'pending'),
                    ('failed', 0, 'failed'),
                    ('review', 0, 'review'),
                    ('empty', 0, ''),
                    ('null', 0, None),
                ],
            )
            conn.commit()
        finally:
            conn.close()

        models.init_db()
        models.init_db()

        conn = sqlite3.connect(self.database_path)
        try:
            statuses = dict(conn.execute('SELECT goofish_order_id, delivery_status FROM orders'))
        finally:
            conn.close()

        self.assertEqual(statuses, {
            'sent-pending': 'sent',
            'failed': 'failed',
            'review': 'review',
            'empty': 'pending',
            'null': 'pending',
        })


class DeliveryTransitionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = os.path.join(self.temp_dir.name, 'orders.db')
        self.products_path = os.path.join(self.temp_dir.name, 'products')
        self.database_patch = patch.object(models, 'DATABASE_PATH', self.database_path)
        self.products_patch = patch.object(models, 'PRODUCTS_DIR', self.products_path)
        self.database_patch.start()
        self.products_patch.start()
        models.init_db()
        self.order_number = 0

    def tearDown(self):
        self.products_patch.stop()
        self.database_patch.stop()
        self.temp_dir.cleanup()

    def add_order(self, status='paid'):
        self.order_number += 1
        return models.add_order(
            1, f'order-{self.order_number}', 'buyer', 'buyer-1', 'item-1', 10,
            status=status,
        )

    def test_only_one_claim_can_move_order_to_sending(self):
        order_id = self.add_order()

        self.assertEqual(models.claim_order_for_delivery(order_id), 1)
        self.assertIsNone(models.claim_order_for_delivery(order_id))

        order = models.get_order(order_id)
        self.assertEqual(order['delivery_status'], 'sending')
        self.assertEqual(order['delivery_attempts'], 1)
        self.assertEqual(order['delivery_error'], '')
        self.assertIsNotNone(order['delivery_started_at'])
        self.assertIsNotNone(order['last_delivery_attempt_at'])

    def test_only_sendable_statuses_can_be_claimed(self):
        for delivery_status in ('sent', 'review', 'sending'):
            with self.subTest(delivery_status=delivery_status):
                order_id = self.add_order()
                models.update_order(order_id, delivery_status=delivery_status)
                self.assertFalse(models.claim_order_for_delivery(order_id))

        unpaid_order_id = self.add_order(status='pending')
        self.assertFalse(models.claim_order_for_delivery(unpaid_order_id))

        failed_order_id = self.add_order()
        models.update_order(failed_order_id, delivery_status='failed', delivery_error='temporary')
        self.assertTrue(models.claim_order_for_delivery(failed_order_id))

    def test_claim_rejects_order_already_marked_as_delivered(self):
        order_id = self.add_order()
        models.update_order(order_id, delivery_sent=1)

        self.assertFalse(models.claim_order_for_delivery(order_id))
        self.assertEqual(models.get_order(order_id)['delivery_status'], 'pending')

    def test_finish_delivery_records_each_outcome(self):
        sent_order_id = self.add_order()
        sent_attempt_token = models.claim_order_for_delivery(sent_order_id)
        self.assertTrue(
            models.finish_delivery(
                sent_order_id,
                sent_attempt_token,
                'sent',
                delivery_content='download link',
            )
        )
        sent_order = models.get_order(sent_order_id)
        self.assertEqual(sent_order['delivery_status'], 'sent')
        self.assertEqual(sent_order['delivery_sent'], 1)
        self.assertEqual(sent_order['delivery_content'], 'download link')
        self.assertEqual(sent_order['status'], 'shipped')
        self.assertIsNotNone(sent_order['sent_at'])
        self.assertIsNone(sent_order['delivery_started_at'])

        for delivery_status in ('failed', 'review'):
            with self.subTest(delivery_status=delivery_status):
                order_id = self.add_order()
                attempt_token = models.claim_order_for_delivery(order_id)
                self.assertTrue(
                    models.finish_delivery(
                        order_id,
                        attempt_token,
                        delivery_status,
                        error='delivery error',
                    )
                )
                order = models.get_order(order_id)
                self.assertEqual(order['delivery_status'], delivery_status)
                self.assertEqual(order['delivery_error'], 'delivery error')
                self.assertEqual(order['delivery_sent'], 0)
                self.assertIsNone(order['delivery_started_at'])

    def test_mark_order_delivery_sent_forces_sent_without_attempt(self):
        order_id = self.add_order()

        self.assertTrue(models.mark_order_delivery_sent(order_id, 'delivery key'))

        order = models.get_order(order_id)
        self.assertEqual(order['delivery_status'], 'sent')
        self.assertEqual(order['delivery_content'], 'delivery key')
        self.assertEqual(order['status'], 'shipped')

    def test_recover_stale_sending_order(self):
        stale_order_id = self.add_order()
        recent_order_id = self.add_order()
        pending_order_id = self.add_order()
        fixed_now = datetime(2026, 7, 25, 12, 0, 0)
        models.update_order(
            stale_order_id,
            delivery_status='sending',
            delivery_started_at=(fixed_now - timedelta(minutes=6)).isoformat(),
        )
        models.update_order(
            recent_order_id,
            delivery_status='sending',
            delivery_started_at=(fixed_now - timedelta(minutes=4)).isoformat(),
        )

        recovered = models.recover_stale_deliveries(now=fixed_now, stale_minutes=5)

        self.assertEqual(recovered, 1)
        stale_order = models.get_order(stale_order_id)
        self.assertEqual(stale_order['delivery_status'], 'failed')
        self.assertEqual(stale_order['delivery_error'], '发送任务中断')
        self.assertIsNone(stale_order['delivery_started_at'])
        self.assertEqual(models.get_order(recent_order_id)['delivery_status'], 'sending')
        self.assertEqual(models.get_order(pending_order_id)['delivery_status'], 'pending')

    def test_late_result_from_recovered_attempt_cannot_complete_new_attempt(self):
        order_id = self.add_order()
        first_attempt_token = models.claim_order_for_delivery(order_id)
        self.assertEqual(first_attempt_token, 1)

        recovered = models.recover_stale_deliveries(
            now=datetime.now() + timedelta(minutes=6),
            stale_minutes=5,
        )
        self.assertEqual(recovered, 1)

        second_attempt_token = models.claim_order_for_delivery(order_id)
        self.assertEqual(second_attempt_token, 2)

        self.assertFalse(
            models.finish_delivery(
                order_id,
                first_attempt_token,
                'sent',
                delivery_content='stale delivery key',
            )
        )
        claimed_order = models.get_order(order_id)
        self.assertEqual(claimed_order['delivery_status'], 'sending')
        self.assertEqual(claimed_order['delivery_attempts'], second_attempt_token)

        self.assertTrue(
            models.finish_delivery(
                order_id,
                second_attempt_token,
                'sent',
                delivery_content='current delivery key',
            )
        )
        completed_order = models.get_order(order_id)
        self.assertEqual(completed_order['delivery_status'], 'sent')
        self.assertEqual(completed_order['delivery_content'], 'current delivery key')

    def test_recovery_preserves_microsecond_staleness_boundary(self):
        exact_cutoff_order_id = self.add_order()
        stale_order_id = self.add_order()
        fixed_now = datetime(2026, 7, 25, 12, 0, 0, 50000)
        cutoff = fixed_now - timedelta(minutes=5)
        models.update_order(
            exact_cutoff_order_id,
            delivery_status='sending',
            delivery_started_at=cutoff.isoformat(),
        )
        models.update_order(
            stale_order_id,
            delivery_status='sending',
            delivery_started_at=(cutoff - timedelta(milliseconds=50)).isoformat(),
        )

        recovered = models.recover_stale_deliveries(now=fixed_now, stale_minutes=5)

        self.assertEqual(recovered, 1)
        self.assertEqual(models.get_order(exact_cutoff_order_id)['delivery_status'], 'sending')
        self.assertEqual(models.get_order(stale_order_id)['delivery_status'], 'failed')

    def test_pending_delivery_query_excludes_sending_and_review(self):
        expected_order_ids = []
        for delivery_status in ('pending', 'failed'):
            order_id = self.add_order()
            models.update_order(order_id, delivery_status=delivery_status)
            expected_order_ids.append(order_id)

        for delivery_status in ('sending', 'review', 'sent'):
            order_id = self.add_order()
            models.update_order(order_id, delivery_status=delivery_status)

        delivered_order_id = self.add_order()
        models.update_order(delivered_order_id, delivery_sent=1, delivery_status='pending')

        unpaid_order_id = self.add_order(status='pending')

        order_ids = {order['id'] for order in models.get_pending_delivery_orders()}
        self.assertEqual(order_ids, set(expected_order_ids))

    def test_concurrent_claims_produce_one_attempt_token_without_database_lock(self):
        order_id = self.add_order()
        barrier = threading.Barrier(2)

        def claim():
            barrier.wait()
            try:
                return models.claim_order_for_delivery(order_id)
            except sqlite3.OperationalError as error:
                return error

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(claim) for _ in range(2)]
            results = [future.result() for future in futures]

        self.assertFalse(
            any(isinstance(result, sqlite3.OperationalError) and 'locked' in str(result) for result in results)
        )
        successful_tokens = [result for result in results if result is not None]
        self.assertEqual(len(successful_tokens), 1)
        self.assertIs(type(successful_tokens[0]), int)
        self.assertEqual(models.get_order(order_id)['delivery_attempts'], 1)


class _FakeInput:
    def __init__(self, click_error=None):
        self.click_error = click_error

    async def click(self):
        if self.click_error:
            raise self.click_error

    async def fill(self, value):
        pass

    async def type(self, value, delay=0):
        pass


class _FakeButton:
    def __init__(self, click_error=None):
        self.click_error = click_error

    async def inner_text(self):
        return 'Send'

    async def click(self):
        if self.click_error:
            raise self.click_error


class _FakeKeyboard:
    def __init__(self, press_error=None):
        self.press_error = press_error

    async def press(self, key):
        if self.press_error:
            raise self.press_error


class _FakePage:
    def __init__(self, message_input=None, button=None, goto_error=None, keyboard=None):
        self.message_input = message_input
        self.button = button
        self.goto_error = goto_error
        self.keyboard = keyboard or _FakeKeyboard()

    async def goto(self, url, **kwargs):
        if self.goto_error:
            raise self.goto_error

    async def query_selector(self, selector):
        if selector == '[contenteditable="true"]':
            return self.message_input
        if selector == 'textarea':
            return None
        return None

    async def query_selector_all(self, selector):
        return [self.button] if selector == 'button' and self.button else []


async def _missing_detailed_sender(*args, **kwargs):
    return {'outcome': 'missing', 'error': 'send_im_message_result is missing'}


class DeliveryMessageResultTests(unittest.TestCase):
    def send_result(self, page):
        sender = getattr(goofish_bot, 'send_im_message_result', _missing_detailed_sender)
        with patch.object(goofish_bot, 'get_browser', new=AsyncMock(return_value=(None, None, page))), \
             patch.object(goofish_bot.asyncio, 'sleep', new=AsyncMock()), \
             patch.object(goofish_bot, 'add_log'):
            return asyncio.run(sender('item-1', 'buyer-1', 'delivery secret'))

    def test_missing_message_input_is_not_sent(self):
        result = self.send_result(_FakePage())

        self.assertEqual(result, {
            'outcome': 'not_sent',
            'error': '未找到闲鱼消息输入框',
        })

    def test_failure_before_send_action_is_not_sent(self):
        result = self.send_result(_FakePage(message_input=_FakeInput(click_error=RuntimeError('fill failed'))))

        self.assertEqual(result['outcome'], 'not_sent')
        self.assertEqual(result['error'], 'fill failed')

    def test_failure_after_send_action_starts_is_unknown(self):
        result = self.send_result(
            _FakePage(message_input=_FakeInput(), button=_FakeButton(click_error=RuntimeError('click lost')))
        )

        self.assertEqual(result['outcome'], 'unknown')
        self.assertEqual(result['error'], 'click lost')

    def test_completed_message_send_is_sent(self):
        result = self.send_result(_FakePage(message_input=_FakeInput(), button=_FakeButton()))

        self.assertEqual(result, {'outcome': 'sent', 'error': ''})

    def test_enter_send_is_sent_when_no_button_exists(self):
        result = self.send_result(_FakePage(message_input=_FakeInput()))

        self.assertEqual(result, {'outcome': 'sent', 'error': ''})

    def test_enter_send_error_is_unknown_when_no_button_exists(self):
        result = self.send_result(
            _FakePage(message_input=_FakeInput(), keyboard=_FakeKeyboard(RuntimeError('enter lost')))
        )

        self.assertEqual(result['outcome'], 'unknown')
        self.assertEqual(result['error'], 'enter lost')

    def test_boolean_wrapper_only_reports_sent_as_success(self):
        cases = [
            ('sent', True),
            ('unknown', False),
            ('not_sent', False),
        ]
        for outcome, expected in cases:
            with self.subTest(outcome=outcome):
                with patch.object(
                    goofish_bot,
                    'send_im_message_result',
                    new=AsyncMock(return_value={'outcome': outcome, 'error': 'failed'}),
                    create=True,
                ):
                    self.assertEqual(
                        asyncio.run(goofish_bot.send_im_message('item-1', 'buyer-1', 'delivery secret')),
                        expected,
                    )


class AutoDeliveryRetryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = os.path.join(self.temp_dir.name, 'orders.db')
        self.products_path = os.path.join(self.temp_dir.name, 'products')
        self.database_patch = patch.object(models, 'DATABASE_PATH', self.database_path)
        self.products_patch = patch.object(models, 'PRODUCTS_DIR', self.products_path)
        self.database_patch.start()
        self.products_patch.start()
        models.init_db()
        self.order_number = 0

    def tearDown(self):
        self.products_patch.stop()
        self.database_patch.stop()
        self.temp_dir.cleanup()

    def add_product(self, delivery_content='download link'):
        return models.add_product('product', 'description', 10, delivery_content)

    def add_order(self, product_id, buyer_user_id='buyer-1', item_id='item-1'):
        self.order_number += 1
        return models.add_order(
            product_id, f'order-{self.order_number}', 'buyer', buyer_user_id, item_id, 10,
            status='paid',
        )

    def run_delivery(self, order_id, results):
        async def send_result(*args):
            result = results.pop(0)
            if callable(result):
                return await result(*args)
            return result

        sender = AsyncMock(side_effect=send_result)
        sleep = AsyncMock()
        with patch.object(goofish_bot, 'send_im_message_result', new=sender, create=True), \
             patch.object(goofish_bot.asyncio, 'sleep', new=sleep):
            result = asyncio.run(goofish_bot.auto_deliver_order(order_id))
        return result, sender, sleep

    def test_second_attempt_success_sleeps_once_and_marks_order_sent(self):
        order_id = self.add_order(self.add_product())

        result, sender, sleep = self.run_delivery(order_id, [
            {'outcome': 'not_sent', 'error': 'temporary failure'},
            {'outcome': 'sent', 'error': ''},
        ])

        order = models.get_order(order_id)
        self.assertEqual(result, {'status': 'sent', 'error': ''})
        self.assertEqual(sender.await_count, 2)
        self.assertEqual([call.args[0] for call in sleep.await_args_list], [5])
        self.assertEqual(order['delivery_status'], 'sent')
        self.assertEqual(order['delivery_attempts'], 2)
        self.assertEqual(order['delivery_content'], 'download link')

    def test_four_explicit_failures_use_all_retry_delays(self):
        order_id = self.add_order(self.add_product())

        result, sender, sleep = self.run_delivery(order_id, [
            {'outcome': 'not_sent', 'error': 'temporary failure'},
        ] * 4)

        order = models.get_order(order_id)
        self.assertEqual(result, {'status': 'failed', 'error': 'temporary failure'})
        self.assertEqual(sender.await_count, 4)
        self.assertEqual([call.args[0] for call in sleep.await_args_list], [5, 30, 120])
        self.assertEqual(order['delivery_status'], 'failed')
        self.assertEqual(order['delivery_attempts'], 4)
        self.assertEqual(order['delivery_error'], 'temporary failure')

    def test_unknown_first_attempt_requires_review_without_retry(self):
        order_id = self.add_order(self.add_product())

        result, sender, sleep = self.run_delivery(order_id, [
            {'outcome': 'unknown', 'error': 'send confirmation unavailable'},
        ])

        order = models.get_order(order_id)
        self.assertEqual(result, {'status': 'review', 'error': 'send confirmation unavailable'})
        self.assertEqual(sender.await_count, 1)
        self.assertEqual(sleep.await_count, 0)
        self.assertEqual(order['delivery_status'], 'review')
        self.assertEqual(order['delivery_attempts'], 1)

    def test_unconfirmed_sender_results_require_review_without_retry(self):
        cases = [
            {'outcome': 'unexpected', 'error': 'unrecognized outcome'},
            {'error': 'missing outcome'},
            'malformed result',
        ]

        for sender_result in cases:
            with self.subTest(sender_result=sender_result):
                order_id = self.add_order(self.add_product())

                result, sender, sleep = self.run_delivery(order_id, [sender_result])

                order = models.get_order(order_id)
                self.assertEqual(result['status'], 'review')
                self.assertIn('\u53d1\u9001\u7ed3\u679c\u65e0\u6cd5\u786e\u8ba4', result['error'])
                self.assertEqual(sender.await_count, 1)
                self.assertEqual(sleep.await_count, 0)
                self.assertEqual(order['delivery_status'], 'review')
                self.assertIn('\u53d1\u9001\u7ed3\u679c\u65e0\u6cd5\u786e\u8ba4', order['delivery_error'])

    def test_initial_claim_conflict_does_not_send(self):
        order_id = self.add_order(self.add_product())
        self.assertEqual(models.claim_order_for_delivery(order_id), 1)

        result, sender, sleep = self.run_delivery(order_id, [])

        self.assertEqual(result['status'], 'conflict')
        self.assertEqual(sender.await_count, 0)
        self.assertEqual(sleep.await_count, 0)

    def test_finish_conflict_returns_current_database_status_without_retry(self):
        order_id = self.add_order(self.add_product())

        async def concurrent_update(*args):
            models.update_order(order_id, delivery_status='review')
            return {'outcome': 'sent', 'error': ''}

        result, sender, sleep = self.run_delivery(order_id, [concurrent_update])

        self.assertEqual(result['status'], 'review')
        self.assertEqual(sender.await_count, 1)
        self.assertEqual(sleep.await_count, 0)
        self.assertEqual(models.get_order(order_id)['delivery_status'], 'review')

    def test_invalid_delivery_prerequisites_are_recorded_as_failed_after_retries(self):
        cases = [
            (None, 'buyer-1', 'item-1'),
            (self.add_product(''), 'buyer-1', 'item-1'),
            (self.add_product(), '', 'item-1'),
            (self.add_product(), 'buyer-1', ''),
        ]

        for product_id, buyer_user_id, item_id in cases:
            with self.subTest(product_id=product_id, buyer_user_id=buyer_user_id, item_id=item_id):
                order_id = self.add_order(product_id, buyer_user_id, item_id)
                result, sender, sleep = self.run_delivery(order_id, [])
                order = models.get_order(order_id)

                self.assertEqual(result['status'], 'failed')
                self.assertEqual(sender.await_count, 0)
                self.assertEqual([call.args[0] for call in sleep.await_args_list], [5, 30, 120])
                self.assertEqual(order['delivery_status'], 'failed')
                self.assertEqual(order['delivery_attempts'], 4)
                self.assertTrue(order['delivery_error'])


if __name__ == '__main__':
    unittest.main()
