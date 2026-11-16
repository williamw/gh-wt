FROM ghcr.io/prefix-dev/pixi:0.84.2
RUN apt-get update && apt-get install -y --no-install-recommends git curl ca-certificates && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL https://github.com/github/gh-stack/releases/download/v0.3.0/linux-amd64 -o /usr/local/bin/gh-stack && echo '957e822cfa7e435cd84a297cd96c419b58e51e3ac58be249762c24bd9e45099b  /usr/local/bin/gh-stack' | sha256sum -c - && chmod +x /usr/local/bin/gh-stack
COPY .devcontainer/gh /usr/local/bin/gh
ENV GH_STACK_NO_UPDATE_NOTIFIER=1 GH_NO_UPDATE_NOTIFIER=1 GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null GIT_TERMINAL_PROMPT=0 GIT_EDITOR=true
WORKDIR /opt/gh-wt
COPY pixi.toml pixi.lock ./
RUN pixi install --locked
ENV PATH="/opt/gh-wt/.pixi/envs/default/bin:${PATH}"
WORKDIR /workspace
