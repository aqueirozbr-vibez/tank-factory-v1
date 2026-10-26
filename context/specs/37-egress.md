# Unit 37: Egress enforcement

## Goal

A task container reaches the model proxy on port 4000 and nothing else;
the rules live on the host, not in the container; every denial on the VM
is an `egress_denied` event with the container, the destination and the
task (PRD §Capability 3 · Limits; TRD §3.2). On this Docker Desktop host
the enforcement point is an internal Docker network (user decision,
2026-10-24): the nftables rule set and the denial tailer ship for the
Linux VM.

## Design

Terminal only. `factory proxy up` creates the `factory-tasks` network with
`--internal` and joins the LiteLLM container to it under the alias
`proxy`; task containers are created on that network and address the
proxy as `http://proxy:4000`. On the VM the installer additionally renders
`deploy/egress/factory-tasks.nft` into the `DOCKER-USER` chain and the
orchestrator tails the kernel log for `factory-egress` denials.

## Implementation

### orchestrator/workspace/container.py

- `DockerCli.network_inspect`, `network_create(internal=True)`,
  `task_containers_by_ip(network)`; `ensure_network(docker, name)` creates
  the network when missing and refuses one that exists without
  `--internal`.

### orchestrator/watchdogs.py

- `nftables_rules(bridge, proxy_ip, port)`: the `inet factory` table with a
  `DOCKER-USER`-style forward chain: established and related accepted,
  the proxy on the port accepted, everything else from the bridge logged
  with prefix `factory-egress ` and dropped.
- `parse_denial(line)` reads the kernel log line's `SRC`, `DST`, `PROTO`,
  `DPT`; `EgressTailer.feed(line)` resolves the source IP to the task's
  container through the docker network and writes `egress_denied` with
  container, destination, port, protocol and the raw line; `journal_lines`
  streams `journalctl -k -f` on the VM.

### deploy/egress/

- `factory-tasks.nft.example` rendered by `nftables_rules` with example
  values and a README with the VM steps; unit 44 wires them into the
  installer.

### Settings, keys, proxy

- `Settings.docker_network` defaults to `factory-tasks`;
  `Settings.proxy_url_in_tasks` (default `http://proxy:4000`) is the URL a
  container uses; `PhaseKeys(task_proxy_url=...)` puts it in the agent
  environment while the admin keeps the host URL.
- `docker-compose.proxy.yml` joins `litellm` to the external
  `factory-tasks` network with the alias `proxy`.

## Dependencies

- Docker Desktop (installed). Denials are not kernel-logged on this host:
  a connection from a task container simply fails; `egress_denied` rows
  come from the VM's tailer, and the hook denials of unit 38 record the
  attempt as a `tool_call` row.

## Verify when done

- [ ] `nftables_rules` output names the bridge, accepts established and the
      proxy port, logs with the prefix and drops the rest
- [ ] `parse_denial` reads a kernel log line; the tailer writes
      `egress_denied` with the task resolved from the source IP and records
      an unknown source without a task
- [ ] `ensure_network` creates `--internal` and refuses a non-internal
      network of the same name; the compose file joins the proxy with the
      alias; the agent env inside a container points at `http://proxy:4000`
- [ ] Opt-in on Docker: from a container on the internal network, GitHub
      and PyPI are unreachable while a container aliased `proxy` on the same
      network answers
- [ ] The four quality commands pass
