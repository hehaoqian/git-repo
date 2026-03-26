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

from typing import Dict, List
import sys
import json
import http.client
import urllib.parse
import ssl
from typing import Optional
from http.client import HTTPResponse

VariableKey = str
VariableValue = str
PipelineUrl = str


class GitlabAPI:
    def __init__(self, domain: str, https: bool) -> None:
        self.domain = domain
        self.https = https
        self.url = f"https://{domain}" if https else f"http://{domain}"

    def Request(self, method, path, retry=1, token=None, **kwargs) -> HTTPResponse:
        if self.https:
            http_connection = http.client.HTTPSConnection
        else:
            http_connection = http.client.HTTPConnection

        params = kwargs.pop("params", None)
        if params:
            path += "?" + urllib.parse.urlencode(params)

        if token:
            headers = kwargs.get("headers", {})
            headers["PRIVATE-TOKEN"] = token
            kwargs["headers"] = headers

        response = None
        try:
            while retry > 0:
                conn = http_connection(self.domain)
                conn.request(method, path, **kwargs)
                response = conn.getresponse()
                if self._HttpStatusSuccess(response.status) or retry <= 1:
                    return response
                retry -= 1
        except Exception as err:
            print(f"❌ Failed to connect to '{self.url}'")
            if issubclass(type(err), ssl.SSLError):
                print(
                    "Possible reasons are that the ssl certificate has expired or the domain does not exist."
                )
            elif isinstance(err, http.client.RemoteDisconnected):
                print("Possible reason is that the domain does not exist.")
            else:
                print(err)
            sys.exit(1)

        return response

    def AccessTokenIsValid(self, token: str) -> bool:
        path = "/api/v4/personal_access_tokens/self"
        response = self.Request("GET", path, token=token)

        if not self._HttpStatusSuccess(response.status):
            return False

        try:
            return json.load(response)["active"] is True
        except json.decoder.JSONDecodeError:
            return False

    def TriggerPipeline(
        self,
        token: str,
        project_path: str,
        branch: str,
        variables: Dict[VariableKey, VariableValue],
    ) -> PipelineUrl:
        path = f"/api/v4/projects/{self.encode_project_path(project_path)}/pipeline"
        payload = {
            "ref": branch,
            "variables": [
                {
                    "key": key,
                    "value": value,
                }
                for key, value in variables.items()
            ],
        }

        response = self.Request(
            "POST",
            path,
            token=token,
            headers={"Content-Type": "application/json"},
            body=json.dumps(payload),
            retry=3,
        )
        result = response.read().decode("utf-8")

        if self._HttpStatusSuccess(response.status):
            return json.loads(result)["web_url"]

        print(result)
        print(f"❌ Failed to trigger pipeline on '{project_path}'.")
        if response.status == 404:
            print(
                "Possible reasons: the project does not exist, or you do not have permission to access it.\n"
                f"Please contact the administrator to invite you into the project '{project_path}'."
            )
        sys.exit(1)

    def GetLabelsOfMRs(
        self, token: str, project_path: str, source_branch: str
    ) -> List[str]:
        path = f"/api/v4/projects/{self.encode_project_path(project_path)}/merge_requests"
        params = {
            "state": "opened",
            "source_branch": source_branch,
        }
        response = self.Request("GET", path, token=token, params=params, retry=3)
        if not self._HttpStatusSuccess(response.status):
            return []

        result = json.loads(response.read().decode("utf-8"))
        labels: List[str] = []
        for mr_result in result:
            labels.extend(mr_result["labels"])
        return labels

    def GetUser(self, token: str, username: str) -> Optional[Dict]:
        if not username:
            return None

        path = "/api/v4/users"
        params = {
            "username": username,
        }
        response = self.Request("GET", path, token=token, params=params, retry=3)

        if self._HttpStatusSuccess(response.status):
            users = json.loads(response.read().decode("utf-8"))
            if users:
                return users[0]
        return None

    def _HttpStatusSuccess(self, status: int) -> bool:
        return 200 <= status < 300

    def CreateBranch(
        self,
        token: str,
        project_path: str,
        branch_name: str,
        ref: str = "main",
    ) -> bool:
        path = f"/api/v4/projects/{self.encode_project_path(project_path)}/repository/branches"

        payload = {
            "branch": urllib.parse.quote(branch_name),
            "ref": urllib.parse.quote(ref),
        }

        response = self.Request(
            "POST",
            path,
            token=token,
            headers={"Content-Type": "application/json"},
            body=json.dumps(payload),
            retry=3,
        )

        if self._HttpStatusSuccess(response.status):
            return True

        return False

    def CreateMR(
        self,
        token: str,
        project_path: str,
        source_branch: str,
        target_branch: str = "main",
        title: str = "",
        description: str = "",
        remove_source_branch: bool = True,
        labels: Optional[List[str]] = None,
        assignee_id: Optional[int] = None,
        skip_mono_central_pipeline: bool = True,
        squash: bool = False,
    ) -> str:
        """
        Returns:
            MR web URL if created successfully, empty string otherwise
        """
        path = f"/api/v4/projects/{self.encode_project_path(project_path)}/merge_requests"

        payload = {
            "source_branch": urllib.parse.quote(source_branch),
            "target_branch": urllib.parse.quote(target_branch),
            "title": title,
            "description": description,
            "remove_source_branch": remove_source_branch,
            "assignee_id": assignee_id,
            "labels": labels,
            "skip_mono_central_pipeline": skip_mono_central_pipeline,
            "squash": squash,
        }

        response = self.Request(
            "POST",
            path,
            token=token,
            headers={"Content-Type": "application/json"},
            body=json.dumps(payload),
            retry=3,
        )

        if self._HttpStatusSuccess(response.status):
            result = json.loads(response.read().decode("utf-8"))
            return result["web_url"]

        return ""

    def CloseMR(
        self,
        token: str,
        project_path: str,
        merge_request_iid: int,
    ) -> bool:
        path = f"/api/v4/projects/{self.encode_project_path(project_path)}/merge_requests/{merge_request_iid}"

        payload = {"state_event": "close"}

        response = self.Request(
            "PUT",
            path,
            token=token,
            headers={"Content-Type": "application/json"},
            body=json.dumps(payload),
            retry=3,
        )

        if self._HttpStatusSuccess(response.status):
            return True

        return False

    def GetMR(
        self, token: str, project_path: str, merge_request_iid: int
    ) -> Optional[Dict]:
        """
        Get merge request information.

        Returns:
            MR information dict if found, None otherwise
        """
        path = f"/api/v4/projects/{self.encode_project_path(project_path)}/merge_requests/{merge_request_iid}"

        response = self.Request("GET", path, token=token, retry=3)

        if self._HttpStatusSuccess(response.status):
            return json.loads(response.read().decode("utf-8"))

        return None

    def UpdateMR(
        self,
        token: str,
        project_path: str,
        merge_request_iid: int,
        title: Optional[str] = None,
        description: Optional[str] = None,
        labels: Optional[List[str]] = None,
        **kwargs,
    ) -> bool:
        """
        Update merge request information.

        Returns:
            True if updated successfully, False otherwise
        """
        path = f"/api/v4/projects/{self.encode_project_path(project_path)}/merge_requests/{merge_request_iid}"

        payload = {}
        if title is not None:
            payload["title"] = title
        if description is not None:
            payload["description"] = description
        if labels is not None:
            payload["labels"] = labels

        # Add any additional kwargs to payload
        payload.update(kwargs)

        if not payload:
            return True  # Nothing to update

        response = self.Request(
            "PUT",
            path,
            token=token,
            headers={"Content-Type": "application/json"},
            body=json.dumps(payload),
            retry=3,
        )

        if self._HttpStatusSuccess(response.status):
            return True

        return False

    @classmethod
    def encode_project_path(cls, project_path: str) -> str:
        return urllib.parse.quote_plus(project_path.strip("/"))
