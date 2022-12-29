#!/usr/bin/env python3
#
# Given a vendor json file, spit out a reasonable robotpy-build pyproject.toml
#

import argparse
import dataclasses
import json
import os
import pathlib
import re
import shutil
import sys
import typing

from pkg_resources import iter_entry_points

import tomli_w


from robotpy_build.download import download_and_extract_zip
from robotpy_build.maven import _get_artifact_url
from robotpy_build.platforms import get_platform
from robotpy_build.pyproject_configs import MavenLibDownload
from robotpy_build.pkgcfg_provider import PkgCfg, PkgCfgProvider


validation_re = re.compile(r"[A-Za-z0-9\._-]+")

platmap = {
    "windowsx86-64": get_platform("win-amd64"),
    "linuxx86-64": get_platform("linux-x86_64"),
    "linuxathena": get_platform("linux-athena"),
    "osxuniversal": get_platform("macos-universal"),
}


def is_ok_fname(s) -> bool:
    return validation_re.match(s) and s != ".."


# from https://gist.github.com/u0pattern/0e9ac6cc6a51ca9551867ee42619dd40
def ldd_elf(fname: str):
    from elftools.elf.elffile import ELFFile

    with open(fname, "rb") as f:
        elffile = ELFFile(f)
        # iter_segments implementation :
        # https://github.com/eliben/pyelftools/blob/master/elftools/elf/elffile.py#L171
        for segment in elffile.iter_segments():
            # PT_DYNAMIC section
            # https://docs.oracle.com/cd/E19683-01/817-3677/chapter6-42444/index.html
            if segment.header.p_type == "PT_DYNAMIC":
                # iter_tags implementation :
                # https://github.com/eliben/pyelftools/blob/master/elftools/elf/dynamic.py#L144-L160
                for t in segment.iter_tags():
                    # DT_NEEDED section
                    # https://docs.oracle.com/cd/E19683-01/817-3677/6mj8mbtbe/index.html
                    if t.entry.d_tag == "DT_NEEDED":
                        yield t.needed


def ldd_dll(fname: str):
    import pefile

    pe = pefile.PE(fname)
    for entry in pe.DIRECTORY_ENTRY_IMPORT:
        yield entry.dll.decode("utf-8")


def ldd_dylib(fname: str):
    import delocate.tools

    return delocate.tools.get_install_names(fname)


def ldd(fname: pathlib.Path):
    if fname.suffix == ".so":
        yield from ldd_elf(fname)
    elif fname.suffix == ".dylib":
        yield from ldd_dylib(fname)
    elif fname.suffix == ".dll":
        yield from ldd_dll(fname)
    else:
        raise ValueError(f"unknown file extension {fname}")


@dataclasses.dataclass
class LibData:
    repo_url: str
    artifact_id: str
    group_id: str
    version: str
    lib_name: str
    pypkg: str
    sim: typing.Optional[str]
    platforms: typing.Dict[str, typing.List[str]]


def get_deps(pd: typing.List[str], internals, externals):
    deps = []
    for dep in pd:
        iname = internals.get(dep)
        ename = externals.get(dep)
        if iname:
            deps.append(iname)
        elif ename:
            deps.append(ename)
    return list(reversed(deps))


def make_wrapper(
    wrapper,
    name,
    athena_plat,
    sim_plat,
    athena_deps: typing.List[str],
    sim_deps: typing.List[str],
    athena_pkgs: typing.List[str],
    sim_pkgs: typing.List[str],
    wpilib_pkgs: typing.List[str],
    internals,
):
    if athena_plat and not sim_plat:
        athena_pkgs.append(name)
        wrapper["ignore"] = True
        override = {"ignore": False}
        wrapper["override"] = {"arch_athena": override}
        if athena_deps:
            override["depends"] = athena_deps
    elif not athena_plat and sim_plat:
        sim_pkgs.append(name)
        wrapper["override"] = {"arch_athena": {"ignore": True}}
        if sim_deps:
            wrapper["depends"] = sim_deps
    else:
        if athena_deps or sim_deps:
            if set(athena_deps) == set(sim_deps):
                wrapper["depends"] = athena_deps
            else:
                if sim_deps:
                    wrapper["depends"] = sim_deps
                if athena_deps:
                    wrapper["override"] = {"arch_athena": {"depends": athena_deps}}

        athena_pkgs.append(name)
        sim_pkgs.append(name)

    for deps in (athena_deps, sim_deps):
        if not deps:
            continue
        for dep in deps:
            if dep not in internals and dep not in wpilib_pkgs:
                wpilib_pkgs.append(dep)


root = pathlib.Path(__file__).parent.absolute()
cache = root / "vendorcache"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("vendor_json")
    parser.add_argument("package")
    args = parser.parse_args()

    try:
        with open(args.vendor_json) as fp:
            data = json.load(fp)
    except:
        from urllib.request import urlopen
        with urlopen(args.vendor_json) as fp:
            data = json.load(fp)

    maven_url = data["mavenUrls"][0].rstrip("/")
    # .. version doesn't matter

    our_libs: typing.Dict[str, LibData] = {}
    internals = {}
    internals_set = set()

    for dep in data["cppDependencies"]:
        # construct the URL

        libName = dep["libName"]
        artifact_id = dep["artifactId"].lower().replace("-", "_")
        pkgname = f"{args.package}_{artifact_id}"
        pypkg = f"{args.package}._{artifact_id}"

        assert pkgname not in our_libs, pkgname

        ld = LibData(
            repo_url=maven_url,
            artifact_id=dep["artifactId"],
            group_id=dep["groupId"],
            version=dep["version"],
            lib_name=libName,
            sim=dep.get("simMode"),
            pypkg=pypkg,
            platforms={},
        )

        our_libs[pkgname] = ld
        internals_set.add(pkgname)

        # validate the pieces
        for piece in (ld.artifact_id, ld.group_id, ld.version):
            if not is_ok_fname(piece):
                raise ValueError(piece)

        unzip_to = cache / f"{ld.group_id}.{ld.artifact_id}"
        if unzip_to.exists():
            shutil.rmtree(str(unzip_to))

        for platName in dep["binaryPlatforms"]:
            if platName not in ["linuxx86-64", "linuxathena"]:
                continue

            platform = platmap[platName]

            # download it, unzip it to the cache
            arch = platName[5:]
            url = _get_artifact_url(ld, platName)

            download_and_extract_zip(url, str(unzip_to), cache)

            # find the library, add its dependencies
            lib_fname = (
                unzip_to
                / platform.os
                / platform.arch
                / "shared"
                / f"{platform.libprefix}{libName}{platform.libext}"
            )

            internals[f"{platform.libprefix}{libName}{platform.libext}"] = pkgname

            ld.platforms[platName] = list(ldd(lib_fname))

    # populate all robotpy-build data here
    externals = {}
    for ep in iter_entry_points(group="robotpybuild", name=None):
        try:
            pkg = PkgCfg(ep)
        except:
            continue
        libs = pkg.get_library_full_names()
        if libs:
            for lib in libs:
                externals[lib] = pkg.name

    # Ok, now we have enough data to populate depends, and write it all out
    wrappers = {}
    t = {"tool": {"robotpy-build": {"wrappers": wrappers}}}

    wpilib_pkgs = []
    athena_pkgs = []
    sim_pkgs = []

    for name, data in our_libs.items():
        wrapper = dict(name=name)
        wrappers[data.pypkg] = wrapper

        athena_plat = data.platforms.get("linuxathena")
        sim_plat = data.platforms.get("linuxx86-64")
        if data.sim == "hwsim":
            sim_plat = None

        athena_deps = (
            get_deps(athena_plat, internals, externals) if athena_plat else None
        )
        sim_deps = get_deps(sim_plat, internals, externals) if sim_plat else None

        make_wrapper(
            wrapper,
            name,
            athena_plat,
            sim_plat,
            athena_deps,
            sim_deps,
            athena_pkgs,
            sim_pkgs,
            wpilib_pkgs,
            internals_set,
        )

        wrapper["maven_lib_download"] = {
            "artifact_id": data.artifact_id,
            "group_id": data.group_id,
            "repo_url": data.repo_url,
            "version": data.version,
            "libs": [data.lib_name],
        }

    # same business here
    wrapper = dict(name=args.package)
    wrappers[args.package] = wrapper

    if athena_pkgs:
        athena_pkgs = wpilib_pkgs + athena_pkgs
    if sim_pkgs:
        sim_pkgs = wpilib_pkgs + sim_pkgs

    make_wrapper(
        wrapper,
        args.package,
        True if athena_pkgs else False,
        True if sim_pkgs else False,
        athena_pkgs,
        sim_pkgs,
        [],
        [],
        [],
        internals_set,
    )

    print("#")
    print("# Autogenerated TOML with vendorjson.py")
    print("#")
    print()
    print(tomli_w.dumps(t))
    print("# End autogenerated TOML")

    for name, data in our_libs.items():
        d = data.pypkg.split(".")[-1]
        print("mkdir", d, file=sys.stderr)
        if os.name == "nt":
            print("New-Item", d + "/__init__.py", file=sys.stderr)
        else:
            print("touch", d + "/__init__.py", file=sys.stderr)
        print("git add -f", d + "/__init__.py", file=sys.stderr)


if __name__ == "__main__":
    main()
