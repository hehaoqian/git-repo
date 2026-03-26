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

"""End-to-end (integration) tests for the GitLab support.

These tests exercise the full flow from GitlabCentralCiHelper through
GitlabAPI using mock HTTP responses, verifying that all components
work together correctly end-to-end without requiring a real GitLab
instance.
"""

import json
import unittest
from unittest import mock

import gitlab
from subcmds import upload


def _make_http_response(status, body):
    """Build a minimal mock HTTPResponse."""
    resp = mock.MagicMock()
    resp.status = status
    if isinstance(body, str):
        body = body.encode("utf-8")
    resp.read.return_value = body
    return resp


def _mock_http_backend(responses):
    """Return a mock http connection class that replays responses in order.

    Args:
        responses: list of (status, body) tuples to return in sequence.
    """
    call_count = 0

    def get_response(*args, **kwargs):
        nonlocal call_count
        if call_count < len(responses):
            status, body = responses[call_count]
            call_count += 1
        else:
            # Default: return 200 OK with empty body.
            status, body = 200, b"{}"
        return _make_http_response(status, body)

    conn = mock.MagicMock()
    conn.getresponse.side_effect = get_response
    conn_class = mock.MagicMock(return_value=conn)
    return conn_class


class GitlabAPIFullFlowTests(unittest.TestCase):
    """End-to-end tests for common GitlabAPI call sequences."""

    def setUp(self):
        self.api = gitlab.GitlabAPI("gitlab.example.com", True)

    def test_full_pipeline_trigger_flow(self):
        """Access token validation + pipeline trigger in sequence."""
        token_body = json.dumps({"active": True}).encode()
        pipeline_body = json.dumps(
            {"web_url": "https://gitlab.example.com/group/ci/-/pipelines/42"}
        ).encode()

        conn_class = _mock_http_backend([
            (200, token_body),    # AccessTokenIsValid
            (201, pipeline_body), # TriggerPipeline
        ])
        with mock.patch("http.client.HTTPSConnection", conn_class):
            is_valid = self.api.AccessTokenIsValid("my-token")
            self.assertTrue(is_valid)

            url = self.api.TriggerPipeline(
                token="my-token",
                project_path="group/ci-project",
                branch="main",
                variables={"TOPIC": "topic::feat-X", "MANIFEST": "default.xml"},
            )
        self.assertIn("pipelines/42", url)

    def test_full_mr_creation_and_close_flow(self):
        """Branch creation + MR creation + MR close in sequence."""
        branch_body = json.dumps({"name": "temp-branch"}).encode()
        mr_body = json.dumps(
            {"web_url": "https://gitlab.example.com/g/ci/-/merge_requests/7"}
        ).encode()
        close_body = json.dumps({"state": "closed"}).encode()

        conn_class = _mock_http_backend([
            (201, branch_body),   # CreateBranch
            (201, mr_body),       # CreateMR
            (200, close_body),    # CloseMR
        ])
        with mock.patch("http.client.HTTPSConnection", conn_class):
            branch_ok = self.api.CreateBranch(
                token="tok",
                project_path="g/ci",
                branch_name="temp-branch",
                ref="main",
            )
            self.assertTrue(branch_ok)

            mr_url = self.api.CreateMR(
                token="tok",
                project_path="g/ci",
                source_branch="temp-branch",
                target_branch="main",
                title="Auto MR for topic::feat-X",
                labels=["topic::feat-X"],
            )
            self.assertIn("merge_requests/7", mr_url)

            closed = self.api.CloseMR(token="tok", project_path="g/ci", merge_request_iid=7)
            self.assertTrue(closed)

    def test_get_labels_then_trigger_pipeline_flow(self):
        """Fetch MR labels to discover topics, then trigger pipeline."""
        labels_body = json.dumps([
            {"labels": ["topic::my-topic", "bug"]},
            {"labels": ["enhancement"]},
        ]).encode()
        pipeline_body = json.dumps(
            {"web_url": "https://gitlab.example.com/pipelines/99"}
        ).encode()

        conn_class = _mock_http_backend([
            (200, labels_body),    # GetLabelsOfMRs
            (201, pipeline_body),  # TriggerPipeline
        ])
        with mock.patch("http.client.HTTPSConnection", conn_class):
            labels = self.api.GetLabelsOfMRs(
                token="tok", project_path="g/repo", source_branch="my-branch"
            )
            topics = [l for l in labels if l.startswith("topic::")]
            self.assertEqual(topics, ["topic::my-topic"])

            url = self.api.TriggerPipeline(
                token="tok",
                project_path="g/ci",
                branch="main",
                variables={"MONOREPO_TOPIC_LABEL": topics[0]},
            )
        self.assertIn("pipelines/99", url)


class GitlabCentralCiHelperE2ETests(unittest.TestCase):
    """End-to-end tests for GitlabCentralCiHelper."""

    def _make_manifest(self, **kwargs):
        manifest = mock.MagicMock()
        manifest.default.gitlab_url = kwargs.get(
            "gitlab_url", "https://gitlab.example.com"
        )
        manifest.default.enable_central_ci_pipeline = kwargs.get(
            "enable_central_ci_pipeline", False
        )
        manifest.default.mono_upload_create_mr_for_central_ci_project = kwargs.get(
            "mono_upload_create_mr_for_central_ci_project", False
        )
        manifest.default.central_ci_pipeline_must_success = kwargs.get(
            "central_ci_pipeline_must_success", False
        )
        manifest.manifestProject.config.GetString.return_value = "valid-token"
        manifest.push_options = None
        manifest.mr_title_suffix = None
        manifest.RawManifestFileName.return_value = "default.xml"

        proj = mock.MagicMock()
        proj.RootGroup = "myorg"
        proj.GitlabPath = "myorg/repo1"
        manifest.projects = [proj]
        return manifest

    def _make_branches(self, *topic_lists):
        """Create mock ReviewableBranch list.

        Each argument is a list of labels for one branch's open MRs.
        """
        branches = []
        for labels in topic_lists:
            branch = mock.MagicMock()
            branch.name = "feature-branch"
            branch.project.GitlabPath = "myorg/repo1"
            branches.append((branch, labels))
        return [b for b, _ in branches], {b.name: l for b, l in branches}

    def test_trigger_pipelines_end_to_end(self):
        """Full flow: token is valid, labels are fetched, pipeline triggered."""
        manifest = self._make_manifest(enable_central_ci_pipeline=True)

        branch = mock.MagicMock()
        branch.name = "my-feature"
        branch.project.GitlabPath = "myorg/repo1"

        helper = upload.GitlabCentralCiHelper(manifest)
        helper.gitlab_api = mock.MagicMock()
        helper.gitlab_api.AccessTokenIsValid.return_value = True
        helper.gitlab_api.GetLabelsOfMRs.return_value = [
            "topic::feat-A", "bug"
        ]
        helper.gitlab_api.TriggerPipeline.return_value = (
            "https://gitlab.example.com/pipelines/1"
        )

        helper.TriggerPipelines([branch])

        helper.gitlab_api.GetLabelsOfMRs.assert_called_once()
        helper.gitlab_api.TriggerPipeline.assert_called_once()
        _, trigger_kwargs = helper.gitlab_api.TriggerPipeline.call_args
        self.assertEqual(
            trigger_kwargs["variables"]["MONOREPO_TOPIC_LABEL"], "topic::feat-A"
        )
        self.assertEqual(
            trigger_kwargs["variables"]["MANIFEST_FILE"], "default.xml"
        )

    def test_create_mr_end_to_end(self):
        """Full flow: token validated, branch created, MR created and closed."""
        manifest = self._make_manifest(
            mono_upload_create_mr_for_central_ci_project=True
        )
        branch = mock.MagicMock()
        branch.name = "feature-branch"
        branch.project.GitlabPath = "myorg/repo1"

        helper = upload.GitlabCentralCiHelper(manifest)
        helper.gitlab_api = mock.MagicMock()
        helper.gitlab_api.AccessTokenIsValid.return_value = True
        helper.gitlab_api.GetLabelsOfMRs.return_value = ["topic::my-feat", "other"]
        helper.gitlab_api.CreateBranch.return_value = True
        helper.gitlab_api.CreateMR.return_value = (
            "https://gitlab.example.com/myorg/monorepo-ci-project/-/merge_requests/3"
        )
        helper.gitlab_api.CloseMR.return_value = True

        helper.CreateMRForCentralCiProject([branch])

        # Verify branch was created with correct name
        branch_kwargs = helper.gitlab_api.CreateBranch.call_args[1]
        self.assertIn("my-feat", branch_kwargs["branch_name"])

        # Verify MR was created with correct label
        mr_kwargs = helper.gitlab_api.CreateMR.call_args[1]
        self.assertIn("topic::my-feat", mr_kwargs["labels"])

        # Verify MR was closed with correct IID
        close_kwargs = helper.gitlab_api.CloseMR.call_args[1]
        self.assertEqual(str(close_kwargs["merge_request_iid"]), "3")

    def test_full_upload_and_report_triggers_both_helpers(self):
        """_UploadAndReport triggers both MR creation and pipeline when both enabled."""
        cmd = upload.Upload()
        cmd.manifest = self._make_manifest(
            enable_central_ci_pipeline=True,
            mono_upload_create_mr_for_central_ci_project=True,
        )

        opt, _ = cmd.OptionParser.parse_args([])
        opt.dryrun = False

        branch = mock.MagicMock()
        branch.uploaded = False

        helper_instance = mock.MagicMock()

        with mock.patch.object(cmd, "_AppendAutoList", return_value=None):
            with mock.patch.object(cmd, "git_event_log"):
                with mock.patch.object(cmd, "_UploadBranch", return_value=None):
                    with mock.patch(
                        "subcmds.upload.GitlabCentralCiHelper",
                        return_value=helper_instance,
                    ):
                        cmd._UploadAndReport(opt, [branch], mock.MagicMock())

        helper_instance.CreateMRForCentralCiProject.assert_called_once()
        helper_instance.TriggerPipelines.assert_called_once()

    def test_no_double_api_call_for_same_topic_across_branches(self):
        """TriggerPipelines only triggers once per unique topic."""
        manifest = self._make_manifest()
        branch1 = mock.MagicMock()
        branch1.name = "branch1"
        branch1.project.GitlabPath = "myorg/repo1"
        branch2 = mock.MagicMock()
        branch2.name = "branch2"
        branch2.project.GitlabPath = "myorg/repo2"

        helper = upload.GitlabCentralCiHelper(manifest)
        helper.gitlab_api = mock.MagicMock()
        helper.gitlab_api.AccessTokenIsValid.return_value = True
        # Both branches have the same topic label.
        helper.gitlab_api.GetLabelsOfMRs.return_value = ["topic::shared-topic"]
        helper.gitlab_api.TriggerPipeline.return_value = (
            "https://gitlab.example.com/pipelines/1"
        )

        helper.TriggerPipelines([branch1, branch2])

        # Despite two branches having the same topic, pipeline triggered once.
        helper.gitlab_api.TriggerPipeline.assert_called_once()


if __name__ == "__main__":
    unittest.main()
