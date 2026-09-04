# The siting agent's web interview.
#
# Built and run on the operator's own machine, against the operator's own
# Anthropic key. Nothing here is specific to a hosting provider.
#
#     docker compose up
#
# `cache/` is deliberately not copied in. It is bind-mounted by compose.yaml, so
# that a 580 MB raster is not rebuilt into the image on every code change, and
# so that data fetched during a run persists after the container exits.

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive

# Runtime system packages, and Node.
#
#   libgomp1   scipy and scikit-image link OpenMP; the wheels do not bundle it
#   libexpat1  PROJ's data reader, reached through pyproj
#   git, curl  every source fetch is https, and NodeSource's installer needs curl
#   nodejs     the Claude Agent SDK conducts an interview by spawning the Claude
#              Code CLI. With SITING_WEB_ENGINE=sdk and no Node, the interview
#              cannot start at all.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl git libgomp1 libexpat1 \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @anthropic-ai/claude-code \
    && npm cache clean --force \
    && rm -rf /var/lib/apt/lists/*

# uid 1000 so that bind-mounted directories written during a run belong to the
# operator on the host rather than to root.
RUN useradd -m -u 1000 user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH
WORKDIR /app

# Dependencies. build-essential is installed and purged inside one layer so the
# compilers do not survive into the image, while a package that ships no cp312
# wheel can still build.
#
# GOSTnetsraster, named in requirements.txt as an install-separately, is not
# installed and is not needed: siting/sources/friction.py reaches for
# skimage.graph.MCP_Geometric directly.
COPY --chown=user:user requirements.txt ./
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && pip install --no-cache-dir -r requirements.txt \
    && apt-get purge -y build-essential && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

COPY --chown=user:user . .

# Written to during a run. `cache/` is created empty: compose mounts over it,
# and a district outside the warmed countries fetches into it at run time.
RUN mkdir -p runs sessions harness/logs cache \
    && chmod +x harness/hooks/*.sh \
    && chown -R user:user runs sessions harness cache

USER user

# The SDK engine delegates to data-scout, spatial-analyst, plan-reviewer and
# map-reviewer for real. Set SITING_WEB_ENGINE=legacy to fall back to the raw
# Messages API, which needs no Node and no subagents.
ENV SITING_WEB_ENGINE=sdk

EXPOSE 7860

# --timeout-keep-alive is raised because a run is streamed over SSE and the
# stages between two log lines can be minutes apart.
CMD ["uvicorn", "web.app:app", "--host", "0.0.0.0", "--port", "7860", \
     "--timeout-keep-alive", "300"]
