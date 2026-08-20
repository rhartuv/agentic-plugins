# MCP mapping: mcps.json → Compass entity refs

Map **only** MCP servers a skill actually uses. Derive usage from `SKILL.md` `allowed-tools`, `Required MCP Servers`, and workflow MCP tool names — not from copying another skill's manifest.

## Owned MCP servers (registered in `mcps/`)

| `mcps.json` key | Tool / signal hints | Compass entity ref |
|-----------------|---------------------|-------------------|
| `openshift-self-managed` | Assisted Installer self-managed API | `mcpserver:ai5-marketplace/assisted-installer` |
| `openshift-ocm-managed` | OCM / ROSA / managed clusters | `mcpserver:ai5-marketplace/assisted-installer` |
| `openshift-administration` | Kubernetes/OpenShift cluster ops (`configuration_*`, `nodes_*`, etc.) | `mcpserver:ai5-marketplace/openshift-mcp-server` |
| `openshift-virtualization` | KubeVirt VM tools | `mcpserver:ai5-marketplace/openshift-mcp-server` |
| `openshift` | Generic OpenShift MCP (other packs) | `mcpserver:ai5-marketplace/openshift-mcp-server` |
| `aap-mcp-job-management` | `controller.*`, `eda.*`, job/workflow tools | `mcpserver:ai5-marketplace/ansible-automation-platform` |
| `aap-mcp-inventory-management` | `controller.inventories_*`, `controller.hosts_*`, `controller.groups_*` | `mcpserver:ai5-marketplace/ansible-automation-platform` |

## Canonical MCP servers (not duplicated in `mcps/`)

| `mcps.json` key | Tool / signal hints | Compass entity ref |
|-----------------|---------------------|-------------------|
| `lightspeed-mcp` | `vulnerability__*`, `inventory__*`, `get_mcp_version` (Lightspeed) | `mcpserver:redhat/red-hat-lightspeed-mcp-server` |
| `red-hat-security` | Red Hat Security MCP / CVE advisory tools | `mcpserver:redhat/security-mcp-server` |

## AAP tool prefix quick reference

If `allowed-tools` includes any of these prefixes, the skill uses **ansible-automation-platform**:

- `controller.`
- `gateway.`
- `eda.`
- `galaxy.`
- `lightspeed.aap_rag_search`

## No MCP manifest entry

Skills that use only scripts, `WebFetch`, or public APIs (no MCP tools in `allowed-tools` and no `Required MCP Servers`) should have **no** `mcpserver:` in `dependsOn`. Examples: ocp-admin security-validation skills (`container-cve-validator`, `coreos-cve-validator`, `cve-recon`, `image-inspect`).

## Plugin `dependsOn` MCP list

The pack plugin's `dependsOn` MCP entries = **union** of all `mcpserver:` refs across every skill manifest in that pack (not every key in `mcps.json`).

## Owned MCP manifest files

| Compass name | File |
|--------------|------|
| `openshift-mcp-server` | `mcps/openshift-mcp-server.yaml` |
| `assisted-installer` | `mcps/assisted-installer.yaml` |
| `ansible-automation-platform` | `mcps/ansible-automation-platform.yaml` |

When a skill adds or removes an MCP `dependsOn`, update the corresponding MCP file's `dependencyOf` list.
