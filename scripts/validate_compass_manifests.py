#!/usr/bin/env python3
"""
Validate Compass catalog-info.yaml manifests for agentic packs in the root Location.

Structural checks (no SKILL.md semantic inference):
  - Roster parity: skills on disk vs pack Location targets vs manifest files
  - Plugin inverse roster and skill dependsOn plugin
  - Bidirectional dependsOn/dependencyOf for skill→skill and skill→owned-MCP
  - Plugin→owned-MCP inverse; plugin MCP dependsOn union vs skills
  - Forbidden partOf/hasPart on skill manifests; redundant plugin dependsOn system
  - Dangling airesource refs; canonical mcpserver:redhat/* allowed without local file
  - Basic field conventions on skill manifests (namespace, owner, agents, distribution)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ROOT_CATALOG = _REPO_ROOT / "catalog-info.yaml"
_SKILL_TARGET_RE = re.compile(r"^\./skills/([^/]+)/catalog-info\.yaml$")
_PLUGIN_REF = "airesource:ai5-marketplace/{pack}"
_FORBIDDEN_RELATION_RE = re.compile(r"^\s+(partOf|hasPart)\s*:", re.MULTILINE)
_CANONICAL_MCP_PREFIXES = ("mcpserver:redhat/", "mcpserver:default/")
_EXPECTED_OWNER = "group:redhat/ai5-marketplace"
_EXPECTED_NAMESPACE = "ai5-marketplace"


def _load_yaml(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected mapping at top level")
    return data


def _refs(spec: dict, key: str) -> list[str]:
    value = spec.get(key) or []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def registered_packs() -> list[str]:
    data = _load_yaml(_ROOT_CATALOG)
    packs: list[str] = []
    for target in data.get("spec", {}).get("targets", []):
        if not isinstance(target, str):
            continue
        if target.startswith("./mcps/"):
            continue
        if not target.endswith("/catalog-info.yaml"):
            continue
        parts = Path(target).parts
        if len(parts) != 2:
            continue
        packs.append(parts[0])
    return sorted(set(packs))


def skills_on_disk(pack_dir: Path) -> set[str]:
    skills_dir = pack_dir / "skills"
    if not skills_dir.is_dir():
        return set()
    return {
        entry.name
        for entry in skills_dir.iterdir()
        if entry.is_dir() and (entry / "SKILL.md").is_file()
    }


def location_skill_targets(loc_data: dict) -> set[str]:
    targets: set[str] = set()
    for target in loc_data.get("spec", {}).get("targets", []):
        if not isinstance(target, str):
            continue
        match = _SKILL_TARGET_RE.match(target)
        if match:
            targets.add(match.group(1))
    return targets


def load_owned_mcps() -> dict[str, Path]:
    owned: dict[str, Path] = {}
    for path in sorted((_REPO_ROOT / "mcps").glob("*.yaml")):
        if path.name == "catalog-info.yaml":
            continue
        data = _load_yaml(path)
        meta = data.get("metadata", {})
        name = meta.get("name")
        namespace = meta.get("namespace", _EXPECTED_NAMESPACE)
        if name:
            owned[f"mcpserver:{namespace}/{name}"] = path
    return owned


def _is_allowed_dangling_mcp(ref: str) -> bool:
    return ref.startswith(_CANONICAL_MCP_PREFIXES)


def _check_skill_conventions(path: Path, data: dict, errors: list[str]) -> None:
    meta = data.get("metadata", {})
    spec = data.get("spec", {})
    rel = path.relative_to(_REPO_ROOT)

    if meta.get("namespace") != _EXPECTED_NAMESPACE:
        errors.append(f"{rel}: metadata.namespace must be {_EXPECTED_NAMESPACE}")
    labels = meta.get("labels") or {}
    if labels.get("distribution") != "external":
        errors.append(f"{rel}: labels.distribution must be external")
    if spec.get("owner") != _EXPECTED_OWNER:
        errors.append(f"{rel}: spec.owner must be {_EXPECTED_OWNER}")
    if spec.get("agents") != []:
        errors.append(f"{rel}: spec.agents must be []")
    if spec.get("type") != "skill":
        errors.append(f"{rel}: spec.type must be skill")


def validate_pack(pack: str, owned_mcps: dict[str, Path], errors: list[str]) -> None:
    pack_dir = _REPO_ROOT / pack
    loc_path = pack_dir / "catalog-info.yaml"
    plugin_path = pack_dir / f"{pack}-plugin.yaml"
    plugin_ref = _PLUGIN_REF.format(pack=pack)

    if not loc_path.is_file():
        errors.append(f"{pack}: missing {loc_path.relative_to(_REPO_ROOT)}")
        return
    if not plugin_path.is_file():
        errors.append(f"{pack}: missing {plugin_path.relative_to(_REPO_ROOT)}")
        return

    loc_data = _load_yaml(loc_path)
    plugin_data = _load_yaml(plugin_path)
    plugin_spec = plugin_data.get("spec", {})

    disk = skills_on_disk(pack_dir)
    loc_targets = location_skill_targets(loc_data)

    for skill in sorted(disk - loc_targets):
        errors.append(
            f"{pack}: skill '{skill}' on disk but missing from "
            f"{loc_path.relative_to(_REPO_ROOT)} targets"
        )
    for skill in sorted(loc_targets - disk):
        errors.append(
            f"{pack}: {loc_path.relative_to(_REPO_ROOT)} targets skill '{skill}' "
            "but skills/<name>/SKILL.md not found"
        )

    plugin_depof_skills = {
        ref.split("/", 1)[1]
        for ref in _refs(plugin_spec, "dependencyOf")
        if ref.startswith("airesource:ai5-marketplace/") and ref != plugin_ref
    }
    for skill in sorted(disk - plugin_depof_skills):
        errors.append(
            f"{pack}: {plugin_path.relative_to(_REPO_ROOT)} missing "
            f"dependencyOf airesource:ai5-marketplace/{skill}"
        )
    for skill in sorted(plugin_depof_skills - disk):
        errors.append(
            f"{pack}: {plugin_path.relative_to(_REPO_ROOT)} dependencyOf unknown skill '{skill}'"
        )

    if "system:default/agentic-plugins" in _refs(plugin_spec, "dependsOn"):
        errors.append(
            f"{plugin_path.relative_to(_REPO_ROOT)}: redundant "
            "dependsOn system:default/agentic-plugins (use spec.system)"
        )

    skill_depends: dict[str, list[str]] = {}
    skill_depof: dict[str, set[str]] = {}
    pack_skill_mcp_union: set[str] = set()

    for skill in sorted(disk):
        manifest = pack_dir / "skills" / skill / "catalog-info.yaml"
        if not manifest.is_file():
            errors.append(f"{pack}: missing {manifest.relative_to(_REPO_ROOT)}")
            continue

        raw = manifest.read_text(encoding="utf-8")
        if _FORBIDDEN_RELATION_RE.search(raw):
            errors.append(
                f"{manifest.relative_to(_REPO_ROOT)}: partOf/hasPart not supported on AiResource"
            )

        data = _load_yaml(manifest)
        _check_skill_conventions(manifest, data, errors)
        spec = data.get("spec", {})
        deps = _refs(spec, "dependsOn")
        skill_depends[skill] = deps
        skill_depof[skill] = set(_refs(spec, "dependencyOf"))

        if plugin_ref not in deps:
            errors.append(
                f"{manifest.relative_to(_REPO_ROOT)}: missing dependsOn {plugin_ref}"
            )

        for dep in deps:
            if dep.startswith("mcpserver:"):
                pack_skill_mcp_union.add(dep)

    plugin_mcp_deps = {
        d for d in _refs(plugin_spec, "dependsOn") if d.startswith("mcpserver:")
    }
    if plugin_mcp_deps != pack_skill_mcp_union:
        missing_on_plugin = pack_skill_mcp_union - plugin_mcp_deps
        extra_on_plugin = plugin_mcp_deps - pack_skill_mcp_union
        if missing_on_plugin:
            errors.append(
                f"{pack}: {plugin_path.relative_to(_REPO_ROOT)} missing plugin dependsOn "
                f"MCP union entries: {sorted(missing_on_plugin)}"
            )
        if extra_on_plugin:
            errors.append(
                f"{pack}: {plugin_path.relative_to(_REPO_ROOT)} extra plugin dependsOn "
                f"MCP refs not used by any skill: {sorted(extra_on_plugin)}"
            )

    for skill, deps in skill_depends.items():
        orchestrator_ref = f"airesource:ai5-marketplace/{skill}"
        for dep in deps:
            if dep == plugin_ref:
                continue
            if dep.startswith("airesource:ai5-marketplace/"):
                target = dep.split("/", 1)[1]
                if target not in disk:
                    errors.append(
                        f"{pack}/skills/{skill}/catalog-info.yaml: dependsOn {dep} "
                        "but skill not in pack"
                    )
                    continue
                if orchestrator_ref not in skill_depof.get(target, set()):
                    errors.append(
                        f"{pack}/skills/{target}/catalog-info.yaml: missing dependencyOf "
                        f"{orchestrator_ref} (inverse of {skill} dependsOn)"
                    )
            elif dep.startswith("mcpserver:"):
                if dep in owned_mcps:
                    mcp_path = owned_mcps[dep]
                    mcp_spec = _load_yaml(mcp_path).get("spec", {})
                    if orchestrator_ref not in set(_refs(mcp_spec, "dependencyOf")):
                        errors.append(
                            f"{mcp_path.relative_to(_REPO_ROOT)}: missing dependencyOf "
                            f"{orchestrator_ref} (inverse of {skill} dependsOn {dep})"
                        )
                elif not _is_allowed_dangling_mcp(dep):
                    errors.append(
                        f"{pack}/skills/{skill}/catalog-info.yaml: unknown mcpserver ref {dep}"
                    )

    for dep in _refs(plugin_spec, "dependsOn"):
        if dep.startswith("mcpserver:") and dep in owned_mcps:
            mcp_path = owned_mcps[dep]
            mcp_spec = _load_yaml(mcp_path).get("spec", {})
            if plugin_ref not in set(_refs(mcp_spec, "dependencyOf")):
                errors.append(
                    f"{mcp_path.relative_to(_REPO_ROOT)}: missing dependencyOf "
                    f"{plugin_ref} (inverse of plugin dependsOn {dep})"
                )


def main() -> int:
    errors: list[str] = []
    owned_mcps = load_owned_mcps()

    if not _ROOT_CATALOG.is_file():
        errors.append(
            f"missing root catalog Location: {_ROOT_CATALOG.relative_to(_REPO_ROOT)}"
        )
    else:
        for pack in registered_packs():
            validate_pack(pack, owned_mcps, errors)

    if errors:
        print("Compass manifest validation failed:", file=sys.stderr)
        for err in errors:
            print(f"  • {err}", file=sys.stderr)
        return 1

    print("✓ Compass manifest validation passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
