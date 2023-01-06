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
        self.min_versions = {}
        self.max_version = Version(self.cfg.params.max_version)

        # sanity check that min versions are never > version
        for n, min_v in self.cfg.min_versions.items():
            set_v = self.cfg.versions.get(n)
            if set_v is not None:
                set_v = Version(set_v)
            if min_v == "actual":
                if set_v is None:
                    raise ValueError(f"{n}: min version is 'actual' but no set version")

                min_v = set_v
            else:
                min_v = Version(min_v)

            if set_v is not None:
                if min_v > set_v:
                    raise ValueError(
                        f"min version of {n} ({min_v}) is greater than set version {set_v}"
                    )

            self.min_versions[n] = min_v

        self.wpilib_approx = ""
        self.versions = {}
        for n, v in self.cfg.versions.items():
            v = Version(v)
            # New assumption: all wpilib packages are working on the same approx version
            approx = self._compute_wpilib_approx_version(v)
            if self.wpilib_approx == "":
                self.wpilib_approx = approx
            elif approx != self.wpilib_approx:
                raise ValueError(
                    f"wpilib package approx version mismatch: {approx} vs {self.wpilib_approx}"
                )

            self.versions[n] = v

    @property
    def wpilib_bin_version(self) -> str:
        return self.cfg.params.wpilib_bin_version

    @property
    def wpilib_bin_url(self) -> str:
        return self.cfg.params.wpilib_bin_url

    @property
    def wpilib_packages(self) -> typing.List[str]:
        return self.cfg.params.wpilib_packages

    def _compute_wpilib_approx_version(self, v: Version) -> str:
        return ".".join(map(str, v.release[:3]))

    def _update_req_version(
        self,
        pypi_name: str,
        commit_changes: typing.Set[str],
        display_changes: typing.List[str],
        req: Requirement,
        min_version: Version,
    ) -> bool:
        has_min = False
        has_max = False
        has_approx = False
        changed = False

        new_specs = []

        is_this_wpilib_package = pypi_name in self.wpilib_packages
        is_req_wpilib_package = req.name in self.wpilib_packages
        is_wpilib_on_wpilib = is_req_wpilib_package and is_this_wpilib_package

        my_version = self.versions[pypi_name]

        for spec in req.specifier:
            version = Version(spec.version)
            if spec.operator == ">=":
                # min version -- no longer used for wpilib packages depending
                # on wpilib packages
                if is_wpilib_on_wpilib:
                    changed = True
                else:
                    if version < min_version:
                        changed = True
                        commit_changes.add(f"{req.name} >= {min_version}")
                        new_specs.append(f">={min_version}")
                    else:
                        new_specs.append(str(spec))

                    has_min = True
            elif spec.operator == "<":
                # max version -- no longer used for wpilib packages depending
                # on wpilib packages
                if is_wpilib_on_wpilib:
                    changed = True
                else:
                    if version != self.max_version:
                        raise ValueError(
                            f"{req}: max_version ({version}) != {self.max_version}"
                        )

                    new_specs.append(str(spec))
                    has_max = True
            elif spec.operator == "~=":
                # approximate version -- only changed for wpilib packages
                # depending on wpilib packages
                if is_wpilib_on_wpilib and spec.version != self.wpilib_approx:
                    commit_changes.add(f"{req.name} ~= {self.wpilib_approx}")
                    new_specs.append(f"~={self.wpilib_approx}")
                    changed = True
                else:
                    new_specs.append(str(spec))
                has_approx = True
            else:
                new_specs.append(str(spec))

        if is_wpilib_on_wpilib and not has_approx:
            commit_changes.add(f"{req.name} ~= {self.wpilib_approx}")
            new_specs.append(f"~={self.wpilib_approx}")
            changed = True

        if not is_wpilib_on_wpilib:
            if not has_min or not has_max:
                raise ValueError(
                    f"invalid requirement {req} (has_min={has_min}, has_max={has_max} has_approx={has_approx}"
                )

        if changed:
            old_req = str(req)
            req.specifier = SpecifierSet(",".join(new_specs))
            display_changes.append(f"{old_req} -> {req}")
            return True

        return False

    def _update_requirements(
        self,
        pypi_name: str,
        what: str,
        commit_changes: typing.Set[str],
        reqs: typing.List[str],
    ) -> bool:
        requires = list(reqs)
        changes = []
        for i, req_str in enumerate(requires):
            req = Requirement(req_str)
            # see if the requirement is in our list of managed dependencies; if so
            # then change it
            if req.name in self.min_versions:
                if self._update_req_version(
                    pypi_name, commit_changes, changes, req, self.min_versions[req.name]
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
            pypi_name,
            "build-system.requires",
            commit_changes,
            data["build-system"]["requires"],
        )

        # update tool.robotpy-build.metadata: install_requires
        self._update_requirements(
            pypi_name,
            "metadata.install_requires",
            commit_changes,
            data["tool"]["robotpy-build"]["metadata"]["install_requires"],
        )

        # if a wpilib project, update versions in maven_lib_download
        if pypi_name in self.cfg.params.wpilib_packages:
            iter = list(data["tool"]["robotpy-build"]["wrappers"].items())
            if "static_libs" in data["tool"]["robotpy-build"]:
                iter += list(data["tool"]["robotpy-build"]["static_libs"].items())
            for pkg, wrapper in iter:
                if "maven_lib_download" in wrapper and pkg != "opencv_cpp":
                    if wrapper["maven_lib_download"]["repo_url"] != self.wpilib_bin_url:
                        print(
                            "* ",
                            pkg,
                            "repo url:",
                            wrapper["maven_lib_download"]["repo_url"],
                            "->",
                            self.wpilib_bin_url,
                        )
                        commit_changes.add(f"repo updated to {self.wpilib_bin_url}")
                        wrapper["maven_lib_download"]["repo_url"] = self.wpilib_bin_url

                    if (
                        wrapper["maven_lib_download"]["version"]
                        != self.wpilib_bin_version
                    ):
                        print(
                            "* ",
                            pkg,
                            "so version:",
                            wrapper["maven_lib_download"]["version"],
                            "->",
                            self.wpilib_bin_version,
                        )
                        commit_changes.add(f"lib updated to {self.wpilib_bin_version}")
                        wrapper["maven_lib_download"][
                            "version"
                        ] = self.wpilib_bin_version

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
