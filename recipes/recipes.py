#!/usr/bin/env python

# Copyright 2017 The LUCI Authors. All rights reserved.
# Use of this source code is governed under the Apache License, Version 2.0
# that can be found in the LICENSE file.

"""Bootstrap script to clone and forward to the recipe engine tool.

*******************
** DO NOT MODIFY **
*******************

This is a copy of https://chromium.googlesource.com/infra/luci/recipes-py/+/master/recipes.py.
To fix bugs, fix in the googlesource repo then run the autoroller.
"""

import argparse
import json
import logging
import os
import random
import subprocess
import sys
import time
import urlparse

from collections import namedtuple

from cStringIO import StringIO

# The dependency entry for the recipe_engine in the client repo's recipes.cfg
#
# url (str) - the url to the engine repo we want to use.
# revision (str) - the git revision for the engine to get.
# branch (str) - the branch to fetch for the engine as an absolute ref (e.g.
#   refs/heads/master)
EngineDep = namedtuple('EngineDep',
                       'url revision branch')


class MalformedRecipesCfg(Exception):
  def __init__(self, msg, path):
    super(MalformedRecipesCfg, self).__init__('malformed recipes.cfg: %s: %r'
                                              % (msg, path))


def parse(repo_root, recipes_cfg_path):
  """Parse is a lightweight a recipes.cfg file parser.

  Args:
    repo_root (str) - native path to the root of the repo we're trying to run
      recipes for.
    recipes_cfg_path (str) - native path to the recipes.cfg file to process.

  Returns (as tuple):
    engine_dep (EngineDep|None): The recipe_engine dependency, or None, if the
      current repo IS the recipe_engine.
    recipes_path (str) - native path to where the recipes live inside of the
      current repo (i.e. the folder containing `recipes/` and/or
      `recipe_modules`)
  """
  with open(recipes_cfg_path, 'rU') as fh:
    pb = json.load(fh)

  try:
    if pb['api_version'] != 2:
      raise MalformedRecipesCfg('unknown version %d' % pb['api_version'],
                                recipes_cfg_path)

    # If we're running ./recipes.py from the recipe_engine repo itself, then
    # return None to signal that there's no EngineDep.
    if pb['project_id'] == 'recipe_engine':
      return None, pb.get('recipes_path', '')

    engine = pb['deps']['recipe_engine']

    if 'url' not in engine:
      raise MalformedRecipesCfg(
        'Required field "url" in dependency "recipe_engine" not found',
        recipes_cfg_path)

    engine.setdefault('revision', '')
    engine.setdefault('branch', 'refs/heads/master')
    recipes_path = pb.get('recipes_path', '')

    # TODO(iannucci): only support absolute refs
    if not engine['branch'].startswith('refs/'):
      engine['branch'] = 'refs/heads/' + engine['branch']

    recipes_path = os.path.join(
      repo_root, recipes_path.replace('/', os.path.sep))
    return EngineDep(**engine), recipes_path
  except KeyError as ex:
    raise MalformedRecipesCfg(ex.message, recipes_cfg_path)


_BAT = '.bat' if sys.platform.startswith(('win', 'cygwin')) else ''
GIT = 'git' + _BAT
VPYTHON = 'vpython' + _BAT


def _subprocess_call(argv, **kwargs):
  logging.info('Running %r', argv)
  return subprocess.call(argv, **kwargs)


def _git_check_call(argv, **kwargs):
  argv = [GIT]+argv
  logging.info('Running %r', argv)
  subprocess.check_call(argv, **kwargs)


def _git_output(argv, **kwargs):
  argv = [GIT]+argv
  logging.info('Running %r', argv)
  return subprocess.check_output(argv, **kwargs)


def parse_args(argv):
  """This extracts a subset of the arguments that this bootstrap script cares
  about. Currently this consists of:
    * an override for the recipe engine in the form of `-O recipe_engine=/path`
    * the --package option.
  """
  PREFIX = 'recipe_engine='

  p = argparse.ArgumentParser(add_help=False)
  p.add_argument('-O', '--project-override', action='append')
  p.add_argument('--package', type=os.path.abspath)
  args, _ = p.parse_known_args(argv)
  for override in args.project_override or ():
    if override.startswith(PREFIX):
      return override[len(PREFIX):], args.package
  return None, args.package


def checkout_engine(engine_path, repo_root, recipes_cfg_path):
  dep, recipes_path = parse(repo_root, recipes_cfg_path)
  if dep is None:
    # we're running from the engine repo already!
    return os.path.join(repo_root, recipes_path)

  url = dep.url

  if not engine_path and url.startswith('file://'):
    engine_path = urlparse.urlparse(url).path

  if not engine_path:
    revision = dep.revision
    branch = dep.branch

    # Ensure that we have the recipe engine cloned.
    engine_path = os.path.join(recipes_path, '.recipe_deps', 'recipe_engine')

    with open(os.devnull, 'w') as NUL:
      # Note: this logic mirrors the logic in recipe_engine/fetch.py
      _git_check_call(['init', engine_path], stdout=NUL)

      try:
        _git_check_call(['rev-parse', '--verify', '%s^{commit}' % revision],
                        cwd=engine_path, stdout=NUL, stderr=NUL)
      except subprocess.CalledProcessError:
        _git_check_call(['fetch', url, branch], cwd=engine_path, stdout=NUL,
                        stderr=NUL)

    try:
      _git_check_call(['diff', '--quiet', revision], cwd=engine_path)
    except subprocess.CalledProcessError:
      _git_check_call(['reset', '-q', '--hard', revision], cwd=engine_path)

  return engine_path


def main():
  if '--verbose' in sys.argv:
    logging.getLogger().setLevel(logging.INFO)

  args = sys.argv[1:]
  engine_override, recipes_cfg_path = parse_args(args)

  if recipes_cfg_path:
    # calculate repo_root from recipes_cfg_path
    repo_root = os.path.dirname(
      os.path.dirname(
        os.path.dirname(recipes_cfg_path)))
  else:
    # find repo_root with git and calculate recipes_cfg_path
    repo_root = (_git_output(
      ['rev-parse', '--show-toplevel'],
      cwd=os.path.abspath(os.path.dirname(__file__))).strip())
    repo_root = os.path.abspath(repo_root)
    recipes_cfg_path = os.path.join(repo_root, 'infra', 'config', 'recipes.cfg')
    args = ['--package', recipes_cfg_path] + args

  engine_path = checkout_engine(engine_override, repo_root, recipes_cfg_path)

  return _subprocess_call([
      VPYTHON, '-u',
      os.path.join(engine_path, 'recipe_engine', 'main.py')] + args)


if __name__ == '__main__':
  sys.exit(main())
