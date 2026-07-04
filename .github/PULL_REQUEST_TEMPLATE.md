name: Pull request to develop | main
description: |
  Use this template for all PRs. PRs without the **Definition of Done**
  checklist ticked cannot be merged into `main` thanks to the branch
  protection rules in `.github/branch-protection.md`.
about: |
  Reference: GitPlan-DuAn3-AzureDataIntegration.md, branch section "Feature
  branch checklist".
title: "[<type>] <scope>: <short description>"
labels: []
body:
  - type: dropdown
    id: type
    attributes:
      label: Commit type (matches our commit-message convention)
      options:
        - feat
        - fix
        - docs
        - test
        - chore
        - ci
        - refactor
    validations:
      required: true

  - type: input
    id: branch
    attributes:
      label: Source branch
      description: e.g. `feature/data-source-adapter`
      placeholder: "feature/"
    validations:
      required: true

  - type: input
    id: userstory
    attributes:
      label: Linked user story (US-01..US-13) or "infra"
      placeholder: "US-04"
    validations:
      required: true

  - type: textarea
    id: summary
    attributes:
      label: What does this PR change?
      description: A few bullet points is enough.
    validations:
      required: true

  - type: textarea
    id: dod
    attributes:
      label: Definition of Done — checklist
      description: Tick every box that applies. Required for merge to main.
      value: |
        - [ ] PR description links the user story
        - [ ] New code has at least one unit test
        - [ ] `ruff check .` is green locally
        - [ ] `pytest -ra -q` is green locally
        - [ ] No new secrets in code or `.env` (BR-01)
        - [ ] README / docs updated if the change is user-visible
        - [ ] Verified locally with real services when feasible
    validations:
      required: true

  - type: textarea
    id: testing
    attributes:
      label: How was this tested?
      description: Mention specific commands and the result.
