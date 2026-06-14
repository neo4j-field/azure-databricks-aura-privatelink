terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = ">= 3.110.0, < 5.0.0"
    }
  }
}

locals {
  vnet_rg = coalesce(var.vnet_resource_group, var.resource_group_name)

  # Build the cloud-init startup script dynamically from the port list.
  socat_units = join("\n", [
    for port in var.neo4j_ports : <<-UNIT
      cat > /etc/systemd/system/neo4j-proxy-${port}.service <<'EOF'
      [Unit]
      Description=Neo4j Private Endpoint proxy port ${port}
      After=network.target

      [Service]
      ExecStart=/usr/bin/socat TCP-LISTEN:${port},fork,reuseaddr TCP:${var.pe_nic_ip}:${port}
      Restart=always
      RestartSec=3

      [Install]
      WantedBy=multi-user.target
      EOF
      systemctl enable --now neo4j-proxy-${port}.service
    UNIT
  ])

  startup_script = <<-EOT
    #!/bin/bash
    set -euo pipefail
    apt-get update -qq
    apt-get install -y -qq socat
    ${local.socat_units}
  EOT
}

# ---------------------------------------------------------------------------
# Look up existing VNet and jump subnet.
# ---------------------------------------------------------------------------

data "azurerm_virtual_network" "this" {
  name                = var.vnet_name
  resource_group_name = local.vnet_rg
}

data "azurerm_subnet" "jump" {
  name                 = var.jump_subnet_name
  virtual_network_name = data.azurerm_virtual_network.this.name
  resource_group_name  = data.azurerm_virtual_network.this.resource_group_name
}

# ---------------------------------------------------------------------------
# AzureBastionSubnet — Azure requires this exact subnet name.
# ---------------------------------------------------------------------------

resource "azurerm_subnet" "bastion" {
  name                 = "AzureBastionSubnet"
  resource_group_name  = local.vnet_rg
  virtual_network_name = data.azurerm_virtual_network.this.name
  address_prefixes     = [var.bastion_subnet_cidr]
}

# ---------------------------------------------------------------------------
# Azure Bastion host (Standard SKU — required for az network bastion tunnel).
# The public IP is on the Bastion, not on any VM.
# ---------------------------------------------------------------------------

resource "azurerm_public_ip" "bastion" {
  name                = "${var.bastion_name}-pip"
  resource_group_name = var.resource_group_name
  location            = var.location
  allocation_method   = "Static"
  sku                 = "Standard"
  tags                = var.tags
}

resource "azurerm_bastion_host" "this" {
  name                = var.bastion_name
  resource_group_name = var.resource_group_name
  location            = var.location
  sku                 = "Standard"   # Basic does not support az network bastion tunnel
  tags                = var.tags

  ip_configuration {
    name                 = "configuration"
    subnet_id            = azurerm_subnet.bastion.id
    public_ip_address_id = azurerm_public_ip.bastion.id
  }
}

# ---------------------------------------------------------------------------
# NSG for the jump box — SSH allowed only from AzureBastionSubnet.
# ---------------------------------------------------------------------------

resource "azurerm_network_security_group" "jumpbox" {
  name                = "${var.jumpbox_name}-nsg"
  resource_group_name = var.resource_group_name
  location            = var.location
  tags                = var.tags

  security_rule {
    name                       = "AllowSshFromBastion"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "22"
    source_address_prefix      = var.bastion_subnet_cidr
    destination_address_prefix = "*"
  }

  security_rule {
    name                       = "DenyAllInbound"
    priority                   = 4096
    direction                  = "Inbound"
    access                     = "Deny"
    protocol                   = "*"
    source_port_range          = "*"
    destination_port_range     = "*"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }
}

# ---------------------------------------------------------------------------
# Jump box NIC — no public IP.
# ---------------------------------------------------------------------------

resource "azurerm_network_interface" "jumpbox" {
  name                = "${var.jumpbox_name}-nic"
  resource_group_name = var.resource_group_name
  location            = var.location
  tags                = var.tags

  ip_configuration {
    name                          = "internal"
    subnet_id                     = data.azurerm_subnet.jump.id
    private_ip_address_allocation = "Dynamic"
  }
}

resource "azurerm_network_interface_security_group_association" "jumpbox" {
  network_interface_id      = azurerm_network_interface.jumpbox.id
  network_security_group_id = azurerm_network_security_group.jumpbox.id
}

# ---------------------------------------------------------------------------
# Jump box VM — no public IP, socat proxies installed via custom_data.
# ---------------------------------------------------------------------------

resource "azurerm_linux_virtual_machine" "jumpbox" {
  name                  = var.jumpbox_name
  resource_group_name   = var.resource_group_name
  location              = var.location
  size                  = var.vm_size
  admin_username        = var.admin_username
  network_interface_ids = [azurerm_network_interface.jumpbox.id]
  tags                  = var.tags

  custom_data = base64encode(local.startup_script)

  admin_ssh_key {
    username   = var.admin_username
    public_key = var.ssh_public_key
  }

  os_disk {
    caching              = "ReadWrite"
    storage_account_type = "Standard_LRS"
    disk_size_gb         = 30
  }

  source_image_reference {
    publisher = "Canonical"
    offer     = "0001-com-ubuntu-server-jammy"
    sku       = "22_04-lts-gen2"
    version   = "latest"
  }

  # Disable password authentication — SSH key only.
  disable_password_authentication = true
}
