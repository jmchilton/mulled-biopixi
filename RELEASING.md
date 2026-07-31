# Releasing mulled-biopixi

Releases are built from a published GitHub Release and uploaded through PyPI Trusted Publishing.
No long-lived PyPI token is stored in GitHub.

## One-time PyPI setup

Register a pending GitHub publisher at <https://pypi.org/manage/account/publishing/> with these
values:

- PyPI project name: `mulled-biopixi`
- GitHub owner: `jmchilton`
- GitHub repository: `mulled-biopixi`
- Workflow filename: `release.yml`
- Environment name: `pypi`

A pending publisher does not reserve the project name. Complete the first release soon after
registering it.

## Release checklist

1. Set the intended version and update the lockfile:

   ```console
   uv version VERSION
   uv lock
   ```

2. Run `make ci`, commit the version change, and merge it to `main`.
3. Create a GitHub Release from that exact commit with the tag `vVERSION`. The workflow rejects a
   tag that does not match the version in `pyproject.toml`.
4. Publish the GitHub Release and approve its deployment to the protected `pypi` environment.
5. Wait for the `Publish Python distribution` workflow to finish, then verify the release:

   ```console
   uv tool install mulled-biopixi==VERSION
   mulled-biopixi --help
   ```

PyPI releases are immutable. If publication fails after accepting one artifact, diagnose the
workflow and release a new patch version rather than attempting to replace an uploaded file.
