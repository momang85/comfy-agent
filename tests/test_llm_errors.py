# -*- coding: utf-8 -*-
"""LLM 报错人话化单测（离线）。

背景（实测缺陷）：方舟返回 429 时，界面/设置面板原样显示
`LLM HTTP 429: {"error":{"code":"SetLimitExceeded","message":"Your account
[2132184326] has reached the set usage limit ... To continue`（400 字符硬截断，
句子被砍半）。用户看到一屏 JSON，不知道是"Key 错"还是"没额度"，也不知道该动哪里。

这里锁住四条：
  1. 服务商错误 JSON 里的 code / message 被解析出来，不再把裸 JSON 甩给用户
  2. 按状态码给出"是什么问题 + 该动哪里"的建议
  3. 消息仍以 "LLM HTTP <code>" 开头 —— `_is_transient_error()` 靠它识别 5xx
  4. 长响应体不再在 JSON 中间截断（解读在解析之后做）
"""
import io
import unittest
import urllib.error

from brain.llm import (LLMClient, LLMError, _is_transient_error,
                       _parse_error_payload, explain_http_error)

VOLC_429 = (
    '{"error":{"code":"SetLimitExceeded","message":"Your account [2132184326] '
    'has reached the set usage limit for the [glm-5-3-flash] model, and the '
    'model service has been paused. To continue using the model service, '
    'please raise the limit."}}'
)


def _boom(code: int, body: str):
    """构造一个"一打开就抛 HTTPError"的 opener，避免任何真实网络访问。"""

    class _Boom:
        def open(self, req, timeout=None):
            raise urllib.error.HTTPError(
                req.full_url, code, "boom", {}, io.BytesIO(body.encode("utf-8")))

    return _Boom()


def _client(code: int, body: str) -> LLMClient:
    # 回环 http 是白名单（本地推理服务器），且 opener 立刻抛错，不会真的联网
    c = LLMClient(base_url="http://localhost/v1",
                  api_key="test-key-000000", model="glm-5-3-flash")
    c._opener = _boom(code, body)
    return c


class TestParseErrorPayload(unittest.TestCase):
    def test_openai_style(self):
        code, msg = _parse_error_payload(VOLC_429)
        self.assertEqual(code, "SetLimitExceeded")
        self.assertIn("has reached the set usage limit", msg)

    def test_error_as_string(self):
        self.assertEqual(_parse_error_payload('{"error":"bad key"}'),
                         ("", "bad key"))

    def test_plain_message_field(self):
        self.assertEqual(_parse_error_payload('{"message":"model not found"}'),
                         ("", "model not found"))

    def test_non_json_is_kept_as_is(self):
        self.assertEqual(_parse_error_payload("502 Bad Gateway"),
                         ("", "502 Bad Gateway"))


class TestExplainHttpError(unittest.TestCase):
    def test_429_names_code_and_suggests_action(self):
        text = explain_http_error(429, VOLC_429)
        self.assertTrue(text.startswith("LLM HTTP 429"))
        self.assertIn("SetLimitExceeded", text)
        self.assertIn("额度", text)
        self.assertIn("建议", text)
        # 不该把裸 JSON 当正文甩出去
        self.assertNotIn('{"error"', text)

    def test_401_plain_text(self):
        text = explain_http_error(401, "invalid api key")
        self.assertIn("API Key", text)
        self.assertIn("invalid api key", text)

    def test_5xx_stays_transient(self):
        text = explain_http_error(502, "<html>bad gateway</html>")
        self.assertIn("http 502", text.lower())
        self.assertTrue(_is_transient_error(text),
                        "5xx 必须仍被识别为瞬时错误（否则会把网关抖动说成不可用）")

    def test_unknown_code_still_readable(self):
        text = explain_http_error(418, "I'm a teapot")
        self.assertIn("LLM HTTP 418", text)


class TestClientRaisesHumanized(unittest.TestCase):
    def test_post_parses_long_body(self):
        """长响应体内联解析：过去的 400 字符截断会把 JSON 砍断。"""
        filler = "lorem ipsum " * 80
        body = ('{"error":{"code":"SetLimitExceeded","message":"'
                + filler + 'end of message"}}')
        with self.assertRaises(LLMError) as ctx:
            _client(429, body)._post({"model": "m", "messages": []})
        msg = str(ctx.exception)
        self.assertIn("SetLimitExceeded", msg)
        self.assertIn("end of message", msg)   # 结尾的"怎么解决"保住了
        self.assertIn("省略", msg)              # 中间过长的部分被掐掉

    def test_stream_429_humanized(self):
        c = _client(429, VOLC_429)
        with self.assertRaises(LLMError) as ctx:
            list(c.chat_stream([{"role": "user", "content": "hi"}]))
        self.assertIn("额度", str(ctx.exception))

    def test_stream_falls_back_on_400(self):
        """400 仍走"非流式回退"，只是回退后的报错也人话化。"""
        c = _client(400, '{"error":{"message":"stream not supported"}}')
        with self.assertRaises(LLMError) as ctx:
            list(c.chat_stream([{"role": "user", "content": "hi"}]))
        self.assertIn("stream not supported", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
