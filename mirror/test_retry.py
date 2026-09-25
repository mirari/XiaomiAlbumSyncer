import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import requests
from xiaomi import Xiaomi, CloudError


class RetryTests(unittest.TestCase):
    def setUp(self):
        self.cloud = Xiaomi('unused', 1)
        self.cloud.session = Mock()
        self.url = 'https://i.mi.com/test?secret=hidden'

    def response(self, status, headers=None):
        return Mock(status_code=status, headers=headers or {})

    @patch('xiaomi.time.sleep')
    def test_transient_recovery(self, sleep):
        ok = self.response(200)
        self.cloud.session.request.side_effect = [requests.ReadTimeout('secret'), self.response(503), ok]
        self.assertIs(self.cloud._request(self.url), ok)
        self.assertEqual(sleep.call_count, 2)

    @patch('xiaomi.time.sleep')
    def test_limit_and_redaction(self, sleep):
        self.cloud.session.request.side_effect = requests.ConnectionError(self.url)
        with self.assertRaises(CloudError) as ctx:
            self.cloud._request(self.url)
        self.assertIn('ConnectionError after 5 attempts', str(ctx.exception))
        self.assertNotIn('secret', str(ctx.exception))
        self.assertEqual(self.cloud.session.request.call_count, 5)
        self.assertEqual(sleep.call_count, 4)

    @patch('xiaomi.time.sleep')
    def test_retry_after_and_close(self, sleep):
        limited = self.response(429, {'Retry-After': '90'})
        self.cloud.session.request.side_effect = [limited, self.response(200)]
        self.cloud._request(self.url)
        limited.close.assert_called_once()
        sleep.assert_called_once_with(90.0)

    @patch('xiaomi.time.sleep')
    def test_auth_error_not_retried(self, sleep):
        self.cloud.session.request.return_value = self.response(403)
        with self.assertRaisesRegex(CloudError, 'HTTP 403 after 1 attempts'):
            self.cloud._request(self.url)
        sleep.assert_not_called()

    @patch('xiaomi.time.sleep')
    def test_stream_restarts_partial_and_refreshes_urls(self, sleep):
        self.cloud.assets = {'key': ('photo', 'id')}
        self.cloud._json = Mock(return_value={'url': self.url})
        callback = Mock(text='callback({"url":"https://i.mi.com/data","meta":"secret"})')
        broken = Mock(status_code=200)
        def chunks(*args):
            yield b'partial'
            raise requests.exceptions.ChunkedEncodingError('secret')
        broken.iter_content.side_effect = chunks
        good = Mock(status_code=200)
        good.iter_content.return_value = [b'complete']
        def context(value):
            from unittest.mock import MagicMock
            result = MagicMock()
            result.__enter__.return_value = value
            return result
        self.cloud._request = Mock(side_effect=[context(callback), context(broken), context(callback), context(good)])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'stage.part'
            self.cloud.download('key', path)
            self.assertEqual(path.read_bytes(), b'complete')
        self.assertEqual(self.cloud._json.call_count, 2)
        sleep.assert_called_once()

    @patch('xiaomi.time.sleep')
    def test_stream_retry_exhausted(self, sleep):
        self.cloud._download_once = Mock(side_effect=requests.ReadTimeout('secret'))
        with self.assertRaisesRegex(CloudError, 'download stream: ReadTimeout after 3 attempts'):
            self.cloud.download('key', Path('unused'))
        self.assertEqual(self.cloud._download_once.call_count, 3)

    @patch('xiaomi.time.sleep')
    def test_retry_after_is_bounded(self, sleep):
        self.cloud._backoff(0, '999999999')
        sleep.assert_called_once_with(300)


if __name__ == '__main__':
    unittest.main()
