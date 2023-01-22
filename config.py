import pydantic
import tomlkit
import typing


class Model(pydantic.BaseModel):
    class Config:
        extra = "forbid"


class Parameters(Model):
    #: every managed package has this
    max_version: str

    #: any package in 'wpilib_packages' will have their maven downloads
    #: updated to this version / URL
    wpilib_bin_version: str
    wpilib_bin_url: str
    wpilib_packages: typing.List[str]
    vendor_packages: typing.List[str]

    # robotpy-meta project
    meta_package: str

    #: list of repos that we are managing
    repos: typing.List[str]


class UpdateConfig(Model):

    #: These are what versions each pypi package should currently be
    versions: typing.Dict[str, str]

    #: If any managed project has one of these as a dependency, the
    #: minimum version should be this
    min_versions: typing.Dict[str, str]

    params: Parameters


def load(fname) -> typing.Tuple[UpdateConfig, tomlkit.TOMLDocument]:
    with open(fname) as fp:
        cfgdata = tomlkit.parse(fp.read())

    return UpdateConfig(**cfgdata), cfgdata
