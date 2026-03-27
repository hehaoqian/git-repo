# Copyright (C) 2026 The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Unittests for the gitlab.py module."""

import http.client
import json
import ssl
import unittest
from io import BytesIO
from unittest import mock

import gitlab


def _make_response(status, body):
    """Build a minimal mock HTTPResponse with .status and .read()."""
    resp = mock.MagicMock(spec=http.client.HTTPResponse)
    resp.status = status
    if isinstance(body, str):
        body = body.encode("utf-8")
    resp.read.return_value = body
    # Support json.load(response) by making it iterable-like via a BytesIO
    resp.__iter__ = lambda self: iter(BytesIO(body))
    resp.read1 = lambda size=-1: body  # noqa: E731
    return resp


class GitlabAPIInitTests(unittest.TestCase):
    """Tests for GitlabAPI.__init__."""

    def test_https_url(self):
        api = gitlab.GitlabAPI("gitlab.example.com", True)
        self.assertEqual(api.domain, "gitlab.example.com")
        self.assertTrue(api.https)
        self.assertEqual(api.url, "https://gitlab.example.com")

    def test_http_url(self):
        api = gitlab.GitlabAPI("gitlab.example.com", False)
        self.assertFalse(api.https)
        self.assertEqual(api.url, "http://gitlab.example.com")


class HttpStatusSuccessTests(unittest.TestCase):
    """Tests for GitlabAPI._HttpStatusSuccess."""

    def setUp(self):
        self.api = gitlab.GitlabAPI("gitlab.example.com", True)

    def test_200_is_success(self):
        self.assertTrue(self.api._HttpStatusSuccess(200))

    def test_201_is_success(self):
        self.assertTrue(self.api._HttpStatusSuccess(201))

    def test_299_is_success(self):
        self.assertTrue(self.api._HttpStatusSuccess(299))

    def test_300_is_not_success(self):
        self.assertFalse(self.api._HttpStatusSuccess(300))

    def test_404_is_not_success(self):
        self.assertFalse(self.api._HttpStatusSuccess(404))

    def test_500_is_not_success(self):
        self.assertFalse(self.api._HttpStatusSuccess(500))

    def test_199_is_not_success(self):
        self.assertFalse(self.api._HttpStatusSuccess(199))


class EncodeProjectPathTests(unittest.TestCase):
    """Tests for GitlabAPI.encode_project_path."""

    def test_simple_path(self):
        result = gitlab.GitlabAPI.encode_project_path("group/repo")
        self.assertEqual(result, "group%2Frepo")

    def test_nested_path(self):
        result = gitlab.GitlabAPI.encode_project_path("root/sub/repo")
        self.assertEqual(result, "root%2Fsub%2Frepo")

    def test_strips_leading_slash(self):
        result = gitlab.GitlabAPI.encode_project_path("/group/repo")
        self.assertEqual(result, "group%2Frepo")

    def test_strips_trailing_slash(self):
        result = gitlab.GitlabAPI.encode_project_path("group/repo/")
        self.assertEqual(result, "group%2Frepo")

    def test_path_with_hyphens_and_dots(self):
        result = gitlab.GitlabAPI.encode_project_path("my-group/my.repo")
        self.assertEqual(result, "my-group%2Fmy.repo")


class RequestTests(unittest.TestCase):
    """Tests for GitlabAPI.Request."""

    def setUp(self):
        self.api = gitlab.GitlabAPI("gitlab.example.com", True)

    def _make_conn(self, response):
        """Return a mock connection class that returns the given response."""
        conn = mock.MagicMock()
        conn.getresponse.return_value = response
        conn_class = mock.MagicMock(return_value=conn)
        return conn_class, conn

    def test_get_request_success(self):
        """Successful GET request returns the response."""
        resp = _make_response(200, b"{}")
        conn_class, conn = self._make_conn(resp)
        with mock.patch("http.client.HTTPSConnection", conn_class):
            result = self.api.Request("GET", "/api/v4/test")
        self.assertEqual(result.status, 200)
        conn.request.assert_called_once_with("GET", "/api/v4/test")

    def test_token_added_to_headers(self):
        """When a token is provided, PRIVATE-TOKEN header is set."""
        resp = _make_response(200, b"{}")
        conn_class, conn = self._make_conn(resp)
        with mock.patch("http.client.HTTPSConnection", conn_class):
            self.api.Request("GET", "/api/v4/test", token="mytoken")
        _, kwargs = conn.request.call_args
        self.assertEqual(kwargs.get("headers", {}).get("PRIVATE-TOKEN"), "mytoken")

    def test_params_added_to_url(self):
        """Query params are appended to the path."""
        resp = _make_response(200, b"{}")
        conn_class, conn = self._make_conn(resp)
        with mock.patch("http.client.HTTPSConnection", conn_class):
            self.api.Request("GET", "/api/v4/test", params={"state": "opened"})
        args, _ = conn.request.call_args
        self.assertIn("state=opened", args[1])

    def test_retry_on_failure(self):
        """Request retries on non-success response."""
        fail_resp = _make_response(500, b"error")
        success_resp = _make_response(200, b"{}")
        conn = mock.MagicMock()
        conn.getresponse.side_effect = [fail_resp, success_resp]
        conn_class = mock.MagicMock(return_value=conn)
        with mock.patch("http.client.HTTPSConnection", conn_class):
            result = self.api.Request("GET", "/api/v4/test", retry=2)
        self.assertEqual(result.status, 200)
        self.assertEqual(conn.getresponse.call_count, 2)

    def test_no_retry_by_default(self):
        """Without retry=N, first response is returned regardless of status."""
        resp = _make_response(500, b"error")
        conn_class, conn = self._make_conn(resp)
        with mock.patch("http.client.HTTPSConnection", conn_class):
            result = self.api.Request("GET", "/api/v4/test", retry=1)
        self.assertEqual(result.status, 500)

    def test_http_connection_used_when_not_https(self):
        """Uses HTTPConnection for non-HTTPS."""
        api = gitlab.GitlabAPI("gitlab.example.com", False)
        resp = _make_response(200, b"{}")
        conn_class, _ = self._make_conn(resp)
        with mock.patch("http.client.HTTPConnection", conn_class):
            api.Request("GET", "/api/v4/test")
        conn_class.assert_called_once_with("gitlab.example.com")

    def test_ssl_error_calls_exit(self):
        """SSL errors cause sys.exit(1)."""
        conn = mock.MagicMock()
        conn.request.side_effect = ssl.SSLError("SSL error")
        conn_class = mock.MagicMock(return_value=conn)
        with mock.patch("http.client.HTTPSConnection", conn_class):
            with self.assertRaises(SystemExit):
                self.api.Request("GET", "/api/v4/test")

    def test_remote_disconnected_calls_exit(self):
        """RemoteDisconnected errors cause sys.exit(1)."""
        conn = mock.MagicMock()
        conn.request.side_effect = http.client.RemoteDisconnected()
        conn_class = mock.MagicMock(return_value=conn)
        with mock.patch("http.client.HTTPSConnection", conn_class):
            with self.assertRaises(SystemExit):
                self.api.Request("GET", "/api/v4/test")


class AccessTokenIsValidTests(unittest.TestCase):
    """Tests for GitlabAPI.AccessTokenIsValid."""

    def setUp(self):
        self.api = gitlab.GitlabAPI("gitlab.example.com", True)

    def test_valid_active_token(self):
        body = json.dumps({"active": True}).encode()
        resp = _make_response(200, body)
        with mock.patch.object(self.api, "Request", return_value=resp):
            self.assertTrue(self.api.AccessTokenIsValid("good-token"))

    def test_inactive_token(self):
        body = json.dumps({"active": False}).encode()
        resp = _make_response(200, body)
        with mock.patch.object(self.api, "Request", return_value=resp):
            self.assertFalse(self.api.AccessTokenIsValid("bad-token"))

    def test_non_200_response(self):
        resp = _make_response(401, b"Unauthorized")
        with mock.patch.object(self.api, "Request", return_value=resp):
            self.assertFalse(self.api.AccessTokenIsValid("bad-token"))

    def test_malformed_json(self):
        resp = _make_response(200, b"not-json")
        with mock.patch.object(self.api, "Request", return_value=resp):
            self.assertFalse(self.api.AccessTokenIsValid("token"))


class TriggerPipelineTests(unittest.TestCase):
    """Tests for GitlabAPI.TriggerPipeline."""

    def setUp(self):
        self.api = gitlab.GitlabAPI("gitlab.example.com", True)

    def test_success_returns_url(self):
        body = json.dumps({"web_url": "https://gitlab.example.com/pipeline/1"}).encode()
        resp = _make_response(201, body)
        with mock.patch.object(self.api, "Request", return_value=resp):
            url = self.api.TriggerPipeline(
                token="tok",
                project_path="group/ci-project",
                branch="main",
                variables={"KEY": "val"},
            )
        self.assertEqual(url, "https://gitlab.example.com/pipeline/1")

    def test_failure_exits(self):
        resp = _make_response(400, b"Bad Request")
        with mock.patch.object(self.api, "Request", return_value=resp):
            with self.assertRaises(SystemExit):
                self.api.TriggerPipeline(
                    token="tok",
                    project_path="group/ci",
                    branch="main",
                    variables={},
                )

    def test_404_exits_with_message(self):
        resp = _make_response(404, b"Not Found")
        with mock.patch.object(self.api, "Request", return_value=resp):
            with self.assertRaises(SystemExit):
                self.api.TriggerPipeline(
                    token="tok",
                    project_path="group/ci",
                    branch="main",
                    variables={},
                )

    def test_variables_sent_in_payload(self):
        body = json.dumps({"web_url": "https://gitlab.example.com/p/1"}).encode()
        resp = _make_response(201, body)
        with mock.patch.object(self.api, "Request", return_value=resp) as mock_req:
            self.api.TriggerPipeline(
                token="tok",
                project_path="g/p",
                branch="main",
                variables={"MY_VAR": "hello"},
            )
        _, kwargs = mock_req.call_args
        payload = json.loads(kwargs["body"])
        self.assertEqual(payload["ref"], "main")
        self.assertIn({"key": "MY_VAR", "value": "hello"}, payload["variables"])


class GetLabelsOfMRsTests(unittest.TestCase):
    """Tests for GitlabAPI.GetLabelsOfMRs."""

    def setUp(self):
        self.api = gitlab.GitlabAPI("gitlab.example.com", True)

    def test_returns_labels_from_all_mrs(self):
        body = json.dumps([
            {"labels": ["topic::feat-A", "bug"]},
            {"labels": ["topic::feat-B"]},
        ]).encode()
        resp = _make_response(200, body)
        with mock.patch.object(self.api, "Request", return_value=resp):
            labels = self.api.GetLabelsOfMRs("tok", "g/p", "my-branch")
        self.assertIn("topic::feat-A", labels)
        self.assertIn("topic::feat-B", labels)
        self.assertIn("bug", labels)

    def test_returns_empty_on_failure(self):
        resp = _make_response(403, b"Forbidden")
        with mock.patch.object(self.api, "Request", return_value=resp):
            labels = self.api.GetLabelsOfMRs("tok", "g/p", "branch")
        self.assertEqual(labels, [])

    def test_returns_empty_when_no_mrs(self):
        resp = _make_response(200, b"[]")
        with mock.patch.object(self.api, "Request", return_value=resp):
            labels = self.api.GetLabelsOfMRs("tok", "g/p", "branch")
        self.assertEqual(labels, [])


class GetUserTests(unittest.TestCase):
    """Tests for GitlabAPI.GetUser."""

    def setUp(self):
        self.api = gitlab.GitlabAPI("gitlab.example.com", True)

    def test_returns_first_user(self):
        body = json.dumps([{"id": 42, "username": "jdoe"}]).encode()
        resp = _make_response(200, body)
        with mock.patch.object(self.api, "Request", return_value=resp):
            user = self.api.GetUser("tok", "jdoe")
        self.assertEqual(user["id"], 42)

    def test_returns_none_when_empty_list(self):
        resp = _make_response(200, b"[]")
        with mock.patch.object(self.api, "Request", return_value=resp):
            user = self.api.GetUser("tok", "unknown")
        self.assertIsNone(user)

    def test_returns_none_on_failure(self):
        resp = _make_response(404, b"Not Found")
        with mock.patch.object(self.api, "Request", return_value=resp):
            user = self.api.GetUser("tok", "jdoe")
        self.assertIsNone(user)

    def test_returns_none_for_empty_username(self):
        with mock.patch.object(self.api, "Request") as mock_req:
            user = self.api.GetUser("tok", "")
        mock_req.assert_not_called()
        self.assertIsNone(user)

    def test_passes_username_as_param(self):
        resp = _make_response(200, b"[]")
        with mock.patch.object(self.api, "Request", return_value=resp) as mock_req:
            self.api.GetUser("tok", "alice")
        _, kwargs = mock_req.call_args
        self.assertEqual(kwargs.get("params", {}).get("username"), "alice")


class CreateBranchTests(unittest.TestCase):
    """Tests for GitlabAPI.CreateBranch."""

    def setUp(self):
        self.api = gitlab.GitlabAPI("gitlab.example.com", True)

    def test_success_returns_true(self):
        resp = _make_response(201, b"{}")
        with mock.patch.object(self.api, "Request", return_value=resp):
            result = self.api.CreateBranch("tok", "g/p", "new-branch", "main")
        self.assertTrue(result)

    def test_failure_returns_false(self):
        resp = _make_response(400, b"Bad Request")
        with mock.patch.object(self.api, "Request", return_value=resp):
            result = self.api.CreateBranch("tok", "g/p", "new-branch", "main")
        self.assertFalse(result)


class CreateMRTests(unittest.TestCase):
    """Tests for GitlabAPI.CreateMR."""

    def setUp(self):
        self.api = gitlab.GitlabAPI("gitlab.example.com", True)

    def test_success_returns_url(self):
        body = json.dumps({"web_url": "https://gitlab.example.com/g/p/-/merge_requests/7"}).encode()
        resp = _make_response(201, body)
        with mock.patch.object(self.api, "Request", return_value=resp):
            url = self.api.CreateMR(
                token="tok",
                project_path="g/p",
                source_branch="feature",
                target_branch="main",
                title="My MR",
            )
        self.assertEqual(url, "https://gitlab.example.com/g/p/-/merge_requests/7")

    def test_failure_returns_empty_string(self):
        resp = _make_response(400, b"Bad Request")
        with mock.patch.object(self.api, "Request", return_value=resp):
            url = self.api.CreateMR(
                token="tok",
                project_path="g/p",
                source_branch="feature",
                target_branch="main",
                title="MR",
            )
        self.assertEqual(url, "")

    def test_labels_sent_in_payload(self):
        body = json.dumps({"web_url": "https://gitlab.example.com/mr/1"}).encode()
        resp = _make_response(201, body)
        with mock.patch.object(self.api, "Request", return_value=resp) as mock_req:
            self.api.CreateMR(
                token="tok",
                project_path="g/p",
                source_branch="feature",
                target_branch="main",
                title="MR",
                labels=["topic::my-topic", "bug"],
            )
        _, kwargs = mock_req.call_args
        payload = json.loads(kwargs["body"])
        self.assertEqual(payload["labels"], ["topic::my-topic", "bug"])


class CloseMRTests(unittest.TestCase):
    """Tests for GitlabAPI.CloseMR."""

    def setUp(self):
        self.api = gitlab.GitlabAPI("gitlab.example.com", True)

    def test_success_returns_true(self):
        resp = _make_response(200, b"{}")
        with mock.patch.object(self.api, "Request", return_value=resp):
            self.assertTrue(self.api.CloseMR("tok", "g/p", 42))

    def test_failure_returns_false(self):
        resp = _make_response(404, b"Not Found")
        with mock.patch.object(self.api, "Request", return_value=resp):
            self.assertFalse(self.api.CloseMR("tok", "g/p", 42))

    def test_close_state_event_in_payload(self):
        resp = _make_response(200, b"{}")
        with mock.patch.object(self.api, "Request", return_value=resp) as mock_req:
            self.api.CloseMR("tok", "g/p", 5)
        _, kwargs = mock_req.call_args
        payload = json.loads(kwargs["body"])
        self.assertEqual(payload["state_event"], "close")


class GetMRTests(unittest.TestCase):
    """Tests for GitlabAPI.GetMR."""

    def setUp(self):
        self.api = gitlab.GitlabAPI("gitlab.example.com", True)

    def test_success_returns_dict(self):
        body = json.dumps({"iid": 7, "title": "Test MR"}).encode()
        resp = _make_response(200, body)
        with mock.patch.object(self.api, "Request", return_value=resp):
            mr = self.api.GetMR("tok", "g/p", 7)
        self.assertEqual(mr["iid"], 7)

    def test_failure_returns_none(self):
        resp = _make_response(404, b"Not Found")
        with mock.patch.object(self.api, "Request", return_value=resp):
            mr = self.api.GetMR("tok", "g/p", 99)
        self.assertIsNone(mr)


class UpdateMRTests(unittest.TestCase):
    """Tests for GitlabAPI.UpdateMR."""

    def setUp(self):
        self.api = gitlab.GitlabAPI("gitlab.example.com", True)

    def test_update_title_success(self):
        resp = _make_response(200, b"{}")
        with mock.patch.object(self.api, "Request", return_value=resp):
            self.assertTrue(self.api.UpdateMR("tok", "g/p", 1, title="New Title"))

    def test_update_failure_returns_false(self):
        resp = _make_response(404, b"Not Found")
        with mock.patch.object(self.api, "Request", return_value=resp):
            self.assertFalse(self.api.UpdateMR("tok", "g/p", 1, title="T"))

    def test_nothing_to_update_returns_true_without_request(self):
        with mock.patch.object(self.api, "Request") as mock_req:
            result = self.api.UpdateMR("tok", "g/p", 1)
        mock_req.assert_not_called()
        self.assertTrue(result)

    def test_all_fields_in_payload(self):
        resp = _make_response(200, b"{}")
        with mock.patch.object(self.api, "Request", return_value=resp) as mock_req:
            self.api.UpdateMR(
                "tok", "g/p", 1,
                title="T",
                description="D",
                labels=["l1"],
            )
        _, kwargs = mock_req.call_args
        payload = json.loads(kwargs["body"])
        self.assertEqual(payload["title"], "T")
        self.assertEqual(payload["description"], "D")
        self.assertEqual(payload["labels"], ["l1"])

    def test_kwargs_merged_into_payload(self):
        resp = _make_response(200, b"{}")
        with mock.patch.object(self.api, "Request", return_value=resp) as mock_req:
            self.api.UpdateMR("tok", "g/p", 1, state_event="close")
        _, kwargs = mock_req.call_args
        payload = json.loads(kwargs["body"])
        self.assertEqual(payload["state_event"], "close")


class DeleteBranchTests(unittest.TestCase):
    """Tests for GitlabAPI.DeleteBranch."""

    def setUp(self):
        self.api = gitlab.GitlabAPI("gitlab.example.com", True)

    def test_success_returns_true(self):
        resp = _make_response(204, b"")
        with mock.patch.object(self.api, "Request", return_value=resp):
            self.assertTrue(self.api.DeleteBranch("tok", "g/p", "my-branch"))

    def test_failure_returns_false(self):
        resp = _make_response(404, b"Not Found")
        with mock.patch.object(self.api, "Request", return_value=resp):
            self.assertFalse(self.api.DeleteBranch("tok", "g/p", "missing"))

    def test_branch_name_is_url_encoded_in_path(self):
        resp = _make_response(204, b"")
        with mock.patch.object(self.api, "Request", return_value=resp) as mock_req:
            self.api.DeleteBranch("tok", "g/p", "feature/my-branch")
        args, _ = mock_req.call_args
        # The path argument must have the slash percent-encoded.
        self.assertIn("feature%2Fmy-branch", args[1])

    def test_uses_delete_http_method(self):
        resp = _make_response(204, b"")
        with mock.patch.object(self.api, "Request", return_value=resp) as mock_req:
            self.api.DeleteBranch("tok", "g/p", "main")
        args, _ = mock_req.call_args
        self.assertEqual(args[0], "DELETE")


if __name__ == "__main__":
    unittest.main()
