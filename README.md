devtools
========

The contents of this repo are tools that RobotPy maintainers can use to manage
updating core RobotPy projects.

auto-release workflow (can only be done by developers with push access)
-----------------------------------------------------------------------

### Initial setup

```
$ ./mud.py repo clone
```

### Every release

First make sure all repos are up to date:

```
$ ./mud.py repo ensure
```

Then update your cfg.toml with all current versions. If the output looks
right, execute it again with `--doit`.

```
$ ./mud.py project updatecfg
```

Next, update `cfg.toml` with all the versions you want. To see what changes
would be made:

```
$ ./mud.py project update
```

If the output seems right, then execute it with `--commit`. Then go to each repo
to check that it looks right (or not once you've gotten the hang of it).

Next, time to actually do the push. First run this to see if it seems right:

```
$ ./mud.py autopush
```

If that seems right, then execute it again with `--doit`.
