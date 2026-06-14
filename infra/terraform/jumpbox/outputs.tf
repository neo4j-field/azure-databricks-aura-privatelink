output "jumpbox_resource_id" {
  description = "ARM resource ID of the jump box VM. Pass to `az network bastion tunnel --target-resource-id`."
  value       = azurerm_linux_virtual_machine.jumpbox.id
}

output "jumpbox_name" {
  description = "Name of the jump box VM."
  value       = azurerm_linux_virtual_machine.jumpbox.name
}

output "bastion_name" {
  description = "Name of the Azure Bastion host."
  value       = azurerm_bastion_host.this.name
}

output "bastion_tunnel_command" {
  description = "Ready-to-run az CLI command to open the Bastion tunnel. Run this in Terminal 1."
  value       = <<-EOT
    az network bastion tunnel \
      --name ${azurerm_bastion_host.this.name} \
      --resource-group ${var.resource_group_name} \
      --target-resource-id ${azurerm_linux_virtual_machine.jumpbox.id} \
      --resource-port 22 \
      --port 2222
  EOT
}

output "ssh_port_forward_command" {
  description = "SSH port-forward command to run in Terminal 2 after the Bastion tunnel is open."
  value       = <<-EOT
    ssh -i ~/.ssh/id_rsa \
      -L 7687:localhost:7687 \
      -L 7474:localhost:7474 \
      -L 7473:localhost:7473 \
      -N -p 2222 \
      ${var.admin_username}@127.0.0.1
  EOT
}
