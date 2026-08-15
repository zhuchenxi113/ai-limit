"""fetch_status_components 的 DNS 自愈兜底回归测试。

背景：macOS 系统 DNS 解析器（mDNSResponder）曾经对 status.claude.com 单独
卡住一条陈旧的失败缓存，导致状态圆点长期灰色，用户必须手动
`sudo dscacheutil -flushcache` 才能恢复（完整故障现象见
docs/reference/lessons.md「状态圆点灰色但浏览器访问状态页正常」）。
这里锁定修复后的行为：系统解析限时失败/卡住时自动切到 DNS-over-HTTPS 兜底，
不依赖用户手动介入。
"""
import pathlib
import socket
import sys
import time
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT)]

import usage


class ResolveHostWithDeadlineTests(unittest.TestCase):
    def test_returns_ip_on_success(self):
        with mock.patch.object(
            socket, "getaddrinfo",
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
        ):
            self.assertEqual(
                usage._resolve_host_with_deadline("example.com", 2),
                "93.184.216.34",
            )

    def test_returns_none_when_resolution_raises(self):
        with mock.patch.object(
            socket, "getaddrinfo",
            side_effect=socket.gaierror(8, "nodename nor servname provided, or not known"),
        ):
            self.assertIsNone(usage._resolve_host_with_deadline("status.claude.com", 2))

    def test_returns_none_promptly_when_resolution_hangs_past_deadline(self):
        def _hang(*_args, **_kwargs):
            time.sleep(2)
            raise socket.gaierror(8, "simulated stale mDNSResponder negative cache")

        with mock.patch.object(socket, "getaddrinfo", side_effect=_hang):
            t0 = time.time()
            result = usage._resolve_host_with_deadline("status.claude.com", 0.2)
            elapsed = time.time() - t0
        self.assertIsNone(result)
        self.assertLess(elapsed, 1.0, "join(deadline) 必须真正限时返回，不能被卡住的 getaddrinfo 拖住")


class IsIpv4LiteralTests(unittest.TestCase):
    def test_valid_ipv4(self):
        self.assertTrue(usage._is_ipv4_literal("185.45.7.185"))

    def test_rejects_non_ipv4_and_non_string(self):
        self.assertFalse(usage._is_ipv4_literal("not-an-ip"))
        self.assertFalse(usage._is_ipv4_literal("2606:4700::1"))
        self.assertFalse(usage._is_ipv4_literal(None))


class ResolveHostViaDohTests(unittest.TestCase):
    def _mock_response(self, payload_bytes):
        cm = mock.MagicMock()
        cm.__enter__.return_value.read.return_value = payload_bytes
        return cm

    def test_parses_a_record_from_doh_response(self):
        body = b'{"Answer": [{"type": 1, "data": "185.45.7.185"}]}'
        with mock.patch("urllib.request.urlopen", return_value=self._mock_response(body)):
            self.assertEqual(usage._resolve_host_via_doh("status.claude.com", 3), "185.45.7.185")

    def test_ignores_non_a_records(self):
        body = b'{"Answer": [{"type": 16, "data": "some txt record"}]}'
        with mock.patch("urllib.request.urlopen", return_value=self._mock_response(body)):
            self.assertIsNone(usage._resolve_host_via_doh("status.claude.com", 3))

    def test_returns_none_on_request_failure(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("network unreachable")):
            self.assertIsNone(usage._resolve_host_via_doh("status.claude.com", 3))


class FetchStatusComponentsFallbackTests(unittest.TestCase):
    """验证 fetch_status_components 的编排逻辑：系统解析失败时自动切 DoH，
    不需要用户手动 flush 系统 DNS 缓存就能自愈；两条路径都失败才返回 None。"""

    def test_falls_back_to_doh_when_system_dns_fails(self):
        components_payload = {
            "components": [{"id": "yyzkbfz2thpt", "name": "Claude Code", "status": "operational"}]
        }
        with mock.patch.object(usage, "_resolve_host_with_deadline", return_value=None), \
             mock.patch.object(usage, "_resolve_host_via_doh", return_value="185.45.7.185") as doh, \
             mock.patch.object(usage, "_fetch_json_via_resolved_ip", return_value=components_payload) as fetch_ip:
            result = usage.fetch_status_components(usage.CLAUDE_STATUS_COMPONENTS_URL)

        doh.assert_called()
        fetch_ip.assert_called_with(
            usage.CLAUDE_STATUS_COMPONENTS_URL, "status.claude.com", "185.45.7.185", 5,
        )
        self.assertEqual(
            result,
            [{"id": "yyzkbfz2thpt", "name": "Claude Code", "status": "operational"}],
        )

    def test_prefers_system_resolution_when_healthy(self):
        components_payload = {"components": []}
        with mock.patch.object(usage, "_resolve_host_with_deadline", return_value="185.45.7.185"), \
             mock.patch.object(usage, "_resolve_host_via_doh") as doh, \
             mock.patch.object(usage, "_fetch_json_via_resolved_ip", return_value=components_payload):
            usage.fetch_status_components(usage.CLAUDE_STATUS_COMPONENTS_URL)

        doh.assert_not_called()

    def test_returns_none_when_both_resolution_paths_fail_after_retry(self):
        with mock.patch.object(usage, "_resolve_host_with_deadline", return_value=None), \
             mock.patch.object(usage, "_resolve_host_via_doh", return_value=None):
            result = usage.fetch_status_components(usage.CLAUDE_STATUS_COMPONENTS_URL)
        self.assertIsNone(result)

    def test_returns_none_when_ip_fetch_keeps_failing_after_retry(self):
        with mock.patch.object(usage, "_resolve_host_with_deadline", return_value="185.45.7.185"), \
             mock.patch.object(usage, "_fetch_json_via_resolved_ip", side_effect=OSError("boom")):
            result = usage.fetch_status_components(usage.CLAUDE_STATUS_COMPONENTS_URL)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
