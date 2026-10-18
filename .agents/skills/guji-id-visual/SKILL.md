---
name: guji-id-visual
description: Use when redesigning, extending, testing, or reviewing GuJi ID's homepage, search UI, person profile, classical-text citations, animations, typography, or responsive visual system. Do not use this skill to change database, harvesting, DeepSeek verification, identity resolution, or ingestion algorithms.
---

# GuJi ID Visual System

## Non-negotiable style direction

A finely crafted interactive Chinese antique-book experience, **not** an AI-generated SaaS hero layout. Three connected acts: cinematic antique book in low-key warm light → a close-up of a printed page as users scroll → calm, ivory-colored scholarly person records with visible evidence provenance.

Study this project's `DESIGN_SPEC.md`, `index.html`, `styles.css`, and `README.md` before editing. Use the already approved imagery as **visual reference only**; decorative image text is not historical evidence. The typography, records, and navigation must remain actual HTML.

## Design guards

- First screen: one dominant antique book photographic scene, asymmetrical cinema composition, Chinese serif display title. Search deliberately lives in the paper-white research section, not on the dark cover; do not re-add a cover search form.
- Scroll: depth, scaling, focus and a dark-to-parchment shift driven by user input. No mandatory intro, autoplay long video, particle field, heavy WebGL, faux 3D book, or arbitrary card fly-ins.
- Archive stage: thin archival rules, annotations, clear document provenance, no invented citations. Use real database values only after integrating backend.
- Search results MUST preserve separate identities for homonyms. Never simplify contradictory identities or suppress unresolved status just to make the UI clean.
- Aim for careful typographic rhythm, minimal red seals (cinnabar red), subdued paper grain. Do not add gratuitous scrollbars, pill cards, gradient blobs or large rounded tiles.
- Accessibility: keyboard-operable links and search, semantic headings, visible focus, adequate contrast, mobile layout, `prefers-reduced-motion` fallback.
- Performance: prioritize static assets in WebP and CSS/DOM; prefer `transform` + `opacity` and `requestAnimationFrame`; only add GSAP if requirements justify it and it is locally available.

## Workflow

1. Inspect the existing GuJi ID templates, routes, and static asset layout; **do not guess route names**.
2. Work in a feature branch / isolated `design-lab`; preserve screenshots of old views.
3. Render full-height screenshots at **1440×900** and **390×844** for scene 1, transition, scene 3. Review each against approved concept frames.
4. Check search input, button, fast query links, tab keyboard controls, mobile navigation, and no JS errors.
5. Confirm no modifications in database, ingestion, evidence verification, batch approval, or identity resolution.
6. Only then integrate the display layer with existing backend search and people records.

## Optional division of labor

When the running Codex setup supports explicit subagents, one subagent may review visual screenshots and another may check accessibility / regression. Do not infer that a particular subagent or Skill is installed. Do not call DeepSeek just to verify visual quality.

## Definition of done

Functional browser page, responsive, scroll motion has an intentional editorial trajectory, never fake evidence, tests pass, old features retained, screenshots attached in implementation report.


## V0.2 A+B evidence-safe rendering

- The approved static images contain baked-in names, including 李白 in `archive-page.webp`; these are decorative mockups only. Never reuse as authoritative manuscript scans or as a personalized visual for other records.
- A mode: neutral antique-paper/texture photo layer + live HTML/SVG names, aliases, dynasty, relationship status, and proven citations keyed by canonical person identity. Preserve a distinct visual label for digital annotation versus original document text.
- B mode: render a genuine, licensed/allowed archival scan **only** when source, stable person ID and evidence link are verified; draw page-coordinate highlight boxes only when manually checked. Handle absent, uncertain, and broken scans with graceful fallback to A.
- Keep homonyms disambiguated; never infer the identity directly from typed search query. Do not alter DB, ingest, DeepSeek verification, identity resolution, or historical evidence in this design task.
- Read `CODEX_HANDOFF.md` V0.2 for the binding scope and full acceptance tests before any production integration.
