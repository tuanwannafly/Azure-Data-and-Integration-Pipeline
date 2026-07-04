// =============================================================================
// main.bicep — Infrastructure as Code for Azure Data Integration project
// Provisions: Resource Group, Storage Account, Azure SQL Server/DB, Service Bus.
// Databricks / App Service / Container App / APIM are intentionally created via
// separate modules/workflows to keep this template focused and cost-friendly.
//
// Business rules applied (see GitPlan "Business Rules"):
//   - BR-04: every resource tagged project=azure-data-integration
//   - BR-03: (handled in Databricks workspace/cluster creation, not here)
//
// Deploy:
//   az deployment group create \
//     --resource-group rg-dataintegration-dev \
//     --template-file infra/main.bicep \
//     --parameters @infra/main.parameters.example.json \
//     --parameters sqlAdminPassword=$(read -s)
// =============================================================================
//
// NOTE: The target subscription must already exist. If you need this file to
// also create the Resource Group, use a subscription-level deployment instead.

@description('Short project name used in resource names.')
@minLength(2)
@maxLength(20)
param projectName string = 'dataintegration'

@description('Environment suffix (dev / staging / prod).')
@allowed([ 'dev', 'staging', 'prod' ])
param environment string = 'dev'

@description('Location for all resources.')
param location string = resourceGroup().location

@description('Azure SQL Server admin login.')
param sqlAdminLogin string = 'sqladmin'

@description('Azure SQL Server admin password (supply via secure parameter).')
@secure()
param sqlAdminPassword string

@description('Random 2-digit suffix to satisfy global-name uniqueness rules.')
param nameSuffix string = '01'

@description('Standard tags applied to every resource (BR-04).')
param tags object = {
  projectName: 'azure-data-integration'
  environment: environment
  managedBy: 'bicep'
}

// Storage account name must be lowercase, 3-24 chars, alphanumeric only.
var storageAccountName = toLower('st${projectName}${nameSuffix}')
var sqlServerName = 'sql-${projectName}-${environment}'
var sqlDatabaseName = 'sqldb-${projectName}'
var serviceBusNamespaceName = 'sb-${projectName}-${environment}'

// -----------------------------------------------------------------------------
// Storage Account + blob containers (raw/sec batch, raw/finnhub streaming)
// -----------------------------------------------------------------------------
resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  tags: tags
  properties: {
    accessTier: 'Cool'
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  name: '${storageAccount.name}/default'
  properties: {
    containerDeleteRetentionPolicy: {
      enabled: true
      days: 7
    }
  }
}

resource rawSecContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  name: '${blobService.name}/raw-sec'
  properties: {
    publicAccess: 'None'
  }
}

resource rawFinnhubContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  name: '${blobService.name}/raw-finnhub'
  properties: {
    publicAccess: 'None'
  }
}

// -----------------------------------------------------------------------------
// Azure SQL Server + Database (Basic tier — cost-friendly for a demo)
// -----------------------------------------------------------------------------
resource sqlServer 'Microsoft.Sql/servers@2023-08-01-preview' = {
  name: sqlServerName
  location: location
  tags: tags
  properties: {
    administratorLogin: sqlAdminLogin
    administratorLoginPassword: sqlAdminPassword
    minimalTlsVersion: '1.2'
    publicNetworkAccess: 'Enabled'
  }
}

resource sqlDatabase 'Microsoft.Sql/servers/databases@2023-08-01-preview' = {
  name: '${sqlServer.name}/${sqlDatabaseName}'
  location: location
  sku: {
    name: 'Basic'
  }
  tags: tags
  properties: {
    collation: 'SQL_Latin1_General_CP1_CI_AS'
    maxSizeBytes: 2147483648 // 2 GB Basic tier cap
  }
}

// Allow Azure services to reach the SQL server (convenient for ADF / Functions).
resource sqlFirewallRule 'Microsoft.Sql/servers/firewallRules@2023-08-01-preview' = {
  name: '${sqlServer.name}/AllowAzureServices'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

// -----------------------------------------------------------------------------
// Service Bus Namespace + queue used by the Finnhub streaming flow (US-07/08)
// -----------------------------------------------------------------------------
resource serviceBus 'Microsoft.ServiceBus/namespaces@2022-10-01-preview' = {
  name: serviceBusNamespaceName
  location: location
  sku: {
    name: 'Standard'
  }
  tags: tags
  properties: {
    minimumTlsVersion: '1.2'
  }
}

resource marketTicksQueue 'Microsoft.ServiceBus/namespaces/queues@2022-10-01-preview' = {
  name: '${serviceBus.name}/market-ticks'
  properties: {
    // Keep messages short-lived; the Function subscriber upserts immediately.
    defaultMessageTimeToLive: 'PT1H'
    duplicateDetectionTimeInWindow: 'PT30S'
    enableBatchedOperations: true
    lockDuration: 'PT30S'
    maxDeliveryCount: 5
    requiresDuplicateDetection: true
  }
}

// -----------------------------------------------------------------------------
// Outputs (useful for downstream deploy scripts)
// -----------------------------------------------------------------------------
output storageAccountName string = storageAccount.name
output sqlServerFqdn string = sqlServer.properties.fullyQualifiedDomainName
output sqlDatabaseName string = sqlDatabaseName
output serviceBusNamespaceName string = serviceBus.name
output marketTicksQueueName string = 'market-ticks'
