# Backlog

Open work with no single line to mark. `rg -n 'BUG:|TODO:'` lists the rest.

- Define and implement upgradeable MANIAC-managed manpages. Record generation inputs, then compare the installed tool version and documentation context to identify pages that have become stale and can be regenerated.
- Add package-provenance discovery for system candidates. Resolve the exact manpage and executable through the native package manager, distinguish distro/package sources from proven upstream repositories, and cache the ownership lookups. Use the package's bounded `/usr/share/doc` and Info material as documentation context, with package descriptions only as supplementary context.
- Resolve installed package metadata before Mise registry inference. For a resolved package-manager installation, parse the package manifest for repository, homepage, executable, and version metadata; support npm `package.json` first, then equivalent Python, Cargo, Go, and Homebrew metadata. Registry APIs remain a fallback only after the package identity is established, never from a bare executable name.
- Extract bounded documentation from an installed package root as well as a cloned repository. Read every eligible documentation file at the package root and under recognized documentation directories, while excluding dependencies and generated/build directories. Keep package-local, package-manager, and upstream-repository content separately labelled in generated context.
