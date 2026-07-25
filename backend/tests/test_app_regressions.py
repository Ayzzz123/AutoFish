import asyncio
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import app as app_module
import config
import goofish_bot


class SqliteRowLike:
    """Minimal sqlite3.Row stand-in: supports indexing, but not dict.get()."""

    def __init__(self, values):
        self._values = values

    def __getitem__(self, key):
        return self._values[key]


class AppRegressionTests(unittest.TestCase):
    def setUp(self):
        app_module.app.config.update(TESTING=True)
        self.client = app_module.app.test_client()

    def test_publish_accepts_sqlite_row_product(self):
        product = SqliteRowLike({
            'id': 7,
            'title': '测试商品',
            'description': '测试描述',
            'price': 2.9,
            'original_price': 29.0,
            'images': '["C:/tmp/cover.png"]',
            'category': '测试分类',
        })

        with (
            patch.object(app_module, 'get_product', return_value=product),
            patch.object(
                app_module,
                'publish_product_quick',
                new=AsyncMock(return_value={'success': True, 'url': 'https://example.test/publish'}),
            ),
            patch.object(app_module, 'update_product') as update_product,
        ):
            response = self.client.post('/api/goofish/publish', json={'product_id': 7})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['success'])
        update_product.assert_not_called()

    def test_check_im_uses_a_real_browser_page(self):
        page = object()

        async def check_im(received_page):
            self.assertIs(received_page, page)
            return []

        with (
            patch('goofish_bot.get_browser', new=AsyncMock(return_value=(object(), object(), page))),
            patch('monitor_mcp.check_im_for_payment', side_effect=check_im),
        ):
            response = self.client.post('/api/monitor/check-im', json={})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['success'])

    def test_monitor_loop_fetches_and_records_new_orders(self):
        fetched_orders = [{
            'orderId': 'order-1',
            'itemId': 'item-1',
            'buyerName': '测试买家',
            'buyerId': 'buyer-1',
            'price': 2.9,
            'status': 'paid',
        }]
        product = SqliteRowLike({'id': 3, 'goofish_item_id': 'item-1'})

        def stop_after_first_sleep(_seconds):
            app_module._monitor_running = False

        app_module._monitor_running = True
        with (
            patch.object(app_module, 'fetch_sold_orders', new=AsyncMock(return_value=fetched_orders)) as fetch_orders,
            patch.object(app_module, 'get_all_products', return_value=[product]),
            patch.object(app_module, 'get_orders', return_value=[]),
            patch.object(app_module, 'add_order', return_value=10) as add_order,
            patch.object(app_module, 'get_pending_delivery_orders', return_value=[]),
            patch.object(app_module, 'add_log'),
            patch.object(app_module.time, 'sleep', side_effect=stop_after_first_sleep),
        ):
            app_module.monitor_loop()

        fetch_orders.assert_awaited_once()
        add_order.assert_called_once()

    def test_existing_order_is_updated_when_platform_status_changes(self):
        existing_order = SqliteRowLike({
            'id': 2,
            'goofish_order_id': 'order-2',
            'status': 'paid',
            'delivery_sent': 0,
        })
        incoming = [{
            'orderId': 'order-2',
            'itemId': 'item-2',
            'buyerName': '测试买家',
            'buyerId': 'buyer-2',
            'price': 9.9,
            'status': 'shipped',
        }]

        with (
            patch.object(app_module, 'get_all_products', return_value=[]),
            patch.object(app_module, 'get_orders', return_value=[existing_order]),
            patch.object(app_module, 'mark_order_delivery_sent') as mark_delivered,
            patch.object(app_module, 'add_log'),
        ):
            new_count = app_module.record_new_orders(incoming)

        self.assertEqual(new_count, 0)
        mark_delivered.assert_called_once_with(2, order_status='shipped')

    def test_orders_api_serializes_delivery_state(self):
        order = SqliteRowLike({
            'id': 2, 'product_id': 3, 'goofish_order_id': 'order-2',
            'buyer_name': 'buyer', 'buyer_user_id': 'buyer-2', 'amount': 9.9,
            'status': 'paid', 'delivery_sent': 0, 'delivery_content': '',
            'sent_at': None, 'detected_at': '2026-07-25T12:00:00', 'remark': '',
            'delivery_status': 'failed', 'delivery_attempts': 2,
            'delivery_error': 'temporary failure', 'delivery_started_at': None,
            'last_delivery_attempt_at': '2026-07-25T12:01:00',
        })

        with patch.object(app_module, 'get_orders', return_value=[order]):
            response = self.client.get('/api/orders')

        self.assertEqual(response.status_code, 200)
        data = response.get_json()['data'][0]
        self.assertEqual(data['delivery_status'], 'failed')
        self.assertEqual(data['delivery_attempts'], 2)
        self.assertEqual(data['delivery_error'], 'temporary failure')
        self.assertIsNone(data['delivery_started_at'])
        self.assertEqual(data['last_delivery_attempt_at'], '2026-07-25T12:01:00')

    def test_deliver_order_maps_auto_delivery_results(self):
        order = SqliteRowLike({
            'id': 5, 'status': 'paid', 'delivery_sent': 0,
            'delivery_status': 'pending',
        })
        cases = [
            ('sent', 200, True, None),
            ('failed', 422, False, None),
            ('review', 409, False, '需要人工检查'),
            ('conflict', 409, False, None),
        ]

        for status, expected_code, expected_success, error_part in cases:
            with self.subTest(status=status), \
                 patch.object(app_module, 'get_order', return_value=order), \
                 patch.object(
                     app_module,
                     'auto_deliver_order',
                     new=AsyncMock(return_value={'status': status, 'error': 'delivery error'}),
                 ) as auto_deliver:
                response = self.client.post('/api/orders/5/deliver')

            body = response.get_json()
            self.assertEqual(response.status_code, expected_code)
            self.assertEqual(body['success'], expected_success)
            self.assertEqual(body['data']['delivery_status'], status)
            if error_part:
                self.assertIn(error_part, body['error'])
            auto_deliver.assert_awaited_once_with(5)

    def test_deliver_order_rejects_missing_or_non_sendable_orders_without_auto_delivery(self):
        cases = [
            (None, 404),
            (SqliteRowLike({'status': 'pending', 'delivery_sent': 0, 'delivery_status': 'pending'}), 409),
            (SqliteRowLike({'status': 'paid', 'delivery_sent': 1, 'delivery_status': 'sent'}), 409),
            (SqliteRowLike({'status': 'paid', 'delivery_sent': 0, 'delivery_status': 'review'}), 409),
        ]

        for order, expected_code in cases:
            with self.subTest(order=order), \
                 patch.object(app_module, 'get_order', return_value=order), \
                 patch.object(app_module, 'auto_deliver_order', new=AsyncMock()) as auto_deliver:
                response = self.client.post('/api/orders/5/deliver')

            self.assertEqual(response.status_code, expected_code)
            self.assertFalse(response.get_json()['success'])
            auto_deliver.assert_not_awaited()

    def test_platform_shipped_or_completed_orders_are_marked_sent_with_platform_status(self):
        product = SqliteRowLike({'id': 3, 'goofish_item_id': 'item-1'})
        incoming = [
            {'orderId': 'order-shipped', 'itemId': 'item-1', 'status': 'shipped'},
            {'orderId': 'order-completed', 'itemId': 'item-1', 'status': 'completed'},
        ]

        with (
            patch.object(app_module, 'get_all_products', return_value=[product]),
            patch.object(app_module, 'get_orders', return_value=[]),
            patch.object(app_module, 'add_order', side_effect=[10, 11]),
            patch.object(app_module, 'mark_order_delivery_sent') as mark_delivered,
            patch.object(app_module, 'add_log'),
        ):
            new_count = app_module.record_new_orders(incoming)

        self.assertEqual(new_count, 2)
        self.assertEqual(
            mark_delivered.call_args_list,
            [
                call(10, order_status='shipped'),
                call(11, order_status='completed'),
            ],
        )

    def test_record_new_orders_ignores_duplicate_insert_and_continues(self):
        product = SqliteRowLike({'id': 3, 'goofish_item_id': 'item-1'})
        incoming = [
            {'orderId': 'duplicate', 'itemId': 'item-1', 'status': 'paid'},
            {'orderId': 'new-order', 'itemId': 'item-1', 'status': 'paid'},
        ]

        with (
            patch.object(app_module, 'get_all_products', return_value=[product]),
            patch.object(app_module, 'get_orders', return_value=[]),
            patch.object(app_module, 'add_order', side_effect=[sqlite3.IntegrityError('duplicate'), 12]),
            patch.object(app_module, 'add_log') as add_log,
        ):
            new_count = app_module.record_new_orders(incoming)

        self.assertEqual(new_count, 1)
        self.assertTrue(any('重复订单已忽略' in call.args[1] for call in add_log.call_args_list))

    def test_mark_delivered_uses_delivery_transition_with_product_content(self):
        order = SqliteRowLike({'id': 5, 'product_id': 3})
        product = SqliteRowLike({'delivery_content': 'download link'})
        with (
            patch.object(app_module, 'get_order', return_value=order),
            patch.object(app_module, 'get_product', return_value=product),
            patch.object(app_module, 'mark_order_delivery_sent', return_value=True) as mark_delivered,
            patch.object(app_module, 'add_log'),
        ):
            response = self.client.post('/api/monitor/mark-delivered', json={'order_id': 5})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['success'])
        mark_delivered.assert_called_once_with(5, 'download link')

    def test_monitor_start_recovers_stale_deliveries_before_starting_thread(self):
        app_module._monitor_running = False
        thread = MagicMock()
        with (
            patch.object(app_module, 'recover_stale_deliveries', return_value=2) as recover,
            patch.object(app_module.threading, 'Thread', return_value=thread),
            patch.object(app_module, 'add_log') as add_log,
        ):
            response = self.client.post('/api/monitor/start')

        self.assertEqual(response.status_code, 200)
        recover.assert_called_once_with()
        thread.start.assert_called_once_with()
        self.assertTrue(any('恢复' in call.args[1] for call in add_log.call_args_list))
        app_module._monitor_running = False

    def test_main_recovers_stale_deliveries_before_running_server(self):
        with (
            patch.object(app_module, 'recover_stale_deliveries', return_value=1) as recover,
            patch.object(app_module, 'add_log') as add_log,
            patch.object(app_module.app, 'run') as run,
        ):
            app_module.main()

        recover.assert_called_once_with()
        run.assert_called_once_with(
            host=app_module.FLASK_HOST,
            port=app_module.FLASK_PORT,
            debug=app_module.FLASK_DEBUG,
        )
        self.assertTrue(any('恢复' in call.args[1] for call in add_log.call_args_list))

    def test_waiting_for_receipt_is_a_shipped_status(self):
        self.assertEqual(goofish_bot.normalize_order_status('订单状态：待收货'), 'shipped')

    def test_product_image_upload_saves_file_and_updates_product(self):
        with tempfile.TemporaryDirectory() as products_dir:
            with (
                patch.object(app_module, 'PRODUCTS_DIR', products_dir),
                patch.object(app_module, 'get_product', return_value=object()),
                patch.object(app_module, 'update_product') as update_product,
                patch.object(app_module, 'add_log'),
            ):
                response = self.client.post(
                    '/api/products/7/image',
                    data={'image': (io.BytesIO(b'fake-png-content'), 'cover.png')},
                    content_type='multipart/form-data',
                )

            self.assertEqual(response.status_code, 200)
            saved_path = update_product.call_args.kwargs['images'][0]
            self.assertTrue(os.path.isfile(saved_path))
            self.assertEqual(os.path.splitext(saved_path)[1], '.png')

    def test_product_image_endpoint_serves_saved_image(self):
        with tempfile.TemporaryDirectory() as products_dir:
            image_path = os.path.join(products_dir, 'cover.png')
            with open(image_path, 'wb') as image_file:
                image_file.write(b'fake-png-content')
            product = SqliteRowLike({'images': json.dumps([image_path])})

            with (
                patch.object(app_module, 'PRODUCTS_DIR', products_dir),
                patch.object(app_module, 'get_product', return_value=product),
            ):
                response = self.client.get('/api/products/7/image')

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data, b'fake-png-content')
            response.close()

    def test_unpublish_updates_local_status_only_after_goofish_success(self):
        product = SqliteRowLike({'id': 7, 'goofish_item_id': 'item-7'})
        with (
            patch.object(app_module, 'get_product', return_value=product),
            patch.object(
                app_module,
                'unpublish_product',
                new=AsyncMock(return_value={'success': True, 'message': '闲鱼商品已下架'}),
                create=True,
            ),
            patch.object(app_module, 'update_product') as update_product,
            patch.object(app_module, 'add_log'),
        ):
            response = self.client.post('/api/goofish/unpublish', json={'product_id': 7})

        self.assertEqual(response.status_code, 200)
        update_product.assert_called_once_with(7, status='removed')

    def test_unpublish_keeps_local_status_when_goofish_fails(self):
        product = SqliteRowLike({'id': 7, 'goofish_item_id': 'item-7'})
        with (
            patch.object(app_module, 'get_product', return_value=product),
            patch.object(
                app_module,
                'unpublish_product',
                new=AsyncMock(return_value={'success': False, 'error': '请先登录闲鱼'}),
                create=True,
            ),
            patch.object(app_module, 'update_product') as update_product,
        ):
            response = self.client.post('/api/goofish/unpublish', json={'product_id': 7})

        self.assertEqual(response.status_code, 409)
        update_product.assert_not_called()

    def test_login_status_reports_background_login_progress(self):
        app_module._login_state.update({
            'in_progress': True,
            'logged_in': False,
            'error': '',
        })

        response = self.client.get('/api/goofish/login-status')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {
            'success': True,
            'in_progress': True,
            'logged_in': False,
            'error': '',
        })

    def test_login_check_rejects_page_with_visible_login_entry(self):
        class VisibleLogin:
            first = None

            def __init__(self):
                self.first = self

            async def count(self):
                return 1

            async def is_visible(self):
                return True

        page = MagicMock()
        page.goto = AsyncMock()
        page.query_selector = AsyncMock(return_value=object())
        page.get_by_text.return_value = VisibleLogin()

        with patch.object(app_module, 'check_login_status'):
            import goofish_bot
            with patch.object(goofish_bot, 'get_browser', new=AsyncMock(return_value=(None, None, page))):
                logged_in = asyncio.run(goofish_bot.check_login_status())

        self.assertFalse(logged_in)

    def test_browser_is_recreated_when_asyncio_event_loop_changes(self):
        stale_browser = MagicMock()
        stale_browser.is_connected.return_value = True
        stale_page = object()

        fresh_page = object()
        fresh_context = MagicMock()
        fresh_context.new_page = AsyncMock(return_value=fresh_page)
        fresh_browser = MagicMock()
        fresh_browser.new_context = AsyncMock(return_value=fresh_context)
        chromium = MagicMock()
        chromium.launch = AsyncMock(return_value=fresh_browser)
        playwright = MagicMock(chromium=chromium)
        manager = MagicMock()
        manager.start = AsyncMock(return_value=playwright)

        goofish_bot._browser = stale_browser
        goofish_bot._context = object()
        goofish_bot._page = stale_page
        goofish_bot._browser_loop = object()
        try:
            with (
                patch.object(goofish_bot, 'async_playwright', return_value=manager),
                patch.object(goofish_bot.os.path, 'exists', return_value=False),
                patch.object(goofish_bot.os, 'makedirs'),
            ):
                browser, context, page = asyncio.run(goofish_bot.get_browser())

            self.assertIs(browser, fresh_browser)
            self.assertIs(context, fresh_context)
            self.assertIs(page, fresh_page)
            chromium.launch.assert_awaited_once()
        finally:
            goofish_bot._browser = None
            goofish_bot._context = None
            goofish_bot._page = None
            goofish_bot._browser_loop = None

    def test_unpublish_success_marker_returns_without_fixed_delay(self):
        self.assertTrue(hasattr(goofish_bot, '_wait_for_unpublish_success'))

        marker = MagicMock()
        marker.is_visible = AsyncMock(return_value=True)
        locator = MagicMock()
        locator.count = AsyncMock(return_value=1)
        locator.nth.return_value = marker
        page = MagicMock()
        page.get_by_text.return_value = locator

        with patch.object(goofish_bot.asyncio, 'sleep', new=AsyncMock()) as sleep:
            success = asyncio.run(goofish_bot._wait_for_unpublish_success(page))

        self.assertTrue(success)
        sleep.assert_not_awaited()

    def test_debug_mode_is_disabled_by_default(self):
        self.assertFalse(config.FLASK_DEBUG)

    def test_cors_rejects_untrusted_origins(self):
        with (
            patch.object(app_module, 'get_all_products', return_value=[]),
            patch.object(app_module, 'get_orders', return_value=[]),
            patch.object(app_module, 'get_pending_delivery_orders', return_value=[]),
        ):
            response = self.client.get(
                '/api/system/info',
                headers={'Origin': 'https://malicious.example'},
            )

        self.assertNotIn('Access-Control-Allow-Origin', response.headers)


if __name__ == '__main__':
    unittest.main()
