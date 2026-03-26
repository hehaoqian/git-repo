# GitLab Changes Analysis: mono-client vs git-repo

This document provides an exhaustive diff/analysis of all GitLab-related changes introduced in the **mono-client** repository compared to the upstream **git-repo** codebase. It is intended to guide developers who need to port these changes to another codebase.

---

## 1. New Files

### `gitlab.py` (entirely new)

A new top-level module providing a thin HTTP/HTTPS client wrapper around the GitLab REST API.

**Location:** `gitlab.py` (repo root)

**Type aliases:**
```python
VariableKey = str
VariableValue = str
PipelineUrl = str
```

**Class `GitlabAPI`:**

| Method | Signature | Purpose |
|--------|-----------|---------|
| `__init__` | `(domain: str, https: bool) -> None` | Stores domain and scheme; builds `self.url` |
| `Request` | `(method, path, retry=1, token=None, **kwargs) -> HTTPResponse` | Generic HTTP request helper with retry; injects `PRIVATE-TOKEN` header when `token` provided; handles SSL/connection errors |
| `AccessTokenIsValid` | `(token: str) -> bool` | Validates a personal access token |
| `TriggerPipeline` | `(token, project_path, branch, variables) -> PipelineUrl` | Triggers a CI/CD pipeline; returns the pipeline URL |
| `GetLabelsOfMRs` | `(token, project_path, source_branch) -> List[str]` | Returns labels of open MRs for a given source branch |
| `GetUser` | `(token, username) -> Optional[Dict]` | Looks up a user by username |
| `CreateBranch` | `(token, project_path, branch_name, ref="main") -> bool` | Creates a branch from `ref` |
| `CreateMR` | `(token, project_path, source_branch, target_branch, title, description, remove_source_branch, labels, assignee_id, skip_mono_central_pipeline, squash) -> str` | Creates a merge request; returns the MR web URL |
| `CloseMR` | `(token, project_path, merge_request_iid) -> bool` | Closes (but does not merge) an MR |
| `GetMR` | `(token, project_path, merge_request_iid) -> Optional[Dict]` | Fetches MR details |
| `UpdateMR` | `(token, project_path, merge_request_iid, title, description, labels, **kwargs) -> bool` | Updates MR metadata |
| `_HttpStatusSuccess` | `(status) -> bool` | Returns True when HTTP status indicates success |
| `encode_project_path` | `@classmethod (project_path) -> str` | URL-encodes a GitLab project path (e.g. `group/sub/repo` → `group%2Fsub%2Frepo`) |

**Key implementation details in `Request`:**
- Uses `http.client.HTTPSConnection` or `http.client.HTTPConnection` depending on `self.https`
- Supports query parameters via `params=` kwarg (URL-encoded via `urllib.parse.urlencode`)
- Retry loop: retries up to `retry` times on non-success HTTP status
- On exception: prints friendly error for SSL errors (`ssl.SSLError`) and remote disconnection (`http.client.RemoteDisconnected`), then calls `exit(1)`

---

## 2. `manifest_xml.py` Changes

### 2.1 New Imports

```python
# Added to manifest_xml.py
from typing import List, Optional, Dict, Any
```
The base file does not import from `typing`.

---

### 2.2 `_Default` Class — New Attributes

The `_Default` class (project defaults within the manifest XML `<default>` element) gains five new class-level attributes:

```python
class _Default(object):
    # ... existing attributes ...
    gitlab_url = None
    enable_central_ci_pipeline = None
    central_ci_pipeline_must_success = False
    mono_upload_create_mr_for_central_ci_project = False  # 默认为 False — requires GitLab Token
    mono_upload_create_mr_for_business_projects = True
```

| Attribute | Type | Default | XML Attribute | Purpose |
|-----------|------|---------|---------------|---------|
| `gitlab_url` | `str\|None` | `None` | `gitlab-url` | Base URL of the GitLab instance (e.g. `https://jihulab.com`) |
| `enable_central_ci_pipeline` | `bool\|None` | `None` (→ `False`) | `enable-central-ci-pipeline` | Whether to trigger a central CI pipeline on `repo upload` |
| `central_ci_pipeline_must_success` | `bool` | `False` | `central-ci-pipeline-must-success` | If True, injects `CENTRAL_CI_PIPELINE_MUST_SUCCESS=true` into pipeline variables |
| `mono_upload_create_mr_for_central_ci_project` | `bool` | `False` | `mono-upload-create-mr-for-central-ci-project` | Whether to auto-create an MR in the central CI project during `repo upload` |
| `mono_upload_create_mr_for_business_projects` | `bool` | `True` | `mono-upload-create-mr-for-business-projects` | Whether to auto-create MRs for each business project (with GitLab push options) during `repo upload` |

**Validation added to `_ParseDefault`:**
```python
if d.enable_central_ci_pipeline:
    if not d.gitlab_url:
        raise ManifestParseError(
            "gitlab-url must be specified when enable-central-ci-pipeline is true"
        )
    elif not d.gitlab_url.startswith("http"):
        raise ManifestParseError(
            "gitlab-url must start with http or https"
        )
```

**`_ParseDefault` additions (reading `<default>` XML element):**
```python
d.enable_central_ci_pipeline = XmlBool(node, "enable-central-ci-pipeline", False)
d.central_ci_pipeline_must_success = XmlBool(node, "central-ci-pipeline-must-success", False)
d.gitlab_url = node.getAttribute("gitlab-url") or None
d.mono_upload_create_mr_for_central_ci_project = XmlBool(
    node, "mono-upload-create-mr-for-central-ci-project", False
)
d.mono_upload_create_mr_for_business_projects = XmlBool(
    node, "mono-upload-create-mr-for-business-projects", True
)
```

**`ToXml` additions (serializing `<default>` XML element):**
```python
if d.enable_central_ci_pipeline:
    e.setAttribute("enable-central-ci-pipeline", d.enable_central_ci_pipeline)
if d.central_ci_pipeline_must_success:
    e.setAttribute("central-ci-pipeline-must-success", "true")
if d.gitlab_url:
    e.setAttribute("gitlab-url", d.gitlab_url)
```

---

### 2.3 New Class `_PushOptions`

A new class representing the `<push-options>` element in the manifest XML.

```python
class _PushOptions(object):
    """Push options configuration within the manifest."""

    def __init__(self):
        self.central_ci_project: List[str] = []
        self.business_projects: List[str] = []
```

| Attribute | Type | Purpose |
|-----------|------|---------|
| `central_ci_project` | `List[str]` | Push options strings for the central CI project |
| `business_projects` | `List[str]` | Push options strings for business (child) projects |

**Method `fetch_central_ci_project_variables() -> Dict[str, str]`:**

Parses `ci.variable='VAR_NAME=value'` entries from `self.central_ci_project`.

```
Input option format:  "ci.variable='CI_VAR=my_val'"
Output dict:          {"CI_VAR": "my_val"}
```
- Strips single/double quote wrappers
- Splits on first `=` to separate variable name from value

**Method `fetch_central_ci_project_mr_options() -> Dict[str, Any]`:**

Parses `merge_request.*` options from `self.central_ci_project` into a GitLab API-friendly dict.

```
Input options (list):
  - "merge_request.assign=root"
  - "merge_request.title=my-title"
  - "merge_request.description=my-description"
  - "merge_request.label=l1"
  - "merge_request.label=l2"
  - "merge_request.squash"

Output dict:
  {
    "assignee_id": "root",
    "title": "my-title",
    "description": "my-description",
    "labels": ["l1", "l2"],
    "squash": True,
  }
```
- Allowed keys: `assign`, `title`, `description`, `label`, `squash`
- `merge_request.squash` (no `=`) is rewritten to `merge_request.squash=True`

**Implements `__eq__` and `__ne__`.**

---

### 2.4 New Class `_MrTitleSuffix`

A new class representing the `<mr-title-suffix>` element in the manifest XML.

```python
class _MrTitleSuffix(object):
    """MR title suffix configuration within the manifest."""

    def __init__(self):
        self.central_ci_project: str = ""
        self.business_projects: str = ""
```

| Attribute | Type | Purpose |
|-----------|------|---------|
| `central_ci_project` | `str` | Suffix appended to auto-generated MR titles for the central CI project |
| `business_projects` | `str` | Suffix appended to MR titles for business projects |

**Implements `__eq__` and `__ne__`.**

---

### 2.5 `XmlManifest` — New Instance Variables

In `XmlManifest.__init__` (the `_InitializeSelf`-equivalent section that resets the parsed state), two new instance variables are added:

```python
self._push_options = None
self._mr_title_suffix = None
```

---

### 2.6 `XmlManifest` — New Properties

```python
@property
def push_options(self) -> Optional[_PushOptions]:
    """Return push options for this manifest."""
    self._Load()
    return self._push_options

@property
def mr_title_suffix(self) -> Optional[_MrTitleSuffix]:
    """Return MR title suffix for this manifest."""
    self._Load()
    return self._mr_title_suffix
```

---

### 2.7 `XmlManifest` — XML Parsing (new element handlers)

In `_ParseManifestXml` (the main node parsing loop), two new `elif` branches are added after the `"default"` handler:

```python
elif node.nodeName == "push-options":
    if self._push_options is not None:
        raise ManifestParseError(
            "duplicate push-options in %s" % (self.manifestFile)
        )
    self._push_options = self._ParsePushOptions(node)
elif node.nodeName == "mr-title-suffix":
    if self._mr_title_suffix is not None:
        raise ManifestParseError(
            "duplicate mr-title-suffix in %s" % (self.manifestFile)
        )
    self._mr_title_suffix = self._ParseMrTitleSuffix(node)
```

After the loop, defaults are initialized:
```python
if self._push_options is None:
    self._push_options = _PushOptions()

if self._mr_title_suffix is None:
    self._mr_title_suffix = _MrTitleSuffix()
```

---

### 2.8 New Private Parse Methods

**`_ParsePushOptionsChild(self, child_node) -> List[str]`:**
```python
def _ParsePushOptionsChild(self, child_node) -> List[str]:
    """Parse options from a push-options child node (central-ci-project or business-projects)"""
    options = []
    for option_node in child_node.childNodes:
        if (option_node.nodeType == option_node.ELEMENT_NODE and
                option_node.nodeName == "option"):
            option_text = ""
            for text_node in option_node.childNodes:
                if text_node.nodeType == text_node.TEXT_NODE:
                    option_text += text_node.data
            if option_text.strip():
                options.append(option_text.strip())
    return options
```

**`_ParsePushOptions(self, node) -> _PushOptions`:**
```python
def _ParsePushOptions(self, node) -> _PushOptions:
    """Reads a <push-options> element from the manifest file (nested XML format only)"""
    push_options = _PushOptions()
    for child in node.childNodes:
        if child.nodeType != child.ELEMENT_NODE:
            continue
        if child.nodeName == "central-ci-project":
            push_options.central_ci_project = self._ParsePushOptionsChild(child)
        elif child.nodeName == "business-projects":
            push_options.business_projects = self._ParsePushOptionsChild(child)
    return push_options
```

**`_ParseMrTitleSuffix(self, node) -> _MrTitleSuffix`:**
```python
def _ParseMrTitleSuffix(self, node) -> _MrTitleSuffix:
    """Reads a <mr-title-suffix> element from the manifest file"""
    mr_title_suffix = _MrTitleSuffix()
    for child in node.childNodes:
        if child.nodeType != child.ELEMENT_NODE:
            continue
        if child.nodeName == "central-ci-project":
            # Extract text content
            text_content = ""
            for text_node in child.childNodes:
                if text_node.nodeType == text_node.TEXT_NODE:
                    text_content += text_node.data
            mr_title_suffix.central_ci_project = text_content.strip()
        elif child.nodeName == "business-projects":
            text_content = ""
            for text_node in child.childNodes:
                if text_node.nodeType == text_node.TEXT_NODE:
                    text_content += text_node.data
            mr_title_suffix.business_projects = text_content.strip()
    return mr_title_suffix
```

---

### 2.9 New Method `RawManifestFileName() -> str`

Added to the `XmlManifest` class (or its subclass section):

```python
def RawManifestFileName(self) -> str:
    """
    Retrieve the manifest file name as originally specified in `mono init`.
    
    self.manifestFile stores .repo/manifest.xml which contains:
      <manifest><include name="test.xml" /></manifest>
    Returns "test.xml".
    """
    name = ""
    try:
        root = xml.dom.minidom.parse(self.manifestFile)
        for manifest in root.childNodes:
            if manifest.nodeName == "manifest":
                break
        for node in manifest.childNodes:
            if node.nodeName == "include":
                name = self._reqatt(node, "name")
                break
    except Exception as error:
        print(f"⚠️ Failed to get manifest file name. {error}")
    return name
```

Used by `subcmds/upload.py`'s `GitlabCentralCiHelper.TriggerPipeline` to inject `MANIFEST_FILE` into pipeline variables.

---

### 2.10 `ToXml` — New `<push-options>` and `<mr-title-suffix>` Serialization

```python
if (self._push_options and
        (self._push_options.central_ci_project or self._push_options.business_projects)):
    e = doc.createElement("push-options")
    if self._push_options.central_ci_project:
        central_ci_element = doc.createElement("central-ci-project")
        for option in self._push_options.central_ci_project:
            if option:
                option_element = doc.createElement("option")
                option_element.appendChild(doc.createTextNode(str(option)))
                central_ci_element.appendChild(option_element)
        e.appendChild(central_ci_element)
    if self._push_options.business_projects:
        business_element = doc.createElement("business-projects")
        for option in self._push_options.business_projects:
            if option:
                option_element = doc.createElement("option")
                option_element.appendChild(doc.createTextNode(str(option)))
                business_element.appendChild(option_element)
        e.appendChild(business_element)
    root.appendChild(e)

if (self._mr_title_suffix.central_ci_project or self._mr_title_suffix.business_projects):
    e = doc.createElement("mr-title-suffix")
    if self._mr_title_suffix.central_ci_project:
        central_ci_element = doc.createElement("central-ci-project")
        central_ci_element.appendChild(doc.createTextNode(self._mr_title_suffix.central_ci_project))
        e.appendChild(central_ci_element)
    if self._mr_title_suffix.business_projects:
        business_element = doc.createElement("business-projects")
        business_element.appendChild(doc.createTextNode(self._mr_title_suffix.business_projects))
        e.appendChild(business_element)
    root.appendChild(e)
```

---

### 2.11 Manifest XML Schema Summary

New XML elements and attributes introduced:

**`<default>` element — new attributes:**
```xml
<default
  gitlab-url="https://jihulab.com"
  enable-central-ci-pipeline="true"
  central-ci-pipeline-must-success="false"
  mono-upload-create-mr-for-central-ci-project="false"
  mono-upload-create-mr-for-business-projects="true"
/>
```

**`<push-options>` element (top-level, singleton):**
```xml
<push-options>
  <central-ci-project>
    <option>ci.variable='MY_VAR=value'</option>
    <option>merge_request.label=my-label</option>
    <option>merge_request.title=my title</option>
    <option>merge_request.description=desc</option>
    <option>merge_request.assign=username</option>
    <option>merge_request.squash</option>
  </central-ci-project>
  <business-projects>
    <option>some-push-option</option>
  </business-projects>
</push-options>
```

**`<mr-title-suffix>` element (top-level, singleton):**
```xml
<mr-title-suffix>
  <central-ci-project>suffix text for central ci MR titles</central-ci-project>
  <business-projects>suffix text for business project MR titles</business-projects>
</mr-title-suffix>
```

---

## 3. `subcmds/upload.py` Changes

### 3.1 New Imports

```python
# Added:
import json
from typing import List, Set, Optional
from urllib.parse import urlparse
from project import ReviewableBranch, ManifestProject
from manifest_xml import XmlManifest
from gitlab import GitlabAPI
```

**Removed (present in base, absent in mono):**
```python
# Removed from mono version:
from error import GitError
from error import SilentRepoExitError
from repo_logging import RepoLogger
from subcmds.sync import LocalSyncState
import git_superproject
```

### 3.2 `_DEFAULT_UNUSUAL_COMMIT_THRESHOLD` Changed

| Version | Value |
|---------|-------|
| Base (git-repo) | `5` |
| Mono-client | `30` |

The default threshold for "large number of commits" warning is raised from 5 to 30.

### 3.3 New Type Alias

```python
PipelineUrl = str
```

### 3.4 New Class `GitlabCentralCiHelper`

This is the main orchestration class for GitLab CI integration during `repo upload`.

```python
class GitlabCentralCiHelper:
    CI_PROJECT_NAME = "monorepo-ci-project"

    def __init__(self, manifest: XmlManifest):
        self.manifest = manifest
        self.manifest_project: ManifestProject = manifest.manifestProject
        self.access_token_config_key = "manifest.gitlab-personal-access-token"

        gitlab_url = urlparse(self.manifest.default.gitlab_url)
        self.gitlab_api = GitlabAPI(
            gitlab_url.netloc, gitlab_url.scheme == "https"
        )

        root_group_of_projects: str = self.manifest.projects[0].RootGroup
        self.ci_project_path = f"{root_group_of_projects}/{self.CI_PROJECT_NAME}"
```

**Constants:**
- `CI_PROJECT_NAME = "monorepo-ci-project"` — the GitLab project name for the central CI project

**Config key:**
- `"manifest.gitlab-personal-access-token"` — stored in `.git/config` of the manifest project

**Methods:**

#### `TriggerPipelines(self, branches: List[ReviewableBranch]) -> None`
Iterates unique topics from the uploaded branches and triggers a pipeline for each.

#### `_FetchTopics(self, branches: List[ReviewableBranch]) -> Set[str]`
For each branch, calls `_GetTopicsFromGitLabMRs` to find `topic::*` labels on existing MRs. Returns the union of all found topics.

#### `TriggerPipeline(self, topic_name: str) -> None`
Triggers a pipeline on the central CI project's `main` branch with variables:
```python
variables = {
    "MONOREPO_TOPIC_LABEL": topic_name,
    "MANIFEST_FILE": self.manifest.RawManifestFileName(),
    # Plus any ci.variable options from manifest push_options
    # Plus "CENTRAL_CI_PIPELINE_MUST_SUCCESS": "true"  (if configured)
}
```
Calls `self.gitlab_api.TriggerPipeline(token, project_path, "main", variables)`.

#### `CreateMRForCentralCiProject(self, branches: List[ReviewableBranch]) -> None`
For each topic found:
1. Creates a temp branch: `temp-branch-for-topic-{topic_name_without_prefix}` on the central CI project
2. Resolves `assignee_id` from username via `GetUserIdByUsername`
3. Constructs MR title (uses `merge_request.title` option if provided, otherwise `"Auto MR for topic \`{topic_name}\`"`)
4. Appends `mr_title_suffix.central_ci_project` if configured
5. Calls `self.gitlab_api.CreateMR(...)` with:
   - `labels=[topic_name] + extra_labels`
   - `squash`, `description`, `assignee_id` from manifest push options
6. Immediately closes the MR via `CloseMR` (since it has no commits, to avoid blocking "Merge all")

#### `_GetAccessToken(self) -> str`
1. Reads PAT from git config key `manifest.gitlab-personal-access-token`
2. If absent, prompts user interactively: `"Personal Access Token is required..."`
3. Validates the token via `gitlab_api.AccessTokenIsValid`; re-prompts if invalid
4. Saves token back to git config (or clears it if user skips)

#### `_GetTopicsFromGitLabMRs(self, project_path: str, source_branch: str) -> List[str]`
Calls `gitlab_api.GetLabelsOfMRs` and filters for labels starting with `"topic::"`.

#### `GetUserIdByUsername(self, username: str) -> Optional[int]`
- If `username` is already numeric, returns `int(username)` directly
- Otherwise calls `gitlab_api.GetUser` and returns `user_info["id"]`

---

### 3.5 `Upload` Class — Modified `helpDescription`

The description string is updated: references to "Gerrit Code Review" are replaced with "GitLab".

### 3.6 `Upload._Options` — No New Options for GitLab

The `-o`/`--push-option` option was **already present** in the base codebase. No new CLI flags were added specifically for GitLab.

### 3.7 `Upload.Execute` — GitLab Integration Hook

At the end of `Execute` (after uploads complete, before returning), the following block is added:

```python
if not opt.dryrun:
    gitlab_central_ci_helper = GitlabCentralCiHelper(self.manifest)

    # Why create MR then also trigger pipeline manually?
    # Because MR-triggered pipelines cannot write our required CI Variables.
    if self.manifest.default.mono_upload_create_mr_for_central_ci_project:
        print("\nCreating central ci project MR...")
        gitlab_central_ci_helper.CreateMRForCentralCiProject(todo)
    if self.manifest.default.enable_central_ci_pipeline:
        print("\nTriggering central ci pipeline...")
        gitlab_central_ci_helper.TriggerPipelines(todo)
```

**Conditions:**
- `mono_upload_create_mr_for_central_ci_project` → triggers `CreateMRForCentralCiProject`
- `enable_central_ci_pipeline` → triggers `TriggerPipelines`
- Both are only run when `not opt.dryrun`

---

## 4. `project.py` Changes

### 4.1 New/Changed Imports

**Added:**
```python
from typing import NamedTuple, List, Optional   # (NamedTuple was already there; List, Optional added)
from git_config import (
    GitConfig, IsId, GetSchemeFromUrl, GetUrlCookieFile, ID_RE, Branch
)  # Added: ID_RE, Branch (not in base)
from git_refs import (
    GitRefs, HEAD, R_HEADS, R_TAGS, R_PUB, R_M, R_WORKTREE_M, R_REMOTES_ORIGIN
)  # Added: R_REMOTES_ORIGIN (not in base git_refs.py)
```

**Removed from base (absent in mono):**
```python
import datetime
import string
from error import GitAuthError, RepoError
from git_refs import R_CHANGES, R_WORKTREE
from repo_logging import RepoLogger
```

**New constant needed in `git_refs.py`:**
```python
R_REMOTES_ORIGIN = "refs/remotes/origin/"
```
This constant is imported from `git_refs` but does not exist in the base `git_refs.py`. It must be added there.

### 4.2 New Property `GitlabPath`

```python
@property
def GitlabPath(self) -> str:
    """
    Obtain the project path of the project's remote URL.

    Examples:
        http://jihulab.com/rootgroup/subgroup/repo1.git      -> rootgroup/subgroup/repo1
        ssh://jihulab.com:29418/rootgroup/subgroup/repo1.git -> rootgroup/subgroup/repo1
        git@jihulab.com:rootgroup/subgroup/repo1.git         -> rootgroup/subgroup/repo1
    """
    repo_url = self.remote.url
    url_has_protocol = "://" in repo_url
    if url_has_protocol:
        url_without_protocol = repo_url.split("://")[1]
        repo_path = url_without_protocol.split("/", 1)[1]
    else:
        repo_path = repo_url.split(":", 1)[1]
    
    # Remove the .git suffix
    if repo_path.endswith(".git"):
        repo_path = repo_path[:-4]
    return repo_path
```

### 4.3 New Property `RootGroup`

```python
@property
def RootGroup(self) -> str:
    """
    Obtain the root group of the project's remote URL.

    Examples:
        http://jihulab.com/rootgroup/subgroup/repo1.git      -> rootgroup
        ssh://jihulab.com:29418/rootgroup/subgroup/repo1.git -> rootgroup
        git@jihulab.com:rootgroup/subgroup/repo1.git         -> rootgroup
    """
    return self.GitlabPath.split("/")[0]
```

**Used by:** `GitlabCentralCiHelper.__init__` to find the root GitLab group to construct `ci_project_path`.

### 4.4 `UploadForReview` — Complete Rewrite for GitLab

The `Project.UploadForReview` method is substantially different. Here are the key changes:

**Signature changes:**
| Parameter | Base (git-repo) | Mono-client |
|-----------|-----------------|-------------|
| `topic` | ✅ Present | ❌ Removed |
| `patchset_description` | ✅ Present | ❌ Removed |
| `auto_topic` | ❌ Absent | ✅ Added |

**URL source:** Base uses `branch.remote.ReviewUrl(self.UserEmail, validate_certs)` (Gerrit URL); mono uses `branch.remote.url` (the plain Git remote URL — GitLab does not need a separate review URL).

**Push target:** Base pushes to Gerrit's `refs/for/{dest_branch}` refspec with `%topic=,...` options; mono pushes directly to `refs/heads/{branch.name}`.

**GitLab MR creation via push options** (conditional on `mono_upload_create_mr_for_business_projects`):
```python
if not dryrun and self.manifest.default.mono_upload_create_mr_for_business_projects:
    push_options = self._AdjustOptionFormatOfTopic(push_options)

    # Build MR title with optional suffix from manifest
    mr_title = ""
    if self.manifest.mr_title_suffix and self.manifest.mr_title_suffix.business_projects:
        try:
            base = branch.LocalMerge or self.GetRevisionId()
            rb = ReviewableBranch(self, branch, base)
            if rb.commits:
                first_commit = rb.commits[0]
                if ' ' in first_commit:
                    mr_title = first_commit.split(' ', 1)[1]  # strip hash prefix
                else:
                    mr_title = first_commit
                mr_title += f" {self.manifest.mr_title_suffix.business_projects}"
        except Exception:
            pass

    push_options += self._GitlabMRCreationPushOptions(
        target_branch=dest_branch,
        draft=wip,
        title=mr_title,
    )

# Merge additional push options from manifest business_projects
if self.manifest.push_options and self.manifest.push_options.business_projects:
    push_options = push_options or []
    push_options.extend(self.manifest.push_options.business_projects)
```

**Git push command:** Mono uses `-o {push_option}` flags and pushes `refs/heads/{branch.name}` directly (no `refs/for/` refspec, no Gerrit receive-pack).

### 4.5 New Static/Class Method `_AdjustOptionFormatOfTopic`

```python
@classmethod
def _AdjustOptionFormatOfTopic(cls, push_options: Optional[List[str]]) -> List[str]:
    """Adjust the format of the topic option for GitLab.

    Input:  ["topic=topic_name",   "others_options"]
            or ["topic::topic_name", "others_options"]
    Output: ["merge_request.label=topic::topic_name", "others_options"]
    """
    push_options = push_options or []
    new_push_options = []
    for option in push_options:
        if option.startswith("topic="):
            topic_name = option.split("topic=")[1]
            new_push_options.append(f"merge_request.label=topic::{topic_name}")
        elif option.startswith("topic::"):
            new_push_options.append(f"merge_request.label={option}")
        else:
            new_push_options.append(option)
    return new_push_options
```

### 4.6 New Class Method `_GitlabMRCreationPushOptions`

```python
@classmethod
def _GitlabMRCreationPushOptions(
    cls,
    target_branch: str,
    draft: bool = False,
    title: str = "",
) -> List[str]:
    options = [
        "merge_request.create",
        f"merge_request.target={target_branch}",
        "merge_request.skip_mono_central_pipeline",
    ]
    if draft:
        options.append("merge_request.draft")
    if title:
        escaped_title = title.replace('"', '\\"').replace('\n', ' ').strip()
        if escaped_title:
            options.append(f'merge_request.title={escaped_title}')
    return options
```

**Push option key meanings:**
- `merge_request.create` — instructs GitLab to create an MR on push
- `merge_request.target={branch}` — sets the MR target branch
- `merge_request.skip_mono_central_pipeline` — custom GitLab CI option to skip the central pipeline (since `TriggerPipeline` is called separately)
- `merge_request.draft` — marks MR as draft/WIP
- `merge_request.title={title}` — sets the MR title

### 4.7 `StartBranch` — Remote Branch Detection

The `StartBranch` method is extended to check for remote branches:

```python
all_refs = self.bare_ref.all
branch_exists_locally: bool = R_HEADS + name in all_refs
branch_exists_on_remote: bool = R_REMOTES_ORIGIN + name in all_refs
if branch_exists_locally or branch_exists_on_remote:
    return GitCommand(self, ["checkout", "-q", name, "--"]).Wait() == 0
```

Previously only `branch_exists_locally` was checked. Now if a remote tracking branch exists (e.g., the branch was merged and deleted locally but exists on `origin`), it checkouts rather than creating a new one.

### 4.8 `Sync_LocalHalf` — Git Pull Before Sync

In `Sync_LocalHalf` (the local sync half), a new block is added to handle tracking configuration and pull:

```python
if not branch.LocalMerge:
    # If no tracking configuration but remote branch with same name exists, update tracking.
    branch_exists_on_remote: bool = R_REMOTES_ORIGIN + branch.name in all_refs
    if branch_exists_on_remote:
        branch.merge = R_HEADS + branch.name
        branch.Save()

# Pull if tracked remote branch still exists (may be deleted after MR merge)
tracked_branch_exists_on_remote = branch.LocalMerge in all_refs
if branch.LocalMerge and tracked_branch_exists_on_remote:
    self.work_git.pull(self.remote.name, "--quiet")
```

This ensures local branches are updated via `git pull` when a tracked remote branch exists.

---

## 5. `subcmds/sync.py` Changes

> **⚠️ Important clarification:** The file provided as "mono-client subcmds/sync.py" is actually the **mono launcher script** (equivalent to git-repo's `repo` shell launcher). It is **not** `subcmds/sync.py`. The actual `subcmds/sync.py` was not provided and likely has no significant GitLab-specific changes.

### Changes in the mono launcher script (mono's `repo` → `mono`):

#### 5.1 Repository URL and Branding

```python
# Base (git-repo's 'repo' script):
REPO_URL = "https://gerrit.googlesource.com/git-repo"
BUG_URL = "https://issues.gerritcodereview.com/issues/new?component=1370071"

# Mono-client:
REPO_URL = "https://jihulab.com/gitlab-cn/mono-client"
BUG_URL = "https://jihulab.com/gitlab-cn/mono-client/-/issues"
```

#### 5.2 GPG Verification Disabled by Default

```python
# Base (default=True means verify-by-default):
group.add_option(
    "--no-repo-verify",
    dest="repo_verify",
    default=True,       # ← verification enabled by default
    action="store_false",
    help="do not verify repo source code",
)

# Mono (default=False means no-verify by default):
group.add_option(
    "--no-repo-verify",
    dest="repo_verify",
    default=False,      # ← verification DISABLED by default
    action="store_false",
    help="do not verify mono source code",
)
```

**Reason:** GitLab does not support GPG-verified tags (as referenced in comments:  
`# Default 'False' because GPG verified tags are not supported on GitLab.`  
`# See https://docs.gitlab.com/ee/user/project/repository/gpg_signed_commits/`)

#### 5.3 Default Revision

```python
# Base:
REPO_REV = os.environ.get("REPO_REV")
if not REPO_REV:
    REPO_REV = "stable"

# Mono:
REPO_REV = os.environ.get("REPO_REV")
if not REPO_REV:
    REPO_REV = "main-jh"
```

#### 5.4 Text branding

All occurrences of "repo" in user-facing strings replaced with "mono" (e.g. help text, error messages, option descriptions).

---

## 6. `fetch.py` Changes

**No changes.** The `fetch.py` file in mono-client is identical to the base git-repo `fetch.py`. No GitLab-related modifications were made to this file.

---

## 7. `subcmds/init.py` Changes

**Minor branding only.** Based on the launcher script analysis, the init command has minor branding changes (replacing "repo" with "mono" in user-facing text). No GitLab-specific functional changes to `subcmds/init.py` were observed. The `--repo-url` and `--repo-rev` options in the launcher default to the mono-client GitLab repository instead of the git-repo Gerrit repository.

---

## 8. Summary: What Needs to be Ported

The following is a prioritized, exhaustive list of changes needed to port the mono-client GitLab integration.

### 8.1 New File to Create

| File | Action |
|------|--------|
| `gitlab.py` | Create new file with `GitlabAPI` class (HTTP client for GitLab REST API) |

### 8.2 `git_refs.py` — Add Missing Constant

```python
R_REMOTES_ORIGIN = "refs/remotes/origin/"
```

### 8.3 `manifest_xml.py` — Full List of Changes

1. **Add import:** `from typing import List, Optional, Dict, Any`
2. **Add 5 attributes to `_Default` class:** `gitlab_url`, `enable_central_ci_pipeline`, `central_ci_pipeline_must_success`, `mono_upload_create_mr_for_central_ci_project`, `mono_upload_create_mr_for_business_projects`
3. **Add new class `_PushOptions`** with `central_ci_project`, `business_projects` attributes and two parse methods
4. **Add new class `_MrTitleSuffix`** with `central_ci_project`, `business_projects` attributes
5. **Add `self._push_options = None`** and **`self._mr_title_suffix = None`** to `__init__`/reset section
6. **Add `push_options` property** and **`mr_title_suffix` property** to `XmlManifest`
7. **Add `_ParsePushOptions`, `_ParsePushOptionsChild`, `_ParseMrTitleSuffix`** methods
8. **Update `_ParseManifestXml`** to handle `push-options` and `mr-title-suffix` nodes (with duplicate detection and default initialization)
9. **Update `_ParseDefault`** to parse 5 new XML attributes with validation
10. **Update `ToXml`** to serialize `<default>` GitLab attributes and new `<push-options>` / `<mr-title-suffix>` elements
11. **Add `RawManifestFileName()` method** to `XmlManifest`

### 8.4 `project.py` — Full List of Changes

1. **Add `R_REMOTES_ORIGIN` to git_refs import**
2. **Add `ID_RE`, `Branch` to git_config import**
3. **Add `GitlabPath` property** to `Project`
4. **Add `RootGroup` property** to `Project`
5. **Rewrite `UploadForReview`** to push directly to `branch.remote.url` (not Gerrit review URL), remove `topic`/`patchset_description` params, add `auto_topic` param, add GitLab push options generation
6. **Add `_AdjustOptionFormatOfTopic` classmethod** (converts `topic=name` → `merge_request.label=topic::name`)
7. **Add `_GitlabMRCreationPushOptions` classmethod** (generates `merge_request.create`, `merge_request.target`, etc.)
8. **Update `StartBranch`** to also check `R_REMOTES_ORIGIN + name` when deciding whether to create a new branch
9. **Update `Sync_LocalHalf`** to auto-configure tracking and run `git pull` when tracked remote branch exists

### 8.5 `subcmds/upload.py` — Full List of Changes

1. **Add imports:** `json`, `Set`, `Optional` from typing; `urlparse`; `ManifestProject` from project; `XmlManifest` from manifest_xml; `GitlabAPI` from gitlab
2. **Change `_DEFAULT_UNUSUAL_COMMIT_THRESHOLD`** from `5` to `30`
3. **Add `PipelineUrl = str` type alias**
4. **Add `GitlabCentralCiHelper` class** with all 7 methods documented in section 3.4
5. **Update `Upload.Execute`** to call `GitlabCentralCiHelper` after successful uploads (section 3.7)
6. **Update `UploadForReview` call** in `_UploadAndReport` to remove `topic=opt.topic` and `patchset_description` arguments (they no longer exist in mono's `Project.UploadForReview`)

### 8.6 Launcher Script (`repo`) — Optional/Branding Changes

These are branding changes and only matter if you are distributing the mono-client launcher:

1. Change `REPO_URL` to point to your GitLab instance
2. Change `REPO_REV` to your default branch
3. Change `BUG_URL` to your issue tracker
4. Change `--no-repo-verify` default from `True` to `False` (disable GPG verification)
5. Replace "repo" with "mono" in user-facing strings

---

### 8.7 Dependency Graph

```
gitlab.py
    ↑ imported by
subcmds/upload.py (GitlabCentralCiHelper)

manifest_xml.py (_Default attrs, _PushOptions, _MrTitleSuffix)
    ↑ used by
project.py (UploadForReview reads manifest.default.* and manifest.push_options/mr_title_suffix)
subcmds/upload.py (GitlabCentralCiHelper reads manifest.default.gitlab_url etc.)

git_refs.py (R_REMOTES_ORIGIN)
    ↑ imported by
project.py (StartBranch, Sync_LocalHalf)
```

### 8.8 Manifest XML Attribute Quick Reference

| XML Attribute | Python Field | Type | Default | Where Checked |
|---------------|-------------|------|---------|---------------|
| `gitlab-url` | `default.gitlab_url` | str | None | `GitlabCentralCiHelper.__init__` |
| `enable-central-ci-pipeline` | `default.enable_central_ci_pipeline` | bool | False | `Upload.Execute` |
| `central-ci-pipeline-must-success` | `default.central_ci_pipeline_must_success` | bool | False | `TriggerPipeline` |
| `mono-upload-create-mr-for-central-ci-project` | `default.mono_upload_create_mr_for_central_ci_project` | bool | False | `Upload.Execute` |
| `mono-upload-create-mr-for-business-projects` | `default.mono_upload_create_mr_for_business_projects` | bool | True | `Project.UploadForReview` |

### 8.9 Config Key

| Git Config Key | Usage |
|----------------|-------|
| `manifest.gitlab-personal-access-token` | Stored in `.git/config` of manifest project; used by `GitlabCentralCiHelper._GetAccessToken` |
