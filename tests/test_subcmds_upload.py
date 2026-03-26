# Copyright (C) 2023 The Android Open Source Project
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

"""Unittests for the subcmds/upload.py module."""

import unittest
from unittest import mock

from error import GitError
from error import UploadError
from subcmds import upload


class UnexpectedError(Exception):
    """An exception not expected by upload command."""


class UploadCommand(unittest.TestCase):
    """Check registered all_commands."""

    def setUp(self):
        self.cmd = upload.Upload()
        self.branch = mock.MagicMock()
        self.people = mock.MagicMock()
        self.opt, _ = self.cmd.OptionParser.parse_args([])
        mock.patch.object(
            self.cmd, "_AppendAutoList", return_value=None
        ).start()
        mock.patch.object(self.cmd, "git_event_log").start()

    def tearDown(self):
        mock.patch.stopall()

    def test_UploadAndReport_UploadError(self):
        """Check UploadExitError raised when UploadError encountered."""
        side_effect = UploadError("upload error")
        with mock.patch.object(
            self.cmd, "_UploadBranch", side_effect=side_effect
        ):
            with self.assertRaises(upload.UploadExitError):
                self.cmd._UploadAndReport(self.opt, [self.branch], self.people)

    def test_UploadAndReport_GitError(self):
        """Check UploadExitError raised when GitError encountered."""
        side_effect = GitError("some git error")
        with mock.patch.object(
            self.cmd, "_UploadBranch", side_effect=side_effect
        ):
            with self.assertRaises(upload.UploadExitError):
                self.cmd._UploadAndReport(self.opt, [self.branch], self.people)

    def test_UploadAndReport_UnhandledError(self):
        """Check UnexpectedError passed through."""
        side_effect = UnexpectedError("some os error")
        with mock.patch.object(
            self.cmd, "_UploadBranch", side_effect=side_effect
        ):
            with self.assertRaises(type(side_effect)):
                self.cmd._UploadAndReport(self.opt, [self.branch], self.people)


class GitlabCentralCiHelperTests(unittest.TestCase):
    """Tests for GitlabCentralCiHelper."""

    def _make_manifest(
        self,
        gitlab_url="https://gitlab.example.com",
        enable_central_ci_pipeline=False,
        mono_upload_create_mr_for_central_ci_project=False,
        central_ci_pipeline_must_success=False,
        projects=None,
    ):
        """Build a minimal mock manifest."""
        manifest = mock.MagicMock()
        manifest.default.gitlab_url = gitlab_url
        manifest.default.enable_central_ci_pipeline = enable_central_ci_pipeline
        manifest.default.mono_upload_create_mr_for_central_ci_project = (
            mono_upload_create_mr_for_central_ci_project
        )
        manifest.default.central_ci_pipeline_must_success = (
            central_ci_pipeline_must_success
        )
        manifest.manifestProject.config.GetString.return_value = None
        manifest.push_options = None
        manifest.mr_title_suffix = None

        if projects is None:
            proj = mock.MagicMock()
            proj.RootGroup = "myorg"
            proj.GitlabPath = "myorg/repo1"
            projects = [proj]
        manifest.projects = projects
        return manifest

    def _make_helper(self, manifest=None, **kwargs):
        """Create a GitlabCentralCiHelper with mocked API."""
        if manifest is None:
            manifest = self._make_manifest(**kwargs)
        helper = upload.GitlabCentralCiHelper(manifest)
        helper.gitlab_api = mock.MagicMock()
        return helper

    def test_init_sets_ci_project_path(self):
        helper = self._make_helper()
        self.assertEqual(helper.ci_project_path, "myorg/monorepo-ci-project")

    def test_init_raises_if_no_projects(self):
        manifest = self._make_manifest(projects=[])
        with self.assertRaises(ValueError):
            upload.GitlabCentralCiHelper(manifest)

    def test_get_user_id_by_username_none_returns_none(self):
        helper = self._make_helper()
        self.assertIsNone(helper.GetUserIdByUsername(None))

    def test_get_user_id_by_username_empty_returns_none(self):
        helper = self._make_helper()
        self.assertIsNone(helper.GetUserIdByUsername(""))

    def test_get_user_id_by_username_numeric_string_returns_int(self):
        helper = self._make_helper()
        result = helper.GetUserIdByUsername("42")
        self.assertEqual(result, 42)
        self.assertIsInstance(result, int)

    def test_get_user_id_by_username_calls_api(self):
        helper = self._make_helper()
        helper.manifest.manifestProject.config.GetString.return_value = "tok"
        helper.gitlab_api.AccessTokenIsValid.return_value = True
        helper.gitlab_api.GetUser.return_value = {"id": 7, "username": "alice"}
        result = helper.GetUserIdByUsername("alice")
        self.assertEqual(result, 7)
        helper.gitlab_api.GetUser.assert_called_once_with(
            token="tok", username="alice"
        )

    def test_get_user_id_by_username_user_not_found(self):
        helper = self._make_helper()
        helper.manifest.manifestProject.config.GetString.return_value = "tok"
        helper.gitlab_api.AccessTokenIsValid.return_value = True
        helper.gitlab_api.GetUser.return_value = None
        self.assertIsNone(helper.GetUserIdByUsername("nobody"))

    def test_get_topics_from_gitlab_mrs_filters_topic_labels(self):
        helper = self._make_helper()
        helper.manifest.manifestProject.config.GetString.return_value = "tok"
        helper.gitlab_api.AccessTokenIsValid.return_value = True
        helper.gitlab_api.GetLabelsOfMRs.return_value = [
            "topic::feat-A",
            "bug",
            "topic::feat-B",
            "enhancement",
        ]
        topics = helper._GetTopicsFromGitLabMRs("myorg/repo1", "my-branch")
        self.assertIn("topic::feat-A", topics)
        self.assertIn("topic::feat-B", topics)
        self.assertNotIn("bug", topics)
        self.assertNotIn("enhancement", topics)

    def test_get_topics_from_gitlab_mrs_empty_when_no_token(self):
        helper = self._make_helper()
        helper.manifest.manifestProject.config.GetString.return_value = None
        # Simulate user pressing Enter (empty input).
        with mock.patch("builtins.input", return_value=""):
            result = helper._GetTopicsFromGitLabMRs("myorg/repo1", "branch")
        self.assertEqual(result, [])

    def test_trigger_pipelines_skips_when_no_topics(self):
        helper = self._make_helper()
        with mock.patch.object(
            helper, "_FetchTopics", return_value=set()
        ):
            with mock.patch.object(helper, "TriggerPipeline") as mock_trigger:
                helper.TriggerPipelines([])
        mock_trigger.assert_not_called()

    def test_trigger_pipelines_calls_trigger_for_each_topic(self):
        helper = self._make_helper()
        with mock.patch.object(
            helper, "_FetchTopics", return_value={"topic::A", "topic::B"}
        ):
            with mock.patch.object(helper, "TriggerPipeline") as mock_trigger:
                helper.TriggerPipelines([])
        self.assertEqual(mock_trigger.call_count, 2)

    def test_trigger_pipeline_builds_correct_variables(self):
        helper = self._make_helper()
        helper.manifest.manifestProject.config.GetString.return_value = "tok"
        helper.gitlab_api.AccessTokenIsValid.return_value = True
        helper.manifest.RawManifestFileName.return_value = "default.xml"
        helper.gitlab_api.TriggerPipeline.return_value = (
            "https://gitlab.example.com/pipelines/1"
        )
        helper.TriggerPipeline("topic::my-feature")
        _, kwargs = helper.gitlab_api.TriggerPipeline.call_args
        self.assertEqual(kwargs["variables"]["MONOREPO_TOPIC_LABEL"], "topic::my-feature")
        self.assertEqual(kwargs["variables"]["MANIFEST_FILE"], "default.xml")

    def test_trigger_pipeline_adds_must_success_variable(self):
        manifest = self._make_manifest(central_ci_pipeline_must_success=True)
        helper = self._make_helper(manifest=manifest)
        helper.manifest.manifestProject.config.GetString.return_value = "tok"
        helper.gitlab_api.AccessTokenIsValid.return_value = True
        helper.manifest.RawManifestFileName.return_value = "default.xml"
        helper.gitlab_api.TriggerPipeline.return_value = (
            "https://gitlab.example.com/pipelines/1"
        )
        helper.TriggerPipeline("topic::test")
        _, kwargs = helper.gitlab_api.TriggerPipeline.call_args
        self.assertEqual(
            kwargs["variables"]["CENTRAL_CI_PIPELINE_MUST_SUCCESS"], "true"
        )

    def test_create_mr_for_central_ci_project_skips_when_no_topics(self):
        helper = self._make_helper()
        with mock.patch.object(
            helper, "_FetchTopics", return_value=set()
        ):
            helper.CreateMRForCentralCiProject([])
        helper.gitlab_api.CreateMR.assert_not_called()

    def test_create_mr_for_central_ci_project_creates_and_closes_mr(self):
        helper = self._make_helper()
        helper.manifest.manifestProject.config.GetString.return_value = "tok"
        helper.gitlab_api.AccessTokenIsValid.return_value = True
        helper.gitlab_api.CreateBranch.return_value = True
        helper.gitlab_api.CreateMR.return_value = (
            "https://gitlab.example.com/g/ci/-/merge_requests/5"
        )
        helper.gitlab_api.CloseMR.return_value = True
        with mock.patch.object(
            helper,
            "_FetchTopics",
            return_value={"topic::my-feat"},
        ):
            helper.CreateMRForCentralCiProject([])
        helper.gitlab_api.CreateMR.assert_called_once()
        helper.gitlab_api.CloseMR.assert_called_once()

    def test_create_mr_with_title_suffix(self):
        helper = self._make_helper()
        helper.manifest.manifestProject.config.GetString.return_value = "tok"
        helper.gitlab_api.AccessTokenIsValid.return_value = True
        helper.gitlab_api.CreateBranch.return_value = True
        helper.gitlab_api.CreateMR.return_value = (
            "https://gitlab.example.com/g/ci/-/merge_requests/3"
        )
        helper.gitlab_api.CloseMR.return_value = True
        # Give the mr_title_suffix a value
        suffix_obj = mock.MagicMock()
        suffix_obj.central_ci_project = "[CI]"
        helper.manifest.mr_title_suffix = suffix_obj
        with mock.patch.object(
            helper,
            "_FetchTopics",
            return_value={"topic::feat"},
        ):
            helper.CreateMRForCentralCiProject([])
        _, kwargs = helper.gitlab_api.CreateMR.call_args
        self.assertIn("[CI]", kwargs["title"])


class UploadAndReportGitlabTests(unittest.TestCase):
    """Tests for _UploadAndReport GitLab integration."""

    def setUp(self):
        self.cmd = upload.Upload()
        self.branch = mock.MagicMock()
        self.branch.uploaded = False
        self.people = mock.MagicMock()
        self.opt, _ = self.cmd.OptionParser.parse_args([])
        mock.patch.object(
            self.cmd, "_AppendAutoList", return_value=None
        ).start()
        mock.patch.object(self.cmd, "git_event_log").start()

    def tearDown(self):
        mock.patch.stopall()

    def _set_manifest_gitlab_url(self, url):
        self.cmd.manifest = mock.MagicMock()
        self.cmd.manifest.default.gitlab_url = url
        self.cmd.manifest.default.mono_upload_create_mr_for_central_ci_project = False
        self.cmd.manifest.default.enable_central_ci_pipeline = False

    def test_gitlab_helpers_not_called_without_gitlab_url(self):
        """No GitLab helpers invoked when gitlab_url is not set."""
        self._set_manifest_gitlab_url(None)
        with mock.patch.object(
            self.cmd, "_UploadBranch", return_value=None
        ):
            with mock.patch(
                "subcmds.upload.GitlabCentralCiHelper"
            ) as mock_helper_cls:
                self.opt.dryrun = False
                self.cmd._UploadAndReport(
                    self.opt, [self.branch], self.people
                )
        mock_helper_cls.assert_not_called()

    def test_gitlab_helpers_not_called_on_dryrun(self):
        """GitLab helpers are not invoked in dry-run mode."""
        self._set_manifest_gitlab_url("https://gitlab.example.com")
        with mock.patch.object(
            self.cmd, "_UploadBranch", return_value=None
        ):
            with mock.patch(
                "subcmds.upload.GitlabCentralCiHelper"
            ) as mock_helper_cls:
                self.opt.dryrun = True
                self.cmd._UploadAndReport(
                    self.opt, [self.branch], self.people
                )
        mock_helper_cls.assert_not_called()

    def test_mr_creation_called_when_enabled(self):
        """CreateMRForCentralCiProject is called when configured."""
        self._set_manifest_gitlab_url("https://gitlab.example.com")
        self.cmd.manifest.default.mono_upload_create_mr_for_central_ci_project = True
        helper_instance = mock.MagicMock()
        with mock.patch.object(
            self.cmd, "_UploadBranch", return_value=None
        ):
            with mock.patch(
                "subcmds.upload.GitlabCentralCiHelper",
                return_value=helper_instance,
            ):
                self.opt.dryrun = False
                self.cmd._UploadAndReport(
                    self.opt, [self.branch], self.people
                )
        helper_instance.CreateMRForCentralCiProject.assert_called_once()

    def test_trigger_pipelines_called_when_enabled(self):
        """TriggerPipelines is called when enable_central_ci_pipeline is True."""
        self._set_manifest_gitlab_url("https://gitlab.example.com")
        self.cmd.manifest.default.enable_central_ci_pipeline = True
        helper_instance = mock.MagicMock()
        with mock.patch.object(
            self.cmd, "_UploadBranch", return_value=None
        ):
            with mock.patch(
                "subcmds.upload.GitlabCentralCiHelper",
                return_value=helper_instance,
            ):
                self.opt.dryrun = False
                self.cmd._UploadAndReport(
                    self.opt, [self.branch], self.people
                )
        helper_instance.TriggerPipelines.assert_called_once()
