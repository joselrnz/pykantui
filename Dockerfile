# Ubuntu rather than a python:slim image, on purpose.
#
# The point of this container is to find the things that only break away from
# the machine it was written on: a path separator assumed to be a backslash, a
# console encoding that happened to be cp1252, a filename that Windows accepted
# and Linux does not. A stock Ubuntu with the distro's own Python is much closer
# to where this will actually run than a curated Python image is.
FROM ubuntu:24.04

# Fail the build on a failing pipe stage rather than silently continuing.
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        python3-venv \
        git \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

# A virtualenv even in a container: Ubuntu 24.04 marks its system Python as
# externally managed (PEP 668), and working around that with --break-system-
# packages is exactly the sort of thing that behaves differently here than on a
# developer's machine.
ENV VIRTUAL_ENV=/opt/venv
RUN python3 -m venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

WORKDIR /app

# Dependencies first, so editing source does not reinstall the world.
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir -e ".[dev]" || pip install --no-cache-dir -e .

COPY tests/ ./tests/
COPY tools/ ./tools/
COPY docs/ ./docs/
COPY .env.example ./
COPY docker-compose.yml ./

# Not root: a bug that only shows up with restricted permissions is one worth
# finding here rather than on somebody's server. Also proves the app never
# needs to write outside its own directories.
RUN useradd --create-home --shell /bin/bash kbn \
    && chown -R kbn:kbn /app "$VIRTUAL_ENV"
USER kbn

# Advertise terminal capabilities without pinning its geometry. Python's
# ``shutil.get_terminal_size`` checks COLUMNS/LINES before the PTY, so setting
# either here prevents Textual from following a resized Windows Terminal.
ENV TERM=xterm-256color

CMD ["python", "-m", "unittest", "discover", "-s", "tests", "-v"]
