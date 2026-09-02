# ADR-0004: Add a global help-generated discovery flag to list-missing instead of a separate command

Status: Superseded by [ADR-0006](0006-candidate-repositories-translations.md)
Date: 2026-09-01

## Context

`list-missing` had scanned executable names in one bin directory and treated a page as present or absent.
Users wanted the same workflow to surface existing pages that MANIAC could replace with a richer manual, while finding them across the active system manpath.
The current reliable detector recognizes the standard generated-file marker, but its generator is an implementation detail rather than the product concept.
A separate command would have duplicated candidate reporting and split the discovery workflow.

## Decision

`list-missing` will gain an opt-in global discovery flag.
Without it, the command will retain its current bin-directory scan and output.
With it, the command will include both missing pages and recognized help-derived pages found by scanning active manpath directories, reporting each candidate's status and path.
The scan will inspect a bounded prefix of local roff files, including standard compressed formats, without a `man` subprocess per page, repository discovery, or LLM calls.
The exact flag spelling remains open until implementation, but it will describe help-derived pages rather than the detector used to recognize them.

## Consequences

Candidate discovery stays in one command and existing scripts keep the default behavior.
The flagged mode requires a global filesystem walk and a result schema that distinguishes missing from help-derived candidates.
The detector will initially classify only pages with a reliable generated-file signature, so the inventory will be intentionally conservative and can gain new classifiers later.
