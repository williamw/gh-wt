IMAGE := localhost/gh-wt-dev:local
RUN := podman run --rm --cpus=2 --memory=2g --network=none --security-opt label=disable -e PYTHONDONTWRITEBYTECODE=1 -v "$(CURDIR):/workspace:rw" -w /workspace $(IMAGE)

.PHONY: image lock check smoke shell
image:
	podman build --cpu-period=100000 --cpu-quota=200000 --memory=2g -t $(IMAGE) -f .devcontainer/Dockerfile .

# Regenerate both platform locks with Pixi, without installing tools on the host.
lock:
	podman run --rm --cpus=2 --memory=2g --security-opt label=disable -v "$(CURDIR):/workspace:rw" -w /workspace ghcr.io/prefix-dev/pixi:0.84.2 pixi lock

check: image
	$(RUN) python -m pytest test_gh_wt.py test_stack_worktrees.py -v -p no:cacheprovider

smoke: image
	$(RUN) sh -c 'python --version && pytest --version && gh stack --version && python -m py_compile test_gh_wt.py test_stack_worktrees.py'

shell: image
	$(RUN) bash
