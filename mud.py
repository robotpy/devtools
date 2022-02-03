#!/usr/bin/env python3

import datetime
import pathlib
import subprocess
import typing

from packaging.version import Version

import click
import requests
import time
import tomlkit

import config
import githelp
import update_pyproject


root = pathlib.Path(__file__).parent
repodir = root / "repos"


class IterMeta(typing.NamedTuple):
    repo: githelp.GitRepo
    name: str
    actual_version: str
    desired_version: str


class MassUpdateData:
    def __init__(self) -> None:
        self.cfg, self.rawcfg = config.load(root / "cfg.toml")
        self.pyupdater = update_pyproject.ProjectUpdater(self.cfg)

    def get_repo_path(self, repo_url: str) -> pathlib.Path:
        return repodir / repo_url.rsplit("/", 1)[-1]

    def repo_dirs(self):
        for repo_url in self.cfg.params.repos:
            yield githelp.GitRepo(self.get_repo_path(repo_url))

    def iter_repos_meta(self):
        for repo in self.repo_dirs():
            actual_version = repo.get_last_tag()
            name = self.get_repo_pypi_name(repo)
            desired_version = self.cfg.versions[name]
            yield IterMeta(repo, name, actual_version, desired_version)

    def get_repo_pypi_name(self, repo: githelp.GitRepo) -> str:
        with open(repo.path / "pyproject.toml") as fp:
            data = tomlkit.parse(fp.read())

        return data["tool"]["robotpy-build"]["metadata"]["name"]


@click.group()
@click.pass_context
def main(ctx: click.Context):
    """RobotPy mass update management tool"""
    ctx.obj = MassUpdateData()


@main.group()
def repo():
    """Repo management"""
    pass


@repo.command()
@click.pass_obj
def clone(mud: MassUpdateData):
    """Check out repos"""

    repodir.mkdir(parents=True, exist_ok=True)

    for repo_url in mud.cfg.params.repos:
        if not mud.get_repo_path(repo_url).exists():
            subprocess.run(["git", "clone", repo_url], cwd=repodir)


@repo.command()
@click.pass_obj
def ensure(mud: MassUpdateData):
    """Ensures repos are on main branch, updated, and clean"""

    for repo in mud.repo_dirs():
        print(f"{repo.path.name}:")
        repo.checkout_branch("main")
        repo.pull()
        print()


@main.command(context_settings={"ignore_unknown_options": True})
@click.argument("args", nargs=-1)
@click.pass_obj
def git(mud: MassUpdateData, args):
    """Execute git command across all repos"""
    for repo in mud.repo_dirs():
        print(f"{repo.path.name}:")
        subprocess.run(["git"] + list(args), cwd=repo.path)
        print()


@main.group()
def pyproject():
    """pyproject.toml management"""
    pass


@pyproject.command()
@click.option("--commit", is_flag=True, default=False)
@click.pass_obj
def update(mud: MassUpdateData, commit: bool):
    """Updates pyproject.toml from the configuration"""
    if not commit:
        mud.pyupdater.dry_run = True
    else:
        mud.pyupdater.dry_run = False
        mud.pyupdater.git_commit = True

    for repo in mud.repo_dirs():
        mud.pyupdater.update_pyproject_toml(repo.path / "pyproject.toml")
        print()


@pyproject.command()
@click.argument("project")
@click.pass_obj
def reset_origin(mud: MassUpdateData, project: str):
    for meta in mud.iter_repos_meta():
        if meta.name == project:
            meta.repo.reset_origin("main")


# @version.command()
# @click.option("--doit", default=False)
# @click.pass_obj
# def updatecfg(mud: MassUpdateData, doit: bool):

#     changed = False
#     rawcfg = mud.rawcfg

#     for (repo, name, actual_version, desired_version) in mud.iter_repos_meta():
#         if actual_version != desired_version:
#             rawcfg["versions"][name] = actual_version
#             changed = True

#     if doit and changed:
#         pass
#     elif changed:
#         print(tomlkit.dumps(rawcfg))


# select the package set we're going to update
# - already did this in update_requirements.py, use that code

# dry run it first and show the results
# - check for dirty, fail
# - check for main, fail
# - check set-version vs git-describe, fail if lower

# for each repo
# verify versions
# - update version, push it
# push it --ff-only
# - fail if not successful
# tag it
# push the tag
# - fail if not successful
# poll pypi for release (10s poll?)
# - curl --head https://pypi.org/pypi/wpilib/2022.2.1.0/json\

# TODO: sanity check versions against set-version


def does_pypi_release_exist(pkgname: str, version: str):
    url = f"https://pypi.org/pypi/{pkgname}/{version}/json"
    req = requests.head(url)
    return req.status_code == 200, req.status_code


def wait_for_pypi_version(pkgname: str, version: str):
    url = f"https://pypi.org/pypi/{pkgname}/{version}/json"
    sleeptime = 90

    while True:
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"{now}: checking {url}...")
        ok, status_code = does_pypi_release_exist(pkgname, version)
        print("... ", status_code)
        if ok:
            # give pypi a minute to sync otherwise the next job will fail
            time.sleep(90)
            break

        time.sleep(sleeptime)
        if sleeptime > 20:
            sleeptime -= 10


@main.command()
@click.option("--doit", is_flag=True, default=False)
@click.pass_obj
def autopush(mud: MassUpdateData, doit: bool):
    """
    Publish each repo that has outstanding changes.

    Will wait for package to be published to pypi before publishing the
    next repo.
    """

    toexec: typing.List[IterMeta] = []

    # sanity checks first
    for meta in mud.iter_repos_meta():
        if meta.actual_version == meta.desired_version:
            ok, _ = does_pypi_release_exist(meta.name, meta.desired_version)
            if ok:
                continue

            print(meta.name, "not on PyPI, will try again")
            toexec.append(meta)
            continue

        if Version(meta.actual_version) > Version(meta.desired_version):
            raise click.ClickException(
                f"{meta.name}: desired version ({meta.desired_version}) < actual ({meta.actual_version})"
            )

        verb = "Will" if doit else "Would"
        print(
            verb,
            "upgrade",
            meta.name,
            "\n",
            meta.actual_version,
            "->",
            meta.desired_version,
        )

        if meta.repo.is_file_dirty("."):
            raise click.ClickException(f"{meta.name}: repo has outstanding changes")

        branch = meta.repo.get_current_branch()
        if branch != "main":
            raise click.ClickException(
                f"{meta.name}: branch is not 'main', is '{branch}'"
            )

        toexec.append(meta)

    if not toexec:
        print("Nothing to do")
        return
    elif not doit:
        print("Would: ", ",".join([meta.name for meta in toexec]))
        return

    for meta in toexec:

        # TODO: must roll back tag on failure
        if meta.actual_version != meta.desired_version:
            print("Updating", meta.name, "to", meta.desired_version)
            meta.repo.make_tag(meta.desired_version)
        else:
            print("Processing", meta.name, meta.desired_version)

        meta.repo.push()
        meta.repo.push_tag(meta.desired_version)

        ok, _ = does_pypi_release_exist(meta.name, meta.desired_version)
        if not ok:
            wait_for_pypi_version(meta.name, meta.desired_version)


@main.command()
@click.argument("pkgname")
@click.argument("version")
def pypi_wait(pkgname: str, version: str):
    wait_for_pypi_version(pkgname, version)


if __name__ == "__main__":
    main()
