# Network State Architecture

## One source of truth

A single `network.yaml` declares the entire LAN — VLANs, subnets, port
assignments, 802.1x policies, and DHCP scopes. Two renderers target
different devices from the same data:

```
network.yaml
     │
     ├── Brocade renderer ──> web API / SSH commands
     │
     └── NixOS renderer  ──> dhcpd.conf, freeradius, nftables
```

## Schema

```yaml
# ── VLANs ──────────────────────────────────────────────────────
vlans:
  10:
    name: k8s-backplane
    subnet: 10.10.10.0/24
    dhcp:
      pool: 10.10.10.100-10.10.10.200
      router: 10.10.10.1
      dns: [10.10.10.1]

  20:
    name: iot
    subnet: 10.10.20.0/24
    dhcp:
      pool: 10.10.20.100-10.10.20.200
      router: 10.10.20.1

  1024:
    name: native
    subnet: 172.16.1.0/24

# ── Switch ports ───────────────────────────────────────────────
ports:
  1/2/2:    { vlan: 10, speed: 10G-full }
  1/2/3:    { vlan: 10, speed: 10G-full }
  1/2/4:    { vlan: 10, speed: 10G-full }
  1/2/7:    { vlan: 10, speed: 10G-full }
  1/2/8:    { vlan: 10, speed: 10G-full }
  1/2/9:    { vlan: 10, speed: 10G-full }
  1/1/44-48: { vlan: 20, poe: true }
  1/1/5:    { vlan: 20 }
  # omitted = dual-mode tagged trunk (existing behaviour)

# ── LAGs ───────────────────────────────────────────────────────
lags:
  server1: { id: 1, ports: [1/2/2, 1/2/7] }
  server2: { id: 2, ports: [1/2/3, 1/2/8] }
  server3: { id: 3, ports: [1/2/4, 1/2/9] }
  beeflet: { id: 4, ports: [1/2/5, 1/2/10] }

# ── 802.1x ─────────────────────────────────────────────────────
dot1x:
  enabled: true
  radius:
    server: 10.10.10.2
    secret: "…"           # or ref: sops://…/radius
  default_vlan: 20
  ports:
    1/1/1-48:
      auth: dot1x
      fallback_vlan: 20
    1/2/1-10:
      auth: none          # server trunks
    1/3/1-8:
      auth: none          # 10G uplinks

# ── Static routes ──────────────────────────────────────────────
routes:
  - to: 0.0.0.0/0
    via: 172.16.1.1
    vlan: 1024

# ── Router (NixOS) ────────────────────────────────────────────
router:
  interface: eth0         # trunk port to switch
  dhcp: true              # generate dhcpd.conf
  radius: true            # generate freeradius config
  firewall:               # generate nftables rules
    - action: accept
      from: vlan 10
      to: any
    - action: accept
      from: vlan 20
      to: vlan 1024       # iot can reach native/WAN
    - action: drop
      from: vlan 20
      to: vlan 10         # iot isolated from k8s
```

## Rendering

| Concern | Renderer | Target |
|---|---|---|
| VLAN + port config | `icx_apply` (Python) | Brocade web API / SSH |
| LAG config | `icx_apply` | Brocade web API / SSH |
| 802.1x | `icx_apply` | Brocade SSH (CLI-only) |
| ACLs / routes | `icx_apply` | Brocade SSH |
| DHCP pools | NixOS module | `services.dhcpd` / `kea` |
| RADIUS users | NixOS module | `services.freeradius` |
| Firewall | NixOS module | `networking.nftables` |
