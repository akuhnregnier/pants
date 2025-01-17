# Copyright 2022 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import annotations

import os
from typing import Any, Iterable

from pants.backend.helm.resolve.remotes import HelmRemotes
from pants.backend.helm.target_types import HelmChartTarget, HelmRegistriesField
from pants.core.util_rules.external_tool import TemplatedExternalTool
from pants.engine.platform import Platform
from pants.option.option_types import (
    ArgsListOption,
    BoolOption,
    DictOption,
    StrListOption,
    StrOption,
)
from pants.util.memo import memoized_method
from pants.util.strutil import bullet_list, help_text, softwrap

_VALID_PASSTHROUGH_FLAGS = [
    "--atomic",
    "--cleanup-on-fail",
    "--create-namespace",
    "--debug",
    "--dry-run",
    "--force",
    "--wait",
    "--wait-for-jobs",
]

_VALID_PASSTHROUGH_OPTS = [
    "--kubeconfig",
    "--kube-context",
    "--kube-apiserver",
    "--kube-as-group",
    "--kube-as-user",
    "--kube-ca-file",
    "--kube-token",
]


class InvalidHelmPassthroughArgs(Exception):
    def __init__(self, args: Iterable[str], *, extra_help: str = "") -> None:
        super().__init__(
            softwrap(
                f"""
                The following command line arguments are not valid: {' '.join(args)}.

                Only the following passthrough arguments are allowed:

                {bullet_list([*_VALID_PASSTHROUGH_FLAGS, *_VALID_PASSTHROUGH_OPTS])}

                {extra_help}
                """
            )
        )


registries_help = help_text(
    f"""
    Configure Helm OCI registries. The schema for a registry entry is as follows:

        {{
            "registry-alias": {{
                "address": "oci://registry-domain:port",
                "default": bool,
            }},
            ...
        }}

    If no registries are provided in either a `{HelmChartTarget.alias}` target, then all default
    addresses will be used, if any.

    The `{HelmChartTarget.alias}.{HelmRegistriesField.alias}` may be provided with a list of registry
    addresses and registry alias prefixed with `@` to be used instead of the defaults.

    A configured registry is marked as default either by setting `default = true`
    or with an alias of `"default"`.

    Registries also participate in resolving third party Helm charts uplodaded to those registries.
    """
)


class HelmSubsystem(TemplatedExternalTool):
    options_scope = "helm"
    help = "The Helm command line (https://helm.sh)"

    default_version = "3.11.1"
    default_known_versions = [
        "3.11.1|linux_arm64 |919173e8fb7a3b54d76af9feb92e49e86d5a80c5185020bae8c393fa0f0de1e8|13484900",
        "3.11.1|linux_x86_64|0b1be96b66fab4770526f136f5f1a385a47c41923d33aab0dcb500e0f6c1bf7c|15023104",
        "3.11.1|macos_arm64 |43d0198a7a2ea2639caafa81bb0596c97bee2d4e40df50b36202343eb4d5c46b|14934852",
        "3.11.1|macos_x86_64|2548a90e5cc957ccc5016b47060665a9d2cd4d5b4d61dcc32f5de3144d103826|15757902",
        "3.10.0|linux_arm64 |3b72f5f8a60772fb156d0a4ab93272e8da7ef4d18e6421a7020d7c019f521fc1|13055719",
        "3.10.0|linux_x86_64|bf56beb418bb529b5e0d6d43d56654c5a03f89c98400b409d1013a33d9586474|14530566",
        "3.10.0|macos_arm64 |f7f6558ebc8211824032a7fdcf0d55ad064cb33ec1eeec3d18057b9fe2e04dbe|14446277",
        "3.10.0|macos_x86_64|1e7fd528482ac2ef2d79fe300724b3e07ff6f846a2a9b0b0fe6f5fa05691786b|15237557",
        "3.8.0|linux_arm64 |23e08035dc0106fe4e0bd85800fd795b2b9ecd9f32187aa16c49b0a917105161|12324642",
        "3.8.0|linux_x86_64|8408c91e846c5b9ba15eb6b1a5a79fc22dd4d33ac6ea63388e5698d1b2320c8b|13626774",
        "3.8.0|macos_arm64 |751348f1a4a876ffe089fd68df6aea310fd05fe3b163ab76aa62632e327122f3|14078604",
        "3.8.0|macos_x86_64|532ddd6213891084873e5c2dcafa577f425ca662a6594a3389e288fc48dc2089|14318316",
    ]
    default_url_template = "https://get.helm.sh/helm-v{version}-{platform}.tar.gz"
    default_url_platform_mapping = {
        "linux_arm64": "linux-arm64",
        "linux_x86_64": "linux-amd64",
        "macos_arm64": "darwin-arm64",
        "macos_x86_64": "darwin-amd64",
    }

    _registries = DictOption[Any](help=registries_help, fromfile=True)
    lint_strict = BoolOption(default=False, help="Enables strict linting of Helm charts")
    default_registry_repository = StrOption(
        default=None,
        help=softwrap(
            """
            Default location where to push Helm charts in the available registries
            when no specific one has been given.

            If no registry repository is given, charts will be pushed to the root of
            the OCI registry.
            """
        ),
    )
    extra_env_vars = StrListOption(
        help=softwrap(
            """
            Additional environment variables that would be made available to all Helm processes
            or during value interpolation.
            """
        ),
        advanced=True,
    )
    tailor = BoolOption(
        default=True,
        help="If true, add `helm_chart` targets with the `tailor` goal.",
        advanced=True,
        removal_hint="Use `[helm].tailor_charts` instead.",
        removal_version="2.19.0.dev0",
    )
    tailor_charts = BoolOption(
        default=None,
        help="If true, add `helm_chart` targets with the `tailor` goal.",
        advanced=True,
    )
    tailor_unittests = BoolOption(
        default=True,
        help="If true, add `helm_unittest_tests` targets with the `tailor` goal.",
        advanced=True,
    )

    args = ArgsListOption(
        example="--dry-run",
        passthrough=True,
        extra_help=softwrap(
            f"""
            Additional arguments to pass to Helm command line.

            Only a subset of Helm arguments are considered valid as passthrough arguments as most of them
            have equivalents in the form of fields of the different target types.

            The list of valid arguments is as follows:

            {bullet_list([*_VALID_PASSTHROUGH_FLAGS, *_VALID_PASSTHROUGH_OPTS])}

            Before attempting to use passthrough arguments, check the reference of each of the available target types
            to see what fields are accepted in each of them.
            """
        ),
    )

    @memoized_method
    def valid_args(self, *, extra_help: str = "") -> tuple[str, ...]:
        valid, invalid = _cleanup_passthrough_args(self.args)
        if invalid:
            raise InvalidHelmPassthroughArgs(invalid, extra_help=extra_help)
        return tuple(valid)

    def generate_exe(self, plat: Platform) -> str:
        mapped_plat = self.default_url_platform_mapping[plat.value]
        bin_path = os.path.join(mapped_plat, "helm")
        return bin_path

    @memoized_method
    def remotes(self) -> HelmRemotes:
        return HelmRemotes.from_dict(self._registries)


def _cleanup_passthrough_args(args: Iterable[str]) -> tuple[list[str], list[str]]:
    valid_args: list[str] = []
    removed_args: list[str] = []

    skip = False
    for arg in args:
        if skip:
            valid_args.append(arg)
            skip = False
            continue

        if arg in _VALID_PASSTHROUGH_FLAGS:
            valid_args.append(arg)
        elif "=" in arg and arg.split("=")[0] in _VALID_PASSTHROUGH_OPTS:
            valid_args.append(arg)
        elif arg in _VALID_PASSTHROUGH_OPTS:
            valid_args.append(arg)
            skip = True
        else:
            removed_args.append(arg)

    return (valid_args, removed_args)
