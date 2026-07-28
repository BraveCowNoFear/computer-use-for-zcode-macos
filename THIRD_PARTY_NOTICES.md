# Third-party notices

## Cua Driver

- Project: `trycua/cua`, component `cua-driver`
- Source: https://github.com/trycua/cua
- Tested release: `cua-driver-rs-v0.12.6`
- Reviewed general Skill: https://github.com/trycua/cua/blob/9eb1f481b8a12cd6ffda2ad5af21653a9e5aa9e5/libs/cua-driver/rust/Skills/cua-driver/SKILL.md
- Reviewed macOS guidance: https://github.com/trycua/cua/blob/9eb1f481b8a12cd6ffda2ad5af21653a9e5aa9e5/libs/cua-driver/rust/Skills/cua-driver/MACOS.md
- Reviewed revision: `9eb1f481b8a12cd6ffda2ad5af21653a9e5aa9e5`
- License: MIT

This repository does not vendor the Cua Driver binary or source. The plugin's
macOS launcher downloads the versioned universal release archive, verifies its
pinned SHA-256 and Cua AI signing identity, and publishes the upstream signed
app inside the plugin's data directory. Cua Driver remains subject to its
upstream license and notices.

## Hermes Agent macOS Computer Use Skill

- Project: `NousResearch/hermes-agent`
- Reviewed source: https://github.com/NousResearch/hermes-agent/blob/17dfc6bec4a8b7fd840d479c33e9a7b2449f805d/skills/apple/macos-computer-use/SKILL.md
- Reviewed revision: `17dfc6bec4a8b7fd840d479c33e9a7b2449f805d`
- License: MIT at the reviewed repository revision

The project's high-level background-control routing was informed by publicly
documented behavior in the Hermes Skill. No Hermes source text is distributed
as part of this project.
