# Branch protection (configure once via the GitHub UI or `gh api`)

This file documents the intended settings for `main` and `develop`.
Apply them manually once per repo (or use the `gh` snippet at the bottom).

## `main`

| Setting | Value | Reason |
|---|---|---|
| Require a pull request before merging | ✅ | No direct push to `main` |
| Require approvals | **1** | Enforces the DoD in the PR template |
| Require approval of the most recent reviewable push | ✅ | Avoid stale approvals after force-pushes |
| Dismiss stale pull request approvals when new commits are pushed | ✅ | Same as above |
| Require review from Code Owners | ✅ | `.github/CODEOWNERS` routes infra/CI changes to the owner |
| Allow specified actors to bypass | never | Discipline |
| Require status checks to pass before merging | ✅ | See below |
| Require branches to be up to date before merging | ✅ | Avoid races with develop |
| Require conversation resolution | ✅ | Block on unresolved review comments |
| Require signed commits | ❌ | Optional — easier to sign once we move off Windows |
| Require linear history | ❌ | We use merge commits to preserve feature branch topology |
| Do not allow bypassing the above settings | ✅ | Even admins cannot push without review |
| Restrict who can push to matching branches | nobody | Lock down |
| Allow force pushes | ❌ |  |
| Allow deletions | ❌ |  |

### Required status checks (must all pass)

- `CI / lint`
- `CI / test (Python 3.10, linux)`
- `CI / test (Python 3.11, linux)`
- `CI / secret-scan`
- `Deploy / lint`

## `develop`

Same rules as `main` **except**: only **1** required approval, and **no** Code
Owner requirement. Developers can self-merge to `develop` from their feature
branches once CI is green.

## `gh` snippet to apply the above

```bash
gh api \
  --method PUT \
  -H "Accept: application/vnd.github+json" \
  /repos/:owner/:repo/branches/main/protection \
  --input - <<'JSON'
{
  "required_status_checks": {
    "strict": true,
    "contexts": [
      "CI / lint",
      "CI / test (Python 3.10, linux)",
      "CI / test (Python 3.11, linux)",
      "CI / secret-scan",
      "Deploy / lint"
    ]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "dismissal_restricted_team": null,
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": true,
    "required_approving_review_count": 1
  },
  "restrictions": null,
  "required_linear_history": false,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "block_creations": false,
  "required_conversation_resolution": true
}
JSON
```

(`develop` is identical except `require_code_owner_reviews` is `false`.)