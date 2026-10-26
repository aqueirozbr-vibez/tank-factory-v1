# Task container egress (TRD §3.2)

Two layers:

1. The `factory-tasks` Docker network is created with `--internal`: it has
   no route out of the host. The LiteLLM container joins it under the alias
   `proxy`, so a task container can reach `http://proxy:4000` and nothing
   else. `factory proxy up` creates the network and starts the proxy on it.
   This is the only layer on Docker Desktop (Windows and macOS), where the
   engine runs inside its own VM and the host has no nftables chain.

2. On a Linux VM the installer also renders `factory-tasks.nft` (see the
   example here) into the host's `DOCKER-USER` chain so that any packet
   leaving the bridge for anything but the proxy is logged with the prefix
   `factory-egress ` and dropped. The orchestrator tails the kernel log
   (`journalctl -k -f`) and writes each denial as an `events` row of kind
   `egress_denied` with the container, destination and task.

Rendering on the VM (unit 44 wires this into `factory install`):

```
BRIDGE=br-$(docker network inspect factory-tasks --format '{{slice .Id 0 12}}')
PROXY_IP=$(docker inspect litellm --format '{{(index .NetworkSettings.Networks "factory-tasks").IPAddress}}')
uv run python -c "from orchestrator.watchdogs import nftables_rules; print(nftables_rules('$BRIDGE', '$PROXY_IP'))" | sudo nft -f -
```
