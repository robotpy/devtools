#!/usr/bin/env python3

import argparse
import pathlib
import typing

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import Version
import tomlkit

import config
from githelp import GitRepo


class ProjectUpdater:
    """
    Updates pyproject.toml versions for robotpy-build projects
    """

    def __init__(self, cfg: config.UpdateConfig) -> None:
        self.dry_run = False
        self.git_commit = True

        self.cfg = cfg
        self.min_versions = {n: Version(v) for n, v in self.cfg.min_versions.items()}
        self.max_version = Version(self.cfg.params.max_version)

        # sanity check that min versions are never > version
        for n, v in self.min_versions.items():
            set_v = self.cfg.versions.get(n)
            if set_v is not None:
                if v > Version(set_v):
                    raise ValueError(
                        f"min version of {n} ({v}) is greater than set version {set_v}"
                    )
        for n, v in self.cfg.versions.items():
            v = Version(v)

    @property
    def wpilib_version(self) -> str:
        return self.cfg.params.wpilib_version

    def _update_req_version(
        self,
        commit_changes: typing.Set[str],
        display_changes: typing.List[str],
        req: Requirement,
        min_version: Version,
    ) -> bool:
        has_min = False
        has_max = False
        changed = False

        new_specs = []

        for spec in req.specifier:
            version = Version(spec.version)
            if spec.operator == ">=":
                # min version
                if version < min_version:
                    changed = True
                    commit_changes.add(f"{req.name} >= {min_version}")
                    new_specs.append(f">={min_version}")
                else:
                    new_specs.append(str(spec))

                has_min = True
            elif spec.operator == "<":
                # max version
                if version != self.max_version:
                    raise ValueError(
                        f"{req}: max_version ({version}) != {self.max_version}"
                    )

                new_specs.append(str(spec))
                has_max = True
            else:
                new_specs.append(str(spec))

        if not has_min or not has_max:
            raise ValueError(
                f"invalid requirement {req} (has_min={has_min}, has_max={has_max}"
            )

        if changed:
            old_req = str(req)
            req.specifier = SpecifierSet(",".join(new_specs))
            display_changes.append(f"{old_req} -> {req}")
            return True

        return False

    def _update_requirements(
        self, what: str, commit_changes: typing.Set[str], reqs: typing.List[str]
    ) -> bool:
        requires = list(reqs)
        changes = []
        for i, req_str in enumerate(requires):
            req = Requirement(req_str)
            # see if the requirement is in our list of managed dependencies; if so
            # then change it
            if req.name in self.min_versions:
                if self._update_req_version(
                    commit_changes, changes, req, self.min_versions[req.name]
                ):
                    reqs[i] = str(req)

        if changes:
            print(f"* {what}:")
            for change in changes:
                print("  -", change)
            return True

        return False

    def update_pyproject_toml(self, path: pathlib.Path) -> bool:

        with open(path) as fp:
            data = tomlkit.parse(fp.read())

        pypi_name = data["tool"]["robotpy-build"]["metadata"]["name"]

        if self.dry_run:
            print(f"Checking {pypi_name} ({path})")
        else:
            print(f"Updating {pypi_name} ({path})")

        commit_changes: typing.Set[str] = set()

        # update build-system
        self._update_requirements(
            "build-system.requires", commit_changes, data["build-system"]["requires"]
        )

        # update tool.robotpy-build.metadata: install_requires
        self._update_requirements(
            "metadata.install_requires",
            commit_changes,
            data["tool"]["robotpy-build"]["metadata"]["install_requires"],
        )

        # if a wpilib project, update versions in maven_lib_download
        if pypi_name in self.cfg.params.wpilib_packages:
            for pkg, wrapper in data["tool"]["robotpy-build"]["wrappers"].items():
                if "maven_lib_download" in wrapper:
                    if wrapper["maven_lib_download"]["version"] != self.wpilib_version:
                        print(
                            "* ",
                            pkg,
                            "so version:",
                            wrapper["maven_lib_download"]["version"],
                            "->",
                            self.wpilib_version,
                        )
                        commit_changes.add(f"lib updated to {self.wpilib_version}")
                        wrapper["maven_lib_download"]["version"] = self.wpilib_version

        if not commit_changes:
            print("* no changes needed")
            return False

        elif not self.dry_run:
            repo = GitRepo(path.parent)
            if self.git_commit:
                if repo.is_file_dirty(path.name):
                    raise ValueError(f"{path} has outstanding edits, refusing to write")

            s = tomlkit.dumps(data)
            with open(path, "w") as fp:
                fp.write(s)

            if self.git_commit:
                msg = "Updated dependencies\n\n"
                msg += "- " + "\n- ".join(sorted(commit_changes))

                repo.commit(path.name, msg)

        return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("pyproject_toml", type=pathlib.Path)
    parser.add_argument("--commit", action="store_true", default=False)

    args = parser.parse_args()

    cfgpath = pathlib.Path(__file__).parent.absolute() / "cfg.toml"
    cfg, _ = config.load(cfgpath)

    updater = ProjectUpdater(cfg)
    updater.git_commit = args.commit

    updater.update_pyproject_toml(args.pyproject_toml)


if __name__ == "__main__":
    exit(main())
