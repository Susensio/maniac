```markdown
% FAKETOOL(1) | User Commands

# NAME

faketool - build and serve fake projects

# SYNOPSIS

**faketool** [*options*] *command* [*args*]

# DESCRIPTION

**faketool** builds a *Project* into an *Artifact* and serves it locally.
Flags such as **--release** change how the artifact is built; a lone **-** reads the project from standard input.

**Project**
:   A directory holding a `fake.toml` manifest.

**Artifact**
:   The build output, written under *target/* (default: *./target*).

# COMMANDS

## Building

**faketool build** [**--release**] [*path*]
:   Build the project at *path*.

    **--release**
    :   Optimise the build and strip debug symbols.

    **--jobs** *n*, **-j** *n*
    :   Run *n* jobs in parallel (default: *4*).

## Serving

**faketool serve** [**--port** *port*]
:   Serve the last build.

    **--port** *port*
    :   Listen on *port* (default: *8080*).

    **--format** *format*
    :   Log format: *format* = *text* | *json*.

# OPTIONS

**-v**, **--verbose**
:   Print every step.

**--color**=*when*
:   Colour output: *when* = *auto* | *always* | *never*.

**--version**
:   Print the version and exit.

**-h**, **--help**
:   Print help and exit.

# EXAMPLES

Build a release and serve it on another port:

```
faketool build --release ./site
faketool serve --port 9000 --format json
```

Read the project from standard input:

```
cat fake.toml | faketool build -
```

# EXIT STATUS

**0**
:   Success.

**2**
:   Invalid arguments.

# ENVIRONMENT

**FAKETOOL_HOME**
:   Overrides the configuration directory (default: *~/.config/faketool*).

# SEE ALSO

**make**(1), **python3**(1)

<https://example.invalid/faketool>
```
