# Developer Desktop Access After Disabling Public Traffic

Once you toggle **Disable public access** in the Aura console, connections from
developer laptops — Neo4j Desktop, a browser pointed at Neo4j Browser, or any
local driver script — are refused. The Aura Private Link endpoint lives inside
your Azure VNet and the Private DNS Zone that resolves the Aura hostname to the
endpoint NIC is only linked to that VNet. Laptops use public DNS, receive Aura's
public IP, and get rejected.

This guide shows two options to restore that access without re-enabling public traffic.

---

## How it works

```
Your laptop (public DNS: <dbid>.databases.neo4j.io → 52.x.x.x ✗ refused)
                         │
               Option A  │  Option B
          Azure Bastion  │  P2S VPN Gateway
          (SSH tunnel)   │  (OpenVPN)
                         │
         Jump box VM ←──┘└──► Your laptop joined to VNet
              │                         │
              └──── Private DNS Zone ───┘
                    resolves to PE NIC:
                    <dbid>.databases.neo4j.io → 10.x.x.x
                              │
                    Azure Private Endpoint NIC
                              │
                    Neo4j Aura VDC (over Azure backbone)
```

---

## Option A — Azure Bastion + SSH Tunnel

Azure Bastion is Microsoft's managed SSH/RDP proxy. The jump box VM that Bastion
connects to has no public IP. The Bastion host itself has a public IP but that is
an Azure-managed resource — your developers never SSH directly to a VM IP.

**Standard tier is required.** Basic Bastion only exposes a browser-based terminal.
Standard tier adds `az network bastion tunnel`, which opens a local TCP port that
lets a desktop SSH client connect — necessary for port-forwarding Neo4j traffic.

### What you will build

```
Developer laptop
  │  az network bastion tunnel (HTTPS/443 to Azure Bastion)
  ▼
Azure Bastion (Standard, public IP, AzureBastionSubnet)
  │  SSH
  ▼
Jump box VM (no public IP, consumer VNet)
  │  socat proxy → PE NIC IP
  ▼
Private Endpoint NIC → Neo4j Aura VDC
```

### Step 1: Deploy the jump box and Bastion

This repo includes a Terraform module at
[`infra/terraform/jumpbox/`](../infra/terraform/jumpbox/). Add it to your
deployment after the private endpoint stack:

```hcl
module "jumpbox" {
  source = "./infra/terraform/jumpbox"

  resource_group_name  = var.resource_group_name
  location             = var.location
  vnet_name            = var.virtual_network_name
  vnet_resource_group  = var.resource_group_name
  jump_subnet_name     = "jump-subnet"           # must already exist, /28 or larger
  bastion_subnet_cidr  = "10.0.255.0/26"         # AzureBastionSubnet — /26 is minimum
  pe_nic_ip            = module.private_endpoint.private_endpoint_nic_ip
  neo4j_ports          = [7687, 7474, 7473, 8491]
  tags                 = var.tags
}
```

The module creates:
- An `AzureBastionSubnet` with the CIDR you provide (Azure requires this exact name)
- A public IP for the Bastion host (Azure Bastion requires one; your VMs do not)
- An Azure Bastion host (Standard SKU)
- A jump box Linux VM (Standard_B1s, no public IP)
- socat systemd proxy services for each Neo4j port, forwarding to the PE NIC IP
- An NSG allowing SSH inbound only from `AzureBastionSubnet`

Apply:

```bash
cd infra/terraform/azure-private-endpoint
terraform apply   # already ran? re-run picks up new module
```

Note the jump box resource ID from the module output — you need it in the next step:

```
jumpbox_resource_id = "/subscriptions/.../virtualMachines/neo4j-jumpbox"
```

### Step 2: Add the Aura hostname to your hosts file

The jump box's socat proxies forward to the Private Endpoint NIC IP. Neo4j Desktop
and browsers verify the TLS certificate against the **hostname in your connection
URI**, not the IP. To make TLS verify correctly while routing traffic through the
local tunnel, map the hostname to `127.0.0.1` on your laptop.

Open your hosts file as administrator:

- **macOS / Linux**: `/etc/hosts`
- **Windows**: `C:\Windows\System32\drivers\etc\hosts`

Add one line — use your Aura instance hostname:

```
127.0.0.1  <dbid>.databases.neo4j.io
```

Example:

```
127.0.0.1  b7253d3b.databases.neo4j.io
```

> Remove this line when you no longer need the tunnel. While it is present, every
> DNS lookup for that hostname on your laptop resolves to `127.0.0.1`.

### Step 3: Open the Bastion tunnel

Run this in a dedicated terminal (it must stay open). Replace the placeholders
with your values from `terraform output`:

```bash
az network bastion tunnel \
  --name neo4j-bastion \
  --resource-group <resource-group> \
  --target-resource-id <jumpbox_resource_id> \
  --resource-port 22 \
  --port 2222
```

This opens `localhost:2222` as a proxy to port 22 on the jump box via Azure Bastion.
Leave this terminal running.

### Step 4: Open the Neo4j port forwards

In a second terminal:

```bash
ssh -i ~/.ssh/id_rsa \
  -L 7687:localhost:7687 \
  -L 7474:localhost:7474 \
  -L 7473:localhost:7473 \
  -N -p 2222 \
  azureuser@127.0.0.1
```

This SSH session connects through the Bastion tunnel and port-forwards Neo4j
traffic: local port → jump box `localhost` → socat → Private Endpoint NIC IP →
Aura. The `-N` flag keeps it open without a shell. Leave this terminal running too.

### Step 5: Connect Neo4j Desktop

Open Neo4j Desktop and add a remote connection:

| Field       | Value                                          |
| ----------- | ---------------------------------------------- |
| Connect URL | `bolt+s://<dbid>.databases.neo4j.io:7687`      |
| Username    | `neo4j` (or your Aura username)                |
| Password    | your Aura password                             |

The traffic path:
1. Desktop resolves `<dbid>.databases.neo4j.io` → `127.0.0.1` (hosts file)
2. TCP connects to `127.0.0.1:7687` → SSH tunnel → jump box `localhost:7687`
3. Jump box socat → PE NIC IP:7687 → Aura
4. TLS: Aura presents cert for `*.databases.neo4j.io`, Desktop verifies `<dbid>.databases.neo4j.io` → ✓

### Access Neo4j Browser in Chrome

Navigate to:

```
https://<dbid>.databases.neo4j.io:7474
```

Same routing through the `:7474` leg of the tunnel.

### Closing the tunnel

Kill both terminal processes when done. Remove the `/etc/hosts` line once closed.

---

## Option B — Azure Point-to-Site VPN Gateway (OpenVPN)

**This is the recommended option for financial services, insurance, and regulated
industries.** Azure P2S VPN is a Microsoft-managed service that:

- Uses the **OpenVPN protocol** — battle-tested, widely accepted by compliance and
  security teams in regulated sectors
- Integrates with **Azure Active Directory** for authentication, enabling
  **MFA via Conditional Access** policies
- Produces full audit trails in **Azure Monitor** and Log Analytics
- Holds compliance certifications for **ISO 27001, SOC 1/2, PCI DSS, FedRAMP**
- Requires **no VM to manage** — Microsoft operates the gateway infrastructure

When connected, your laptop joins the VNet as a full participant. The Private DNS
Zone linked to that VNet resolves `<dbid>.databases.neo4j.io` to the Private
Endpoint NIC IP automatically. Neo4j Desktop, browsers, and any driver connect
with the Private URI as-is — no hosts file changes, no tunnel window to keep open.

### Architecture

```
Developer laptop (VPN client connected)
  │
  │  OpenVPN tunnel (TCP/443 or UDP/1194)
  ▼
Azure VPN Gateway (P2S, GatewaySubnet)
  │  Joined to VNet
  ▼
Private DNS Zone: <dbid>.databases.neo4j.io → PE NIC IP
  │
Private Endpoint NIC → Neo4j Aura VDC
```

### Prerequisites

- An Azure VNet with a dedicated `GatewaySubnet` (minimum `/27`, `/28` supported
  for smaller deployments)
- VPN Gateway SKU: **VpnGw1** or higher (P2S with OpenVPN requires at least VpnGw1)
- Azure AD global admin or Application Administrator access for Azure AD auth
  (or a CA certificate if using certificate-based auth)

> **Provisioning note:** VPN Gateways take **30–45 minutes** to deploy. Plan
> accordingly and do not cancel the apply mid-run.

### Option B-1: Azure AD authentication (recommended for enterprise)

Azure AD auth ties each VPN connection to an Entra ID identity. MFA and Conditional
Access policies apply automatically.

**Terraform snippet:**

```hcl
resource "azurerm_subnet" "gateway" {
  name                 = "GatewaySubnet"   # must be exactly this name
  resource_group_name  = var.resource_group_name
  virtual_network_name = var.virtual_network_name
  address_prefixes     = ["10.0.254.0/27"]
}

resource "azurerm_public_ip" "vpn_gw" {
  name                = "neo4j-vpngw-pip"
  resource_group_name = var.resource_group_name
  location            = var.location
  allocation_method   = "Static"
  sku                 = "Standard"
}

resource "azurerm_virtual_network_gateway" "p2s" {
  name                = "neo4j-vpn-gw"
  resource_group_name = var.resource_group_name
  location            = var.location
  type                = "Vpn"
  vpn_type            = "RouteBased"
  sku                 = "VpnGw1"
  active_active       = false
  enable_bgp          = false

  ip_configuration {
    name                          = "vnetGatewayConfig"
    public_ip_address_id          = azurerm_public_ip.vpn_gw.id
    private_ip_address_allocation = "Dynamic"
    subnet_id                     = azurerm_subnet.gateway.id
  }

  vpn_client_configuration {
    address_space = ["172.16.0.0/24"]   # IP pool for VPN clients; must not overlap your VNet

    vpn_client_protocols = ["OpenVPN"]

    aad_tenant   = "https://login.microsoftonline.com/<your-tenant-id>/"
    aad_audience = "41b23e61-6c1e-4545-b367-cd054e0ed4b4"  # Azure VPN client app ID
    aad_issuer   = "https://sts.windows.net/<your-tenant-id>/"
  }
}
```

**Connect developers:**

1. In the Azure portal, navigate to your VPN Gateway → **Point-to-site configuration**
2. Click **Download VPN client** — this produces a zip with OpenVPN config profiles
3. Developers install the **Azure VPN Client** (Windows/macOS) and import the profile
4. They authenticate with their Entra ID credentials + MFA
5. Once connected, `<dbid>.databases.neo4j.io` resolves to the PE NIC IP
6. Open Neo4j Desktop: `bolt+s://<dbid>.databases.neo4j.io:7687` — works directly

### Option B-2: Certificate-based authentication

If Azure AD is not available, use mutual TLS certificates. Generate a root CA and
issue per-developer client certificates. Upload the root CA public key to the VPN
Gateway. Developers import their client certificate into the VPN client.

```hcl
vpn_client_configuration {
  address_space        = ["172.16.0.0/24"]
  vpn_client_protocols = ["OpenVPN"]

  root_certificate {
    name             = "neo4j-vpn-root-ca"
    public_cert_data = file("certs/root-ca-public.pem")  # base64, no headers
  }
}
```

Certificate authentication is simpler to deploy but lacks the MFA capability and
the centralised revocation that Azure AD provides. For regulated industries, Azure
AD auth is strongly preferred.

---

## Option comparison

| | Option A — Azure Bastion | Option B — P2S VPN (OpenVPN) |
|---|---|---|
| Public IP required | On Bastion host (Azure-managed) | On VPN Gateway (Azure-managed) |
| VMs to manage | Jump box VM | None |
| DNS managed automatically | No — hosts file required | Yes — Private DNS Zone resolves natively |
| Tunnel command to keep open | Yes (two terminals) | No — VPN client handles reconnects |
| Authentication | SSH key | Azure AD + MFA (recommended) or certificates |
| Audit trail | SSH logs on jump box | Azure Monitor / Log Analytics |
| FIPS 140-2 | Not applicable | ✅ Azure-managed |
| Regulatory acceptance | Limited | ✅ Widely accepted in finance/insurance |
| Approximate monthly cost | ~£25 (Bastion Standard) + ~£5 (VM) | ~£140 (VpnGw1) |
| Provisioning time | ~5 minutes | ~40 minutes (gateway) |

**For developer teams that connect regularly, or for any deployment in a regulated
environment, Option B (P2S VPN with OpenVPN) is the right choice.** Option A suits
ad-hoc access or environments where P2S VPN licensing is not available.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `az network bastion tunnel` fails with "Bastion not found" | Wrong resource group or name | Confirm `terraform output bastion_name` and the RG |
| `az network bastion tunnel` requires Standard SKU error | Bastion is Basic tier | Upgrade to Standard in the portal or via Terraform SKU change |
| Neo4j Desktop shows TLS error | Hosts file entry missing or wrong hostname | Confirm `/etc/hosts` entry matches the Aura instance hostname exactly |
| Neo4j Desktop shows "connection refused" | SSH port-forward not running | Confirm the second terminal (Step 4) is still open |
| socat not proxying on jump box | Startup script didn't finish | SSH to jump box via Bastion and run `systemctl status neo4j-proxy-7687` |
| VPN connects but `<dbid>.databases.neo4j.io` resolves to public IP | Private DNS Zone not linked to VNet | In Azure portal, check the DNS zone's VNet links; the VPN client subnet must be in the linked VNet |
| VPN authentication fails with Azure AD | Conditional Access policy blocking | Check the sign-in logs in Entra ID for the blocked policy; common cause is device compliance requirement |
