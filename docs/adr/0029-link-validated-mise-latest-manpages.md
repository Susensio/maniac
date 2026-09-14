# ADR-0029: Link validated Mise latest aliases for global vendor manpages

Status: Accepted
Date: 2026-09-14

## Context

ADR-0028 allowed a direct vendor link only when the provider supplied a target that could advance safely.
Mise installations on the development system carried a `latest` alias beside their concrete version directory, and that alias advanced when Mise updated the globally selected tool.

MANIAC concerned itself with globally selected tools rather than project-local version resolution.
For that scope, following Mise's global alias was more useful than freezing a vendor page in MANIAC storage.

Mise's Directory Structure documentation described prefix and alias links as optional and directed callers to inspect an installation rather than construct an assumed alias path.
An unchecked `latest` path would therefore turn an unusual installation layout into a dangling or wrong-version manpage.

## Decision

The Mise provider may return a direct vendor-manpage target only when the page is under the inspected installation root, its sibling `latest` path is a symlink, and that alias resolves to that exact root at install time.
The installed manpath link uses the page's relative path below the validated alias.

All other Mise layouts retain ADR-0028's durable MANIAC materialization fallback.

`list` treats a managed vendor page whose alias target no longer belongs to the current inspected installation as `outdated`.
Provider-owned target bytes may change as an expected Mise update, so uninstall removes an intact expected external link without treating that content change as a user modification and never removes the provider's target.

Existing materialized vendor pages are not retargeted automatically because their manifest entries do not retain the original page-relative source path.
A normal reinstall can adopt the validated alias.

## Consequences

Global Mise upgrades can refresh eligible vendor manuals without a MANIAC reinstall.
An alias that is absent, no longer matches the inspected binary, or names an unsupported layout remains visibly stale or conservatively materialized instead of being trusted by path convention.

MANIAC adds a Mise-specific capability rather than asserting that every installer has a comparable global alias.
The global-tools scope is intentional; project-local version selection remains outside this behavior.
