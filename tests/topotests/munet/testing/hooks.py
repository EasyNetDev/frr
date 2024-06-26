# -*- coding: utf-8 eval: (blacken-mode 1) -*-
# SPDX-License-Identifier: GPL-2.0-or-later
#
# April 22 2022, Christian Hopps <chopps@gmail.com>
#
# Copyright (c) 2022, LabN Consulting, L.L.C
#
"""A module that implements pytest hooks.

To use in your project, in your conftest.py add:

  from munet.testing.hooks import *
"""
import logging
import os
import sys
import traceback

import pytest

from ..args import add_testing_args
from ..base import BaseMunet  # pylint: disable=import-error
from ..cli import cli  # pylint: disable=import-error
from .util import pause_test


# ===================
# Hooks (non-fixture)
# ===================


def pytest_addoption(parser):
    add_testing_args(parser.addoption)


def pytest_configure(config):
    if "PYTEST_XDIST_WORKER" not in os.environ:
        os.environ["PYTEST_XDIST_MODE"] = config.getoption("dist", "no")
        os.environ["PYTEST_IS_WORKER"] = ""
        is_xdist = os.environ["PYTEST_XDIST_MODE"] != "no"
        is_worker = False
    else:
        os.environ["PYTEST_IS_WORKER"] = os.environ["PYTEST_XDIST_WORKER"]
        is_xdist = True
        is_worker = True

    # Turn on live logging if user specified verbose and the config has a CLI level set
    if config.getoption("--verbose") and not is_xdist and not config.getini("log_cli"):
        if config.getoption("--log-cli-level", None) is None:
            # By setting the CLI option to the ini value it enables log_cli=1
            cli_level = config.getini("log_cli_level")
            if cli_level is not None:
                config.option.log_cli_level = cli_level

    have_tmux = bool(os.getenv("TMUX", ""))
    have_screen = not have_tmux and bool(os.getenv("STY", ""))
    have_xterm = not have_tmux and not have_screen and bool(os.getenv("DISPLAY", ""))
    have_windows = have_tmux or have_screen or have_xterm
    have_windows_pause = have_tmux or have_xterm
    xdist_no_windows = is_xdist and not is_worker and not have_windows_pause

    for winopt in ["--shell", "--stdout", "--stderr"]:
        b = config.getoption(winopt)
        if b and xdist_no_windows:
            pytest.exit(
                f"{winopt} use requires byobu/TMUX/XTerm "
                f"under dist {os.environ['PYTEST_XDIST_MODE']}"
            )
        elif b and not is_xdist and not have_windows:
            pytest.exit(f"{winopt} use requires byobu/TMUX/SCREEN/XTerm")

    cli_pause = (
        config.getoption("--cli-on-error")
        or config.getoption("--pause")
        or config.getoption("--pause-at-end")
        or config.getoption("--pause-on-error")
    )
    if config.getoption("--capture") == "fd" and cli_pause:
        pytest.exit(
            "CLI is not compatible with `--capture=fd`, "
            "please run again with `-s` or other `--capture` value"
        )


def pytest_runtest_makereport(item, call):
    """Pause or invoke CLI as directed by config."""
    isatty = sys.stdout.isatty()

    pause = bool(item.config.getoption("--pause"))
    skipped = False

    if call.excinfo is None:
        error = False
    elif call.excinfo.typename == "Skipped":
        skipped = True
        error = False
        pause = False
    else:
        error = True
        modname = item.parent.module.__name__
        exval = call.excinfo.value
        logging.error(
            "test %s/%s failed: %s: stdout: '%s' stderr: '%s'",
            modname,
            item.name,
            exval,
            exval.stdout if hasattr(exval, "stdout") else "NA",
            exval.stderr if hasattr(exval, "stderr") else "NA",
        )
        if not pause:
            pause = item.config.getoption("--pause-on-error")

    if error and isatty and item.config.getoption("--cli-on-error"):
        if not BaseMunet.g_unet:
            logging.error("Could not launch CLI b/c no munet exists yet")
        else:
            print(f"\nCLI-ON-ERROR: {call.excinfo.typename}")
            print(f"CLI-ON-ERROR:\ntest {modname}/{item.name} failed: {exval}")
            if hasattr(exval, "stdout") and exval.stdout:
                print("stdout: " + exval.stdout.replace("\n", "\nstdout: "))
            if hasattr(exval, "stderr") and exval.stderr:
                print("stderr: " + exval.stderr.replace("\n", "\nstderr: "))
            cli(BaseMunet.g_unet)

    if pause:
        if skipped:
            item.skip_more_pause = True
        elif hasattr(item, "skip_more_pause"):
            pass
        elif call.when == "setup":
            if error:
                item.skip_more_pause = True

            # we can't asyncio.run() (which pause does) if we are not unhsare_inline
            # at this point, count on an autouse fixture to pause instead in this
            # case
            if BaseMunet.g_unet and BaseMunet.g_unet.unshare_inline:
                pause_test(f"before test '{item.nodeid}'")

        # check for a result to try and catch setup (or module setup) failure
        # e.g., after a module level fixture fails, we do not want to pause on every
        # skipped test.
        elif call.when == "teardown" and call.excinfo:
            logging.warning(
                "Caught exception during teardown: %s\n:Traceback:\n%s",
                call.excinfo,
                "".join(traceback.format_tb(call.excinfo.tb)),
            )
            pause_test(f"after teardown after test '{item.nodeid}'")
        elif call.when == "teardown" and call.result:
            pause_test(f"after test '{item.nodeid}'")
        elif error:
            item.skip_more_pause = True
            print(f"\nPAUSE-ON-ERROR: {call.excinfo.typename}")
            print(f"PAUSE-ON-ERROR:\ntest {modname}/{item.name} failed: {exval}")
            if hasattr(exval, "stdout") and exval.stdout:
                print("stdout: " + exval.stdout.replace("\n", "\nstdout: "))
            if hasattr(exval, "stderr") and exval.stderr:
                print("stderr: " + exval.stderr.replace("\n", "\nstderr: "))
            pause_test(f"PAUSE-ON-ERROR: '{item.nodeid}'")
