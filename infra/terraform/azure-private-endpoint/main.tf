terraform {
  required_version = ">= 1.6.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = ">= 3.110.0, < 5.0.0"
    }
  }
}

provider "azurerm" {
  features {}
  subscription_id = var.azure_subscription_id
  tenant_id       = var.azure_tenant_id
}

# ----------------------------------------------------------------------------
# Looks up the existing resource group and target subnet. We never create these
# here — they must already exist and be owned by the consumer team.
# ----------------------------------------------------------------------------
data "azurerm_resource_group" "this" {
  name = var.resource_group_name
}

data "azurerm_virtual_network" "this" {
  name                = var.virtual_network_name
  resource_group_name = var.vnet_resource_group_name != null ? var.vnet_resource_group_name : var.resource_group_name
}

data "azurerm_subnet" "pe_subnet" {
  name                 = var.pe_subnet_name
  virtual_network_name = data.azurerm_virtual_network.this.name
  resource_group_name  = data.azurerm_virtual_network.this.resource_group_name
}

# ----------------------------------------------------------------------------
# Private DNS zone for Aura's databases.neo4j.io hostname. NCC manages DNS on
# the serverless side; for customer-managed consumers we must create a private
# DNS zone and link it to the VNet so the Aura hostname resolves to the PE NIC.
# ----------------------------------------------------------------------------
resource "azurerm_private_dns_zone" "neo4j" {
  count               = var.manage_private_dns ? 1 : 0
  name                = "databases.neo4j.io"
  resource_group_name = data.azurerm_resource_group.this.name

  tags = var.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "neo4j" {
  count                 = var.manage_private_dns ? 1 : 0
  name                  = "${var.private_endpoint_name}-vnet-link"
  resource_group_name   = data.azurerm_resource_group.this.name
  private_dns_zone_name = azurerm_private_dns_zone.neo4j[0].name
  virtual_network_id    = data.azurerm_virtual_network.this.id
  registration_enabled  = false

  tags = var.tags
}

# ----------------------------------------------------------------------------
# Private Endpoint into Aura's Private Link Service via the published PLS alias.
#
# The PLS alias is the canonical handle Azure exposes for cross-tenant PLS
# consumption. The Aura side must have the consumer subscription registered
# in its Network Access config before the connection request will appear in
# the Aura console for approval.
# ----------------------------------------------------------------------------
resource "azurerm_private_endpoint" "aura" {
  name                = var.private_endpoint_name
  location            = data.azurerm_resource_group.this.location
  resource_group_name = data.azurerm_resource_group.this.name
  subnet_id           = data.azurerm_subnet.pe_subnet.id

  custom_network_interface_name = "${var.private_endpoint_name}-nic"

  private_service_connection {
    name                              = "${var.private_endpoint_name}-conn"
    is_manual_connection              = true
    private_connection_resource_alias = var.aura_pls_alias
    request_message                   = var.connection_request_message
  }

  dynamic "private_dns_zone_group" {
    for_each = var.manage_private_dns ? [1] : []
    content {
      name                 = "default"
      private_dns_zone_ids = [azurerm_private_dns_zone.neo4j[0].id]
    }
  }

  tags = var.tags
}

# ----------------------------------------------------------------------------
# Explicit A record for the Aura instance hostname.
#
# Without this record, the wildcard private DNS zone is empty and the hostname
# will fall back to public resolution. We pull the PE NIC IP from the resource
# itself, so this is robust across PE recreations.
# ----------------------------------------------------------------------------
resource "azurerm_private_dns_a_record" "aura" {
  count               = var.manage_private_dns ? 1 : 0
  name                = var.aura_instance_id
  zone_name           = azurerm_private_dns_zone.neo4j[0].name
  resource_group_name = data.azurerm_resource_group.this.name
  ttl                 = 30
  records             = [azurerm_private_endpoint.aura.private_service_connection[0].private_ip_address]

  tags = var.tags

  depends_on = [
    azurerm_private_endpoint.aura,
    azurerm_private_dns_zone_virtual_network_link.neo4j,
  ]
}

# ----------------------------------------------------------------------------
# Outputs
# ----------------------------------------------------------------------------
output "private_endpoint_id" {
  value       = azurerm_private_endpoint.aura.id
  description = "ARM resource ID of the private endpoint."
}

output "private_endpoint_nic_ip" {
  value       = azurerm_private_endpoint.aura.private_service_connection[0].private_ip_address
  description = "Private IP assigned to the endpoint NIC."
}

output "private_dns_zone_id" {
  value       = var.manage_private_dns ? azurerm_private_dns_zone.neo4j[0].id : null
  description = "Private DNS zone for databases.neo4j.io, or null if managed externally."
}

output "next_steps" {
  value = <<-EOT
    Private endpoint submitted to Aura PLS.

    Next:
    1. Open the Aura console -> Security -> Network Access -> Pending approvals.
    2. Approve the incoming endpoint request from this subscription.
    3. Confirm the PE shows `Approved` in the Azure portal.
    4. From any VM/cluster in the linked VNet, verify DNS resolution:
         nslookup ${var.aura_instance_id}.databases.neo4j.io
       The result must be a private address inside the VNet.
    5. Open a Bolt+TLS connection on port 7687 to validate end-to-end.
  EOT
}
