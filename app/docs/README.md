# Documentation Guide

This directory contains both public project documentation and maintainer-facing
working material.

Tandem itself is a public developer preview and is intentionally positioned as
an OpenClaw-first browser environment, so documentation should stay consistent
with that framing.

That positioning is first-party, not incidental: Tandem is maintained in the
same ecosystem as OpenClaw and should be documented accordingly.

## Start Here

If you are new to the project, use this order:

1. [README.md](../README.md)
2. [PROJECT.md](../PROJECT.md)
3. [CHANGELOG.md](../CHANGELOG.md)
4. the specific docs folder relevant to the subsystem you are touching

## Public-Facing Docs

- `plans/`: design proposals for not-yet-implemented features
- `research/`: browser and feature research material
- `implementations/`: subsystem notes and implementation writeups
- `archive/`: historical material kept for engineering context

## Maintainer Workflow Docs

Some directories still contain files such as `CLAUDE.md`,
`LEES-MIJ-EERST.md`, phase documents, and session prompts. Those files are
retained because they still capture useful implementation context, but they are
not the recommended entry point for new public readers.

## Internal Planning Docs

Project-management material lives under `internal/`.

- `internal/ROADMAP.md`
- `internal/STATUS.md`

These files are intentionally separated from the main public documentation
surface.
