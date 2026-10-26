# Unit 17: GitHub client

## Goal

One module through which every GitHub call passes, under exactly one
identity: the installation's GitHub App, with a token minted per task
(PRD §Capability 5; architecture.md · Invariants 6 and 7). Issues,
comments, labels, collaborator permission, Contents, branches, pull
requests and reviewer requests; every label and comment write recorded as
an event naming the identity used; `CODEOWNERS` parsed locally.

## Design

No UI. Visible result: contract tests against recorded responses
(`httpx.MockTransport`) prove the paths, headers, bodies, the JWT, the
per-task token cache and the recorded events; no network.

## Implementation

### orchestrator/github/auth.py

- `app_jwt(app_id, private_key_pem, now)`: RS256, `iat` 60 s in the past,
  `exp` 9 minutes ahead, `iss` = app id.
- `InstallationTokenMinter(app_id, private_key_pem, installation_id,
  http)`: `token_for(task_id)` posts to
  `/app/installations/{id}/access_tokens` with the three permissions and
  caches the token per task until 5 minutes before expiry.
- `TokenProvider` protocol: the client accepts nothing else; the
  orchestrator has one implementation.

### orchestrator/github/client.py

- `GitHubClient(owner, repo, tokens, http, recorder)` with `get_issue`,
  `list_issue_comments`, `create_comment`, `add_labels`, `remove_label`,
  `list_labels`, `collaborator_permission`, `get_file` (Contents, decoded),
  `get_branch_sha`, `create_branch`, `create_pull`, `get_pull`,
  `list_pulls_for_branch`, `request_reviewers`, `get_user`.
- Headers: `Authorization: Bearer <token>`, `Accept:
  application/vnd.github+json`, `X-GitHub-Api-Version: 2022-11-28`,
  `User-Agent: tank-factory/<version>`.
- Retries: 3 on 5xx and on 403/429 rate limits with backoff 5, 20, 60 s
  (injectable sleep); other 4xx raise `GitHubApiError(status, message)`.
- `EventRecorder` callback: every label and comment write produces one
  `label_write` or `comment_write` event with the object, the label or
  comment id and `identity = "app"`.

### orchestrator/github/codeowners.py

- `parse_codeowners(text)` and `owners_for(path, rules)`: last matching
  rule wins; patterns without a slash match any component; a leading `/`
  anchors; a trailing `/` is a directory; `*` and `**` as in git.

## Dependencies

- `httpx` (async HTTP), `pyjwt` and `cryptography` (RS256 App JWT)

## Verify when done

- [ ] The JWT decodes with the test public key and carries `iss`, `iat`
      and `exp` as specified
- [ ] `token_for` mints once per task, sends the Bearer JWT and the
      permissions, and re-mints after expiry
- [ ] Each client method hits the documented path with the documented
      method and body against the recorded fixtures
- [ ] Adding a label, removing a label and creating a comment each record
      one event with `identity = "app"`
- [ ] A 502 is retried with the backoff sequence and then succeeds; a 404
      raises `GitHubApiError` with the status
- [ ] `owners_for` resolves the CODEOWNERS examples (global, anchored
      directory, extension glob, last rule wins)
- [ ] The four quality commands pass
