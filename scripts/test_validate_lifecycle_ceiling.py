#!/usr/bin/env python3
"""Pytest unit tests for the Compass lifecycle ceiling validator."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

_SCRIPTS = Path(__file__).resolve().parent


def _load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


lifecycle_ceiling = _load_module("validate_lifecycle_ceiling", "validate_lifecycle_ceiling.py")


def _write_manifest(path: Path, *, name: str, kind: str = "AiResource", lifecycle: str | None = "__unset__") -> None:
    """Write a minimal Compass manifest. lifecycle='__unset__' omits the field entirely."""
    data: dict = {
        "apiVersion": "backstage.io/v1alpha1",
        "kind": kind,
        "metadata": {"name": name},
        "spec": {},
    }
    if lifecycle != "__unset__":
        data["spec"]["lifecycle"] = lifecycle
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _write_root_catalog(root: Path, packs: list[str]) -> None:
    data = {
        "apiVersion": "backstage.io/v1alpha1",
        "kind": "Location",
        "metadata": {"name": "agentic-plugins"},
        "spec": {"targets": [f"./{pack}/catalog-info.yaml" for pack in packs] + ["./mcps/catalog-info.yaml"]},
    }
    (root / "catalog-info.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    (root / "mcps").mkdir(parents=True, exist_ok=True)
    (root / "mcps" / "catalog-info.yaml").write_text(
        yaml.safe_dump({"apiVersion": "backstage.io/v1alpha1", "kind": "Location", "spec": {"targets": []}}),
        encoding="utf-8",
    )


def _write_pack(
    root: Path,
    pack: str,
    *,
    plugin_lifecycle: str | None = "__unset__",
    skills: dict[str, str | None] | None = None,
) -> None:
    """Create <pack>/<pack>-plugin.yaml and <pack>/skills/<skill>/catalog-info.yaml files."""
    pack_dir = root / pack
    _write_manifest(pack_dir / f"{pack}-plugin.yaml", name=pack, lifecycle=plugin_lifecycle)
    for skill_name, skill_lifecycle in (skills or {}).items():
        _write_manifest(
            pack_dir / "skills" / skill_name / "catalog-info.yaml",
            name=skill_name,
            lifecycle=skill_lifecycle,
        )
    (root / pack / "catalog-info.yaml").write_text(
        yaml.safe_dump({"apiVersion": "backstage.io/v1alpha1", "kind": "Location", "spec": {"targets": []}}),
        encoding="utf-8",
    )


@pytest.fixture
def repo_root(tmp_path: Path) -> Path:
    return tmp_path


class TestLifecycleRank:
    def test_known_lifecycles_ordered(self) -> None:
        assert lifecycle_ceiling.lifecycle_rank("development") == 0
        assert lifecycle_ceiling.lifecycle_rank("beta") == 1
        assert lifecycle_ceiling.lifecycle_rank("production") == 2

    def test_missing_lifecycle_defaults_to_development(self) -> None:
        assert lifecycle_ceiling.lifecycle_rank(None) == lifecycle_ceiling.lifecycle_rank("development")

    def test_unknown_lifecycle_raises(self) -> None:
        with pytest.raises(ValueError):
            lifecycle_ceiling.lifecycle_rank("ga")

    def test_is_deprecated(self) -> None:
        assert lifecycle_ceiling.is_deprecated("deprecated") is True
        assert lifecycle_ceiling.is_deprecated("Deprecated") is True
        assert lifecycle_ceiling.is_deprecated("beta") is False
        assert lifecycle_ceiling.is_deprecated(None) is False


class TestPassingCases:
    def test_skill_equal_to_plugin_passes(self, repo_root: Path) -> None:
        _write_pack(repo_root, "rh-demo", plugin_lifecycle="beta", skills={"demo-skill": "beta"})

        errors: list[str] = []
        lifecycle_ceiling.check_pack(repo_root, "rh-demo", errors)

        assert errors == []

    def test_skill_less_mature_than_plugin_passes(self, repo_root: Path) -> None:
        _write_pack(repo_root, "rh-demo", plugin_lifecycle="production", skills={"demo-skill": "development"})

        errors: list[str] = []
        lifecycle_ceiling.check_pack(repo_root, "rh-demo", errors)

        assert errors == []

    def test_missing_lifecycles_default_to_development_and_pass(self, repo_root: Path) -> None:
        # Neither plugin nor skill declares spec.lifecycle -> both default to development.
        _write_pack(repo_root, "rh-demo", plugin_lifecycle="__unset__", skills={"demo-skill": "__unset__"})

        errors: list[str] = []
        lifecycle_ceiling.check_pack(repo_root, "rh-demo", errors)

        assert errors == []

    def test_multiple_skills_all_within_ceiling(self, repo_root: Path) -> None:
        _write_pack(
            repo_root,
            "rh-demo",
            plugin_lifecycle="beta",
            skills={"skill-a": "development", "skill-b": "beta"},
        )

        errors: list[str] = []
        lifecycle_ceiling.check_pack(repo_root, "rh-demo", errors)

        assert errors == []


class TestFailCase:
    def test_skill_more_mature_than_plugin_fails(self, repo_root: Path) -> None:
        _write_pack(repo_root, "rh-demo", plugin_lifecycle="development", skills={"demo-skill": "beta"})

        errors: list[str] = []
        lifecycle_ceiling.check_pack(repo_root, "rh-demo", errors)

        assert len(errors) == 1
        assert "demo-skill" in errors[0]
        assert "'beta'" in errors[0]
        assert "'development'" in errors[0]

    def test_production_skill_under_beta_plugin_fails(self, repo_root: Path) -> None:
        _write_pack(repo_root, "rh-demo", plugin_lifecycle="beta", skills={"demo-skill": "production"})

        errors: list[str] = []
        lifecycle_ceiling.check_pack(repo_root, "rh-demo", errors)

        assert len(errors) == 1

    def test_only_offending_skill_is_reported(self, repo_root: Path) -> None:
        _write_pack(
            repo_root,
            "rh-demo",
            plugin_lifecycle="development",
            skills={"ok-skill": "development", "bad-skill": "production"},
        )

        errors: list[str] = []
        lifecycle_ceiling.check_pack(repo_root, "rh-demo", errors)

        assert len(errors) == 1
        assert "bad-skill" in errors[0]
        assert "ok-skill" not in errors[0]


class TestDeprecatedSkipLogic:
    def test_deprecated_skill_is_skipped_even_if_more_mature(self, repo_root: Path) -> None:
        _write_pack(repo_root, "rh-demo", plugin_lifecycle="development", skills={"demo-skill": "deprecated"})

        errors: list[str] = []
        lifecycle_ceiling.check_pack(repo_root, "rh-demo", errors)

        assert errors == []

    def test_deprecated_plugin_skips_all_skills(self, repo_root: Path) -> None:
        _write_pack(
            repo_root,
            "rh-demo",
            plugin_lifecycle="deprecated",
            skills={"demo-skill": "production"},
        )

        errors: list[str] = []
        lifecycle_ceiling.check_pack(repo_root, "rh-demo", errors)

        assert errors == []

    def test_deprecated_skill_among_others_only_skips_itself(self, repo_root: Path) -> None:
        _write_pack(
            repo_root,
            "rh-demo",
            plugin_lifecycle="development",
            skills={"deprecated-skill": "deprecated", "bad-skill": "beta"},
        )

        errors: list[str] = []
        lifecycle_ceiling.check_pack(repo_root, "rh-demo", errors)

        assert len(errors) == 1
        assert "bad-skill" in errors[0]
        assert "deprecated-skill" not in errors[0]


class TestValidateAll:
    def test_validate_all_discovers_registered_packs_from_root_catalog(self, repo_root: Path) -> None:
        _write_root_catalog(repo_root, ["rh-good", "rh-bad"])
        _write_pack(repo_root, "rh-good", plugin_lifecycle="beta", skills={"good-skill": "beta"})
        _write_pack(repo_root, "rh-bad", plugin_lifecycle="development", skills={"bad-skill": "production"})

        errors = lifecycle_ceiling.validate_all(repo_root)

        assert len(errors) == 1
        assert "bad-skill" in errors[0]

    def test_validate_all_ignores_unregistered_packs(self, repo_root: Path) -> None:
        # rh-bad exists on disk but is not listed in the root catalog-info.yaml targets.
        _write_root_catalog(repo_root, ["rh-good"])
        _write_pack(repo_root, "rh-good", plugin_lifecycle="beta", skills={"good-skill": "beta"})
        _write_pack(repo_root, "rh-bad", plugin_lifecycle="development", skills={"bad-skill": "production"})

        errors = lifecycle_ceiling.validate_all(repo_root)

        assert errors == []

    def test_validate_all_missing_root_catalog_reports_error(self, repo_root: Path) -> None:
        errors = lifecycle_ceiling.validate_all(repo_root)

        assert len(errors) == 1
        assert "catalog-info.yaml" in errors[0]

    def test_pack_missing_plugin_manifest_reports_error(self, repo_root: Path) -> None:
        _write_root_catalog(repo_root, ["rh-orphan"])
        (repo_root / "rh-orphan").mkdir(parents=True)

        errors = lifecycle_ceiling.validate_all(repo_root)

        assert len(errors) == 1
        assert "rh-orphan" in errors[0]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
